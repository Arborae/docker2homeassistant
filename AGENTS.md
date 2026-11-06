# D2HA – Agent Guide

Guide for AI/code assistants working on D2HA (Docker to Home Assistant): a small Flask app that
checks container image updates, shows release notes and logs, updates containers safely, and
exposes all of it to Home Assistant through a versioned API.

## Scope
Keep D2HA focused: update status + release notes, Home Assistant integration, container logs,
Telegram notifications (updates, crashed and unhealthy containers), sign-in with 2FA, PWA,
light/dark theme, Italian/English. Do not add general Docker management
(networks, volumes, compose editing, stats dashboards).

## Layout
- `d2ha/` – the container app (build context).
  - `app.py` – `create_app()` factory, security headers, shared services (`D2HA`).
  - `config.py` – settings from environment variables.
  - `store.py` – `d2ha.json` (account, token hash, settings, per-container preferences); imports 0.3 data.
  - `security.py` – passwords, TOTP, API token, CSRF, sessions, rate limits, auth decorators.
  - `docker_ops.py` – Docker access: list, logs, prune, safe recreate (`build_create_config`).
  - `registry.py` – registry v2 client (HEAD digest, labels of the new image).
  - `releases.py` – GitHub release notes and breaking changes.
  - `checker.py` – scheduled update check, persisted to `state.json`.
  - `jobs.py` – background jobs with live progress.
  - `notify.py` – Telegram notifications and the Docker events watcher.
  - `web.py` – HTML pages; `api.py` – `/api/v1` (session + CSRF, or bearer token).
  - `i18n.py` + `translations/{en,it}.json` – every user-visible string.
  - `templates/`, `static/` (`app.css`, `app.js`, `icons.svg`, `sw.js`, `manifest.json`).
- `tests/` – pytest, with fakes for Docker, registry and GitHub (`tests/conftest.py`).
- `scripts/demo.py` – the interface with sample data and no Docker, used for the screenshots.

## Rules
- English for code and comments; user-facing text only through translation keys, in both languages
  (`tests/test_i18n.py` checks parity).
- No inline scripts or styles, no external requests from the browser (strict CSP).
- Writes to Docker only through POST endpoints with CSRF or the API token.
- Never expose container environment variables, commands or secrets.
- New dependencies only when really needed.

## Commands
```bash
python -m venv .venv && .venv/bin/pip install -r d2ha/requirements.txt pytest pyflakes
.venv/bin/pyflakes d2ha tests && .venv/bin/pytest -q
docker build -t d2ha:local -f d2ha/Dockerfile d2ha
```

## Multi-agent coordination
More than one AI assistant works on this repository. To avoid conflicts:
- Read `HANDOFF.md` before starting any work and update it before finishing: what you did,
  what is in progress, what comes next.
- Start every session with `git pull`, end it with a commit and a push. Never leave
  uncommitted work behind.
- One branch per task; declare the branch and the task as "in progress" in `HANDOFF.md`
  before touching files, and never start work that another agent has marked in progress.
- Add yourself in the `Co-Authored-By` trailer of every commit.
- Exception (decided by the owner): `HANDOFF.md` alone may be committed straight to `main`,
  without a pull request, so that claims and state are visible to everyone at once.
  Everything else still goes through a branch and a pull request.

## Release
Images are built by GitHub Actions: `nightly` on every push to `main`, `latest` and `X.Y.Z` on a
GitHub release (amd64 + arm64). Update `CHANGELOG.md` with every user-visible change.
