---
date: 2026-11-18
issue: 2228
impact: patch
title: Bind deployment release.repository to historical signed manifest source
---

After the verjson-github-runner → verjson-git-runners rename, host observation and
asset download use the live API identity while attestation verify --repo must keep
the historical signed source from the release manifest. The controller now splits
those fields the same way the transport rename test already required.

Fixes #2228.
