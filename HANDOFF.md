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
- **v0.4.1 released** (PR #215: changelog dated, 0.4.1 section); CI published the
  `0.4.1` and `latest` images.

## Next up

1. Home Assistant integration in `custom_components/d2ha`, installable through HACS as a
   custom repository: update entities, container states, update buttons (uses `/api/v1`
   with the bearer token). In progress, see the table above.
2. **Landing page / docs site**: the current `docs/` site is completely out of sync with
   0.4 (it shows pages and features that no longer exist). The owner wants it redone.
   Claude's plan: new site in `docs-site/` published through GitHub Actions instead of
   the `docs/` folder.
3. Telegram notifications for available updates: the owner wants to retire Diun and
   docker-telegram-notifier. Proposed direction: **not** in D2HA core (notifications were
   removed in 0.4 by design) but as Home Assistant automations on the HACS integration
   entities (update available → notify.telegram). Decide with the owner before building
   anything.
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
