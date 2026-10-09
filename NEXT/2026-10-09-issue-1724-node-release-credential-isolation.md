---
date: 2026-10-09
issue: 1724
impact: patch
title: Isolate Node release registry credentials from lifecycle scripts
---

The canonical Node release workflow now confines credentialed acquisition, lifecycle scripts, metadata preparation, version stamping, build, and packing to a read-only preparation job. A fresh GitHub-hosted publisher validates the transferred archives against package identities and versions read from the immutable tag, checks archive digests and paths, then publishes with scripts disabled. The publisher and retention jobs cannot reuse a self-hosted preparation runner. Contract tests cover credential boundaries, artifact tampering, and real npm pack output.
