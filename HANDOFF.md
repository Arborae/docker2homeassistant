# Handoff

Shared state between the AI assistants working on this repository (see AGENTS.md,
"Multi-agent coordination"). Read this first; update it last.

## Tasks

Tasks are **GitHub issues** labelled `claude` or `kimi` (rules in AGENTS.md):

- **Released on 2026-11-10: app v0.5.3 and ha-d2ha 0.3.1** (prepared by #280 here and by
  ha-d2ha#17).
- **The ha-d2ha tag is fixed.** The release was created as `` `v0.3.1 ``, with a backtick
  in front: with the owner's OK Claude published it again as `v0.3.1` on the same commit
  and removed the wrong release and tag (2026-11-10).
- **Open: #281, fixed by #282 (Claude; Kimi reviews).** The first real updates from Home
  Assistant (2026-11-11) went well, with no `-d2ha-old` container listed (#278 works on a
  real Docker), except a container on the network of another container: Docker refuses
  the hostname D2HA sent, and D2HA put the old container back. #282 sends no `Hostname`,
  `Domainname` or `ExposedPorts` with the `container:` network mode.
- On 2026-11-10 Claude did Kimi's open issues too (#267, #268, #269,
  #270, #274: the owner's decision, written in the issues) and Kimi reviewed everything:
  seven pull requests, approved with no changes requested and merged the same evening
  (here #275, #276, #278, #279; in ha-d2ha #14, #15, #16). The Marketplace plan #256 is
  closed. `docs/` and `ha-d2ha` stay Kimi's area for new work.
- **Decided by the owner on 2026-11-10** (written in the issues):
  - #267: no binary sensor per container.
  - #268: no `d2ha.*` services. `update.install` and `button.press` do the same today, and
    the Automations page of the docs site explains them.
  - #270: automatic removal, always on, after 24 hours, with three protections: only the
    answers of D2HA count, an empty list removes nothing, the absence must be continuous.
  - #277: fixed in the app (#278).
- Claude: "What you get" and "Choose the containers" on the docs site (what 0.3.1 adds).
- Kimi: the review of #282.
- Lesson from the owner's server: a new config entry gives the containers new entity ids,
  so a card that picks them by an id pattern ends up empty. Pick them by the integration
  `d2ha` and the `key` attribute instead.

Both agents follow the "Code of conduct" in AGENTS.md (PR #242): every action has a level,
from free (read only) to forbidden, and the levels in between need the owner's explicit OK.

## State (2026-11-10)

- **App:** **v0.5.3 released** (2026-11-10, CHANGELOG in #280): the install instructions
  lead with the Marketplace search (#275), and the backup container of an update is no
  longer listed as a container of its own (#278). 0.5.2 changed only text: install
  instructions for Home Assistant's built-in Marketplace (2026.11), with HACS for 2026.8
  to 2026.10, in Settings and the README (PR #247 and #250).
- **Landing:** Marketplace instructions (PR #249) and the 2026.8 minimum (PR #251).
- **Docs site:** online since 2026-11-09 (PR #255): 14 plain HTML pages under `docs/guide/`
  and `docs/install/`, with `docs/css/docs.css` and `docs/js/docs.js`; no build step.
  `tests/test_docs_site.py` checks that every page has the same sidebar, previous/next in
  sidebar order, and local links, anchors and images that exist: when you add a page, add it
  to the sidebar of every page. The **Automations** page (#279, 2026-11-10) has examples
  ready to paste, each one tried on a test Home Assistant.
- **Integration `arborae/ha-d2ha`:** **0.3.1 released** (2026-11-10, tag `v0.3.1`) and
  listed in the Marketplace catalogue (the HACS default list) since
  2026-11-10:
  - 0.2.0: one device per container, the dashboard strategy `custom:d2ha`, minimum Home
    Assistant 2026.8;
  - 0.3.0: icons, diagnostics, reconfiguration, the fix for the dashboard that often did not
    load, the README with screenshots (ha-d2ha#7 to #13);
  - 0.3.1: the "Last check" sensor, the `created` and `check_error` attributes
    (ha-d2ha#15), the device of a container D2HA no longer lists removed after 24 hours
    (ha-d2ha#16), the README that leads with the Marketplace search (ha-d2ha#14).

  CI runs pytest and the node tests of the strategy, hassfest and the HACS validation.
- **The owner's server** runs D2HA 0.5.3 (updated and checked on 2026-11-10) with
  Telegram notifications on. Diun and docker-telegram-notifier were stopped on 2026-11-10:
  the alerts now come from D2HA only. Its Home Assistant runs the integration 0.3.1
  (checked on 2026-11-10) on a new config entry, added by the owner the same day: 15
  devices, 35 entities with the "Last check" sensor, no errors.
- `/api/v1` is unchanged since 0.5.0 (`API.md`).

## Next up

1. The review of #282 (Kimi), then the owner's merge.
2. The docs site: "What you get" and "Choose the containers" (Claude).

## Notes from the owner

- Do not change the MQTT password for now. Portainer stays.
- Version numbers and release timing are the owner's decision.
- Claude and Kimi each work in their own clones; neither works in the other's.
- Commits use the GitHub noreply address: check `git config user.email` in every fresh
  clone before the first commit.
- Both repositories delete a pull request's branch automatically after the merge.
- Do not merge one pull request's branch into another: GitHub then sees two merge bases
  and reports a conflict. Wait for the first merge, then merge `main` into the second.
