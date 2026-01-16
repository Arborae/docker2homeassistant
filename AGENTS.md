# D2HA – Agent Guide

Short guide for AI/code assistants working on Docker to Home Assistant (Flask-based web dashboard with optional MQTT/Home Assistant integration).

## Standard Development Workflow
1.  **Plan**: Understand the goal. If refactoring or adding complex features, outline a plan first.
2.  **Implement**: Make changes following the modular structure (see below).
3.  **Update Tests**: Ensure `tests/` reflect your changes. Use unit tests to verify logic.
4.  **Verify**: Run `python -m unittest discover tests` and perform manual checks (start app with `python d2ha/app.py`).

## Project Structure (Modular)
- `d2ha/` – Core application.
    - `app.py`: Application entry point, service initialization, blueprint registration.
    - `services/`: Core logic and business rules.
        - `docker.py`: `DockerService` for all Docker interactions.
        - `preferences.py`: `AutodiscoveryPreferences` for HA config.
        - `utils.py`: Shared utilities.
    - `mqtt/`: All MQTT-related logic (`manager.py`).
    - `routes/`: Flask Blueprints:
        - `ui.py`: Frontend routes and view logic.
        - `api.py`: JSON API endpoints (`/api/overview`, `/api/networks`, `/api/notifications`, etc.).
        - `auth.py`: Authentication and onboarding flows.
    - `templates/`, `static/`: Frontend assets.
- `docs/` – Static marketing/docs assets.
- `tests/` – Python unit tests.
- `.github/workflows/` – CI pipelines.

## Local Development
1. Create a virtualenv and install deps:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r d2ha/requirements.txt
   ```
2. Run the Flask app:
   ```bash
   python d2ha/app.py
   ```
   App listens on `http://localhost:12021`. Ensure your user can access the Docker socket.

## Running Tests & Quality Checks
- Unit tests (from repo root):
  ```bash
  python -m unittest discover tests
  ```
- No dedicated linters/formatters are configured in this repo; follow existing style.

## Build & Deployment
- Docker image (local):
  ```bash
  docker build -t d2ha:local -f d2ha/Dockerfile d2ha
  ```
- Docker Compose (local dev):
  ```bash
  D2HA_VERSION=dev docker compose -f d2ha/docker-compose.yml up -d
  ```
- Published images:
  - Stable/Release: `ghcr.io/arborae/docker2homeassistant:latest` or `:X.Y.Z`
  - Nightly: `ghcr.io/arborae/docker2homeassistant:nightly`
- CI builds/pushes images via GitHub Actions.

## Configuration & Secrets
- Environment variables: `D2HA_SECRET_KEY`, `MQTT_*`, `D2HA_AUTH_CONFIG_PATH`.
- Docker socket is required (`/var/run/docker.sock`).
- Never commit secrets/tokens.

## Style & Conventions
- **Modular Code**: Logic goes in `services/`, routes in `routes/`, MQTT in `mqtt/`. **Do not add logic to `app.py` directly.**
- **Documentation**: If adding new API endpoints, **always update the README.md** to include them.
- Language: English for code/comments.
- Follow existing Flask/Jinja structure.
- Avoid new dependencies unless necessary.

## Pull Requests
- Keep diffs focused.
- Update docs if behavior changes.
- **Always update tests** when altering logic.
