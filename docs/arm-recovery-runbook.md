# Authorization-arm recovery runbook

The organization ruleset `ai-authorization-arm-required` (20722935) requires the
`gate-rearm.yml` workflow on every enrolled default branch through a `workflows`
rule. GitHub blocks the merge ("base branch policy prohibits merge") until that
required workflow has a successful run on the exact head.
`scripts/assert-mergeable-head.sh` checks the same condition as **Gate W**
([ADR 0255](decisions/0255-gate-w-required-workflow-run-precondition/README.md)).
Gate W exits 3 and names the reason.

A required run cannot be re-run: `gh run rerun` answers HTTP 404 on its
`/actions/workflows/<id>` path, because the workflow is injected by the
organization. Never use an admin bypass to merge over a refused Gate W. This
runbook lists the recoveries that do work.

## Re-arm sweep after a ruleset edit

Gate W treats a required run created at or before the ruleset's `updated_at` as
**stale** (`stale:`). No run field binds a run to the rule's pinned SHA, so a run
from before the edit cannot be shown to run the current rule. GitHub moves
`updated_at` on **every** edit: a pin rotation, a targeting change, or a
#1945/#1956 edit. Each such edit makes Gate W refuse every open enrolled pull
request whose arm ran before it. So every edit ships with a planned sweep:

1. **Plan the sweep in the change that edits the ruleset.** List the affected
   repositories. These are the repositories the ruleset targets: today,
   `verjson-core-checks=enforced` and not `verjson-actions-state=paused`.

   ```bash
   gh api --paginate orgs/verJSON/properties/values --jq '.[]
     | select(any(.properties[]; .property_name == "verjson-core-checks" and .value == "enforced"))
     | select(any(.properties[]; .property_name == "verjson-actions-state" and .value == "paused") | not)
     | .repository_full_name'
   ```

2. **After the edit lands, read its time** from any targeted repository. Use it
   to classify the open pull requests:

   ```bash
   gh api repos/<owner>/<repo>/rulesets/20722935 --jq .updated_at
   gh pr list --repo <owner>/<repo> --state open --json number --jq '.[].number'
   bash scripts/assert-mergeable-head.sh <owner>/<repo> <pr>   # read-only
   ```

   Only a pull request whose refusal reads `Gate W: … stale:` needs the sweep.
   Any other Gate W reason, and any refusal from another gate, is not part of
   this sweep.

3. **Re-arm each stale head** with one close-and-reopen under the
   [interim recovery rules](#interim-recovery-close-and-reopen). The author may
   push a new head instead.

4. **Verify the result.** Confirm a new required run exists on the same head,
   created after `updated_at`, with conclusion `success`. Then confirm the
   assertion passes Gate W.

5. **Record the sweep** on the ruleset change: each pull request, its outcome,
   and every pull request left for escalation.

A run created in the same second as the change counts as stale. Run times have
one-second resolution and `updated_at` does not.

## Interim recovery: close and reopen

Use this when the newest required arm run on the exact head is cancelled
(`Gate W: … cancelled:`), or when the head is `stale` after a ruleset edit. A
close-and-reopen delivers `reopened`, which starts a new required run on the same
head. It can fail, so it is an attempt and not a guarantee. These rules are
[ADR 0255](decisions/0255-gate-w-required-workflow-run-precondition/README.md) §4:

- **Actor.** Only a human, or an App identity whose events start workflows.
  Never `GITHUB_TOKEN`: its close and reopen events start no workflow.
- **Quiet family first.** List the runs of the pull request's whole arm family
  and act only if none of them is `queued` or `in_progress`. The family is the
  required `AI review authorization arm`, `gate re-arm`, `AI review label
  re-arm`, and `AI review lifecycle re-arm`, across every head and pin. A new
  required run that arrives while another family run holds the
  `ai-review-arm-<PR>` group waits in the single pending slot. A later family run
  can then displace it, which is the #1905 cancellation again. These runs report
  no pull-request number, so bind the family by head branch and head repository:

  ```bash
  gh api --paginate "repos/<owner>/<repo>/actions/runs?branch=<head-branch>&per_page=100" --jq '
    .workflow_runs[]
    | select(.head_repository.full_name == "<head-owner>/<head-repo>")
    | select(.path | IN(".github/workflows/gate-rearm.yml",
                        ".github/workflows/ai-review-label-rearm.yml",
                        ".github/workflows/ai-review-lifecycle-rearm.yml"))
    | select(.status != "completed")
    | [.id, .name, .status, .head_sha] | @tsv'
  ```

  An empty result is quiet. If the listing cannot be read or is incomplete,
  quiet is not established: stop and escalate. The check and the reopen are not
  atomic, so recheck after every attempt.
- **At most 2 attempts per head.** After the second unsuccessful attempt, stop and
  escalate on the pull request and on #1905. Do not keep retrying.
- **Verify every attempt.** Confirm a new required run on the exact head
  (`workflow_url` ending `/actions/required_workflows/<id>`) concluded `success`.
  Then run `bash scripts/assert-mergeable-head.sh <owner>/<repo> <pr>` and confirm
  Gate W and the admission evidence pass.
- **Undo the side effects.** Closing a pull request clears native auto-merge, so
  re-request it if it was set. Reopen a Renovate-authored pull request
  immediately. If Renovate posted an "ignored" notice while the pull request was
  closed, recreate it from the dependency dashboard instead.
- **Never** run `gh run rerun` on a required run: it answers 404, because the
  workflow is organization-injected. **Never** use an admin or ruleset bypass to
  merge over the refusal.

Using this recovery against concurrency displacement is interim. It ends once
the event-time guard (F-1), Gate W, and the F-7 required-only closure are
verified fleet-wide. The same actor, cap, quiet-family, and never-rerun rules
stay **permanently** for watchdog and external cancellations. Those are two of
the three owner-ratified exceptions to "never cancel the required run". The
third is one required run replacing another (ADR 0255 §5).

## Recovery by refusal reason

| Gate W reason | Recovery |
| --- | --- |
| `cancelled` | [Interim recovery](#interim-recovery-close-and-reopen), or push a new head. |
| `stale` | [Re-arm sweep](#re-arm-sweep-after-a-ruleset-edit): one close-and-reopen under the interim rules. |
| `absent` | The head has no required run. Check that the repository is enrolled and the base ref carries the rule. Then use the interim recovery. |
| `not successful` | The required run failed or skipped. Read its log and fix the cause before re-arming. |
| `unbound`, `ambiguous`, `undated`, `changed`, unreadable rule | Do not re-arm. The evidence cannot be attributed. Re-run the read-only assertion. If the refusal persists, escalate on #1905 with the message. |
