# Handoff

Shared state between the AI assistants working on this repository (see AGENTS.md,
"Multi-agent coordination"). Read this first; update it last.

## Tasks

Tasks are **GitHub issues** labelled `claude` or `kimi` (rules in AGENTS.md):

- Kimi: no open issue. **Please review PR #255** (the docs site in `docs/`, your area) when
  you are back: the owner wants your review before the merge.
- Claude: #254, the docs site, in PR #255.

Both agents follow the "Code of conduct" in AGENTS.md (PR #242): every action has a level,
from free (read only) to forbidden, and the levels in between need the owner's explicit OK.

## State (2026-11-09)

- **App:** **v0.5.2 released** (CHANGELOG in PR #253). It changes only text: install
  instructions for Home Assistant's built-in Marketplace (2026.11), with HACS for 2026.8
  to 2026.10, in Settings and the README (PR #247 and #250). Nothing is "Unreleased".
- **Landing:** Marketplace instructions (PR #249) and the 2026.8 minimum (PR #251).
- **Integration `arborae/ha-d2ha`:** **v0.2.0 released**:
  - one device per container, `title` on the update entities (ha-d2ha#3);
  - the dashboard strategy `custom:d2ha` (ha-d2ha#5);
  - Marketplace README (ha-d2ha#4);
  - minimum Home Assistant 2026.8;
  - `manifest.json` at 0.2.0 (ha-d2ha#6).

  CI runs pytest and the node tests of the strategy.
- **The owner's server** runs D2HA 0.5.2 (updated and checked on 2026-11-09) with
  Telegram notifications on. Its Home Assistant
  runs the integration v0.2.0: the entity ids were kept, 15 devices, no errors.
- `/api/v1` is unchanged since 0.5.0 (`API.md`).

## Next up

1. Retire Diun and docker-telegram-notifier after a few days of overlap with the D2HA
   alerts (Claude, around 2026-11-10, after the owner's OK).
2. A docs site, one page per topic, as an extension of the landing in `docs/` (the
   owner's decision, 2026-11-09). Kimi is unavailable for now, so the owner asked Claude in
   writing to build it: issue #254, PR #255 (13 plain HTML pages under `guide/` and
   `install/`, `docs/css/docs.css`, `docs/js/docs.js`, `tests/test_docs_site.py`). Kimi
   reviews it before the merge; afterwards Kimi owns `docs/` again, pages included.

## Notes from the owner

- Do not change the MQTT password for now. Portainer stays.
- Version numbers and release timing are the owner's decision.
- Claude and Kimi each work in their own clones; neither works in the other's.
- Commits use the GitHub noreply address: check `git config user.email` in every fresh
  clone before the first commit.
- Both repositories delete a pull request's branch automatically after the merge.
- Do not merge one pull request's branch into another: GitHub then sees two merge bases
  and reports a conflict. Wait for the first merge, then merge `main` into the second.
