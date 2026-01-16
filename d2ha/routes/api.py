from typing import Any, Dict, List
from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context
from .auth import onboarding_required, _publish_current_state
from .ui import _build_notifications_summary, _build_home_context

api_bp = Blueprint("api", __name__, url_prefix="/api")

def is_safe_mode_enabled():
    config = current_app.config.get("AUTH_CONFIG", {})()
    return bool(config.get("safe_mode_enabled", True))

def is_performance_mode_enabled():
    config = current_app.config.get("AUTH_CONFIG", {})()
    return bool(config.get("performance_mode_enabled", False))

def is_debug_mode_enabled():
    config = current_app.config.get("AUTH_CONFIG", {})()
    return bool(config.get("debug_mode_enabled", False))

def set_safe_mode(enabled: bool) -> bool:
    config = current_app.config.get("AUTH_CONFIG", {})()
    config["safe_mode_enabled"] = enabled
    current_app.config.get("SAVE_AUTH_CONFIG", lambda x: None)(config)
    return enabled

def set_performance_mode(enabled: bool) -> bool:
    config = current_app.config.get("AUTH_CONFIG", {})()
    config["performance_mode_enabled"] = enabled
    current_app.config.get("SAVE_AUTH_CONFIG", lambda x: None)(config)
    return enabled

def set_debug_mode(enabled: bool) -> bool:
    config = current_app.config.get("AUTH_CONFIG", {})()
    config["debug_mode_enabled"] = enabled
    current_app.config.get("SAVE_AUTH_CONFIG", lambda x: None)(config)
    return enabled

def _sse_event(event_type: str, data: Any) -> str:
    import json
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

def _find_container_overview_entry(container_id: str):
    # This might fail if overview is not cached yet, but cache should be running
    docker_service = current_app.docker_service
    overview = docker_service.get_cached_overview()
    for stack in overview:
        for c in stack.get("containers", []):
            if c["id"] == container_id or c.get("short_id") == container_id:
                return c
    return None

@api_bp.route("/mqtt/publishes", methods=["GET"])
@onboarding_required
def api_mqtt_publishes():
    try:
        limit = int(request.args.get("limit", "200"))
    except (TypeError, ValueError):
        limit = 200

    limit = max(1, min(limit, 500))
    return jsonify({"entries": current_app.mqtt_manager.get_publish_history(limit)})


@api_bp.route("/overview", methods=["GET"])
@onboarding_required
def api_overview():
    stacks, summary = _build_home_context()
    return jsonify({"summary": summary, "stacks": stacks})


@api_bp.route("/notifications", methods=["GET"])
@onboarding_required
def api_notifications():
    force_refresh = request.args.get("refresh") == "1"
    data = _build_notifications_summary(force=force_refresh)
    return jsonify(data)


@api_bp.route("/networks", methods=["GET", "POST"])
@onboarding_required
def api_networks():
    docker_service = current_app.docker_service
    if request.method == "GET":
        try:
            networks = docker_service.list_networks_overview()
            return jsonify({"networks": networks})
        except Exception as exc:
            return jsonify({"error": str(exc) or "Impossibile elencare le reti"}), 500

    payload = request.get_json(force=True, silent=True) or {}
    name = (payload.get("name") or "").strip()
    driver = (payload.get("driver") or "bridge").strip() or "bridge"
    internal = bool(payload.get("internal", False))
    attachable = bool(payload.get("attachable", False))
    subnet = (payload.get("subnet") or "").strip() or None
    gateway = (payload.get("gateway") or "").strip() or None
    labels = payload.get("labels") or {}

    try:
        created = docker_service.create_network(
            name,
            driver=driver,
            internal=internal,
            attachable=attachable,
            subnet=subnet,
            gateway=gateway,
            labels=labels,
        )
        return jsonify(created), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc) or "Impossibile creare la rete"}), 500


