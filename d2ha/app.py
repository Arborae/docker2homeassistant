import json
import os
import secrets
import time
from functools import wraps

import pyotp
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from auth_store import ensure_default_auth_config, load_auth_config, save_auth_config
from docker_service import (
    AutodiscoveryPreferences,
    DockerService,
    MqttManager,
    format_timedelta,
    human_bytes,
)

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("D2HA_SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=not app.debug,
    SESSION_COOKIE_SAMESITE="Lax",
)
app.jinja_env.globals["human_bytes"] = human_bytes

ensure_default_auth_config()


def get_auth_config():
    return load_auth_config()

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
_safe_mode_state = {"enabled": True}
_safe_mode_file = os.path.join(os.path.dirname(__file__), "safe_mode_state.json")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        config = get_auth_config()
        current_user = session.get("user")
        if not current_user or current_user != config.get("username"):
            session.clear()
            return redirect(url_for("login", next=request.url))
        return view(*args, **kwargs)

    return wrapped


def onboarding_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        config = get_auth_config()
        current_user = session.get("user")
        if not current_user or current_user != config.get("username"):
            session.clear()
            return redirect(url_for("login", next=request.url))
        if not is_onboarding_done():
            return redirect(url_for("setup_account"))
        return view(*args, **kwargs)

    return wrapped


