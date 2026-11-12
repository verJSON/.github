---
date: 2026-11-12
issue: 2031
impact: minor
title: Merge admission sets aside its own producer's check and binds an expected head
---

`scripts/assert-mergeable-head.sh` now sets aside, in Gates A and C, every
`merge-admission` check run produced by the AI review App (id 4528902) that is still in
progress or concluded success, and the
`merge-admission` required context only where the ruleset binds it to that App. A
same-named check from any other App, GitHub Actions included, a completed non-success
from the AI review App, or a same-named commit status stays a failure. An optional third argument names the head the caller answers
for; a pull request whose head moved exits 1 (ADR 0260, #1990, #2031).

`scripts/ci-gate/merge-admission.sh` performs one producer evaluation: it creates a new
`merge-admission` check run in progress on the exact head with the AI review App token,
refuses to continue unless GitHub reports that App as its producer, runs the canonical
assertion for that head with the read-only token, and completes the check as success
only when the assertion exits 0 printing exactly `true`. Every refusal or error leaves
the check in progress and exits 0 (ADR 0260 precondition (a)).

`scripts/ci-gate/merge-admission-plan.sh` lists the heads one producer run evaluates:
every open pull request that is not a draft, or only those at the head an event names.
An unreadable listing plans nothing and a malformed entry is skipped, each named in a
warning, so the producer's own job still succeeds.

The reusable producer `.github/workflows/merge-admission.yml` runs these from this
repository at its own executing SHA, one evaluation per head at a time without
cancellation, on the ephemeral lane, and only from `workflow_run`, `schedule`, or
`workflow_dispatch`, so its runs stay outside every head's inventories. This repository
adopts it through `merge-admission-caller.yml` (completion of each pull-request workflow
and of the keyless `merge-admission-trigger.yml`, plus a 15-minute sweep) and posts
`merge-admission` while no ruleset requires it. `app-key-environment.yml` reads a
scheduled run's default branch from its ref, since a schedule event carries no
repository record. ADR 0260 records the ARB-G1-2026-11-06 C2 decision; no ruleset is
created or enabled.
