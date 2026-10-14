# 0227 — The authorization arm bootstraps stale default-branch review callers

- **Date:** 2026-10-13
- **Status:** Accepted
- **Issue:** [verJSON/.github#1696](https://github.com/verJSON/.github/issues/1696)
- **Extends:** [ADR 0079](../0079-head-bound-ai-authorization-and-native-auto-merge/README.md), [ADR 0145](../0145-recover-zero-provider-review-preflight/README.md), [ADR 0166](../0166-environment-only-app-private-keys/README.md), [ADR 0206](../0206-ai-authorization-property-is-independent/README.md)
- **Category:** Merge-gate authorization (sensitive class)

## Context

GitHub dispatches the receipt-bound review from the consumer's protected default branch,
so a pull request never authorizes itself with the caller it proposes. That is the point
of the design, and it becomes a deadlock when the default-branch caller is pinned to a
canonical revision whose verifier no longer accepts current GitHub data.

GitHub now reports the organization as `verJSON`. Every canonical revision before
`65529002` compares repository identity case-sensitively: `68b823b1` (verjson-cli)
requires the required-workflow rule source to equal `Verjson`, and `2d1ea177`
(verjson-git-runners, verjson-cli-cloud) compares the arm run's repository full name
exactly. Their reviews fail closed with `arm run provenance mismatch` for every pull
request, including the generated caller update that would repair them (verjson-cli#302
run 37379607690; verjson-git-runners#237 run 37383712808).

None of the existing paths can move such a caller:

- ADR 0145's zero-provider recovery reruns the same run at the same revision.
- Dispatching the proposed caller from its branch cannot reach the `ai-review-app`
  environment, whose deployment policy admits only `main` (ADR 0187), and the reusable
  review refuses a non-protected ref.
- A workflow on this repository cannot complete a consumer's check: its token is
  repository-scoped.
- Admin merge would bypass the authorization the gate exists to require.

The organization ruleset runs `gate-rearm.yml` at the revision stored in the rule, not at
the consumer's pin. The arm is therefore the one place where current canonical code
already runs in the consumer, with the consumer's `ai-review-app` environment and the
check-creating Actions identity.

## Decision

On `opened`, `reopened`, and `synchronize` arm runs, after the arm creates its exact-head
`AI review authorization` check and publishes the immutable receipt, it classifies the
pull request with an inline proof. If the proof holds, the arm completes the check itself
instead of dispatching the stale review. If the proof fails for any reason, including an
API error, a malformed response, or a missing interpreter, the arm dispatches the ordinary
review unchanged. Explicit `ai-review` and `re-review` label deliveries always take the
ordinary dispatch.

The proof admits a pull request only when all of these hold:

1. **Identity.** The pull request is open, its head is the receipt head, it targets the
   default branch from the same repository, the default branch is protected, and the head
   descends from the protected tip (`compare` reports `ahead`, `behind_by` 0, and the tip
   as merge base).
2. **Complete diff.** The changed paths are computed from the two exact recursive trees,
   which must not be truncated or repeat a path. That set must equal the pull request's
   file list and its `changed_files` count, which is at most 32. Every changed file is a
   regular `100644` blob of at most 256 KiB whose bytes hash to its git object ID.
3. **Generated caller.** `.github/workflows/ai-review-merge.yml` changes in place. The
   default-branch bytes equal the generated caller at the stale revision they name. The
   head bytes equal the generated caller at the new revision they name. The two
   revisions differ.
4. **Canonical revisions.** On `Verjson/.github`, the stale revision does not contain
   `65529002`, the casing repair, so the lane closes once a consumer is current. The new
   revision contains `MINIMUM_SAFE_REVIEW`, strictly descends from the stale revision, and
   is reachable from canonical `main`. Every canonical comparison must report the exact
   base it was asked about. `MINIMUM_SAFE_REVIEW` is currently `12098a0e` (#1700). It
   fixed #1650, where the finalizer did not bind the check to the exact head, and it
   contains the casing repair. A revision from `65529002` up to but not including that fix
   would otherwise be approved without model review while carrying a known authorization
   defect. The constant is raised whenever a review-path security fix lands on `main`.
5. **Companions.** Every other modified file lies outside `.github/`, is not `action.yml`
   or `action.yaml`, and changes only by swapping two tokens: the stale revision for the
   new one, and the stale caller's SHA-256 for the new caller's. These are the consumer
   contract tests that mirror the pin. At most one file may be added. It must be named
   `NEXT/<date>-issue-<identity>-ai-review-caller-bootstrap.md` and match, byte for byte,
   the fragment `scripts/gen-ai-review-caller-bootstrap-fragment.sh` renders from its
   filename and the two revisions. Deletions, renames, mode changes, other additions, and
   any free text are refused.

The completion step runs the proof again immediately before granting authority. It
requires the same revisions and head, and it confirms the check is this arm's in-progress,
`github-actions`-owned check with the receipt's `external_id`. It then applies the
receipt's authority exactly as ordinary completion does. Under `ai-approve` or `ai-merge`,
the dedicated App approves the exact head. The approval body carries the existing
`ai-review-authorization`, `ai-review-head`, and `ai-review-run` markers plus
`ai-review-bootstrap:<stale>:<new>`. The step reads the approval back and completes the
check `success` with the approval-only `ai-review-authorized:v1:<check>:<head>:ai-approve`
marker, even under `ai-merge`. As a result, `ai-post-merge.yml` and promotion retry never
treat the bootstrap as an autonomous-merge review, whose evidence would have to come from an
`ai-review-merge.yml` run. Under `human` authority,
or when the receipt carries a legacy policy without authority, the check completes
`neutral` and the human path stays open. Any failure before completion leaves the check to
the always-run terminalizer, which records a bootstrap-specific failure.

The App token is minted only after the proof holds, with `contents: read` and
`pull-requests: write`, the same narrowed grant ordinary completion uses. The arm does not
dispatch terminal promotion: the consumer's privileged merge caller is just as stale and
would reject the receipt. The persisted App approval still has ADR 0079's native-merge
effect. Under `ai-approve` or `ai-merge`, the approval satisfies the review requirement on
its own, so GitHub native auto-merge, if the pull request author or any writer enables it,
merges the pull request once every required check is green. Any writer can also merge it
directly. No human approval is involved in either case. That is acceptable only because
every merged byte is generated: the proof leaves nothing for a human reviewer to judge.
Under `human` authority, no approval is persisted, and a human approval is still required.
The receipt is retained, not consumed, so a later current privileged merge can still
verify it.

The proof is embedded in the workflow rather than checked out. Commit `ebe23faf` removed a
checkout at the executing workflow SHA after that SHA turned out not to name a revision of
this repository in every arm context. The caller template inside the proof is therefore a
copy, so the behavioral test compares it byte for byte with
`scripts/gen-ai-review-caller.sh` at five revisions and fails on drift.

## Threat model

| Threat | Control |
| --- | --- |
| Arbitrary pull request content approves itself | Only generator-determined bytes can change: the caller template, a two-token swap of already-reviewed files, and one rendered fragment. Workflow and action configuration, deletions, mode changes, and free text are refused. |
| Hand-written or lookalike caller | Both sides must equal the generated bytes, and the base must be the protected tip. |
| Downgrade or side-branch revision | The new revision must contain the casing repair, descend from the stale pin, and be reachable from canonical `main`. |
| Pinning a revision with a known, already-fixed review-path defect | The new revision must contain `MINIMUM_SAFE_REVIEW`, which is raised with every review-path security fix. |
| A post-repair consumer uses the lane to skip model review | A stale revision that already contains the repair is refused. |
| Head moves, or the base moves under the approval | The proof binds the receipt head and the protected tip, runs again before approval, and approves an explicit `commit_id`. A push after the approval is covered only where the consumer's branch rules set `dismiss_stale_reviews_on_push` and `require_last_push_approval`; checking both is a recipe prerequisite. |
| Forged or replayed check | The arm only completes the check it created in the same run, matching ID, `external_id`, head, App, and in-progress state. |
| Lying or partial API responses | Tree truncation, repeated paths, a file-list or count disagreement, blob bytes that do not match their object ID, a comparison of the wrong base, and malformed JSON are all refused. |
| Authority escalation | Receipt authority can only lower the outcome: no approval under `human`, and an approval-only `ai-approve` marker under `ai-approve` or `ai-merge`. No new App permission, no admin bypass, and no terminal promotion. |
| Proof outage blocks ordinary pull requests | Classification never fails the step, and an unavailable proof keeps the ordinary dispatch. |

## Alternatives rejected

- **Relax receipt validation or accept legacy casing.** This would weaken the verifier,
  and the stale verifier is immutable code at a pinned SHA, so it cannot be changed
  anyway.
- **Rename the organization back to `Verjson`.** That is an owner-level identity decision
  outside this repository. It would also make verifier correctness depend on a casing
  that GitHub can change again.
- **Run the generator fetched from each canonical revision.** This would execute remote
  code in a token-bearing job and make the arm depend on every historical generator.
  Embedded bytes with a drift test keep the trusted computing base to the executing
  revision.
- **Check out the proof at `github.workflow_sha` or `job.workflow_sha`.** `ebe23faf` showed
  that the executing SHA is not reliably a revision of this repository under ruleset
  injection.
- **Allow only the caller file.** Every affected consumer requires a changelog fragment
  and has contract tests that pin the caller digest. A caller-only pull request would be
  approved but could never pass the consumer's required checks without admin merge.
- **Permanent model-free lane for every caller bump.** Current consumers already have a
  working reviewer. Limiting the lane to pre-repair pins keeps it a migration path that
  closes itself.

## Consequences

- A stale consumer can move its review caller through the normal required checks, with
  the existing authorization check and App identity, and without admin bypass.
- The lane only moves `ai-review-merge.yml`. The consumer's other generated callers then
  move in an ordinary pull request that the now-current reviewer authorizes. Until they
  do, `ai-merge` terminal promotion stays manual for that consumer.
- Changing the generated caller format requires updating the embedded template in the
  same pull request. The drift test enforces this.
- **Operator steps (owner-mediated, not performed by this change):** rotate organization
  ruleset 20722935's `workflows[0].sha` for `.github/workflows/gate-rearm.yml` to the merge
  SHA of this change, read it back, then prove the lane on a focused verjson-cli pull
  request before retrying the other consumers. No App, environment, or permission change is
  required.

## Consumer recipe

Prerequisite: read the consumer's default-branch rules
(`gh api repos/<owner>/<repo>/rules/branches/<default-branch>`) and confirm that the
`pull_request` rule sets both `dismiss_stale_reviews_on_push` and
`require_last_push_approval` to `true`. Without them, a push after the App approval could
merge under that approval. Fix the rules first if either is missing; the bootstrap does not
check them. On 2026-10-13, a read-only check found both set on verjson-cli,
verjson-git-runners, and verjson-cli-cloud.

From a clean branch at the consumer's protected tip:

1. `scripts/gen-ai-review-caller.sh <new-sha> > .github/workflows/ai-review-merge.yml`,
   using a canonical `main` commit at or after `MINIMUM_SAFE_REVIEW` (currently
   `12098a0e`). Prefer current `main`. A pin such as `65529002`, which contains the
   casing repair but not the minimum safe review fix, is refused.
2. Wherever a contract test pins the stale revision or the stale caller's SHA-256, swap
   exactly those tokens for the new ones and change nothing else.
3. Add `NEXT/<date>-issue-<identity>-ai-review-caller-bootstrap.md` from
   `scripts/gen-ai-review-caller-bootstrap-fragment.sh <date> <identity> <stale-sha> <new-sha>`.
4. Open the pull request alone. Any other change takes the ordinary, stale path and fails
   closed.

## Verification

`python3 scripts/ci-gate/review-caller-bootstrap.test.py` runs the real classification,
completion, and terminalizer steps against a stubbed GitHub API. It covers 40 refusal
reasons, completion refusals, and authority outcomes. In 48 mutation proofs, each removes
one guard from the workflow and shows that the same case is then wrongly admitted. The test
also compares the embedded template with the generator, and checks that every generated
fragment satisfies `scripts/changelog.py validate`. A read-only run of the extracted proof
against live verjson-cli#302 found that its default-branch caller is byte-identical to the
generated `68b823b1` caller and its head to the generated `65529002` caller. It then refused
the pull request because of its unrelated additions.