@api_bp.route("/networks/<network_id>", methods=["GET", "DELETE"])
@onboarding_required
def api_network_detail(network_id):
    docker_service = current_app.docker_service
    if request.method == "GET":
        details = docker_service.inspect_network(network_id)
        if not details:
            return jsonify({"error": "Rete non trovata"}), 404
        return jsonify(details)

    payload = request.get_json(force=True, silent=True) or {}
    confirmed = bool(payload.get("confirm")) or request.args.get("confirm") == "1"

    if is_safe_mode_enabled() and not confirmed:
        return jsonify({"error": "Modalità sicura attiva: conferma richiesta"}), 403

    try:
        docker_service.remove_network(network_id)
        return jsonify({"status": "removed"})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc) or "Impossibile rimuovere la rete"}), 500


@api_bp.route("/networks/<network_id>/connect", methods=["POST"])
@onboarding_required
def api_network_connect(network_id):
    payload = request.get_json(force=True, silent=True) or {}
    container_id = (payload.get("container_id") or "").strip()
    if not container_id:
        return jsonify({"error": "Container non valido"}), 400

    try:
        current_app.docker_service.connect_container_to_network(network_id, container_id)
        return jsonify({"status": "connected"})
    except Exception as exc:
        return jsonify({"error": str(exc) or "Impossibile collegare il container"}), 500


@api_bp.route("/networks/<network_id>/disconnect", methods=["POST"])
@onboarding_required
def api_network_disconnect(network_id):
    payload = request.get_json(force=True, silent=True) or {}
    container_id = (payload.get("container_id") or "").strip()
    confirmed = bool(payload.get("confirm")) or request.args.get("confirm") == "1"

    if not container_id:
        return jsonify({"error": "Container non valido"}), 400

    if is_safe_mode_enabled() and not confirmed:
        return jsonify({"error": "Modalità sicura attiva: conferma richiesta"}), 403

    try:
        current_app.docker_service.disconnect_container_from_network(
            network_id, container_id, force=True
        )
        return jsonify({"status": "disconnected"})
    except Exception as exc:
        return jsonify({"error": str(exc) or "Impossibile scollegare il container"}), 500


@api_bp.route("/safe_mode", methods=["GET", "POST"])
@onboarding_required
def api_safe_mode():
    if request.method == "GET":
        return jsonify({"enabled": is_safe_mode_enabled()})

    payload = request.get_json(force=True, silent=True) or {}
    enabled = bool(payload.get("enabled", False))
    return jsonify({"enabled": set_safe_mode(enabled)})


@api_bp.route("/performance_mode", methods=["GET", "POST"])
@onboarding_required
def api_performance_mode():
    if request.method == "GET":
        return jsonify({"enabled": is_performance_mode_enabled()})

    payload = request.get_json(force=True, silent=True) or {}
    enabled = bool(payload.get("enabled", False))
    return jsonify({"enabled": set_performance_mode(enabled)})


@api_bp.route("/debug_mode", methods=["GET", "POST"])
@onboarding_required
def api_debug_mode():
    if request.method == "GET":
        return jsonify({"enabled": is_debug_mode_enabled()})

    payload = request.get_json(force=True, silent=True) or {}
    enabled = bool(payload.get("enabled", False))
    return jsonify({"enabled": set_debug_mode(enabled)})


@api_bp.route("/images/delete_unused/stream", methods=["GET"])
@onboarding_required
def api_delete_unused_images_stream():
    if not is_debug_mode_enabled():
        return jsonify({"error": "Debug mode disabilitato"}), 403

    def generate():
        yield _sse_event(
            "command",
            {"action": "delete_unused_images", "message": "Ricerca immagini non in uso"},
        )

        removed: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        unused_images = current_app.docker_service.list_unused_images()

        if not unused_images:
            yield _sse_event("log", {"message": "Nessuna immagine non utilizzata trovata"})
            yield _sse_event("result", {"removed": removed, "errors": errors})
            yield _sse_event("end", {"removed": removed, "errors": errors})
            return

        for image in unused_images:
            tag = image.get("tags", [""])[0] or image.get("short_id", "Immagine")
            yield _sse_event(
                "command",
                {
                    "action": "delete_unused_images",
                    "image": image,
                    "message": f"Eliminazione {tag} ({image.get('short_id')})",
                },
            )
            try:
                current_app.docker_service.remove_image(image["id"])
                removed.append(image)
                yield _sse_event(
                    "log",
                    {
                        "action": "delete_unused_images",
                        "image": image,
                        "message": f"Immagine {tag} rimossa",
                    },
                )
            except Exception as exc:
                error_msg = str(exc) or "Impossibile rimuovere l'immagine"
                errors.append({**image, "error": error_msg})
                yield _sse_event(
                    "error",
                    {
                        "action": "delete_unused_images",
                        "image": image,
                        "message": error_msg,
                    },
                )

        yield _sse_event("result", {"removed": removed, "errors": errors})
        yield _sse_event("end", {"removed": removed, "errors": errors})

    headers = {"Cache-Control": "no-cache"}
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers=headers,
    )


