---
date: 2026-11-13
issue: 1906
impact: patch
title: Install Node for package-less release-snapshot verification hooks
---

Generated release-snapshot callers install the configured Node before running package-less `scripts/release-verify.sh` hooks. The contract fails closed when the configured Node version or release mode is missing or mismatched, and package-less trees without a verification hook still fail before the snapshot. Dependency acquisition, installation, and version stamping remain guarded by the root `package.json` check.

The generated contract test supplies the caller's release mode and configured Node version and statically models package-less release-snapshot PATH and environment only when the setup-node action, modeled conditions, runtime and verification scripts, and verification environment match their pinned forms. It never executes caller-provided shell or environment values. Mutation tests cover missing and incorrect Node versions, altered run bodies, `BASH_ENV`, leaked host credentials, and absolute or traversal `hashFiles()` paths. The static check does not require `/usr/bin/python3` on its host; the actual release runner still uses that interpreter for runtime capture. ADR 0220 records the re-recorded release-snapshot pre-credential digest. Release-node and release-artifact callers remain byte-identical to their previous output.
