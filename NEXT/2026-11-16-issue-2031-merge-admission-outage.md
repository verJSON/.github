---
date: 2026-11-16
issue: 2031
impact: minor
title: Land merge-admission outage alert and canary gates
---

Ship the ADR 0260 outage monitor and the remaining canary code gates (REST budget
fit, consumer caller/trigger generator, runbook with C9 principal `lightningleap`,
hosted-minute ceiling, and C8 enrollment-scope audit) so `merge-admission-required`
can be created and Decision 7 canaries can close #2031.

The merge-admission producer now has an outage monitor. A default-branch workflow fails
when a producer run fails, when a cancelled run started a job, when an evaluation logs
`was not evaluated`, when no successful evaluate completes for 45 minutes while a
non-draft pull request is open, or when the producer workflow is not active. A cancelled
run that never started a job stays quiet, so displaced pending concurrency cancellations
do not page. The delivery channel is that failing workflow run, owned by the
Verjson/.github repository owner. An Actions-wide outage or a billing limit that stops
the monitor is the accepted remainder of the C1 outage window (ADR 0260, ARB-2031
C10 (ii)). The monitor checkout path is unique per run, attempt, and job; job
`defaults.run.working-directory` matches that path; and the path is removed with
`if: always()` so repository checkout isolation stays green.

`scripts/gen-merge-admission-caller.sh` emits consumer callers pinned to
`Verjson/.github/.github/workflows/merge-admission.yml@<sha>`; this repository keeps
its relative-path producer caller.

Org ruleset `merge-admission-required` (**27387670**) is created on canary
`repository_id` 1269388380, requiring `merge-admission` bound to App 4528902, and
enabled after the full conformance receipt. The bypass contract pins
`ruleset_id` 27387670.
