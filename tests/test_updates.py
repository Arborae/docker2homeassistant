import importlib
import sys
from pathlib import Path
from unittest import mock

from flask import get_flashed_messages, session


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_app_module():
    sys.path.append(str(PROJECT_ROOT / "d2ha"))

    mock_client = mock.MagicMock()
    mock_client.api = mock.MagicMock()
    mock_client.info.return_value = {}

    with mock.patch("docker.from_env", return_value=mock_client), mock.patch(
        "docker_service.DockerService.start_overview_refresher"
    ), mock.patch("docker_service.MqttManager.start_periodic_publisher"), mock.patch(
        "docker_service.MqttManager.setup"
    ):
        # Ensure a fresh import for each test run
        sys.modules.pop("app", None)
        app_module = importlib.import_module("app")

    return app_module


def create_docker_service():
    sys.path.append(str(PROJECT_ROOT / "d2ha"))

    mock_client = mock.MagicMock()
    mock_client.api = mock.MagicMock()
    mock_client.info.return_value = {}

    with mock.patch("docker.from_env", return_value=mock_client):
        import docker_service

        return docker_service.DockerService()


def test_updates_page_handles_docker_errors_gracefully():
    app_module = load_app_module()

    app_module.docker_service.collect_containers_info_for_updates = mock.Mock(
        side_effect=Exception("boom")
    )
    app_module.docker_service.get_cached_overview = mock.Mock(return_value=[])
    app_module.docker_service.get_host_info = mock.Mock(return_value={})
    app_module.docker_service.get_disk_usage = mock.Mock(return_value={})

    app_module.is_onboarding_done = lambda: True

    with app_module.app.test_request_context("/updates"):
        session["user"] = app_module.get_auth_config().get("username")
        with mock.patch.object(app_module, "render_template", return_value="OK"):
            response = app_module.updates()
            flashes = get_flashed_messages(with_categories=True)

    assert response == "OK"
    assert ("error", "Impossibile caricare gli aggiornamenti. Riprova più tardi.") in flashes


def test_extract_version_supports_home_assistant_labels():
    service = create_docker_service()

    version = service._extract_version({
        "io.hass.version": "2025.12.5",
        "org.opencontainers.image.version": "2024.1.0",
    })

    assert version == "2025.12.5"


def test_fetch_remote_info_prefers_home_assistant_annotations():
    service = create_docker_service()
    service.docker_api.inspect_distribution.return_value = {
        "Descriptor": {
            "annotations": {
                "io.hass.version": "2025.12.5",
                "org.opencontainers.image.version": "2024.1.0",
            }
        },
        "Digest": "sha256:deadbeef",
    }

    info = service._fetch_remote_info("ghcr.io/homeassistant/home-assistant:latest")

    assert info.get("remote_version") == "2025.12.5"

