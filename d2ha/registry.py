"""Minimal client for the Docker Registry HTTP API v2 (anonymous pulls).

``remote_digest`` uses a HEAD request, which Docker Hub does not count against the
pull rate limit. Labels of the remote image (to know its real version) are fetched
only when the digest changed, and cached by digest.
"""

import re
import threading
import time
from typing import Dict, Optional, Tuple

import requests

MANIFEST_TYPES = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))
INDEX_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
TIMEOUT = 15
USER_AGENT = "D2HA (+https://github.com/Arborae/docker2homeassistant)"


class RegistryError(Exception):
    pass


def parse_ref(ref: str) -> Tuple[str, str, str]:
    """Split an image reference into (registry host, repository path, tag or digest)."""
    name, reference = ref, "latest"
    if "@" in name:
        name, reference = name.split("@", 1)
    else:
        last = name.rsplit("/", 1)[-1]
        if ":" in last:
            name, reference = name.rsplit(":", 1)
    parts = name.split("/", 1)
    if len(parts) == 2 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        host, path = parts
    else:
        host, path = "docker.io", name
    if host in ("docker.io", "index.docker.io", "registry-1.docker.io"):
        host = "registry-1.docker.io"
        if "/" not in path:
            path = f"library/{path}"
    return host, path, reference


class RegistryClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._tokens: Dict[str, Tuple[str, float]] = {}
        self._labels: Dict[str, Dict[str, str]] = {}
        self._lock = threading.Lock()

    # -- auth -------------------------------------------------------------------

    def _token_for(self, challenge: str) -> Optional[str]:
        if not challenge.lower().startswith("bearer "):
            raise RegistryError("unsupported auth")  # e.g. Basic: needs credentials
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = params.pop("realm", "")
        if not realm.startswith("https://"):
            raise RegistryError("bad auth realm")
        cache_key = f"{realm}|{params.get('service')}|{params.get('scope')}"
        with self._lock:
            cached = self._tokens.get(cache_key)
        if cached and cached[1] > time.time():
            return cached[0]
        resp = self.session.get(realm, params=params, timeout=TIMEOUT)
        if resp.status_code != 200:
            raise RegistryError(f"token {resp.status_code}")
        data = resp.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            raise RegistryError("no token")
        ttl = int(data.get("expires_in") or 300)
        with self._lock:
            self._tokens[cache_key] = (token, time.time() + max(ttl - 30, 30))
        return token

    def _request(self, method: str, url: str, accept: str = MANIFEST_TYPES) -> requests.Response:
        headers = {"Accept": accept}
        resp = self.session.request(method, url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code == 401:
            token = self._token_for(resp.headers.get("WWW-Authenticate", ""))
            headers["Authorization"] = f"Bearer {token}"
            resp = self.session.request(method, url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code == 429:
            raise RegistryError("rate limited")
        if resp.status_code >= 400:
            raise RegistryError(f"HTTP {resp.status_code}")
        return resp

    # -- public API ----------------------------------------------------------------

    def remote_digest(self, ref: str) -> str:
        host, path, reference = parse_ref(ref)
        resp = self._request("HEAD", f"https://{host}/v2/{path}/manifests/{reference}")
        digest = resp.headers.get("Docker-Content-Digest", "")
        if not digest:
            # A few registries omit the header on HEAD: ask for the manifest itself.
            resp = self._request("GET", f"https://{host}/v2/{path}/manifests/{reference}")
            digest = resp.headers.get("Docker-Content-Digest", "")
        if not digest.startswith("sha256:"):
            raise RegistryError("no digest")
        return digest

    def remote_labels(self, ref: str, digest: str, arch: str = "amd64") -> Dict[str, str]:
        """Labels of the image ``digest`` for this platform (cached forever: digests are immutable)."""
        with self._lock:
            if digest in self._labels:
                return self._labels[digest]
        host, path, _ = parse_ref(ref)
        base = f"https://{host}/v2/{path}"
        manifest = self._request("GET", f"{base}/manifests/{digest}").json()
        if manifest.get("mediaType") in INDEX_TYPES or "manifests" in manifest:
            chosen = None
            for entry in manifest.get("manifests") or []:
                platform = entry.get("platform") or {}
                if platform.get("os") == "linux" and platform.get("architecture") == arch:
                    chosen = entry
                    break
            if not chosen:
                raise RegistryError("platform not found")
            manifest = self._request("GET", f"{base}/manifests/{chosen['digest']}").json()
        config_digest = (manifest.get("config") or {}).get("digest")
        if not config_digest:
            raise RegistryError("no config")
        blob = self._request("GET", f"{base}/blobs/{config_digest}", accept="application/json").json()
        labels = (blob.get("config") or {}).get("Labels") or {}
        with self._lock:
            self._labels[digest] = labels
        return labels
