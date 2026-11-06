"""Periodic update check: compares the digest of each running tag with its registry."""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import docker

from docker_ops import VERSION_LABELS, DockerOps, label_value, split_ref
from notify import Notifier, NotifyError
from registry import RegistryClient, RegistryError
from releases import ReleaseNotes, github_repo
from store import Store, read_json, write_json_atomic

log = logging.getLogger(__name__)


def short_digest(digest: str) -> str:
    return digest.split(":", 1)[-1][:7] if digest else ""


def describe(version: str, tag: Optional[str], digest: str) -> str:
    if version:
        return version
    short = short_digest(digest)
    tag = tag or "?"
    return f"{tag} ({short})" if short else tag


class UpdateChecker:
    def __init__(self, ops: DockerOps, registry: RegistryClient, releases: ReleaseNotes,
                 store: Store, state_path: str, notifier: Optional[Notifier] = None):
        self.ops = ops
        self.registry = registry
        self.releases = releases
        self.store = store
        self.state_path = state_path
        self.notifier = notifier
        state = read_json(state_path) or {}
        self.remote: Dict[str, Dict[str, Any]] = state.get("remote") or {}
        self.checked_at: float = float(state.get("checked_at") or 0)
        # image ref -> digest already announced on Telegram (one message per new version).
        self.notified: Dict[str, str] = state.get("notified") or {}
        self.running = False
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._arch: Optional[str] = None

    # -- scheduling -----------------------------------------------------------------

    @property
    def interval_seconds(self) -> int:
        hours = int(self.store.get("settings", "check_interval_hours", default=6) or 6)
        return max(1, hours) * 3600

    @property
    def next_check(self) -> float:
        """When the next scheduled check is due (0 = never checked: due now)."""
        return self.checked_at + self.interval_seconds if self.checked_at else 0.0

    def due(self) -> bool:
        return time.time() >= self.next_check

    def start(self) -> None:
        threading.Thread(target=self._loop, name="update-checker", daemon=True).start()

    def trigger(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        time.sleep(5)
        while True:
            if self.due():
                self.check()
            self._wake.wait(timeout=max(5.0, min(300.0, self.next_check - time.time())))
            if self._wake.is_set():
                self._wake.clear()
                self.check()

    # -- checking ---------------------------------------------------------------------

    def _arch_name(self) -> str:
        if self._arch is None:
            self._arch = self.ops.engine_info().get("arch") or "amd64"
        return self._arch

    def check(self, only_ref: Optional[str] = None) -> None:
        with self._lock:
            if self.running:
                return
            self.running = True
        try:
            containers = self.ops.list_containers()
            refs = sorted({c["image"] for c in containers if c["image"]})
            if only_ref:
                refs = [r for r in refs if r == only_ref]
            for ref in refs:
                self.remote[ref] = self._check_ref(ref, [c for c in containers if c["image"] == ref])
            if not only_ref:
                self.remote = {r: v for r, v in self.remote.items() if r in refs}
                self.notified = {r: d for r, d in self.notified.items() if r in refs}
                self.checked_at = time.time()
            self._notify_new(containers)
            write_json_atomic(self.state_path, {"checked_at": self.checked_at, "remote": self.remote,
                                                "notified": self.notified})
            log.info("Update check done: %d images", len(refs))
        except Exception:  # noqa: BLE001 - the checker thread must survive any failure
            log.exception("Update check failed")
        finally:
            self.running = False

    def _check_ref(self, ref: str, containers: List[Dict[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {"checked_at": time.time()}
        repo, tag = split_ref(ref)
        if tag is None or ref.startswith("sha256:"):
            result["status"] = "pinned"
            return result
        if not any(self._local_digests(c["image_id"]) for c in containers):
            # Built on this machine (no registry digest): nothing to compare with.
            result["status"] = "local"
            return result
        try:
            digest = self.registry.remote_digest(ref)
        except (RegistryError, OSError) as exc:
            digest = self._daemon_digest(ref)
            if not digest:
                result["status"] = "error"
                result["error"] = str(exc)
                return result
        result["digest"] = digest
        outdated = [c for c in containers if digest not in self._local_digests(c["image_id"])]
        if not outdated:
            return result
        # Something runs an older image: learn the real version of the new one.
        try:
            labels = self.registry.remote_labels(ref, digest, self._arch_name())
        except (RegistryError, OSError, ValueError) as exc:
            log.debug("Labels of %s unavailable: %s", ref, exc)
            labels = {}
        version = label_value(labels, VERSION_LABELS)
        result["version"] = version
        source = github_repo(labels, ref) or github_repo(self.ops.image_info(containers[0]["image_id"])["labels"], ref)
        if source and version:
            notes = self.releases.find(source[0], source[1], version)
            if notes:
                result["notes"] = notes
        if source:
            result["source"] = f"https://github.com/{source[0]}/{source[1]}"
        return result

    def _notify_new(self, containers: List[Dict[str, Any]]) -> None:
        """Announce updates whose new digest was not announced yet (once per version)."""
        if not self.notifier:
            return
        items, announced = [], {}
        for ref, remote in self.remote.items():
            digest = remote.get("digest", "")
            if not digest or self.notified.get(ref) == digest:
                continue
            outdated = [v for v in (self.view(c) for c in containers if c["image"] == ref)
                        if v["update"] == "available"]
            if not outdated:
                continue
            first = outdated[0]
            items.append({
                "names": [v["name"] for v in outdated],
                "stack": first["stack"],
                "installed": first["installed_version"],
                "latest": first["latest_version"],
                "url": first["release_url"],
                "breaking": first["breaking"],
            })
            announced[ref] = digest
        if not items:
            return
        try:
            if self.notifier.updates(items):
                self.notified.update(announced)
        except NotifyError as exc:
            log.warning("Telegram: %s", exc)  # tried again at the next check

    def _daemon_digest(self, ref: str) -> str:
        """Fallback for private registries: the daemon has the credentials."""
        try:
            data = self.ops.api.inspect_distribution(ref)
            return (data.get("Descriptor") or {}).get("digest", "")
        except docker.errors.DockerException:
            return ""

    def _local_digests(self, image_id: str) -> set:
        return {d.split("@", 1)[1] for d in self.ops.image_info(image_id)["repo_digests"] if "@" in d}

    # -- views ------------------------------------------------------------------------

    def view(self, container: Dict[str, Any]) -> Dict[str, Any]:
        """A container enriched with its update status, as exposed by the API."""
        info = self.ops.image_info(container["image_id"])
        local = {d.split("@", 1)[1] for d in info["repo_digests"] if "@" in d}
        local_digest = sorted(local)[0] if local else ""
        _, tag = split_ref(container["image"]) if container["image"] else (None, None)
        remote = self.remote.get(container["image"]) or {}
        installed = label_value(info["labels"], VERSION_LABELS)

        if not container["image"] or remote.get("status") == "pinned":
            status = "pinned"
        elif not local:
            status = "local"  # built locally, nothing to compare with
        elif not remote:
            status = "unknown"
        elif remote.get("status") == "error":
            status = "error"
        elif remote.get("digest") in local:
            status = "current"
        else:
            status = "available"

        installed_text = describe(installed, tag, local_digest)
        latest_text = installed_text
        notes = None
        if status == "available":
            latest_text = describe(remote.get("version", ""), tag, remote.get("digest", ""))
            if latest_text == installed_text:
                # Same label (often inherited from the base image): tell them apart.
                installed_text = f"{installed_text} ({short_digest(local_digest)})"
                latest_text = f"{latest_text} ({short_digest(remote.get('digest', ''))})"
            notes = remote.get("notes")

        return {
            **container,
            "update": status,
            "installed_version": installed_text,
            "latest_version": latest_text,
            "release_url": (notes or {}).get("url") or remote.get("source", ""),
            "has_notes": bool(notes and notes.get("body")),
            "breaking": bool(notes and notes.get("breaking")),
            "error": remote.get("error", "") if status == "error" else "",
            "ha": self.store.container_in_ha(container["key"]),
        }

    def notes_for(self, image_ref: str) -> Optional[Dict[str, Any]]:
        return (self.remote.get(image_ref) or {}).get("notes")
