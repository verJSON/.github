# 0202 — Bind autonomous merge to an independent-review receipt

- **Date:** 2026-09-23
- **Status:** Accepted
- **Issue:** [Verjson/.github#1383](https://github.com/Verjson/.github/issues/1383)
- **Amends:** [ADR 0098](../0098-require-bounded-ai-review-for-code/README.md), [ADR 0120](../0120-bind-terminal-merge-to-repository-app-token/README.md)
- **Category:** merge authorization (sensitive class)

## Context

The automated AI review and exact-head authorization are necessary but do not record the
independent reviewer pass required by autonomous delivery. Pull requests #1373 and #1375
both completed their App-owned AI authorization before the terminal merge App merged them,
while their independent reviewer passes were still running. No defect reached `main`, but
the procedural review requirement was not part of the merge authorization.

Adding another GitHub App would create identity and key-custody work unrelated to the
missing evidence. GitHub pull-request reviews already provide a review ID, author,
submitted commit, state, and body. Existing maintainers can publish an adjudicated
independent-review result through that surface without granting a new principal authority.
Review bodies and states are mutable, so one read is not durable authorization evidence.

## Decision

Every autonomous terminal merge requires one exact marker line in a `COMMENTED` review:

`<!-- independent-review:v1 pr:<number> head:<40-hex-head> verdict:approved -->`

The terminal merge workflow treats the newest exact-head review from an organization member,
owner, or collaborator account as the governing independent-review verdict. It accepts that
review only when its live state is `COMMENTED`, its entire trimmed body is the canonical
approval marker, its actor is a non-App `User`, and the actor's live repository role is
`maintain` or `admin`. A later `CHANGES_REQUESTED`, `DISMISSED`, withdrawn, edited, or
otherwise non-canonical review supersedes an earlier approval and blocks autonomous merge.

Immediately before merge-token minting, the workflow re-fetches the selected review by ID,
re-lists reviews to prove no later exact-head verdict appeared, and re-reads current actor
permission. Association hints alone are insufficient. A missing, malformed, stale-head,
App-authored, superseded, or insufficiently authorized receipt fails closed before the merge
App token is minted.

The receipt records the central PM's adjudication of an independent reviewer result. It
does not replace the automated provider review, dedicated App approval, required CI,
hold/draft checks, or live-base validation. It also does not change the protected human
merge path: the additional evidence is consumed only by the autonomous privileged workflow.

The publisher uses an existing independently authorized non-App account and the ordinary
pull-request review API. GitHub's `User` account type does not prove a human operator: it can
also represent an account driven through a PAT or other service automation. The trust claim
is therefore limited to a current maintain/admin account distinct from the merge and review
Apps; it is not a proof of human presence. No App permission, private key, ruleset bypass, or
live identity mutation is added. Changing the PR head invalidates the receipt by construction
and requires a new independent review and receipt.

After App-token minting and immediately before `gh pr merge --admin`, the terminal helper
re-fetches the pull request and re-evaluates the same draft, normalized hold-label, and
`DO NOT MERGE` title predicate together with the open state, exact head, authorized base, and
live default-branch ref. A hold added during receipt revalidation or token minting therefore
blocks the merge. GitHub does not offer an atomic transaction spanning those reads and the
merge mutation, so a residual race remains only after that final terminal read.

## Consequences

- The merge App cannot race an in-flight independent reviewer pass.
- Receipt publication plus terminal revalidation is an explicit handoff from reviewer
  adjudication to merge automation.
- A maintainer can deliberately attest a result dishonestly, but that principal already
  controls the protected human merge path; this design adds no new authority.
- Provider approval without an independent-review receipt leaves autonomous promotion red
  and the pull request open for correction or an authorized human merge.

## Verification

`scripts/ci-gate/native-automerge.test.sh` exercises the valid receipt and rejects absent,
stale-head, foreign-PR, fenced, surrounding-context, edited, dismissed, withdrawn,
later-negative, later-created, App-authored, and permission-downgrade variants.
It also injects a hold only after authorization and proves the terminal merge boundary
rejects that late mutation.

## 2026-10-22 amendment — Authorship identity is composite, approval identity is a direct human, and delegation-owner review is a named exception

**What.** This amendment records the attribution policy for agent-authored work. It
changes no gate code and no ruleset, and it does not alter the receipt contract above.

**Why.** The decision above states that a non-App `User` with `maintain` or `admin` is
not proof of human presence. Approval therefore cannot yet be attributed to an
agent-plus-human identity pair, which is why the four sensitive-path reviewer rulesets are
suspended (ADR 0210). The owner's requirement is that the agent identity may do and commit
work as a combined user, model, and agent identity, while the human must prompt as
themselves to sign off a review or admin-merge to satisfy the org requirement of at least
one independent reviewer. The owner approved recording the delegating human as an
acceptable approver as an explicit policy exception. This amendment records that policy.
ADR 0208 (live collaborator permission as the eligibility rule) is unaffected.

### Policy

1. **Authorship identity is composite.** An agent's work is attributed to
   `{human subject, model, agent/run}`. It is carried on commits (trailers), on issued
   credentials, and on evidence. It is modeled with the existing `Actor` of
   [`@verjson/identity-contracts`](https://github.com/Verjson/verjson-identity-contracts)
   (the RFC 8693 `act` chain): the principal `sub` is the human and `act` is the
   agent-run. No new schema is introduced here. `Actor` has no field for the model
   identifier today, so where the model id is recorded is **open** and owned by
   `verjson-identity-contracts`, as is how the agent and the run are told apart within `act`
   (the composite has three parts, `Actor` carries `sub`, `iss`, `type`, and `act`); nothing in
   this record invents either.
2. **Approval identity is a direct human session only.** Agent credentials structurally
   cannot carry review, approve, or admin-merge scope, enforced at issuance and again at
   the gate. An approval counts only when issuer-backed evidence shows a direct human
   session. A missing `act` claim alone is insufficient evidence: this ADR already
   records that a GitHub `User` account does not prove a human operator.
3. **Delegation-owner review is an explicit named policy.** The human who delegated the
   work may approve it. Records and gate output name it `delegation-owner-review` and
   never call it plain "independent review". Safeguards:
   - the approval binds the pull request, the head SHA, the base, and the diff digest;
   - it goes stale on any push or change of base and is rechecked immediately before
     merge;
   - the audit record keeps both the authoring composite identity and the direct human
     approver, even when they are the same person;
   - an agent can never obtain, refresh, or relay an approval or an admin-merge
     credential.

   `delegation-owner-review` is a distinct approval kind and is **not** independent review.
   It counts toward the org requirement of one approving review only as this owner-approved
   exception. No gate accepts it today; the change that re-enables the rulesets defines how
   a gate recognizes it.
4. **Autonomous system actions are represented explicitly**, as a machine subject with
   no human subject. A delegated action that lacks its human subject fails closed.

### Interim state and re-enable trigger

The four sensitive-path admin-review rulesets stay suspended as declared in
[ADR 0210](../0210-code-owner-review-is-real-through-a-generated-codeowners-member/README.md)
(2026-10-22 amendment). Their re-enable trigger is refined from "agent and human
identity attribution exists" to: **direct-human approval events with head-SHA and diff
binding are verifiable.** The `reenable_when` text in
`config/required-reviewer-rulesets.json` predates this refinement and is deliberately
left unchanged here; the change that removes a suspension updates it.

Until then, the receipt above remains evidence for the autonomous merge path only. It is
neither a direct-human approval nor a `delegation-owner-review`, and it is not evidence
for any reviewer ruleset.

### Ownership

| Concern | Owner |
| --- | --- |
| Composite identity schema (`Actor`, `act` chain) | [verjson-identity-contracts](https://github.com/Verjson/verjson-identity-contracts) |
| Agent admission, policy, credential issuance, and revocation execution for agent and run credentials | [verjson-agent-identity](https://github.com/Verjson/verjson-agent-identity) |
| Human-session revocation | [verjson-identity-lifecycle](https://github.com/Verjson/verjson-identity-lifecycle) |
| Customer workflows that call revocation | [verjson-cli-identity](https://github.com/Verjson/verjson-cli-identity) |
| Issuer of the evidence that proves a direct human session | **Open: not yet decided** |

The shared architecture is in
[verjson-docs ADRs 0006–0008](https://github.com/Verjson/verjson-docs/tree/main/decisions),
merged in verjson-docs#18 on 2026-10-22.

### Not covered

No gate verifies direct-human sessions or head and diff binding today, so this policy is
not yet enforced by code. Enforcing it, and the issuer decision, are separate work.
