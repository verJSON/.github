---
date: 2026-10-17
id: e15d144f
impact: patch
title: Bind the contract canary's same-repository Cosign publisher to its source commit
---

Fix: the container-candidate contract canary can sign and verify its own provenance
again. It calls the publisher with `uses: ./`, so its GitHub OIDC `job_workflow_ref`
ends in `@refs/heads/main`. Since #1690, the signer had rejected every such ref as "not
immutably pinned" (run 39060824097). Adopters, which call `Verjson/.github/...@<sha>`,
see no behavior change.

The Cosign producer and the statement verifier now share one strict predicate. A
publisher pinned at `@<job_workflow_sha>` is accepted as before. A same-repository
publisher is accepted only at the canonical publisher path, when its parsed repository
path and ref exactly equal the source repository and ref, and when
`job_workflow_sha == workflow_sha == sha`. The
publisher's `prepare` job authorizes one `publisher-workflow-ref` that the retry, GAR
mirror, and candidate-manifest steps all read. The candidate validator accepts that signer only
for the contract repository itself, and the canary fixture reviews that identity.
Release attestation verification still requires the pinned contract, the candidate validator
full-matches the pinned publisher, and promotion now splits on the first `@` and requires
the exact publisher path and a 40-hex contract commit (it previously promoted
`Verjson/.github/...@refs/heads/main` and `...@refs/heads/main@<sha>` candidates), so canary candidates are never promoted
([ADR 0216](../docs/decisions/0216-keyless-container-provenance-is-verified-per-registry/README.md)
amendment of 2026-10-17).
