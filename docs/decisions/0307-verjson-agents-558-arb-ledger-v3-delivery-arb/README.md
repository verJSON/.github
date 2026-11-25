# 0307 — Delivery ARB archive for verjson-agents#558 ARB ledger v3

- **Date:** 2026-11-25
- **Status:** Accepted. Ratified by the Architecture Review Board: `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-agents-pr558-d782499c81-20261125-01`; slate digest `e2fcebda188faad25168321ad1870377008aa30c15f7499875c993ab4f648112`.
- **Related:** [verJSON/verjson-agents#558](https://github.com/verJSON/verjson-agents/pull/558), [verJSON/verjson-agents#556](https://github.com/verJSON/verjson-agents/issues/556), [ADR 0306](../0306-verjson-cli-411-vaultwarden-lifecycle-delivery-arb/README.md)
- **Extends:** [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md)
- **Category:** autonomous delivery approval archive (governance tooling: the ARB decision rule and adoption record)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

verjson-agents#558 implements ARB ledger schema version 3 (verjson-agents ADR 0044): the `verified_supermajority` decision rule, adjudicated advisory dissent, the `discover_verify_escalate` round policy and the escalation record. It also re-pins version 2's contract manifest. The repository owner ratified both versions on 2026-11-25 in [a comment on #558](https://github.com/verJSON/verjson-agents/pull/558#issuecomment-7163523808); the PR's last two commits record that ratification.

The board ran under the charter the owner set on 2026-11-24, the same one ADR 0306 records: claude is the only required member; codex, cursor-agent and agy are advisory; each candidate needs claude's ACCEPT plus at least two advisory ACCEPTs; claude adjudicates advisory dissent; at most three rounds, and round 2 only verifies. The board judged the PR with ledger version 2 and the chair enforced the supermajority from the recorded advisory ballots, so the version 3 implementation under review did not judge itself.

In round 3 every candidate met the rule. codex's round-3 REVISE on B6, L1–L7 and M1 restates its round-2 rebuttals, which claude adjudicated as kind (b) in round 3; the chair reads it, as ADR 0306 does, as retained dissent rather than an unresolved split, and records it for the owner.

## Decision

1. **Archive the chair record** at `docs/decisions/0307-verjson-agents-558-arb-ledger-v3-delivery-arb/arb-record.md`.
2. **Adopted slate:** merge verjson-agents#558 at exactly `d782499c8168f46993a9125f9942faaf96bc7ebf` after Gate R. L1–L7 and D1 are kind (b) debt for one follow-up PR linked from verjson-agents#556, with a single re-ratification of the changed manifests. codex's retained dissent on B6, L1–L7 and M1 is recorded for the owner.
3. **This ADR does not** authorize `--admin`, any other head, or a change to the ratified manifests.

## Consequences

Exact-head Gate R citations for verjson-agents#558 may name `verJSON/.github@<archive-sha>:docs/decisions/0307-verjson-agents-558-arb-ledger-v3-delivery-arb/arb-record.md`. verjson-agents#558 cites this record only after it lands on `main`, the order ADR 0305 set.

[arb-record]: ./arb-record.md
