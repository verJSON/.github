---
date: 2026-10-09
issue: 1733
impact: patch
title: 'ci: run changelog caller mutations concurrently'
---

The changelog-release actions-ci shard no longer regenerates an identical adopter for every rejection case, and independent mutations run together on the job's CPUs. That shard was the check that stayed in progress for about 25 minutes after a package release had already finished.

See #1733.
