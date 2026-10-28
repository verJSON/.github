# 0079 — Head-bound AI authorization and native auto-merge

> **2026-08-25 identity note:** ADR 0138 records the existing App's rename to the
> organization-neutral `ai-review-authorization` slug. Historical names below remain
> unchanged because they describe the configuration accepted at this decision date.

> **Partially superseded by [ADR 0081](../0081-event-driven-terminal-ai-promotion/README.md):**
> native auto-merge and GitHub-owned CI waiting could not satisfy the organization
> code-owner and last-push approval rules. The head-bound paid-review and App
> authorization decisions remain in force.

- **Date:** 2026-08-08
- **Status:** Accepted
- **Issues:** [#632](https://github.com/Verjson/.github/issues/632), [#650](https://github.com/Verjson/.github/issues/650)
- **Supersedes:** [ADR 0017](../0017-two-stage-ai-merge-gate/README.md) for merge orchestration and [ADR 0063](../0063-required-workflow-events-are-bridged/README.md) for re-arm dispatch
- **Amends:** [ADR 0036](../0036-separate-pr-review-from-privileged-merge/README.md) by replacing run artifacts with an explicit Checks API authorization

## Context

The old gate made a paid review job wait for ordinary CI and made a privileged
job wait again before merging. Those waits occupied runners for work GitHub's
branch policy already performs, and unrelated check transitions could cause a
new gate run and another model charge. A `workflow_dispatch` re-arm also ran on
the default branch rather than the PR head, so it could not replace the failed
required `gate` context. Eight days of this behavior incurred roughly $400 in
model API charges.

The security boundary remains strict: PR-controlled workflow code must not
receive secrets, a verdict for one head must never authorize another, and an
inconclusive or blocking review must not enable a merge.

## Decision

Use `gate-rearm.yml`, loaded by `pull_request_target` from the trusted base, as
the sole automatic arm. For each current same-repository PR head it creates an
`AI review authorization` check-run through the Checks API using a dedicated
GitHub App, not the repository's shared `github-actions` installation. The arm
gives the check a random external ID and publishes an immutable, run-owned
artifact receipt naming the repository, PR, head, check ID, arm run/attempt,
external ID, dedicated App ID/slug, and details URL. It dispatches the
default-branch review workflow with those exact identities. Duplicate events
for a head reuse its existing check and spend no
model tokens. Only an explicit `re-review` label may replace a same-head result;
a changed head receives a new authorization. Fork PRs fail the authorization
without dispatching secrets.

The model workflow has no `pull_request` or `pull_request_target` trigger. It
downloads the uniquely named receipt from the exact arm run, verifies GitHub's
artifact digest and strict receipt schema, and requires the receipt to name the
exact dedicated-App check ID, external ID, head, PR, and run. It validates this
before reading the head, permits the model
only `Read`, `Grep`, and `Glob`, and completes that exact check. Blocking,
missing, malformed, stale, or errored verdicts complete it as failure. A
successful exact-head verdict completes it as success and dispatches the
trusted default-branch promotion workflow.

The promotion workflow repeats the receipt, artifact-digest, arm-run, live PR,
and exact successful dedicated-App check verification. Before the check can
become successful, the same dedicated App uses its receipt-bound token to
submit an approving review for the exact reviewed commit and reads that review
back by ID. A rejected, missing, differently authored, or stale-head approval
completes the authorization as failure. Promotion also requires GitHub's live
`reviewDecision` to be `APPROVED`; `REVIEW_REQUIRED` remains fail-closed. It then requests
GitHub native auto-merge with `SQUASH`. It does not read ordinary CI and does
not merge with a ruleset bypass; GitHub owns waiting for all configured
required checks. Holds, drafts, forks, stale heads, missing permissions, and
failed or inconclusive checks remain fail-closed. Duplicate promotions are
idempotent.

Arm receipts use the repository's 90-day artifact-retention maximum so a
long-lived hold can be removed without another model call. Hold removal reuses
only a successful exact-head authorization whose receipt is still uniquely
available. If that evidence has expired or become ambiguous, the arm fails red
with an explicit administrator-recovery message and never automatically
dispatches another paid review. Recovery must restore independently verified
authorization evidence or require an explicit maintainer `re-review` decision.
Clearing a hold or draft never silently buys another review. A failed or
inconclusive same-head authorization produces an actionable PR notice requiring
an intentional `re-review` label. A receipt-proven in-progress authorization
produces wait guidance and is not redispatched; missing or ambiguous pending
evidence produces administrator-recovery guidance and fails red.

## Ruleset migration and rollback

Create and install a dedicated GitHub App with Checks read/write and pull
requests read/write on each managed repository. Store its numeric ID and slug in `AI_REVIEW_APP_ID` and
`AI_REVIEW_APP_SLUG` organization variables, and its private key in the
restricted `AI_REVIEW_APP_PRIVATE_KEY` organization secret. The private key is
available only to trusted `pull_request_target` arm code and trusted
default-branch completion code; it is never passed to the model action or a
PR-controlled workflow. Receipt artifacts are intentionally readable and are
not bearer credentials: another workflow can copy their nonce, but it cannot
alter the immutable artifact, change the check ID named inside it, or mint or
complete a check as the dedicated App.

The shared `github-actions` slug plus a trusted-looking `details_url` is
explicitly insufficient: any same-repository workflow can reproduce those
fields. The dedicated App identity and exact immutable receipt are both
required. Repository workflow tokens receive no `checks: write` permission.

The pull-request permission is an external deployment prerequisite, not a
workflow-side permission escalation: an organization owner must add
`Pull requests: Read and write` to `verjson-ai-review-authorization`, approve
the permission change, and reinstall or approve the installation if GitHub
requests it. Keep both AI workflows disabled until that installation can mint a
token with `checks:write` and `pull_requests:write` and a controlled PR proves
the exact-head App approval, successful App check, and terminal native merge.
Rollback first disables both workflows, then removes the App's pull-request
permission after restoring the prior human/admin merge path; no model review is
automatically retried during either transition.

Deploy the workflows and generated callers first. **Ruleset migration remains
unavailable** until the dedicated App provisioning blocker
[#641](https://github.com/Verjson/.github/issues/641) is fulfilled and a test PR
head has produced a receipt-bound check from the
exact dedicated App, review has completed that same check, and native auto-merge
has remained pending on an ordinary required CI check. Only then configure the
ruleset to require `AI review authorization` from that dedicated App and remove
the old required-workflow `gate` requirement. Do not require both:
the retired workflow context would keep every PR blocked. Confirm that native
auto-merge and squash merging are enabled and that the promotion credential can
request auto-merge but is not configured as a bypass actor.

### 2026-08-18 clarification — configure adopters before activation (#836)

Issue [#836](https://github.com/Verjson/.github/issues/836) exposed an omitted
adoption prerequisite: generated callers can be present while organization
variables remain unavailable to the repository. Enabling the property in that
state immediately creates a required authorization check that can only fail.

Before enabling `verjson-core-checks=enforced` for an adopter, an organization
owner must prove that every `AI_REVIEW_*` organization variable consumed by the
generated arm and review callers is visible to that repository. This includes the
App identity, authority, provider, model, and budget variables for both the
primary review and re-review paths. Separately verify that
`AI_REVIEW_APP_PRIVATE_KEY` and every provider secret selectable by the primary,
primary-fallback, re-review, and re-review-fallback inputs are visible without
reading or recording their values. The current generated caller inputs bind those
providers to `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `DEEPSEEK_API_KEY`; all
three must therefore be available even when the current policy selects only one.
Variable visibility does not prove secret visibility or that the App is installed
with the required permissions.

The adoption order is therefore: install the App and verify its permissions;
make the complete variable and secret families visible; deploy the generated
callers; then set `verjson-core-checks=enforced` and use a controlled pull request
to prove the arm, exact-head review, authorization, and terminal promotion. The
property is last because it activates the required arm immediately; enabling it
before its organization configuration is reachable creates a required check that
can only fail.

On 2026-08-18, organization metadata showed the complete variable family and all
four required secrets at organization-wide visibility. Controlled adopter runs in
`Verjson/verjson-cli-cloud#328` and `Verjson/verjson-cli-projects#95` then produced
green arm and authorization checks, providing the missing end-to-end evidence.

Rollback reverses that order: restore the immutable old required workflow and
verify its context appears on a test PR before removing the new required check.
Then restore the prior workflow revision and revoke the App private key.
Existing successful authorizations
are head-bound and do not become reusable on another commit during either
transition.

### 2026-08-10 clarification — superseded promotion events are terminal no-ops

Issue [#723](https://github.com/Verjson/.github/issues/723) exposed an expected
workflow-run race: ordinary CI completed for an authorized head after the PR had
already advanced. Terminal promotion must still validate its default-branch and
canonical-workflow provenance, then compare the authoritative live PR head with
the authorized head before verifying the immutable receipt. A closed PR or a
different live head is a successful no-op because there is no authorized
mutation left to perform. The current open head continues through full receipt,
App approval, required-CI provenance, and exact-head merge verification. This
does not make authorization reusable; it prevents obsolete events from
misreporting a security rejection as an operational failure.

## Consequences

- One head pays for at most one automatic review; an explicit maintainer
  re-review is the only same-head exception.
- No gate or privileged runner waits for ordinary CI.
- Ordinary CI can finish before or after review without triggering more model
  work; native auto-merge observes it.
- The dedicated App approval satisfies the organization review rule without
  giving the promotion token a ruleset bypass or weakening review requirements
  for unrelated pull requests.
- Repositories must install the generated trusted arm and promotion callers and
  migrate the required-check identity deliberately.
- Follow-up issue creation and branch deletion can no longer be coupled to a
  privileged synchronous merge. They require separate post-merge automation if
  the repository does not already provide it ([#640](https://github.com/Verjson/.github/issues/640)).

## Contract-test migration

Focused coverage was not discarded with the polling implementation:

| Retired contract | Event-driven replacement |
| --- | --- |
| `gate-queue`, `ci-wait-fail-closed`, `required-checks-skip-poll`, `toolchain-missing`, `self-job-exclusion` | `event-driven-authorization` proves no wait/check loop; `native-automerge` proves CI-pending delegation and fail-closed promotion |
| `gate-rearm`, `required-workflow-provenance`, `entry-workflow-provenance` | `arm-receipt` executes exact run/artifact/digest/check binding mutations; `gate-dispatch` executes dispatch failure handling |
| `hold`, applicable `require-secrets` cases | `native-automerge` executes hold/draft/fork/credential cases; `event-driven-authorization` audits that shared workflow tokens never receive Checks write |
| `dispatch-permission` | `gate-dispatch` and `native-automerge` execute dispatch, idempotency, GraphQL-error, and privileged-token failure paths |
| `reusable-workflow`, `gate-rearm-caller-contract`, `privileged-merge-caller-contract`, `privileged-merge-pin` | both generated-caller contracts retain immutable/thin caller, exact input, secret, runner, canonical target, and default-branch provenance coverage |
| `followup-issues`, `merge-branch-cleanup` | No authorization assertion was silently weakened; the behavior moved to tracked post-merge work in #640 |

Model retry, semantic verdict, review publication, freshness, diff acquisition,
fast-lane classification, target visibility, and budget-exhaustion suites remain
unchanged and continue to run in the merge-gate group.

### 2026-08-11 clarification — remove superseded unregistered extractors (#733)

The migration table above named the current replacement suites, but the retired
`gate-rearm.test.sh` and `hold.test.sh` files remained in the repository. They were
not registered in `scripts/actions-ci-groups.tsv` and still searched for step IDs
removed by this ADR, so their coverage claims were both stale and invisible to CI.

Those files are removed. The registered `gate-hold-disable.test.sh` now carries the
live arm's hold and event-rearm truth table, including normalized hold release,
ready-for-review, title release, unrelated-label suppression, metadata API failures,
empty/null/missing and malformed metadata, closed/merged states, receipt reuse, and
explicit review lanes. The registered `native-automerge.test.sh` now carries every
terminal hold spelling plus empty/null and malformed metadata against the current
privileged promotion step. The caller contract pins the canonical arm's exact
permissions, concurrency, trusted metadata sources, input validation, and absence of
a head checkout, then delegates behavioral claims to the registered arm, event, and
receipt suites that actually own them. Existing authority-envelope, App identity,
exact-head, and required-CI coverage remains unchanged. This is a coverage repair
only; no workflow logic or authority changes.

### 2026-08-11 clarification — provider-neutral verdict confirmation (#739)

Every model adapter now ends at one provider-neutral verdict boundary. Adapters own
their transport envelope, terminal-state checks, usage evidence, and extraction of
exactly one assistant JSON object; they do not independently decide whether that
object authorizes a merge. The canonical validator documents accepted field aliases,
normalizes structured review locations, projects one strict verdict shape, and
enforces blocking/findings equivalence plus non-empty review-first guidance for
sensitive changes.

Confirmation is deterministic and local: it makes no additional model call. Invalid
or ambiguous output fails closed with bounded field-path diagnostics that exclude the
raw model response and pull-request diff. Cross-provider conformance tests pass the
same representative response through the Claude workflow path and the OpenAI and
DeepSeek transport adapters before applying the canonical validator. This keeps model
integration generic without guessing arbitrary prose into an approval.

### 2026-08-13 clarification — prompt canonical verdict field names (#771)

The shared provider-neutral prompt must request the same strict item shapes that the
canonical validator returns and the deterministic renderer consumes. In particular,
`review_first` entries use `{location, why}`; `reason` belongs to blocking findings,
not to the canonical review-first shape. The validator continues to accept `reason`
and `rationale` as compatibility aliases for historical output and ordinary model
variance, but the first-party prompt does not ask providers to spend that flexibility.

Contract coverage binds the shared prompt supplied to Claude, OpenAI, and both
DeepSeek passes to the renderer's `review_first[].why` access, then confirms canonical
and aliased provider outputs converge before publication. This restores the existing
provider-neutral verdict boundary and does not change review or merge authority.

### 2026-08-19 correction — the App's permission envelope was never the defect (#702)

[#702](https://github.com/Verjson/.github/issues/702) reported that the dedicated
App's `pulls/reviews` and check-run mutations returned `403 Resource not
accessible by integration` even though the live installation reported
`checks: write`, `pull_requests: write`, `repository_selection: all`, and not
suspended — language that read as a contradiction of the permission envelope this
ADR requires. It was not: the installation's envelope was correct throughout, and
the defect was entirely in how the completion workflow used its tokens, in four
layers uncovered one at a time by controlled runs against `Verjson/toquorum`:

1. PR-head resolution used `gh pr view --json headRefOid`, a GraphQL call, under
   the least-privilege App token; the App had no Contents access at the time, so
   the underlying read 403'd and the step aborted under `set -e` before any
   approval was attempted (#703 added the App-level `Contents: Read-only`
   permission this ADR's App provisioning omitted).
2. The head lookup was moved to the REST `pulls/{pull_number}` endpoint, but a
   `GH_TOKEN=""` prefix on that call silently substituted the restricted
   workflow token for the dedicated App token (#705, #707, #708 restored the
   intended token on that read).
3. Even with both fixed, a controlled run (31404494311) still 403'd: the job's
   `env:` blocks aliased both the workflow token and the App token through a
   shared `GH_TOKEN` name at different scopes, so it was not reliably provable
   which token a given `gh api` call actually inherited. #711 removed the
   ambient alias entirely — every dedicated-App call now goes through a shared
   `app_api` helper that binds `GH_TOKEN="$APP_TOKEN"` explicitly per
   invocation, and validates the minted App slug and installation ID before any
   mutation. #714 made a failed mutation's phase, operation, and sanitized
   GitHub diagnostics visible on `stderr` instead of swallowing them in command
   substitution.
4. No installation, permission, or App-recreation change was needed at any
   point; the 2026-08-18 installation snapshot (`checks: write`,
   `contents: read`, `pull_requests: write`, `repository_selection: all`,
   updated `2026-08-18T21:47:01Z`) matches the 2026-08-10 snapshot quoted in the
   issue. The "contradicts ADR 0079" framing was the imprecise part, not the
   installation.

**Live proof.** `Verjson/toquorum#596` produced three independent exact-head
`verjson-ai-review-authorization[bot]` `APPROVE` reviews, each matching its
commit via `commit_id` and carrying the `ai-review-authorization:<check-id>`
marker, with the dedicated `AI review authorization` check completing
`success` and the promotion job dispatching native auto-merge:

| Head | Review | Check run | Workflow run |
| --- | --- | --- | --- |
| `2ce5edf3` | submitted 2026-08-14T19:15:17Z | 94870822424 (success) | 31832421537 |
| `2081c8ff` | submitted 2026-08-15T00:17:23Z | 94932616877 (success) | 31853145540 |
| `ffc31da9` | submitted 2026-08-15T16:52:48Z | 95040989359 (success) | 31896699431 |

This closes [#702](https://github.com/Verjson/.github/issues/702) and
`Verjson/toquorum#558`. No further owner or GitHub-UI action is required for
the App's permission envelope.

A separate, unrelated defect surfaced while reproducing this: `toquorum`'s
generated `gate-rearm.yml` caller remains pinned to contract commit
`a6b3ccc0590f4fcfdacd7818279ab3eea6b30155` (2026-08-10), which predates
`deepseek` review-provider support. Since the organization's
`AI_REVIEW_PRIMARY_PROVIDER` variable moved to `deepseek` org-wide on
2026-08-16, every arm run in `toquorum` since then fails immediately with
`unsupported review provider`, before ever reaching the authorization step
this ADR governs (reproduced live 2026-08-19, `toquorum` run 32208257958 on
PR #524). That is a consumer caller-pin-staleness gap, not a recurrence of
this ADR's permission envelope, and is tracked separately
([#933](https://github.com/Verjson/.github/issues/933)).

### 2026-09-12 clarification — retire the remaining unregistered extractors (#1320)

The 2026-08-11 sweep removed two of the retired harnesses; the rest stayed on disk.
`429d441` deregistered them from `scripts/actions-ci-groups.tsv` when it deleted the
`ci_wait` step, so for a month they were files that asserted a topology no longer
shipped and that no CI job ever executed. A stale test that cannot run is worse than
no test: it reads as coverage in review while proving nothing. Eight are now removed,
each because the *step it extracts* is gone rather than renamed:

| Removed | Why the assertion is no longer meaningful |
| --- | --- |
| `ci-wait-fail-closed.test.sh` | Drove the `ci_wait` polling step deleted by `429d441`. The #143 property (an ABSENT required check is not green) is now a promotion property, covered by `native-automerge.test.sh` |
| `required-checks-skip-poll.test.sh` | Pinned ADR 0058 step 6's conditional polling; there is no poll to skip |
| `toolchain-missing.test.sh` | Pinned the 30-minute poll window's toolchain preconditions; the only surviving loop is a bounded four-attempt fetch |
| `self-job-exclusion.test.sh` | Pinned the rollup-subtraction filter over `runs/<id>/jobs`. The #276 invariant survives in a different form and is re-covered below |
| `required-workflow-provenance.test.sh` | ADR 0039's run-attestation matcher was replaced by the arm receipt plus an explicit Checks API authorization |
| `entry-workflow-provenance.test.sh` | ADR 0044's `referenced_workflows` matcher is absent from every production workflow |
| `merge-branch-cleanup.test.sh` | Extracted `gh pr merge --delete-branch`; cleanup moved to `post-merge-reconcile.sh`. The #458 invariant is re-covered below |
| `followup-issues.test.sh` | Behavior moved to `post-merge-reconcile.sh` and is covered by the registered `post-merge-reconcile.test.sh` |

Two live invariants were genuinely uncovered once those files are discounted, so
registered coverage was added **before** deletion rather than after:

- **#276 self-exclusion.** ADR 0081's promotion rejects a `REQUIRED_CHECK_POLICY`
  entry naming a gate check or a gate workflow path, so the gate can never satisfy
  its own readiness. `native-automerge.test.sh` now drives all three check names and
  all three workflow paths; stripping either clause from `ai-privileged-merge.yml`
  turns six cases red.
- **#458 non-fatal cleanup.** A failing head-ref delete must not fail an
  already-merged reconcile. `post-merge-reconcile.test.sh` now forces the delete to
  exit non-zero and requires the documented notice and a zero exit; removing the
  guard turns it red.

`dispatch-permission.test.sh` is the one file kept, because its subject — the
least-privilege split across the gate's jobs and the hardening of the single job
holding `actions: write` — is still live. It is rewritten against the five-job shape
this ADR introduced and registered in `merge-gate`. Its permission assertion is an
exact per-job map rather than the original global `grep -c` counts: a count says one
job holds `actions: write` without saying which, so relocating the grant kept the old
assertion green. The map makes any new job or widened scope a stated change.

`scripts/actions-ci-groups.test.sh` now fails when a `scripts/ci-gate/*.test.sh`
exists but is reachable from neither the manifest nor the hosted-compatibility job.
This is a hard fail rather than a warning because the exemption list it needs already
existed and was already asserted there, so no new infrastructure was introduced to
enable it. That guard is the durable fix; this sweep is the one-time backlog.

## Amendment (2026-09-14, #1331) — the disposition table names deleted files

This ADR's decision is unchanged, and so is every disposition recorded above. The table
is a historical record of a completed sweep, and the eight harnesses it lists —
`ci-wait-fail-closed.test.sh`, `required-checks-skip-poll.test.sh`,
`toolchain-missing.test.sh`, `self-job-exclusion.test.sh`,
`required-workflow-provenance.test.sh`, `entry-workflow-provenance.test.sh`,
`merge-branch-cleanup.test.sh` and `followup-issues.test.sh` — are deleted, as that
section says in prose.

This amendment exists so the statement is machine-readable. [ADR
0181](../0181-adr-coverage-claims-follow-their-tests/README.md)'s check resolves bare
basenames as well as full paths, and a table column header ("Why the assertion is no
longer meaningful") is not a sentence that retires a file. Where a property survived the
deletion, the table and the two bullets below it already name the registered suite that
carries it now; none of that changes.

## Amendment (2026-09-20, #1504) — authorize every hold removal

Only a repository `maintain` or `admin` actor, determined from GitHub's `role_name` field, may clear a draft hold through `ready_for_review`, remove an existing `DO NOT MERGE` title marker, remove a recognized `hold` / `DO NOT MERGE` label, or add an `ai-review` / `re-review` label. The authorization check runs before minting the dedicated review App token. A permissionless untrusted-runner filter skips protected jobs for unrelated label additions and removals. Recognized hold-label additions still reach the protected arm because it must disable native auto-merge. A title edit that adds the marker reaches the trusted arm without hold-clear authorization so it can disable native auto-merge; a title edit that removes the marker requires maintainer/admin authorization. Title edits that leave held state unchanged skip the protected arm. All hold-clear paths retain the exact-head receipt requirement.

## Amendment (2026-10-02, #1682) — compare required-workflow source casing safely

GitHub's live branch-rules response reports the organization source as `verJSON`,
while the gate had required the spelling `Verjson`; run metadata can also report
the repository name with different casing from `TARGET_REPO`. Compare these GitHub
identity strings with ASCII case folding. Continue requiring an organization
ruleset, the canonical `.github` repository ID, `.github/workflows/gate-rearm.yml`,
and the exact `refs/heads/main` source ref. The arm receipt suite covers both live
casing shapes, wrong IDs, foreign organizations, workflow paths, protected refs,
and a Unicode confusable source. This clarifies the identity invariant tracked in
[#1682](https://github.com/Verjson/.github/issues/1682).

The same API casing rule applies to API-backed run provenance in `gate-rearm.yml`,
`ai-post-merge.yml`, and `verify-zero-provider-recovery.sh`: compare only the full
repository slug with ASCII case folding, while retaining exact run and attempt,
workflow path, head, protected branch, and repository-ID checks. Mixed-case fixtures
exercise those paths; Unicode lookalikes remain rejected.

### Amendment (2026-10-27, #1831) — accept exact legacy canonical dispatch receipts

The canonical dispatcher moved from `.github/workflows/ai-review-merge.yml` to
`.github/workflows/ai-review-merge-dispatch.yml`. Post-merge reconciliation accepts
receipts from either exact path only in `Verjson/.github`, so runs already in flight
when the path changed can finish. Adopters continue to require their generated
`.github/workflows/ai-review-merge.yml` caller path. Repository, workflow event,
branch, status, conclusion, head, authorization, and attestation checks remain
unchanged; lookalike paths fail closed.

## Amendment (2026-10-27, #1831) — validate generated contract pins as ASCII

`gen-ai-review-caller.sh` accepts a contract revision only when all 40
characters are lowercase ASCII hexadecimal digits. The explicit character set
keeps Bash validation stable under locale collation; uppercase and Unicode
lookalikes fail. The caller contract test exercises valid and invalid pins under
`en_US.UTF-8`.
