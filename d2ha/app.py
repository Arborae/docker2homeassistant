import logging
import os
import secrets
import time
from typing import Optional
from urllib.parse import urlparse

from flask import Flask, redirect, render_template, request, session, url_for, jsonify
from dotenv import load_dotenv

from auth_store import (
    AUTH_CONFIG_PATH,
    ensure_default_auth_config,
    save_auth_config,
    get_auth_config,
)
from services.docker import DockerService
from services.preferences import AutodiscoveryPreferences
from services.utils import human_bytes, read_system_uptime_seconds, format_timedelta
from mqtt.manager import MqttManager
from i18n import DEFAULT_LANG, SUPPORTED_LANGS, get_current_lang, t, set_current_lang
from theme import SUPPORTED_THEMES, get_current_theme
from version import get_d2ha_version
from routes.auth import auth_bp
from routes.ui import ui_bp
from routes.api import api_bp

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("D2HA_SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=(
        os.environ.get("D2HA_SESSION_COOKIE_SECURE", "false").lower() == "true"
    ),
    SESSION_COOKIE_SAMESITE="Lax",
)

# Custom Global Functions for Jinja
app.jinja_env.globals["human_bytes"] = human_bytes
app.jinja_env.globals["t"] = t
app.jinja_env.globals["get_current_lang"] = get_current_lang
app.jinja_env.globals["SUPPORTED_LANGS"] = SUPPORTED_LANGS
app.jinja_env.globals["get_current_theme"] = get_current_theme
app.jinja_env.globals["SUPPORTED_THEMES"] = SUPPORTED_THEMES

ensure_default_auth_config()

# Shared Services Initialization
docker_service = DockerService()
docker_service.start_overview_refresher()

preferences_path = os.environ.get(
    "D2HA_AUTODISCOVERY_PREFS_PATH",
    os.path.join(
        os.path.dirname(AUTH_CONFIG_PATH) or os.path.dirname(__file__),
        "autodiscovery_preferences.json",
    ),
)
autodiscovery_preferences = AutodiscoveryPreferences(preferences_path)

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "d2ha_server")
MQTT_DISCOVERY_PREFIX = os.getenv("MQTT_DISCOVERY_PREFIX", "homeassistant")
MQTT_NODE_ID = os.getenv("MQTT_NODE_ID", "d2ha_server")
MQTT_STATE_INTERVAL = int(os.getenv("MQTT_STATE_INTERVAL", "5"))

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

# Attach services to app for easy access in blueprints
app.docker_service = docker_service
app.mqtt_manager = mqtt_manager
app.autodiscovery_preferences = autodiscovery_preferences

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(ui_bp)
app.register_blueprint(api_bp)

# Logging Configuration
class SensitiveDataFilter(logging.Filter):
    def __init__(self, sensitive_values: Optional[list] = None):
        super().__init__()
        self.sensitive_values = [str(v) for v in (sensitive_values or []) if v]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True

        sanitized = message
        for secret in self.sensitive_values:
            sanitized = sanitized.replace(secret, "<redacted>")

        if sanitized != message:
            record.msg = sanitized
            record.args = ()
        return True

def _attach_filter(logger: logging.Logger, log_filter: logging.Filter) -> None:
    if not any(isinstance(existing, type(log_filter)) for existing in logger.filters):
        logger.addFilter(log_filter)

def _ensure_handlers(logger: logging.Logger, source: logging.Logger) -> None:
    if logger is source:
        return
    if not logger.handlers:
        for handler in source.handlers:
            logger.addHandler(handler)

def configure_logging(debug_mode_enabled: bool) -> None:
    level = logging.DEBUG if debug_mode_enabled else logging.INFO
    sensitive_values = [
        app.config.get("SECRET_KEY"),
        os.environ.get("D2HA_SECRET_KEY"),
        MQTT_PASSWORD,
    ]
    try:
        auth_config = get_auth_config()
        sensitive_values.extend(
            auth_config.get(key)
            for key in ("password_hash", "totp_secret")
            if auth_config.get(key)
        )
    except Exception:
        pass

    redaction_filter = SensitiveDataFilter(sensitive_values)
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    app_logger = app.logger
    app_logger.setLevel(level)
    app_logger.propagate = False
    _attach_filter(app_logger, redaction_filter)

    docker_logger = logging.getLogger("docker_service")
    docker_logger.setLevel(level)
    docker_logger.propagate = False
    _ensure_handlers(docker_logger, app_logger)
    _attach_filter(docker_logger, redaction_filter)

    # Re-attach logger to services if needed, though they user logging.getLogger(__name__) usually
    # MqttManager relies on passed logger

app.config["AUTH_CONFIG"] = get_auth_config # helper for some routes
app.config["SAVE_AUTH_CONFIG"] = save_auth_config

# Initialize Logging
configure_logging(bool(get_auth_config().get("debug_mode_enabled", False)))

# App Helpers for Context and Splash
def _is_backend_ready() -> bool:
    try:
        docker_ready = docker_service.is_engine_running()
    except Exception:
        docker_ready = False

    overview_ready = False
    try:
        overview_ready = bool(docker_service.overview_cache_ts)
    except Exception:
        overview_ready = False

    return docker_ready and overview_ready

def _get_system_info():
    host_info = docker_service.get_host_info()
    uptime_seconds = read_system_uptime_seconds()
    return {
        "os": host_info.get("OperatingSystem") or "-",
        "docker_version": host_info.get("ServerVersion") or host_info.get("Version") or "-",
        "d2ha_version": get_d2ha_version(),
        "uptime": format_timedelta(uptime_seconds) if uptime_seconds >= 0 else "-",
    }

def _default_redirect_after_ready() -> str:
    config = get_auth_config()
    current_user = session.get("user")
    if current_user and current_user == config.get("username"):
        if not config.get("onboarding_done"):
            return url_for("auth.setup_account")
        return url_for("ui.index")
    return url_for("auth.login")

def _sanitize_next_param(raw_next: str) -> str:
    if not raw_next:
        return _default_redirect_after_ready()
    parsed = urlparse(raw_next)
    if parsed.netloc and parsed.netloc != request.host:
        return _default_redirect_after_ready()
    return raw_next

@app.context_processor
def inject_common_context():
    system_info = _get_system_info()
    config = get_auth_config()
    return {
        "safe_mode_enabled": bool(config.get("safe_mode_enabled", True)),
        "performance_mode_enabled": bool(config.get("performance_mode_enabled", False)),
        "debug_mode_enabled": bool(config.get("debug_mode_enabled", False)),
        "system_info": system_info,
        "d2ha_version": system_info.get("d2ha_version", ""),
    }

@app.route("/splash", methods=["GET"])
def splash():
    if _is_backend_ready():
        return redirect(_default_redirect_after_ready())

    requested_next = _sanitize_next_param(request.args.get("next") or "")
    return render_template("splash.html", target_url=requested_next)

@app.before_request
def check_splash_redirect():
    # Allow static resources, splash page itself, and health check API
    if request.path.startswith("/static") or request.path == "/splash" or request.path == "/api/health":
        return None
    
    # If backend is not ready, force redirect to splash
    if not _is_backend_ready():
        return redirect(url_for("splash", next=request.path))

_app_started_ts = time.time()

@app.route("/api/health", methods=["GET"])
def health_check_override():
    ready = _is_backend_ready()
    uptime_seconds = max(time.time() - _app_started_ts, 0)
    status = "ready" if ready else "starting"
    return jsonify({"status": status, "ready": ready, "uptime_seconds": int(uptime_seconds)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=12021)

