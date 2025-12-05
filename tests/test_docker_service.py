import unittest
from unittest import mock

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "d2ha"))
import docker_service


class DockerServiceHostNameTests(unittest.TestCase):
    @mock.patch("docker_service.docker.from_env")
    @mock.patch("docker_service.platform.node")
    def test_load_host_name_fallback(self, mock_platform_node, mock_from_env):
        mock_client = mock.MagicMock()
        mock_client.info.side_effect = Exception("Docker not available")
        mock_from_env.return_value = mock_client
        mock_platform_node.return_value = "test-node"

        service = docker_service.DockerService()
        self.assertEqual(service.host_name, "test-node")
        mock_platform_node.assert_called_once()


if __name__ == "__main__":
    unittest.main()
