---
date: 2026-10-09
issue: 1735
impact: patch
title: 'ci: keep passing contract fixtures out of the check annotations'
---

actions-ci masks workflow commands printed by a passing contract, so a green shell-test group no longer shows errors and warnings from fixtures that are supposed to fail inside the test.

See #1735.
