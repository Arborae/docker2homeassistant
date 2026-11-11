"""Docker access: container listing, logs, image pruning and safe container recreation."""

import copy
import ipaddress
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import docker
from docker.utils import parse_repository_tag

log = logging.getLogger(__name__)

ProgressFn = Callable[[Dict[str, Any]], None]
# During an update the old container is kept under this name until the new one runs.
OLD_SUFFIX = "-d2ha-old"
# After an update the new container must stay up this long without restarting, and turn
# healthy if its image has a health check, or the previous container is put back.
CHECK_SECONDS = 60
CHECK_MAX_SECONDS = 180
CHECK_INTERVAL = 2

VERSION_LABELS = (
    "org.opencontainers.image.version",
    "io.hass.version",
    "org.label-schema.version",
    "version",
)
SOURCE_LABELS = (
    "org.opencontainers.image.source",
    "org.opencontainers.image.url",
    "org.label-schema.vcs-url",
)


def container_key(stack: str, name: str) -> str:
    """Stable identifier (stack + name) that survives container recreation.

    Same algorithm as D2HA 0.3, so saved preferences keep matching.
    """
    base = f"{stack or 'no_stack'}__{name or 'container'}"
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in base)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def label_value(labels: Optional[Dict[str, str]], keys) -> str:
    for key in keys:
        value = (labels or {}).get(key)
        if value:
            return str(value).strip()
    return ""


def split_ref(ref: str):
    """Return (repository, tag) for an image reference; tag is None for digests."""
    if "@" in ref:
        return ref.split("@", 1)[0], None
    repo, tag = parse_repository_tag(ref)
    return repo, tag or "latest"


def _self_container_id() -> str:
    """Our own container id, read from mountinfo (cgroup v2) or the hostname."""
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as fh:
            match = re.search(r"/containers/([0-9a-f]{64})/hostname", fh.read())
            if match:
                return match.group(1)
    except OSError:
        pass
    return os.environ.get("HOSTNAME", "") if re.fullmatch(r"[0-9a-f]{12}", os.environ.get("HOSTNAME", "")) else ""


def docker_proxy_host(docker_host: Optional[str] = None) -> str:
    """The host name in DOCKER_HOST (e.g. ``docker-proxy``), or "" for a local socket or an IP."""
    raw = os.environ.get("DOCKER_HOST", "") if docker_host is None else docker_host
    match = re.match(r"^(?:tcp|https?)://\[?([^\]/:]+)\]?(?::\d+)?/?$", raw.strip())
    if not match:
        return ""
    host = match.group(1).lower()
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        return "" if host == "localhost" else host


def is_proxy_container(proxy_host: str, name: str, labels: Optional[Dict[str, str]],
                       networks: Optional[Dict[str, Any]]) -> bool:
    """True if this container answers to the DOCKER_HOST name D2HA uses to reach Docker."""
    if not proxy_host:
        return False
    names = {name.lower(), str((labels or {}).get("com.docker.compose.service", "")).lower()}
    for endpoint in (networks or {}).values():
        for alias in (endpoint or {}).get("Aliases") or []:
            names.add(str(alias).lower())
        for alias in (endpoint or {}).get("DNSNames") or []:
            names.add(str(alias).lower())
    return proxy_host in names


