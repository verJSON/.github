---
date: 2026-11-12
issue: 2031
impact: minor
title: Merge admission sets aside its own producer's check and binds an expected head
---

`scripts/assert-mergeable-head.sh` now sets aside, in Gates A and C, every
`merge-admission` check run produced by the AI review App (id 4528902), and the
`merge-admission` required context only where the ruleset binds it to that App. A
same-named check from any other App, GitHub Actions included, or a same-named commit
status stays a failure. An optional third argument names the head the caller answers
for; a pull request whose head moved exits 1 (ADR 0260, #1990, #2031).

`scripts/ci-gate/merge-admission.sh` performs one producer evaluation: it creates a new
`merge-admission` check run in progress on the exact head with the AI review App token,
refuses to continue unless GitHub reports that App as its producer, runs the canonical
assertion for that head with the read-only token, and completes the check as success
only when the assertion exits 0 printing exactly `true`. Every refusal or error leaves
the check in progress and exits 0 (ADR 0260 precondition (a)).
