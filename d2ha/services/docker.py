import logging
import os
import re
import shutil
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

import docker
from docker.models.containers import Container
from docker.types import IPAMConfig, IPAMPool
from docker.utils import parse_repository_tag

from .utils import build_stable_id, format_timedelta, human_bytes

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
            return info.get("Name") or os.uname().nodename
        except Exception:
            return os.uname().nodename

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
            "io.hass.version",
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

    def _extract_github_repo(self, labels: dict, image_ref: str) -> Optional[Tuple[str, str]]:
        """Extract GitHub owner/repo from image labels or image reference.
        
        Returns (owner, repo) tuple or None if not found.
        """
        # Try to get from labels first
        source_url = None
        for key in (
            "org.opencontainers.image.source",
            "org.opencontainers.image.url",
            "org.label-schema.vcs-url",
        ):
            if labels.get(key):
                source_url = labels[key]
                break
        
        # Parse GitHub URL
        if source_url:
            match = re.search(r"github\.com[/:]([^/]+)/([^/.\s]+)", source_url)
            if match:
                return (match.group(1), match.group(2).replace(".git", ""))
        
        # Try to infer from image reference (ghcr.io/owner/repo:tag)
        if image_ref:
            if "ghcr.io/" in image_ref:
                # ghcr.io/home-assistant/home-assistant:stable
                parts = image_ref.split("ghcr.io/")[-1].split(":")[0].split("/")
                if len(parts) >= 2:
                    return (parts[0], parts[1])
            
            # Try docker.io/library format or direct owner/repo
            clean_ref = image_ref.split(":")[0].split("@")[0]
            if "/" in clean_ref:
                parts = clean_ref.split("/")
                # Skip registry part if present
                if "." in parts[0]:  # Registry like docker.io, gcr.io
                    parts = parts[1:]
                if len(parts) >= 2:
                    return (parts[0], parts[1])
        
        return None

    def _fetch_github_release_info(
        self, owner: str, repo: str, version: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch release information from GitHub API.
        
        Returns dict with changelog, changelog_url, breaking_changes, release_date.
        """
        cache_key = f"{owner}/{repo}"
        now = time.time()
        
        # Check cache
        with self._lock:
            if cache_key in self.github_release_cache:
                if now - self.github_release_cache_ts.get(cache_key, 0) < self.github_cache_ttl:
                    cached = self.github_release_cache[cache_key]
                    # If we have a version, try to find matching release
                    if version and "releases" in cached:
                        for rel in cached["releases"]:
                            if self._version_matches_release(version, rel):
                                return self._extract_release_info(rel)
                    # Return latest release info
                    if "latest" in cached:
                        return cached["latest"]
                    return {}
        
        result: Dict[str, Any] = {}
        releases_list: List[Dict[str, Any]] = []
        
        try:
            # Fetch releases from GitHub API
            url = f"https://api.github.com/repos/{owner}/{repo}/releases"
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Docker2HomeAssistant/1.0"
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                releases = response.json()
                releases_list = releases[:20]  # Keep last 20 releases
                
                if releases:
                    # Extract latest release info
                    latest = releases[0]
                    result = self._extract_release_info(latest)
                    
                    # If we have a version, try to find matching release
                    if version:
                        for rel in releases:
                            if self._version_matches_release(version, rel):
                                result = self._extract_release_info(rel)
                                break
            else:
                self.logger.debug(f"GitHub API returned {response.status_code} for {owner}/{repo}")
        except requests.RequestException as e:
            self.logger.debug(f"Failed to fetch GitHub releases for {owner}/{repo}: {e}")
        except Exception as e:
            self.logger.debug(f"Error processing GitHub releases for {owner}/{repo}: {e}")
        
        # Cache results
        with self._lock:
            self.github_release_cache[cache_key] = {
                "releases": releases_list,
                "latest": result
            }
            self.github_release_cache_ts[cache_key] = now
        
        return result

    def _version_matches_release(self, version: str, release: Dict[str, Any]) -> bool:
        """Check if a version string matches a GitHub release."""
        tag = release.get("tag_name", "")
        name = release.get("name", "")
        
        # Normalize version (remove 'v' prefix if present)
        v_normalized = version.lstrip("v").lower()
        tag_normalized = tag.lstrip("v").lower()
        name_normalized = name.lstrip("v").lower()
        
        return v_normalized == tag_normalized or v_normalized == name_normalized or version in tag or version in name

    def _extract_release_info(self, release: Dict[str, Any]) -> Dict[str, Any]:
        """Extract relevant info from a GitHub release object."""
        body = release.get("body", "") or ""
        
        # Try to extract breaking changes section
        breaking = None
        breaking_patterns = [
            r"(?:## )?Breaking [Cc]hanges?\s*\n([\s\S]*?)(?=\n## |\Z)",
            r"(?:## )?BREAKING\s*\n([\s\S]*?)(?=\n## |\Z)",
            r"\*\*Breaking [Cc]hanges?\*\*:?\s*([\s\S]*?)(?=\n\*\*|\n## |\Z)",
        ]
        for pattern in breaking_patterns:
            match = re.search(pattern, body)
            if match:
                breaking = match.group(1).strip()
                break
        
        return {
            "changelog": body[:2000] if body else None,  # Limit length
            "changelog_url": release.get("html_url"),
            "breaking_changes": breaking,
            "release_date": release.get("published_at", "")[:10] if release.get("published_at") else None,
            "release_name": release.get("name") or release.get("tag_name"),
        }

    @staticmethod
    def _format_display_version(
        channel: Optional[str], version: Optional[str], digest_short: Optional[str]
    ) -> Optional[str]:
        """Return a human-friendly version string for UI display."""

        channel = (channel or "").strip()
        version = (version or "").strip()
        digest_short = (digest_short or "").strip()

        # If we have a version, prioritize it
        if version:
            # If version is just a number/semver, add V. prefix
            # Check if it starts with digit or 'v'
            if version[0].isdigit() or version.lower().startswith("v"):
                # Ensure it starts with V. for consistency if it's semantic
                if version.lower().startswith("v"):
                    return f"V. {version[1:]}"
                return f"V. {version}"
            return version

        if channel:
            if digest_short:
                return f"{channel} ({digest_short})"
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
        
        # Handle changelog URL logic for local info too
        remote_changelog = remote_info.get("remote_changelog")
        changelog_url = remote_info.get("remote_changelog_url")
        
        if not remote_changelog and not changelog_url:
            local_log = installed_info.get("local_changelog")
            if local_log:
                if local_log.startswith("http://") or local_log.startswith("https://"):
                    changelog_url = local_log
                else:
                    remote_changelog = local_log

        return {
            "remote_id": remote_id,
            "remote_id_short": remote_id_short,
            "remote_version": remote_version,
            "remote_tag": remote_tag,
            "remote_changelog": remote_changelog,
            "remote_changelog_url": changelog_url,
            "remote_breaking": remote_info.get("remote_breaking"),
            "remote_release_date": remote_info.get("remote_release_date"),
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
                "remote_changelog_url": None,
                "remote_breaking": None,
                "remote_release_date": None,
            }

        descriptor = distribution.get("Descriptor") or {}
        annotations = descriptor.get("annotations") or {}

        remote_id = descriptor.get("digest") or distribution.get("Digest")
        remote_short = remote_id.split(":")[-1][:12] if remote_id else None

        remote_version = (
            annotations.get("io.hass.version")
            or annotations.get("org.opencontainers.image.version")
            or annotations.get("version")
            or annotations.get("org.opencontainers.image.revision")
            or reference_tag
            or remote_short
        )

        # Get changelog from annotations first
        remote_changelog = annotations.get("org.opencontainers.image.changelog")
        remote_breaking = annotations.get("org.opencontainers.image.breaking_changes")
        changelog_url = None
        release_date = None

        # Check if remote_changelog is just a URL
        is_url = False
        if remote_changelog and (remote_changelog.startswith("http://") or remote_changelog.startswith("https://")):
            changelog_url = remote_changelog
            remote_changelog = None # Clear it so we try to fetch text content from GitHub
            is_url = True

        # If no changelog (or it was just a URL), try GitHub API
        if not remote_changelog or is_url:
            github_repo = self._extract_github_repo(annotations, image_ref)
            if github_repo:
                owner, repo = github_repo
                try:
                    github_info = self._fetch_github_release_info(owner, repo, remote_version)
                    if github_info:
                        if github_info.get("changelog"):
                            remote_changelog = github_info.get("changelog")
                        if github_info.get("changelog_url"):
                            changelog_url = github_info.get("changelog_url")
                        release_date = github_info.get("release_date")
                        
                        # Use GitHub release version if available to get clean version number
                        if github_info.get("release_name"):
                            # If release_name looks like a version, use it
                            gh_ver = github_info.get("release_name")
                            if gh_ver and (gh_ver[0].isdigit() or gh_ver.lower().startswith("v")):
                                remote_version = gh_ver
                        
                        if not remote_breaking:
                            remote_breaking = github_info.get("breaking_changes")
                except Exception as e:
                    self.logger.debug(f"GitHub fetch failed: {e}")

        return {
            "remote_id": remote_id,
            "remote_id_short": remote_short,
            "remote_version": remote_version,
            "remote_tag": reference_tag,
            "remote_changelog": remote_changelog,
            "remote_changelog_url": changelog_url,
            "remote_breaking": remote_breaking,
            "remote_release_date": release_date,
        }

    def get_remote_info(self, image_ref: str, ttl: Optional[float] = None) -> Dict[str, Any]:
        now = time.time()
        effective_ttl = ttl if ttl is not None else self.remote_cache_ttl

        with self._lock:
            cached_ts = self.remote_cache_ts.get(image_ref, 0)
            if now - cached_ts <= effective_ttl and image_ref in self.remote_cache:
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
                installed_info,
                self.get_remote_info(check_ref, ttl=update_config["frequency"] * 60),
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
                    "changelog_url": remote_info.get("remote_changelog_url"),
                    "breaking_changes": breaking,
                    "release_date": remote_info.get("remote_release_date"),
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
            installed_info,
            self.get_remote_info(check_ref, ttl=0 if force_refresh else update_config["frequency"] * 60),
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
        import sys
        print(f"[DOCKER] === Starting full update for container {container_id} ===", file=sys.stderr, flush=True)
        
        # Step 1: Get container info before stopping it
        try:
            c = self.docker_client.containers.get(container_id)
            print(f"[DOCKER] Found container: {c.name}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[DOCKER] Container not found: {container_id}", file=sys.stderr, flush=True)
            raise RuntimeError(f"Container non trovato: {container_id}") from e

        attrs = self.docker_api.inspect_container(c.id)
        installed_info = self._get_installed_image_info(c)
        image_ref = installed_info["image_ref"]
        old_image_id = installed_info.get("installed_id")
        
        name = attrs.get("Name", "").lstrip("/") or c.name
        config = attrs.get("Config", {}) or {}
        host_config = attrs.get("HostConfig", {}) or {}
        networks = attrs.get("NetworkSettings", {}).get("Networks", {}) or {}
        
        print(f"[DOCKER] Container: {name}", file=sys.stderr, flush=True)
        print(f"[DOCKER] Image ref: {image_ref}", file=sys.stderr, flush=True)
        print(f"[DOCKER] Current image ID: {old_image_id}", file=sys.stderr, flush=True)

        # Step 2: STOP AND REMOVE the container FIRST
        try:
            print(f"[DOCKER] Stopping and removing container: {name}", file=sys.stderr, flush=True)
            self.docker_api.remove_container(c.id, force=True)
            print(f"[DOCKER] Container removed", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[DOCKER] Remove container failed: {e}", file=sys.stderr, flush=True)
            raise RuntimeError(f"Errore nella rimozione del container {name}: {e}") from e

        # Step 3: NOW we can remove the image tag to force fresh pull
        try:
            print(f"[DOCKER] Removing old image tag: {image_ref}", file=sys.stderr, flush=True)
            self.docker_api.remove_image(image_ref, force=False, noprune=True)
            print(f"[DOCKER] Old image tag removed - will force fresh pull", file=sys.stderr, flush=True)
        except Exception as untag_err:
            print(f"[DOCKER] Could not remove image tag (will try pull anyway): {untag_err}", file=sys.stderr, flush=True)

        # Step 4: Pull fresh image
        try:
            print(f"[DOCKER] Pulling fresh image: {image_ref}", file=sys.stderr, flush=True)
            
            for line in self.docker_api.pull(image_ref, stream=True, decode=True):
                status = line.get("status", "")
                progress = line.get("progress", "")
                if status and "Pulling" in status or "Downloading" in status or "Extracting" in status or "Pull complete" in status:
                    msg = f"[DOCKER] {status}"
                    if progress:
                        msg += f" {progress}"
                    print(msg, file=sys.stderr, flush=True)
            
            pulled_image = self.docker_client.images.get(image_ref)
            new_image_id = pulled_image.id if pulled_image else "unknown"
            print(f"[DOCKER] Pulled image ID: {new_image_id}", file=sys.stderr, flush=True)
            
            if old_image_id and new_image_id == old_image_id:
                print(f"[DOCKER] WARNING: Same image ID - no update available on registry", file=sys.stderr, flush=True)
            else:
                print(f"[DOCKER] SUCCESS: New image downloaded!", file=sys.stderr, flush=True)
                print(f"[DOCKER]   Old: {old_image_id[:19] if old_image_id else 'none'}", file=sys.stderr, flush=True)
                print(f"[DOCKER]   New: {new_image_id[:19]}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[DOCKER] Pull failed: {e}", file=sys.stderr, flush=True)
            raise RuntimeError(f"Errore nel pull dell'immagine {image_ref}: {e}") from e

        # Step 5: Create and start new container
        networking_config = None
        if networks:
            networking_config = {"EndpointsConfig": networks}

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
            print(f"[DOCKER] Creating new container: {name}", file=sys.stderr, flush=True)
            new_container = self.docker_api.create_container(**create_kwargs)
            new_container_id = new_container.get("Id")
            
            self.docker_api.start(new_container_id)
            print(f"[DOCKER] Container {name} started successfully", file=sys.stderr, flush=True)
            
            # Verify
            new_c = self.docker_client.containers.get(new_container_id)
            print(f"[DOCKER] Verification - container image: {new_c.image.id[:19]}", file=sys.stderr, flush=True)
            print(f"[DOCKER] === Full update completed for {name} ===", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[DOCKER] Create/start failed: {e}", file=sys.stderr, flush=True)
            raise RuntimeError(f"Errore nella creazione/avvio del container {name}: {e}") from e
