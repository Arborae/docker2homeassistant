import os
from dotenv import load_dotenv
import time
from flask import Flask, jsonify, redirect, render_template, request, url_for

from docker_service import (
    AutodiscoveryPreferences,
    DockerService,
    MqttManager,
    human_bytes,
)

load_dotenv()

app = Flask(__name__)
app.jinja_env.globals["human_bytes"] = human_bytes

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "d2ha_server")
MQTT_DISCOVERY_PREFIX = os.getenv("MQTT_DISCOVERY_PREFIX", "homeassistant")
MQTT_NODE_ID = os.getenv("MQTT_NODE_ID", "d2ha_server")
MQTT_STATE_INTERVAL = int(os.getenv("MQTT_STATE_INTERVAL", "5"))


docker_service = DockerService()
docker_service.start_overview_refresher()
preferences_path = os.path.join(os.path.dirname(__file__), "autodiscovery_preferences.json")
autodiscovery_preferences = AutodiscoveryPreferences(preferences_path)
mqtt_manager = MqttManager(
    docker_service=docker_service,
    preferences=autodiscovery_preferences,
    broker=MQTT_BROKER,
    port=MQTT_PORT,
    username=MQTT_USERNAME,
    password=MQTT_PASSWORD,
    base_topic=MQTT_BASE_TOPIC,
    discovery_prefix=MQTT_DISCOVERY_PREFIX,
    node_id=MQTT_NODE_ID,
    state_interval=MQTT_STATE_INTERVAL,
    logger=app.logger,
)

mqtt_manager.setup()
mqtt_manager.start_periodic_publisher()


_notifications_cache = {"ts": 0.0, "data": {}}


def _build_notifications_summary(force: bool = False) -> dict:
    now = time.time()
    if not force and now - _notifications_cache.get("ts", 0.0) < 15:
        return _notifications_cache.get("data", {})

    unused_count = 0
    reclaimable_bytes = 0
    try:
        images_overview = docker_service.list_images_overview()
        unused_images = [img for img in images_overview if not img.get("used_by")]
        unused_count = len(unused_images)
        reclaimable_bytes = sum(img.get("size", 0) or 0 for img in unused_images)
    except Exception:
        pass

    updates_pending = 0
    try:
        containers_info = docker_service.collect_containers_info_for_updates()
        updates_pending = sum(
            1 for c in containers_info if c.get("update_state") == "update_available"
        )
    except Exception:
        pass

    critical_events = 0
    try:
        events = docker_service.list_events(since_seconds=24 * 3600, limit=300)
        critical_events = sum(
            1
            for ev in events
            if (ev.get("severity", "") or "").lower() in {"critical", "fatal", "error"}
        )
    except Exception:
        pass

    summary = {
        "unused_images": {
            "count": unused_count,
            "reclaimable_bytes": reclaimable_bytes,
            "reclaimable_h": human_bytes(reclaimable_bytes),
        },
        "updates_pending": updates_pending,
        "critical_events": critical_events,
    }

    _notifications_cache.update({"ts": now, "data": summary})
    return summary


def _build_home_context():
    stacks_raw = docker_service.get_cached_overview()
    host_info = docker_service.get_host_info()
    disk_usage = docker_service.get_disk_usage()

    stacks = []
    for stack in stacks_raw:
        containers = stack.get("containers", [])
        total_cpu = sum(c.get("cpu_percent", 0.0) for c in containers)
        total_mem_bytes = sum(c.get("mem_usage_bytes", 0.0) for c in containers)
        total_net_rx = sum(c.get("net_rx_bytes", 0.0) for c in containers)
        total_net_tx = sum(c.get("net_tx_bytes", 0.0) for c in containers)

        stacks.append(
            {
                **stack,
                "total_cpu": round(total_cpu, 1),
                "total_mem_bytes": total_mem_bytes,
                "total_mem_h": human_bytes(total_mem_bytes),
                "total_net_rx_bytes": total_net_rx,
                "total_net_tx_bytes": total_net_tx,
                "total_net_rx_h": human_bytes(total_net_rx),
                "total_net_tx_h": human_bytes(total_net_tx),
            }
        )

    total_containers = sum(len(stack.get("containers", [])) for stack in stacks)
    running = sum(
        1
        for stack in stacks
        for container in stack.get("containers", [])
        if container.get("status") == "running"
    )
    paused = sum(
        1
        for stack in stacks
        for container in stack.get("containers", [])
        if container.get("status") == "paused"
    )
    stopped = total_containers - running - paused

    total_cpu = sum(
        container.get("cpu_percent", 0.0)
        for stack in stacks
        for container in stack.get("containers", [])
    )
    total_mem_bytes = sum(
        container.get("mem_usage_bytes", 0.0)
        for stack in stacks
        for container in stack.get("containers", [])
    )

    mem_total = host_info.get("MemTotal", 0)
    mem_percent = (total_mem_bytes / mem_total * 100) if mem_total else 0

    disk_layers = None
    try:
        disk_layers = disk_usage.get("LayersSize")
    except Exception:
        disk_layers = None

    used_images = {
        container.get("image") for stack in stacks for container in stack.get("containers", [])
    }
    images_used_count = len(used_images)
    images_unused = host_info.get("Images", 0) - images_used_count
    if images_unused < 0:
        images_unused = 0

    summary = {
        "stacks": len(stacks),
        "total_containers": total_containers,
        "running": running,
        "paused": paused,
        "stopped": stopped,
        "total_cpu": round(total_cpu, 1),
        "total_mem_bytes": total_mem_bytes,
        "mem_total": mem_total,
        "mem_percent": round(mem_percent, 1),
        "images": host_info.get("Images", 0),
        "images_used": images_used_count,
        "images_unused": images_unused,
        "cpu_count": host_info.get("NCPU", 0),
        "disk_layers": disk_layers,
        "mem_total_h": human_bytes(mem_total) if mem_total else "-",
        "mem_used_h": human_bytes(total_mem_bytes),
        "disk_layers_h": human_bytes(disk_layers) if disk_layers else "-",
    }

    return stacks, summary


