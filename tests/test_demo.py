import os
import sys

from conftest import FakeReleases

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import demo  # noqa: E402
from app import create_app  # noqa: E402
from config import Config  # noqa: E402
from security import new_api_token  # noqa: E402


def test_the_demo_answers_like_the_real_app(tmp_path):
    """The demo backs the screenshots and the integration tests: its fake Docker must stay complete."""
    config = Config(data_dir=str(tmp_path), start_background=False, version="demo")
    app = create_app(config, ops=demo.DemoOps(), registry=demo.DemoRegistry(), releases=FakeReleases())
    d2ha = app.extensions["d2ha"]
    token, token_hash = new_api_token()
    d2ha.store.update(lambda d: d.update(api_token_hash=token_hash))
    d2ha.checker.check()
    client = app.test_client()
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/v1/info", headers=headers).status_code == 200
    response = client.get("/api/v1/containers?ha=1", headers=headers)
    assert response.status_code == 200
    shown = {c["name"]: c for c in response.get_json()["containers"]}
    assert shown["jellyfin"]["update"] == "available" and shown["jellyfin"]["can_update"]
    proxy = shown[demo.PROXY]
    assert proxy["is_proxy"] and proxy["update"] == "available" and not proxy["can_update"]
    assert "d2ha" not in shown  # D2HA itself is never shown in Home Assistant
