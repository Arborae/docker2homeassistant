"""Run the D2HA interface with sample data: no Docker needed.

    python scripts/demo.py [--port 18765]

Sign in as ``demo`` / ``demo-password``. Containers, logs and registry are made up and
updates are simulated; release notes are the real ones, read from GitHub (when online).
The data folder is temporary. Used for the screenshots of the documentation.
"""

import argparse
import hashlib
import os
import random
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "d2ha"))

from app import create_app  # noqa: E402
from config import Config  # noqa: E402
from docker_ops import container_key, split_ref  # noqa: E402
from security import hash_password  # noqa: E402

USERNAME, PASSWORD = "demo", "demo-password"

# stack, name, image, installed version, newer version (None: up to date), GitHub repository
SAMPLE = [
    ("documents", "paperless", "ghcr.io/paperless-ngx/paperless-ngx:latest", "3.4.2", None, "paperless-ngx/paperless-ngx"),
    ("documents", "paperless-redis", "redis:7", "7.4.6", None, ""),
    ("media", "jellyfin", "jellyfin/jellyfin:latest", "12.3", "12.4", "jellyfin/jellyfin"),
    ("monitoring", "grafana", "grafana/grafana:latest", "13.2.5", "13.3.0", "grafana/grafana"),
    ("monitoring", "prometheus", "prom/prometheus:latest", "3.16.0", None, "prometheus/prometheus"),
    ("monitoring", "uptime-kuma", "louislam/uptime-kuma:2", "2.5.8", "2.5.9", "louislam/uptime-kuma"),
    ("photos", "immich-server", "ghcr.io/immich-app/immich-server:release", "3.3.5", "3.3.6", "immich-app/immich"),
    ("photos", "immich-machine-learning", "ghcr.io/immich-app/immich-machine-learning:release", "3.3.5", "3.3.6", "immich-app/immich"),
    ("photos", "immich-postgres", "postgres:16", "16.10", None, ""),
    ("proxy", "traefik", "traefik:v3.7", "3.7.16", "3.7.17", "traefik/traefik"),
    ("", "d2ha", "ghcr.io/arborae/docker2homeassistant:latest", "0.4.0", None, "arborae/docker2homeassistant"),
    ("", "homeassistant", "ghcr.io/home-assistant/home-assistant:stable", "2026.11.0", None, "home-assistant/core"),
    ("", "vaultwarden", "vaultwarden/server:latest", "1.37.4", "1.37.5", "dani-garcia/vaultwarden"),
    ("", "backup", "backup-tool:local", "", None, ""),
]
STOPPED = {"backup"}

LOG_LINES = [
    ("INF", "Request completed in {ms} ms: GET /api/status 200"),
    ("INF", "Scheduled task finished in {ms} ms"),
    ("DBG", "Cache hit ratio {pct}%"),
    ("INF", "Connected clients: {n}"),
    ("WRN", "Slow query took {ms} ms"),
    ("INF", "Health check passed"),
    ("ERR", "Upstream timed out after 30000 ms, retrying"),
]


def sha(*parts: str) -> str:
    return "sha256:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


class DemoApi:
    def inspect_distribution(self, ref):
        return {"Descriptor": {"digest": DemoRegistry().remote_digest(ref)}}


