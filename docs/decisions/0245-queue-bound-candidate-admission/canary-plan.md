# Isolated public merge-queue canary plan

**Status:** Proposed; no separate organization or repository has been created.
**Issue:** [verJSON/.github#1945](https://github.com/verJSON/.github/issues/1945).
This is a platform experiment, not a production admission design or authority to
change VerJSON settings.

## Why isolation is necessary

Both Actions-enabled public VerJSON repositories have default-branch
`pull_request_target` re-arm workflows. Opening a test PR can reach production
review authority and, in one caller, a trusted self-hosted lane. Creating
another VerJSON repository would inherit organization-wide secrets and App
installations. A temporary base branch alone does not isolate those paths.
GitHub documents public merge queues for organization-owned repositories;
a personal public repository is not a supported substitute. The configuration
probes and their limits are recorded in the parent ADR.

## Environment and preflight

An owner may provide a separate GitHub organization and a public test-only
repository. Before any experiment, record the authenticated operator, repository
and organization IDs, visibility, plan, default branch SHA, GitHub Actions
permissions and selected-repository list, installed Apps and their repository
selections, organization and repository secret and variable **metadata**,
environments and deployment policies, runners and runner groups, active
rulesets and bypass actors, and default-branch workflow inventory. Never record
secret values or tokens. Inspect inherited controls, not just repository-local
settings. Stop if any production secret, privileged App, runner, required
workflow, existing data, or unclear ownership remains accessible; a workflow's
choice not to reference a secret does not remove its inherited availability.

Actions must be enabled only for the test repository through a reversible
organization or repository setting. Record the exact preimage and verify the
postimage; do not change billing, other repositories, production settings, or
shared runner configuration. If enabling the test requires altering a shared
scope, stop and obtain a separately reviewed setup. Verify the repository can
use an ephemeral GitHub-hosted `ubuntu-24.04` runner; do not fall back to a
self-hosted lane. Create no App installation, environment, secret, or
credential for the experiment.

## Exact test controls

Create uniquely named temporary target and head branches and record their initial
object IDs. All edits must be test-owned; do not rewrite existing history. Add
only a test workflow with `pull_request` and `merge_group` triggers, a distinct
stable check name, `permissions: {}`, a literal `ubuntu-24.04` runner, no
checkout, no external actions, no secrets, and no deployment. GitHub still
creates a `GITHUB_TOKEN`; the workflow must neither access nor expose it. Its job reports
only the event, test identifier, GitHub-provided source and group identifiers,
and result; it must not log token material. The workflow check is test code,
not a trusted production coordinator or an authenticated receipt.

Create a new repository ruleset matching **only** the exact temporary target
ref, with an empty `bypass_actors` list and these controls:

- Pull request required; one genuine human approval, last-push approval,
  stale-review dismissal and resolved review threads required.
- Merge queue required; `SQUASH`, `ALLGREEN`, build concurrency 1, group
  minimum and maximum 1, minimum wait 0 minutes, and check timeout 60 minutes.
- Require the canary workflow's stable check, bound to the GitHub Actions
  integration. Confirm the effective rule and check producer by readback.

Verify no inherited rule or other App supplies a bypass. The PR author and
approving human must be distinct where GitHub requires it. An agent, bot,
shared account, existing receipt, administrator bypass, or self-approval is
not a substitute for a required human approval. A human approval is not
assumed: without it, observe the blocked state and stop before any positive
merge that requires it. Never weaken the review or check rule merely to make
the test complete.

## Test sequence and evidence

1. Before approval, create the controlled PR and observe GitHub's reported
   review and mergeability state read-only. Do not request a merge or queue
   enrollment while any required human approval or check is missing or stale.
2. After a real eligible human approval, refresh the exact PR head, applicable
   rulesets and actors, review decision, every required human, code-owner and
   last-push approval, and all checks applicable to the PR head. Stop if any
   required check is missing or not green, or any approval evidence is stale.
   A merge-group check normally cannot exist before enrollment. Record how the
   provider reports it; do not treat a missing required check as successful.

   An ordinary unqueued merge API negative probe is permitted only if every
   applicable required check is already satisfied, including any applicable
   merge-group requirement, and all approvals still apply to the captured head.
   If GitHub cannot produce that unqueued state, record the probe as untested;
   do not issue the merge request. Otherwise use a non-bypass actor and the
   expected-head SHA on the disposable target. GitHub may reject or enqueue it
   depending on the API. An unexpected direct merge or ambiguous response is
   a stop-and-record condition. If it enqueues the PR, observe that entry
   instead of issuing a duplicate. Never use `--admin`, `--auto`, or a bypass
   App to advance it.

   If no entry exists, enqueue the exact head only after freshly checking the
   same approval and PR-head check gates and confirming GitHub reports no
   required check missing at that stage. Once a merge group exists, require its
   checks and every other applicable required check to succeed before any
   actual merge. A failure, absent result, or ambiguity is a hold, not green CI.
   Record repository, PR and provider-returned queue identifiers; PR head and
   target base SHAs; merge-group head and base refs and SHAs; workflow ID,
   event, run ID and attempt; all required check conclusions; ruleset revision
   and actors; and timestamps. Record a native merge-group ID only if GitHub
   exposes one. Missing or ambiguous required evidence fails the experiment.
3. In separately authorized controlled cases, exercise a changed head, a base
   advance and a two-PR queue using the same review and check requirements.
   Record queue membership, group rebuild or removal, approval state, check
   invalidation, and final outcomes. Do not assume that head-only approval
   covers a new group or that a stale success will be rejected.
4. If a merge occurs, fetch the actual target-ref update and landed commit;
   record its SHA, tree, parents, PR merge identity, and the tested group commit
   and tree. Compare the landed **tree** to the tested group tree, accounting
   for the selected squash method. A successful check or PR head alone does
   not prove which tree landed. Any mismatch or unobservable transition fails
   the experiment and blocks a production claim.

Keep a sanitized evidence bundle containing API responses and workflow/check
metadata, their hashes, exact source and policy revisions, outcome, operator,
and explicit gaps. Hashes establish consistency of retained files; they do not
by themselves authenticate GitHub responses. Redact secrets and retain
sufficient access-controlled original receipts for independent review. Capture
negative, canceled, missing-check and failed-check cases as well as any success.

## Stop and teardown

Stop before mutation if ownership, inherited authority, target scope, or a
cleanup plan is unclear. Stop during execution on unexpected rules, a non-test
actor, unavailable evidence, a moved test ref, unapproved bypass, or inability
to identify all resources created by this run. Preserve evidence and do not
remove the affected resource when its ownership or contents are uncertain.

After the experiment, close any test PR, remove queue entries, and verify their
terminal states. Remove only the exact test-owned ruleset, workflow and branch
refs after verifying their IDs, expected contents and absence of other data.
Read back each deletion and compare pre/post existing-rule inventories and
non-test refs. Closed PRs and check history remain durable GitHub records;
they are not represented as deleted. Retain the test organization and
repository unless their owner separately authorizes deletion and evidence
retention is verified. Restore any permitted Actions setting to its recorded
preimage and verify readback. Never delete the only forensic evidence.

A successful public-org experiment demonstrates only the observed platform
behavior in that environment. Production rollout still requires independently
reviewed VerJSON-specific required-workflow identity, candidate provenance,
runner and credential isolation, actual ruleset and bypass-actor inspection,
applicable human approvals, and private-repository admission. The observed
private queue-rule rejection does not prove a plan-specific cause or establish
an alternative admission path.
