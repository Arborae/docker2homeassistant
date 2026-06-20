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

class DockerContainersMixin:
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
            return 0.0, 0, 0.0, 0, 0

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

    def apply_simple_action(self, container_id: str, action: str):
        try:
            c = self.docker_client.containers.get(container_id)
        except Exception as exc:
            raise RuntimeError(f"Container non trovato: {container_id}") from exc

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
        else:
            raise ValueError(f"Azione non supportata: {action}")

    def remove_container(self, container_id: str):
        self.docker_api.remove_container(container_id, force=True)

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

