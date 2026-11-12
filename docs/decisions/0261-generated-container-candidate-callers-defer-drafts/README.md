# 0261 — Generated container-candidate callers skip drafts and cancel only on new commits

- **Date:** 2026-11-12
- **Status:** Accepted
- **Issue:** [verJSON/.github#1944](https://github.com/verJSON/.github/issues/1944)
- **Category:** required-check scope (sensitive class)
- **Related:** [ADR 0024](../0024-absent-checks-fail-closed/README.md),
  [ADR 0078](../0078-container-release-and-runner-deployment-contract/README.md),
  [ADR 0223](../0223-draft-pull-requests-defer-heavy-ci/README.md),
  [ADR 0226](../0226-superseded-cancelled-check-runs-do-not-block-admission/README.md),
  [ADR 0229](../0229-required-contexts-need-success-and-finished-runs/README.md),
  [ADR 0253](../0253-generated-node-ci-callers-defer-drafts/README.md)

## Context

ADR 0223 classifies candidate builds as Tier 2 work that waits until a pull
request is ready, and this repository's own `container candidate reusable-call
contract` canary already does. The caller that `scripts/gen-container-candidate.sh`
generates for adopters had no draft handling. Its `validate` job ran the full
multi-architecture candidate build on every draft push, so adopters such as
`verjson-git-runners` spent runner time on draft iteration even where their own
image checks were deferred (verjson-git-runners#247).

ADR 0253 settled the same question for generated Node CI callers, under the
owner's 2026-10-28 direction that draft pull requests run no CI tests and that a
draft skip must never act as a passing gate for a ready pull request. Its
reasoning applies here unchanged:

- **A draft must skip, not fail.** Gate C ignores only failures from cancelled,
  superseded runs, so a draft-time `failure` would stay on the head after the
  ready run passed. Gate A requires a SUCCESS for every required context, so a
  skip never satisfies one.
- **Draft scheduling belongs to the caller, not the reusable workflow.** A job
  skipped inside `container-candidate.yml` still yields a successful call, and a
  default-on guard there would change this repository's canary and any other
  caller on its next pin bump. The caller owns the trigger, as it owns the
  concurrency (ADR 0253).

The generated caller also had no concurrency block, so a new commit left the
superseded head's build running to the end.

## Decision

1. The generated `validate` job's condition is `github.event_name ==
   'pull_request' && !github.event.pull_request.draft`. A draft holds no runner.
   The event-name term stays because a push carries no draft flag: the bare
   guard ADR 0253 emits would run pull-request validation on every push to
   `main`.
2. The generated `pull_request` trigger requests `opened`, `synchronize`,
   `reopened`, and `ready_for_review`. Marking a pull request ready runs the
   whole validation on its head. A `synchronize` while ready runs it again.
   `converted_to_draft` is not requested, because it could only start a run
   that skips everything.
3. The caller gains a workflow-level concurrency block. Its group is
   `container-candidate-caller-<workflow>-pr-<number>` for pull requests and
   `…-run-<run_id>` for every other event, and `cancel-in-progress` is
   `github.event_name == 'pull_request' && github.event.action == 'synchronize'`.
   Only a new commit cancels the run it supersedes. Same-head runs queue behind
   the running run. A push to `main` gets a group of its own and is never
   cancelled, queued, or replaced, so publication behaves as before. The prefix
   differs from the reusable workflows' own `container-candidate-` and
   `container-candidate-publish-` groups, so the nested groups cannot deadlock.
4. The generated contract test asserts each of these lines, and
   `scripts/container-candidate-draft-deferral.test.py` evaluates the generated
   conditions for draft, ready, push, and cancellation events.
5. `container-candidate.yml`, `container-candidate-publish.yml`, and the
   generated `publish` job are unchanged. A hand-written caller that wants the
   same behavior adds the same job guard, trigger types, and concurrency.

## Required checks stay fail-closed

`validate` calls a reusable workflow, so the contexts a ready run reports are
nested: `validate / prepare`, `validate / pull-request-build (…)`, and
`validate / skip-private-node-build`. When the calling job is skipped, GitHub
reports one skipped check under the bare caller job name, `validate`, and none
of the nested contexts. A required nested context is therefore absent on a
draft, and an absent required context fails closed, both natively and under
ADR 0024. This is the shape of ADR 0253's single-job caller, whose required
`ci / build-test` is never reported on a draft. The container caller has no
top-level aggregate like `test-levels`, so ADR 0253's native-merge window for
multi-level callers does not arise here.

The bare `validate` context exists only as a skip, as the bare `publish` context
already does on every pull request. Neither may be a required context. As of
2026-11-12, five repositories run the generated caller: catalog-api, catalog-worker,
marketing-studio, verjson-ci and verjson-git-runners. None of them requires `validate`,
`publish`, or any `validate / …` context, by org or repository ruleset (read through
`gh api` on each repository's `rules/branches/main`). marketing-studio requires
`docker · api` and `docker · web`, which its own `docker-build.yml` produces, not this
caller. The organization rulesets require only `changelog / validate`, `shell-tests`,
`ci / build-test`, `ci / eligibility`, `changelog-contract`, `ci-node22 / *` and
`admission`. So no adopter requires a candidate context yet.

## Consequences

- Adopters get the deferral by regenerating their caller, which already moves
  in step with its `uses:` pin (ADR 0078). Hand-written callers keep their
  current behavior.
- Release-stage multi-architecture validation is unchanged. A push to `main`
  still runs the trusted publisher, which builds, signs, and publishes every
  configured platform. Every ready pull request still runs the full read-only
  validation before it can merge. Drafts cannot merge, so no merged head loses
  validation.
- **Retargeted pull requests.** Changing the base branch fires `pull_request`
  `edited`, which neither GitHub's default types nor this trigger request. A
  retarget therefore starts no run, as before this decision. The head keeps the
  results of its last run, which validated the merge ref against the old base.
  A new commit, a branch update, or a draft-then-ready toggle validates against
  the new base. Requiring branches to be up to date before merging forces that
  update.
- This departs from ADR 0223 in one way. `converted_to_draft` does not cancel a
  ready validation in progress. That spends those minutes so a ready run is
  never cancelled into a cancelled required check (verjson-agents#457), which
  ADR 0253 chose for the same reason.
- It departs from ADR 0253 in two ways. The `validate` guard keeps its
  event-name term (Decision 1). The `publish` job carries no draft guard,
  because its condition already admits only a push to `main`, and its bytes stay
  unchanged.
- A pull request that goes ready, then draft, then ready again on one head runs
  the validation a second time. The second run waits for the first instead of
  cancelling it. This trades duplicate minutes in a rare sequence for never
  cancelling a ready run.
- Re-running a draft-time run replays its draft payload and still skips. The
  `ready_for_review` run is the verification run.
