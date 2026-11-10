# Handoff

Shared state between the AI assistants working on this repository (see AGENTS.md,
"Multi-agent coordination"). Read this first; update it last.

## Tasks

Tasks are **GitHub issues** labelled `claude` or `kimi` (rules in AGENTS.md):

- **Plan #256: ha-d2ha is in the Marketplace catalogue** (hacs/default#13788 merged on
  2026-11-09; listed as "D2HA" v0.3.0 since 2026-11-10). The install instructions now lead
  with the search: #275 and ha-d2ha#14, both in review.
- **Change of hands (owner, 2026-11-10):** Claude took over all of Kimi's open issues
  (#267, #268, #269, #270, #274; labels moved to `claude`, written in the issues) and
  **Kimi reviews everything**. Kimi is available again since the evening of 2026-11-10 and
  has no open issue, only the reviews below. `docs/` and `ha-d2ha` stay Kimi's area for
  new work.
- **Kimi, to review: seven pull requests.**
  - Here:
    - #275: the install instructions lead with the Marketplace search (Settings en/it,
      README, the Integration page of the docs, a screenshot): #273 and part of #274;
    - #276: two rows in `API.md` (#267);
    - #278: bug #277, during an update the backup container `<name>-d2ha-old` was listed
      as a new container, also for Home Assistant;
    - #279: the new **Automations** page of the docs site. It closes #269 and #268.
  - In ha-d2ha:
    - #14: the README, as frenck asked (#274);
    - #15: the "Last check" sensor, the `created` and `check_error` attributes (#267);
    - #16: the device of a container D2HA no longer lists is removed after 24 hours (#270).
  - A good order: #275 and ha-d2ha#14 first (they can be merged at once), then #278, #276
    and ha-d2ha#15, then ha-d2ha#16 and #279. The three ha-d2ha branches merge together
    without conflicts (61 tests pass).
- **Decided by the owner on 2026-11-10** (written in the issues):
  - #267: no binary sensor per container. The issue is closed by hand after ha-d2ha#15.
  - #268: no `d2ha.*` services. `update.install` and `button.press` do the same today, and
    the Automations page explains them.
  - #270: automatic removal, always on, after 24 hours, with three protections: only the
    answers of D2HA count, an empty list removes nothing, the absence must be continuous.
  - #277: fixed in the app (#278).
- Claude: answers the reviews. #275 and #278 both add an "Unreleased" section to the
  CHANGELOG at the same place: after the first merge, `main` is merged into the other.
  After the next ha-d2ha release: "What you get" and "Choose the containers" on the docs
  site.
- Owner: merges after the reviews; closes #267 and #256 by hand; decides the next releases
  and their numbers (app: #275 and #278; ha-d2ha: #15 and #16).

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
  Telegram notifications on. Diun and docker-telegram-notifier were stopped on 2026-11-10:
  the alerts now come from D2HA only. Its Home Assistant
  runs the integration v0.3.0 (checked on 2026-11-09): the entity ids were kept, 15
  devices, no errors.
- `/api/v1` is unchanged since 0.5.0 (`API.md`).

## Next up

1. Reviews and merges of the seven pull requests (see "Tasks"); then the releases, when the
   owner decides.
2. After the next ha-d2ha release: the docs site ("What you get", "Choose the containers")
   and a check of the owner's Home Assistant.

## Notes from the owner

- Do not change the MQTT password for now. Portainer stays.
- Version numbers and release timing are the owner's decision.
- Claude and Kimi each work in their own clones; neither works in the other's.
- Commits use the GitHub noreply address: check `git config user.email` in every fresh
  clone before the first commit.
- Both repositories delete a pull request's branch automatically after the merge.
- Do not merge one pull request's branch into another: GitHub then sees two merge bases
  and reports a conflict. Wait for the first merge, then merge `main` into the second.
