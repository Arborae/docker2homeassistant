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
- `API.md` – the `/api/v1` contract used by the Home Assistant integration.

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
Two AI assistants work on D2HA, **Claude** and **Kimi**; the owner merges and releases.
They cannot talk to each other: GitHub issues, pull requests and `HANDOFF.md` are the only
channel, so everything must be written there.

### Areas
Each agent changes only its own area. For a change in the other area, open an issue with
the other agent's label and wait: never edit it yourself.

| Area | Agent |
| --- | --- |
| The app: `d2ha/`, `tests/`, `scripts/`, `API.md`, `compose.example.yaml`, `.env.example`, `README.md`, `CHANGELOG.md`, `.github/` | Claude |
| The Home Assistant integration (repository `arborae/ha-d2ha`) and the landing page `docs/` | Kimi |
| The owner's server | Claude only (Kimi works through the GitHub API) |
| Merges, releases, version numbers, repository settings | the owner |

`HANDOFF.md` and `AGENTS.md` are shared: change them in small steps and pull first.
`API.md` is the contract between the app and the integration: the app changes it only by
adding (see its rules), the integration relies only on what it says.

### Tasks
- Every task is a **GitHub issue** with the label `claude` or `kimi`. Before starting, check
  that no open issue or pull request already covers it, assign the label to yourself and
  comment "Started on branch `<branch>`".
- One branch per issue, from an up-to-date `main`, named `<type>/<issue>-<short-name>`
  (e.g. `feat/221-api-docs`); the pull request says `Closes #<issue>`.
- Never push to a branch that has been merged or that belongs to the other agent: branches
  are deleted on merge, start a new one.
- `HANDOFF.md` holds the overall state and the notes for the other agent; it alone may be
  committed straight to `main` (owner's decision). It is public: nothing about the owner's
  server, network, domains or accounts.

### Before opening a pull request (both agents)
1. **Facts from the source.** `git fetch --prune`, then check branches, issues, pull requests
   and releases with `git`/`gh` before stating anything about them. Read the code you change
   and its tests; never assume how something works.
2. **Tests.** `pyflakes` and `pytest` pass. New behaviour has new tests, fixed bugs a test
   that fails without the fix.
3. **Try it for real.** UI and behaviour in `scripts/demo.py` or a throwaway container, in
   both languages and both themes, at phone width for UI. Never on the owner's data.
4. **Clean files.** Unix line endings, no permission changes, no editor or OS files
   (the CI checks fail otherwise). Work on Linux or WSL, not on a Windows share.
5. **Privacy.** No secrets, tokens, personal e-mail addresses, IP addresses, domains or
   details of the owner's setup in code, docs, screenshots, commit messages or pull requests.
   Screenshots come from the demo. Commits use the GitHub noreply address.
6. **Docs.** `CHANGELOG.md` (under "Unreleased") for anything users notice; `API.md` for any
   API change; README when the setup changes.
7. **Read your own diff** line by line before asking for review.

### Pull requests and review
- Fill in the template: what changes, why, **how it was tested** (commands and results).
- Every pull request is reviewed by the **other agent** before the owner merges it. The
  reviewer checks it against the list above and runs the tests. Both agents use the owner's
  GitHub account, which cannot approve its own pull requests, so the verdict is a comment
  that starts with **"Review by Claude: approved"** or **"Review by Kimi: changes requested"**
  (or the other way round), followed by the findings with file and line. The author answers
  every finding in the pull request.
- The owner merges only after an "approved" review comment and with green checks.
- Every commit has a `Co-Authored-By` trailer with the agent's name.

## Release
Images are built by GitHub Actions: `nightly` on every push to `main`, `latest` and `X.Y.Z` on a
GitHub release (amd64 + arm64). Update `CHANGELOG.md` with every user-visible change.
Before the owner publishes a release, Claude opens a pull request that turns "Unreleased" into
`[X.Y.Z] - date` (the owner chooses the number), so the released changelog is complete.
