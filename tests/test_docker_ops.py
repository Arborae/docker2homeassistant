from docker_ops import DockerOps, container_key, split_ref

OLD_ID = "a" * 64

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
