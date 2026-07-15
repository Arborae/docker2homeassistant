<h1 align="center">D2HA</h1>
<p align="center">
  <b>Docker to Home Assistant</b><br/>
  Monitor and control your Docker containers from a modern dashboard, with smart Home Assistant integration.<br/>
  <i>Monitora e controlla i container Docker da una dashboard moderna, con integrazione smart in Home Assistant.</i>
</p>

<p align="center">
  <a href="https://github.com/arborae/docker2homeassistant/actions/workflows/publish-docker.yml">
    <img src="https://github.com/arborae/docker2homeassistant/actions/workflows/publish-docker.yml/badge.svg" alt="Release CI" />
  </a>
  <a href="https://github.com/arborae/docker2homeassistant/actions/workflows/nightly-docker.yml">
    <img src="https://github.com/arborae/docker2homeassistant/actions/workflows/nightly-docker.yml/badge.svg" alt="Nightly CI" />
  </a>
</p>

<p align="center">
  <a href="https://github.com/arborae/docker2homeassistant/releases">
    <img src="https://img.shields.io/github/v/release/arborae/docker2homeassistant?style=for-the-badge&label=stable%20release" alt="Latest release" />
  </a>
  <a href="https://ghcr.io/arborae/docker2homeassistant">
    <img src="https://img.shields.io/badge/GHCR-docker2homeassistant-0d1117?style=for-the-badge&logo=docker&logoColor=white" alt="GHCR Docker image" />
  </a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-alpha-00bcd4.svg" alt="Status: alpha" />
  <img src="https://img.shields.io/badge/python-3.11+-3776AB.svg" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/backend-Flask-ff9800.svg" alt="Flask" />
  <img src="https://img.shields.io/badge/docker-compose-2496ED.svg" alt="Docker Compose" />
  <img src="https://img.shields.io/badge/MQTT-Home%20Assistant-32c955.svg" alt="MQTT Home Assistant" />
</p>

<p align="center">🇬🇧 <b>English</b> · <a href="#-italiano">🇮🇹 Italiano</a></p>

---

## 📸 UI overview · Panoramica UI

> Dark/light theme, built for headless servers and Home Assistant dashboards.

<p align="center"><img src="docs/d2ha_home_swipe.gif" alt="Home - Dark / Light theme" width="900"/></p>
<p align="center"><img src="docs/d2ha_containers_swipe.gif" alt="Containers - Dark / Light theme" width="900"/></p>
<p align="center"><img src="docs/d2ha_images_swipe.gif" alt="Images - Dark / Light theme" width="900"/></p>
<p align="center"><img src="docs/d2ha_updates_swipe.gif" alt="Updates - Dark / Light theme" width="900"/></p>
<p align="center"><img src="docs/d2ha_events_swipe.gif" alt="Events - Dark / Light theme" width="900"/></p>
<p align="center"><img src="docs/d2ha_autodiscovery_swipe.gif" alt="Autodiscovery - Dark / Light theme" width="900"/></p>

---

# 🇬🇧 English

## ✨ Overview

**D2HA** is a lightweight web server that reads the Docker socket directly (`/var/run/docker.sock`) and gives you:

- a **real-time dashboard** for CPU, RAM, network and container status;
- quick controls for **start / stop / restart / pause / unpause / delete / full update**;
- a **complete Home Assistant integration** over MQTT (automatic discovery, sensors, switches, update status);
- a polished UI with a **dark/light theme switcher** and **multi-language (IT/EN)**, from the login page to the onboarding wizard;
- an installable **Progressive Web App (PWA)** for Android/iOS/desktop, with service worker, manifest and basic offline support.

No database, no heavy dependencies: just Docker, Flask and — if you want — MQTT.

## 🚀 Main features

### Server dashboard
- System CPU / RAM overview.
- Number of running, paused and stopped containers.
- Docker image usage (layers / disk space).
- Live charts of the latest samples.

### Stacks & containers
- Grouping by **Docker Compose stack**.
- For each container: status, uptime, CPU, RAM, network; exposed ports, volumes, network; action buttons:
  - ▶️ Play (start) · ⏸ Pause · ⏹ Stop · 🔁 Restart · 🔄 Full update (image pull + recreate) · 🗑 Delete

### Docker images
- List of installed images with tag, ID, creation date; size and usage; the containers using each image.
- Ability to delete unused images.

