# Handoff

Shared state between the AI assistants working on this repository (see AGENTS.md,
"Multi-agent coordination"). Read this first; update it last.

## Tasks

Tasks are **GitHub issues** labelled `claude` or `kimi` (rules in AGENTS.md):

- **Plan #256: ha-d2ha is in the Marketplace catalogue** (hacs/default#13788 merged on
  2026-11-09; listed as "D2HA" v0.3.0 since 2026-11-10). The install instructions now lead
  with the search: #275 (app Settings, README, docs page, screenshot: #273 and the docs part
  of #274) and ha-d2ha#14 (the README, as frenck asked: #274). Both are open and can be
  merged after review.
- **Change of hands (owner, 2026-11-10):** Kimi is only partly available, so this time
  Claude does the work and **Kimi reviews**. Claude took over #274 and #267 (labels moved to
  `claude`, written in the issues).
- Kimi, to review: #275 and #276 here, ha-d2ha#14 and ha-d2ha#15. Still Kimi's, no hurry:
  #268 (services), #269 (an automations page, after #267 and #268) and the technical proposal
  for #270 (the owner decides). Also still open: reading the docs site merged in PR #255.
- Claude: #267 is in ha-d2ha#15 (the "Last check" sensor, `created` and `check_error`
  attributes) and #276 (the two rows in `API.md`). The binary sensor of #267 is left out, the
  `update` entity is already on/off: the owner says yes or no in the issue. After the next
  ha-d2ha release: "What you get" on the docs site.
- Owner: merges after the reviews; decides on the binary sensor (#267), on #270, and on the
  next releases (app: the Settings text of #275; ha-d2ha: #15).

Both agents follow the "Code of conduct" in AGENTS.md (PR #242): every action has a level,
from free (read only) to forbidden, and the levels in between need the owner's explicit OK.

## State (2026-11-10)

- **App:** **v0.5.2 released** (CHANGELOG in PR #253). It changes only text: install
  instructions for Home Assistant's built-in Marketplace (2026.11), with HACS for 2026.8
  to 2026.10, in Settings and the README (PR #247 and #250). Nothing is "Unreleased".
- **Landing:** Marketplace instructions (PR #249) and the 2026.8 minimum (PR #251).
- **Docs site:** online since 2026-11-09 (PR #255): 13 plain HTML pages under `docs/guide/`
  and `docs/install/`, with `docs/css/docs.css` and `docs/js/docs.js`; no build step.
  `tests/test_docs_site.py` checks that every page has the same sidebar, previous/next in
  sidebar order, and local links, anchors and images that exist: when you add a page, add it
  to the sidebar of every page.
- **Integration `arborae/ha-d2ha`:** **v0.3.0 released** (2026-11-09) and listed in the
  Marketplace catalogue (the HACS default list) since 2026-11-10:
  - 0.2.0: one device per container, the dashboard strategy `custom:d2ha`, minimum Home
    Assistant 2026.8;
  - 0.3.0: icons, diagnostics, reconfiguration, the fix for the dashboard that often did not
    load, the README with screenshots (ha-d2ha#7 to #13).

  CI runs pytest and the node tests of the strategy, hassfest and the HACS validation.
- **The owner's server** runs D2HA 0.5.2 (updated and checked on 2026-11-09) with
  Telegram notifications on. Its Home Assistant
  runs the integration v0.3.0 (checked on 2026-11-09): the entity ids were kept, 15
  devices, no errors.
- `/api/v1` is unchanged since 0.5.0 (`API.md`).

## Next up

1. Retire Diun and docker-telegram-notifier after a few days of overlap with the D2HA
   alerts (Claude, around 2026-11-10, after the owner's OK).
2. Reviews and merges of #275, #276, ha-d2ha#14 and ha-d2ha#15 (see "Tasks"); then the
   releases, when the owner decides.
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
