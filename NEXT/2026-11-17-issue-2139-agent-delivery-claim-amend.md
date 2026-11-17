---
date: 2026-11-17
issue: 2139
impact: minor
title: Record Renovate handoff as agent delivery claim in ADR 0243
---

Amends ADR 0243 for #2139 (ARB `arb-2139-2026-11-17`, slate digest
`9fee6d34a4ef9c4140a899064fd9bfdc82ccddd611eac6c785dbf52b84b7aff9`): the durable
Renovate handoff is an idempotent agent delivery claim per
`(repo, pr, head_sha)`, not a restored privileged `NEXT/` writer. Keeps
`needs-changelog-context` as the signal and `check-pr` as the sole changelog
merge gate. Dispatcher implementation is deferred to #2191.
