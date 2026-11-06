#!/bin/sh
set -e

# With the Docker socket mounted directly, let the d2ha user use it.
# (Not needed when DOCKER_HOST points to a socket proxy.)
if [ -S /var/run/docker.sock ]; then
    gid=$(stat -c %g /var/run/docker.sock)
    group=$(getent group "$gid" | cut -d: -f1)
    if [ -z "$group" ]; then
        group=docker-host
        groupadd -g "$gid" "$group"
    fi
    usermod -aG "$group" d2ha
fi

mkdir -p /app/data
chown -R d2ha:d2ha /app/data
chmod 700 /app/data

exec gosu d2ha "$@"
