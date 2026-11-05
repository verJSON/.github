# 0257 — Merge admission requires the exact-head independent-review receipt on promotion-enabled repositories

- **Date:** 2026-11-05
- **Status:** Proposed (ARB input for #2031). It changes no ruleset, property, or required check. Accepted on owner approval.
- **Issue:** [verJSON/.github#2031](https://github.com/verJSON/.github/issues/2031), a handoff from [verJSON/verjson-agents#471](https://github.com/verJSON/verjson-agents/issues/471) ([verjson-agents ADR 0035](https://github.com/verJSON/verjson-agents/tree/main/docs/decisions/0035-push-delivery-legs-only-after-the-reviewer-gate))
- **Amends:** [ADR 0202](../0202-bind-autonomous-merge-to-independent-review-receipt/README.md) (the receipt is also consumed by merge admission, and its check moves to one shared verifier)
- **Extends:** [ADR 0184](../0184-merge-gates-assert-execution-not-absence-of-red/README.md), [ADR 0229](../0229-required-contexts-need-success-and-finished-runs/README.md), [ADR 0255](../0255-gate-w-required-workflow-run-precondition/README.md)
- **Related:** [#1990](https://github.com/verJSON/.github/issues/1990), [#1964](https://github.com/verJSON/.github/issues/1964), [#1655](https://github.com/verJSON/.github/issues/1655), [ADR 0091](../0091-ruleset-requires-authorization-arm/README.md), [ADR 0206](../0206-ai-authorization-property-is-independent/README.md), [ADR 0252](../0252-exclude-actions-paused-repos-from-required-checks/README.md), [ADR 0254](../0254-audit-recognizes-the-main-protection-suspension/README.md)
- **Category:** merge-gate admission (sensitive class)

## Context

verjson-agents ADR 0035 makes a delivery leg on a repository with autonomous promotion
commit locally and push only after the independent reviewer gate. github.com has no
pre-receive hooks, so that rule is process only. #2031 asks for the merge-time
counterpart: a promotion-enabled repository must not merge a head that lacks a valid
ADR 0202 independent-review receipt bound to that exact head.

On 2026-11-05 a promotion-enabled repository had four merge paths:

| Path | Principal | What holds it | Receipt checked |
| --- | --- | --- | --- |
| Terminal merge (`ai-privileged-merge.yml`) | merge App, ruleset bypass | the workflow's own gates | yes, ADR 0202 |
| Agent `gh pr merge --admin` or `--auto` | owner account, `OrganizationAdmin` bypass | `scripts/assert-mergeable-head.sh`, which the merge guard makes mandatory | **no** |
| Merge button or native auto-merge | any writer, no bypass | rulesets only | no |
| Human admin bypass | owner | nothing; outside the autonomous path | no |

Facts read live on 2026-11-05:

- Every organization ruleset lists `OrganizationAdmin` with `bypass_mode: always`, and
  agents act through the owner's account. No ruleset can hold such an agent; only the
  admission assertion, which the agent must run, can.
- The `verjson-ai-authorization` property of ADR 0206 is not in the organization
  schema. Ruleset `20722935` still uses the legacy `verjson-core-checks=enforced`
  selector, minus ADR 0252's paused repositories. The effective rules of an armed
  branch carry the arm as a `workflows` entry, for example
  `repos/verJSON/verjson-identity-contracts/rules/branches/main` reports
  `repository_id: 1269388380, path: .github/workflows/gate-rearm.yml`.
- `main-protection` (`18098028`), which carries the human-review rule, is suspended
  (#1933, ADR 0254). For now the native path is held only by deterministic required
  checks and the arm workflow, and ADR 0091 made the arm intentionally non-vetoing.
- 35 generated adopter callers are missing across 23 armed repositories (#1810,
  `config/audit-expected-findings.json`).

## Options

**(a) A new required status check** that a trusted workflow posts after verifying the
receipt for the head.

- *Native paths:* it holds the merge button and native auto-merge for principals that
  cannot bypass. It does not hold an agent: the agent bypasses as `OrganizationAdmin`.
- *Forgeability:* a context bound to `github-actions` (15368) can be forged by any
  same-repository branch's `pull_request` workflow with `checks: write`, and agents push
  same-repository branches. It must therefore be bound to a dedicated App whose key
  lives in a main-only environment. That means a trusted trigger, and none of them fires
  on a review: `pull_request_target` has no review activity, and a ruleset `workflows`
  rule fires only on `pull_request`, `pull_request_target`, and `merge_group`
  (ADR 0091). A `pull_request_review` to `workflow_run` bridge would be needed.
- *Head change:* a check run belongs to one SHA, so a new head has no context and is
  blocked by construction.
- *Same-head supersession:* the check is a snapshot. Dismissal and edits need further
  events to repost it, and a permission downgrade emits no event at all, so a stale
  success persists. The terminal workflow would still have to revalidate live.
- *ADR 0202:* either a second receipt verifier or the shared one; the terminal workflow
  keeps its own revalidation either way.
- *Rollout:* a ruleset change plus a generated caller in every armed repository. With
  #1810 open, a required context that is never posted blocks every merge in the
  repositories missing the caller.
- *Failure mode:* closed. It also makes an AI-infrastructure outage veto the human path,
  which ADR 0091 ("intentionally non-vetoing so AI infrastructure cannot remove the
  human path") and ADR 0202 ("does not change the protected human merge path") rejected.

**(b) Gate R in `scripts/assert-mergeable-head.sh`.**

- *Native paths:* not held.
- *Forgeability:* nothing to post or forge. It reads the live reviews and the live
  collaborator permission, the same trust claim ADR 0202 already makes.
- *Head change:* the assertion reads `headRefOid` and checks the receipt for exactly that
  SHA. The merge guard passes that captured SHA to `--match-head-commit`, so a push
  between the assertion and the merge fails the merge.
- *Same-head supersession:* a live read and a second read, as in the terminal workflow.
- *ADR 0202:* the same verifier file runs on both paths, so they cannot drift.
- *Rollout:* none. It applies to the next canonical fetch of the script, and it detects
  enrollment from the effective rule, so it holds under either arm selector.
- *Failure mode:* closed. A missing or invalid receipt exits `6`; an unclassifiable arm
  entry or a missing verifier exits `1`.

**(c) Both** costs everything in (a) to cover the native path, which #1990 already
proposes to cover in general with one admission check.

## Decision

1. **Adopt (b).** `scripts/assert-mergeable-head.sh` gains Gate R, evaluated after
   Gates A, W, B, and C, so it is reached only on an otherwise mergeable head.
   - A base ref is **promotion-enabled** when its effective `workflows` rules name the
     organization's arm: `repository_id` `1269388380` (verJSON/.github) and path
     `.github/workflows/gate-rearm.yml`. These values equal the reviewed arm image in
     `config/ai-review-required-workflow-rollout.json`, and a test fails when they
     disagree. Reading the effective rule, not a property, is selector-agnostic
     (ADR 0206) and leaves ADR 0252's paused repositories, where nothing can be
     promoted, unarmed.
   - An entry at the arm's path without a numeric `repository_id` cannot be classified
     and exits `1`. Reading it as unarmed would skip the receipt.
   - On a promotion-enabled base ref, the head must carry an ADR 0202 receipt that
     passes `scripts/ci-gate/independent-review-receipt.sh` for this repository, pull
     request, and exact head. A refusal exits `6` and quotes the verifier's reason.
   - Off promotion-enabled base refs, Gate R reads nothing and requires nothing.
2. **One verifier.** The receipt check moves verbatim from the terminal workflow's inline
   step into `scripts/ci-gate/independent-review-receipt.sh`. The terminal workflow runs
   it from its immutable `.gate-trust` checkout, and Gate R runs it from beside the
   script. The only behavioral addition is a named refusal when the review listing
   cannot be read; before, that step failed without a reason.
3. **No receipt-only required context.** Native-path coverage belongs to #1990's
   admission check. When that check runs this assertion, Gate R comes with it. Whether
   that check may veto the human merge path is the owner decision that ADR 0091 and
   ADR 0202 reserved; #1990's ADR must record it.
4. **Remote enforcement against an agent** needs an agent principal that cannot bypass.
   That is #1964 (issuer-bound agent review authority). Until then, Gate R is as binding
   as the merge guard that requires the assertion.

## Relationship to open issues

- **#1990** (native-merge admission through a required check) is the vehicle for the
  native-path half of #2031. #2031 does not merge into it, because Gate R is useful on
  its own and lands first. The remainder of #2031 should be tracked as *blocked by*
  #1990.
- **#1964** (issuer-bound agent review authority) owns the non-bypass agent principal
  and the successor to ADR 0202's account-based receipt. When it lands, the verifier
  file is the one place to change.
- **#1655** (signed merge-gate provenance) authenticates which canonical required
  workflow produced a gate receipt. Option (a) would need it for its check's
  provenance; Gate R posts nothing and does not depend on it.

## Consequences

- An autonomous merge on a promotion-enabled repository now needs a published receipt
  first, as the terminal merge App already did. That includes dependency-update pull
  requests the PM merges.
- Exit `6` is new. A caller that routes by exit code needs a row for it:
  verjson-agents `rules/workflow.md` lists `1` to `5` today. Any non-zero exit already
  blocks the merge, so an unrouted `6` fails closed.
- The assertion now depends on a sibling file, so it must run from a canonical checkout,
  as the merge guard already requires. A lone copy exits `1` and says so.
- **Residual race:** a receipt dismissed after the assertion but before the merge does
  not stop that merge. The window is the same one ADR 0202 accepts after its terminal
  read.
- **Residual process gap:** an agent that skips the assertion is not stopped, as for
  every other gate in it. #1964 closes this gap.

## Verification

`scripts/assert-mergeable-head.review-receipt.test.sh` (merge-gate group) runs a fixture
that passes Gates A, W, B, and C, then:

- rejects absent, malformed (marker inside other text), stale-head, App-authored,
  superseded (a later changes-requested verdict, and a verdict that lands during
  revalidation), and write-only receipts, each with exit `6` and its specific reason;
- accepts a valid maintain-role receipt;
- reads no reviews on a base ref without the arm rule;
- exits `1` for an arm-path entry without a repository id and for a script copied
  without its verifier, and names an unreadable review listing;
- pins the arm identity to the reviewed ruleset image.

Each case that already held when written was proven by a mutant that fails it for its
asserted reason. `scripts/ci-gate/native-automerge.test.sh` keeps every ADR 0202 case
and now exercises the shared verifier through the terminal workflow; the verifier
mutants also fail it.
