---
date: 2026-10-09
issue: 1736
impact: patch
title: 'ci: shorten the remaining long actions-ci commands'
---

Shellcheck runs one process per core, and the ref-encoding gate lexes each workflow slice once instead of once per pattern. A group command that exceeds `ACTIONS_CI_COMMAND_BUDGET_SECONDS` fails that group.

See #1736.