@app.route("/", methods=["GET"])
@app.route("/home", methods=["GET"])
def index():
    stacks, summary = _build_home_context()
    notifications = _build_notifications_summary()
    return render_template(
        "home.html",
        stacks=stacks,
        summary=summary,
        notifications=notifications,
        active_page="home",
    )


@app.route("/containers", methods=["GET"])
def containers_view():
    stacks, summary = _build_home_context()
    return render_template(
        "containers.html", stacks=stacks, summary=summary, active_page="containers"
    )


@app.route("/images", methods=["GET"])
def images_view():
    images = docker_service.list_images_overview()
    stacks, summary = _build_home_context()
    return render_template(
        "images.html", images=images, summary=summary, active_page="images"
    )


@app.route("/events", methods=["GET"])
def events_view():
    hours_param = request.args.get("hours", default="24")
    try:
        hours = int(hours_param)
    except ValueError:
        hours = 24

    hours = max(1, min(hours, 24 * 30))
    events = docker_service.list_events(since_seconds=hours * 3600, limit=400)
    stacks, summary = _build_home_context()

    return render_template(
        "events.html",
        events=events,
        selected_hours=hours,
        summary=summary,
        active_page="events",
    )


@app.route("/updates", methods=["GET"])
def updates():
    containers_info = docker_service.collect_containers_info_for_updates()
    mqtt_manager.publish_autodiscovery_and_state(containers_info)
    stack_map = {}
    for c in containers_info:
        stack_name = c.get("stack", "_no_stack")
        stack_map.setdefault(stack_name, []).append(c)

    grouped_containers = [
        {"name": name, "containers": stack_map[name]}
        for name in sorted(stack_map.keys())
    ]
    stacks, summary = _build_home_context()
    return render_template(
        "updates.html",
        stacks=grouped_containers,
        summary=summary,
        active_page="updates",
    )


@app.route("/autodiscovery", methods=["GET", "POST"])
def autodiscovery_view():
    containers_info = docker_service.collect_containers_info_for_updates()
    containers_info = [
        c for c in containers_info if not mqtt_manager.is_self_container(c)
    ]
    stable_ids = [c.get("stable_id", "") for c in containers_info]

    if request.method == "POST":
        for c in containers_info:
            stable_id = c.get("stable_id")
            if not stable_id:
                continue

            state_enabled = request.form.get(f"{stable_id}_state") == "on"
            actions = {
                action: request.form.get(f"{stable_id}_{action}") == "on"
                for action in AutodiscoveryPreferences.AVAILABLE_ACTIONS
            }
            autodiscovery_preferences.set_preferences(stable_id, state_enabled, actions)

        autodiscovery_preferences.prune(stable_ids)
        _publish_current_state()
        return redirect(url_for("autodiscovery_view"))

    stack_map = {}
    for c in containers_info:
        stack_map.setdefault(c.get("stack", "_no_stack"), []).append(c)
    stack_map = {k: stack_map[k] for k in sorted(stack_map.keys())}

    pref_map = autodiscovery_preferences.build_map_for(stable_ids)
    stacks, summary = _build_home_context()

    return render_template(
        "autodiscovery.html",
        stack_map=stack_map,
        preferences=pref_map,
        actions=AutodiscoveryPreferences.AVAILABLE_ACTIONS,
        summary=summary,
        active_page="autodiscovery",
    )


