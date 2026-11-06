# Handoff

Shared state between the AI assistants working on this repository (see AGENTS.md,
"Multi-agent coordination"). Read this first; update it last.

## In progress

| Task | Branch | Agent | Since |
| ---- | ------ | ----- | ----- |
| HACS integration (`custom_components/d2ha`) | `feature/hacs-integration` | Kimi | 2026-11-06 |
| Telegram notifications in D2HA (updates, crashed and unhealthy containers) | `feature/telegram-notifications` | Claude | 2026-11-06 |

## State (2026-11-06, evening)

- D2HA 0.4 rewrite merged to `main` (PR #213) and released as **v0.4.0**.
- `feature/v0.4-rewrite` merged to `main` (PR #214): new README, GPLv3 license,
  `scripts/demo.py`, progress-bar fix, HACS step in Settings, coordination docs.
- **v0.4.1 released** (PR #215: changelog dated, 0.4.1 section); CI published the
  `0.4.1` and `latest` images.
- The owner's server runs 0.4.1 behind a socket proxy (done by Claude, 2026-11-06).
- To fix: D2HA must never offer to update the socket proxy it talks through. Being done
  on `feature/telegram-notifications`.

## Next up

1. Home Assistant integration in `custom_components/d2ha`, installable through HACS as a
   custom repository: update entities, container states, update buttons (uses `/api/v1`
   with the bearer token). In progress, see the table above.
2. Landing page: rewritten for 0.4 (PR #217) and deployed by its own workflow (PR #218);
   Pages source is "GitHub Actions". A full docs site is **not** wanted for now: the owner
   will decide after the integration.
3. Telegram notifications: **decided with the owner, inside D2HA** (not Home Assistant
   automations, which cannot report Home Assistant itself crashing). They replace Diun
   (update found) and docker-telegram-notifier (container stopped with an error, exit code
   other than 0 and 143, or unhealthy). Bot token and chat from the environment. No
   commands from Telegram. In progress, see the table above.
4. Move the server to 0.4, update the HA dashboards and remove the old "Docker ..."
   helpers and MQTT topics.

## Notes from the owner

- The tunnel target of `d2ha.casadm.uk` now points to `http://d2ha_server:12021`.
- Do not change the MQTT password for now.
- Portainer, Diun and docker-telegram-notifier are in use. The owner's goal: D2HA +
  the HACS integration should cover update checks, Telegram notifications and updating
  containers from Home Assistant, so Diun and docker-telegram-notifier can be retired
  (see "Next up" 3 for the proposed approach).
- The landing page is out of sync with 0.4 and the owner wants it redone (see "Next up" 2).
- Local branch `feature/update-progress` can be deleted.
- The local working copy on the server belongs to Claude; Kimi works only through the
  GitHub API on its own branches (Kimi's Windows share left CRLF and root-owned files,
  fixed on 2026-11-06).
- The HACS integration will live in a new repository, `arborae/ha-d2ha` (Kimi). When it
  exists, the README and the "Home Assistant" step in Settings (`settings.ha_step1`) must
  point to it instead of this repository.
