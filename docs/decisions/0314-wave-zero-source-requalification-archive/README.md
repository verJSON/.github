# 0314 — Wave 0 source requalification archive for the GitLab runner rehearsal

- **Date:** 2026-11-26
- **Status:** Accepted. The [chair record](arb-record.md) preserves the November 24 ARB’s four unanimous decisions in run `arb-wave0-requalification-20261124`.
- **Admission:** [ADR 0309](../0309-standing-archive-only-admission-lane/README.md) and [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md).
- **Related:** [adoption tracker #1264](https://github.com/verJSON/.github/issues/1264), [runner #279](https://github.com/verJSON/verjson-git-runners/pull/279), and [policy #2278](https://github.com/verJSON/.github/pull/2278).

## Decision and scope

The board accepted Q1–Q4 in round two, with five votes per decision. Q2 supports the source of runner #279 at the exact Git objects below. Q3 retains the independent admission checks. Q4 records the archive options available at the time and leaves any nonpublishing merge window or live rehearsal subject to its own controls. See the [original board outcome](https://github.com/verJSON/.github/issues/1264#issuecomment-7140862680).

| Runner #279 Git object | Reviewed value |
| --- | --- |
| Base | `67a494b8485ac8a6e8a58c73504dc84b2979f7fc` |
| Head | `6d3f147edb3755e1ff0602e14dbb141fbbaa7daa` |
| Native merge candidate | `413100c0c62128690ec07d011ffe2c1d5acddfae` |
| Resulting tree | `890757725ab39fb15a71344947be3a22ae7f483c` |

Q1 records the historical policy #2278 candidate. Policy `main` has advanced. [ADR 0304](../0304-wave-zero-runner-source-adjudication/README.md) requires fresh qualification whenever the affected base, head or merge candidate changes. Recheck Q2’s four coordinates before citing it for runner #279.

## Provenance

Whole-file SHA-256 of the independently retained chair artifact is `28e62ffb25b022081a577e7023fef020550f0bc0a46645ac24af0dc85e6fcd87`. The canonical fence has SHA-256 `7ea06b83828da8bac048cd54914f3ea8854d09ccee33348fb7b99e76ea805b44`. The chair materialized this record from the advisory and the recorded decision ledger before this archive PR. The original ledger, ballots, and the contemporaneous chair-artifact digest provide an independent comparison for the receipt reviewer.