### Update management
- Analysis of every installed container.
- Comparison of **installed vs remote version** (based on the image digest).
- Reads **version, changelog and breaking changes** from the image's OCI labels/annotations; as a fallback, changelog/breaking from the source repo's **GitHub releases**.
  - Set `D2HA_GITHUB_TOKEN` (a read-only GitHub token on public repos) to raise the GitHub API rate limit (**60 → 5000 requests/hour**) and keep changelog detection reliable.
  - Note: for images that don't publish this info (many generic Docker Hub images) changelog/version may be unavailable — the digest comparison stays reliable regardless.
- An "Update image" action runs pull + container recreate, with **live progress** (phase, download percentage and log) in the update popup.

### Container detail
Detail popup with CPU/RAM/network charts; networks, ports, volumes; full environment; Docker/Compose labels; update history; the associated docker-compose (if available).

### MQTT + Home Assistant (optional)
- Automatic discovery of `sensor` entities (status, resources and updates) and `switch` entities (to control containers).
- Commands over MQTT: `start`, `stop`, `restart`, `pause`, `unpause`, `full_update`.
- State published to a configurable base topic (e.g. `d2ha_server`).

### Installable app (PWA)
- Integrated **manifest** and **service worker**: D2HA can be installed from the browser as a standalone app on Android, iOS and desktop.
- PWA requirements (manifest, registered service worker, 192/512 icons) are present on **all entry pages** (login, splash, dashboard), so installation is offered from the login screen onward.
- A **splash** screen during backend startup and basic caching of static assets for a faster first load.
- Works behind an HTTPS reverse proxy; if you put an authentication gateway in front (e.g. **Cloudflare Access**) read the note in the **🛡️ Security** section.

## 🧱 Architecture

- **Backend** — **Flask** to expose JSON APIs and serve the static web UI; **Paho MQTT** (optional) to publish sensors and switches to Home Assistant; direct access to **Docker Engine** via the socket to avoid a CLI dependency.
- **Frontend** — minimalist server-rendered (Jinja2) interface, no build step, optimized for headless environments; live charts with **Chart.js** and lightweight polling; **theme switcher** (dark/light) and **multi-language** on every page; **PWA** (`manifest.json` + `sw.js`).
- **Service layer** — Docker logic organized in a **modular package** (`services/docker/`) split by domain (containers, images/updates, networks, volumes, system, events); reads via the Python **docker SDK** (direct socket access); update handling comparing local vs remote tags and reading OCI labels; in-memory cache to reduce repeated Docker daemon calls.
- **Home Assistant integration** — automatic MQTT discovery (sensors and switches) with Lovelace-friendly payloads; state published to dedicated topics at a configurable interval.

> Goal: stay "batteries included" without a database, message queue or extra components.

## 📋 Requirements

- Docker Engine 20.10+ with access to the `/var/run/docker.sock` socket.
- Python 3.11+ if you run it bare-metal.
- Network access to the MQTT broker (only if you enable the integration).
- Tested architectures: `amd64`, `arm64` (Raspberry Pi 4/5).

## 🔐 Authentication & onboarding

- **Initial credentials:** `admin / admin`.
- On **first login** a guided wizard covers four steps:
  1. choose a new password (mandatory) and, optionally, a new username;
  2. enable or skip **TOTP 2FA** (Google Authenticator, Aegis, etc.);
  3. initial setup of **Safe mode** (on by default) and **Performance mode** (off by default);
  4. choose the initial behavior for MQTT autodiscovery: expose all default entities or not.
- Everything stays changeable afterwards: credentials and 2FA from the Security page; Safe/Performance mode from the gear icon in the header; MQTT entities from the Autodiscovery page.
- Security config is stored in the `auth_config.json` file (created automatically with restricted permissions on first run). To keep it across rebuilds, point the path with `D2HA_AUTH_CONFIG_PATH` (default `/app/data/auth_config.json`) and mount the `./data:/app/data` volume in `docker-compose.yml`.
- MQTT autodiscovery preferences are stored in `autodiscovery_preferences.json` in the **same path** as `auth_config.json` (or the path from `D2HA_AUTODISCOVERY_PREFS_PATH`), so they persist when you mount `./data:/app/data`.
- Useful variables: `D2HA_SECRET_KEY` for the Flask session key (mandatory in production); `D2HA_ADMIN_USERNAME` to customize the initial username before first run.

