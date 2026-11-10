---
date: 2026-11-09
issue: 1941
impact: patch
title: Reconcile the manual release proposal with current contracts
---

Reconcile the draft fragment-derived release proposal with current canonical
workflows and regenerate the ADR index. Preserve specific invalid-version rejection
under the C locale while allowing the proposed empty-version derivation, and
refresh the recorded Actions availability without claiming live release authority.

Regenerate five stale runner-isolation backlog digests from the checker's report,
following ADR 0235's content-binding and row-contention rules. The capability-floor
observer, changelog release, release-App canary, release dispatch, and release
verification bind the proposal's changed workflows, changelog CLI, and caller
generator. Only digest columns change; frozen keys, unresolved reasons, runner
selectors, refusal checks, and pending justifications remain unchanged.

PR #1941 remains a proposal: issuer-backed release-manager enforcement and the
remote expected-head guard evidence stay with #1940 and #1942.
