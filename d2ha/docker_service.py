import json
import os
import platform
import shutil
import threading
import logging
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import docker
from docker.models.containers import Container
from docker.types import IPAMConfig, IPAMPool
from docker.utils import parse_repository_tag

try:
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    mqtt = None


def format_timedelta(delta_seconds: float) -> str:
    if delta_seconds < 0:
        delta_seconds = 0
    days = int(delta_seconds // 86400)
    hours = int((delta_seconds % 86400) // 3600)
    minutes = int((delta_seconds % 3600) // 60)
    parts: List[str] = []
    if days:
        parts.append(f"{days}g")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def human_bytes(num: float, suffix: str = "B") -> str:
    for unit in ["", "K", "M", "G", "T"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}P{suffix}"


def slugify_container(name: str, short_id: str) -> str:
    base = "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_")
    if not base:
        base = "container"
    return f"{base}_{short_id}"


def build_stable_id(container_info: Dict[str, Any]) -> str:
    """Create a stable ID for HA based on stack + container name.

    Avoids Docker IDs so the unique_id stays stable when containers are recreated.
    """

    stack = container_info.get("stack") or "no_stack"
    name = container_info.get("name") or "container"

    base = f"{stack}__{name}"

    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in base)

    while "__" in slug:
        slug = slug.replace("__", "_")
    slug = slug.strip("_")

    if not slug:
        slug = "container"

    return slug


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


class DockerService:
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
        self.compose_path = os.path.join(os.path.dirname(__file__), "docker-compose.yml")
        self.host_name = self._load_host_name()

    def _load_host_name(self) -> str:
        try:
            info = self.docker_client.info()
            return info.get("Name") or platform.node()
        except Exception:
            return platform.node()

    def is_engine_running(self) -> bool:
        try:
            self.docker_client.ping()
            return True
        except Exception:
            return False

    def _calc_cpu_percent(self, stat: dict) -> float:
        try:
            cpu_delta = (
                stat["cpu_stats"]["cpu_usage"]["total_usage"]
                - stat["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            system_delta = (
                stat["cpu_stats"]["system_cpu_usage"]
                - stat["precpu_stats"]["system_cpu_usage"]
            )
            if system_delta > 0 and cpu_delta > 0:
                cores = len(stat["cpu_stats"]["cpu_usage"].get("percpu_usage") or []) or 1
                cpu_percent = (cpu_delta / system_delta) * cores * 100.0
                return cpu_percent
        except Exception:
            pass
        return 0.0

    def _extract_version(self, labels: dict) -> Optional[str]:
        for key in (
            "org.opencontainers.image.version",
            "version",
            "org.opencontainers.image.revision",
        ):
            val = labels.get(key)
            if val:
                return val
        return None

    def _extract_tag(self, image_ref: Optional[str]) -> Optional[str]:
        if not image_ref:
            return None

        if "@" in image_ref:
            ref_part = image_ref.split("@", 1)[0]
        else:
            ref_part = image_ref

        repository, tag = parse_repository_tag(ref_part)
        return tag

    def _extract_changelog(self, labels: dict) -> Optional[str]:
        for key in (
            "org.opencontainers.image.changelog",
            "changelog",
            "org.opencontainers.image.description",
        ):
            val = labels.get(key)
            if val:
                return val
        return None

    def _extract_breaking(self, labels: dict) -> Optional[str]:
        for key in (
            "org.opencontainers.image.breaking_changes",
            "breaking_changes",
        ):
            val = labels.get(key)
            if val:
                return val
        return None

    @staticmethod
    def _format_display_version(
        channel: Optional[str], version: Optional[str], digest_short: Optional[str]
    ) -> Optional[str]:
        """Return a human-friendly version string for UI display."""

        channel = (channel or "").strip()
        version = (version or "").strip()
        digest_short = (digest_short or "").strip()

        if channel and version and channel != version:
            return f"{channel} {version}"

        if version:
            return version

        if channel and digest_short:
            return f"{channel} {digest_short}"

        if channel:
            return channel

        if digest_short:
            return digest_short

        return None

    def _get_container_ports(self, container: Container) -> Dict[str, Any]:
        attrs = container.attrs or {}
        host_config = attrs.get("HostConfig", {}) or {}
        network_mode = host_config.get("NetworkMode") or ""

        ports_attr = attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
        bindings = []

        for port_proto, mappings in sorted(ports_attr.items()):
            if not mappings:
                bindings.append(port_proto)
                continue

            for mapping in mappings:
                host_ip = mapping.get("HostIp") or "0.0.0.0"
                host_port = mapping.get("HostPort")

                host_ip = "*" if host_ip in ("0.0.0.0", "") else host_ip

                if host_port:
                    bindings.append(f"{host_ip}:{host_port}->{port_proto}")
                else:
                    bindings.append(f"{host_ip}->{port_proto}")

        return {"mode": network_mode, "bindings": bindings}

    def _get_stack_name(self, container: Container) -> str:
        return container.labels.get("com.docker.compose.project") or "_no_stack"

    def _get_installed_image_info(self, container: Container) -> Dict[str, Any]:
        installed_image = container.image
        installed_id = installed_image.id
        installed_short = installed_id.split(":")[-1][:12]

        labels = (
            getattr(installed_image, "labels", None)
            or installed_image.attrs.get("Config", {}).get("Labels", {})
            or {}
        )

        if installed_image.tags:
            image_ref = installed_image.tags[0]
        else:
            image_ref = container.attrs.get("Config", {}).get("Image") or installed_id

        reference_tag = self._extract_tag(image_ref)
        ref_repo = image_ref.split("@")[0].rsplit(":", 1)[0] if image_ref else None
        repo_digests = installed_image.attrs.get("RepoDigests", []) or []

        installed_digest = None
        for digest_ref in repo_digests:
            if "@" not in digest_ref:
                continue
            repo, digest = digest_ref.split("@", 1)
            if ref_repo and repo == ref_repo:
                installed_digest = digest
                break

        if not installed_digest and repo_digests:
            fallback = repo_digests[0]
            if "@" in fallback:
                installed_digest = fallback.split("@", 1)[1]

        installed_digest_short = (
            installed_digest.split(":")[-1][:12] if installed_digest else None
        )

        installed_version = self._extract_version(labels) or reference_tag or installed_short
        changelog_local = self._extract_changelog(labels)
        breaking_local = self._extract_breaking(labels)

        return {
            "image_ref": image_ref,
            "installed_id": installed_id,
            "installed_id_short": installed_short,
            "installed_digest": installed_digest,
            "installed_digest_short": installed_digest_short,
            "installed_version": installed_version,
            "installed_tag": reference_tag,
            "local_changelog": changelog_local,
            "local_breaking": breaking_local,
        }

    def _build_check_reference(self, image_ref: str, preferred_tag: Optional[str]) -> str:
        if not preferred_tag:
            return image_ref

        repository, _ = parse_repository_tag(image_ref)
        if not repository:
            return image_ref

        base_repo = repository.split("@", 1)[0]
        return f"{base_repo}:{preferred_tag}"

    def _merge_remote_with_installed(
        self, installed_info: Dict[str, Any], remote_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        remote_id = remote_info.get("remote_id")
        remote_id_short = remote_info.get("remote_id_short")
        remote_version = remote_info.get("remote_version")
        remote_tag = remote_info.get("remote_tag")

        installed_digest = installed_info.get("installed_digest")
        installed_version = installed_info.get("installed_version")
        installed_tag = installed_info.get("installed_tag")

        if not remote_id and installed_digest:
            remote_id = installed_digest
            remote_id_short = installed_info.get("installed_digest_short")

        if remote_id and not remote_id_short:
            remote_id_short = remote_id.split(":")[-1][:12]

        if not remote_version:
            remote_version = installed_version

        if not remote_tag:
            remote_tag = installed_tag

        return {
            "remote_id": remote_id,
            "remote_id_short": remote_id_short,
            "remote_version": remote_version,
            "remote_tag": remote_tag,
            "remote_changelog": remote_info.get("remote_changelog"),
            "remote_breaking": remote_info.get("remote_breaking"),
        }

    def _fetch_remote_info(self, image_ref: str) -> Dict[str, Any]:
        reference_tag = self._extract_tag(image_ref)
        try:
            distribution = self.docker_api.inspect_distribution(image_ref)
        except Exception:
            return {
                "remote_id": None,
                "remote_id_short": None,
                "remote_version": None,
                "remote_changelog": None,
                "remote_breaking": None,
            }

        descriptor = distribution.get("Descriptor") or {}
        annotations = descriptor.get("annotations") or {}

        remote_id = descriptor.get("digest") or distribution.get("Digest")
        remote_short = remote_id.split(":")[-1][:12] if remote_id else None

        remote_version = (
            annotations.get("org.opencontainers.image.version")
            or annotations.get("version")
            or reference_tag
            or remote_short
        )

        return {
            "remote_id": remote_id,
            "remote_id_short": remote_short,
            "remote_version": remote_version,
            "remote_tag": reference_tag,
            "remote_changelog": annotations.get("org.opencontainers.image.changelog"),
            "remote_breaking": annotations.get("org.opencontainers.image.breaking_changes"),
        }

    def get_remote_info(self, image_ref: str) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            cached_ts = self.remote_cache_ts.get(image_ref, 0)
            if now - cached_ts <= self.remote_cache_ttl and image_ref in self.remote_cache:
                return self.remote_cache[image_ref]

        remote_info = self._fetch_remote_info(image_ref)

        with self._lock:
            self.remote_cache[image_ref] = remote_info
            self.remote_cache_ts[image_ref] = now
        return remote_info

    def _get_update_config(self, container_id: str) -> Dict[str, Any]:
        pref = self.update_preferences.get(container_id) or {}
        frequency = int(pref.get("frequency", 60) or 60)
        frequency = max(5, min(frequency, 24 * 60))

        track = pref.get("track")
        track = track.strip() if isinstance(track, str) else None
        track = track or None

        return {"frequency": frequency, "track": track}

    def get_container_stats(self, container: Container):
        now = time.time()
        with self._lock:
            cached_ts = self.stats_cache_ts.get(container.id, 0)
            if now - cached_ts <= self.stats_cache_ttl and container.id in self.stats_cache:
                data = self.stats_cache[container.id]
                return (
                    data["cpu_percent"],
                    data["usage"],
                    data["mem_percent"],
                    data.get("net_rx", 0),
                    data.get("net_tx", 0),
                )

        try:
            stats = self.docker_api.stats(container.id, stream=False)
        except Exception:
            return 0.0, 0, 0.0

        cpu_percent = self._calc_cpu_percent(stats)

        try:
            usage = stats["memory_stats"]["usage"]
            cache = stats["memory_stats"].get("stats", {}).get("cache", 0)
            usage = max(usage - cache, 0)
            limit = stats["memory_stats"]["limit"]
            mem_percent = (usage / limit) * 100.0 if limit > 0 else 0.0
        except Exception:
            usage = 0
            mem_percent = 0.0

        networks = stats.get("networks", {}) or {}
        net_rx = sum(val.get("rx_bytes", 0) for val in networks.values())
        net_tx = sum(val.get("tx_bytes", 0) for val in networks.values())

        with self._lock:
            self.stats_cache[container.id] = {
                "cpu_percent": cpu_percent,
                "usage": usage,
                "mem_percent": mem_percent,
                "net_rx": net_rx,
                "net_tx": net_tx,
            }
            self.stats_cache_ts[container.id] = now

        return cpu_percent, usage, mem_percent, net_rx, net_tx

    def get_container_live_stats(self, container_id: str) -> Optional[Dict[str, Any]]:
        try:
            container = self.docker_client.containers.get(container_id)
        except Exception:
            return None

        try:
            stats = self.docker_api.stats(container.id, stream=False)
        except Exception:
            return None

        cpu_percent = self._calc_cpu_percent(stats)

        mem_usage = 0
        mem_limit = 0
        mem_percent = 0.0
        try:
            mem_usage = stats.get("memory_stats", {}).get("usage", 0)
            cache_val = stats.get("memory_stats", {}).get("stats", {}).get("cache", 0)
            mem_usage = max(mem_usage - cache_val, 0)
            mem_limit = stats.get("memory_stats", {}).get("limit", 0)
            mem_percent = (mem_usage / mem_limit * 100.0) if mem_limit else 0.0
        except Exception:
            pass

        networks = stats.get("networks", {}) or {}
        rx = sum(val.get("rx_bytes", 0) for val in networks.values())
        tx = sum(val.get("tx_bytes", 0) for val in networks.values())

        return {
            "cpu_percent": round(cpu_percent, 1),
            "mem_usage": mem_usage,
            "mem_limit": mem_limit,
            "mem_percent": round(mem_percent, 1),
            "net_rx": rx,
            "net_tx": tx,
        }

    def list_stacks_overview(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        all_containers = self.docker_client.containers.list(all=True)

        stacks_map: Dict[str, List[Dict[str, Any]]] = {}

        for c in all_containers:
            state = c.attrs.get("State", {})
            status = state.get("Status", c.status)
            started_at = state.get("StartedAt")

            uptime_str = "-"
            if started_at and status == "running":
                try:
                    started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    delta = (now - started_dt).total_seconds()
                    uptime_str = format_timedelta(delta)
                except Exception:
                    pass

            cpu_percent, mem_usage, mem_percent, net_rx, net_tx = self.get_container_stats(c)

            networks = []
            nets = c.attrs.get("NetworkSettings", {}).get("Networks", {})
            for name, cfg in nets.items():
                networks.append({"name": name, "ip": cfg.get("IPAddress", "")})

            ports = self._get_container_ports(c)

            if c.image.tags:
                image_name = c.image.tags[0]
            else:
                image_name = c.image.short_id

            stack_name = self._get_stack_name(c)

            container_info = {
                "id": c.id,
                "short_id": c.short_id,
                "name": c.name,
                "image": image_name,
                "status": status,
                "uptime": uptime_str,
                "cpu_percent": round(cpu_percent, 1),
                "mem_usage": human_bytes(mem_usage),
                "mem_usage_bytes": mem_usage,
                "mem_percent": round(mem_percent, 1),
                "restarts": state.get("RestartCount", 0),
                "networks": networks,
                "ports": ports,
                "net_rx_bytes": net_rx,
                "net_tx_bytes": net_tx,
            }

            stacks_map.setdefault(stack_name, []).append(container_info)

        stacks = []
        for stack_name, containers in stacks_map.items():
            containers.sort(key=lambda x: x["name"].lower())
            stacks.append({"name": stack_name, "containers": containers})

        stacks.sort(key=lambda s: (s["name"] == "_no_stack", s["name"].lower()))
        return stacks

    def get_container_detail(self, container_id: str) -> Optional[Dict[str, Any]]:
        try:
            container = self.docker_client.containers.get(container_id)
        except Exception:
            return None

        attrs = container.attrs or {}
        state = attrs.get("State", {})
        config = attrs.get("Config", {})
        host_config = attrs.get("HostConfig", {}) or {}

        uptime = "-"
        if state.get("Status") == "running":
            started_at = state.get("StartedAt")
            if started_at:
                try:
                    started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    delta = (datetime.now(timezone.utc) - started_dt).total_seconds()
                    uptime = format_timedelta(delta)
                except Exception:
                    pass

        networks = []
        nets = attrs.get("NetworkSettings", {}).get("Networks", {})
        for name, cfg in nets.items():
            networks.append({"name": name, "ip": cfg.get("IPAddress", "")})

        ports = self._get_container_ports(container)

        mounts = []
        for m in attrs.get("Mounts", []) or []:
            mounts.append(
                {
                    "source": m.get("Source"),
                    "destination": m.get("Destination"),
                    "mode": m.get("Mode"),
                    "type": m.get("Type"),
                }
            )

        env_raw = config.get("Env", []) or []
        env = []
        for e in env_raw:
            if "=" in e:
                key, val = e.split("=", 1)
            else:
                key, val = e, ""
            env.append({"key": key, "value": val})

        labels = []
        for key, val in (config.get("Labels") or {}).items():
            labels.append({"key": key, "value": val})

        restart_policy = host_config.get("RestartPolicy", {}) or {}

        image_name = container.image.tags[0] if container.image.tags else container.image.short_id

        return {
            "id": container.id,
            "short_id": container.short_id,
            "name": container.name,
            "image": image_name,
            "status": state.get("Status", container.status),
            "created": attrs.get("Created"),
            "uptime": uptime,
            "command": " ".join(config.get("Cmd") or []) or "-",
            "restart_policy": restart_policy.get("Name") or "-",
            "ports": ports,
            "networks": networks,
            "mounts": mounts,
            "env": env,
            "labels": labels,
        }

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

    def list_images_overview(self) -> List[Dict[str, Any]]:
        containers = self.docker_client.containers.list(all=True)
        usage_map: Dict[str, List[str]] = {}

        for container in containers:
            usage_map.setdefault(container.image.id, []).append(container.name)

        images_overview: List[Dict[str, Any]] = []
        for image in self.docker_client.images.list():
            tags = image.tags or ["<none>:<none>"]
            images_overview.append(
                {
                    "id": image.id,
                    "short_id": image.short_id,
                    "tags": tags,
                    "size": image.attrs.get("Size", 0),
                    "created": image.attrs.get("Created"),
                    "used_by": usage_map.get(image.id, []),
                }
            )

        images_overview.sort(key=lambda img: (img["tags"][0] or "").lower())
        return images_overview

    def remove_image(self, image_id: str) -> None:
        try:
            self.docker_client.images.remove(image_id)
        except Exception:
            pass

    def list_unused_images(self) -> List[Dict[str, Any]]:
        containers = self.docker_client.containers.list(all=True)
        usage_map: Dict[str, List[str]] = {}

        for container in containers:
            usage_map.setdefault(container.image.id, []).append(container.name)

        unused: List[Dict[str, Any]] = []
        for image in self.docker_client.images.list():
            used_by = usage_map.get(image.id, [])
            if used_by:
                continue

            tags = image.tags or ["<none>:<none>"]
            unused.append(
                {
                    "id": image.id,
                    "short_id": image.short_id,
                    "tags": tags,
                    "size": image.attrs.get("Size", 0),
                    "created": image.attrs.get("Created"),
                    "used_by": used_by,
                }
            )

        unused.sort(key=lambda img: (img["tags"][0] or "").lower())
        return unused

    def remove_unused_images(self) -> Dict[str, List[Dict[str, Any]]]:
        removed: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        for image in self.list_unused_images():
            try:
                self.docker_client.images.remove(image["id"])
                removed.append(image)
            except Exception as exc:
                errors.append({**image, "error": str(exc) or "Unknown error"})

        return {"removed": removed, "errors": errors}

    def list_volumes_overview(self) -> List[Dict[str, Any]]:
        containers = self.docker_client.containers.list(all=True)

        volume_usage: Dict[str, List[str]] = {}
        bind_usage: Dict[str, List[str]] = {}

        for container in containers:
            mounts = container.attrs.get("Mounts") or []
            for mount in mounts:
                mount_type = (mount.get("Type") or mount.get("type") or "").lower()
                if mount_type == "volume":
                    name = mount.get("Name") or mount.get("Source")
                    if name:
                        volume_usage.setdefault(name, []).append(container.name)
                elif mount_type == "bind":
                    source = mount.get("Source")
                    if source:
                        bind_usage.setdefault(source, []).append(container.name)

        volumes_overview: List[Dict[str, Any]] = []

        for volume in self.docker_client.volumes.list():
            attrs = volume.attrs or {}
            created_at = attrs.get("CreatedAt") or attrs.get("Created") or "-"
            mountpoint = attrs.get("Mountpoint") or attrs.get("MountPoint") or "-"

            volumes_overview.append(
                {
                    "name": volume.name,
                    "type": "volume",
                    "driver": attrs.get("Driver", "volume"),
                    "mountpoint": mountpoint,
                    "created": created_at,
                    "used_by": volume_usage.get(volume.name, []),
                }
            )

        for path, containers_using in bind_usage.items():
            created_at = "-"
            try:
                created_at = datetime.fromtimestamp(os.path.getctime(path)).isoformat()
            except Exception:
                pass

            volumes_overview.append(
                {
                    "name": path,
                    "type": "bind",
                    "driver": "bind",
                    "mountpoint": path,
                    "created": created_at,
                    "used_by": containers_using,
                }
            )

        volumes_overview.sort(key=lambda vol: (vol.get("type", ""), vol.get("name", "").lower()))
        return volumes_overview

    def remove_volume(self, name: str, volume_type: str = "volume") -> None:
        if volume_type == "bind":
            abs_path = os.path.abspath(name)
            if abs_path in {"/", ""}:
                return

            bind_volumes = [
                vol
                for vol in self.list_volumes_overview()
                if vol.get("type") == "bind"
            ]

            volume_info = next(
                (
                    vol
                    for vol in bind_volumes
                    if os.path.abspath(vol.get("name", "")) == abs_path
                    or os.path.abspath(vol.get("mountpoint", "")) == abs_path
                ),
                None,
            )

            if not volume_info or volume_info.get("used_by"):
                return

            try:
                if os.path.isdir(abs_path):
                    shutil.rmtree(abs_path)
                elif os.path.exists(abs_path):
                    os.remove(abs_path)
            except Exception:
                pass
            return

        try:
            volume = self.docker_client.volumes.get(name)
            volume.remove(force=True)
        except Exception:
            pass

    def list_unused_volumes(self) -> List[Dict[str, Any]]:
        volumes = self.list_volumes_overview()
        return [vol for vol in volumes if vol.get("type") == "volume" and not vol.get("used_by")]

    def remove_unused_volumes(self) -> Dict[str, List[Dict[str, Any]]]:
        removed: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        for volume in self.list_unused_volumes():
            try:
                self.docker_client.volumes.get(volume.get("name")).remove(force=True)
                removed.append(volume)
            except Exception as exc:
                errors.append({**volume, "error": str(exc) or "Unknown error"})

        return {"removed": removed, "errors": errors}

    def _is_protected_network(self, name: str) -> bool:
        return (name or "").lower() in self.SYSTEM_NETWORKS

    def list_networks_overview(self) -> List[Dict[str, Any]]:
        networks_overview: List[Dict[str, Any]] = []

        for network in self.docker_client.networks.list():
            # Ensure we have the full inspection data so container counts are accurate
            try:
                network.reload()
            except Exception:
                self.logger.warning("Unable to reload network %s", getattr(network, "name", ""))
            attrs = network.attrs or {}
            ipam_config = (attrs.get("IPAM", {}) or {}).get("Config") or []
            ipam_entry = ipam_config[0] if ipam_config else {}
            containers = attrs.get("Containers") or {}
            name = attrs.get("Name") or network.name or ""

            networks_overview.append(
                {
                    "id": network.id,
                    "name": name,
                    "driver": attrs.get("Driver", ""),
                    "scope": attrs.get("Scope", ""),
                    "internal": bool(attrs.get("Internal")),
                    "attachable": bool(attrs.get("Attachable")),
                    "ipam_subnet": ipam_entry.get("Subnet", ""),
                    "ipam_gateway": ipam_entry.get("Gateway", ""),
                    "container_count": len(containers),
                    "deletable": not self._is_protected_network(name),
                    "labels": attrs.get("Labels", {}) or {},
                }
            )

        networks_overview.sort(key=lambda net: net.get("name", "").lower())
        return networks_overview

    def inspect_network(self, network_id: str) -> Optional[Dict[str, Any]]:
        try:
            network = self.docker_client.networks.get(network_id)
        except Exception:
            return None

        attrs = network.attrs or {}
        containers_info = []
        raw_containers = attrs.get("Containers") or {}

        for container_id, cfg in raw_containers.items():
            container_name = cfg.get("Name") or container_id
            ip_addr = (cfg.get("IPv4Address") or "").split("/")[0]
            status = "unknown"
            try:
                container_obj = self.docker_client.containers.get(container_id)
                status = container_obj.status or container_obj.attrs.get("State", {}).get("Status", "unknown")
            except Exception:
                pass

            containers_info.append(
                {
                    "id": container_id,
                    "name": container_name,
                    "ip": ip_addr,
                    "status": status,
                }
            )

        ipam_entries = (attrs.get("IPAM", {}) or {}).get("Config") or []

        return {
            "id": network.id,
            "name": attrs.get("Name") or network.name,
            "driver": attrs.get("Driver", ""),
            "scope": attrs.get("Scope", ""),
            "internal": bool(attrs.get("Internal")),
            "attachable": bool(attrs.get("Attachable")),
            "labels": attrs.get("Labels", {}) or {},
            "ipam": ipam_entries,
            "containers": containers_info,
        }

    def create_network(
        self,
        name: str,
        driver: str = "bridge",
        internal: bool = False,
        attachable: bool = False,
        subnet: Optional[str] = None,
        gateway: Optional[str] = None,
        labels: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        if not name:
            raise ValueError("Network name is required")

        ipam_config = None
        if subnet or gateway:
            pool = IPAMPool(subnet=subnet or None, gateway=gateway or None)
            ipam_config = IPAMConfig(pool_configs=[pool])

        self.logger.info("Creating network %s (driver=%s)", name, driver)
        network = self.docker_client.networks.create(
            name,
            driver=driver or "bridge",
            internal=internal,
            attachable=attachable,
            ipam=ipam_config,
            labels=labels or {},
        )
        return self.inspect_network(network.id) or {"id": network.id, "name": name}

    def remove_network(self, network_id: str) -> None:
        network = self.docker_client.networks.get(network_id)
        network_name = network.name or network_id
        if self._is_protected_network(network_name):
            raise ValueError("Protected network")

        self.logger.info("Removing network %s", network_name)
        network.remove()

    def connect_container_to_network(self, network_id: str, container_id: str) -> None:
        network = self.docker_client.networks.get(network_id)
        self.logger.info("Connecting container %s to network %s", container_id, network.name)
        network.connect(container_id)

    def disconnect_container_from_network(self, network_id: str, container_id: str, force: bool = False) -> None:
        network = self.docker_client.networks.get(network_id)
        self.logger.info("Disconnecting container %s from network %s", container_id, network.name)
        network.disconnect(container_id, force=force)

    def collect_containers_info_for_updates(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        all_containers = self.docker_client.containers.list(all=True)

        containers_info = []

        for c in all_containers:
            state = c.attrs.get("State", {})
            status = state.get("Status", c.status)
            started_at = state.get("StartedAt")

            uptime_str = "-"
            if started_at and status == "running":
                try:
                    started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    delta = (now - started_dt).total_seconds()
                    uptime_str = format_timedelta(delta)
                except Exception:
                    pass

            stack_name = self._get_stack_name(c)
            installed_info = self._get_installed_image_info(c)
            image_ref = installed_info["image_ref"]

            update_config = self._get_update_config(c.id)
            check_ref = self._build_check_reference(image_ref, update_config["track"])

            ports = self._get_container_ports(c)

            if c.image.tags:
                image_name = c.image.tags[0]
            else:
                image_name = c.image.short_id

            remote_info = self._merge_remote_with_installed(
                installed_info, self.get_remote_info(check_ref)
            )
            remote_id = remote_info["remote_id"]
            remote_version = remote_info["remote_version"]

            installed_digest = installed_info.get("installed_digest")
            installed_compare_ref = installed_digest or installed_info["installed_id"]

            if remote_id is None:
                update_state = "unknown"
            else:
                update_state = (
                    "up_to_date"
                    if remote_id == installed_compare_ref
                    else "update_available"
                )

            changelog = remote_info["remote_changelog"] or installed_info["local_changelog"]
            breaking = remote_info["remote_breaking"] or installed_info["local_breaking"]

            stable_id = build_stable_id({"stack": stack_name, "name": c.name})

            installed_display_version = self._format_display_version(
                installed_info.get("installed_tag"),
                installed_info.get("installed_version"),
                installed_info.get("installed_digest_short"),
            )
            remote_display_version = self._format_display_version(
                remote_info.get("remote_tag"),
                remote_info.get("remote_version"),
                remote_info.get("remote_id_short"),
            )

            containers_info.append(
                {
                    "id": c.id,
                    "short_id": c.short_id,
                    "name": c.name,
                    "stack": stack_name,
                    "stable_id": stable_id,
                    "image": image_name,
                    "status": status,
                    "uptime": uptime_str,
                    "image_ref": image_ref,
                    "installed_id_short": installed_info.get("installed_digest_short")
                    or installed_info["installed_id_short"],
                    "installed_version": installed_info["installed_version"],
                    "installed_display_version": installed_display_version,
                    "installed_tag": installed_info.get("installed_tag"),
                    "remote_id_short": remote_info["remote_id_short"],
                    "remote_version": remote_version,
                    "remote_display_version": remote_display_version,
                    "remote_tag": remote_info.get("remote_tag"),
                    "update_state": update_state,
                    "changelog": changelog,
                    "breaking_changes": breaking,
                    "ports": ports,
                    "check_tag": update_config["track"],
                }
            )

        containers_info.sort(key=lambda x: (x["stack"], x["name"].lower()))
        return containers_info

    def get_container_update_info(
        self, container_id: str, force_refresh: bool = False
    ) -> Optional[Dict[str, Any]]:
        try:
            container = self.docker_client.containers.get(container_id)
        except Exception:
            return None

        installed_info = self._get_installed_image_info(container)
        image_ref = installed_info["image_ref"]

        update_config = self._get_update_config(container_id)
        check_ref = self._build_check_reference(image_ref, update_config["track"])

        if force_refresh:
            with self._lock:
                self.remote_cache_ts[check_ref] = 0

        remote_info = self._merge_remote_with_installed(
            installed_info, self.get_remote_info(check_ref)
        )
        remote_id = remote_info.get("remote_id")

        installed_digest = installed_info.get("installed_digest")
        installed_compare_ref = installed_digest or installed_info.get("installed_id")

        if remote_id is None:
            update_state = "unknown"
        elif installed_compare_ref and remote_id == installed_compare_ref:
            update_state = "up_to_date"
        else:
            update_state = "update_available"

        repo_link = image_ref
        if image_ref and "/" in image_ref:
            repo_link = f"https://hub.docker.com/r/{image_ref.split(':')[0]}"

        frequency = update_config.get("frequency", 60)

        installed_display_version = self._format_display_version(
            installed_info.get("installed_tag"),
            installed_info.get("installed_version"),
            installed_info.get("installed_digest_short"),
        )
        remote_display_version = self._format_display_version(
            remote_info.get("remote_tag"),
            remote_info.get("remote_version"),
            remote_info.get("remote_id_short"),
        )

        return {
            "name": container.name,
            "image_ref": image_ref,
            "installed_version": installed_info.get("installed_version"),
            "installed_display_version": installed_display_version,
            "installed_tag": installed_info.get("installed_tag"),
            "remote_version": remote_info.get("remote_version"),
            "remote_display_version": remote_display_version,
            "remote_tag": remote_info.get("remote_tag"),
            "update_state": update_state,
            "remote_id_short": remote_info.get("remote_id_short"),
            "installed_id_short": installed_info.get("installed_digest_short")
            or installed_info.get("installed_id_short"),
            "repo_link": repo_link,
            "frequency_minutes": frequency,
            "check_tag": update_config.get("track"),
        }

    def set_update_frequency(self, container_id: str, minutes: int) -> int:
        minutes = max(5, min(minutes, 24 * 60))
        with self._lock:
            prefs = self.update_preferences.get(container_id, {})
            prefs["frequency"] = minutes
            self.update_preferences[container_id] = prefs
        return minutes

    def set_update_track(self, container_id: str, tag: Optional[str]) -> Optional[str]:
        clean_tag = tag.strip() if isinstance(tag, str) else None
        clean_tag = clean_tag or None

        with self._lock:
            prefs = self.update_preferences.get(container_id, {})
            prefs["track"] = clean_tag
            self.update_preferences[container_id] = prefs

        return clean_tag

    def get_container_logs(self, container_id: str, tail: Optional[int] = 100) -> str:
        try:
            container = self.docker_client.containers.get(container_id)
        except Exception:
            return ""

        tail_arg: Optional[Any]
        if tail is None:
            tail_arg = "all"
        elif tail <= 0:
            tail_arg = "all"
        else:
            tail_arg = tail

        try:
            logs = container.logs(tail=tail_arg).decode("utf-8", errors="ignore")
        except Exception:
            logs = ""
        return logs

    def stream_container_logs(
        self,
        container_id: str,
        tail: Optional[int] = 100,
        follow: bool = True,
        timeout: float = 10.0,
    ) -> Iterable[str]:
        try:
            container = self.docker_client.containers.get(container_id)
        except Exception:
            return []

        tail_arg: Optional[Any]
        if tail is None or (isinstance(tail, int) and tail <= 0):
            tail_arg = "all"
        else:
            tail_arg = tail

        try:
            log_stream = container.logs(stream=True, tail=tail_arg, follow=follow)
            start = time.time()
            for chunk in log_stream:
                try:
                    yield chunk.decode("utf-8", errors="ignore").rstrip()
                except Exception:
                    continue
                if timeout and time.time() - start > timeout:
                    break
        except Exception:
            return []

    def _compose_path_from_labels(self, labels: Dict[str, str]) -> Optional[str]:
        config_files = labels.get("com.docker.compose.project.config_files")
        if not config_files:
            return None

        first_file = config_files.split(",")[0].strip()
        if not first_file:
            return None

        if os.path.isabs(first_file):
            return first_file

        working_dir = labels.get("com.docker.compose.project.working_dir")
        if working_dir:
            return os.path.abspath(os.path.join(working_dir, first_file))

        return None

    def _resolve_compose_path_for_container(self, container_id: str) -> Optional[str]:
        try:
            container = self.docker_client.containers.get(container_id)
        except Exception:
            return None

        labels = (container.attrs.get("Config") or {}).get("Labels") or {}
        path = self._compose_path_from_labels(labels)
        if not path and os.path.exists(self.compose_path):
            path = self.compose_path

        return path

    def get_compose_file(self) -> Optional[str]:
        if not os.path.exists(self.compose_path):
            return None
        try:
            with open(self.compose_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    def get_compose_file_for_container(self, container_id: str) -> Optional[Dict[str, str]]:
        path = self._resolve_compose_path_for_container(container_id)
        if not path or not os.path.exists(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return {"content": f.read(), "path": path}
        except Exception:
            return None

    def save_compose_file(self, content: str) -> bool:
        try:
            with open(self.compose_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception:
            return False

    def save_compose_file_for_container(self, container_id: str, content: str) -> bool:
        path = self._resolve_compose_path_for_container(container_id)
        if not path:
            return False

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception:
            return False

    def apply_simple_action(self, container_id: str, action: str):
        try:
            c = self.docker_client.containers.get(container_id)
        except Exception:
            return
        try:
            if action == "start":
                c.start()
            elif action == "stop":
                c.stop()
            elif action == "restart":
                c.restart()
            elif action == "pause":
                c.pause()
            elif action == "unpause":
                c.unpause()
            elif action == "delete":
                self.remove_container(container_id)
        except Exception:
            pass

    def remove_container(self, container_id: str):
        try:
            self.docker_api.remove_container(container_id, force=True)
        except Exception:
            pass

    def recreate_container_with_latest_image(self, container_id: str):
        try:
            c = self.docker_client.containers.get(container_id)
        except Exception:
            return

        attrs = self.docker_api.inspect_container(c.id)
        installed_info = self._get_installed_image_info(c)
        image_ref = installed_info["image_ref"]

        try:
            self.docker_client.images.pull(image_ref)
        except Exception:
            return

        name = attrs.get("Name", "").lstrip("/") or c.name

        config = attrs.get("Config", {}) or {}
        host_config = attrs.get("HostConfig", {}) or {}
        networks = attrs.get("NetworkSettings", {}).get("Networks", {}) or {}

        networking_config = None
        if networks:
            networking_config = {"EndpointsConfig": networks}

        try:
            self.docker_api.remove_container(c.id, force=True)
        except Exception:
            return

        create_kwargs: Dict[str, Any] = {
            "image": image_ref,
            "name": name,
            "environment": config.get("Env"),
            "host_config": host_config,
            "labels": config.get("Labels"),
            "command": config.get("Cmd"),
            "entrypoint": config.get("Entrypoint"),
            "working_dir": config.get("WorkingDir"),
            "user": config.get("User"),
        }

        if config.get("Volumes"):
            create_kwargs["volumes"] = list(config["Volumes"].keys())

        if networking_config:
            create_kwargs["networking_config"] = networking_config

        try:
            new_container = self.docker_api.create_container(**create_kwargs)
            self.docker_api.start(new_container.get("Id"))
        except Exception:
            pass


class MqttManager:
    def __init__(
        self,
        docker_service: DockerService,
        preferences: AutodiscoveryPreferences,
        broker: Optional[str],
        port: int,
        username: Optional[str],
        password: Optional[str],
        base_topic: str,
        discovery_prefix: str,
        node_id: str,
        state_interval: int,
        logger,
    ):
        self.docker_service = docker_service
        self.preferences = preferences
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.base_topic = base_topic
        self.discovery_prefix = discovery_prefix
        self.node_id = node_id
        self.state_interval = state_interval
        self.logger = logger
        self.mqtt_client = None
        self.container_slug_map: Dict[str, str] = {}
        self.publish_history: deque = deque(maxlen=200)

    def _record_publish(self, topic: str, payload: Any, qos: int, retain: bool) -> None:
        try:
            if isinstance(payload, bytes):
                payload_str = payload.decode("utf-8", errors="replace")
            else:
                payload_str = str(payload)
        except Exception:
            payload_str = "<unserializable>"

        self.publish_history.append(
            {
                "topic": topic,
                "payload": payload_str,
                "qos": qos,
                "retain": retain,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _publish(
        self, topic: str, payload: Any, qos: int = 0, retain: bool = False
    ) -> None:
        if not self.mqtt_client:
            return

        try:
            self.mqtt_client.publish(topic, payload, qos=qos, retain=retain)
        finally:
            self._record_publish(topic, payload, qos, retain)

    def get_publish_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        entries = list(self.publish_history)
        if limit > 0:
            entries = entries[-limit:]
        return entries

    def _device_info(self) -> Dict[str, Any]:
        return {
            "identifiers": ["d2ha_server"],
            "name": "d2ha_server",
            "manufacturer": "d2ha_server",
            "model": "Docker stack monitor",
        }

    def _publish_delete_unused_images_button(
        self, device_info: Dict[str, Any], enabled: bool
    ) -> None:
        btn_config_topic = (
            f"{self.discovery_prefix}/button/{self.node_id}/docker_delete_unused_images/config"
        )
        cmd_topic = f"{self.base_topic}/docker/set/delete_unused_images"

        if enabled:
            payload = {
                "name": "Cancella immagini non in uso",
                "command_topic": cmd_topic,
                "unique_id": "d2ha_delete_unused_images",
                "device": device_info,
                "icon": "mdi:trash-can-outline",
            }
            self._publish(btn_config_topic, json.dumps(payload), qos=0, retain=True)
        else:
            self._publish(btn_config_topic, "", qos=0, retain=True)

    def _publish_updates_overview(
        self,
        containers_info: List[Dict[str, Any]],
        device_info: Dict[str, Any],
        enabled: bool,
    ) -> None:
        sensor_config_topic = (
            f"{self.discovery_prefix}/sensor/{self.node_id}/docker_updates/config"
        )
        state_topic = f"{self.base_topic}/docker/updates/state"
        attr_topic = f"{self.base_topic}/docker/updates/attributes"

        if enabled:
            updates = [
                c for c in containers_info if c.get("update_state") == "update_available"
            ]
            payload = {
                "name": "Container da aggiornare",
                "state_topic": state_topic,
                "json_attr_t": attr_topic,
                "unique_id": "d2ha_docker_updates",
                "device": device_info,
                "icon": "mdi:update",
            }

            attributes = {
                "containers": [c.get("name") for c in updates if c.get("name")],
                "updates_pending": len(updates),
            }

            self._publish(sensor_config_topic, json.dumps(payload), qos=0, retain=True)
            self._publish(state_topic, str(len(updates)), qos=0, retain=True)
            self._publish(attr_topic, json.dumps(attributes), qos=0, retain=True)
        else:
            for topic in (sensor_config_topic, state_topic, attr_topic):
                self._publish(topic, "", qos=0, retain=True)

    def _publish_full_update_all_button(
        self, device_info: Dict[str, Any], enabled: bool
    ) -> None:
        btn_config_topic = (
            f"{self.discovery_prefix}/button/{self.node_id}/docker_full_update_all/config"
        )
        cmd_topic = f"{self.base_topic}/docker/set/full_update_all"

        if enabled:
            payload = {
                "name": "Aggiorna tutti i container",
                "command_topic": cmd_topic,
                "unique_id": "d2ha_full_update_all",
                "device": device_info,
                "icon": "mdi:update-all",
            }
            self._publish(btn_config_topic, json.dumps(payload), qos=0, retain=True)
        else:
            self._publish(btn_config_topic, "", qos=0, retain=True)

    def _full_update_all_containers(self) -> None:
        containers_info = self.docker_service.collect_containers_info_for_updates()

        for c in containers_info:
            if self._is_self_container(c):
                continue
            if c.get("update_state") != "update_available":
                continue

            container_id = c.get("id")
            if not container_id:
                continue

            try:
                self.docker_service.recreate_container_with_latest_image(container_id)
            except Exception:
                self.logger.exception(
                    "MQTT action full_update_all failed for %s", container_id
                )

        try:
            self.docker_service.refresh_overview_cache()
        except Exception:
            self.logger.exception("Failed to refresh overview cache after full update")

        try:
            updated = self.docker_service.collect_containers_info_for_updates()
            self.publish_autodiscovery_and_state(updated)
        except Exception:
            self.logger.exception("Failed to refresh MQTT state after full update")

    def _publish_docker_status(
        self,
        containers_info: List[Dict[str, Any]],
        device_info: Dict[str, Any],
        global_preferences: Dict[str, Any],
    ) -> None:
        state_topic = f"{self.base_topic}/docker/state"
        attr_topic = f"{self.base_topic}/docker/attributes"
        config_topic = (
            f"{self.discovery_prefix}/binary_sensor/{self.node_id}/docker_status/config"
        )

        sensor_payload = {
            "name": "Docker engine",
            "state_topic": state_topic,
            "json_attributes_topic": attr_topic,
            "unique_id": "d2ha_docker_status",
            "device": device_info,
            "icon": "mdi:docker",
            "payload_on": "on",
            "payload_off": "off",
            "device_class": "connectivity",
        }

        running_count = sum(1 for c in containers_info if c.get("status") == "running")
        total_count = len(containers_info)
        inactive_count = max(total_count - running_count, 0)

        docker_running = self.docker_service.is_engine_running()
        state = "on" if docker_running else "off"

        updates_pending = sum(
            1 for c in containers_info if c.get("update_state") == "update_available"
        )

        unused_images = 0
        try:
            images_overview = self.docker_service.list_images_overview()
            unused_images = sum(1 for img in images_overview if not img.get("used_by"))
        except Exception:
            unused_images = 0

        attributes = {
            "active_containers": running_count,
            "inactive_containers": inactive_count,
            "total_containers": total_count,
            "updates_pending": updates_pending,
            "unused_images": unused_images,
        }

        self._publish(config_topic, json.dumps(sensor_payload), qos=0, retain=True)
        self._publish(state_topic, state, qos=0, retain=True)
        self._publish(attr_topic, json.dumps(attributes), qos=0, retain=True)

        self._publish_delete_unused_images_button(
            device_info, bool(global_preferences.get("delete_unused_images", True))
        )
        self._publish_updates_overview(
            containers_info,
            device_info,
            bool(global_preferences.get("updates_overview", True)),
        )
        self._publish_full_update_all_button(
            device_info, bool(global_preferences.get("full_update_all", True))
        )

    def _is_self_container(self, container_info: Dict[str, Any]) -> bool:
        """Return True if the container represents the d2ha instance itself.

        We want to avoid exposing the application container to Home Assistant
        (no state sensor, no buttons). Matching is based on well-known
        identifiers that default to the node_id/base_topic, along with the
        canonical container name used in docker-compose.
        """

        name = (container_info.get("name") or "").lower()
        if not name:
            return False

        known_identifiers = {
            (self.node_id or "").lower(),
            (self.base_topic or "").lower(),
            "d2ha_server",
            "d2ha",
        }
        known_identifiers.discard("")

        if name in known_identifiers:
            return True

        name_parts = [part for part in re.split(r"[./:_-]+", name) if part]
        return any(part in known_identifiers for part in name_parts)

    def is_self_container(self, container_info: Dict[str, Any]) -> bool:
        """Public wrapper around :meth:`_is_self_container`.

        Exposing this check allows the web UI to hide the D2HA container from
        manual autodiscovery configuration while keeping the MQTT filtering
        logic centralized.
        """

        return self._is_self_container(container_info)

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        topic = f"{self.base_topic}/+/set/+"
        self.logger.info("MQTT connected with result code %s, subscribing to %s", rc, topic)
        try:
            client.subscribe(topic)
        except Exception:
            self.logger.exception("MQTT subscription to %s failed", topic)

    def _on_message(self, client, userdata, msg):
        self.logger.info("MQTT message received on %s: %s", msg.topic, msg.payload)
        parts = msg.topic.split("/")
        if len(parts) < 4:
            return
        if parts[0] != self.base_topic or parts[2] != "set":
            return

        slug = parts[1]
        action = parts[3].lower()

        if slug == "docker" and action == "delete_unused_images":
            try:
                self.docker_service.remove_unused_images()
            except Exception:
                self.logger.exception("MQTT action delete_unused_images failed")
            return

        if slug == "docker" and action == "full_update_all":
            try:
                self._full_update_all_containers()
            except Exception:
                self.logger.exception("MQTT action full_update_all failed")
            return

        container_id = self.container_slug_map.get(slug)
        if not container_id:
            self.logger.warning("MQTT message for unknown container slug: %s", slug)
            return

        try:
            if action in ("start", "stop", "restart", "pause", "unpause"):
                self.docker_service.apply_simple_action(container_id, action)
            elif action == "delete":
                self.docker_service.remove_container(container_id)
            elif action == "full_update":
                self.docker_service.recreate_container_with_latest_image(container_id)
        except Exception:
            self.logger.exception("MQTT action %s failed for container %s", action, container_id)

    def setup(self):
        if mqtt is None or not self.broker:
            return
        self.logger.info("Connecting to MQTT broker %s:%s", self.broker, self.port)
        client_kwargs = {"client_id": self.node_id, "clean_session": True}

        callback_api_version = getattr(mqtt, "CallbackAPIVersion", None)
        if callback_api_version is not None:
            client_kwargs["callback_api_version"] = callback_api_version.VERSION2

        client = mqtt.Client(**client_kwargs)
        if self.username or self.password:
            client.username_pw_set(self.username, self.password)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        try:
            client.connect(self.broker, self.port, keepalive=60)
            client.loop_start()
            self.mqtt_client = client
            self.logger.info("MQTT connection established")
        except Exception:
            self.mqtt_client = None
            self.logger.exception("MQTT connection failed")

    def is_connected(self) -> bool:
        if self.mqtt_client is None:
            return False

        checker = getattr(self.mqtt_client, "is_connected", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return True

        return True

    def _clear_state_topics(self, slug: str):
        state_topic = f"{self.base_topic}/{slug}/state"
        attr_topic = f"{self.base_topic}/{slug}/attributes"

        sensor_config_topic = (
            f"{self.discovery_prefix}/sensor/{self.node_id}/{slug}_status/config"
        )

        self._publish(sensor_config_topic, "", qos=0, retain=True)
        self._publish(state_topic, "", qos=0, retain=True)
        self._publish(attr_topic, "", qos=0, retain=True)

    def _clear_action_button(self, slug: str, action: str):
        btn_config_topic = (
            f"{self.discovery_prefix}/button/{self.node_id}/{slug}_{action}/config"
        )
        self._publish(btn_config_topic, "", qos=0, retain=True)

    def _publish_discovery_for_container(
        self, c: Dict[str, Any], device_info: Dict[str, Any], preferences: Dict[str, Any]
    ):
        slug = slugify_container(c["name"], c["short_id"])
        stable_id = build_stable_id(c)
        self.container_slug_map[slug] = c["id"]

        state_topic = f"{self.base_topic}/{slug}/state"
        attr_topic = f"{self.base_topic}/{slug}/attributes"

        sensor_config_topic = (
            f"{self.discovery_prefix}/sensor/{self.node_id}/{slug}_status/config"
        )

        if preferences.get("state", True):
            sensor_payload = {
                "name": f"{c['name']} Stato",
                "state_topic": state_topic,
                "json_attributes_topic": attr_topic,
                # ATTENZIONE: unique_id basata su stack+nome (stable_id), non sull'ID Docker,
                # per evitare entità duplicate in Home Assistant (sensor.xxx, sensor.xxx_2, etc.)
                "unique_id": f"d2ha_{stable_id}_status",
                "device": device_info,
                "icon": "mdi:docker",
            }

            self._publish(
                sensor_config_topic, json.dumps(sensor_payload), qos=0, retain=True
            )

            attrs = {
                "container": c["name"],
                "stack": c["stack"],
                "image": c["image_ref"],
                "installed_version": c["installed_version"],
                "remote_version": c["remote_version"],
                "update_state": c["update_state"],
                "changelog": c["changelog"] or "",
                "breaking_changes": c["breaking_changes"] or "",
                "ports": c.get("ports", {}),
            }

            self._publish(state_topic, c["status"], qos=0, retain=True)
            self._publish(attr_topic, json.dumps(attrs), qos=0, retain=True)
        else:
            self._clear_state_topics(slug)

        actions = [
            ("start", "Start"),
            ("pause", "Pausa"),
            ("stop", "Stop"),
            ("restart", "Riavvia"),
            ("delete", "Elimina"),
            ("full_update", "Aggiorna (pull + ricrea)"),
        ]

        actions_pref = preferences.get("actions", {})
        for action, label in actions:
            if actions_pref.get(action, True):
                btn_config_topic = (
                    f"{self.discovery_prefix}/button/{self.node_id}/{slug}_{action}/config"
                )
                cmd_topic = f"{self.base_topic}/{slug}/set/{action}"
                btn_payload = {
                    "name": f"{c['name']} {label}",
                    "command_topic": cmd_topic,
                    # ATTENZIONE: unique_id basata su stack+nome (stable_id), non sull'ID Docker,
                    # per evitare entità duplicate in Home Assistant (sensor.xxx, sensor.xxx_2, etc.)
                    "unique_id": f"d2ha_{stable_id}_{action}",
                    "device": device_info,
                }
                self._publish(btn_config_topic, json.dumps(btn_payload), qos=0, retain=True)
            else:
                self._clear_action_button(slug, action)

    def publish_autodiscovery_and_state(self, containers_info: List[Dict[str, Any]]):
        if self.mqtt_client is None:
            return

        device_info = self._device_info()

        try:
            global_preferences = self.preferences.get_global_preferences()
            self._publish_docker_status(
                containers_info, device_info, global_preferences
            )
        except Exception:
            self.logger.exception("Failed MQTT publish for Docker status")

        current_slugs = set()
        for c in containers_info:
            if self._is_self_container(c):
                continue

            slug = slugify_container(c["name"], c["short_id"])
            current_slugs.add(slug)

            try:
                preferences = self.preferences.get_with_defaults(c["stable_id"])
                self._publish_discovery_for_container(c, device_info, preferences)
            except Exception:
                self.logger.exception("Failed MQTT publish for container %s", c["name"])

        stale_slugs = set(self.container_slug_map.keys()) - current_slugs
        for stale_slug in stale_slugs:
            state_topic = f"{self.base_topic}/{stale_slug}/state"
            attr_topic = f"{self.base_topic}/{stale_slug}/attributes"

            sensor_config_topic = (
                f"{self.discovery_prefix}/sensor/{self.node_id}/{stale_slug}_status/config"
            )

            try:
                self._publish(sensor_config_topic, "", qos=0, retain=True)
                self._publish(state_topic, "", qos=0, retain=True)
                self._publish(attr_topic, "", qos=0, retain=True)
            except Exception:
                self.logger.exception(
                    "Failed to clear MQTT config/state for stale slug %s", stale_slug
                )

            for action in (
                "start",
                "pause",
                "stop",
                "restart",
                "delete",
                "full_update",
            ):
                btn_config_topic = (
                    f"{self.discovery_prefix}/button/{self.node_id}/{stale_slug}_{action}/config"
                )
                try:
                    self._publish(btn_config_topic, "", qos=0, retain=True)
                except Exception:
                    self.logger.exception(
                        "Failed to clear MQTT button config for stale slug %s", stale_slug
                    )

            self.container_slug_map.pop(stale_slug, None)

    def _periodic_publisher(self):
        while True:
            try:
                containers_info = self.docker_service.collect_containers_info_for_updates()
                self.publish_autodiscovery_and_state(containers_info)
            except Exception:
                self.logger.exception("MQTT periodic publish failed")
            time.sleep(self.state_interval)

    def start_periodic_publisher(self):
        if mqtt is None or not self.broker:
            return
        thread = threading.Thread(
            target=self._periodic_publisher, name="mqtt_publisher", daemon=True
        )
        thread.start()
