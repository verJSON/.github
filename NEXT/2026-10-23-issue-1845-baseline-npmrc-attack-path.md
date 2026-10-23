---
date: 2026-10-23
issue: 1845
impact: patch
title: Assert the credentialed baseline reaches the dependency .npmrc attack path
---

`scripts/node-release-credential-isolation.test.py` now asserts, in its unprotected baseline, that no project, user, global or exported-registry setting names the attacker registry and that the credentialed request is the git dependency's own `bait` resolution, so the baseline provably exercises the dependency `.npmrc` path rather than passing through a higher-precedence npm config (#1845).
