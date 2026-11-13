---
date: 2026-11-13
issue: 1906
impact: patch
title: Install Node for package-less release-snapshot verification hooks
---

Generated release-snapshot callers install configured Node for package-less `scripts/release-verify.sh` hooks, and the contract fails closed when its configured Node version is missing or mismatched. Dependency acquisition, installation, and version stamping still run only when the release tree has a root `package.json`; package-less callers without a verification hook still fail before the snapshot.

The generated contract test supplies the generator's declared Node version and mutation-checks both missing and incorrect values. It runs each release caller's verify job against a package-less tree, requires the hook to see configured Node, resolves the suite step's own `env:`, and rejects expressions it cannot resolve. The host running the contract test needs executable `/usr/bin/python3`, which the generated runtime capture step invokes. ADR 0220 records the re-recorded release-snapshot pre-credential digest. Release-node and release-artifact callers remain byte-identical to their previous output.
