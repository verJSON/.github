---
date: 2026-10-09
issue: 1724
impact: patch
title: Isolate Node release registry credentials from lifecycle scripts
---

The canonical Node release workflow acquires locked packages with lifecycle scripts disabled and the registry credential scoped to that command, then repeats `npm ci --prefer-offline` after removing the token. The second install lets npm run its normal dependency and root lifecycle; an offline contract fixture compares hook counts and per-package order with a normal install and verifies hooks cannot observe the credential. Empty dependency trees remain supported.
