"""Small JSON store for settings, credentials and per-container preferences.

Everything lives in one file (``d2ha.json``, mode 600) written atomically.
On first start it imports the files of D2HA 0.3 (``auth_config.json`` and
``autodiscovery_preferences.json``) so existing users keep their account.
"""

import json
import os
import secrets
import tempfile
import threading
from copy import deepcopy
from typing import Any, Callable, Dict

DEFAULTS: Dict[str, Any] = {
    "schema": 1,
    "secret_key": "",
    "auth": {
        "username": "",
        "password_hash": "",
        "totp_secret": "",
        "totp_enabled": False,
        # Bumped on password/2FA changes: every older session becomes invalid.
        "generation": 1,
    },
    "api_token_hash": "",
    "settings": {
        "check_interval_hours": 6,
        "session_timeout_minutes": 30,
    },
    # key -> {"ha": bool}; missing keys default to visible in Home Assistant.
    "containers": {},
}


def write_json_atomic(path: str, data: Any) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _merge_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(DEFAULTS)
    for key, value in (data or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def import_v03(data_dir: str) -> Dict[str, Any]:
    """Return the store content built from D2HA 0.3 files, or {} if there are none."""
    old_auth = read_json(os.path.join(data_dir, "auth_config.json")) or {}
    old_prefs = read_json(os.path.join(data_dir, "autodiscovery_preferences.json")) or {}
    if not old_auth.get("password_hash") or not old_auth.get("onboarding_done"):
        return {}

    totp_enabled = bool(old_auth.get("two_factor_enabled") and old_auth.get("totp_secret"))
    containers = {}
    for key, pref in (old_prefs.get("containers") or {}).items():
        if isinstance(pref, dict):
            containers[str(key)] = {"ha": bool(pref.get("state", True))}

    return {
        "auth": {
            "username": old_auth.get("username") or "admin",
            "password_hash": old_auth["password_hash"],
            "totp_secret": old_auth.get("totp_secret") or "" if totp_enabled else "",
            "totp_enabled": totp_enabled,
            "generation": 1,
        },
        "settings": {
            "session_timeout_minutes": int(old_auth.get("session_timeout_minutes") or 30),
        },
        "containers": containers,
    }


class Store:
    def __init__(self, path: str, default_check_hours: int = 6):
        self.path = path
        self._lock = threading.RLock()
        raw = read_json(path)
        migrated = False
        if raw is None:
            raw = import_v03(os.path.dirname(path))
            migrated = bool(raw)
        self._data = _merge_defaults(raw)
        if raw is None or not raw.get("settings", {}).get("check_interval_hours"):
            self._data["settings"]["check_interval_hours"] = default_check_hours
        if not self._data["secret_key"]:
            self._data["secret_key"] = secrets.token_hex(32)
        self.migrated_from_v03 = migrated
        self._save()

    def _save(self) -> None:
        write_json_atomic(self.path, self._data)

    def get(self, *keys: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for key in keys:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return deepcopy(node)

    def update(self, mutate: Callable[[Dict[str, Any]], None]) -> None:
        """Apply ``mutate`` to the data under the lock, then persist."""
        with self._lock:
            mutate(self._data)
            self._save()

    # -- convenience helpers -------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.get("auth", "password_hash"))

    def container_in_ha(self, key: str) -> bool:
        return bool(self.get("containers", key, "ha", default=True))

    def set_container_in_ha(self, key: str, visible: bool) -> None:
        def mutate(data: Dict[str, Any]) -> None:
            data["containers"].setdefault(key, {})["ha"] = bool(visible)

        self.update(mutate)
