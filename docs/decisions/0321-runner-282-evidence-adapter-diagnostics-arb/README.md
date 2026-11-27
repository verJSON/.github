# 0321 — Board review of runner evidence adapter diagnostics

- **Date:** 2026-11-27
- **Status:** Approved for the exact source. Five seats accepted S1 in round one of the [chair record](arb-record.md).
- **Scope:** [Runner PR #282][source] under [runner canary issue #197][issue].
- **Admission:** [ADR 0309](../0309-standing-archive-only-admission-lane/README.md) and [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md).

The board qualified the fixed release metadata, manifest download and Git
checkout diagnostics at head `a81f75df15aa1547167c26ba599ec26231aba546`.
The source includes relay tests and runs the offline suite in the existing
shell job. The native candidate was `d33862900335ddef841a75e22e7abdb148880a31`
with tree `43287e5a69f5a60d6f518f252898887b2e92fc08` and the reviewed base
`8b711792865dcd097572f4e416916c1672e3e6f6`. The earlier dry-run failure remains
unattributed. [Failed dry run](https://github.com/verJSON/verjson-git-runners/actions/runs/46026637316)

Source landing requires ordinary protection, current checks and native candidate
verification. The exact-head archive citation precedes the independent review
receipt. A separate owner authorization and a verified publisher window are
required before source landing. That window must verify containment and recovery
readiness before merging, then restoration and zero candidate runs after landing.
Changed source coordinates require requalification. Live execution and rollback
remain outstanding. [Decision and conditions](arb-record.md)

## Provenance

The chair supplied the original 12,585-byte record before this commit. Its
whole-file SHA-256 is
`e810a503bf820a0f2c795be19eef915277e4aff29370533ee40e12cb7756588a`.
Run `arb-runner-evidence-282-a81f75df15-20261127` has canonical fence SHA-256
`ee59130f62a4869858754373ca310a279becee4621f4ea303041ebf846de71c1`
and ledger slate SHA-256
`c9bb496980182e935b35917749362780b795f91e2b382b3162ab7d8999498972`.
Four seats reported their served model. The OpenAI seat had a disclosed
identity gap. DeepSeek Pro returned no completion and cast no vote.
[Timestamped chair outcome][outcome] · [Archive work-start standards][standards]

[source]: https://github.com/verJSON/verjson-git-runners/pull/282
[issue]: https://github.com/verJSON/verjson-git-runners/issues/197
[outcome]: https://github.com/verJSON/verjson-git-runners/pull/282#issuecomment-7222746229
[standards]: https://github.com/verJSON/verjson-git-runners/pull/282#issuecomment-7222734966
