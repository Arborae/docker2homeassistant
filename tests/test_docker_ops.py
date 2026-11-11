import docker
import pytest

from docker_ops import DockerOps, UpdateError, container_key, split_ref

OLD_ID = "a" * 64
NEW_ID = "c" * 64

IMAGE_CONFIG = {
    "Env": ["PATH=/usr/bin", "LANG=C.UTF-8"],
    "Cmd": ["nginx", "-g", "daemon off;"],
    "Labels": {"org.opencontainers.image.version": "1.0", "maintainer": "x"},
    "ExposedPorts": {"80/tcp": {}},
    "Volumes": {"/var/cache": {}},
    "WorkingDir": "/srv",
}


def inspect(**overrides):
    container = {
        "Id": OLD_ID,
        "Name": "/web",
        "Config": {
            "Image": "nginx:latest",
            "Hostname": OLD_ID[:12],
            "Env": ["PATH=/usr/bin", "LANG=C.UTF-8", "TZ=Europe/Rome"],
            "Cmd": ["nginx", "-g", "daemon off;"],
            "Labels": {"org.opencontainers.image.version": "1.0", "maintainer": "x",
                       "com.docker.compose.project": "site", "com.docker.compose.image": "sha256:old"},
            "ExposedPorts": {"80/tcp": {}, "8443/tcp": {}},
            "Volumes": {"/var/cache": {}},
            "WorkingDir": "/srv",
            "Tty": False,
        },
        "HostConfig": {
            "NetworkMode": "site_default",
            "Binds": ["site_data:/data:rw"],
            "RestartPolicy": {"Name": "unless-stopped"},
            "PortBindings": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]},
        },
        "Mounts": [
            {"Type": "volume", "Name": "site_data", "Destination": "/data", "RW": True},
            {"Type": "volume", "Name": "b" * 64, "Destination": "/var/cache", "RW": True},
        ],
        "NetworkSettings": {"Networks": {
            "site_default": {"Aliases": ["web", OLD_ID[:12]], "IPAMConfig": None},
            "proxy": {"Aliases": None, "IPAMConfig": {"IPv4Address": "172.30.0.5"}},
        }},
    }
    container.update(overrides)
    return container


def test_only_user_settings_are_carried_over():
    config = DockerOps.build_create_config(inspect(), IMAGE_CONFIG, "nginx:latest", "sha256:new")
    assert config["Image"] == "nginx:latest"
    assert config["Env"] == ["TZ=Europe/Rome"]  # image defaults come from the new image
    assert "Cmd" not in config and "WorkingDir" not in config
    assert config["Labels"] == {"com.docker.compose.project": "site", "com.docker.compose.image": "sha256:new"}
    assert config["ExposedPorts"] == {"8443/tcp": {}}
    assert "Hostname" not in config  # the default hostname is the old container id


def test_volumes_and_networks_are_preserved():
    config = DockerOps.build_create_config(inspect(), IMAGE_CONFIG, "nginx:latest", "sha256:new")
    host = config["HostConfig"]
    assert host["Binds"] == ["site_data:/data:rw", f"{'b' * 64}:/var/cache"]  # anonymous volume re-attached
    assert host["RestartPolicy"] == {"Name": "unless-stopped"}
    endpoints = config["NetworkingConfig"]["EndpointsConfig"]
    assert endpoints["site_default"]["Aliases"] == ["web"]  # the old short id is dropped
    assert endpoints["proxy"]["IPAMConfig"] == {"IPv4Address": "172.30.0.5"}


def test_host_network_has_no_endpoints():
    old = inspect()
    old["HostConfig"]["NetworkMode"] = "host"
    config = DockerOps.build_create_config(old, IMAGE_CONFIG, "nginx:latest", "sha256:new")
    assert "NetworkingConfig" not in config


def test_network_of_another_container_sends_no_hostname_or_ports():
    # The hostname is the start of the other container's id (#281).
    old = inspect()
    old["HostConfig"]["NetworkMode"] = "container:" + "d" * 64
    old["Config"]["Hostname"] = "d" * 12
    old["Config"]["Domainname"] = "lan"
    config = DockerOps.build_create_config(old, IMAGE_CONFIG, "nginx:latest", "sha256:new")
    assert "Hostname" not in config
    assert "Domainname" not in config
    assert "ExposedPorts" not in config
    assert "NetworkingConfig" not in config
    assert config["HostConfig"]["NetworkMode"] == "container:" + "d" * 64


