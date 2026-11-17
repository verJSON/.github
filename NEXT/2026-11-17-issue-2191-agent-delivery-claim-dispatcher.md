---
date: 2026-11-17
issue: 2191
impact: minor
title: Dispatch idempotent Renovate agent delivery claims
---

Adds a fail-closed dispatcher that claims exactly one agent delivery job per
`(repository, pull_request, head_sha)` when Renovate opens or synchronizes a PR
that still needs changelog context, posting a durable PR comment receipt with
claim id and triple. Concurrency is bounded; double-claim races resolve to one
id. The dependency-upgrade worker remains stubbed for the follow-on canary on
#2191. Does not restore privileged `NEXT/` writers (ARB `arb-2139-2026-11-17`,
slate digest
`9fee6d34a4ef9c4140a899064fd9bfdc82ccddd611eac6c785dbf52b84b7aff9`).

Also archives the `arb-2139-2026-11-17` chair record under
`docs/decisions/0243-agent-authored-changelog-context/arb-record.md` so Gate R
can cite the slate digest for ADR 0202 receipts.
