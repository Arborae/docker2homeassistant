# D2HA API v1

The contract between D2HA and its clients, above all the Home Assistant integration.
It describes what `d2ha/api.py` does; when the two disagree, the code is right and this
file is fixed in the same pull request.

## Rules

- Base path: `/api/v1`. JSON in and out, UTF-8.
- **Additive changes only** within v1: new endpoints, new fields, new error codes. Clients
  must ignore fields they do not know. Removing or renaming anything means `/api/v2`.
- Every change is listed in `CHANGELOG.md` and in this file, in the same pull request.

## Authentication

| Client | How | Writes |
| --- | --- | --- |
| Home Assistant, scripts | `Authorization: Bearer <token>`, created in **Settings → Home Assistant** (shown once, stored as a hash) | allowed |
| The web interface | the session cookie | need the `X-CSRF-Token` header |

A wrong or revoked token gives `401 {"error": "unauthorized"}`, also when a session cookie
is present. Logs are for the web interface only: the token cannot read them.

Writes (`POST`, `PUT`) are limited to 30 per minute per client IP: Home Assistant on the
same host shares one IP for all its calls.

## Errors

Errors are `{"error": "<code>"}` with these status codes:

| Status | `error` | When |
| --- | --- | --- |
| 400 | `bad_request` | the body is not what the endpoint expects |
| 400 | `self_update` | the container is D2HA itself |
| 400 | `proxy_update` | the container is the socket proxy D2HA reaches Docker through |
| 401 | `unauthorized` | no session and no valid token |
| 403 | `csrf` | a session write without a valid `X-CSRF-Token` |
| 403 | `read_only` | D2HA runs with `D2HA_READ_ONLY=true` |
| 404 | `not_found` | unknown container key or job id |
| 404 | `no_notes` | no release notes for this container |
| 409 | `nothing_to_update` | "update all" with nothing to update |
| 429 | `rate_limited` | too many writes, retry later |

## Reading

### `GET /info`

```json
{
  "version": "0.5.0",
  "api": 1,
  "read_only": false,
  "docker": "28.4.0",
  "check": {"last": 1762448964.1, "next": 1762470564.1, "interval_hours": 6, "running": false},
  "counts": {"containers": 38, "running": 37, "updates": 8, "unused_images": 3}
}
```

`check.last` and `check.next` are Unix times, `null` before the first check.
`counts.unused_images` is `null` when Docker does not answer.

### `GET /containers`

`?ha=1` returns only the containers shown in Home Assistant (a switch in **Settings**),
without D2HA itself. The integration uses this form.

```json
{
  "containers": [
    {
      "key": "monitoring_grafana",
      "id": "4f2c…",
      "name": "grafana",
      "stack": "monitoring",
      "image": "grafana/grafana:latest",
      "image_id": "sha256:…",
      "state": "running",
      "status": "Up 3 days (healthy)",
      "health": "healthy",
      "created": 1762100000,
      "is_self": false,
      "is_proxy": false,
      "update": "available",
      "installed_version": "13.2.5",
      "latest_version": "13.3.0",
      "release_url": "https://github.com/grafana/grafana/releases/tag/v13.3.0",
      "has_notes": true,
      "breaking": true,
      "error": "",
      "ha": true,
      "can_update": true
    }
  ],
  "checked_at": 1762448964.1,
  "checking": false
}
```

| Field | Meaning |
| --- | --- |
| `key` | **Stable id** (stack + name): it survives updates and recreation, while `id` changes. Use it for entity unique ids and in URLs. |
| `state` | Docker state: `running`, `exited`, `paused`, `restarting`, `created`, `dead` |
| `health` | `healthy`, `unhealthy`, `starting`, or `""` without a health check |
| `update` | `available`, `current`, `local` (built on this host: nothing to compare), `pinned` (digest or no tag), `unknown` (not checked yet), `error` (the check failed: see `error`) |
| `installed_version`, `latest_version` | Text for people: a version when the image says it, otherwise tag and short digest. Equal unless `update` is `available`. Do not parse them. |
| `release_url` | Release page of the new version, or the project page; may be `""` |
| `has_notes` | `GET /containers/<key>/notes` will answer |
| `breaking` | The release notes have a breaking-changes section |
| `is_self`, `is_proxy` | D2HA itself, or the socket proxy it uses: never updated by D2HA |
| `can_update` | An update can be started now (false in read-only mode, for `is_self`, `is_proxy`, `local` and `pinned`) |
| `ha` | Shown in Home Assistant |

