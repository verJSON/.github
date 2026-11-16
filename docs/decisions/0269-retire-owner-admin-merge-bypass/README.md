# 0269 — Retire owner `--admin` merge bypass from the autonomous terminal path

- **Date:** 2026-11-16
- **Status:** Accepted (ARB ratification in the default-pm session, 2026-11-16). Records the retirement already observed in this repository; creates no ruleset and changes no required check.
- **Issue:** [verJSON/.github#1945](https://github.com/verJSON/.github/issues/1945)
- **Amends:** [ADR 0260](../0260-native-merge-admission-through-a-producer-posted-check/README.md) C11 only to the extent that autonomous (workflow/script) `--admin` is retired; the manual owner outage escape remains
- **Extends:** [ADR 0257](../0257-merge-admission-requires-the-exact-head-review-receipt/README.md), [ADR 0260](../0260-native-merge-admission-through-a-producer-posted-check/README.md), [ADR 0202](../0202-bind-autonomous-merge-to-independent-review-receipt/README.md)
- **Related:** [#1990](https://github.com/verJSON/.github/issues/1990), [#1964](https://github.com/verJSON/.github/issues/1964), [ADR 0244](../0244-derive-manually-dispatched-release-versions-from-fragments/README.md) (push-guard ABA non-goal recorded under closed #1942)
- **Category:** merge-gate admission (sensitive class)

## Context

#1945 asked to replace privileged merge bypass with evidence-bound admission.
Four acceptance items were tracked:

1. Remove remaining privileged `--admin` merges from live terminal paths, or prove
   each remaining use is App-bound ruleset bypass with equal evidence.
2. Required exact-head candidate checks for normal GitHub merges (not only the
   agent assertion).
3. An ADR recording the non-bypass policy and the refusal matrix.
4. Rollout only through the reviewed org contract.

Items 2 and 4 are owned by [#1990](https://github.com/verJSON/.github/issues/1990)
and ADR 0260 (`merge-admission-required` ruleset `27387670` is active on this
repository). Gate refusal semantics (missing, skipped, stale, failed, cancelled,
retarget, concurrent) live in ADR 0257 / 0260 and `scripts/assert-mergeable-head.sh`.

What remained for this ADR is item 1 and the non-bypass policy statement for the
autonomous terminal path.

## Decision

1. **No owner `--admin` in autonomous workflows.** Production workflows and
   scripts in this repository must not invoke `gh pr merge --admin` (or
   equivalent GraphQL admin bypass). Inventory on 2026-11-16 found no live call
   sites; remaining `--admin` strings are test and fixture negatives only. This
   does not forbid the merge App from using its ruleset bypass after
   evidence-bound admission (Decision 2). It also does not revoke the
   **manual** owner outage escape in ADR 0260 C11 / the merge-admission runbook:
   a human may still run `assert-mergeable-head.sh` and then `gh pr merge
   --admin` outside automation when the producer is down. That escape is
   process, not an autonomous path, and stays until #1964 re-decides bypasses.
2. **Allowed merge principals.** Autonomous merges that the canonical contract
   owns go through the merge App under ruleset bypass only after
   `scripts/assert-mergeable-head.sh` prints `true` for the exact head (Gates
   A, W, B, C, and R where applicable) and ADR 0202 independent-review evidence
   is bound. Owner-account `OrganizationAdmin` bypass is not a supported
   autonomous path.
3. **Native path.** On repositories enrolled under ADR 0260, the merge button
   and native auto-merge are held by the producer-posted `merge-admission` check,
   not by SKIPPED-passes semantics. Stated bypass actors (including Renovate and
   OrganizationAdmin on the ruleset bypass list) remain as named carve-outs in
   ADR 0260 until #1964 re-decides them.
4. **Matrix.** This ADR does not restate the gate matrix. Refusals for
   missing/skipped/stale/failed/cancelled/retarget/concurrent heads are defined
   by ADR 0257, ADR 0229, ADR 0226, ADR 0255, and ADR 0260. Closing #1945
   requires those ADRs to remain Accepted and the live `--admin` inventory to
   stay empty outside tests.

## Consequences

Agents and humans operating the autonomous path cannot treat owner `--admin` as
a merge tool. Evidence-bound App merge and enrolled native `merge-admission` are
the supported routes. Broader org enrollment and bypass-list retirement stay
with #1990 and #1964.

## Verification

- `rg -e '--admin' scripts/ .github/workflows/` reports only test/fixture hits.
- `merge-admission-required` remains an active organization ruleset.
- Autonomous merge workflows continue to require the assertion and ADR 0202
  receipt before any App-bound merge.