def test_custom_hostname_and_command_are_kept():
    old = inspect()
    old["Config"]["Hostname"] = "myhost"
    old["Config"]["Cmd"] = ["nginx", "-c", "/etc/custom.conf"]
    config = DockerOps.build_create_config(old, IMAGE_CONFIG, "nginx:latest", "sha256:new")
    assert config["Hostname"] == "myhost"
    assert config["Cmd"] == ["nginx", "-c", "/etc/custom.conf"]


def test_keys_and_refs():
    assert container_key("site", "web-1") == "site_web_1"
    assert container_key("", "web") == "no_stack_web"
    assert split_ref("nginx") == ("nginx", "latest")
    assert split_ref("ghcr.io/a/b:2") == ("ghcr.io/a/b", "2")
    assert split_ref("nginx@sha256:x") == ("nginx", None)


class FakeDocker:
    """Just enough of the Docker API to list the containers and recreate one."""

    def __init__(self, *containers):
        self.api = self
        self.inspected = {c["Id"]: c for c in containers}
        self.fail = None  # the call that Docker refuses
        self.after = lambda call: None  # runs after every change
        self.calls = []  # (call, container name) of every change
        self.created = {}  # name -> the create request

    def _allow(self, call):
        if call == self.fail:
            raise docker.errors.APIError(f"{call} refused")

    def containers(self, all=False):
        return [{
            "Id": c["Id"],
            "Names": [c["Name"]],
            "Image": c["Config"]["Image"],
            "ImageID": c["Image"],
            "Labels": c["Config"]["Labels"],
            "State": "running" if c["State"]["Running"] else "exited",
            "Status": "Up 2 hours" if c["State"]["Running"] else "Exited (0) 1 second ago",
            "NetworkSettings": c["NetworkSettings"],
            "HostConfig": {"NetworkMode": c["HostConfig"]["NetworkMode"]},
        } for c in self.inspected.values()]

    def inspect_container(self, container_id):
        return self.inspected[container_id]

    def inspect_image(self, ref):
        return {"Id": "sha256:old" if ref == "sha256:old" else "sha256:new", "Config": IMAGE_CONFIG}

    def pull(self, ref, stream=False, decode=False):
        return iter(())

    def _log(self, call, container_id):
        self.calls.append((call, self.inspected[container_id]["Name"].lstrip("/")))

    def rename(self, container_id, name):
        self._allow("rename")
        self._log("rename", container_id)
        self.inspected[container_id]["Name"] = f"/{name}"
        self.after("rename")

    def stop(self, container_id, timeout=10):
        self._allow("stop")
        self._log("stop", container_id)
        self.inspected[container_id]["State"] = {"Running": False}
        self.after("stop")

    def create_container_from_config(self, config, name=None):
        self._allow("create")
        self._allow(f"create {name}")
        new_id = NEW_ID if NEW_ID not in self.inspected else str(len(self.created)) * 64
        self.inspected[new_id] = inspect(Id=new_id, Name=f"/{name}", Image="sha256:new",
                                         State={"Running": False}, HostConfig=config["HostConfig"])
        self.created[name] = config
        self._log("create", new_id)
        self.after("create")
        return {"Id": new_id}

    def start(self, container_id):
        self._allow("start")
        self._log("start", container_id)
        self.inspected[container_id]["State"] = {"Running": True}
        self.after("start")

    def remove_container(self, container_id, v=False, force=False):
        self._allow("remove")
        self._log("remove", container_id)
        del self.inspected[container_id]
        self.after("remove")


def running(**overrides):
    return inspect(Image="sha256:old", State={"Running": True}, **overrides)


def listed(ops):
    return [(c["key"], c["name"], c["id"]) for c in ops.list_containers()]


def test_a_container_is_listed_once_under_its_key_while_it_is_updated():
    fake = FakeDocker(running())
    ops = DockerOps(client=fake)
    seen = []
    fake.after = lambda call: seen.append((call, listed(ops)))
    assert ops.recreate(OLD_ID, lambda progress: None) == {"changed": True, "id": NEW_ID, "name": "web", "dependents": []}
    # Under the backup name the old container is still "web"; then the new one takes its place.
    assert seen == [
        ("rename", [("site_web", "web", OLD_ID)]),
        ("stop", [("site_web", "web", OLD_ID)]),
        ("create", [("site_web", "web", NEW_ID)]),
        ("start", [("site_web", "web", NEW_ID)]),
        ("remove", [("site_web", "web", NEW_ID)]),
    ]


