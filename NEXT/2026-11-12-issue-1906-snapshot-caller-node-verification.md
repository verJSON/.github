---
date: 2026-11-12
issue: 1906
impact: patch
title: Install Node for package-less release-snapshot verification hooks
---

Generated release-snapshot callers now install the configured Node version whether or not the release tree has a root `package.json`, so a package-less adopter's `scripts/release-verify.sh` can run on the pinned Node. Dependency acquisition, installation and version stamping still run only when `package.json` exists, and a package-less caller without a verification hook still fails before the snapshot.

ADR 0220 records the re-recorded release-snapshot pre-credential digest. Release-node and release-artifact callers are byte-identical to their previous output.
