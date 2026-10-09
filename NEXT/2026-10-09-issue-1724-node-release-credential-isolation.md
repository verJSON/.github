---
date: 2026-10-09
issue: 1724
impact: patch
title: Isolate Node release registry credentials from lifecycle scripts
---

The canonical Node release workflow now confines credentialed acquisition, lifecycle scripts, metadata preparation, version stamping, build, and packing to a read-only preparation job. A fresh GitHub-hosted publisher validates the transferred archives against package identities and versions read from the immutable tag, checks archive digests and paths, then publishes with scripts disabled. The publisher and retention jobs cannot reuse a self-hosted preparation runner. Contract tests cover credential boundaries, artifact tampering, and real npm pack output. The publisher checks the exact prepared artifact ID and its 2 GiB size before download, including failed-job reruns; upload compression is disabled for already-compressed tarballs so that limit bounds extracted data; archive validation caps aggregate bytes and rejects oversized tar metadata before parsing.
