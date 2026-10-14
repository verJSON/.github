---
date: 2026-10-14
issue: 1763
impact: patch
title: Bound the authorization arm's wait for a runner
---

The authorization arm (`gate-rearm.yml`) now runs a `runner-acquisition-watchdog` job with no environment or repository secret. The job runs, and its write-scoped token is issued, only when the untrusted lane resolves to the ephemeral GitHub-hosted runner. When any arm job stays queued without a runner for 15 minutes, the watchdog names the job in a failed check and cancels the run, which releases the pull request's arm concurrency group. It force-cancels only if that job survives the cancellation. Before this change, a job GitHub accepted but never dispatched held the required arm check, and every later arm for the pull request, pending indefinitely (verjson-payments run 37352915856).

Routing is unchanged: the evidence shows the repository was admitted to the lane, and the job was lost after acceptance. ADR 0230 records the decision and its trust boundary. It takes effect after organization ruleset 20722935 rotates to the merge SHA. `scripts/ci-gate/arm-runner-acquisition-watchdog.test.py` executes the real steps against stubs.
