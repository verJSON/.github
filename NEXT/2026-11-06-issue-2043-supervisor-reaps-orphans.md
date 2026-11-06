---
date: 2026-11-06
issue: 2043
impact: patch
title: Reap orphaned sandbox processes while the candidate runs
---

The trusted script-plan supervisor, which runs as Bubblewrap PID 1, now reaps adopted orphans continuously while the candidate runs instead of only at cleanup, so a candidate that kills a process tree and waits for its process group to disappear no longer times out on zombies left behind by PID 1. This fixes `ci / build-test` for v4.4.0 adopters such as Verjson/verjson-ai, whose consumer cleanup checks failed on every pull request.

The reaper peeks with `waitid(P_ALL, WEXITED | WNOHANG | WNOWAIT)` and reaps only the specific exited pid, leaving an unreaped candidate's status for its `Popen` object. A bare `waitpid(-1)` would consume that status and make `Popen` report exit code 0, turning a failing test into a pass. `scripts/ci-gate/ci-entry-receipts.test.py` covers both behaviors with real-Bubblewrap PID 1 runs and a deterministic test that the reaper leaves an exited candidate's status to `Popen`, and [ADR 0238](../docs/decisions/0238-node-ci-cost-levers/README.md) records the amendment. Adopters need a contract release and regeneration to pick it up.
