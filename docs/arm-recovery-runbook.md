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

3. **Re-arm each stale head** with one close-and-reopen under the recovery
   rules in [ADR 0255](decisions/0255-gate-w-required-workflow-run-precondition/README.md#decision).
   The author may push a new head instead.

4. **Verify the result.** Confirm a new required run exists on the same head,
   created after `updated_at`, with conclusion `success`. Then confirm the
   assertion passes Gate W.

5. **Record the sweep** on the ruleset change: each pull request, its outcome,
   and every pull request left for escalation.

A run created in the same second as the change counts as stale. Run times have
one-second resolution and `updated_at` does not.
