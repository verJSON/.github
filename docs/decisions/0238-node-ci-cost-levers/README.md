# 0238 — node-ci cost levers: larger hosted sizes, diff-classified plans, and what stays caller-side

- **Date:** 2026-10-22
- **Status:** Accepted
- **Issue:** [Verjson/.github#1869](https://github.com/Verjson/.github/issues/1869)
- **Category:** runner topology and required-check scope (sensitive class)
- **Related:** [ADR 0222](../0222-documentation-diffs-skip-heavy-actions-ci/README.md),
  [ADR 0234](../0234-route-secretless-trusted-ref-builds-to-the-hosted-lane/README.md),
  [ADR 0147](../0147-route-untrusted-ci-to-ephemeral-hosted-runners/README.md)

## Context

A private adopter spent 3,406 runner-minutes on 58 pull-request runs in one day.
Most of it was repeated work: three lanes each build the app, an optional heavy lane
runs beside the lane that gates it, and a documentation-only diff pays for every
lane. #1869 proposed five canonical levers. Each was checked against what the reusable
workflow can actually own: `node-ci.yml` is one job per call. It cannot see its
sibling lanes, it runs only the caller's `package.json` scripts, and a matrix inside it
would rename the required `<lane> / build-test` context.

## Decision

1. **Larger hosted sizes are admitted for callers outside Verjson (implemented).** The
   ADR 0234 hosted-lane guard accepts `ubuntu-<latest|YY.MM>` optionally followed by
   `-4-`, `-8-`, `-16-`, `-32-` or `-64-` plus `cores`. The list is closed: arm, gpu,
   and arbitrary suffixes remain refused, as does any label mixed with a self-hosted
   label. A label cannot tell a hosted larger runner from a self-hosted one, and the
   credentialed acquisition job also runs on this lane. The organization's own repositories
   therefore keep exactly the reconciled standard image (`runner-admission-reconcile`,
   ADR 0147, which pins `CI_LANE_UNTRUSTED` to `["ubuntu-24.04"]` and allowlists larger
   runners by identity); a Verjson repository needs a reviewed allowlist identity and a
   reconciler change before it may use a larger size. An external adopter selects the size
   through its own `CI_LANE_UNTRUSTED` variable; the owner comparison folds case because the
   canonical login is `verJSON`, and owns the trust of its runner groups.
   The runtime `RUNNER_ENVIRONMENT=github-hosted` checks in the lifecycle rebuild and
   candidate sandbox remain the backstop. Larger machines raise the resource ceiling of
   the sandboxed scripts but not their reach.
2. **A diff classifier selects a lighter plan (implemented).** Two new optional inputs,
   `secretless-ci-non-app-script-plan` and `secretless-ci-non-app-paths`, let a
   `secretless-pr` call run a reduced non-empty plan, still as the same job and so under
   the same `<lane> / build-test` context. The classifier reads the changed-file list
   from the GitHub compare API at the PR's admitted base and head, never from the
   checkout, and fails closed to the full plan on any API error, truncation, empty list,
   or path outside the allowlist; a base or head that is not a 40-hex object name is a
   malformed event and fails the step. Non-application means a path under `docs/`, `NEXT/` or
   `CHANGELOG/`, a top-level Markdown file, or a caller-declared directory prefix; nested
   Markdown is application content until declared. Only `secretless-pr` on a
   `pull_request` event is eligible, never a trusted-ref push. Workflow files and Node package, lockfile,
   registry-config, git and toolchain-pin files can never classify as non-app, whatever
   the caller declares. A declared prefix asserts that nothing under it feeds the Node
   build, so `python/requirements.txt` under a declared `python/` is non-app. The classifier
   judges paths only: it does not see tree modes, so a changed symlink or gitlink under a
   non-app prefix is non-app too. That is a coverage choice, not a security boundary: both
   plans run in the same credentialless sandbox with the same token handling.
3. **Optional-lane ordering stays caller-side (no change).** A reusable workflow cannot
   `needs:` a sibling caller job. An adopter orders an optional heavy lane by writing
   `needs: verify` and `if: needs.verify.result == 'success'` on that caller job. This is
   safe only for lanes that are not required contexts, because a skipped caller job posts
   the wrong check name (see #1869's run 39548139618). This ADR records the pattern.
4. **Worker and shard counts stay in the consumer's scripts (no change).** node-ci
   runs `package.json` scripts and never invokes vitest or Playwright, so it has
   nothing to pass `--maxWorkers` or `--shard` to. A `shards` input would need a matrix
   in the reusable, which renames the required context to `build-test (n)`. Adopters
   shard in their own scripts, or fan out at the caller and aggregate in a required job
   they own.
5. **Build-once artifacts are deferred (open decision).** Lanes build in separate
   sandboxes, and sharing a `.next` build means a new egress path out of the credentialless
   sandbox plus a consume path that treats the artifact as untrusted. That widens the
   secretless guarantee and needs its own decision from the owner before any code.
6. **Sandbox system packages are deferred (open decision).** The sandbox has no sudo
   and no `/var`. Playwright system libraries are part of the contract of the hosted
   `ubuntu-24.04` and `ubuntu-latest` images, and an adopter must not use
   `--with-deps` inside the sandbox. A matching `pg_dump` major (for example a `pg-major`
   input) means installing an apt package before the sandbox starts, which belongs in an
   audited, pinned provisioning step like the existing sandbox provisioning. It is
   not designed here.

## Consequences

An external adopter can trade per-minute rate for wall time on a vetted size without
holding an old pin. The security posture of the secretless lane is unchanged: no input in this
change touches credentials, network policy, or the sandbox. Items 5 and 6 above remain
open and are reported on the issue.

## Amendment 2026-10-22 — concurrency stays caller-side; test levels not yet designed in the engine

ARB change 3 of #1869 proposed `concurrency` groups with `cancel-in-progress` inside the
reusable CI workflows. A disposable private probe repository (two calls of one reusable
workflow in one run, `workflow_dispatch`) established four facts:

- A group that evaluates to the empty string is a **startup failure with no jobs**, at job
  level, at workflow level, and through a `fromJSON(...) || null` object form. A reusable
  workflow therefore cannot make its group conditional on an input.
- `github.job` is `j` in both calls and `github.run_id` is shared, so nothing available to a
  called workflow distinguishes two calls in one run.
- Two calls that resolve the same group in one run cancel each other (`Canceling since a
  higher priority waiting request for <group> exists`); without `cancel-in-progress` the
  pending one is still replaced.
- Callers with several lanes (the case #1869 measured) would hit exactly that.

So a group declared directly in the reusable is either shared by sibling lanes, which
serializes or cancels them, or needs a caller-supplied lane key that still cannot be left
empty when the feature is off. A job-level variant is conceivable (a preceding job exports a
unique per-call value such as `job.check_run_id` through `needs` for the disabled path, and
a caller-supplied lane key plus the pull-request number names the enabled path), but it adds
a dependency to every call, cancels per job rather than per run, and its failure paths are
undesigned; it is **not adopted here**, not proven impossible. **Decision: cancelling
superseded pull-request runs stays at the caller's workflow level**, where one group covers
all lanes. `docs/node-workflows.md` carries the snippet (cancel only `pull_request`; every
other event, including `pull_request_target`, gets a disjoint `run-` group unique to the
run), and `scripts/ci-gate/reusable-ci-no-concurrency.test.py` pins that the reusable CI
workflows and the protected twin declare none and that the snippet is exact.

Probe receipts: repository `pyousefi/ci-concurrency-probe-1869` (private, archived), commits
`0089e5c`, `1cd28ad`, `dfba982`; runs 39749615522 (two same-group calls cancelled each
other), 39750020363, 39750024475, 39750029020 (empty group at job level, workflow level and
`fromJSON || null`: each completed `failure` with zero jobs), 39749867750 (`github.job` and
`github.run_id` identical in both calls). The startup-failure message itself is not
exposed through the API; the evidence is the zero-job failed run.

A typed `test-levels` input is **not** part of this amendment. The script-plan engine is
parsed in three separate Python blocks of `node-ci.yml` and rewritten by string replacement
into the protected twin; continue-after-failure semantics for a non-fail-fast level need a
design of their own and a decision on what `cancel` means for a level once concurrency is
caller-side. Until then the existing `secretless-ci-script-plan` is an ordered, fail-fast
list.
