import unittest
from unittest import mock

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "d2ha"))
import docker_service  # noqa: E402


class DummyNetwork:
    def __init__(self, net_id, name, attrs):
        self.id = net_id
        self.name = name
        self.attrs = attrs

    def remove(self):
        self.removed = True

    def connect(self, container_id):
        self.last_connected = container_id

    def disconnect(self, container_id, force=False):
        self.last_disconnected = (container_id, force)


class DockerServiceNetworkTests(unittest.TestCase):
    def setUp(self):
        self.client = mock.MagicMock()
        self.client.networks = mock.MagicMock()
        self.client.containers = mock.MagicMock()
        patcher = mock.patch.object(docker_service, "docker", autospec=True)
        self.addCleanup(patcher.stop)
        self.mock_docker = patcher.start()
        self.mock_docker.from_env.return_value = self.client
        self.service = docker_service.DockerService()

    def test_list_networks_overview_collects_basic_fields(self):
        network = DummyNetwork(
            "123",
            "mynet",
            {
                "Name": "mynet",
                "Driver": "bridge",
                "Scope": "local",
                "Internal": False,
                "Attachable": True,
                "IPAM": {"Config": [{"Subnet": "10.0.0.0/24", "Gateway": "10.0.0.1"}]},
                "Containers": {"abc": {}},
            },
        )
        self.client.networks.list.return_value = [network]

        data = self.service.list_networks_overview()

        self.assertEqual(len(data), 1)
        entry = data[0]
        self.assertEqual(entry["name"], "mynet")
        self.assertEqual(entry["driver"], "bridge")
        self.assertEqual(entry["ipam_subnet"], "10.0.0.0/24")
        self.assertEqual(entry["ipam_gateway"], "10.0.0.1")
        self.assertEqual(entry["container_count"], 1)
        self.assertTrue(entry["deletable"])

    def test_protected_networks_cannot_be_removed(self):
        protected = DummyNetwork("bridge-id", "bridge", {})
        self.client.networks.get.return_value = protected

        with self.assertRaises(ValueError):
            self.service.remove_network("bridge-id")

    def test_create_network_requires_name(self):
        with self.assertRaises(ValueError):
            self.service.create_network("")


if __name__ == "__main__":
    unittest.main()
