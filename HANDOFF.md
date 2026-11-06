# Handoff

Shared state between the AI assistants working on this repository (see AGENTS.md,
"Multi-agent coordination"). Read this first; update it last.

## Tasks

Tasks are **GitHub issues** labelled `claude` or `kimi` (rules in AGENTS.md):

- Kimi: #230, landing page fixes (phone overflow on the install page, HACS instructions →
  `arborae/ha-d2ha`, Telegram notifications, small things).
- Claude: no open issue. Next steps on the owner's side are in "Next up" 2 and 3.

## State (2026-11-06, night)

- **v0.5.1 released** (changelog dated in PR #239):
  - OCI labels on the images (PR #229), so D2HA shows its own version and release notes;
  - Telegram alerts (PR #237): no crash alert for containers stopped on purpose, an
    "out of memory" line, and the opt-out label `d2ha.notify=false`;
  - HACS instructions point to `arborae/ha-d2ha` (PR #235).
  
  The `0.5.1` and `latest` images (amd64 + arm64) carry the labels.
- **Home Assistant integration:** `arborae/ha-d2ha` **v0.1.0** released (ha-d2ha#1),
  reviewed end to end on Home Assistant 2026.11. Its tests need Python 3.14 (the test
  package is pinned).
- The owner's server runs 0.5.1 with Telegram notifications on.
- `/api/v1` is unchanged since 0.5.0 (`API.md`).

## Next up

1. Landing page fixes (#230, Kimi).
2. Retire Diun and docker-telegram-notifier after a few days of overlap with the D2HA
   alerts (Claude, around 2026-11-10, after the owner's OK).
3. The owner's Home Assistant (Claude, with the owner):
   - install the integration from HACS;
   - move the dashboards to its entities;
   - remove the old "Docker …" helpers, the old custom component and the retained MQTT topics.
4. A full docs site is not wanted for now.

## Notes from the owner

- Do not change the MQTT password for now. Portainer stays.
- Version numbers and release timing are the owner's decision.
- The local working copy on the server belongs to Claude; Kimi works only through the
  GitHub API.
- Commits use the GitHub noreply address: check `git config user.email` in every fresh
  clone before the first commit.
- In `arborae/ha-d2ha` merged branches are not deleted automatically: delete them by
  hand after the merge.
- Do not merge one pull request's branch into another: GitHub then sees two merge bases
  and reports a conflict. Wait for the first merge, then merge `main` into the second.
