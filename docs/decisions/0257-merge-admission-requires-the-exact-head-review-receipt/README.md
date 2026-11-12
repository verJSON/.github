# 0257 — Merge admission requires the exact-head independent-review receipt on promotion-enabled repositories

- **Date:** 2026-11-05
- **Status:** Accepted (owner decision in the PM session, 2026-11-05). It changes no ruleset, property, or required check.
- **Issue:** [verJSON/.github#2031](https://github.com/verJSON/.github/issues/2031), a handoff from [verJSON/verjson-agents#471](https://github.com/verJSON/verjson-agents/issues/471) ([verjson-agents ADR 0035](https://github.com/verJSON/verjson-agents/tree/main/docs/decisions/0035-push-delivery-legs-only-after-the-reviewer-gate))
- **Amends:** [ADR 0202](../0202-bind-autonomous-merge-to-independent-review-receipt/README.md) (the receipt is also consumed by merge admission, and its check moves to one shared verifier)
- **Supersedes in part:** ADR 0202's 2026-10-22 amendment, policy items 2 and 3, to the extent that they distinguish human from agent identities (Decision 6)
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
- Agents author pull requests, push, and post receipts through the owner's single
  account. On an agent-authored pull request, the receipt's reviewer is therefore the
  author's own identity. verjson-agent-identity does not issue separate identities yet
  (#1964).
- ADR 0202's 2026-10-22 amendment admits approval only from a direct human session and
  names the delegating human's review `delegation-owner-review`. The verifier keeps only
  `.user.type == "User"` reviewers. Both distinguish humans from agents.

On 2026-11-05 the owner decided, in the PM session: "The goal is not to differentiate
between human and agent as long as they have distinct identities." The same day the
owner answered two follow-up questions: admin-merge scope is "role-based, not class",
and distinctness should "compare acting identity". Decisions 5 to 9 record all three.

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
     request, and exact head. A refusal exits `6` and quotes the verifier's reason. A
     review listing or reviewer permission the verifier could not read leaves the
     receipt unanswered, so Gate R exits `1` and quotes the failed read (Decision 2).
   - Off promotion-enabled base refs, Gate R reads nothing and requires nothing.
2. **One verifier.** The receipt check moves verbatim from the terminal workflow's inline
   step into `scripts/ci-gate/independent-review-receipt.sh`. The terminal workflow runs
   it from its immutable `.gate-trust` checkout, and Gate R runs it from beside the
   script. Two behaviors change. A review listing that cannot be read is refused with a
   named reason; before, that step failed without a reason. A reviewer whose repository
   permission cannot be read now stops the search, on the first read and on the
   revalidating one, with a named refusal; before, the search skipped that reviewer as
   unprivileged, so an older receipt could govern past a newer verdict that a failed
   lookup hid. The verifier exits `3` for either failed read and `1` for a receipt it
   refuses. The terminal workflow refuses on any non-zero exit; Gate R maps `3` to its
   exit `1`, like every other API failure in the assertion. A failed re-fetch of the
   selected review stays a refusal, because a deleted review answers it the same way
   a server error does.
3. **No receipt-only required context.** Native-path coverage belongs to #1990's
   admission check. When that check runs this assertion, Gate R comes with it. Whether
   that check may veto the human merge path is the owner decision that ADR 0091 and
   ADR 0202 reserved; #1990's ADR must record it.
4. **Remote enforcement against an agent** needs an agent principal that cannot bypass.
   That is #1964 (issuer-bound agent review authority). Until then, Gate R is as binding
   as the merge guard that requires the assertion.
5. **Independence is identity-neutral.** The target rule: an exact-head review receipt
   counts only when the reviewer's acting identity (Decision 7) is distinct from the
   head's author and pushers, and human and agent identities are treated alike, on
   every merge path. Gate R does not compare identities today. It now requires the
   exact-head receipt on the assertion path; the distinct-identity rule takes effect
   when #1964 switches on. Until then, a receipt from the same identity passes under the
   Decision 8 exception, and Gate R shows the notice. #1990 is the vehicle for the
   native merge-button and auto-merge path; how that check relates to ADR 0091's
   non-vetoing arm remains for #1990's ADR.
6. **What this supersedes in ADR 0202.** Its 2026-10-22 amendment is superseded to the
   extent that it distinguishes humans from agents:
   - *Item 2* ("Approval identity is a direct human session only"): agent credentials are
     no longer barred, as a class, from carrying review scope. An approval counts by
     distinct acting identity, human or agent. Item 2's bar on admin-merge scope for
     agent credentials is superseded too, by a second owner decision of 2026-11-05:
     "role-based, not class". The target rule: admin-merge follows the identity's RBAC
     role and is subject to the same gates for every identity, namely the exact-head
     receipt, the merge assertion, and, once enabled, a distinct reviewer. An agent
     identity of its own holds admin only when the identity infrastructure grants it
     that role. Today agents act through the owner's account and use its
     `OrganizationAdmin` bypass. They are held only by the merge assertion and its Gate
     R, which the merge guard makes them run (Decision 4). The human admin-bypass path
     (the Context table) and the native merge path (Decision 3) are not held by either.
     The direct-human qualifier in that amendment's re-enable trigger falls with item 2;
     the trigger keeps its head-SHA and diff binding.
   - *Item 3* (delegation-owner review as a named exception): the delegating human is one
     acting identity among others. Their review of work their agent authored under a
     distinct agent identity is plain independent review, not a separate approval kind.
     A review from the same acting identity is the `shared-identity-review` exception of
     Decision 8 until #1964 switches on. The safeguard that barred agents as a class
     from obtaining, refreshing, or relaying an approval or admin-merge credential is
     replaced by the identity rule: no identity may use another identity's approval or
     admin-merge credential.

   Not superseded, and still in force: item 1 (authorship identity is composite,
   `{human subject, model, agent/run}`), which Decision 7 keeps for attribution and
   records while comparing only its acting element; item 4 (autonomous system actions
   carry an explicit machine subject); the recheck immediately before merge; ADR 0208's
   live `maintain`/`admin` eligibility; the receipt contract of ADR 0202's Decision,
   except its non-App `User` restriction, which Decision 7 replaces at the #1964
   switch-on; and ADR 0202's interim-state paragraph: the receipt remains evidence for
   the autonomous merge path only, and is not evidence for any reviewer ruleset.

   Also kept, but as the **target** contract for the #1964 switch-on and the
   reviewer-ruleset re-enable, not as current behavior: binding an approval to the
   base and the diff digest, its staleness on a change of base, and an audit record
   that keeps the composite authorship identity beside the reviewer. Today's ADR 0202
   receipt binds only the pull request number and the head SHA. It goes stale on a push
   by construction, and it does not go stale when only the base changes.
7. **"Distinct identity", mechanically, once enabled.** Distinctness compares **acting
   identities**, by a third owner decision of 2026-11-05: "compare acting identity". An
   acting identity is the credential that authored, pushed, or reviewed: an agent
   identity (with its run) or a human credential. It is not the principal subject `sub`
   of the composite authorship identity. A human's review of their own agent's work
   therefore counts as independent, while a review by the same agent identity or run
   never counts. Records keep both the composite authorship identity of item 1 and the
   reviewer's acting identity. The reviewer's registered acting identity (a user login
   or an App identity, human or agent, issued or registered through
   verjson-agent-identity) must differ from the acting identity of the pull request's
   author and of every pusher and committer of the commits in the head range. The
   verifier's
   `.user.type == "User"` filter, ADR 0202's "non-App `User`", is human-versus-agent
   differentiation. The target rule removes it in favor of that registered-identity
   check, and the change lands with the #1964 switch-on, not before. The filter also
   keeps the merge and review Apps from posting receipts today; the registered-identity
   check must keep them out by not registering them as reviewers.
8. **Phasing: `shared-identity-review`, a named, time-boxed exception.** Gate R merges now
   and requires the exact-head receipt. Until verjson-agent-identity issues separate
   identities, agents and the owner act through one GitHub account, so their acting
   identities cannot be told apart. A receipt whose reviewer shares the author's or a
   pusher's account therefore still counts, as `shared-identity-review`, never as plain
   independent review. Nothing is
   blocked today. The exception ends when the distinct-identity check switches on in
   the shared verifier, tracked on #1964.
   - Gate R names it: when the receipt's reviewer login equals the pull request author's
     login, it prints a `::notice::` naming `shared-identity-review`. The notice gates
     nothing. Its absence does not prove distinct identity, because pushers and
     committers are compared only at the switch-on.
   - The terminal workflow does not name it yet. Doing so from the verifier needs the
     pull request author, a new read in a file both paths share, and a change to the
     terminal workflow's stubbed contract. The switch-on adds that comparison to the
     verifier anyway, so both paths name the exception from one place then.
9. **App-authored pull requests get no exemption.** Under the identity-neutral rule, an
   App author such as Renovate is one more distinct identity, so a receipt from any
   eligible reviewer counts. Once pushers are compared, an identity that pushed an
   adaptation commit onto such a branch cannot review it.

## Relationship to open issues

- **#1990** (native-merge admission through a required check) is the vehicle for the
  native-path half of #2031. #2031 does not merge into it, because Gate R is useful on
  its own and lands first. The remainder of #2031 should be tracked as *blocked by*
  #1990.
- **#1964** (issuer-bound agent review authority) owns the non-bypass agent principal
  and the successor to ADR 0202's account-based receipt. It also tracks the switch-on
  that ends `shared-identity-review`: separate identities from verjson-agent-identity,
  the registered-identity check in place of `.user.type == "User"`, the author, pusher,
  and committer comparison, and the exception's naming on the terminal path. When it
  lands, the verifier file is the one place to change.
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
- Gate R accepts a pass only with the result line the verifier promises, a numeric
  review id and a reviewer, and it maps any verifier status other than `0`, `1`, and
  `3` to exit `1`. The assertion runs without `set -e`, so an unread result would
  otherwise reach `true`.
- A Gate R read failure is now exit `1`, not `6`: a server error on the review listing
  or a permission lookup is an unanswered question, not a missing receipt. The
  terminal workflow still refuses on it.
- Until the #1964 switch-on, a receipt on an agent-authored pull request is a
  `shared-identity-review`, and Gate R's output says so. It is not evidence of review
  by a distinct identity.
- A Renovate or other App-authored pull request reviewed from the owner's account
  already has a reviewer distinct from its author, and Gate R prints no notice for it.

## Verification

`scripts/assert-mergeable-head.review-receipt.test.sh` (merge-gate group) runs a fixture
that passes Gates A, W, B, and C, then:

- rejects absent, malformed (marker inside other text), dismissed, stale-head,
  App-authored, superseded (a later changes-requested verdict, and a verdict that lands
  during revalidation), and write-only receipts, each with exit `6` and its specific
  reason;
- accepts a valid maintain-role receipt;
- reads no reviews on a base ref without the arm rule;
- exits `1` for an arm-path entry whose repository id is absent or a string, and for a
  script copied without its verifier;
- names an unreadable review listing, on either read, and an unreadable reviewer
  permission, each with exit `1`; the permission case puts it on a newer verdict above a valid receipt, once
  on the first read and once on a verdict that appears only on the revalidating read
  (removing the second search's exit-`3` handling fails that case);
- pins the arm identity to the reviewed ruleset image;
- with a stub verifier and a null pull request author, exits `1` for a pass that wrote
  no result or a malformed one (the first printed `true` before the fix), and for
  verifier status `2` and a signal (`143`);
- passes a receipt whose reviewer is the pull request's author and names it in a
  `shared-identity-review` notice, while the valid case, with a distinct author, prints
  `true` alone. The stubbed `gh pr view` answers only the requested fields, so the
  notice case fails both when the comparison is inverted and when the script stops
  requesting `author`.

Each case that already held when written was proven by a mutant that fails it for its
asserted reason. `scripts/ci-gate/native-automerge.test.sh` keeps every ADR 0202 case
and now exercises the shared verifier through the terminal workflow; the verifier
mutants also fail it. `scripts/workflow-checkout-isolation.test.py` requires every file a
job reads from a fixed-path sparse checkout, such as `.gate-trust`, to be in that job's
sparse list, so dropping the verifier from the terminal workflow's checkout fails CI
rather than every terminal merge.
