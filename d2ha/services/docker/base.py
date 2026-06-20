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

class DockerBase:
    SYSTEM_NETWORKS = {"bridge", "host", "none"}

    def __init__(self, remote_cache_ttl: int = 300, stats_cache_ttl: int = 2):
        self.logger = logging.getLogger(__name__)
        self.docker_client = docker.from_env()
        self.docker_api = self.docker_client.api
        self.remote_cache: Dict[str, Dict[str, Any]] = {}
        self.remote_cache_ts: Dict[str, float] = {}
        self.stats_cache: Dict[str, Dict[str, Any]] = {}
        self.stats_cache_ts: Dict[str, float] = {}
        self.remote_cache_ttl = remote_cache_ttl
        self.stats_cache_ttl = stats_cache_ttl
        self._lock = threading.Lock()
        self.overview_cache: List[Dict[str, Any]] = []
        self.overview_cache_ts: float = 0.0
        self._overview_thread: Optional[threading.Thread] = None
        self.update_preferences: Dict[str, Dict[str, Any]] = {}
        self.github_release_cache: Dict[str, Dict[str, Any]] = {}
        self.github_release_cache_ts: Dict[str, float] = {}
        self.github_cache_ttl = 3600  # 1 hour cache for GitHub releases
        # Adjust path to point to root or relative correctly. 
        # docker_service.py was in d2ha/, now this is d2ha/services/. 
        # The docker-compose.yml is in d2ha/docker-compose.yml (one level up from here)
        self.compose_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docker-compose.yml")
        self.host_name = self._load_host_name()

    def _load_host_name(self) -> str:
        try:
            info = self.docker_client.info()
            return info.get("Name") or platform.node()
        except Exception:
            return platform.node()

