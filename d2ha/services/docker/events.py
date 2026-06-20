import logging
import os
import platform
import re
import shutil
import threading
import time
import json
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import docker
from docker.models.containers import Container
from docker.types import IPAMConfig, IPAMPool
from docker.utils import parse_repository_tag

from ..utils import build_stable_id, format_timedelta, human_bytes

class DockerEventsMixin:
    def _severity_from_action(self, action: str) -> str:
        action_l = (action or "").lower()
        error_actions = {"die", "oom", "kill", "destroy", "stop"}
        warning_actions = {"restart", "pause", "unpause", "health_status", "update"}

        if action_l in error_actions:
            return "error"
        if action_l in warning_actions:
            return "warning"
        return "info"

    def _format_event_entry(self, raw_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            event_type = raw_event.get("Type") or raw_event.get("type") or ""
            action = raw_event.get("status") or raw_event.get("Action") or ""
            actor = raw_event.get("Actor", {}) or {}
            attrs = actor.get("Attributes", {}) or {}
            name = attrs.get("name") or attrs.get("container") or raw_event.get("id") or ""
            time_val = raw_event.get("time") or time.time()

            ts = datetime.fromtimestamp(float(time_val), tz=timezone.utc)
            message = f"{event_type.capitalize()} {action} {name}".strip()

            return {
                "timestamp": ts,
                "timestamp_local": ts.astimezone(),
                "type": event_type or "docker",
                "action": action or "event",
                "name": name,
                "id": (raw_event.get("id") or "")[:12],
                "severity": self._severity_from_action(action),
                "detail": message,
                "host": self.host_name,
                "source": attrs.get("image") or attrs.get("image_name") or "-",
            }
        except Exception:
            return None

    def list_events(self, since_seconds: int = 86400, limit: int = 300) -> List[Dict[str, Any]]:
        since_seconds = max(0, since_seconds)
        since_ts = max(0, int(time.time()) - since_seconds)
        now_ts = int(time.time())

        events: "deque[Dict[str, Any]]" = deque(maxlen=limit)

        try:
            for ev in self.docker_api.events(since=since_ts, until=now_ts, decode=True):
                parsed = self._format_event_entry(ev)
                if parsed:
                    events.append(parsed)
        except Exception:
            return []

        default_dt = datetime.min.replace(tzinfo=timezone.utc)
        return sorted(list(events), key=lambda e: e.get("timestamp", default_dt), reverse=True)