> Tip: run D2HA behind an HTTPS reverse proxy and treat 2FA as essential if the interface is network-exposed.

## 🛡️ Security

- **CSRF protection:** all state-changing requests from HTML forms are protected by a per-session CSRF token; JSON APIs are exempt because the browser Same-Origin Policy blocks cross-origin `application/json` requests without preflight.
- **Login rate limiting:** after too many failed attempts the `/login` endpoint returns HTTP 429 for 15 minutes, slowing brute-force. The count is based on the **real client IP** (`CF-Connecting-IP` / `X-Forwarded-For` when behind a proxy), not the proxy address.
- **API rate limiting:** write `/api/*` endpoints are rate-limited per IP to mitigate abuse and accidental loops.
- **Safe mode:** when active, **destructive actions** (deleting containers and networks) require explicit confirmation; without it the API returns HTTP 403. The UI shows a confirmation dialog before deleting.
- **Session timeout:** automatic logout on inactivity, configurable (default 30 minutes) from the Security page.
- **Security page:** at `/settings/security` you can change the admin username/password and manage 2FA. Every change requires the current password and, if 2FA is on, a valid TOTP code. Enabling 2FA is guided (QR/URI + explicit verification); disabling requires the current password + code.
- **Log redaction:** sensitive values (secret key, MQTT password, hashes/secrets) are masked in logs.
- **Best practice:** always run behind an HTTPS reverse proxy (Caddy, Traefik, Nginx) and remember that anyone with access to D2HA can control Docker on the host.

> **PWA behind an authentication gateway (Cloudflare Access / Zero Trust, Authelia, etc.)**
> If you protect the app with a gateway that intercepts every request, PWA installation **won't work** until the manifest and service worker are reachable without auth: the browser fetches them **without cookies** and would get the gateway login page instead of the files.
> Configure a **public bypass** for the PWA paths — at least `/sw.js` and `/static/*` (which include `manifest.json` and the icons) — leaving the rest of the app protected. On Cloudflare Access: create a dedicated application for those paths with a **Bypass / Everyone** policy.

## 🗂️ Project structure

```text
d2ha/
├── app.py              # Flask entrypoint, splash gating, logging
├── auth_store.py       # Read/write auth_config.json
├── csrf.py             # CSRF protection
├── rate_limiter.py     # API rate limiting
├── i18n.py / theme.py  # Multi-language (IT/EN) and dark/light theme
├── version.py          # Version (from D2HA_VERSION / git tag / SHA)
├── mqtt/               # MQTT discovery and state publishing
├── routes/             # Flask blueprints: ui.py, api.py, auth.py
├── services/
│   ├── docker/         # Modular package: containers, images_updates,
│   │                   #   networks, volumes, system, events, base
│   ├── preferences.py  # MQTT autodiscovery preferences
│   └── utils.py        # Helpers (human_bytes, slug, etc.)
├── static/             # CSS/JS, icons, manifest.json, sw.js (PWA)
└── templates/          # Jinja2: layouts/, partials/, pages
```

## 🧪 Run locally (development)

```bash
git clone https://github.com/arborae/docker2homeassistant.git
cd docker2homeassistant

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r d2ha/requirements.txt

python d2ha/app.py
```

By default the app is available at `http://localhost:12021`. Make sure the user running it has access to Docker (e.g. add it to the `docker` group on Linux).

## 🐳 Install via Docker / Docker Compose

Use the ready-made images from **GitHub Container Registry (GHCR)**.

**Image tags** — stable: `ghcr.io/arborae/docker2homeassistant:latest` or `:X.Y.Z` (e.g. `0.1.1`); development: `ghcr.io/arborae/docker2homeassistant:nightly`.

Example `docker-compose.yml`:

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
      D2HA_VERSION: "latest"
      D2HA_SECRET_KEY: "change-this-key"        # mandatory in production
      # D2HA_ADMIN_USERNAME: "admin"            # optional, before first run
      # MQTT_BROKER: "192.168.1.100"            # optional MQTT config
      # MQTT_PORT: "1883"
      # MQTT_USERNAME: "homeassistant"
      # MQTT_PASSWORD: "password"
      # MQTT_BASE_TOPIC: "d2ha_server"
      # D2HA_GITHUB_TOKEN: "ghp_xxx"            # optional, raises GitHub API rate limit
