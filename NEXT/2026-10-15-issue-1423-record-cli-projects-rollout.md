---
date: 2026-10-15
issue: 1423
impact: patch
title: CLI projects ruleset rollout records the live previous workflow revision
---

Org ruleset 21830367 (`cli-projects-package-surface-required-workflow`) was rotated on 2026-09-30 to workflow revision `ae70e4ef` (#1666). The rollout record in `config/cli-projects-package-surface-ruleset.json` and the matching check in `scripts/cli-projects-package-surface-ruleset.py` still named `ebe574b6`. As a result, the rotation tool refused every dry run with "canonical organization ruleset differs from reviewed active and disabled images". Both now record `ae70e4ef` as the previous active revision, so the tool can rotate the ruleset to the #1823 fix (`64be2682`). That fix restores admission for CLI projects pull requests after the organization's display-case rename. The human gate and acknowledgement token are unchanged.
