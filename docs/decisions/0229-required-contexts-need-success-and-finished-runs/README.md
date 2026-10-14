# 0229 — A required context needs a SUCCESS, and an unfinished workflow run on the head is pending

- **Date:** 2026-10-14
- **Status:** Accepted
- **Issue:** [verJSON/.github#1755](https://github.com/verJSON/.github/issues/1755)
- **Extends:** [ADR 0184](../0184-merge-gates-assert-execution-not-absence-of-red/README.md)
- **Supersedes in part:** [ADR 0184](../0184-merge-gates-assert-execution-not-absence-of-red/README.md) (Gate A's acceptance of a `SKIPPED`- or `NEUTRAL`-only required context), [ADR 0226](../0226-superseded-cancelled-check-runs-do-not-block-admission/README.md) (the conditional workflow-run read)
- **Related:** [ADR 0223](../0223-draft-pull-requests-defer-heavy-ci/README.md), [ADR 0222](../0222-documentation-diffs-skip-heavy-actions-ci/README.md), [ADR 0024](../0024-absent-checks-fail-closed/README.md)
- **Category:** merge-gate admission (sensitive class)

## Context

ADR 0184's `scripts/assert-mergeable-head.sh` is the admission check run before every
`--admin` merge. Its Gate A accepted a required context whose every report was
`COMPLETED` with a conclusion in `SUCCESS`, `NEUTRAL`, or `SKIPPED`.

Since ADR 0223 a draft pull request's runs skip the heavy Tier 2 jobs, so the
draft-time run reports required contexts as `SKIPPED`. Marking the pull request ready
creates a new workflow run, but that run reports nothing until it creates its jobs.
In that window the only report for a required context can be the draft-time skip, and
the review of #1754's fix showed a baseline probe on `main` exiting `0` on such a
head (#1755). The run-level conclusion cannot settle the question either: a run whose
guarded jobs were all skipped still concludes `success`, and `event` is
`pull_request` for `opened`, `ready_for_review`, `converted_to_draft`, and `labeled`
alike (ADR 0226, attack A1).

Two candidate rules were weighed:

- **(a) An unfinished workflow run on the head is pending.** This closes the window
  once the ready-time run exists. It cannot close the seconds between the
  `ready_for_review` event and the run's creation, and it says nothing about a skip
  that no later run will replace.
- **(b) A skip is not evidence.** A `SKIPPED` report says that a job's `if:` was
  false, not that the context's work ran on this head. Admitting a skip only when
  "the latest ready-time run skipped it by design" would need to identify a ready-time
  run, which neither `event` nor the run conclusion can do, plus a PR timeline read
  and timestamp comparison that ADR 0226 already rejected as an ordering source.

Evidence that the stricter form of (b) keeps real green pull requests mergeable,
collected 2026-10-14 from live check runs started before each pull request's
`mergedAt`:

- This repository's only required context is `shell-tests`. ADR 0222's
  documentation-only path keeps it as an unmatrixed aggregator that runs and reports
  `SUCCESS`. #1753 (documentation-only) carried `shell-tests` as `SKIPPED` from the
  draft-time run and `SUCCESS` from the ready-time run; #1762 had the same shape;
  #1759 carried a single `SUCCESS`.
- Every organization repository whose `main` declares required status checks
  (37 repositories, 296 merged pull requests sampled, up to 10 each) merged with a
  `SUCCESS` for every required context that was present at merge. None had a
  `SKIPPED`-only or `NEUTRAL`-only required context. The only gaps were 10 pull
  requests in 6 repositories whose required context was absent at merge, which
  Gate A already refuses. In those repositories, the ruleset or check name likely
  changed after the merge.

## Decision

1. **Gate A requires a `SUCCESS` for every required context**, from the producer the
   ruleset binds it to. `SKIPPED` and `NEUTRAL` stay in the passing conclusion set:
   they do not fail a context that also carries a `SUCCESS`, and Gate C still treats
   them as passing for advisory checks. Neither is enough on its own. The passing set
   is not relaxed. The rule merely extends ADR 0226's existing requirement that a
   context with a set-aside cancellation carry a `SUCCESS`. A legacy commit status in
   `success` state counts, as it did before, where the ruleset binds the context to
   no app.
2. **Gate A treats every unfinished workflow run on the head as pending.** It reads
   the run inventory (`repos/{repo}/actions/runs?head_sha=`) for every head. Any run
   whose `status` is not exactly `completed` blocks with exit `3`, naming the run. This
   includes `queued`, `in_progress`, `waiting`, `requested`, `pending`, and any status
   the script has never seen. The script cannot know which contexts a run will report
   until it creates its jobs, so the rule covers every workflow.
3. **The run inventory is validated for every head**, by type and never by
   truthiness, as ADR 0226 validated it on cancelled heads. It must also satisfy two
   new conditions: a run that reports `completed` must carry a non-empty string
   conclusion, and a status must be a string. A malformed record, a foreign head SHA,
   a duplicate check suite, or an unattested or truncated walk exits `1`.
4. **Actions (read) is unconditional.** This supersedes ADR 0226's clause that
   read workflow runs only when a cancelled check run was present. A caller without
   it now faults on every head with exit `1`, and the message still names the grant.

## Consequences

- The draft-to-ready window is refused at both stages. Once the ready-time run
  exists, rule 2 blocks. Before it exists, rule 1 blocks every required context
  whose only reports are the draft-time skips. The residual race before run creation
  affects only advisory checks, which Gate C reads, and only for the seconds before
  GitHub creates the run. Required contexts have no residual window.
- A repository whose required context is legitimately `SKIPPED` at merge time (a
  path-filtered job made required) is refused. The remedy is the one ADR 0222
  adopted: keep the required context as an always-running aggregator that reports
  `SUCCESS` after deciding what to run. A required check that does not run does not
  verify the head. The survey above found no such repository today.
- An unfinished run on the head now blocks even if it would report only advisory
  checks. Earlier, Gate C caught it only after it created its jobs. A run stuck in
  `queued` (an unreachable runner label) or `waiting` (an environment review) blocks
  until it is cancelled or approved. That is the fail-closed direction, and its jobs
  already blocked Gate C once they existed.
- **A run can be pending behind a stuck run on an older head.** A concurrency group
  holds a new run `pending` while an earlier run in the same group is unfinished,
  and that earlier run can belong to an older commit. Live on 2026-10-14 in
  `verjson-payments`, run 37352915856 has been `queued` since 2026-10-05 on an
  older head, and it holds run 38427381694 `pending` on open PR #239's head.
  Refusing is correct, because the head's verification has not run. The refusal
  therefore names each unfinished run with its workflow name, URL, creation time,
  and age, and it states the remedy: cancel the stuck run, or wait for it; never
  widen the admission gate. Those display fields are not validated and select
  nothing. An unreadable field is omitted and never changes the verdict.
- **Annotation output is encoded at one sink.** The refusal now prints workflow-run
  names, and like the check-run names the script already printed, a pull request's
  author controls them from the head. A CR or LF in a name would begin a new line,
  and Actions executes a line beginning `::error::` as a workflow command. Every
  `::error::`, `::notice::`, and `::warning::` line therefore goes through one
  `annotate` function, which replaces control characters with spaces. Matching
  still uses the exact reported names.
- The script cannot run on the head it judges from inside a workflow run on that same
  head, because its own run is unfinished. No workflow invokes it today. It is the
  operator's and agents' pre-`--admin` assertion. A workflow that needs it would
  require a separate decision, not an exclusion variable. The script deliberately
  exposes no environment override (ADR 0184).
- **Known limit, accepted:** an older `SUCCESS` on the same head satisfies a required
  context even after a newer run skipped it, for example ready, then draft, then
  ready again before the new run exists. That `SUCCESS` still verified this exact
  commit, and GitHub refuses to merge a draft. Requiring the newest report to be a
  `SUCCESS` would need run placement for commit statuses, which have no runs.
- **Known limit, accepted:** `actions/runs?head_sha=` includes runs for any branch or
  fork that shares the commit, so a foreign run in progress on the same SHA blocks.
  This errs in the fail-closed direction.
- Callers that document their grant set outside this repository must add Actions
  (read). The agent guidance's admin-merge procedure (`rules/workflow.md` in
  `verjson-agents`) lists Metadata, Pull requests, Checks, and Commit statuses only;
  [verJSON/verjson-agents#424](https://github.com/verJSON/verjson-agents/issues/424)
  tracks adding Actions (read), which after #1755 is required for every head.

## Verification

`scripts/assert-mergeable-head.test.sh` (registered under `merge-gate` in
`scripts/actions-ci-groups.tsv`) adds red-first fixtures for:

- **Refused shapes:** a `SKIPPED`-only required context; the ready-transition window,
  with skips only and the ready-time run queued, which both rules refuse; an
  unfinished run under each of seven statuses, including an unknown one and a
  case-variant `Completed`; `NEUTRAL`-only and `SKIPPED`+`NEUTRAL` required contexts;
  a bound context backed only by another app's `SUCCESS`; an unbound context
  reported only as a skipped check run; and a `queued` run that already carries a
  `success` conclusion, which proves the rule keys on `status`.
- **Refusal content:** a pending run's message names its workflow, URL, creation
  time, and age, plus the remedy line. A run whose display fields are unreadable is
  still refused under Gate A and never becomes an evaluation fault.
- **Admitted shape:** #1753's documentation-only draft-then-ready shape (draft-time
  `SKIPPED` plus ready-time `SUCCESS`, advisory skips, and a `skipped` sibling
  workflow).
- **Fail-closed run reads:** a completed run with no or an empty conclusion; seven
  malformed run records on a head with no cancellation; a truncated walk on a green
  head; and a run-read failure on such a head, which faults and names the Actions
  grant.

Twenty-four mutants were run in a detached worktree, and the suite killed each one:

- removing either rule, or replacing the denylist with an allowlist;
- weakening SUCCESS to SUCCESS-or-NEUTRAL, or `any` to `all`, which over-blocks the
  documentation-only shape;
- matching producers by name instead of by bound app;
- comparing status case-insensitively, or counting only `pull_request` runs;
- dropping or truthiness-checking the completed-needs-conclusion and status-type
  guards;
- degrading a read failure to an empty inventory, or restoring the conditional read;
- selecting unfinished runs by `conclusion == null` instead of by `status` (review
  mutant O2);
- dropping the remedy line, the workflow name, the URL, or the age; and removing the
  `try` around the creation-time parse, which would turn a refusal into a fault;
- leaving the annotation sink unsanitized, stripping only LF, or letting the fault or
  notice path bypass the sink. CR, LF, and CRLF injection fixtures cover Gates A, B,
  and C and the supersession notice.

A read-only replay with the branch script against #1753, #1759, and #1762 as of
each `mergedAt` exits `0`, the same as `main`. Live, all three exit the same as
`main`: `0` for #1759, and `5` for #1753 and #1762 on the post-merge `reconcile`
check. Open #1746 exits `3` on its failing `shell-tests` under both scripts.
