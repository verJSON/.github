---
date: 2026-10-19
id: c0a7d1e9
impact: patch
title: Run actions-ci for changes to the pinned deployment CLI contract package
---

`actions-ci` now runs for pull requests and pushes that change `contracts/**`. The container deployment contract test reads the pinned deployment CLI package there, but a Renovate bump of that package never triggered the suite, so it could neither run the test nor report the required `shell-tests` check. `trigger-scope.test.sh` now fails if any tracked file under `contracts/` is not covered by a path trigger.
