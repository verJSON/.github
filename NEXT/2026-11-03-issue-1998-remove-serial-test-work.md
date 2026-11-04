---
date: 2026-11-03
issue: 1998
impact: patch
title: Remove self-inflicted serial work from actions-ci test rows
---
Actions-ci test rows no longer repeat work they already did: the runner-admission reconcile suite runs the reconciler once per case instead of twice, and the zero-provider recovery suite records its retry backoff instead of sleeping through it.

- `scripts/ci-gate/runner-admission-reconcile.test.sh` records each case's exit status from the run whose output it already captured, instead of running the reconciler again to read it, and failure messages no longer rerun it either: a passing run executes the reconciler 76 times instead of 131. Every assertion label is unchanged (102 before and after); the row took 86.0s alone locally before and 48.8s after.
- `scripts/ci-gate/zero-provider-recovery.test.sh` stubs `sleep` through a PATH shim and asserts that a permanently absent run listing backs off with exactly four `sleep 3` calls; removing or resizing the backoff now fails the suite, where it used to pass. A duplicate case whose two assertions the bounded-poll case already makes is removed. The row took 33.0s alone locally before and 5.9s after.
