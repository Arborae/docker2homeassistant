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
        } for c in self.inspected.values()]

    def inspect_container(self, container_id):
        return self.inspected[container_id]

    def inspect_image(self, ref):
        return {"Id": "sha256:old" if ref == "sha256:old" else "sha256:new", "Config": IMAGE_CONFIG}

    def pull(self, ref, stream=False, decode=False):
        return iter(())

    def rename(self, container_id, name):
        self._allow("rename")
        self.inspected[container_id]["Name"] = f"/{name}"
        self.after("rename")

    def stop(self, container_id, timeout=10):
        self._allow("stop")
        self.inspected[container_id]["State"] = {"Running": False}
        self.after("stop")

    def create_container_from_config(self, config, name=None):
        self._allow("create")
        self.inspected[NEW_ID] = inspect(Id=NEW_ID, Name=f"/{name}", Image="sha256:new",
                                         State={"Running": False})
        self.after("create")
        return {"Id": NEW_ID}

    def start(self, container_id):
        self._allow("start")
        self.inspected[container_id]["State"] = {"Running": True}
        self.after("start")

    def remove_container(self, container_id, v=False, force=False):
        self._allow("remove")
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
    assert ops.recreate(OLD_ID, lambda progress: None) == {"changed": True, "id": NEW_ID, "name": "web"}
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
