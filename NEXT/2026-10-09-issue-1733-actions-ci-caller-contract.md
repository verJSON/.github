---
date: 2026-10-09
issue: 1733
impact: patch
title: 'ci: shard the changelog-release actions-ci group'
---

The changelog-release actions-ci group is four matrix cells. The caller-contract suite is partitioned across them by case, and each case still runs the whole generated contract. That group was the check that stayed in progress for about 25 minutes after a package release had already finished.

Independent mutations still run together on the job's CPUs. See #1733 and ADR 0224. The next actions-ci run records the wall-clock change.
