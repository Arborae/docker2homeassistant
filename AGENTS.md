# D2HA – Agent Guide

Short guide for AI/code assistants working on Docker to Home Assistant (Flask-based web dashboard with optional MQTT/Home Assistant integration).

## Project Structure
- `d2ha/` – Flask app entrypoint (`app.py`), Docker integration (`docker_service.py`), auth store, i18n/theme helpers, static assets, and Jinja templates. Includes deployment files (`Dockerfile`, `docker-compose.yml`, `requirements.txt`, `version.py`).
- `docs/` – Static marketing/docs assets (HTML, CSS, JS, GIFs) plus install guide.
- `tests/` – Python unit tests (unittest/pytest compatible).
- `.github/workflows/` – CI pipelines to build/publish Docker images (stable + nightly).

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
  python -m pytest
  ```
  or
  ```bash
  python -m unittest discover -s tests
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
  - Nightly: `ghcr.io/arborae/docker2homeassistant:nightly` (or `:nightly-<commit_sha>`)
- CI builds/pushes images via GitHub Actions (`publish-docker.yml` on releases; `nightly-docker.yml` on `main`).

## Configuration & Secrets
- Environment variables: `D2HA_SECRET_KEY`, `MQTT_*` (broker, port, username/password, topics/prefix, node id, state interval), `D2HA_AUTH_CONFIG_PATH`.
- Docker socket is required (`/var/run/docker.sock` bind mount). Persist data in `./data` for auth and MQTT autodiscovery preferences.
- Never commit secrets/tokens. Use `.env` locally and repository secrets in CI. Redaction of sensitive values is implemented in logging.

## Style & Conventions
- Language: English for code, comments, docs (unless a file is clearly Italian).
- Follow existing Flask/Jinja structure; keep functions clear over dense one-liners.
- Avoid adding new dependencies unless necessary and justified.
- YAML (Compose/GitHub Actions): preserve indentation and current patterns. Do not alter network/volume settings unless required.

## Pull Requests
- Keep diffs focused and well-scoped; describe context, solution, and any manual steps (e.g., migrations, rebuilds).
- Update docs if behavior, endpoints, or configs change.
- Add or update tests when altering logic.

## Testing Targets
- For logic changes, ensure `tests/` still pass via `python -m pytest`.
- If you change Docker build behavior, verify local `docker build` or `docker compose` as appropriate.

## Other Notes
- App defaults to `admin`/`admin`; onboarding wizard forces password change and optional 2FA.
- Running without MQTT is supported; MQTT features auto-disable if not configured.