`checking` is true while a check runs; `checked_at` is the end of the last full check.

### `GET /containers/<key>/notes`

```json
{"version": "v13.3.0", "title": "13.3.0", "url": "https://github.com/…", "published": "2026-10-27",
 "body": "markdown…", "breaking": "markdown of the breaking section, or empty"}
```

`body` is the GitHub release text (Markdown, at most 20,000 characters): render it as
untrusted text.

### Logs (web interface only)

- `GET /containers/<key>/logs?tail=300` → `{"lines": [...], "tail": 300}` (10 to 5,000 lines,
  each starting with the Docker timestamp).
- `GET /containers/<key>/logs/stream` → Server-Sent Events: each `data:` is a JSON array of
  new lines; `: ping` comments every 15 s; the stream ends after 30 minutes.

## Actions

Every action answers `202` with a **job** and runs in the background, one job at a time
(an identical request while one is queued or running returns that job).

| Endpoint | Job `kind` | `result` when done |
| --- | --- | --- |
| `POST /check` | — (answers `{"started": true}`) | read `/info` or `/containers` afterwards |
| `POST /containers/<key>/update` | `update` | `{"changed": true, "id": "…", "name": "grafana"}` (`changed: false` if the image was already the latest) |
| `POST /update-all` | `update_all` | `{"updated": ["grafana"], "failed": [{"name": "x", "error": "error.pull_failed"}]}` |
| `POST /images/prune` | `prune` | `{"removed": 3, "reclaimed": 1236000000}` (bytes) |
| `PUT /containers/<key>/ha` `{"ha": false}` | — (answers `{"key", "ha"}`) | session only |

### Jobs

```json
{
  "id": "a1b2c3d4e5f6", "kind": "update", "target": "monitoring_grafana", "label": "grafana",
  "state": "running", "phase": "pull", "percent": 42, "current": 98000000, "total": 233000000,
  "detail": "Downloading 3e9cc8d20ba3", "step": 0, "steps": 0,
  "result": null, "error": null, "created": 1762449000.2, "finished": null
}
```

| Field | Meaning |
| --- | --- |
| `state` | `queued`, `running`, `done`, `failed` |
| `phase` | `pull`, `stop`, `create`, `start`, `cleanup`, `done` |
| `percent` | 0–100, or `null` while unknown |
| `current`, `total` | bytes downloaded, during `pull` |
| `step`, `steps` | for `update_all`: which container of how many (`label` is its name) |
| `error` | when `failed`: a message key such as `error.recreate_failed` (the old container was restored), `error.pull_failed`, `error.no_tag`, `error.not_found`; `detail` may add Docker's text |

Follow a job with `GET /jobs/<id>` (polling, 1–2 s is plenty) or `GET /jobs/<id>/events`
(Server-Sent Events, one JSON snapshot per change, closes when the job ends).
`GET /jobs` lists the jobs still queued or running. Finished jobs are kept for a while
(the last 30), then `404`.

After an update D2HA re-checks that image, so `/containers` shows the new state.

## For the Home Assistant integration

- Poll `GET /info` and `GET /containers?ha=1` (every 60 s is enough; checks run every few
  hours, so polling faster only costs requests).
- Build entities on `key`, never on `id` or `name`.
- Offer the install action only when `can_update` is true; show `is_self` / `is_proxy`
  containers as updatable by hand.
- Release notes: `has_notes` → `GET /containers/<key>/notes`, otherwise link `release_url`.
