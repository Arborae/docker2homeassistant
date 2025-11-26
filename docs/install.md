# 🐳 Installazione tramite Docker / Docker Compose

Questa guida ti mostra come installare **Docker to Home Assistant (D2HA)** usando le immagini pubblicate su **GitHub Container Registry (GHCR)**.

---

## 🚀 Installazione veloce con Docker

### 1️⃣ Esegui il container usando l’immagine stabile

```bash
docker run -d   --name d2ha   -p 12021:12021   -v /var/run/docker.sock:/var/run/docker.sock:ro   -v $(pwd)/data:/app/data   -e D2HA_SECRET_KEY="cambia-questa-chiave"   ghcr.io/arborae/docker2homeassistant:latest
```

> La directory `./data` contiene:
> - `auth_config.json` (utente, password, 2FA)
> - `autodiscovery_preferences.json` (sensori MQTT)

---

## 📦 Installazione con Docker Compose

### 1️⃣ Crea un file `docker-compose.yml`

```yaml
services:
  d2ha:
    image: ghcr.io/arborae/docker2homeassistant:latest
    container_name: d2ha
    restart: unless-stopped
    ports:
      - "12021:12021"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./data:/app/data
    environment:
      D2HA_SECRET_KEY: "cambia-questa-chiave"

      # MQTT (opzionale)
      # MQTT_BROKER: "192.168.1.10"
      # MQTT_PORT: "1883"
      # MQTT_USERNAME: "ha"
      # MQTT_PASSWORD: "password"
      # MQTT_BASE_TOPIC: "d2ha_server"
```

### 2️⃣ Avvio

```bash
docker compose up -d
```

### 3️⃣ Aggiornamento alla nuova release

```bash
docker compose pull
docker compose up -d
```

---

## 🌙 Usare la versione Nightly

Per provare l’ultima build da `main`:

```yaml
image: ghcr.io/arborae/docker2homeassistant:nightly
```

Oppure una nightly specifica:

```yaml
image: ghcr.io/arborae/docker2homeassistant:nightly-<commit_sha>
```

---

## 🔑 Credenziali iniziali

```text
Username: admin
Password: admin
```

Al primo login partirà il **wizard di onboarding**, che comprende:

- cambio password obbligatorio  
- 2FA opzionale  
- Modalità sicura (on/off)  
- Integrazione MQTT (opzionale)

---

## 🌐 URL di accesso

```text
http://localhost:12021
```

Se usi il deploy su un host remoto, sostituisci `localhost` con l’IP o il dominio del server.

---

## 📡 Configurazione MQTT (opzionale)

Esempio `.env`:

```env
MQTT_BROKER=192.168.1.10
MQTT_PORT=1883
MQTT_USERNAME=homeassistant
MQTT_PASSWORD=password
MQTT_BASE_TOPIC=d2ha_server
MQTT_DISCOVERY_PREFIX=homeassistant
MQTT_NODE_ID=d2ha_server
MQTT_STATE_INTERVAL=5
```

Se MQTT non è configurato, D2HA funziona comunque come **dashboard di gestione Docker**; l’integrazione con Home Assistant viene semplicemente disabilitata.
