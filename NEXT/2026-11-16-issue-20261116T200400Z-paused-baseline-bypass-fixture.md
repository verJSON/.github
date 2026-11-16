---
date: 2026-11-16
id: 20261116T200400Z
refs: 2143
impact: patch
title: Include merge-admission in the paused-baseline fixture inventory
---

After #2143 pinned `merge-admission-required` (27387670) into the live bypass-contract policy, the paused-baseline org-ruleset conformance fixture still omitted that ruleset from its inventory and failed closed as a stale contract on every platform lane. Include the canary merge-admission ruleset beside `core-checks-node` in that fixture so platform CI matches the pinned policy.

Part of restoring platform CI after #2143; unblocks #2144.
