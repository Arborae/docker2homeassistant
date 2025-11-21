import json
import os
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for

from docker_service import build_stable_id, DockerService, MqttManager, human_bytes

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
ENTITIES_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "entities.json")


docker_service = DockerService()
docker_service.start_overview_refresher()
mqtt_manager = MqttManager(
    docker_service=docker_service,
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


def _load_entity_preferences():
    try:
        with open(ENTITIES_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
            if isinstance(raw, dict):
                return {str(k): bool(v) for k, v in raw.items()}
    except FileNotFoundError:
        return {}
    except Exception:
        app.logger.exception("Unable to read entities config")
    return {}


def _save_entity_preferences(preferences: dict):
    try:
        with open(ENTITIES_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(preferences, f, indent=2, sort_keys=True)
    except Exception:
        app.logger.exception("Unable to write entities config")


def _filter_containers_for_mqtt(containers_info):
    preferences = _load_entity_preferences()
    filtered = []
    for c in containers_info:
        stable_id = build_stable_id(c)
        if preferences.get(stable_id, True):
            filtered.append(c)
    return filtered


mqtt_manager.set_container_filter(_filter_containers_for_mqtt)
mqtt_manager.start_periodic_publisher()


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
    return render_template("home.html", stacks=stacks, summary=summary, active_page="home")


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


@app.route("/updates", methods=["GET"])
def updates():
    containers_info = docker_service.collect_containers_info_for_updates()
    _publish_current_state(containers_info)
    stacks, summary = _build_home_context()
    return render_template(
        "updates.html",
        containers=containers_info,
        summary=summary,
        active_page="updates",
    )


@app.route("/api/overview", methods=["GET"])
def api_overview():
    stacks, summary = _build_home_context()
    return jsonify({"summary": summary, "stacks": stacks})


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


@app.route("/entities", methods=["GET", "POST"])
def entities_view():
    containers_info = docker_service.collect_containers_info_for_updates()
    stacks, summary = _build_home_context()

    preferences = _load_entity_preferences()
    if request.method == "POST":
        selected = set(request.form.getlist("entities"))
        new_preferences = {sid: True for sid in selected}
        _save_entity_preferences(new_preferences)
        _publish_current_state(containers_info)
        return redirect(url_for("entities_view"))

    for c in containers_info:
        stable_id = build_stable_id(c)
        c["stable_id"] = stable_id
        c["exposed"] = preferences.get(stable_id, True)

    return render_template(
        "entities.html",
        containers=containers_info,
        summary=summary,
        active_page="entities",
    )


def _publish_current_state(containers_info=None):
    if containers_info is None:
        containers_info = docker_service.collect_containers_info_for_updates()

    filtered = _filter_containers_for_mqtt(containers_info)
    mqtt_manager.publish_autodiscovery_and_state(filtered)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=12021)
