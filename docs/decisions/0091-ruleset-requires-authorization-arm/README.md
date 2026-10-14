# 0091 — Require the authorization arm, not the dispatched AI review

- **Date:** 2026-08-10
- **Status:** Superseded by [ADR 0094](../0094-arm-required-by-its-own-property-scoped-ruleset/README.md)
- **Issue:** [#728](https://github.com/Verjson/.github/issues/728)
- **Amends:** [ADR 0079](../0079-head-bound-ai-authorization-and-native-auto-merge/README.md), [ADR 0090](../0090-human-first-opt-in-ai-review/README.md)

## Context

Organization ruleset `main-protection` (`18098028`) selects
`.github/workflows/ai-review-merge.yml@main` as its one required workflow. GitHub
ruleset workflows run only when their source declares `pull_request`,
`pull_request_target`, or `merge_group`. The selected review workflow deliberately
declares only `workflow_dispatch` and `workflow_call`, because ADR 0079 requires an
immutable exact-head arm receipt before review can gain authority. A consumer PR can
therefore synchronize without a ruleset workflow run, receipt, dedicated-App check,
review, or terminal promotion.

The trusted `gate-rearm.yml` entrypoint already declares `pull_request_target`, never
checks out PR code, creates the exact-head App check and receipt, and dispatches the
review only after the artifact is published. GitHub ignores a ruleset workflow's event
filters and invokes the default `pull_request_target` activity types: `opened`,
`synchronize`, and `reopened`. Repository-local callers remain responsible for label-only
events such as adding `ai-review`; the organization rule guarantees that the next
supported PR synchronization reaches the arm.

Required workflows execute in the governed repository's context. The App private key
and identity variables are currently selected to fewer repositories than the ruleset's
`~ALL` scope. Retargeting first would make the arm unable to mint the dedicated App token
in those repositories. Requiring both the retired workflow and an App check would be
worse: one never starts and the other never appears.

The replacement is intentionally non-vetoing so AI infrastructure cannot remove the
human path. That is safe only where another active rule requires ordinary deterministic
CI. Live inspection found repositories with no such rule, including `demo-repository`
and `AiB`; retargeting `~ALL` in that state would leave human review as their only
automated merge precondition. The rollout must close that coverage gap first.

## Decision

Change the one workflow selected by `main-protection` from
`.github/workflows/ai-review-merge.yml@main` to
`.github/workflows/gate-rearm.yml@main` in a single ruleset update. Never retain both
paths, and do not also require `AI review authorization` as a status context.

The arm job runs on fixed GitHub-hosted `ubuntu-24.04` capacity and is
`continue-on-error`: its failures remain visible, but the required
workflow cannot veto a merge authorized by the existing human review and deterministic
CI policy. This does not weaken AI authority. App approval and terminal promotion still
require the exact successful dedicated-App check, immutable receipt, reviewed head,
policy envelope, and configured deterministic CI. A missing receipt or failed arm grants
nothing.

The reviewed contract stores the complete normalized `main-protection` mutation payload:
name, target, enforcement, every bypass actor, conditions, and every rule. Its postimage
must be byte-for-byte semantic equality after changing only the selected workflow path.
The preimage is retained as the exact rollback payload. The audit can render either
payload only after proving the live ruleset is the corresponding full image; it never
writes GitHub state.

`scripts/ai-review-required-workflow-audit.py` is the mandatory read-only preflight and
post-change verifier. It fails unless:

- every field of `main-protection` equals the reviewed preimage or postimage;
- no other organization ruleset requires either arm identity or the App check;
- every governed default branch has an effective required-status rule naming a
  canonical deterministic CI context;
- the private key and App identity variables reach every currently governed repository;
- no governed repository shadows the organization private key, client ID, numeric App
  ID, or App slug with a repository-level secret or variable;
- the dedicated App installation is unsuspended, covers all repositories, has no event
  subscriptions, and has exactly Checks write, contents read, metadata read, and pull
  requests write with no extra scopes; and
- canonical `gate-rearm.yml@main` exposes `pull_request_target` while keeping the arm
  non-blocking for ADR 0090's human path on provider-hosted capacity; and
- the workflow the ruleset **currently** selects declares at least one trigger a ruleset
  can fire.

Rollout order is strict: merge the workflow, contract, and audit; deploy canonical
deterministic required-CI coverage to every governed default branch; obtain separate
human authorization for any secret or variable scope expansion; remove repository-level
shadowing; run the audit until it reports `state=ready`; render and independently compare
the retarget payload; replace the one ruleset workflow path; then run the audit again
until it reports `state=retargeted`. On a controlled same-repository PR head carrying
`ai-review`, force a synchronization and verify the ruleset-created arm run, immutable
receipt, exact-head App check, App review, immutable privileged caller dispatch, and
terminal promotion waiting on ordinary required CI. The rollout is incomplete until
that live proof exists.

## Consequences

- A supported PR event reaches the trusted arm instead of silently selecting a workflow
  that cannot start.
- AI remains opt-in and non-vetoing; a human can merge through ordinary policy during a
  provider or authorization outage.
- The currently incomplete credential scope is a measured rollout blocker, not an
  implicit request to expose the private key organization-wide.
- Incomplete deterministic-CI coverage is independently blocking; a green arm cannot
  substitute for ordinary repository tests.
- Emergency rollback restores the verified full preimage, including every bypass actor
  and unrelated rule. That returns to the known dispatch-only incident state and requires
  the existing human/administrator recovery path; it is not proof that AI review works.

## Amendment — 2026-08-11: the selection itself is now verified (#728, #743)

The Context above states that the selected workflow "deliberately declares only
`workflow_dispatch` and `workflow_call`" as a premise of the migration. It was never
enforced as an invariant, and the ordering of the audit hid its consequence.

`429d441` (#642) removed the `pull_request` trigger from `ai-review-merge.yml` on
2026-08-08 while `main-protection` still selected it. The record did not fail loudly: it
became **unschedulable**, rendering on every governed pull request as "Workflow
configuration invalid" with no run, no check, and no receipt. The last ruleset-created
gate run in the fleet is `Verjson/verjson-leads` run `31150760886` on 2026-08-07; every
repository merged without an arm for the four days that followed. The audit could not
report it, because it validated only the *replacement* workflow and raised the unrelated
deterministic-CI readiness gap first — so its output described a rollout that could not
start rather than a gate that had already stopped.

`verify_selected_workflow_is_schedulable` therefore reads the workflow the **live**
ruleset selects and requires at least one of `pull_request`, `pull_request_target`, or
`merge_group`, before any rollout precondition is evaluated. A precondition explains why
the retarget cannot land yet; an unschedulable selection means the gate is down now, and
the more urgent fact must be the one the audit prints.

This does not change the decision. It closes the gap between the decision's premise and
what is verified, and it fixes the reporting order that let a live outage hide behind a
rollout blocker. Two related defects found in the same investigation are tracked
separately: #743 (every scheduled workflow in this repository fails, so nothing was
watching) and #744 (all runner lane variables resolve to one pool).

## Amendment — 2026-10-14: AiB enrolled (#1781)

The Context above names AiB as a repository with no ordinary deterministic CI rule. That
gap persisted: AiB carried `verjson-stack=none` and no `verjson-core-checks` value, so its
`main` declared no required status checks and the admin-merge assertion refused every
AiB pull request as an ungoverned ref. AiB already ran the canonical Node CI and changelog
checks under the exact context names the rulesets require, and held both environment App
keys. #1781 adds `Verjson/AiB` to the reviewed arm cohort and then sets
`verjson-stack=node`, `verjson-core-checks=enforced` and `changelog-contract=adopted`
(it already runs the generated changelog caller), which places it under the
deterministic rulesets and the authorization arm in the order this decision requires:
deterministic CI and the arm together, never the arm alone. The decision is unchanged.
