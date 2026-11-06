"""Release notes from GitHub for the repository an image is built from."""

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from docker_ops import SOURCE_LABELS, label_value

log = logging.getLogger(__name__)

GITHUB_RE = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/#?].*)?$", re.IGNORECASE)
BREAKING_HEADING = re.compile(
    r"^(#{1,6})\s*.*(breaking|backward[- ]incompatible).*$", re.IGNORECASE | re.MULTILINE
)
MAX_NOTES = 20000


def github_repo(labels: Dict[str, str], image_ref: str) -> Optional[Tuple[str, str]]:
    """(owner, repo) from the image source label, or from a ghcr.io reference."""
    source = label_value(labels, SOURCE_LABELS)
    match = GITHUB_RE.search(source) if source else None
    if match:
        return match.group(1), match.group(2)
    if image_ref.startswith("ghcr.io/"):
        parts = image_ref[len("ghcr.io/"):].split("@")[0].split(":")[0].split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
    return None


def _normalize(version: str) -> str:
    return version.strip().lower().lstrip("v")


def extract_breaking(body: str) -> str:
    """The "Breaking changes" section of release notes, if any."""
    match = BREAKING_HEADING.search(body or "")
    if not match:
        return ""
    level = len(match.group(1))
    rest = body[match.end():]
    next_heading = re.search(rf"^#{{1,{level}}}\s", rest, re.MULTILINE)
    section = rest[: next_heading.start()] if next_heading else rest
    return section.strip()[:4000]


class ReleaseNotes:
    def __init__(self, token: str = "", ttl: int = 3600):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "User-Agent": "D2HA",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.ttl = ttl
        self._cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def _releases(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        key = f"{owner}/{repo}".lower()
        with self._lock:
            cached = self._cache.get(key)
        if cached and time.time() - cached[0] < self.ttl:
            return cached[1]
        releases: List[Dict[str, Any]] = []
        try:
            resp = self.session.get(f"https://api.github.com/repos/{owner}/{repo}/releases",
                                    params={"per_page": 40}, timeout=15)
            if resp.status_code == 200:
                releases = [r for r in resp.json() if not r.get("draft")]
            else:
                log.debug("GitHub releases for %s: HTTP %s", key, resp.status_code)
        except requests.RequestException as exc:
            log.debug("GitHub releases for %s failed: %s", key, exc)
        with self._lock:
            self._cache[key] = (time.time(), releases)
        return releases

    def find(self, owner: str, repo: str, version: str) -> Optional[Dict[str, Any]]:
        """Notes of the release matching ``version``; None when there is no exact match."""
        wanted = _normalize(version)
        if not wanted:
            return None
        for release in self._releases(owner, repo):
            if wanted in (_normalize(release.get("tag_name") or ""), _normalize(release.get("name") or "")):
                body = (release.get("body") or "").strip()
                return {
                    "version": release.get("tag_name") or version,
                    "title": release.get("name") or release.get("tag_name") or version,
                    "url": release.get("html_url") or f"https://github.com/{owner}/{repo}/releases",
                    "published": (release.get("published_at") or "")[:10],
                    "body": body[:MAX_NOTES],
                    "breaking": extract_breaking(body),
                }
        return None
