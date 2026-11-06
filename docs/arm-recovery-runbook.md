# Authorization-arm recovery runbook

The organization ruleset `ai-authorization-arm-required` (20722935) requires the
`gate-rearm.yml` workflow on every enrolled default branch through a `workflows`
rule. GitHub blocks the merge ("base branch policy prohibits merge") until that
required workflow has a successful run on the exact head.
`scripts/assert-mergeable-head.sh` checks the same condition as **Gate W**
([ADR 0255](decisions/0255-gate-w-required-workflow-run-precondition/README.md)).
Gate W exits 3, names the reason, and prints that reason's recovery from
[Recovery by refusal reason](#recovery-by-refusal-reason).

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

3. **Re-arm each stale head per pull request, at merge time.** Whoever owns
   that pull request does one close-and-reopen under the
   [interim recovery rules](#interim-recovery-close-and-reopen) when it is ready
   to merge. The author may push a new head instead. Never mass-close and reopen
   other sessions' pull requests. A bulk reopen stacks arm runs in each pull
   request's concurrency group, which the quiet-family rule exists to prevent.

4. **Verify the result.** Confirm a new required run exists on the same head,
   created after `updated_at`, with conclusion `success`. Then confirm the
   assertion passes Gate W.

5. **Record the sweep** in the [sweep checklist](#sweep-checklist): one entry
   per ruleset edit, listing its stale pull requests at recording time. Each
   owner records their pull request's re-arm on that pull request.

A run created in the same second as the change counts as stale. Run times have
one-second resolution and `updated_at` does not.

### Sweep checklist

One entry per edit to a ruleset carrying a `workflows` rule, newest first. An
entry stays open until no listed pull request remains open and stale.

- [ ] **2026-11-03T16:39:01.138Z — ruleset 20722935** (`ai-authorization-arm-required`;
  edit predates ADR 0255, workflows entry unchanged at `24db445f`).
  - Classified read-only on 2026-11-03 for ADR 0255. Gate W reads `stale` for
    `verJSON/.github#1968` and `verJSON/verjson-oidc-claims-middleware#97`, and
    passes `verJSON/verjson-agents#452`. Every other open pull request in the
    five enrolled repositories that has a required run had its newest one
    created before 16:39:01Z, so Gate W reads it as `stale`. The assertion run on each pull
    request decides; this list is for planning:
    - `verJSON/.github` #1938, #1946, #1950, #1951, #1952, #1955, #1956, #1959,
      #1960, #1961, #1963, #1968;
    - `verJSON/verjson-agents` #450, #455, #461;
    - `verJSON/verjson-git-runners` #237, #239, #242, #244, #245, #246;
    - `verJSON/verjson-oidc-claims-middleware` #93, #94, #95, #96, #97, #98,
      #99, #107, #111, #113.
    - **Stale and failed:** the newest required run on these heads also
      concluded `failure`: `verJSON/verjson-agents#450`, and
      `verJSON/verjson-oidc-claims-middleware` #94, #95, #97 and #98. Gate W
      names `stale` first, but a reopen alone may not clear them. Read the
      failed run's log and fix its cause before spending one of the two
      re-arm attempts.
    - **Absent, not stale:** these pull requests have no required run at all,
      so Gate W reads `absent`: `verJSON/.github` #1941 and #1943, and
      `verJSON/verjson-oidc-claims-middleware` #106 and #110. Use the `absent`
      row of [Recovery by refusal reason](#recovery-by-refusal-reason).
  - Re-arm each one per pull request at merge time under the interim rules, and
    record it on that pull request.

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
- **Never** run `gh run rerun` (including `--failed`) on a repository-local
  re-arm caller either:
  - `ai-review-label-rearm.yml` and `ai-review-lifecycle-rearm.yml`: their
    `rearm / arm` job refuses a replay by design ("local arm deliveries cannot
    be replayed by rerunning a workflow"), so the rerun concludes `failure`.
    Gate C never sets a failed run aside, even once a later run replaces it
    (ADR 0226).
  - `gate-rearm.yml`: a rerun joins the `ai-review-arm-<PR>` concurrency group
    outside the quiet-family check and can displace a pending required run,
    which is the cancellation this runbook recovers from (#1905). The arm
    watchdog's advice to re-run this caller applies only to a base ref with
    no organization arm. On such a ref there is no required run to displace,
    and Gate W does not apply.
- **If the reopen succeeded but Gate C still refuses on cancelled re-arm jobs**,
  for example a lifecycle re-arm the watchdog cancelled together with the
  required run: Gate C sets a cancelled run aside only when a later successful
  run of the same workflow and event replaced it on that head, and the reopen
  does not re-run that caller. The recovery that worked on #2022 is a new head,
  for example an empty commit, pushed under the same rules as a reopen:
  - only after the quiet-family check above, because the push starts the
    required run and the local `gate re-arm` run in one group;
  - it counts as one of the two attempts per head, not as a way around them;
  - the new head runs full CI again and needs a fresh exact-head review
    receipt before a promotion-enabled merge (Gate R, ADR 0257). Once pushers
    are compared, whoever pushed it cannot also be its reviewer;
  - on a Renovate branch, prefer close and reopen, because a pushed commit stops
    Renovate from managing the branch.

  Untried alternative: a fresh lifecycle event on the same head (for example
  `edited`), after the quiet-family check, gives the cancelled caller a later
  run of the same workflow and event. If that run succeeds and the cancelled
  run was never rerun, ADR 0226 should set the cancelled run aside without
  a new head. Record the outcome on #1905 the first time it is tried.
- **If Gate C refuses on a displaced family run after a guard-skipped local
  run.** A regenerated `gate re-arm` caller (F-1R,
  [ADR 0255 amendment 2026-11-06](decisions/0255-gate-w-required-workflow-run-precondition/README.md#amendment-2026-11-06--f-1r-the-local-caller-yields-to-the-required-arm))
  yields where the base ref requires the organization arm. Its run concludes
  `success` with `guard` SUCCESS and `rearm` SKIPPED. ADR 0226 consults only
  the newest family run and needs every check in its suite to be a SUCCESS. A
  guard-skipped run fails that test, so a family run displaced before it stays
  red. In that case the assertion exits `5` on the displaced check, for example
  `rearm / event-policy=CANCELLED`, while Gate W passes. Recover by
  re-delivering the displaced run's event: re-apply the label, or repeat the
  lifecycle transition. Do this after the quiet-family check. A later
  successful run of that same workflow sets the cancelled check aside. If that
  does not clear it, push a new head under the rules above.

Using this recovery against concurrency displacement is interim. It ends once
the event-time guard (F-1R), Gate W, and the F-7 required-only closure are
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
| `unbound`, `ambiguous`, `undated`, `changed`, unreadable rule, duplicate paths | Do not re-arm. The evidence cannot be attributed. Re-run the read-only assertion. If the refusal persists, escalate on #1905 with the message. |

The assertion prints this table's recovery for each reason it refuses on.
`scripts/assert-mergeable-head.required-workflow.test.sh` fails when the
script's copy and this table disagree, so change them together.
