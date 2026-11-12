---
date: 2026-11-12
issue: 1906
impact: patch
title: Install Node for package-less release-snapshot verification hooks
---

Generated release-snapshot callers now install the configured Node version whether or not the release tree has a root `package.json`, so a package-less adopter's `scripts/release-verify.sh` can run on the pinned Node. Dependency acquisition, installation and version stamping still run only when `package.json` exists, and a package-less caller without a verification hook still fails before the snapshot.

The generated contract test now runs every release caller's verify job on a package-less tree with a Node-based hook and requires the hook to see the configured Node version. It runs the suite with that step's own `env:`, refusing any expression it cannot resolve, and requires the generated package-less refusal rather than any failure when the hook is absent. The host running the contract test needs an executable `/usr/bin/python3`, which the generated runtime capture step invokes. ADR 0220 records the re-recorded release-snapshot pre-credential digest. Release-node and release-artifact callers are byte-identical to their previous output.
