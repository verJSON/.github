# 0206 — Enroll the AI authorization arm with an independent property

- **Date:** 2026-09-23
- **Status:** Accepted
- **Issue:** [#1363](https://github.com/Verjson/.github/issues/1363)
- **Supersedes in part:** [ADR 0186](../0186-required-checks-bind-through-repository-properties/README.md)
- **Category:** ruleset enforcement

## Context

The organization ruleset `ai-authorization-arm-required` (`20722935`) selects
repositories through `verjson-core-checks=enforced`. That property also selects the
stack-specific deterministic CI rulesets. One property therefore means both “require
canonical core checks” and “run the AI authorization arm,” even though either contract
can evolve independently.

The overloaded selector makes adoption misleading and creates latent activation risk:
enabling deterministic CI can silently add the authorization workflow. It also prevents
an auditor from determining which contract an administrator intended to adopt.

Live organization metadata on 2026-09-23 showed 29 repositories selected by
`verjson-core-checks=enforced` and no `verjson-ai-authorization` schema. The migration
must preserve that reviewed cohort without briefly disarming it or expanding it.

## Decision

Create the optional organization property `verjson-ai-authorization` as a
`single_select` with `enforced` and `disabled` values, no default, and values editable
only by organization actors. Ruleset `20722935` selects only
`verjson-ai-authorization=enforced` after migration.

Authorization enrollment remains fail-closed against deterministic CI: every armed
repository must also carry `verjson-core-checks=enforced` and a supported
`verjson-stack`. The properties are independent selectors, not independent safety
requirements.

The reviewed migration cohort is recorded exactly in
`config/ai-review-required-workflow-rollout.json`. Apply the transition in this order:

1. Create the property schema with `PUT /orgs/Verjson/properties/schema/verjson-ai-authorization`.
2. Assign `enforced` to the reviewed cohort with one
   `PATCH /orgs/Verjson/properties/values` request.
3. Replace ruleset `20722935` with the reviewed image that selects the new property.
4. Read back the schema, values, and ruleset, then run the complete authorization-arm
   audit.

The audit recognizes the exact legacy selector and exact target selector, rejects any
third image, and requires the target cohort to equal the reviewed migration list. The
ruleset renderer is unavailable until both the exact schema and exact property cohort
are visible, so a partial values update cannot produce a disarming cutover payload.

The full forward preflight remains blocked by
[#1401](https://github.com/Verjson/.github/issues/1401) while any armed repository lacks
canonical deterministic CI. The dependency is intentional: the migration must not make
an already unsafe authorization cohort look ready by suppressing that finding.

The tool is deliberately read-only: render modes produce reviewed payloads but never
call a mutating API. Live application remains a separately authorized operator action
recorded on #1363.

Rollback can render the reviewed legacy selector from an exact target-selector state
even when the property schema or assignments are incomplete. Restore that selector
first. Only after readback confirms the legacy selector may the tool render the cohort
payload that unsets the dedicated values. Read back the unset values, then delete
`/orgs/Verjson/properties/schema/verjson-ai-authorization`. Deleting the schema before
restoring the selector is forbidden because it would leave the active ruleset referencing
an absent property.

## Consequences

- Core-check adoption no longer silently enrolls a repository in AI authorization.
- The current 29-repository authorization cohort is preserved deliberately.
- Adding or removing an armed repository requires an explicit property change and a
  matching reviewed migration-cohort update.
- The dedicated property is organization-controlled, so repository content and pull
  requests cannot self-enroll or self-disarm.
- ADR 0186 remains authoritative for deterministic status-check enrollment; only its
  statement that `verjson-core-checks` also selects the authorization arm is superseded.

## Verification

- `python3 scripts/ai_review_required_workflow_audit_test.py`
- `python3 scripts/ai-review-required-workflow-audit.py`
- In the legacy state, render and inspect schema, values, and ruleset payloads in order.
- In the target state, render the legacy-selector rollback; after restoring it, render
  the unset-values cleanup payload.

## 2026-09-27 amendment: architecture review of the authorization and privileged-merge path, and the dependency chain

**Status:** Accepted; #1363, #1401, #1500, #1499. Requested by the owner on 2026-09-26 before
the property migration is applied.

### The path, hop by hop

1. **Enrollment.** A repository is *armed* when organization ruleset 20722935
   (`ai-authorization-arm-required`) selects it. Today the selector is
   `verjson-core-checks=enforced`; after this ADR's migration it is
   `verjson-ai-authorization=enforced`. The same `verjson-core-checks=enforced` (with a
   supported `verjson-stack`) selects the deterministic core-checks rulesets
   (`core-checks-node` 20515817 and `core-checks-actions` 20515822; the opt-in
   `core-checks-node-floor` lane declared by ADR 0176 adds `verjson-node-floor=node22` to
   that conjunction and is recorded under #1274), which require the CI contexts on `main`.
2. **Arm.** The ruleset injects `gate-rearm.yml` from this repository as a required workflow
   on every pull request. Its `app-key-policy` preflight (`app-key-environment.yml`) first
   checks the caller's `ai-review-app` deployment-branch policy keylessly, then validates
   that the environment resolves `AI_REVIEW_APP_PRIVATE_KEY`; the `arm` job itself mints no
   App token. It creates the `AI review authorization` check-run on the exact head with the
   job's `github.token` (owner `github-actions`, App 15368), uploads the arm receipt that
   `verify-arm-receipt.sh` later revalidates, and dispatches the review workflow.
3. **Review and authorization.** The adopter's generated `ai-review-merge.yml` caller (mode
   `gen-ai-review-caller.sh`) runs the review gate: it binds the same `ai-review-app`
   environment, mints the installation token for App 4528902 (`ai-review-authorization`)
   from `AI_REVIEW_APP_PRIVATE_KEY`, reads the arm receipt, reads the ruleset for the base
   ref (`scripts/assert-mergeable-head.sh`, Gate A), runs the model review passes, and
   completes the authorization check-run with a verdict. It never holds the merge key.
4. **Privileged merge.** The adopter's generated `ai-privileged-merge.yml` and
   `ai-promotion-retry.yml` callers (`gen-privileged-merge-caller.sh`, pinned to one contract
   SHA with the repository's exact required-check IDs) run in the caller's `merge-app`
   environment, mint a token for App 4693283 (`merge-authorization`) from
   `MERGE_APP_PRIVATE_KEY`, revalidate the exact-head authorization verdict and the
   independent-review receipt (ADR 0202), and perform the squash merge as the App. The App is
   an `always` bypass actor on `main-protection` and on the core-checks rulesets, which is
   why a green privileged merge is App-attributed and needs no human approval.
5. **Custody.** Each App key is confined to a main-only repository environment
   (`ai-review-app`, `merge-app`, `release-app`; ADR 0166/0171), reached only through
   `secrets: inherit` on the generated reusable edges. The organization copies with
   `visibility: all` (#1385) are the legacy path this custody model replaced and are withdrawn
   only after every adopter runs from its environment.

Three properties follow from the shape. Hops 2 and 3 share one environment but not one
identity (the arm writes as `github-actions`, the review as App 4528902); hop 4 is a
different App, a different environment, and a different caller. Hop 1 is the only
place where "armed" is decided, and it is a property, not a workflow. The deterministic CI
contexts are required by rulesets that are *siblings* of the arm ruleset, selected by
overlapping properties; they are not upstream of the arm in any workflow graph.

### Was the four-hop chain real coupling or tracker over-linking?

The chain recorded on the tracker was
`#1363 ← #1401 ← #1500 ← verjson-cli#281`. Read against the path above:

- **`#1500` (merge-app credentials and callers in verjson-ci, verjson-compliance,
  verjson-compliance-schema) → `#1401`:** not load-bearing. `#1401` asks that every *armed*
  repository carry canonical deterministic CI (hop 1 siblings), because the arm must never
  govern a repository whose merge could be approved with nothing deterministic required. That
  is a property-and-ruleset question. Hop 4 (privileged merge) is downstream of hop 3 and
  needs the merge key, but a repository can be armed, reviewed, and blocked without ever
  possessing a merge key; the audit in `ai-review-required-workflow-audit.py` does not read
  merge-app environments at all. The audit run on 2026-09-27 from this checkout, recorded on
  verjson-agents#417, confirms it: the only armed repository without deterministic CI is
  `verjson-agents`, whose gap is `verjson-stack=none` plus no `shell-tests` context, and it
  has nothing to do with merge keys. The edge was over-linking: both issues were "blocked
  on the custodian" at the time, and a shared blocker was recorded as a dependency.
- **`verjson-cli#281` (the App registration and custody bootstrap issue, whose remaining
  step was to reconcile bindings and emit a receipt) → `#1500` and `#1499`:** not
  load-bearing, by the owner's ruling and by the path: the receipt is a report about hop 5
  custody, not an input to any hop. Both edges were removed on 2026-09-26.
- **`#1401` → `#1363`:** real. Step 4 of this ADR's transition runs the complete audit and
  requires the target cohort to carry deterministic CI; applying the property while an armed
  repository lacks it would make an unsafe cohort look ready. This edge stays, now bound to
  the specific prerequisite verjson-agents#417 rather than to the merge-key issues.

Net: one of the three upstream edges was real coupling; two were tracker over-linking.
The real chain is `#1363 ← #1401 ← verjson-agents#417`, and `#1500`/`#1499` proceed in
parallel as hop-4 and hop-5 work.

### What this changes in the migration order

Nothing in the transaction. It reorders the prerequisites: regenerate the privileged-merge
callers and run the App-attributed exact-head canaries (#1500) whenever the current
contract SHA is settled, independently of #1401; finish #1401 when verjson-agents emits
`shell-tests` and sets `verjson-stack=actions`; then apply the reviewed
schema/values/ruleset transaction for this ADR with `APPLY-AI-AUTHORIZATION-PROPERTY-1363`
and run the audit; then withdraw organization App keys role by role (#1385) on
environment-custody receipts.

## 2026-10-14 amendment: reviewed cohort identity is case-insensitive

**Status:** Accepted; [#1775](https://github.com/Verjson/.github/issues/1775).

The reviewed migration cohort stopped matching the live legacy cohort on 2026-10-02.
The last green `ai-review-arm` audit was run 36894003929 on 2026-10-01; the first
`legacy arm cohort differs` failure was run 37030718836 at 2026-10-02T15:58Z. The
organization's `updated_at` reads 2026-10-02T13:49Z, and its login now reads `verJSON`.
Two drifts produced the one finding the audit reported:

1. **Display-case rename.** The API now returns `verJSON/<repository>`, while the
   reviewed list records `Verjson/<repository>`. Exact string equality therefore treated
   all 29 reviewed repositories as removed, and all 30 live repositories as unreviewed.
   No membership had changed.
2. **One real omission.** `verjson-document-contracts` carries
   `verjson-core-checks=enforced` and `verjson-stack=node`, so the legacy selector arms
   it. It is not in the reviewed list.

GitHub owner and repository names are case-insensitive identities, and no two
repositories can differ only by case. The audit therefore compares reviewed-cohort
membership by case-folded identity in the legacy and target checks and in the
`unset-values` cleanup subset check. Case folding is sound only over ASCII (U+212A KELVIN
SIGN folds to `k`), so each reviewed entry must be `<organization>/` followed by GitHub's
repository-name charset `[A-Za-z0-9._-]+`. A case-only duplicate is rejected in the
reviewed list, in the observed cohort, and in the organization property inventory. The reviewed list keeps its `Verjson/` spelling, which matches the
contract's `organization`; it is a list of identities, not of display strings. A cohort
mismatch now names the `unreviewed` and `absent` repositories. The previous message
named neither, and the real omission sat behind it for 12 days.

The reviewed cohort adds `Verjson/verjson-document-contracts` (30 repositories). This
follows the Consequences rule above: the arm already selected the repository, so this
records an existing enrollment rather than widening the cohort. The property migration's
`values` payload will enroll it with the others.

The audit then reaches the deterministic-CI check again. Its only finding is the one
already accepted under #1401, now spelled `verJSON/verjson-agents`, so
`config/audit-expected-findings.json` re-records that finding under the new fingerprint.
