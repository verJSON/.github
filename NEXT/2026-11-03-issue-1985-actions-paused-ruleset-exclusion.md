---
date: 2026-11-03
issue: 1985
impact: patch
title: Record the Actions-paused required-check exclusion
---
ADR 0252 records the owner's decision to exempt repositories with GitHub Actions paused from rulesets whose checks can only run under Actions.
- An explicit `verjson-actions-state` custom property records membership.
- The three required-check rulesets and `core-checks-node-floor` (24056850) exclude `paused` repositories.
- Twelve single-repository rulesets drop to `evaluate`.
- The stand-in ruleset `actions-paused-baseline-protection` (26466984) keeps pull requests required and blocks force-pushes and deletion.
- Each repository's protection is restored when its Actions is re-enabled.

The scheduled ruleset conformance audits now derive ADR 0252's paused image (amendment of 2026-11-04) instead of reporting the applied state as drift. `evaluate` is accepted only on the 12 recorded relaxations, and only while the targeted repository is `verjson-actions-state=paused`. The stand-in 26466984 must carry exactly the bypass actors of the reviewed `core-checks-node` contract, plus ADR 0252's pull-request, force-push and deletion rules. Any other drift is still an error.
