---
date: 2026-11-02
id: 20261102T191402Z
refs: 1966
impact: patch
title: Reap an unreleased trusted launcher through its pinned pidfd
---

When gate authentication fails after the launcher pidfd is pinned, reap the
launcher through that pidfd (killing it through the same pidfd if it ignores
its closed gate) instead of a numeric-PID wait. Report cleanup of an
interruption before identity verification as unverified, never retry a failed
status-pipe close on a reusable descriptor number, and name the Linux 6.9
`PIDFD_SIGNAL_PROCESS_GROUP` requirement when an older kernel rejects group
signaling. Part of #1966.
