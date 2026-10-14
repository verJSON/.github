---
date: 2026-10-13
id: 20261013T160000Z
refs: 1696
impact: minor
title: Bootstrap stale default-branch AI review callers through the authorization arm
---
The organization authorization arm now completes the exact-head `AI review authorization` check itself for the one pull request a stale review caller cannot authorize: the byte-exact generated `ai-review-merge.yml` update from a revision that predates the `verJSON` casing repair (`65529002`) to a canonical `main` commit at or after the minimum safe review revision (`12098a0e`, the #1650 exact-head finalizer fix). Every other change, and every failed or unavailable proof, keeps the ordinary dispatch.

The proof reads the two exact trees and admits only the generated caller, contract-test mirrors that swap nothing but the stale pin and caller digest, and one fragment from the new `scripts/gen-ai-review-caller-bootstrap-fragment.sh`. It runs again before granting. Receipt authority can only narrow the outcome. Under `ai-approve` or `ai-merge` the App approves the exact head and the check records approval-only `ai-approve` authority. Otherwise the human path applies. No terminal promotion is dispatched. The organization ruleset must be rotated to this change's merge SHA before consumers can use it. See [ADR 0227](docs/decisions/0227-arm-bootstraps-stale-review-callers/README.md).
