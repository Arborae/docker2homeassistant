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

