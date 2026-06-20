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

class DockerVolumesMixin:
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

