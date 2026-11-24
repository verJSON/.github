# 0305 — Admit the Wave 0 runner decision archive through ordinary protection

- **Date:** 2026-11-24
- **Scope:** Documentation in [policy PR #2279](https://github.com/verJSON/.github/pull/2279), part of [GitLab and Nexus adoption #1264](https://github.com/verJSON/.github/issues/1264).
- **Authority:** [Owner ratification](https://github.com/verJSON/.github/pull/2279#issuecomment-7139810803), the [companion chair record](./arb-record.md) and [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md).

[ADR 0304](../0304-wave-zero-runner-source-adjudication/README.md) records the
board’s review of runner PRs #278 and #279 and policy PR #2278. Its decision
requires separate authority to publish the record. The archive-publication board
examined the four files at head `e91bb23d2dc2a68f0761339a2f5243dfcf842617`,
base `664e18cf2e74f5f240d5eb31b6a317a99668e0ba` and tree
`dbde021fa4f7e9423458987a7cc24253795fcbcd`. The original ADR 0304 chair
file has SHA-256 `e50672df4e017405bcabd1c15276de97a967ab74a8c9087fab10d0843ad16ecf`.

For #2279 alone, the owner ratified the board’s proposal to use this separate
companion record as the authority for initial publication. The revision adds
this ADR and its chair record, regenerates the decision index and updates the
existing release note. An independent reviewer must verify the final base, head,
tree, paths and record digests. The publisher may then cite only this companion
chair record at that reviewed commit, followed by the independent review
receipt. Merge through ordinary protection with bypass disabled. Verify the
native ruleset result and landed bytes before a source PR cites ADR 0304.

Each of the three source PRs retains its own review and checks. A changed
base, head or merge candidate requires requalification under ADR 0304.
