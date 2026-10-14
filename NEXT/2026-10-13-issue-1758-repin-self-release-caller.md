---
date: 2026-10-13
issue: 1758
impact: patch
title: Repin this repository's release caller and repair its verification hook
---

This repository's own release caller is regenerated at contract `21eb48e713bd8304951fb7b26a900d08359af900`, so its inline Python interpreters now start isolated (#1752), its publish job grants `actions: read` (#1748), and its verify job picks up the credential-free install and clean-environment verification already shipped to adopters. `scripts/release-verify.sh` again finds the generated header; it had kept the retired `Verjson/.github` spelling, so every self-release since #1702 would have failed at `verify`. It now matches the owner without regard to case, so a future casing change cannot break it the same way, while the byte-for-byte comparison with the pinned generator still rejects any header the generator did not emit.

The four temporary `release.yml` exceptions in `scripts/ci-gate/workflow-isolated-python.test.py` are removed, leaving the allowlist empty; a fixture self-test keeps its suppression and stale-entry guard exercised. `scripts/release-verify.test.sh` runs the hook in a disposable repository against the real generator, covering a matching caller, a generator that emits another owner casing, a hand-edited caller, the retired spelling, an abbreviated pin, an empty dispatched version, and a failing verification command.
