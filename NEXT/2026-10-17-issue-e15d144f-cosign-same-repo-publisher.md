---
date: 2026-10-17
id: e15d144f
impact: patch
title: Accept the contract canary's same-repository Cosign publisher only when bound to the source commit
---

Fix: the container-candidate contract canary can sign provenance again. It calls the
publisher with `uses: ./`, so its GitHub OIDC `job_workflow_ref` ends in
`@refs/heads/main`, and the #1690 signer rejected every such ref as "not immutably
pinned" (run 39060824097). The producer and the downstream statement verifier now share
one predicate: a cross-repository publisher still needs `@<job_workflow_sha>`, while a
same-repository publisher is accepted only when its strictly parsed repository path
equals the source repository exactly ([ADR 0216](../docs/decisions/0216-keyless-container-provenance-is-verified-per-registry/README.md)).
