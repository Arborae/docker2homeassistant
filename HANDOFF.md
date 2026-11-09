# Handoff

Shared state between the AI assistants working on this repository (see AGENTS.md,
"Multi-agent coordination"). Read this first; update it last.

## Tasks

Tasks are **GitHub issues** labelled `claude` or `kimi` (rules in AGENTS.md):

- **Plan #256: ha-d2ha in the Marketplace** (HACS default): **v0.3.0 released** (2026-11-09) and
  the request is open, hacs/default#13788, from the owner's personal account. After the
  inclusion: install instructions become "search D2HA in the Marketplace" (Claude: the app's
  Settings and README; Kimi: the ha-d2ha README, the landing and the docs site).
- Kimi: docs #263 (diagnostics and reconfigure in the docs site), now that v0.3.0 is out.
  Also: the owner merged PR #255 (the docs site in `docs/`, your area) before your review:
  please read the new pages and open an issue or a pull request for anything you would change.
- Claude: reviews of Kimi's pull requests, then the text of the `hacs/default` pull request
  (#261 screenshots: done).
- Owner: decides on removing by themselves the devices of containers that D2HA no longer lists
  (#256), publishes v0.3.0, opens the `hacs/default` pull request (or gives Claude the OK).

Both agents follow the "Code of conduct" in AGENTS.md (PR #242): every action has a level,
from free (read only) to forbidden, and the levels in between need the owner's explicit OK.

## State (2026-11-09)

- **App:** **v0.5.2 released** (CHANGELOG in PR #253). It changes only text: install
  instructions for Home Assistant's built-in Marketplace (2026.11), with HACS for 2026.8
  to 2026.10, in Settings and the README (PR #247 and #250). Nothing is "Unreleased".
- **Landing:** Marketplace instructions (PR #249) and the 2026.8 minimum (PR #251).
- **Docs site:** online since 2026-11-09 (PR #255): 13 plain HTML pages under `docs/guide/`
  and `docs/install/`, with `docs/css/docs.css` and `docs/js/docs.js`; no build step.
  `tests/test_docs_site.py` checks that every page has the same sidebar, previous/next in
  sidebar order, and local links, anchors and images that exist: when you add a page, add it
  to the sidebar of every page.
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
2. ha-d2ha in the Marketplace: plan #256 (v0.3.0, release, `hacs/default`, then the install
   instructions everywhere become "search D2HA in the Marketplace").
3. Kimi reads the docs site (merged without its review).

## Notes from the owner

- Do not change the MQTT password for now. Portainer stays.
- Version numbers and release timing are the owner's decision.
- Claude and Kimi each work in their own clones; neither works in the other's.
- Commits use the GitHub noreply address: check `git config user.email` in every fresh
  clone before the first commit.
- Both repositories delete a pull request's branch automatically after the merge.
- Do not merge one pull request's branch into another: GitHub then sees two merge bases
  and reports a conflict. Wait for the first merge, then merge `main` into the second.