class DemoOps:
    """The subset of DockerOps that D2HA uses, backed by SAMPLE."""

    def __init__(self):
        self.api = DemoApi()
        self.images: Dict[str, Dict[str, Any]] = {}
        self.containers: List[Dict[str, Any]] = []
        now = time.time()
        for index, (stack, name, image, installed, _, repo) in enumerate(SAMPLE):
            image_id = self._add_image(image, installed, repo)
            self.containers.append({
                "key": container_key(stack, name), "id": sha("container", name)[7:], "name": name,
                "stack": stack, "image": image, "image_id": image_id,
                "state": "exited" if name in STOPPED else "running",
                "status": "Exited (0) 2 days ago" if name in STOPPED else f"Up {index % 5 + 2} days",
                "health": "" if name in STOPPED or index % 3 else "healthy",
                "created": int(now - 86400 * (index % 5 + 2)), "is_self": name == "d2ha",
            })

    def _add_image(self, image: str, version: str, repo: str) -> str:
        image_id = sha("image", image, version)
        labels = {"org.opencontainers.image.version": version} if version else {}
        if repo:
            labels["org.opencontainers.image.source"] = f"https://github.com/{repo}"
        repository, _ = split_ref(image)
        local = image.endswith(":local")
        self.images[image_id] = {
            "labels": labels,
            "repo_digests": [] if local else [f"{repository}@{sha('manifest', image, version)}"],
            "created": "",
        }
        return image_id

    def engine_info(self):
        return {"version": "29.0.1", "arch": "amd64", "os": "linux"}

    def image_info(self, image_id):
        return self.images.get(image_id, {"labels": {}, "repo_digests": [], "created": ""})

    def list_containers(self):
        return [dict(c) for c in self.containers]

    def unused_images(self):
        used = {c["image_id"] for c in self.containers}
        return [i for i in self.images if i not in used] + [sha("old", "1"), sha("old", "2")]

    def prune_images(self):
        time.sleep(1.5)
        removed = len(self.unused_images())
        used = {c["image_id"] for c in self.containers}
        self.images = {i: v for i, v in self.images.items() if i in used}
        return {"removed": removed, "reclaimed": removed * 412_000_000}

    def logs(self, container_id, tail=200):
        rng = random.Random(container_id)
        start = time.time() - tail * 7
        return "".join(self._line(rng, start + i * 7) for i in range(min(tail, 400)))

    @staticmethod
    def _line(rng: random.Random, when: float) -> str:
        level, text = rng.choices(LOG_LINES, weights=[30, 20, 10, 10, 4, 20, 1])[0]
        text = text.format(ms=rng.randint(3, 900), pct=rng.randint(80, 99), n=rng.randint(1, 40))
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(when)) + f".{rng.randint(0, 999999):06d}000Z"
        return f"{stamp} {level} {text}\n"

    def log_stream(self, container_id):
        return DemoStream(self, container_id)

    def recreate(self, container_id, progress):
        container = next(c for c in self.containers if c["id"] == container_id)
        sample = next(s for s in SAMPLE if s[1] == container["name"])
        total = 180_000_000 + len(container["name"]) * 7_000_000
        for step in range(1, 41):
            time.sleep(0.15)
            current = total * step // 40
            progress({"phase": "pull", "percent": round(current * 100 / total), "current": current,
                      "total": total, "detail": f"Downloading {sha('layer', str(step % 6))[7:19]}"})
        for phase in ("stop", "create", "start", "cleanup"):
            time.sleep(0.6)
            progress({"phase": phase, "percent": 100})
        if sample[4]:
            container["image_id"] = self._add_image(container["image"], sample[4], sample[5])
            container["status"] = "Up 1 second"
        progress({"phase": "done", "percent": 100})
        return {"changed": True, "id": container_id, "name": container["name"]}


class DemoStream:
    """Live log lines, one every second or so, until closed."""

    def __init__(self, ops: DemoOps, container_id: str):
        self.rng = random.Random(container_id + "live")
        self.closed = threading.Event()

    def __iter__(self):
        while not self.closed.wait(self.rng.uniform(0.4, 1.6)):
            yield DemoOps._line(self.rng, time.time()).encode()

    def close(self):
        self.closed.set()


class DemoRegistry:
    """Newer versions from SAMPLE, everything else up to date."""

    def remote_digest(self, ref):
        sample = next((s for s in SAMPLE if s[2] == ref), None)
        version = (sample[4] or sample[3]) if sample else ""
        return sha("manifest", ref, version)

    def remote_labels(self, ref, digest, arch="amd64"):
        sample = next(s for s in SAMPLE if s[2] == ref)
        labels = {"org.opencontainers.image.version": sample[4] or sample[3]}
        if sample[5]:
            labels["org.opencontainers.image.source"] = f"https://github.com/{sample[5]}"
        return labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--data", default="", help="data folder (default: a new temporary one)")
    args = parser.parse_args()

    data_dir = args.data or tempfile.mkdtemp(prefix="d2ha-demo-")
    config = Config(data_dir=data_dir, start_background=False, version="0.4.0",
                    github_token=os.environ.get("D2HA_GITHUB_TOKEN", ""))
    app = create_app(config, ops=DemoOps(), registry=DemoRegistry())
    d2ha = app.extensions["d2ha"]
    d2ha.store.update(lambda d: d["auth"].update(username=USERNAME, password_hash=hash_password(PASSWORD)))
    d2ha.jobs.start()
    print("Checking the sample images (release notes from GitHub)...")
    d2ha.checker.check()
    print(f"D2HA demo on http://127.0.0.1:{args.port} - user {USERNAME!r}, password {PASSWORD!r}")
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
