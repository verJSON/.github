---
date: 2026-11-16
issue: 2031
impact: patch
title: Alert when the merge-admission producer stops succeeding
---

The merge-admission producer now has an outage monitor. A default-branch workflow fails when a producer run fails, when a cancelled run started a job, when an evaluation logs `was not evaluated`, when no successful evaluate completes for 45 minutes while a non-draft pull request is open, or when the producer workflow is not active. A cancelled run that never started a job stays quiet, so displaced pending concurrency cancellations do not page.

The delivery channel is that failing workflow run, owned by the Verjson/.github repository owner. An Actions-wide outage or a billing limit that stops the monitor is the accepted remainder of the C1 outage window (ADR 0260, ARB-2031 C10 (ii)). The monitor does not create or edit a ruleset. The REST budget, caller generator, runbook, hosted-minute budget, C9 principal, and C8 enrollment-scope check still gate the canary ruleset.
The monitor checkout path is unique per run, attempt, and job, and is removed with `if: always()` so repository checkout isolation stays green.
