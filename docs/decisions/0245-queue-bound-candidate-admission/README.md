# 0245 — Propose queue-bound candidate admission without merge bypass

- **Date:** 2026-10-28
- **Status:** Proposed
- **Issue:** [verJSON/.github#1945](https://github.com/verJSON/.github/issues/1945)
- **Category:** merge-gate admission and protected-branch authority (sensitive class)
- **Related:** [ADR 0120](../0120-bind-terminal-merge-to-repository-app-token/README.md), [ADR 0223](../0223-draft-pull-requests-defer-heavy-ci/README.md), [ADR 0229](../0229-required-contexts-need-success-and-finished-runs/README.md), [ADR 0235](../0235-public-pull-request-code-uses-ephemeral-runners/README.md)

## Context

The generated container-candidate caller validates draft pull requests. Deferring
its matrix would save resources, but the candidate checks are not required in
every adopter. GitHub may treat a skipped required check as successful, while
ADR 0229 permits skipped advisory checks. A ready or retarget event can precede
creation of the next run. Therefore a draft guard alone cannot prove that a
candidate build ran on the merge candidate.

The current terminal helper invokes `gh pr merge --admin` after authorization.
That privileged path is not evidence that ordinary GitHub merges enforce the
same candidate checks. GitHub reports merge queues available for public
organization repositories and private repositories on Enterprise Cloud; each
adopter's eligibility and actual rules must be verified before enrollment.

## Proposed contract

This is a proposal, not authority to change a live ruleset or a claim that any
queue is operational. The desired result is one platform-enforced route for
ordinary and autonomous merges; no agent uses an administrative bypass.

1. A reviewed, organization-owned required workflow runs on `merge_group` and
   emits one stable, required candidate aggregate from GitHub Actions. The
   policy, executable gate, and workflow identity are pinned to an immutable
   trusted contract, not chosen by the candidate pull request. The required
   workflow's actual event delivery and pin semantics are rollout prerequisites,
   not assumed properties. The aggregate runs even after a failed or cancelled
   prerequisite and fails for any absent, skipped, failed, or unverified build.
2. Validate the synthetic merge-group commit and its base, not just the original
   pull-request head. Bind the reviewable evidence to the repository, protected
   base, merge-group SHA, participating PR heads, trusted workflow identity,
   candidate configuration, and the exact build outputs. A queue group may
   contain multiple PRs; a result for one member is not evidence for the group.
   A changed base, head, configuration, workflow, or group requires new evidence.
3. The admission boundary must prevent candidate-controlled workflows from
   forging its required check or selecting the policy. Candidate code executes
   credentiallessly in the existing untrusted isolation boundary; public PR and
   merge-group execution follows ADR 0235. The protected coordinator does not
   expose App keys, signing credentials, registry writers, or host sockets to
   candidate code. Check identity alone is insufficient to authenticate an
   artifact's provenance; the evidence and trusted coordinator must bind it.
4. The candidate matrix must exercise every policy-required variant and
   architecture. Existing private-package paths that skip PR builds are not a
   successful candidate proof: provide and verify a credentialless acquisition
   contract, or keep the aggregate failing for that adopter. Test and release
   artifacts may move between stages only with digest and provenance checks.
5. The protected target requires the queue and the aggregate, and automated
   identities do not bypass that enforcement. Preserve all existing required
   checks, independent review, hold/draft restrictions, human or code-owner
   approvals when required, and exact-head authorization. Any automation
   credential that can bypass the queue is not an autonomous merge authority.
6. Replace the privileged terminal merge with a non-bypass queue enrollment
   bound to the expected head and authorized base. Enrollment is pending, not
   a merged result. A separate observer confirms the terminal merged state and
   actual head before cleanup or issue closure. Re-read policy and approvals at
   enrollment, and reauthorize when the head, base, PR state, hold, or required
   approval changes. A timeout, malformed response, unavailable evidence,
   cancellation, failed check, or uncertain queue state remains non-success.

The existing direct-admin decision remains in force in the implementation until
this proposal is implemented and reviewed; this document does not change it.
The authorized agent path must still honor the session's no-bypass merge guard.
If accepted, a successor decision must identify exactly which portions of
ADR 0120 and its terminal-merge extensions it supersedes.

## Agent identity and unresolved approval authority

Independent agent evaluation requires distinct verified agent principals and run
identities, scoped authority, and signed provenance bound to the reviewed head,
base, diff, and evaluation evidence. A provider username, an unsigned commit
trailer, or a bot review is not proof of independent identity and cannot be
represented as a human approval. Verification must fail closed when issuer,
registration, authority, revocation, or provenance evidence is unavailable.
This proposal does not claim that the identity service or its evidence archive
is deployed or that such evidence is currently available.

The [agent-identity contract at 993d0a60](https://github.com/VerJSON/verjson-agent-identity/blob/993d0a60593a2993258385587f6e820e787f6f6d/README.md)
states that agent credentials cannot carry review, approval, bypass, or
administrator permissions and that a human approval requires a direct human
session. An agent-authored evaluation and a provider approval event are distinct
acts; the former must not be presented as the latter or as a human attestation.

That contract's direct-session approval requirement may prevent the autonomous
delivery goal under the applicable provider profile. This tension is unresolved:
[Issue #1945](https://github.com/verJSON/.github/issues/1945) must resolve the
admission authority under the applicable provider contract. Identity-backed
autonomous approval depends on the relevant control-plane work in
[verjson-agent-identity#1](https://github.com/VerJSON/verjson-agent-identity/issues/1);
non-bypass protections that do not require that capability need not wait for
the entire identity service. Do not assume a human review is the default for every policy,
or waive a required direct-session approval while the applicable contract
requires it. Any change to that identity contract needs its own authorized,
reviewed decision in the owning repository.

## Threat model and limitations

A pull-request author can control candidate content, workflow files in that
candidate, configuration, the branch tip, and the timing of draft, ready, and
retarget events. Other PRs or base updates can change the queue composition. A
mutable tag, PR-authored check with a familiar name, stale successful run, skipped
job, unauthenticated receipt, or claim about a local mock cannot establish the
required evidence. Tokens, runner state, and platform configuration are separate
trust boundaries; live ownership and GitHub queue semantics must be observed.

Queue availability is plan- and visibility-dependent. Unsupported repositories
must remain explicitly held until an equally strict, independently reviewed
non-bypass alternative exists. A merge queue does not itself provide scoped
credentials, verify artifact provenance, or make privileged runner execution
safe. Adding a required check to PR heads alone does not close the draft-to-ready
race because GitHub can accept skipped results. A queue is only effective when
bypass actors cannot bypass it and the required check runs for every merge group.

## Admission preconditions and operational sequence

The ruleset must require the organization-owned workflow at its reviewed,
immutable path and revision. A same-named status from the GitHub Actions App
is not sufficient: candidate-controlled workflows can use that App too. Bind
any required status to its expected producer, and verify that ruleset-required
workflow enforcement cannot be replaced or omitted by candidate code. The
aggregate itself must have no job-level condition or path filter that can skip
it; absent or skipped prerequisites are failures.

Before a pilot, inventory the actual applicable rulesets and bypass actors.
Restore the protections suspended under #1933 or establish a reviewed,
equivalent enforcement image for the pilot; do not assume suspended controls
are active. A bypass-capable credential cannot be used for autonomous
admission. Configure and test queue grouping so every candidate tree that can
land has passed its own required evidence. Record the merge method, grouping,
concurrency, timeout, removal, and failure attribution in the reviewed pilot
configuration; if the platform cannot enforce this, hold the adopter.

Where the applicable policy requires a direct-human attestation bound to a
queue candidate, a head-only authorization is insufficient. A group rebuild
invalidates the prior candidate-bound evidence. Do not enroll or merge under
that policy until an implementable candidate-attestation sequence, expiration,
rebuild handling, and bounded recovery are verified against the identity
contract. If no such sequence exists, that policy and queue combination stays
unsupported. Independent agent approval requires its own authorized identity
and policy capability; it is not implied by agent evaluation or by this ADR.
Ineligible repositories remain held until a separately reviewed non-bypass
alternative is available.

## Verification before rollout

- Unit and mutation tests reject missing, skipped, failed, cancelled, stale,
  misattributed, duplicate, malformed, or substituted checks and receipts;
  ensure only a trusted aggregate for the exact group can succeed.
- Model draft, ready, retarget, base/head updates, concurrent and reordered
  groups, queue removal, timeout, replay, and unrelated PRs sharing a head.
  Exercise all group members, not only the final PR.
- Test token scope, candidate isolation, ephemeral runner routing, private
  dependency acquisition, publication separation, and every intended matrix
  leg; no simulated check may stand in for live platform or custody evidence.
- Test separation of author and evaluator principals and runs, signed provenance
  binding, scoped authority, revocation and missing evidence. Verify that an
  agent evaluation or provider bot approval cannot impersonate or satisfy a
  required direct-human-session approval.
- In an explicitly authorized disposable public pilot, prove ordinary and
  automated enrollment, required check dispatch, head/base changes, approval
  invalidation, queue rebuild, failure, cancellation, and no-bypass behavior.
  Record exact rules, actors, workflow SHAs, merge-group SHAs, checks, and runs.
  Do not deploy to production or alter billing, runners, or credentials as a test.
- Stage the trusted workflow and observe it before selecting the pilot in a
  reviewed ruleset. Verify ordinary GitHub merges and the autonomous path both
  fail closed; only then defer draft candidate builds for that adopter. Expand
  one eligible adopter at a time using generated contract artifacts.

If validation fails, hold merges; do not remove required checks or enable a
bypass as a rollback. Restore a previously reviewed equally strict policy only
through the authorized ruleset process. Defer #1944's draft deferral until the
selected adopter has this evidence.
