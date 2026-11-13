# 0094 — Require the authorization arm from its own property-scoped ruleset

- **Date:** 2026-08-11
- **Status:** Accepted
- **Issue:** [#748](https://github.com/Verjson/.github/issues/748) (outage: [#728](https://github.com/Verjson/.github/issues/728))
- **Supersedes:** [ADR 0091](../0091-ruleset-requires-authorization-arm/README.md)
- **Amends:** [ADR 0079](../0079-head-bound-ai-authorization-and-native-auto-merge/README.md), [ADR 0090](../0090-human-first-opt-in-ai-review/README.md)
- **Amended by:** [ADR 0254](../0254-audit-recognizes-the-main-protection-suspension/README.md) (a third, derived `suspended` state for the #1933 owner hold)

## Context

ADR 0091 decided to retarget the one workflow selected by `main-protection`
(`18098028`) from `ai-review-merge.yml` to `gate-rearm.yml` "in a single ruleset
update". That decision has not been executable, and the organization gate has
been down since 2026-08-08 while it waited.

Two facts, both established against live state on 2026-08-11:

**The precondition cannot be met in any near term.** ADR 0091 requires canonical
deterministic required CI on every governed default branch before retargeting,
because `main-protection` applies to `~ALL` and the arm is deliberately
non-vetoing — arming a repository with no deterministic CI would leave human
review as its only automated precondition. `ai-review-required-workflow-audit.py`
reports **70 of 91** organization repositories missing it. That is not a property
backfill: setting `verjson-core-checks=enforced` on a repository that produces no
`ci / build-test`, `ci / eligibility`, or `shell-tests` context blocks its pull
requests permanently. The precondition is a multi-repository CI rollout program,
and the gate stays dark for its duration.

**The scope of the workflows rule and the scope of branch protection were never
the same requirement.** `main-protection` carries five rules: `deletion`,
`non_fast_forward`, `required_linear_history`, `pull_request`, and `workflows`.
The first four must apply to `~ALL` — every repository needs force-push and
deletion protection regardless of its CI maturity. Only `workflows` needs to be
limited to repositories that can safely be armed. Because both live in one
ruleset, ADR 0091 had to choose one scope for both, and correctly chose the
stricter precondition rather than weakening branch protection.

The organization already models exactly this pattern three times.
`core-checks-node`, `core-checks-actions`, and `changelog-contract-required` are
separate rulesets whose `conditions` select repositories by **custom repository
property** rather than by name. `verjson-core-checks: enforced` is the property
that already gates deterministic CI, and all 21 repositories carrying it also
carry a declared `verjson-stack`. A workflows rule scoped to that same property
is armed exactly where deterministic CI exists, by construction, with no
coverage gap to close first.

## Decision

Split the `workflows` rule out of `main-protection` into its own organization
ruleset, `ai-authorization-arm-required`, and select `gate-rearm.yml@main` there.

- `main-protection` keeps `deletion`, `non_fast_forward`,
  `required_linear_history`, and `pull_request`, unchanged, at `~ALL`, with its
  bypass actors and ref conditions untouched. It loses one rule and nothing else.
- `ai-authorization-arm-required` carries the **relocated** rule — same
  parameters, same `do_not_enforce_on_create`, same source repository and
  immutable ref, differing only in the selected path — with the same bypass
  actors and the same ref conditions as the ruleset it left.
- Its `conditions` select by `repository_property` `verjson-core-checks:
  enforced` and **never** by `repository_name`. Scoping it by name would
  reconstruct the `~ALL` hazard the split exists to remove and silently decouple
  the arm from deterministic CI.

Two consequences of the narrower scope are deliberate, and are the substance of
what this ADR changes:

1. **Deterministic-CI coverage is required of armed repositories only.** A
   repository without the property gets no arm, so the arm cannot become its only
   merge precondition. The hazard ADR 0091 guarded against does not arise there.
   A repository that carries the property but no declared stack is still
   rejected — that one *is* armed with nothing deterministic behind it.
2. **The App credential must reach armed repositories only.** ADR 0091 treated
   organization-wide reach as a rollout blocker; requiring fleet-wide reach for a
   rule that governs a subset was demanding a scope expansion the decision did
   not need. Least privilege here means `selected` over the armed set — which
   also narrows `AI_REVIEW_APP_PRIVATE_KEY` from its current `visibility: all`
   (see [#265](https://github.com/Verjson/.github/issues/265)).

The audit's states become `ready` (protection ruleset is the full reviewed
preimage; the arm ruleset does not yet exist) and `split` (protection ruleset is
the postimage; exactly one ruleset matches the full reviewed arm image). The
postimage is **derived** from the preimage by removing the workflows rule and
compared, so a contract that also relaxes enforcement, widens a bypass, drops a
protection rule, or narrows `repository_name` while presenting itself as "just
moving the workflows rule" is rejected rather than reviewed by eye. Rollback
restores the verified preimage and deletes the arm ruleset.

### One deliberate exception, and its bound

`--render-split-payload` and `--render-arm-ruleset-payload` run the full audit
with `allow_unschedulable_selection=True`. Every other precondition still
applies. This exists because the currently selected workflow being unschedulable
*is the outage the split repairs* (#745): refusing to render the remedy while the
defect is present would make the audit hold the outage in place. The exception is
bounded three ways — it never applies to a plain audit, so the finding is never
suppressed; it never applies in `split` state, where a dead selection is a fresh
regression rather than a state being repaired; and it suppresses only that one
condition.

## Consequences

- The gate is restored for the 21 repositories that have canonical deterministic
  CI, without waiting on the other 70 and without weakening branch protection
  anywhere.
- **Arming a repository becomes a property assignment.** Setting
  `verjson-core-checks=enforced` now also grants the arm. That is the intended
  coupling — the property means "this repository has deterministic CI" — but it
  makes the property load-bearing for two things at once, and any future tool
  that sets it must be reviewed on that basis.
- The 70 uncovered repositories keep human review and ordinary branch protection
  and are no longer a blocker for anything. Closing that gap remains worthwhile
  and is now ordinary backlog rather than a release gate.
- Two organization rulesets must be reasoned about together. The audit's
  exclusivity check enforces that exactly one ruleset ever selects the retired or
  replacement path, so "both are required" and "neither is" both fail loudly.
- Rollback is two mutations rather than one: restore the preimage and delete the
  arm ruleset. The audit refuses to render the rollback unless it first proves
  the live state is the full split image.

## 2026-08-12 amendment — arm receipts bind required-workflow runs to the organization rule (#757)

The split exposed a false assumption in receipt verification. A run created by an
organization `workflows` rule has a consumer-scoped `workflow_id` and a
`workflow_url` under `actions/required_workflows`; it does not share the ID returned
by the consumer's repository-local `actions/workflows/gate-rearm.yml` registration.
Run `31597679007` demonstrated both IDs in this repository: required-workflow ID
`332376738` versus local-workflow ID `328994427`. Seventeen of the 21 armed
repositories had no local registration at all, so the path lookup returned 404
before a receipt could be checked.

Receipt verification now recognizes the two installation shapes without weakening
the provenance invariant:

- a repository-local arm still requires exact equality with the workflow ID resolved
  from that repository's `gate-rearm.yml` registration;
- a ruleset-created arm requires the run's exact consumer-scoped
  `actions/required_workflows/<workflow_id>` URL and an active rule on the pull
  request's base branch. Every rule selecting `gate-rearm.yml` must originate from
  the `Verjson` organization, name canonical repository ID `1269388380`, and pin
  `refs/heads/main`.

The run's path is only a selector for rules that must then pass the source checks; it
never establishes trust by itself. A repository ruleset using the same path is
rejected, and an impostor beside a valid organization rule withdraws trust. This is
the arm-specific application of ADR 0039's required-workflow provenance boundary and
retains its ambient-configuration limitation: configuration is read when the receipt
is verified and is re-read by the verifier at terminal promotion, rather than being a
signed property embedded in the historical run.

`scripts/ci-gate/arm-receipt.test.sh` exercises a consumer with no local caller and
rejects repository-sourced, wrong-ref, and later-page same-path forgeries while still
requiring the immutable receipt, exact run attempt, App identity, and current pull
request head.

## 2026-08-13 amendment — scope the complete local-caller policy family atomically (#789)

An armed repository that uses repository-local generated callers is a separate
installation shape from the organization required-workflow path. Assigning
`verjson-core-checks=enforced` scopes the required arm itself, but it does not make
the organization variables consumed by the local arm and review caller visible.
Partial variable rollout therefore fails after admission: the caller can be present
and trusted while one of its identity, authority, provider, model, or budget inputs
is absent or silently takes a default.

Adopting this existing arm policy through repository-local generated callers now
requires one atomic selected-repository grant for the complete primary identity and
policy family:

- `AI_REVIEW_APP_ID`, `AI_REVIEW_APP_SLUG`, and `AI_REVIEW_CLIENT_ID`;
- `AI_REVIEW_AUTHORITY`;
- `AI_REVIEW_PRIMARY_PROVIDER`, `AI_REVIEW_PRIMARY_MODEL`, and
  `AI_REVIEW_PRIMARY_BUDGET_USD`;
- `AI_REVIEW_PRIMARY_FALLBACK_MODEL` and
  `AI_REVIEW_PRIMARY_FALLBACK_BUDGET_USD`.

The grant is complete only when an exact selected-repository membership read proves
all nine variables reach the repository. The dedicated App installation is verified
separately: its App ID, unsuspended state, repository selection, and exact permission
map remain deployment prerequisites. Provider and App secrets are also verified
separately by name and visibility without reading or recording secret values. A
successful variable grant is not evidence that either prerequisite is satisfied, and
an organization-wide App installation or secret does not repair an incomplete
selected-variable family.

The live pre-rollout receipt for `Verjson/verjson-ai` on 2026-08-13 recorded the
authorization App as unsuspended, App ID `4528902`, `repository_selection: all`, and
permissions `checks: write`, `contents: read`, `pull_requests: write`, and
`metadata: read`. `AI_REVIEW_APP_PRIVATE_KEY` and `DEEPSEEK_API_KEY` both had
`visibility: all`; no secret value was read. Exact organization selected-repository
membership reads found the repository absent from all nine variables above. This
amendment records the adoption precondition and evidence; it does not itself mutate
organization settings or change the authority architecture decided by ADR 0079.
