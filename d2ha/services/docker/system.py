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

class DockerSystemMixin:
    def refresh_overview_cache(self):
        stacks = self.list_stacks_overview()
        with self._lock:
            self.overview_cache = stacks
            self.overview_cache_ts = time.time()

    def get_cached_overview(self) -> List[Dict[str, Any]]:
        with self._lock:
            if self.overview_cache:
                return [
                    {**stack, "containers": list(stack.get("containers", []))}
                    for stack in self.overview_cache
                ]

        return self.list_stacks_overview()

    def start_overview_refresher(self, interval: int = 5):
        if self._overview_thread and self._overview_thread.is_alive():
            return

        def _run():
            while True:
                try:
                    self.refresh_overview_cache()
                except Exception:
                    self.logger.exception("Failed to refresh overview cache")
                time.sleep(interval)

        thread = threading.Thread(target=_run, name="overview_refresher", daemon=True)
        thread.start()
        self._overview_thread = thread

    def get_host_info(self) -> Dict[str, Any]:
        try:
            return self.docker_client.info() or {}
        except Exception:
            return {}

    def get_disk_usage(self) -> Dict[str, Any]:
        try:
            return self.docker_api.df() or {}
        except Exception:
            return {}