def test_a_backup_container_left_behind_is_listed():
    fake = FakeDocker(running())
    fake.fail = "remove"
    ops = DockerOps(client=fake)
    ops.recreate(OLD_ID, lambda progress: None)
    assert listed(ops) == [
        ("site_web", "web", NEW_ID),
        ("site_web_d2ha_old", "web-d2ha-old", OLD_ID),
    ]


def test_a_failed_update_puts_the_container_back_in_the_list():
    fake = FakeDocker(running())
    fake.fail = "create"
    ops = DockerOps(client=fake)
    with pytest.raises(UpdateError):
        ops.recreate(OLD_ID, lambda progress: None)
    assert listed(ops) == [("site_web", "web", OLD_ID)]
    # The update is over: a container with the backup name is a container like the others.
    fake.rename(OLD_ID, "web-d2ha-old")
    assert listed(ops) == [("site_web_d2ha_old", "web-d2ha-old", OLD_ID)]


APP_ID = "e" * 64


def on_network_of_web(mode="container:" + OLD_ID, running_now=True):
    """An app that uses the network of the web container, as an app behind a VPN container does."""
    app = inspect(Id=APP_ID, Name="/app", Image="sha256:old", State={"Running": running_now})
    app["Config"].update(Image="app:latest", Hostname=OLD_ID[:12])
    app["HostConfig"] = {"NetworkMode": mode, "RestartPolicy": {"Name": "unless-stopped"}}
    app["NetworkSettings"] = {"Networks": {}}
    return app


def test_the_containers_on_its_network_are_recreated_on_the_new_one():
    fake = FakeDocker(running(), on_network_of_web())
    ops = DockerOps(client=fake)
    result = ops.recreate(OLD_ID, lambda progress: None)
    assert result["dependents"] == ["app"]
    # The app stops before the container it depends on, and comes back after it.
    assert fake.calls.index(("stop", "app")) < fake.calls.index(("stop", "web-d2ha-old"))
    assert fake.calls.index(("start", "web")) < fake.calls.index(("create", "app"))
    assert fake.created["app"]["HostConfig"]["NetworkMode"] == "container:" + NEW_ID
    assert "Hostname" not in fake.created["app"]
    apps = [c for c in fake.inspected.values() if c["Name"] == "/app"]
    assert len(apps) == 1 and apps[0]["Id"] != APP_ID and apps[0]["State"]["Running"]
    assert sorted(name for _, name, _ in listed(ops)) == ["app", "web"]


def test_a_network_named_after_the_container_keeps_its_name():
    fake = FakeDocker(running(), on_network_of_web(mode="container:web"))
    ops = DockerOps(client=fake)
    ops.recreate(OLD_ID, lambda progress: None)
    assert fake.created["app"]["HostConfig"]["NetworkMode"] == "container:web"


def test_a_stopped_app_on_its_network_stays_stopped():
    fake = FakeDocker(running(), on_network_of_web(running_now=False))
    ops = DockerOps(client=fake)
    ops.recreate(OLD_ID, lambda progress: None)
    assert ("stop", "app") not in fake.calls
    assert not [c for c in fake.inspected.values() if c["Name"] == "/app"][0]["State"]["Running"]


def test_when_the_update_fails_the_apps_on_its_network_start_again():
    fake = FakeDocker(running(), on_network_of_web())
    fake.fail = "create web"
    ops = DockerOps(client=fake)
    with pytest.raises(UpdateError):
        ops.recreate(OLD_ID, lambda progress: None)
    assert fake.inspected[OLD_ID]["State"]["Running"]
    assert fake.inspected[APP_ID]["State"]["Running"]  # the same container, on the old network
    assert fake.calls[-1] == ("start", "app")


def test_an_app_that_cannot_be_reconnected_is_reported():
    fake = FakeDocker(running(), on_network_of_web())
    fake.fail = "create app"
    ops = DockerOps(client=fake)
    with pytest.raises(UpdateError) as error:
        ops.recreate(OLD_ID, lambda progress: None)
    assert error.value.code == "error.dependents_failed" and error.value.detail == "app"
    # The update itself went through; the old app is left under its name for docker compose up.
    assert sorted(name for _, name, _ in listed(ops)) == ["app", "web"]
    assert [c["Id"] for c in fake.inspected.values() if c["Name"] == "/web"] == [NEW_ID]