@api_bp.route("/containers/<container_id>/full_update", methods=["POST"])
@onboarding_required
def api_container_full_update(container_id):
    """Perform full container update (pull image + recreate) without requiring debug mode."""
    docker_service = current_app.docker_service
    
    try:
        docker_service.recreate_container_with_latest_image(container_id)
        
        docker_service.refresh_overview_cache()
        _publish_current_state()
        
        result = _find_container_overview_entry(container_id)
        return jsonify({
            "success": True,
            "container_id": container_id,
            "container": result,
        })
    except Exception as exc:
        print(f"[DEBUG] Exception occurred: {exc}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(exc) or "Update failed",
            "container_id": container_id,
        }), 500

@api_bp.route("/containers/<container_id>/<action>", methods=["POST"])
@onboarding_required
def api_container_action(container_id, action):
    allowed_actions = {
        "start",
        "stop",
        "restart",
        "pause",
        "unpause",
        "delete",
        "kill",
    }
    
    if action not in allowed_actions:
        return jsonify({"error": "Azione non supportata"}), 400
        
    docker_service = current_app.docker_service
    try:
        docker_service.apply_simple_action(container_id, action)
        
        # After action, update cache
        docker_service.refresh_overview_cache()
        _publish_current_state()
        
        result = _find_container_overview_entry(container_id)
        if not result and action != "delete":
             return jsonify({"error": "Container non trovato dopo azione"}), 404
             
        return jsonify({"success": True, "container": result, "removed": action == "delete"})
    except Exception as exc:
        current_app.logger.error(f"Error performing {action} on {container_id}: {exc}")
        return jsonify({"error": str(exc) or "Azione fallita"}), 500


@api_bp.route("/containers/<container_id>/actions/<action>/stream", methods=["GET"])
@onboarding_required
def api_container_action_stream(container_id, action):
    if not is_debug_mode_enabled():
        return jsonify({"error": "Debug mode disabilitato"}), 403

    allowed_actions = {
        "start",
        "stop",
        "restart",
        "pause",
        "unpause",
        "delete",
        "pull",
        "full_update",
    }

    if action not in allowed_actions:
        return jsonify({"error": "Azione non supportata"}), 400

    def generate():
        yield _sse_event(
            "command",
            {
                "action": action,
                "container_id": container_id,
                "message": f"Esecuzione '{action}' per {container_id}",
            },
        )
        
        docker_service = current_app.docker_service

        try:
            if action in ("start", "stop", "restart", "pause", "unpause", "delete"):
                docker_service.apply_simple_action(container_id, action)
            elif action in ("pull", "full_update"):
                yield _sse_event(
                    "command",
                    {
                        "action": action,
                        "container_id": container_id,
                        "message": "Pull immagine e ricreazione container",
                    },
                )
                docker_service.recreate_container_with_latest_image(container_id)

            yield _sse_event(
                "status", {"state": "completed", "action": action, "container_id": container_id}
            )
        except Exception as exc:
            yield _sse_event(
                "error",
                {
                    "action": action,
                    "container_id": container_id,
                    "message": str(exc) or "Action failed",
                },
            )
            yield _sse_event("end", {"action": action, "container_id": container_id})
            return

        docker_service.refresh_overview_cache()
        _publish_current_state()

        result = _find_container_overview_entry(container_id)
        removed = result is None or action == "delete"
        yield _sse_event(
            "result",
            {
                "action": action,
                "container_id": container_id,
                "container": result,
                "removed": removed,
            },
        )

        if not removed:
            for line in docker_service.stream_container_logs(container_id, tail=50, follow=True, timeout=12.0):
                if not line:
                    continue
                yield _sse_event("log", {"action": action, "line": line})

        yield _sse_event("end", {"action": action, "container_id": container_id})

    headers = {"Cache-Control": "no-cache"}
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)


