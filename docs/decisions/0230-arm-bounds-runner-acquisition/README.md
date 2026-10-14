# 0230 — The authorization arm bounds runner acquisition instead of rerouting its key validator

- **Date:** 2026-10-14
- **Status:** Accepted
- **Issue:** [verJSON/.github#1763](https://github.com/verJSON/.github/issues/1763)
- **Refines:** [ADR 0040](../0040-runner-lanes-and-admission-axes/README.md) (lanes),
  [ADR 0147](../0147-route-untrusted-ci-to-ephemeral-hosted-runners/README.md) (untrusted lane on
  ephemeral hosted runners, with rollback to the persistent pool),
  [ADR 0090](../0090-human-first-opt-in-ai-review/README.md) (the arm never vetoes a human merge),
  [ADR 0164](../0164-bridge-review-lifecycle-deliveries/README.md) (PR-scoped,
  non-cancelling arm concurrency)
- **Category:** Merge-gate liveness (sensitive class: merge-gate behavior)

## Context

verjson-payments run 37352915856 (`gate-rearm.yml`, PR #239, head `87fb6ee1`) has had
`app-key-policy / validate-ai-review-key` queued since 2026-10-05T18:02:27Z with labels
`["self-hosted","general"]` and no runner. Because the workflow's concurrency group
`ai-review-arm-<PR>` does not cancel in progress (ADR 0164), run 38427381694 for the
PR's next head has been `pending` behind it since 2026-10-13, so the required arm
check for that PR can never finish.

The issue proposed that the key validator routes to a lane the repository cannot reach.
The evidence disproves that premise:

- Both `CI_LANE_TRUSTED` and `CI_LANE_FALLBACK` resolve to `["self-hosted","general"]`
  for verjson-payments and verjson-ai alike
  (`gh api repos/verJSON/<repo>/actions/organization-variables`). Neither repository
  has a repository-level override. The organization variable and runner-group endpoints
  return 403 to this token, so group membership is inferred from job placement below.
- verjson-payments ran 14 earlier and later arm runs of the same workflow at the same
  pinned validator (`app-key-environment.yml@ebe23faf`) to success, with this exact job
  on runner group `digitalocean` (`gha-general-*`), including run 37383729366 at
  2026-10-05T22:38Z — four and a half hours later.
- In the stalled run itself, sibling `self-hosted,general` jobs in other workflows were
  placed at 18:02:13Z and 18:02:32Z, either side of the stalled job's 18:02:27Z queue time.
- GitHub's documented 24-hour self-hosted queue limit did not terminate the job. An
  organization-wide scan on 2026-10-14 found four other runs still `queued` after 7–69
  days. Those runs have no unfinished job, which is a different shape from this stall.
  GitHub declared an Actions incident for 18:48–22:49Z that day, 46 minutes after the
  job was queued.

The root cause is a job GitHub accepted but never dispatched, not routing. ADR 0033's
visibility routing would not change the result either: verjson-payments is private, and
the merge gate's own visibility expression (`ai-review-merge.yml`) sends private
repositories to the same `self-hosted,general` pool. Job `timeout-minutes` never counts
queue time, and no workflow expression can bound it.

## Decision

1. **Routing is unchanged.** `validate-ai-review-key` and `arm` stay on the trusted lane
   (`CI_LANE_TRUSTED || CI_LANE_FALLBACK || ubuntu-24.04`). The validator only reads the
   environment-bound key into a `umask 077` temporary file that an `EXIT` trap removes.
   Its trust requirement is the same as the `arm` job that mints a token from the same
   key on the same lane. Moving only the validator to a hosted lane would not remove the
   self-hosted dependency, because `arm` still needs that lane. It would also split one
   credential across two substrates and contradict ADR 0040's rule that fleet placement
   changes through variables, not hard-coded labels.
2. **The arm bounds acquisition in-band.** `gate-rearm.yml` gains a
   `runner-acquisition-watchdog` job that runs beside `app-key-policy` once
   `event-policy` admits the delivery:
   - It runs on the lane `event-policy` uses (`CI_LANE_UNTRUSTED`), with
     `permissions: {actions: write}` only. It has no environment, repository secret, or
     checkout, and it reads only its own run's jobs listing for the current attempt. It
     does hold a write-scoped `GITHUB_TOKEN`, and it is the first job to do so on the lane
     that also runs PR-authored code.
   - That placement is safe only while the lane is ephemeral and GitHub-hosted, as
     ADR 0147 routes it today. ADR 0147 also allows a rollback to the persistent
     self-hosted pool. The job's `if:` therefore admits it only when
     `vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]'`, the same chain `runs-on` resolves,
     equals ADR 0147's exact hosted value `'["ubuntu-24.04"]'`. Any other value skips the
     job before a runner is assigned, including a self-hosted array, a hosted label
     beside a self-hosted one, or a whitespace-reformatted array. **No token is issued
     on a persistent host.** A reformatted hosted value fails safe: it makes the job
     dormant, never misplaced.
   - Each step also starts with a backstop guard on `runner.environment`. Unless the
     runner is `github-hosted`, the step writes a notice and exits 0 without listing or
     cancelling anything. After a rollback the watchdog is dormant and the arm loses
     its bound, so the arm's behavior reverts to the pre-ADR state.
   - It polls every 15 s. A job other than itself in `queued`, `pending`, or `requested`
     for at least **900 s** is a stall. `waiting` is excluded because it is an
     environment's human gate, not runner acquisition. The watchdog exits once the
     final `arm` job holds a runner; from then on, that job's own timeout bounds the run.
   - On a stall, it writes a named `::error title=Runner acquisition stalled::`
     annotation with the job name, labels, and age. It then requests an ordinary
     **cancel** of its own run. The `always()` `arm` job can then complete the
     authorization check as failed, as it already does for any non-successful
     `app-key-policy`, and the PR's concurrency group is released for the next arm.
   - Detection passes the stalled job's id through `$GITHUB_OUTPUT`. An `always()`
     escalation step waits 120 s, then **force-cancels** only if that same job is still
     unassigned. Any other queued job, such as the `arm` job the cancellation queued to
     complete the authorization check, never triggers escalation, so that check is never
     orphaned. An unreadable or malformed listing, a job without a queue time, or a
     missing job id is never treated as a stall, either before or after cancellation.
   - The job is `continue-on-error: true`. Neither `arm` nor `app-key-policy` needs it,
     so a watchdog defect cannot turn the required workflow into a merge veto
     (ADR 0090). In a stall, the effect comes from cancellation, not from this job's
     result. Its 40-minute timeout covers two sequential stalls at the limit, the policy
     jobs between them, and the grace period.
3. **The 900 s limit.** Arm jobs normally acquire a runner within seconds. GitHub's
   2026-10-05 incident had 26.5 % of hosted jobs miss a 5-minute start, so 15 minutes
   tolerates an incident-length delay and a future autoscaled cold start. It still turns
   an indefinite hold into a named, re-runnable failure within about 17 minutes.

`scripts/ci-gate/arm-runner-acquisition-watchdog.test.py` (registered in `merge-gate`)
executes both real steps against stubbed `gh`, `date`, and `sleep`. The `gh` stub fails
any scenario that requests a path other than this run's own attempt listing and its
cancel and force-cancel endpoints. The scenarios cover:

- jobs that acquire runners, with and without the reusable caller's job-name prefix;
- a stalled validator, a stalled `arm`, and a stall shorter than the limit;
- environment waiting, unreadable and malformed listings, and jobs without `created_at`
  or with null labels;
- a cancel that takes effect versus one that does not, including the full grace period
  before force-cancel;
- a different job queued at re-check time;
- dormancy off GitHub-hosted runners;
- the job `if:` evaluated against stubbed `vars`: unset, the exact hosted value, a
  self-hosted array, a mixed array, a whitespace variant, and an unused fallback
  variable. Each is cross-checked against the lane the `runs-on` chain resolves.

It also pins the watchdog's lane, permissions, and non-veto wiring, and checks that its
timeout covers the worst case.

## Consequences

- A lost dispatch now produces a named failed check and a cancelled arm within about
  17 minutes, instead of a permanent pending state that blocks the PR and every later
  arm run for it.
- **Not verified live:** whether GitHub's cancel or force-cancel endpoints actually
  terminate a lost-dispatch job. Neither could be exercised without mutating another
  repository's run. The verjson-payments owner's cancellation of run 37352915856, which
  is the operational remediation, is the first live receipt. If force-cancel cannot clear
  such a job either, the annotation and the failed watchdog check still name it, and the
  remaining remedy belongs to GitHub.
- Every admitted arm delivery runs one more short job on the untrusted lane, which is
  currently hosted. It normally lasts about as long as the arm takes to reach its last
  job. For private repositories, that is about one more billed hosted minute per arm
  run. This is the deliberate trade-off for liveness of a required check.
- A pool that stays saturated for more than 15 minutes now fails the arm, which can then
  be re-run. Before this change, the arm would eventually have succeeded. Bounded
  liveness for a required check is worth that trade, and the annotation names the
  saturated lane.
- **Accepted residual risk: the watchdog cannot watch itself.** Its own dispatch can be
  lost just like any other job's. That leaves the run exactly as unbounded as it was
  before this decision, and nothing else in the run observes the loss.
- **Accepted residual risk: an unreadable listing holds the group.** If the jobs listing
  stays unreadable, the watchdog keeps polling until its 40-minute `timeout-minutes`
  expires. Until then the run, and with it the PR's arm concurrency group, stays open,
  even after `arm` has finished. The bound then degrades to 40 minutes rather than
  failing open into a cancellation without evidence.
- `gate-rearm.yml` is the workflow that organization ruleset 20722935 runs at a stored
  SHA. This change takes effect only after the owner rotates that ruleset to the merge
  SHA, and the generated callers pick it up on their next repin.
- `ai-review-merge.yml` nests the same `app-key-policy` call and has the same exposure in
  its review run. That run is dispatched rather than required and has its own retry path.
  Extending the watchdog there is follow-up work, not part of this decision.
