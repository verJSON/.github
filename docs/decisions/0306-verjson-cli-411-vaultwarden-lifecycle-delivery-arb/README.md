# 0306 — Delivery ARB archive for verjson-cli#411 Vaultwarden member lifecycle

- **Date:** 2026-11-25
- **Status:** Accepted. Ratified by the Architecture Review Board: `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-cli-pr411-27ae92f024-20261125-01`; slate digest `c6c28f504d8972930e12670bcdd7b9989c5787fbe18cb5d59fa291e996dbfad7`.
- **Related:** [verJSON/verjson-cli#411](https://github.com/verJSON/verjson-cli/pull/411), [verJSON/verjson-cli#410](https://github.com/verJSON/verjson-cli/issues/410), [verJSON/verjson-agents#556](https://github.com/verJSON/verjson-agents/issues/556)
- **Extends:** [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md)
- **Category:** autonomous delivery approval archive (sensitive class: OIDC federation, access control, ingress)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

verjson-cli#411 renders the Vaultwarden SSO-only, invitation, event-log and organization-creation policy (verjson-cli ADR 0038). Seventeen earlier board runs did not converge on a prose apply, rollback and recovery runbook. The owner removed that runbook from the PR and moved it to tested scripts (verjson-cli#412).

The owner also set a new charter on 2026-11-24:
- claude, the verified member, is the only required member;
- each candidate needs claude's ACCEPT plus at least two of the three advisory ACCEPTs (codex, cursor-agent, agy);
- claude adjudicates every advisory dissent;
- at most three rounds; round 2 only verifies earlier findings; a split remaining at the last round escalates to the owner.

Until verjson-agents#556 lands, the ledger engine sees only the required member, so the chair enforced the supermajority condition from the recorded advisory ballots, as the chair record states.

Gate R required this archived chair record before the autonomous merge of head `27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f`. verjson-cli#411 merged as `abd1085` at 2026-11-25T02:06:13Z, citing this branch's commit `519e3c9dae3fe13dc3fb6901a00bcf2b4ca65c47` before the archive landed on `main`. That reverses the order ADR 0305 set for the Wave 0 archive, where the archive landed before any source PR cited it; this ADR records the deviation rather than repairing it, and `arb-record.md` stays byte-identical to the cited commit.

The charter does not define a split. The chair reads it as a candidate that does not meet the decision rule (claude's ACCEPT plus at least two advisory ACCEPTs). codex's round-3 REVISE on B2, L3 and M1 counts as adjudicated because it restates codex's round-2 rebuttal: claude dismissed the finding in round 2 and confirmed that dismissal against the rebuttal in round 3; each of those candidates meets the rule, so the chair found no split to escalate. The record's note that owner ratification is pending is not a merge condition: under ADR 0271 the board's consensus is the approval authority.

## Decision

1. **Archive the chair record** at `docs/decisions/0306-verjson-cli-411-vaultwarden-lifecycle-delivery-arb/arb-record.md`.
2. **Adopted slate:** merge verjson-cli#411 at exactly `27ae92f0240e4757e4a80f5a71a8f2b175cc1d1f` after Gate R. L1–L3 and D1 are kind (b) debt for a follow-up issue linked from verjson-cli#410. codex's retained dissent on B2, L3 and M1 (one finding) is recorded for the owner.
3. **This ADR does not** authorize `--admin`, live host mutation, or any other head.

## Consequences

Exact-head Gate R citations for verjson-cli#411 may name `verJSON/.github@<archive-sha>:docs/decisions/0306-verjson-cli-411-vaultwarden-lifecycle-delivery-arb/arb-record.md`.

[arb-record]: ./arb-record.md
