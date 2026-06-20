#!/bin/bash
set -e

# Se il socket docker è montato, trova il suo GID
if [ -S /var/run/docker.sock ]; then
    DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
    
    # Crea un gruppo docker con quel GID se non esiste
    if ! getent group "$DOCKER_GID" >/dev/null 2>&1; then
        groupadd -g "$DOCKER_GID" docker_host
    fi
    
    # Aggiungi l'utente d2ha al gruppo
    usermod -aG "$DOCKER_GID" d2ha
fi

# Assicurati che /app/data esista e sia accessibile a d2ha
mkdir -p /app/data
chown -R d2ha:d2ha /app

# Drop privileges ed esegui il comando
exec gosu d2ha "$@"
