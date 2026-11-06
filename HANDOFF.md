# Handoff

Shared state between the AI assistants working on this repository (see AGENTS.md,
"Multi-agent coordination"). Read this first; update it last.

## In progress

| Task | Branch | Agent | Since |
| ---- | ------ | ----- | ----- |
| HACS integration (`custom_components/d2ha`) | `feature/hacs-integration` | Kimi | 2026-11-06 |

## State (2026-11-06, evening)

- D2HA 0.4 rewrite merged to `main` (PR #213) and released as **v0.4.0**.
- `feature/v0.4-rewrite` merged to `main` (PR #214): new README, GPLv3 license,
  `scripts/demo.py`, progress-bar fix, HACS step in Settings, coordination docs.
- `chore/release-0.4.1` prepares the changelog for **v0.4.1**; publish the GitHub release
  after merging it (CI builds the `0.4.1` and `latest` images).

## Next up

1. Home Assistant integration in `custom_components/d2ha`, installable through HACS as a
   custom repository: update entities, container states, update buttons (uses `/api/v1`
   with the bearer token).
2. New docs site in `docs-site/` replacing the old `docs/` (publish through GitHub Actions,
   not the docs folder — ask the owner first).
3. Release 0.4.1: merge `chore/release-0.4.1`, create the GitHub release, then move the
   server to the new system, update the HA dashboards and remove the old "Docker ..."
   helpers and MQTT topics.

## Notes from the owner

- The tunnel target of `d2ha.casadm.uk` now points to `http://d2ha_server:12021`.
- Do not change the MQTT password for now.
- Portainer, Diun and docker-telegram-notifier are in use; the goal is for D2HA to cover
  updates, Telegram notifications and container updates from HA, so those tools can be
  retired.
- Local branch `feature/update-progress` can be deleted.
