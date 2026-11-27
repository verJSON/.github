# 0319 — Delivery ARB archive for verJSON/.github#2299 doctl diagnostics

- **Date:** 2026-11-27
- **Status:** Approved. All five seats accepted S1 and S2 in round one of run `arb-policy-doctl-629-d19889bf49-20261127`.
- **Scope:** [Source PR #2299][source], [tracking issue #629][issue], and [runner canary #197][runner].
- **Admission:** [ADR 0309](../0309-standing-archive-only-admission-lane/README.md) and [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md).

## Archived record

The [chair record](./arb-record.md) is the original 18,266-byte artifact. Its
whole-file SHA-256 is
`58213da2074f4d919122014eae920bb3664b212f096033bdb7d44c0d7a6bab83`.
The canonical fence SHA-256 is
`76e1f898d8ccd5a224ad32272adbbed82dc9ecfe48c07f60379430ced613c803`.
The ledger slate SHA-256 is
`93fc30cd191f7bad4e25f4b76b1a2bffcd7451900943d92d57c7819700d79934`.
The [chair outcome comment][outcome] records the hashes and run before the
archive commit.

S1 approves the bounded diagnostic classifier at source head
`d19889bf49031481035221291b25358270404a1d`. The protected dry-run's cause
remains unidentified. Downstream regeneration and live operations require
their own evidence and authorization.

S2 qualifies one successor whose only advance from the reviewed base is
this four-file archive. Source admission requires independently verified
archive provenance and landing, an unchanged source head and complete
delta, a fresh native candidate with reproduced parents and result tree,
and passing candidate checks. The record requires the exact-head citation
before the independent review receipt, an ordinary head-bound merge with
bypass disabled, and a coordinated delivery window with checks immediately
before and after landing. Other base changes return to the board.

Four seats reported the served model. The OpenAI adapter did not report
that metadata. DeepSeek was unavailable before chartering.

[source]: https://github.com/verJSON/.github/pull/2299
[issue]: https://github.com/verJSON/.github/issues/629
[runner]: https://github.com/verJSON/verjson-git-runners/issues/197
[outcome]: https://github.com/verJSON/.github/pull/2299#issuecomment-7220800499
