import json
import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError:
    mqtt = None

from services.docker import DockerService
from services.preferences import AutodiscoveryPreferences
from services.utils import build_stable_id, slugify_container

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