def _load_safe_mode_state():
    global _safe_mode_state
    try:
        with open(_safe_mode_file, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            _safe_mode_state["enabled"] = bool(data.get("enabled", True))
    except FileNotFoundError:
        _safe_mode_state["enabled"] = True
    except Exception:
        _safe_mode_state["enabled"] = True


def _save_safe_mode_state():
    try:
        with open(_safe_mode_file, "w", encoding="utf-8") as fp:
            json.dump({"enabled": _safe_mode_state.get("enabled", False)}, fp)
    except Exception:
        pass


def is_safe_mode_enabled() -> bool:
    return bool(_safe_mode_state.get("enabled", False))


def set_safe_mode(enabled: bool) -> bool:
    _safe_mode_state["enabled"] = bool(enabled)
    _save_safe_mode_state()
    return is_safe_mode_enabled()


def is_onboarding_done():
    config = get_auth_config()
    return bool(config.get("onboarding_done"))


def _read_system_uptime_seconds() -> float:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as fp:
            content = fp.read().strip().split()
            if content:
                return float(content[0])
    except Exception:
        pass
    return -1.0


def _get_system_info():
    host_info = docker_service.get_host_info()
    uptime_seconds = _read_system_uptime_seconds()

    return {
        "os": host_info.get("OperatingSystem") or "-",
        "docker_version": host_info.get("ServerVersion")
        or host_info.get("Version")
        or "-",
        "uptime": format_timedelta(uptime_seconds) if uptime_seconds >= 0 else "-",
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    config = get_auth_config()
    next_url = request.args.get("next")

    if session.get("user"):
        if not config.get("onboarding_done"):
            return redirect(url_for("setup_account"))
        return redirect(next_url or url_for("index"))

    two_factor = bool(config.get("two_factor_enabled") and config.get("totp_secret"))

    if request.method == "POST":
        username_input = (request.form.get("username") or "").strip()
        password_input = request.form.get("password") or ""
        token_input = (request.form.get("token") or "").strip()

        if username_input == config.get("username") and check_password_hash(
            config.get("password_hash", ""), password_input
        ):
            totp_valid = True
            if two_factor:
                totp = pyotp.TOTP(config.get("totp_secret"))
                totp_valid = bool(token_input) and bool(
                    totp.verify(token_input, valid_window=1)
                )

            if totp_valid:
                session.clear()
                session["user"] = config.get("username")
                session["logged_at"] = int(time.time())

                if not config.get("onboarding_done"):
                    return redirect(url_for("setup_account"))

                return redirect(next_url or url_for("index"))

        flash("Invalid credentials or 2FA code", "error")

    return render_template("login.html", two_factor=two_factor)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/setup-account", methods=["GET", "POST"])
@login_required
def setup_account():
    config = get_auth_config()
    if config.get("onboarding_done"):
        return redirect(url_for("index"))

    if request.method == "POST":
        new_username = (request.form.get("new_username") or config.get("username", "")).strip()
        new_password = request.form.get("new_password") or ""
        new_password_confirm = request.form.get("new_password_confirm") or ""

        if not new_password:
            flash("Please choose a password.", "error")
        elif new_password != new_password_confirm:
            flash("Passwords do not match.", "error")
        elif new_password == "admin":
            flash("For security, the password cannot remain 'admin'.", "error")
        elif len(new_password) < 10:
            flash("Choose a longer password (at least 10 characters).", "error")
        else:
            config["username"] = new_username or config.get("username", "admin")
            config["password_hash"] = generate_password_hash(new_password)
            save_auth_config(config)
            session["user"] = config["username"]
            flash("Account updated. Let's finish setting up security.", "success")
            return redirect(url_for("setup_2fa"))

    return render_template(
        "setup_account.html",
        current_username=config.get("username", "admin"),
    )


@app.route("/setup-2fa", methods=["GET", "POST"])
@login_required
def setup_2fa():
    config = get_auth_config()

    if config.get("onboarding_done"):
        return redirect(url_for("index"))

    secret = session.get("pending_totp_secret") or pyotp.random_base32()
    session["pending_totp_secret"] = secret
    provisioning_uri = pyotp.TOTP(secret).provisioning_uri(
        name=config.get("username", "admin"), issuer_name="D2HA"
    )

    if request.method == "POST":
        choice = request.form.get("choice")
        if choice == "skip":
            config["two_factor_enabled"] = False
            config["totp_secret"] = None
            config["onboarding_done"] = True
            save_auth_config(config)
            session.pop("pending_totp_secret", None)
            flash(
                "2FA is currently disabled. You can enable it later in Security Settings.",
                "info",
            )
            return redirect(url_for("index"))

        if choice == "enable":
            token = (request.form.get("token") or "").strip()
            totp = pyotp.TOTP(secret)
            if totp.verify(token, valid_window=1):
                config["two_factor_enabled"] = True
                config["totp_secret"] = secret
                config["onboarding_done"] = True
                save_auth_config(config)
                session.pop("pending_totp_secret", None)
                flash("Two-factor authentication enabled.", "success")
                return redirect(url_for("index"))

            flash("Invalid verification code. Please try again.", "error")

    return render_template(
        "setup_2fa.html",
        secret=secret,
        provisioning_uri=provisioning_uri,
        username=config.get("username", "admin"),
    )




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


_load_safe_mode_state()


@app.context_processor
def inject_common_context():
    return {
        "safe_mode_enabled": is_safe_mode_enabled(),
        "system_info": _get_system_info(),
    }


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


@app.route("/containers", methods=["GET"])
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


@app.route("/images", methods=["GET"])
@onboarding_required
def images_view():
    images = docker_service.list_images_overview()
    stacks, summary = _build_home_context()
    notifications = _build_notifications_summary()
    return render_template(
        "images.html",
        images=images,
        summary=summary,
        notifications=notifications,
        active_page="images",
    )


@app.route("/events", methods=["GET"])
@onboarding_required
def events_view():
    hours_param = request.args.get("hours", default="24")
    severity_param = (request.args.get("severity") or "all").lower()
    try:
        hours = int(hours_param)
    except ValueError:
        hours = 24

    hours = max(1, min(hours, 24 * 30))
    events = docker_service.list_events(since_seconds=hours * 3600, limit=400)
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


@app.route("/updates", methods=["GET"])
@onboarding_required
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
    notifications = _build_notifications_summary()
    return render_template(
        "updates.html",
        stacks=grouped_containers,
        summary=summary,
        notifications=notifications,
        active_page="updates",
    )


@app.route("/autodiscovery", methods=["GET", "POST"])
@onboarding_required
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
    notifications = _build_notifications_summary()

    return render_template(
        "autodiscovery.html",
        stack_map=stack_map,
        preferences=pref_map,
        actions=AutodiscoveryPreferences.AVAILABLE_ACTIONS,
        summary=summary,
        notifications=notifications,
        active_page="autodiscovery",
    )


@app.route("/api/overview", methods=["GET"])
@onboarding_required
def api_overview():
    stacks, summary = _build_home_context()
    return jsonify({"summary": summary, "stacks": stacks})


@app.route("/api/notifications", methods=["GET"])
@onboarding_required
def api_notifications():
    force_refresh = request.args.get("refresh") == "1"
    data = _build_notifications_summary(force=force_refresh)
    return jsonify(data)


@app.route("/api/safe_mode", methods=["GET", "POST"])
@onboarding_required
def api_safe_mode():
    if request.method == "GET":
        return jsonify({"enabled": is_safe_mode_enabled()})

    payload = request.get_json(force=True, silent=True) or {}
    enabled = bool(payload.get("enabled", False))
    return jsonify({"enabled": set_safe_mode(enabled)})


@app.route("/containers/<container_id>/<action>", methods=["POST"])
@onboarding_required
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
@onboarding_required
def container_full_update(container_id):
    docker_service.recreate_container_with_latest_image(container_id)
    _publish_current_state()
    return redirect(url_for("updates"))


@app.route("/images/<path:image_id>/delete", methods=["POST"])
@onboarding_required
def delete_image(image_id):
    docker_service.remove_image(image_id)
    return redirect(url_for("images_view"))


@app.route("/api/containers/<container_id>/details", methods=["GET"])
@onboarding_required
def api_container_details(container_id):
    details = docker_service.get_container_detail(container_id)
    if not details:
        return jsonify({"error": "Container non trovato"}), 404
    return jsonify(details)


@app.route("/api/containers/<container_id>/stats", methods=["GET"])
@onboarding_required
def api_container_stats(container_id):
    stats = docker_service.get_container_live_stats(container_id)
    if stats is None:
        return jsonify({"error": "Statistiche non disponibili"}), 404
    return jsonify(stats)


@app.route("/api/containers/<container_id>/logs", methods=["GET"])
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

    logs = docker_service.get_container_logs(container_id, tail=tail_val)
    if logs == "":
        return jsonify({"logs": "", "error": "Log non disponibili"}), 404
    return jsonify({"logs": logs})


@app.route("/api/containers/<container_id>/updates", methods=["GET", "POST"])
@onboarding_required
def api_container_updates(container_id):
    force_refresh = request.method == "POST"
    info = docker_service.get_container_update_info(container_id, force_refresh=force_refresh)
    if not info:
        return jsonify({"error": "Container non trovato"}), 404
    return jsonify(info)


@app.route("/api/containers/<container_id>/updates/frequency", methods=["POST"])
@onboarding_required
def api_container_updates_frequency(container_id):
    data = request.get_json(force=True, silent=True) or {}
    minutes = int(data.get("minutes", 60))
    minutes = docker_service.set_update_frequency(container_id, minutes)
    return jsonify({"minutes": minutes})


@app.route("/api/containers/<container_id>/compose", methods=["GET", "POST"])
@onboarding_required
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
@onboarding_required
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