```

The `./data:/app/data` volume persists `auth_config.json` (credentials + 2FA + security) and `autodiscovery_preferences.json` (exposed MQTT sensors). Start with `docker compose up -d`; update with `docker compose pull && docker compose up -d`. Switch to nightly by setting the image to `:nightly`.

## 📡 MQTT configuration

MQTT is fully optional — without a broker, D2HA works as a plain web dashboard. Supported environment variables:

`MQTT_BROKER` · `MQTT_PORT` · `MQTT_USERNAME` · `MQTT_PASSWORD` · `MQTT_BASE_TOPIC` · `MQTT_DISCOVERY_PREFIX` · `MQTT_NODE_ID` · `MQTT_STATE_INTERVAL` (seconds between state publishes).

If `paho-mqtt` isn't installed or the connection fails, the MQTT part is disabled and the UI keeps working normally.

## 🌐 Main endpoints

**UI:** `/` (dashboard) · `/containers` · `/images` · `/volumes` · `/networks` · `/updates` · `/events` · `/autodiscovery` · `/settings/security`

**JSON API (excerpt):**
- `GET /api/overview` – host and stack overview
- `GET /api/containers/<id>/details` – container detail
- `POST /api/containers/<id>/<action>` – container action (`start`/`stop`/`restart`/`pause`/`unpause`/`delete`/`kill`); destructive actions require `?confirm=1` when safe mode is on
- `POST /api/containers/<id>/full_update` – image pull + container recreate
- `GET /api/notifications` · `GET|POST /api/networks` · `GET /api/containers/<id>/stats`
- `GET|POST /api/containers/<id>/updates` · `POST /api/containers/<id>/updates/frequency`
- `GET|POST /api/containers/<id>/compose` · `GET|POST /api/compose` · `GET /api/containers/<id>/logs?tail=<N|all>`

**System / PWA:** `GET /api/health` · `GET /splash` · `GET /sw.js` · `GET /static/manifest.json`

## 🗺️ Roadmap

- Advanced filtering and search across containers / stacks
- Historical CPU/RAM charts (time series)
- Ready-made Lovelace templates for Home Assistant
- Roles / permissions (read vs control)

## 🤝 Contributing

1. **Fork** the repository.
2. Create a branch: `feature/my-feature`.
3. Add tests or basic checks where it makes sense.
4. Open a **pull request** clearly explaining your changes.

## 📄 License

This project is released under the **MIT** license. See [`LICENSE`](./LICENSE) for the full details.

<br>

---
---

<a id="-italiano"></a>

# 🇮🇹 Italiano

## ✨ Panoramica

**D2HA** è un webserver leggero che legge direttamente il socket Docker (`/var/run/docker.sock`) e ti offre:

- una **dashboard in tempo reale** per CPU, RAM, rete e stato dei container;
- controlli rapidi per **start / stop / restart / pause / unpause / delete / full update**;
- una **integrazione completa con Home Assistant** tramite MQTT (discovery automatico, sensori, switch, stato aggiornamenti);
- una UI curata con **theme switcher dark/light** e **multi-lingua (IT/EN)**, dalla login page al wizard di onboarding;
- una **Progressive Web App (PWA)** installabile su Android/iOS/desktop, con service worker, manifest e supporto offline di base.

Niente database, niente dipendenze pesanti: solo Docker, Flask e – se vuoi – MQTT.

## 🚀 Funzionalità principali

### Dashboard server
- Panoramica CPU / RAM del sistema.
- Numero container attivi, in pausa e fermi.
- Utilizzo immagini Docker (layers / spazio disco).
- Grafici live degli ultimi campioni.

### Stack & container
- Raggruppamento per **stack Docker Compose**.
- Per ogni container: stato, uptime, CPU, RAM, rete; porte esposte, volumi, rete; pulsanti azione:
  - ▶️ Play (start) · ⏸ Pausa · ⏹ Stop · 🔁 Riavvia · 🔄 Full update (pull immagine + recreate) · 🗑 Elimina

### Immagini Docker
- Lista immagini installate con tag, ID, data di creazione; dimensione e utilizzo; associazione ai container che la usano.
- Possibilità di eliminare immagini non utilizzate.

### Gestione aggiornamenti
- Analisi di tutti i container installati.
- Confronto **versione installata vs versione remota** (basato sul digest dell'immagine).
- Lettura di **versione, changelog e breaking changes** da label/annotations OCI dell'immagine; in fallback, changelog/breaking dalle **release GitHub** del repo sorgente.
  - Imposta `D2HA_GITHUB_TOKEN` (token GitHub di sola lettura su repo pubblici) per alzare il rate limit della GitHub API (**60 → 5000 richieste/ora**) e mantenere affidabile il rilevamento del changelog.
  - Nota: per immagini che non pubblicano queste informazioni (molte immagini Docker Hub generiche) changelog/versione possono non essere disponibili — il confronto digest resta comunque affidabile.
- Azione “Aggiorna immagine” per eseguire pull + ricreazione container, con **avanzamento live** (fase, percentuale di download e log) nel popup di aggiornamento.

### Dettaglio container
Popup di dettaglio con grafici CPU, RAM, rete; reti, porte, volumi; environment completo; label Docker / Compose; cronologia aggiornamenti; docker-compose associato (se disponibile).

### MQTT + Home Assistant (opzionale)
- Discovery automatico di `sensor` (stato, risorse e aggiornamenti) e `switch` (per controllare i container).
- Comandi via MQTT: `start`, `stop`, `restart`, `pause`, `unpause`, `full_update`.
- Stato pubblicato su un base topic configurabile (es. `d2ha_server`).

### App installabile (PWA)
- **Manifest** e **service worker** integrati: D2HA è installabile dal browser come app standalone su Android, iOS e desktop.
- I requisiti PWA (manifest, service worker registrato, icone 192/512) sono presenti su **tutte le pagine d'ingresso** (login, splash, dashboard), così l'installazione è offerta fin dalla schermata di accesso.
- Schermata di **splash** durante l'avvio del backend e cache di base degli asset statici per un primo caricamento più rapido.
- Funziona dietro reverse proxy HTTPS; se usi un gateway di autenticazione davanti all'app (es. **Cloudflare Access**) leggi la nota nella sezione **🛡️ Sicurezza**.

## 🧱 Architettura

- **Backend** — **Flask** per esporre le API JSON e servire l'interfaccia web statica; **Paho MQTT** (opzionale) per pubblicare sensori e switch in Home Assistant; accesso diretto a **Docker Engine** tramite socket per evitare dipendenze dalla CLI.
- **Frontend** — interfaccia server-rendered (Jinja2) minimalista, senza build step, ottimizzata per ambienti headless; grafici live con **Chart.js** e polling leggero; **theme switcher** (dark/light) e **multi-lingua** su tutte le pagine; **PWA** (`manifest.json` + `sw.js`).
- **Service layer** — logica Docker in un **package modulare** (`services/docker/`) suddiviso per dominio (container, immagini/aggiornamenti, reti, volumi, sistema, eventi); lettura tramite **docker SDK** Python (accesso diretto al socket); gestione aggiornamenti confrontando tag locali e remoti e leggendo label OCI; cache in memoria per ridurre le chiamate al daemon Docker.
- **Integrazione Home Assistant** — discovery MQTT automatico (sensori e switch) con payload compatibili con la UI Lovelace; pubblicazione stati su topic dedicati con intervallo configurabile.

> Obiettivo: restare “batteries included” senza database, message queue o componenti aggiuntivi.

## 📋 Requisiti

- Docker Engine 20.10+ con accesso al socket `/var/run/docker.sock`.
- Python 3.11+ se esegui in modalità bare-metal.
- Accesso di rete al broker MQTT (solo se abiliti l'integrazione).
- Architetture testate: `amd64`, `arm64` (Raspberry Pi 4/5).

## 🔐 Autenticazione e onboarding

- **Credenziali iniziali:** `admin / admin`.
- Al **primo login** viene avviata una procedura guidata che copre quattro passi:
  1. scelta di una nuova password (obbligatoria) e, se vuoi, di un nuovo username;
  2. abilita o salta la **2FA TOTP** (Google Authenticator, Aegis, ecc.);
  3. configurazione iniziale di **Modalità sicura** (on di default) e **Modalità prestazioni** (off di default);
  4. scelta del comportamento iniziale per l'autodiscovery MQTT: esporre o meno tutte le entità di default.
- Tutte le scelte restano modificabili dopo il primo avvio: credenziali e 2FA dalla pagina Sicurezza; Modalità sicura / prestazioni dall'icona a ingranaggio nell'header; entità MQTT dalla pagina Autodiscovery.
- La configurazione di sicurezza viene salvata nel file JSON `auth_config.json` (creato automaticamente con permessi ristretti al primo avvio). Per conservarla tra i rebuild puoi puntare il percorso con `D2HA_AUTH_CONFIG_PATH` (default `/app/data/auth_config.json`) e montare il volume `./data:/app/data` nel `docker-compose.yml`.
- Le preferenze di autodiscovery MQTT vengono salvate in `autodiscovery_preferences.json` nello **stesso percorso** di `auth_config.json` (o nel path indicato da `D2HA_AUTODISCOVERY_PREFS_PATH`) così da restare persistenti quando monti il volume `./data:/app/data`.
- Variabili utili: `D2HA_SECRET_KEY` per la chiave di sessione Flask (obbligatorio in produzione); `D2HA_ADMIN_USERNAME` per personalizzare l'username iniziale prima del primo avvio.

> Suggerimento: esegui D2HA dietro a un reverse proxy HTTPS e considera la 2FA indispensabile se l'interfaccia è esposta in rete.

## 🛡️ Sicurezza

- **Protezione CSRF:** tutte le richieste di modifica via form HTML sono protette da token CSRF per-sessione; le API JSON sono esenti perché la Same-Origin Policy del browser impedisce richieste `application/json` cross-origin senza preflight.
- **Rate limiting login:** dopo troppi tentativi falliti l'endpoint `/login` restituisce HTTP 429 per 15 minuti, rallentando attacchi bruteforce. Il conteggio è basato sull'**IP reale del client** (header `CF-Connecting-IP` / `X-Forwarded-For` quando sei dietro un proxy), non sull'indirizzo del proxy.
- **Rate limiting API:** gli endpoint `/api/*` di scrittura sono limitati per IP per attenuare abusi e loop accidentali.
- **Modalità sicura:** quando attiva, le **azioni distruttive** (eliminazione di container e reti) richiedono una conferma esplicita; senza conferma l'API risponde con HTTP 403. L'interfaccia mostra un dialog di conferma prima di eliminare.
- **Timeout di sessione:** logout automatico per inattività, configurabile (default 30 minuti) dalla pagina Sicurezza.
- **Pagina Sicurezza:** da `/settings/security` puoi cambiare username/password dell'admin e gestire la 2FA. Ogni modifica richiede la password attuale e, se la 2FA è attiva, anche un codice TOTP valido. L'abilitazione 2FA è guidata (QR/URI + verifica esplicita); la disattivazione richiede password + codice corrente.
- **Redazione log:** i valori sensibili (secret key, password MQTT, hash/segreti) vengono oscurati nei log.
- **Best practice:** esegui sempre dietro reverse proxy HTTPS (Caddy, Traefik, Nginx) e ricorda che chi accede a D2HA può controllare Docker sull'host.

> **PWA dietro un gateway di autenticazione (Cloudflare Access / Zero Trust, Authelia, ecc.)**
> Se proteggi l'app con un gateway che intercetta ogni richiesta, l'installazione come PWA **non funziona** finché il manifest e il service worker non sono raggiungibili senza autenticazione: il browser li scarica **senza cookie** e riceverebbe la pagina di login del gateway invece dei file.
> Configura un **bypass pubblico** per i percorsi PWA — almeno `/sw.js` e `/static/*` (che includono `manifest.json` e le icone) — lasciando protetto il resto dell'app. Su Cloudflare Access: crea un'applicazione dedicata a quei percorsi con criterio **Bypass / Everyone**.

## 🗂️ Struttura del progetto

```text
d2ha/
├── app.py              # Entrypoint Flask, splash gating, logging
├── auth_store.py       # Lettura/scrittura di auth_config.json
├── csrf.py             # Protezione CSRF
├── rate_limiter.py     # Rate limiting API
├── i18n.py / theme.py  # Multi-lingua (IT/EN) e tema dark/light
├── version.py          # Versione (da D2HA_VERSION / git tag / SHA)
├── mqtt/               # Discovery e pubblicazione stato MQTT
├── routes/             # Blueprint Flask: ui.py, api.py, auth.py
├── services/
│   ├── docker/         # Package modulare: containers, images_updates,
│   │                   #   networks, volumes, system, events, base
│   ├── preferences.py  # Preferenze autodiscovery MQTT
│   └── utils.py        # Helper (human_bytes, slug, ecc.)
├── static/             # CSS/JS, icone, manifest.json, sw.js (PWA)
└── templates/          # Jinja2: layouts/, partials/, pagine
```

## 🧪 Avvio in locale (sviluppo)

```bash
git clone https://github.com/arborae/docker2homeassistant.git
cd docker2homeassistant

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r d2ha/requirements.txt

python d2ha/app.py
```

Per default l’app è disponibile su `http://localhost:12021`. Assicurati che l’utente con cui la esegui abbia accesso a Docker (es. aggiungendolo al gruppo `docker` su Linux).

## 🐳 Installazione via Docker / Docker Compose

Usa le **immagini pronte su GitHub Container Registry (GHCR)**.

**Tag immagine** — stabile: `ghcr.io/arborae/docker2homeassistant:latest` o `:X.Y.Z` (es. `0.1.1`); sviluppo: `ghcr.io/arborae/docker2homeassistant:nightly`.

Esempio `docker-compose.yml`:

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
      D2HA_VERSION: "latest"
      D2HA_SECRET_KEY: "cambia-questa-chiave"   # obbligatoria in produzione
      # D2HA_ADMIN_USERNAME: "admin"            # opzionale, prima del primo avvio
      # MQTT_BROKER: "192.168.1.100"            # config MQTT opzionale
      # MQTT_PORT: "1883"
      # MQTT_USERNAME: "homeassistant"
      # MQTT_PASSWORD: "password"
      # MQTT_BASE_TOPIC: "d2ha_server"
      # D2HA_GITHUB_TOKEN: "ghp_xxx"            # opzionale, alza il rate limit GitHub API
```

Il volume `./data:/app/data` mantiene persistenti `auth_config.json` (credenziali + 2FA + sicurezza) e `autodiscovery_preferences.json` (sensori MQTT esposti). Avvia con `docker compose up -d`; aggiorna con `docker compose pull && docker compose up -d`. Per la nightly imposta l'immagine su `:nightly`.

## 📡 Configurazione MQTT

L’integrazione MQTT è completamente opzionale — senza broker, D2HA funziona come semplice dashboard web. Variabili d’ambiente supportate:

`MQTT_BROKER` · `MQTT_PORT` · `MQTT_USERNAME` · `MQTT_PASSWORD` · `MQTT_BASE_TOPIC` · `MQTT_DISCOVERY_PREFIX` · `MQTT_NODE_ID` · `MQTT_STATE_INTERVAL` (secondi tra le pubblicazioni di stato).

Se `paho-mqtt` non è installato o la connessione fallisce, la parte MQTT viene disabilitata e la UI continua a funzionare normalmente.

## 🌐 Endpoint principali

**UI:** `/` (dashboard) · `/containers` · `/images` · `/volumes` · `/networks` · `/updates` · `/events` · `/autodiscovery` · `/settings/security`

**API JSON (estratto):**
- `GET /api/overview` – Panoramica host e stack
- `GET /api/containers/<id>/details` – Dettaglio container
- `POST /api/containers/<id>/<action>` – Azione container (`start`/`stop`/`restart`/`pause`/`unpause`/`delete`/`kill`); le azioni distruttive richiedono `?confirm=1` se la modalità sicura è attiva
- `POST /api/containers/<id>/full_update` – Pull immagine + ricreazione container
- `GET /api/notifications` · `GET|POST /api/networks` · `GET /api/containers/<id>/stats`
- `GET|POST /api/containers/<id>/updates` · `POST /api/containers/<id>/updates/frequency`
- `GET|POST /api/containers/<id>/compose` · `GET|POST /api/compose` · `GET /api/containers/<id>/logs?tail=<N|all>`

**Sistema / PWA:** `GET /api/health` · `GET /splash` · `GET /sw.js` · `GET /static/manifest.json`

## 🗺️ Roadmap

- Filtro avanzato e ricerca tra container / stack
- Grafici storici CPU/RAM (serie temporali)
- Template Lovelace pronti per Home Assistant
- Ruoli / permessi (lettura vs controllo)

## 🤝 Contributi

1. Fai un **fork** del repository.
2. Crea un branch: `feature/mia-funzionalita`.
3. Aggiungi test o controlli di base dove ha senso.
4. Apri una **pull request** spiegando chiaramente le modifiche.

## 📄 Licenza

Questo progetto è distribuito sotto licenza **MIT**. Vedi il file [`LICENSE`](./LICENSE) per tutti i dettagli.
