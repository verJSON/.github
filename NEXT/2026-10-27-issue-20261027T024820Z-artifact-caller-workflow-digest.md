---
date: 2026-10-27
id: 20261027T024820Z
refs: 1869
impact: patch
title: Bind ArtifactSet authorization to caller workflow bytes
---
ArtifactSet preflight and producer or consumer authorization now require the approved caller workflow path and SHA-256 digest, read from the workflow ref and immutable workflow revision. Preflight selects independent lane plans when the caller path or bytes mismatch; producer and consumer reauthorization fails closed with that reason.

Regression coverage checks producer and consumer authorization, immutable workflow bytes, and branch refs with `@` in the branch name. The trusted policy table remains empty pending the local-reusable-workflow, policy/pin-cycle, schema-version, and aggregate-protection enrollment gates documented in ADR 0238.
