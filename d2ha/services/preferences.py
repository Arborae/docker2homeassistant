import json
import os
import threading
from typing import Any, Dict, Iterable

class AutodiscoveryPreferences:
    AVAILABLE_ACTIONS = (
        "start",
        "pause",
        "stop",
        "restart",
        "delete",
        "full_update",
    )

    DEFAULT_GLOBAL_PREFERENCES = {
        "delete_unused_images": True,
        "updates_overview": True,
        "full_update_all": True,
    }

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._global: Dict[str, Any] = dict(self.DEFAULT_GLOBAL_PREFERENCES)
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                containers_raw: Dict[str, Any] = {}
                global_raw: Dict[str, Any] = {}

                if isinstance(raw, dict):
                    containers_raw = raw.get("containers") or raw
                    if isinstance(raw.get("global"), dict):
                        global_raw = raw.get("global") or {}

                if isinstance(containers_raw, dict):
                    self._data = {
                        str(k): self._apply_defaults(v)
                        for k, v in containers_raw.items()
                        if isinstance(v, dict)
                    }

                self._global = self._apply_global_defaults(global_raw)
        except Exception:
            self._data = {}
            self._global = dict(self.DEFAULT_GLOBAL_PREFERENCES)

    def _apply_defaults(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        actions_raw = entry.get("actions") or {}
        actions = {
            action: bool(actions_raw.get(action, True))
            for action in self.AVAILABLE_ACTIONS
        }
        return {"state": bool(entry.get("state", True)), "actions": actions}

    def _apply_global_defaults(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        prefs = dict(self.DEFAULT_GLOBAL_PREFERENCES)
        for key in prefs:
            prefs[key] = bool(entry.get(key, prefs[key])) if isinstance(entry, dict) else prefs[key]
        return prefs

    def _save(self) -> None:
        dir_path = os.path.dirname(self.path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        payload = {"containers": self._data, "global": self._global}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def get_with_defaults(self, stable_id: str) -> Dict[str, Any]:
        return self._apply_defaults(self._data.get(stable_id) or {})

    def build_map_for(self, stable_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        return {sid: self.get_with_defaults(sid) for sid in stable_ids}

    def get_global_preferences(self) -> Dict[str, Any]:
        return self._apply_global_defaults(self._global)

    def set_global_preferences(self, preferences: Dict[str, Any]) -> Dict[str, Any]:
        prefs = self._apply_global_defaults(preferences)
        with self._lock:
            self._global = prefs
            self._save()
        return prefs

    def set_preferences(
        self, stable_id: str, state_enabled: bool, actions: Dict[str, Any]
    ) -> Dict[str, Any]:
        pref = self._apply_defaults({"state": state_enabled, "actions": actions})
        with self._lock:
            self._data[stable_id] = pref
            self._save()
        return pref

    def prune(self, valid_ids: Iterable[str]) -> None:
        valid_set = set(valid_ids)
        with self._lock:
            removed = [sid for sid in list(self._data.keys()) if sid not in valid_set]
            for sid in removed:
                self._data.pop(sid, None)
            if removed:
                self._save()
