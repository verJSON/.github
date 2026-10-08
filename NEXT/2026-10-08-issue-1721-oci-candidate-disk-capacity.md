---
date: 2026-10-08
issue: 1721
impact: patch
title: Reduce OCI candidate publisher peak disk use
---

OCI candidate builds can opt into safe cleanup of unused preloaded images and retain disk, Docker, and BuildKit usage diagnostics. Private Node dependency archives now restore through authenticated streaming extraction, reducing peak disk use while preserving publication and provenance behavior.

The cleanup setting defaults off and only removes pre-job image IDs unused by existing containers on GitHub-hosted runners. No global image pruning is used. Signing remains deferred.
