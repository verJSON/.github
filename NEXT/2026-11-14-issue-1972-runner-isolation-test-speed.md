---
date: 2026-11-14
issue: 1972
title: Speed the public PR runner-isolation behavioral suite
impact: patch
---

The public PR runner-isolation suite calls the checker in-process (with
path-keyed cache clears), gives probe shards a lean release.yml verify
fixture instead of rebuilding the live suite, and skips replaying those
live fixture assignments into probe parts. Subprocess coverage remains for
exit codes 0/1/2. Local probe shards drop from ~130s each to ~15s; core
drops from ~246s to ~70s on a clean tree. Hosted platform run 43669973375
measured core at 63s and probes at 13s/11s; ADR 0225 sets the core budget
to 150 (twice hosted, rounded up) and keeps probes on the 90s floor.

Part of the legacy-CI speed work toward faster trunk feedback.
