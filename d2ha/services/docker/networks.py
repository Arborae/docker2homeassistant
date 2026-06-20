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

class DockerNetworksMixin:
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

