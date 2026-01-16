import time
from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app, jsonify
from .auth import onboarding_required, _publish_current_state
from services.utils import human_bytes
from services.preferences import AutodiscoveryPreferences

ui_bp = Blueprint("ui", __name__)

_notifications_cache = {}

def _build_notifications_summary(force: bool = False) -> dict:
    now = time.time()
    if not force and now - _notifications_cache.get("ts", 0.0) < 15:
        return _notifications_cache.get("data", {})
    
    docker_service = current_app.docker_service

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
    docker_service = current_app.docker_service
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


@ui_bp.route("/", methods=["GET"])
@ui_bp.route("/home", methods=["GET"])
@onboarding_required
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


@ui_bp.route("/containers", methods=["GET"])
@onboarding_required
def containers_view():
    stacks, summary = _build_home_context()
    notifications = _build_notifications_summary()
    return render_template(
        "containers.html",
        stacks=stacks,
        summary=summary,
        notifications=notifications,
        active_page="containers",
    )


@ui_bp.route("/images", methods=["GET"])
@onboarding_required
def images_view():
    images = current_app.docker_service.list_images_overview()
    stacks, summary = _build_home_context()
    notifications = _build_notifications_summary()
    return render_template(
        "images.html",
        images=images,
        summary=summary,
        notifications=notifications,
        active_page="images",
    )


@ui_bp.route("/images/delete_unused", methods=["POST"])
@onboarding_required
def delete_unused_images():
    current_app.docker_service.remove_unused_images()
    return redirect(url_for("ui.images_view"))


@ui_bp.route("/images/<path:image_id>/delete", methods=["POST"])
@onboarding_required
def delete_image(image_id):
    current_app.docker_service.remove_image(image_id)
    return redirect(url_for("ui.images_view"))


@ui_bp.route("/volumes", methods=["GET"])
@onboarding_required
def volumes_view():
    volumes = current_app.docker_service.list_volumes_overview()
    stacks, summary = _build_home_context()
    notifications = _build_notifications_summary()
    return render_template(
        "volumes.html",
        volumes=volumes,
        summary=summary,
        notifications=notifications,
        active_page="volumes",
    )


@ui_bp.route("/networks", methods=["GET"])
@onboarding_required
def networks_view():
    networks = current_app.docker_service.list_networks_overview()
    stacks, summary = _build_home_context()
    notifications = _build_notifications_summary()
    return render_template(
        "networks.html",
        networks=networks,
        summary=summary,
        notifications=notifications,
        active_page="networks",
    )


@ui_bp.route("/volumes/delete", methods=["POST"])
@onboarding_required
def delete_volume():
    volume_name = request.form.get("volume_name") or ""
    volume_type = request.form.get("volume_type") or "volume"
    if volume_name:
        current_app.docker_service.remove_volume(volume_name, volume_type)
    return redirect(url_for("ui.volumes_view"))


@ui_bp.route("/volumes/delete_unused", methods=["POST"])
@onboarding_required
def delete_unused_volumes():
    current_app.docker_service.remove_unused_volumes()
    return redirect(url_for("ui.volumes_view"))


@ui_bp.route("/events", methods=["GET"])
@onboarding_required
def events_view():
    hours_param = request.args.get("hours", default="24")
    severity_param = (request.args.get("severity") or "all").lower()
    try:
        hours = int(hours_param)
    except ValueError:
        hours = 24

    hours = max(1, min(hours, 24 * 30))
    events = current_app.docker_service.list_events(since_seconds=hours * 3600, limit=400)
    allowed_severities = {"all", "info", "warning", "error"}
    selected_severity = severity_param if severity_param in allowed_severities else "all"
    if selected_severity != "all":
        events = [ev for ev in events if ev.get("severity") == selected_severity]
    stacks, summary = _build_home_context()
    notifications = _build_notifications_summary()

    return render_template(
        "events.html",
        events=events,
        selected_hours=hours,
        selected_severity=selected_severity,
        summary=summary,
        notifications=notifications,
        active_page="events",
    )


@ui_bp.route("/updates", methods=["GET"])
@onboarding_required
def updates():
    docker_service = current_app.docker_service
    mqtt_manager = current_app.mqtt_manager
    try:
        containers_info = docker_service.collect_containers_info_for_updates()
        mqtt_manager.publish_autodiscovery_and_state(containers_info)
    except Exception:
        current_app.logger.exception("Failed to load updates page")
        flash("Impossibile caricare gli aggiornamenti. Riprova più tardi.", "error")
        containers_info = []
    stack_map = {}
    for c in containers_info:
        stack_name = c.get("stack", "_no_stack")
        stack_map.setdefault(stack_name, []).append(c)

    grouped_containers = [
        {"name": name, "containers": stack_map[name]}
        for name in sorted(stack_map.keys())
    ]
    stacks, summary = _build_home_context()
    notifications = _build_notifications_summary()
    return render_template(
        "updates.html",
        stacks=grouped_containers,
        summary=summary,
        notifications=notifications,
        active_page="updates",
    )


@ui_bp.route("/autodiscovery", methods=["GET", "POST"])
@onboarding_required
def autodiscovery_view():
    docker_service = current_app.docker_service
    mqtt_manager = current_app.mqtt_manager
    autodiscovery_preferences = current_app.autodiscovery_preferences

    containers_info = docker_service.collect_containers_info_for_updates()
    containers_info = [
        c for c in containers_info if not mqtt_manager.is_self_container(c)
    ]
    stable_ids = [c.get("stable_id", "") for c in containers_info]

    if request.method == "POST":
        global_preferences = {
            "delete_unused_images": request.form.get("delete_unused_images")
            == "on",
            "updates_overview": request.form.get("updates_overview") == "on",
            "full_update_all": request.form.get("full_update_all") == "on",
        }
        autodiscovery_preferences.set_global_preferences(global_preferences)

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
        return redirect(url_for("ui.autodiscovery_view"))

    stack_map = {}
    for c in containers_info:
        stack_map.setdefault(c.get("stack", "_no_stack"), []).append(c)
    stack_map = {k: stack_map[k] for k in sorted(stack_map.keys())}

    pref_map = autodiscovery_preferences.build_map_for(stable_ids)
    global_preferences = autodiscovery_preferences.get_global_preferences()
    stacks, summary = _build_home_context()
    notifications = _build_notifications_summary()

    shared_entities = sum(1 for pref in pref_map.values() if pref.get("state", True))

    general_total = 1
    general_shared = 1

    if global_preferences.get("updates_overview", True):
        general_total += 1
        general_shared += 1

    if global_preferences.get("delete_unused_images", True):
        general_total += 1
        general_shared += 1

    if global_preferences.get("full_update_all", True):
        general_total += 1
        general_shared += 1

    mqtt_status = {
        "connected": mqtt_manager.is_connected(),
        "broker": mqtt_manager.broker,
        "port": mqtt_manager.port,
        "shared_entities": shared_entities + general_shared,
        "total_entities": len(containers_info) + general_total,
    }

    return render_template(
        "autodiscovery.html",
        stack_map=stack_map,
        preferences=pref_map,
        actions=AutodiscoveryPreferences.AVAILABLE_ACTIONS,
        global_preferences=global_preferences,
        summary=summary,
        notifications=notifications,
        active_page="autodiscovery",
        mqtt_status=mqtt_status,
    )