@api_bp.route("/containers/<container_id>/details", methods=["GET"])
@onboarding_required
def api_container_details(container_id):
    details = current_app.docker_service.get_container_detail(container_id)
    if not details:
        return jsonify({"error": "Container non trovato"}), 404
    return jsonify(details)


@api_bp.route("/containers/<container_id>/stats", methods=["GET"])
@onboarding_required
def api_container_stats(container_id):
    stats = current_app.docker_service.get_container_live_stats(container_id)
    if stats is None:
        return jsonify({"error": "Statistiche non disponibili"}), 404
    return jsonify(stats)


@api_bp.route("/containers/<container_id>/logs", methods=["GET"])
@onboarding_required
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

    logs = current_app.docker_service.get_container_logs(container_id, tail=tail_val)
    if logs == "":
        return jsonify({"logs": "", "error": "Log non disponibili"}), 404
    return jsonify({"logs": logs})


@api_bp.route("/containers/<container_id>/updates", methods=["GET", "POST"])
@onboarding_required
def api_container_updates(container_id):
    force_refresh = request.method == "POST"
    info = current_app.docker_service.get_container_update_info(container_id, force_refresh=force_refresh)
    if not info:
        return jsonify({"error": "Container non trovato"}), 404
    return jsonify(info)


@api_bp.route("/containers/<container_id>/updates/frequency", methods=["POST"])
@onboarding_required
def api_container_updates_frequency(container_id):
    data = request.get_json(force=True, silent=True) or {}
    minutes = int(data.get("minutes", 60))
    minutes = current_app.docker_service.set_update_frequency(container_id, minutes)
    return jsonify({"minutes": minutes})


@api_bp.route("/containers/<container_id>/updates/track", methods=["POST"])
@onboarding_required
def api_container_updates_track(container_id):
    data = request.get_json(force=True, silent=True) or {}
    tag = data.get("tag")
    tag = current_app.docker_service.set_update_track(container_id, tag)

    info = current_app.docker_service.get_container_update_info(container_id, force_refresh=True)
    if not info:
        return jsonify({"error": "Container non trovato"}), 404

    return jsonify({"tag": tag, "update_state": info.get("update_state")})


@api_bp.route("/containers/<container_id>/compose", methods=["GET", "POST"])
@onboarding_required
def api_container_compose(container_id):
    if request.method == "GET":
        compose_info = current_app.docker_service.get_compose_file_for_container(container_id)
        if not compose_info:
            return jsonify({"error": "docker-compose.yml non trovato"}), 404
        return jsonify(compose_info)

    payload = request.get_json(force=True, silent=True) or {}
    content = payload.get("content")
    if not isinstance(content, str):
        return jsonify({"error": "Contenuto non valido"}), 400

    if current_app.docker_service.save_compose_file_for_container(container_id, content):
        return jsonify({"status": "ok"})

    return jsonify({"error": "Impossibile salvare il file"}), 500


@api_bp.route("/compose", methods=["GET", "POST"])
@onboarding_required
def api_compose_file():
    if request.method == "GET":
        content = current_app.docker_service.get_compose_file()
        if content is None:
            return jsonify({"error": "docker-compose.yml non trovato"}), 404
        return jsonify({"content": content})

    payload = request.get_json(force=True, silent=True) or {}
    content = payload.get("content")
    if not isinstance(content, str):
        return jsonify({"error": "Contenuto non valido"}), 400

    if current_app.docker_service.save_compose_file(content):
        return jsonify({"status": "ok"})

    return jsonify({"error": "Impossibile salvare il file"}), 500
