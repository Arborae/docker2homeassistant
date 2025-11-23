import json
import os
from datetime import datetime
from typing import Any, Dict

from werkzeug.security import generate_password_hash

AUTH_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "auth_config.json")


_DEFAULT_CONFIG = {
    "username": "admin",
    "password_hash": generate_password_hash("admin"),
    "onboarding_done": False,
    "two_factor_enabled": False,
    "totp_secret": None,
    "safe_mode_enabled": True,
    "performance_mode_enabled": False,
    "mqtt_default_entities_enabled": True,
    "session_timeout_minutes": 30,
}


def _now_ts() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def ensure_default_auth_config() -> Dict[str, Any]:
    if not os.path.exists(AUTH_CONFIG_PATH):
        username = os.environ.get("D2HA_ADMIN_USERNAME", "admin")
        timestamp = _now_ts()
        default_config: Dict[str, Any] = {
            **_DEFAULT_CONFIG,
            "username": username,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        try:
            with open(AUTH_CONFIG_PATH, "w", encoding="utf-8") as fp:
                json.dump(default_config, fp, indent=2)
            try:
                os.chmod(AUTH_CONFIG_PATH, 0o600)
            except Exception:
                pass
        except Exception:
            return default_config
        return default_config

    return load_auth_config()


def _apply_defaults(config: Dict[str, Any]) -> Dict[str, Any]:
    changed = False
    for key, value in _DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value
            changed = True
    if changed:
        # avoid circular import by writing directly
        try:
            with open(AUTH_CONFIG_PATH, "w", encoding="utf-8") as fp:
                config.setdefault("created_at", _now_ts())
                config["updated_at"] = _now_ts()
                json.dump(config, fp, indent=2)
        except Exception:
            pass
    return config


def load_auth_config() -> Dict[str, Any]:
    try:
        with open(AUTH_CONFIG_PATH, "r", encoding="utf-8") as fp:
            raw = json.load(fp)
            if isinstance(raw, dict):
                return _apply_defaults(raw)
            return _apply_defaults({})
    except FileNotFoundError:
        return ensure_default_auth_config()
    except Exception:
        return ensure_default_auth_config()


def save_auth_config(config: Dict[str, Any]) -> None:
    config = dict(config)
    config.setdefault("created_at", _now_ts())
    config["updated_at"] = _now_ts()
    with open(AUTH_CONFIG_PATH, "w", encoding="utf-8") as fp:
        json.dump(config, fp, indent=2)
    try:
        os.chmod(AUTH_CONFIG_PATH, 0o600)
    except Exception:
        pass
