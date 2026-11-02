---
date: 2026-11-02
issue: 1975
impact: patch
title: Emitted contract suite reports guard diagnostics instead of aborting silently
---
The generated `changelog-contract.test.sh` no longer aborts under `pipefail` when a guard's `grep` capture matches nothing. Eight captures (the release-verification ordering lines and the private build matrix selectors and indices) now fall through to their existing guards. An adopter missing package-metadata preparation, for example, now sees the #550 diagnostic instead of a red check with no reason. Rejection outcomes are unchanged. A static check keeps any new capture from reintroducing the silent abort.
