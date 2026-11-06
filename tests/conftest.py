import os
import sys
import time
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "d2ha"))

from app import create_app  # noqa: E402
from config import Config  # noqa: E402
from docker_ops import container_key  # noqa: E402
from security import hash_password  # noqa: E402

PASSWORD = "correct horse battery"


class FakeApi:
    def inspect_distribution(self, ref):
        return {"Descriptor": {"digest": "sha256:" + "f" * 64}}


class FakeOps:
    """Stands in for DockerOps: no Docker needed."""

    def __init__(self):
        self.api = FakeApi()
        self.self_id = ""
        self.recreated: List[str] = []
        self.containers: List[Dict[str, Any]] = [
            self._container("web", "nginx:1.27", "img-web", stack="site"),
            self._container("db", "postgres:16", "img-db", stack="site", state="exited"),
            self._container("tool", "localbuild:latest", "img-local"),
        ]
        self.images = {
            "img-web": {"labels": {"org.opencontainers.image.version": "1.27.0"}, "repo_digests": ["nginx@sha256:" + "1" * 64], "created": ""},
            "img-db": {"labels": {}, "repo_digests": ["postgres@sha256:" + "2" * 64], "created": ""},
            "img-local": {"labels": {}, "repo_digests": [], "created": ""},
        }

    @staticmethod
    def _container(name, image, image_id, stack="", state="running"):
        return {"key": container_key(stack, name), "id": f"id-{name}", "name": name, "stack": stack,
                "image": image, "image_id": image_id, "state": state, "status": "", "health": "",
                "created": 0, "is_self": False, "is_proxy": False}

    def list_containers(self):
        return [dict(c) for c in self.containers]

    def image_info(self, image_id):
        return self.images.get(image_id, {"labels": {}, "repo_digests": [], "created": ""})

    def engine_info(self):
        return {"version": "28.0.0", "arch": "amd64", "os": "linux"}

    def unused_images(self):
        return ["sha256:old"]

    def logs(self, container_id, tail=200):
        return "2026-01-01T00:00:00.000Z hello\n2026-01-01T00:00:01.000Z ERROR boom\n"

    def events(self, since=None):
        return iter(())

    def prune_images(self):
        return {"removed": 1, "reclaimed": 1024}

    def recreate(self, container_id, progress):
        self.recreated.append(container_id)
        progress({"phase": "pull", "percent": 50})
        return {"changed": True, "id": container_id, "name": container_id}


class FakeTelegram:
    """Collects messages instead of sending them."""

    def __init__(self, configured=True, error=None):
        self.configured = configured
        self.error = error
        self.sent: List[str] = []

    def send(self, text):
        if self.error:
            raise self.error
        self.sent.append(text)


class FakeRegistry:
    def __init__(self):
        self.digests = {"nginx:1.27": "sha256:" + "9" * 64, "postgres:16": "sha256:" + "2" * 64}

    def remote_digest(self, ref):
        return self.digests[ref]

    def remote_labels(self, ref, digest, arch="amd64"):
        return {"org.opencontainers.image.version": "1.27.3",
                "org.opencontainers.image.source": "https://github.com/nginx/nginx"}


class FakeReleases:
    def find(self, owner, repo, version):
        return {"version": version, "title": version, "url": f"https://github.com/{owner}/{repo}",
                "published": "2026-01-01", "body": "## Changes\n- fixed", "breaking": ""}


@pytest.fixture
def make_app(tmp_path):
    def factory(read_only=False):
        config = Config(data_dir=str(tmp_path), start_background=False, read_only=read_only, version="test")
        app = create_app(config, ops=FakeOps(), registry=FakeRegistry(), releases=FakeReleases())
        app.config["TESTING"] = True
        return app

    return factory


@pytest.fixture
def app(make_app):
    return make_app()


def set_account(app, username="admin", password=PASSWORD):
    store = app.extensions["d2ha"].store
    store.update(lambda d: d["auth"].update(username=username, password_hash=hash_password(password)))


@pytest.fixture
def client(app):
    """A client already signed in."""
    set_account(app)
    client = app.test_client()
    response = client.post("/login", data={"username": "admin", "password": PASSWORD})
    assert response.status_code == 302
    return client


def csrf_of(client) -> str:
    with client.session_transaction() as session:
        return session["csrf"]


def wait_for(predicate, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False
