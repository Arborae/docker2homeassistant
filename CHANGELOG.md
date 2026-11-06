# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Telegram notifications**, to replace tools like Diun and docker-telegram-notifier:
  a message when a new version of an image is found (once per version, with the release
  notes link and a warning for breaking changes), when a container stops with an error
  (exit code other than 0 and 143) and when a container becomes unhealthy. Each kind can be
  turned off in Settings, with the language of the messages and a test button. The bot token
  and the chat come from `D2HA_TELEGRAM_TOKEN` and `D2HA_TELEGRAM_CHAT_ID`; they are never
  stored or shown. The same alert for the same container is sent at most every 15 minutes.

### Fixed
- D2HA no longer offers to update the socket proxy it reaches Docker through, and "Update
  all" skips it: recreating it cut D2HA off from Docker halfway and left the proxy stopped.
  The proxy is recognised from the host name in `DOCKER_HOST`.

## [0.4.1] - 2026-11-06

### Changed
- The README is rewritten from scratch for 0.4, with screenshots taken from the demo
  (sample data only).
- The license changes from MIT to GNU GPL v3.0.
- The Settings page suggests adding D2HA to HACS as a custom repository (the integration
  is not in the default HACS list).

### Added
- `scripts/demo.py`: runs the interface with sample data and no Docker, with real release
  notes from GitHub.

### Fixed
- The update progress bar no longer jumps back when the first percentage arrives.

## [0.4.0] - 2026-11-06

A rewrite focused on what D2HA is for: knowing when your containers have updates,
seeing them in Home Assistant, and reading container logs. Everything else is gone.

### Added
- **Real versions from the registry.** Update checks read the image digest with a HEAD request
  (free on Docker Hub) and, only when something changed, the labels of the new image, so the
  latest version shown is the real one. Private registries fall back to the Docker daemon.
- **Release notes that match.** Notes come from the GitHub release of the exact new version,
  found through the image `org.opencontainers.image.source` label; breaking changes are highlighted.
- **Safer updates.** The new image is pulled while the container keeps running; the container is
  then recreated with the settings you gave it (the defaults of the old image are not copied over,
  anonymous volumes are re-attached, networks and aliases kept). If the new container does not
  start, the previous one is restored.
- Updates run as background jobs with live progress (phase, download size, percentage):
  closing the page does not stop them.
- Versioned API (`/api/v1`) with a bearer token for Home Assistant.
- First-run setup protected by a one-time code printed in the container logs.
- Read-only mode (`D2HA_READ_ONLY`) and an example compose file with a Docker socket proxy.
- `scripts/demo.py`: the interface with sample data, no Docker needed.
- Complete Italian and English translations; light, dark and system themes.

### Changed
- New interface: one page for containers (updates, release notes, logs) and one for settings.
- License: GNU GPL v3.0 (was MIT).
- Update checks run every 6 hours by default (configurable) instead of continuously.
- Python 3.13; images for amd64 and arm64.
- The app uses ~50 MB of RAM and almost no CPU when idle (no per-container statistics polling).
- Data is kept in one file, `d2ha.json` (mode 600). Accounts and preferences of 0.3 are imported
  automatically on first start; the old files are left in place.

### Removed
- MQTT integration, replaced by a native Home Assistant integration installable from HACS.
- Network, volume, image and compose-file management; events page; CPU/RAM/network charts;
  container details with environment variables; safe, performance and debug modes; notifications
  panel; clock settings.

### Security
- Container environment variables and commands are no longer exposed by the API.
- Strict Content Security Policy, no third-party requests (Chart.js from a CDN is gone).
- Actions only through POST with a CSRF token; logout through POST.
- Sessions are invalidated on password or 2FA change; 2FA codes cannot be reused.
- The service worker caches static files only, never pages, API responses or logs.

## [0.3.1] - 2026-06-20

### Added
- Live progress while updating a container: current phase, download progress (bytes and
  percentage across all layers) and the last log line, streamed over SSE.
- Optional `D2HA_GITHUB_TOKEN` to raise the GitHub API rate limit for release notes
  (60 to 5000 requests per hour).
- `.dockerignore` to keep local data and secrets out of the image.

### Fixed
- The update runs in a background thread: a client disconnecting halfway through the pull no
  longer leaves the container removed but not recreated.
- The image revision label (a commit hash) is no longer shown as a version.

### Removed
- The old debug overlay of the update flow, replaced by the progress view.

## [0.3.0] - 2026-06-20

### Added
- Installable Progressive Web App (`manifest.json`, service worker), offered on every entry
  page including sign-in.
- Splash screen with a backend health check during start-up.
- CSRF protection for form requests.
- Rate limiting on write endpoints under `/api/*`.
- Configurable session timeout (automatic logout after inactivity).

### Changed
- The monolithic Docker service split into a package by domain: containers, images and
  updates, networks, volumes, system, events.
- Login and API rate limits use the real client IP behind a reverse proxy
  (`CF-Connecting-IP` or the first `X-Forwarded-For` hop).
- Update-flow debug output goes through the logger, with sensitive values redacted.

### Fixed
- Container actions no longer report success when the Docker operation fails.
- Image removal errors are no longer ignored.
- A template error in the splash screen that produced invalid JavaScript.
- Validation of the `minutes` input of the update frequency endpoint (no more HTTP 500).

### Security
- Consistent safe mode: destructive container actions require explicit confirmation.
- Fixed a bypass of the login brute-force protection through a forged `X-Forwarded-For`.
- Fixed an open-redirect edge case (`/\host`) in the `next` parameter.
- Sensitive values are redacted in logs.

## [0.2.3] and earlier

Docker dashboard with container, image, volume and network management, updates from OCI labels,
MQTT integration with Home Assistant, sign-in with onboarding and TOTP two-factor authentication,
dark and light themes, Italian and English.
