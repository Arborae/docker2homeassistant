import pytest

from registry import RegistryClient, RegistryError, parse_ref


@pytest.mark.parametrize("ref, expected", [
    ("nginx", ("registry-1.docker.io", "library/nginx", "latest")),
    ("nginx:1.27", ("registry-1.docker.io", "library/nginx", "1.27")),
    ("linuxserver/radarr:latest", ("registry-1.docker.io", "linuxserver/radarr", "latest")),
    ("docker.io/library/redis:7", ("registry-1.docker.io", "library/redis", "7")),
    ("ghcr.io/home-assistant/home-assistant:stable", ("ghcr.io", "home-assistant/home-assistant", "stable")),
    ("lscr.io/linuxserver/code-server", ("lscr.io", "linuxserver/code-server", "latest")),
    ("localhost:5000/app:dev", ("localhost:5000", "app", "dev")),
    ("registry.local:5000/team/app", ("registry.local:5000", "team/app", "latest")),
    ("nginx@sha256:abc", ("registry-1.docker.io", "library/nginx", "sha256:abc")),
])
def test_parse_ref(ref, expected):
    assert parse_ref(ref) == expected


class FakeResponse:
    def __init__(self, status=200, headers=None, payload=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    """Answers 401 with a bearer challenge until a token is sent."""

    def __init__(self):
        self.headers = {}
        self.calls = []

    def request(self, method, url, headers=None, **kwargs):
        self.calls.append((method, url, dict(headers or {})))
        if "Authorization" not in (headers or {}):
            return FakeResponse(401, {"WWW-Authenticate": 'Bearer realm="https://auth.example/token",service="reg",scope="repository:library/nginx:pull"'})
        return FakeResponse(200, {"Docker-Content-Digest": "sha256:" + "a" * 64})

    def get(self, url, params=None, **kwargs):
        assert url == "https://auth.example/token"
        assert params == {"service": "reg", "scope": "repository:library/nginx:pull"}
        return FakeResponse(200, payload={"token": "t0k", "expires_in": 300})


def test_remote_digest_uses_anonymous_token():
    client = RegistryClient()
    client.session = FakeSession()
    assert client.remote_digest("nginx:latest") == "sha256:" + "a" * 64
    method, url, headers = client.session.calls[-1]
    assert method == "HEAD"
    assert url == "https://registry-1.docker.io/v2/library/nginx/manifests/latest"
    assert headers["Authorization"] == "Bearer t0k"


def test_basic_auth_registries_are_not_supported():
    client = RegistryClient()
    with pytest.raises(RegistryError):
        client._token_for('Basic realm="private"')
