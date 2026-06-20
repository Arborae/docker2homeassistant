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

class DockerImagesUpdatesMixin:
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

    def recreate_container_with_latest_image(self, container_id: str):
        self.logger.info("Starting full update for container %s", container_id)

        # Step 1: Get container info before stopping it
        try:
            c = self.docker_client.containers.get(container_id)
            self.logger.debug("Found container: %s", c.name)
        except Exception as e:
            self.logger.warning("Container not found: %s", container_id)
            raise RuntimeError(f"Container non trovato: {container_id}") from e

        attrs = self.docker_api.inspect_container(c.id)
        installed_info = self._get_installed_image_info(c)
        image_ref = installed_info["image_ref"]
        old_image_id = installed_info.get("installed_id")

        name = attrs.get("Name", "").lstrip("/") or c.name
        config = attrs.get("Config", {}) or {}
        host_config = attrs.get("HostConfig", {}) or {}
        networks = attrs.get("NetworkSettings", {}).get("Networks", {}) or {}

        self.logger.debug(
            "Container: %s | image ref: %s | current image ID: %s",
            name, image_ref, old_image_id,
        )

        # Step 2: STOP AND REMOVE the container FIRST
        try:
            self.logger.debug("Stopping and removing container: %s", name)
            self.docker_api.remove_container(c.id, force=True)
            self.logger.debug("Container removed")
        except Exception as e:
            self.logger.error("Remove container failed: %s", e)
            raise RuntimeError(f"Errore nella rimozione del container {name}: {e}") from e

        # Step 3: NOW we can remove the image tag to force fresh pull
        try:
            self.logger.debug("Removing old image tag: %s", image_ref)
            self.docker_api.remove_image(image_ref, force=False, noprune=True)
            self.logger.debug("Old image tag removed - will force fresh pull")
        except Exception as untag_err:
            self.logger.debug("Could not remove image tag (will try pull anyway): %s", untag_err)

        # Step 4: Pull fresh image
        try:
            self.logger.debug("Pulling fresh image: %s", image_ref)

            for line in self.docker_api.pull(image_ref, stream=True, decode=True):
                status = line.get("status", "")
                progress = line.get("progress", "")
                if status and ("Pulling" in status or "Downloading" in status
                               or "Extracting" in status or "Pull complete" in status):
                    self.logger.debug("%s %s", status, progress)

            pulled_image = self.docker_client.images.get(image_ref)
            new_image_id = pulled_image.id if pulled_image else "unknown"
            self.logger.debug("Pulled image ID: %s", new_image_id)

            if old_image_id and new_image_id == old_image_id:
                self.logger.info("No update available on registry for %s (same image ID)", name)
            else:
                self.logger.info(
                    "New image downloaded for %s (old: %s, new: %s)",
                    name, old_image_id[:19] if old_image_id else "none", new_image_id[:19],
                )
        except Exception as e:
            self.logger.error("Pull failed: %s", e)
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
            self.logger.debug("Creating new container: %s", name)
            new_container = self.docker_api.create_container(**create_kwargs)
            new_container_id = new_container.get("Id")

            self.docker_api.start(new_container_id)
            self.logger.info("Container %s started successfully", name)

            # Verify
            new_c = self.docker_client.containers.get(new_container_id)
            self.logger.debug("Verification - container image: %s", new_c.image.id[:19])
            self.logger.info("Full update completed for %s", name)
            return new_container_id
        except Exception as e:
            self.logger.error("Create/start failed: %s", e)
            raise RuntimeError(f"Errore nella creazione/avvio del container {name}: {e}") from e

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
        self.docker_client.images.remove(image_id)

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