class UpdateError(RuntimeError):
    """An update failed; ``code`` is an i18n key for the user-facing message."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class DockerOps:
    def __init__(self, client: Optional[docker.DockerClient] = None):
        self.client = client or docker.from_env(version="auto", timeout=60)
        self.api = self.client.api
        self.self_id = _self_container_id()
        # D2HA reaches Docker through this container (a socket proxy): it must never update it.
        self.proxy_host = docker_proxy_host()
        self._image_cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        # Ids of the containers an update is recreating: for a moment they have the backup name.
        self._recreating: set = set()
        self._clock = time.monotonic
        self._sleep = time.sleep

    # -- engine -------------------------------------------------------------------

    def engine_info(self) -> Dict[str, str]:
        try:
            ver = self.api.version()
            return {"version": ver.get("Version", ""), "arch": ver.get("Arch", "amd64"), "os": ver.get("Os", "linux")}
        except docker.errors.DockerException:
            return {"version": "", "arch": "amd64", "os": "linux"}

    # -- images -------------------------------------------------------------------

    def image_info(self, image_id: str) -> Dict[str, Any]:
        """Labels and repo digests of a local image (cached: image ids are immutable)."""
        with self._lock:
            cached = self._image_cache.get(image_id)
        if cached is not None:
            return cached
        try:
            data = self.api.inspect_image(image_id)
        except docker.errors.NotFound:
            return {"labels": {}, "repo_digests": [], "created": ""}
        info = {
            "labels": (data.get("Config") or {}).get("Labels") or {},
            "repo_digests": data.get("RepoDigests") or [],
            "created": data.get("Created", ""),
        }
        with self._lock:
            self._image_cache[image_id] = info
        return info

    def unused_images(self) -> List[str]:
        used = {c["ImageID"] for c in self.api.containers(all=True)}
        return [img["Id"] for img in self.api.images() if img["Id"] not in used]

    def prune_images(self) -> Dict[str, Any]:
        unused = set(self.unused_images())
        result = self.api.prune_images(filters={"dangling": False})
        # ImagesDeleted has one entry per tag and per layer: count the images that are gone.
        left = {img["Id"] for img in self.api.images()}
        return {
            "removed": len(unused - left),
            "reclaimed": int(result.get("SpaceReclaimed") or 0),
        }

    # -- containers -----------------------------------------------------------------

    def list_containers(self) -> List[Dict[str, Any]]:
        items = []
        raws = self.api.containers(all=True)
        names = {(raw.get("Names") or ["/?"])[0].lstrip("/") for raw in raws}
        for raw in raws:
            labels = raw.get("Labels") or {}
            name = (raw.get("Names") or ["/?"])[0].lstrip("/")
            if raw["Id"] in self._recreating and name.endswith(OLD_SUFFIX):
                # The same container in the middle of an update, not a new one: it keeps
                # its name and key until the new container is there.
                name = name[:-len(OLD_SUFFIX)]
                if name in names:
                    continue
            stack = labels.get("com.docker.compose.project", "")
            status = raw.get("Status") or ""
            image = raw.get("Image", "")
            if image.startswith("sha256:"):
                # The tag moved to another image: the configured reference is in the inspect data.
                try:
                    image = (self.api.inspect_container(raw["Id"]).get("Config") or {}).get("Image", image)
                except docker.errors.NotFound:
                    pass
            health = ""
            for word in ("unhealthy", "healthy", "starting"):
                if f"({word}" in status:
                    health = word
                    break
            items.append({
                "key": container_key(stack, name),
                "id": raw["Id"],
                "name": name,
                "stack": stack,
                "image": image,
                "image_id": raw.get("ImageID", ""),
                "state": raw.get("State", ""),
                "status": status,
                "health": health,
                "created": raw.get("Created", 0),
                "is_self": bool(self.self_id) and raw["Id"].startswith(self.self_id),
                "is_proxy": is_proxy_container(self.proxy_host, name, labels,
                                               (raw.get("NetworkSettings") or {}).get("Networks")),
            })
        items.sort(key=lambda c: (c["stack"] == "", c["stack"].lower(), c["name"].lower()))
        return items

    def logs(self, container_id: str, tail: int = 200) -> str:
        data = self.api.logs(container_id, stdout=True, stderr=True, timestamps=True, tail=tail)
        return data.decode("utf-8", errors="replace")

    def events(self, since: Optional[int] = None):
        """Container events for the alerts (died, stop signals, out of memory, health), live and from ``since``."""
        return self.api.events(since=since, decode=True,
                               filters={"type": "container", "event": ["die", "health_status", "kill", "oom"]})

    def log_stream(self, container_id: str):
        """A live log stream (call ``close()`` on it to stop following)."""
        return self.api.logs(container_id, stdout=True, stderr=True, timestamps=True,
                             stream=True, follow=True, tail=0)

    # -- recreation -------------------------------------------------------------------

    @staticmethod
    def build_create_config(old: Dict[str, Any], old_image_config: Dict[str, Any],
                            image_ref: str, new_image_id: str) -> Dict[str, Any]:
        """Turn ``docker inspect`` of a container into a create request for the new image.

        Only what the user configured is kept: values identical to the old image's
        defaults are dropped, so the new image's defaults (env, cmd, labels...) apply.
        """
        cfg = old.get("Config") or {}
        img = old_image_config or {}

        def differs(key: str):
            value = cfg.get(key)
            return value if value not in (None, "", [], {}) and value != img.get(key) else None

        image_env = set(img.get("Env") or [])
        env = [e for e in cfg.get("Env") or [] if e not in image_env]
        image_labels = img.get("Labels") or {}
        labels = {k: v for k, v in (cfg.get("Labels") or {}).items() if image_labels.get(k) != v}
        if "com.docker.compose.image" in labels:
            labels["com.docker.compose.image"] = new_image_id  # keeps `docker compose up` quiet
        exposed = {p: {} for p in (cfg.get("ExposedPorts") or {}) if p not in (img.get("ExposedPorts") or {})}
        volumes = {v: {} for v in (cfg.get("Volumes") or {}) if v not in (img.get("Volumes") or {})}

        config: Dict[str, Any] = {"Image": image_ref, "Env": env, "Labels": labels}
        for key in ("Cmd", "Entrypoint", "WorkingDir", "User", "Healthcheck", "StopSignal", "Shell"):
            value = differs(key)
            if value is not None:
                config[key] = value
        # On the network of another container, the hostname and the ports are that
        # container's: Docker refuses them in the create request.
        shared_network = ((old.get("HostConfig") or {}).get("NetworkMode") or "").startswith("container:")
        for key in ("Tty", "OpenStdin", "StdinOnce", "AttachStdin", "AttachStdout", "AttachStderr", "Domainname", "StopTimeout"):
            if cfg.get(key) not in (None, "") and not (shared_network and key == "Domainname"):
                config[key] = cfg[key]
        hostname = cfg.get("Hostname")
        if hostname and not shared_network and not old.get("Id", "").startswith(hostname):
            config["Hostname"] = hostname  # only a hostname set by the user, not the default id
        if exposed and not shared_network:
            config["ExposedPorts"] = exposed
        if volumes:
            config["Volumes"] = volumes

        host_config = dict(old.get("HostConfig") or {})
        # Re-attach anonymous volumes, otherwise their data would be left behind.
        binds = list(host_config.get("Binds") or [])
        declared = {b.split(":")[1] for b in binds if ":" in b}
        declared |= {m.get("Target") for m in host_config.get("Mounts") or []}
        for mount in old.get("Mounts") or []:
            if mount.get("Type") != "volume" or mount.get("Destination") in declared:
                continue
            if re.fullmatch(r"[0-9a-f]{64}", mount.get("Name") or ""):
                binds.append(f"{mount['Name']}:{mount['Destination']}" + ("" if mount.get("RW", True) else ":ro"))
        if binds:
            host_config["Binds"] = binds
        config["HostConfig"] = host_config

        network_mode = host_config.get("NetworkMode", "")
        networks = (old.get("NetworkSettings") or {}).get("Networks") or {}
        if networks and not network_mode.startswith(("container:", "host", "none")):
            short_id = old.get("Id", "")[:12]
            endpoints = {}
            for net_name, ep in networks.items():
                endpoints[net_name] = {
                    "Aliases": [a for a in ep.get("Aliases") or [] if a != short_id] or None,
                    "IPAMConfig": ep.get("IPAMConfig"),
                    "Links": ep.get("Links"),
                    "DriverOpts": ep.get("DriverOpts"),
                }
            config["NetworkingConfig"] = {"EndpointsConfig": endpoints}
        return config

    def pull(self, image_ref: str, progress: ProgressFn) -> str:
        """Pull ``image_ref`` reporting aggregated layer progress; return the image id."""
        layers: Dict[str, Dict[str, int]] = {}
        last = 0.0
        for line in self.api.pull(image_ref, stream=True, decode=True):
            if line.get("error"):
                raise UpdateError("error.pull_failed", line["error"])
            layer = line.get("id")
            detail = line.get("progressDetail") or {}
            status = line.get("status") or ""
            if layer and detail.get("total"):
                layers[layer] = {"current": int(detail.get("current") or 0), "total": int(detail["total"])}
            elif layer and status in ("Download complete", "Pull complete", "Already exists") and layer in layers:
                layers[layer]["current"] = layers[layer]["total"]
            now = time.time()
            if now - last >= 0.25:
                last = now
                total = sum(l["total"] for l in layers.values())
                current = sum(min(l["current"], l["total"]) for l in layers.values())
                progress({
                    "phase": "pull",
                    "percent": round(current * 100 / total) if total else None,
                    "current": current,
                    "total": total,
                    "detail": f"{status} {layer or ''}".strip(),
                })
        return self.api.inspect_image(image_ref)["Id"]

    def recreate(self, container_id: str, progress: ProgressFn) -> Dict[str, Any]:
        """Update one container to the latest image of its tag. Returns a summary dict."""
        old = self.api.inspect_container(container_id)
        name = old["Name"].lstrip("/")
        image_ref = (old.get("Config") or {}).get("Image", "")
        if not image_ref or image_ref.startswith("sha256:") or "@" in image_ref:
            raise UpdateError("error.no_tag", image_ref)
        if self.self_id and old["Id"].startswith(self.self_id):
            raise UpdateError("error.self_update")
        if is_proxy_container(self.proxy_host, name, (old.get("Config") or {}).get("Labels"),
                              (old.get("NetworkSettings") or {}).get("Networks")):
            raise UpdateError("error.proxy_update")

        progress({"phase": "pull", "percent": 0})
        new_image_id = self.pull(image_ref, progress)
        if new_image_id == old["Image"]:
            progress({"phase": "done", "percent": 100})
            return {"changed": False, "id": old["Id"], "name": name}

        old_image_config = self.api.inspect_image(old["Image"]).get("Config") or {}
        config = self.build_create_config(old, old_image_config, image_ref, new_image_id)
        was_running = bool((old.get("State") or {}).get("Running"))
        backup_name = f"{name}{OLD_SUFFIX}"
        # Containers on the network of this one: Docker ties them to its id, so they are
        # stopped first and recreated on the new container, as docker compose does.
        dependents = self._dependents(old)

        progress({"phase": "stop", "percent": 100})
        self._recreating.add(old["Id"])
        try:
            self.api.rename(old["Id"], backup_name)
            try:
                for dep in dependents:
                    if (dep.get("State") or {}).get("Running"):
                        self.api.stop(dep["Id"], timeout=(dep.get("Config") or {}).get("StopTimeout") or 10)
                if was_running:
                    stop_timeout = (old.get("Config") or {}).get("StopTimeout") or 10
                    self.api.stop(old["Id"], timeout=stop_timeout)
                progress({"phase": "create", "percent": 100})
                new_id = self.api.create_container_from_config(config, name=name)["Id"]
                try:
                    if was_running:
                        progress({"phase": "start", "percent": 100})
                        self.api.start(new_id)
                except docker.errors.APIError:
                    self.api.remove_container(new_id, force=True)
                    raise
            except docker.errors.APIError as exc:
                log.warning("Recreating %s failed, restoring the old container: %s", name, exc)
                self._restore(old["Id"], name, was_running, dependents)
                raise UpdateError("error.recreate_failed", str(exc)) from exc

            problem = self._check(new_id, progress) if was_running else None
            if problem:
                log.warning("%s did not pass the check (%s), restoring the old container", name, problem)
                try:
                    self.api.remove_container(new_id, force=True)
                except docker.errors.APIError as exc:
                    log.error("Could not remove the new container of %s: %s", name, exc)
                self._restore(old["Id"], name, was_running, dependents)
                raise UpdateError("error.recreate_failed",
                                  f"The new container did not pass the check: {problem}.")

            progress({"phase": "cleanup", "percent": 100})
            try:
                self.api.remove_container(old["Id"], v=False)
            except docker.errors.NotFound:
                pass  # removed on stop (AutoRemove)
            except docker.errors.APIError as exc:
                log.warning("Could not remove the previous container of %s: %s", name, exc)
        finally:
            # From here a backup container still there is listed: its cleanup failed.
            self._recreating.discard(old["Id"])
        reconnected, failed = self._reconnect(dependents, name, new_id, progress)
        if failed:
            raise UpdateError("error.dependents_failed", ", ".join(failed))
        progress({"phase": "done", "percent": 100})
        return {"changed": True, "id": new_id, "name": name, "dependents": reconnected}

    def _dependents(self, provider: Dict[str, Any]) -> List[Dict[str, Any]]:
        """``docker inspect`` of the containers that use the network of ``provider``."""
        names = {provider["Id"], provider["Id"][:12], provider["Name"].lstrip("/")}
        found = []
        for raw in self.api.containers(all=True):
            mode = (raw.get("HostConfig") or {}).get("NetworkMode") or ""
            if mode.startswith("container:") and mode.split(":", 1)[1] in names and raw["Id"] != provider["Id"]:
                # A copy: the state before the update decides what is started again.
                found.append(copy.deepcopy(self.api.inspect_container(raw["Id"])))
        return found

    def _reconnect(self, dependents: List[Dict[str, Any]], provider_name: str, new_id: str,
                   progress: ProgressFn):
        """Recreate the containers that used the network of the old container on the new one."""
        reconnected, failed = [], []
        for dep in dependents:
            dep_name = dep["Name"].lstrip("/")
            progress({"phase": "reconnect", "percent": 100, "detail": dep_name})
            image_ref = (dep.get("Config") or {}).get("Image", "")
            try:
                # The same tag, not pulled: what docker compose up would use.
                image_id = self.api.inspect_image(image_ref)["Id"]
                image_config = self.api.inspect_image(dep["Image"]).get("Config") or {}
                config = self.build_create_config(dep, image_config, image_ref, image_id)
                target = (dep.get("HostConfig") or {}).get("NetworkMode", "").split(":", 1)[1]
                if target != provider_name:  # a name still works; an id is the old one
                    config["HostConfig"]["NetworkMode"] = f"container:{new_id}"
            except docker.errors.APIError as exc:
                log.error("Could not prepare %s for the network of %s: %s", dep_name, provider_name, exc)
                failed.append(dep_name)
                continue
            dep_new = None
            self._recreating.add(dep["Id"])
            try:
                self.api.rename(dep["Id"], f"{dep_name}{OLD_SUFFIX}")
                dep_new = self.api.create_container_from_config(config, name=dep_name)["Id"]
                if (dep.get("State") or {}).get("Running"):
                    self.api.start(dep_new)
            except docker.errors.APIError as exc:
                log.error("Could not reconnect %s to the network of %s: %s", dep_name, provider_name, exc)
                failed.append(dep_name)
                try:  # leave the old one under its name, for docker compose up
                    if dep_new:
                        self.api.remove_container(dep_new, force=True)
                    self.api.rename(dep["Id"], dep_name)
                except docker.errors.APIError:
                    pass
                self._recreating.discard(dep["Id"])
                continue
            reconnected.append(dep_name)
            try:
                self.api.remove_container(dep["Id"], v=False)
            except docker.errors.APIError as exc:
                log.warning("Could not remove the previous container of %s: %s", dep_name, exc)
            self._recreating.discard(dep["Id"])
        return reconnected, failed

    def _check(self, container_id: str, progress: ProgressFn) -> str:
        """Watch a container that has just started; return what went wrong, or "" if it works."""
        start = self._clock()
        first = self.api.inspect_container(container_id)
        started_at = (first.get("State") or {}).get("StartedAt")
        restarts = first.get("RestartCount") or 0
        while True:
            elapsed = self._clock() - start
            info = self.api.inspect_container(container_id)
            state = info.get("State") or {}
            if state.get("Restarting") or (info.get("RestartCount") or 0) != restarts \
                    or state.get("StartedAt") != started_at:
                return "it restarted"
            if not state.get("Running"):
                return f"it stopped (exit code {state.get('ExitCode', '?')})"
            health = (state.get("Health") or {}).get("Status")
            if health == "unhealthy":
                return "its health check says unhealthy"
            if elapsed >= CHECK_SECONDS and health in (None, "", "healthy"):
                return ""
            if elapsed >= CHECK_MAX_SECONDS:
                return f"it was not healthy after {CHECK_MAX_SECONDS} seconds"
            progress({"phase": "check", "percent": min(99, round(elapsed * 100 / CHECK_SECONDS)),
                      "detail": f"{round(elapsed)} s"})
            self._sleep(CHECK_INTERVAL)

    def _restore(self, old_id: str, name: str, was_running: bool,
                 dependents: Optional[List[Dict[str, Any]]] = None) -> None:
        try:
            self.api.rename(old_id, name)
            if was_running:
                self.api.start(old_id)
        except docker.errors.APIError as exc:
            log.error("Could not restore %s: %s", name, exc)
        # They were stopped before the update: back on the network of the old container.
        for dep in dependents or []:
            if (dep.get("State") or {}).get("Running"):
                try:
                    self.api.start(dep["Id"])
                except docker.errors.APIError as exc:
                    log.error("Could not start %s again: %s", dep["Name"].lstrip("/"), exc)