@app.route("/api/overview", methods=["GET"])
def api_overview():
    stacks, summary = _build_home_context()
    return jsonify({"summary": summary, "stacks": stacks})


@app.route("/api/notifications", methods=["GET"])
def api_notifications():
    force_refresh = request.args.get("refresh") == "1"
    data = _build_notifications_summary(force=force_refresh)
    return jsonify(data)


@app.route("/containers/<container_id>/<action>", methods=["POST"])
def container_action(container_id, action):
    if action == "play":
        action = "start"

    if action in ("start", "stop", "restart", "pause", "unpause"):
        docker_service.apply_simple_action(container_id, action)
    elif action == "delete":
        docker_service.remove_container(container_id)

    _publish_current_state()

    return redirect(url_for("containers_view"))


@app.route("/containers/<container_id>/full_update", methods=["POST"])
def container_full_update(container_id):
    docker_service.recreate_container_with_latest_image(container_id)
    _publish_current_state()
    return redirect(url_for("updates"))


@app.route("/images/<path:image_id>/delete", methods=["POST"])
def delete_image(image_id):
    docker_service.remove_image(image_id)
    return redirect(url_for("images_view"))


@app.route("/api/containers/<container_id>/details", methods=["GET"])
def api_container_details(container_id):
    details = docker_service.get_container_detail(container_id)
    if not details:
        return jsonify({"error": "Container non trovato"}), 404
    return jsonify(details)


@app.route("/api/containers/<container_id>/stats", methods=["GET"])
def api_container_stats(container_id):
    stats = docker_service.get_container_live_stats(container_id)
    if stats is None:
        return jsonify({"error": "Statistiche non disponibili"}), 404
    return jsonify(stats)


@app.route("/api/containers/<container_id>/logs", methods=["GET"])
def api_container_logs(container_id):
    tail_param = request.args.get("tail", default="100")
    tail_val = 100
    if tail_param == "all":
        tail_val = None
    else:
        try:
            tail_val = int(tail_param)
        except ValueError:
            tail_val = 100

    logs = docker_service.get_container_logs(container_id, tail=tail_val)
    if logs == "":
        return jsonify({"logs": "", "error": "Log non disponibili"}), 404
    return jsonify({"logs": logs})


@app.route("/api/containers/<container_id>/updates", methods=["GET", "POST"])
def api_container_updates(container_id):
    force_refresh = request.method == "POST"
    info = docker_service.get_container_update_info(container_id, force_refresh=force_refresh)
    if not info:
        return jsonify({"error": "Container non trovato"}), 404
    return jsonify(info)


@app.route("/api/containers/<container_id>/updates/frequency", methods=["POST"])
def api_container_updates_frequency(container_id):
    data = request.get_json(force=True, silent=True) or {}
    minutes = int(data.get("minutes", 60))
    minutes = docker_service.set_update_frequency(container_id, minutes)
    return jsonify({"minutes": minutes})


@app.route("/api/containers/<container_id>/compose", methods=["GET", "POST"])
def api_container_compose(container_id):
    if request.method == "GET":
        compose_info = docker_service.get_compose_file_for_container(container_id)
        if not compose_info:
            return jsonify({"error": "docker-compose.yml non trovato"}), 404
        return jsonify(compose_info)

    payload = request.get_json(force=True, silent=True) or {}
    content = payload.get("content")
    if not isinstance(content, str):
        return jsonify({"error": "Contenuto non valido"}), 400

    if docker_service.save_compose_file_for_container(container_id, content):
        return jsonify({"status": "ok"})

    return jsonify({"error": "Impossibile salvare il file"}), 500


@app.route("/api/compose", methods=["GET", "POST"])
def api_compose_file():
    if request.method == "GET":
        content = docker_service.get_compose_file()
        if content is None:
            return jsonify({"error": "docker-compose.yml non trovato"}), 404
        return jsonify({"content": content})

    payload = request.get_json(force=True, silent=True) or {}
    content = payload.get("content")
    if not isinstance(content, str):
        return jsonify({"error": "Contenuto non valido"}), 400

    if docker_service.save_compose_file(content):
        return jsonify({"status": "ok"})

    return jsonify({"error": "Impossibile salvare il file"}), 500


def _publish_current_state():
    containers_info = docker_service.collect_containers_info_for_updates()
    mqtt_manager.publish_autodiscovery_and_state(containers_info)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=12021)
