# 0235 — Public pull-request code runs only on ephemeral runners

- **Date:** 2026-10-15
- **Status:** Accepted
- **Issue:** [verJSON/.github#1831](https://github.com/verJSON/.github/issues/1831)
  tracks the deferred work. The finding was verified against the live
  organization on 2026-10-15 and fixed for `actions-ci.yml` in
  [verJSON/.github#1827](https://github.com/verJSON/.github/pull/1827).
- **Extends:** [ADR 0147](../0147-route-untrusted-ci-to-ephemeral-hosted-runners/README.md),
  which made the untrusted lane ephemeral
- **Narrows:** [ADR 0054](../0054-public-repositories-admitted-to-the-general-pool/README.md),
  whose "Residual risk" section accepted public pull-request code on the
  persistent pool
- **Supersedes in part:** [ADR 0047](../0047-fast-lane-runner-variable/README.md),
  for `actions-ci.yml` only
- **Related:** [ADR 0234](../0234-route-secretless-trusted-ref-builds-to-the-hosted-lane/README.md)
  ([#1826](https://github.com/verJSON/.github/pull/1826)).
  Its trade-off paragraph cites ADR 0147's rollback of `CI_LANE_UNTRUSTED` to
  `["self-hosted","general"]`. Decision 2 below withdraws that rollback, so
  0234's fail-closed eligibility guard is the expected outcome of an attempted
  persistent rollback, not an available operating state.

## Context

`Verjson/.github` is public. On 2026-10-15 the organization was configured as
follows:

| Setting | Live value |
| --- | --- |
| `CI_RUNNER_FASTLANE`, `CI_LANE_TRUSTED`, `CI_LANE_FALLBACK` | `["self-hosted","general"]` |
| `CI_LANE_UNTRUSTED`, `CI_RUNNER_UNTRUSTED`, `CI_RUNNER_ISOLATED` | `["ubuntu-24.04"]` |
| Runner groups `DigitalOcean`, `GitHub`, `runner-fleet-recovery` | `visibility: all`, `allows_public_repositories: true` |
| Runner group `verjson-runner-canary` | `visibility: selected`, `allows_public_repositories: true` |
| Fork pull-request approval policy | `all_external_contributors` |

`actions-ci.yml` triggers on `pull_request`, and every job in it selected
`fromJSON(vars.CI_RUNNER_FASTLANE || vars.CI_LANE_TRUSTED || ...)`. By design,
those jobs run the pull request's own scripts and tests. Code from any pull
request, including a fork, therefore ran on the persistent DigitalOcean
`general` pool. That pool also runs the AI review gate, container deployment,
and other privileged jobs. The only remaining control was the click that
approves a fork's first workflow run.

GitHub's guidance is direct. The
[secure-use reference](https://docs.github.com/en/actions/reference/security/secure-use)
says self-hosted runners "should almost never be used for public repositories"
because "any user can open pull requests against the repository and compromise
the environment". The
[runner setup guide](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
recommends self-hosted runners only for private repositories.

ADR 0054 accepted this risk because public repositories had to be able to use
the general pool. This decision keeps that ability for trusted events. It
removes only pull-request execution from the pool.

### Cost (owner goal #1817: minimize hosted spend)

The
[GitHub Actions billing reference](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
states that usage is free "for public repositories that use standard
GitHub-hosted runners". The same page notes that larger runners are always
billed. `ubuntu-24.04` is a standard runner, so this change spends no metered
minutes. The real limit is concurrency. The
[Actions limits reference](https://docs.github.com/en/actions/reference/limits)
caps standard hosted runners at 60 concurrent jobs on the Team plan, shared
across the organization. A full `actions-ci` run uses up to 9 of those slots:
`change-scope`, six `shell-test-groups` cells (already `max-parallel: 6`),
`docs-contracts`, the existing hosted compatibility job, `adr-number-collision`,
and `shell-tests`, some of which run in sequence.

## Decision

**Every job that a `pull_request` event can reach in this public repository
selects an ephemeral runner.** Reachable jobs include those in local reusable
workflows. The job must use either the untrusted lane with a hosted tail and no
persistent fallback, `fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]')`,
or a literal GitHub-hosted image.

1. **`actions-ci.yml` uses the untrusted lane for every job and every event.**
   One selector is the simplest rule a reviewer can check. It is also the only
   event-aware option that `hosted-selector-policy.py`'s reviewed grammar
   permits today without a new allowlist entry. Push validation of `main` now
   runs on the same image its pull requests used, which removes a source of
   drift between the two. Trusted push jobs could have stayed on the pool. They
   do not, because hosted capacity costs nothing here and a split selector adds
   no safety.
2. **The ADR 0147 untrusted family is the only set of variables treated as
   ephemeral.** The six names are `CI_LANE_UNTRUSTED`, `CI_RUNNER_UNTRUSTED`,
   `CI_RUNNER_ISOLATED`, and their `VERJSON_` aliases. This decision
   **withdraws ADR 0147's rollback to `["self-hosted","general"]`**. A rollback
   target for the untrusted lane must be ephemeral: GitHub-hosted, or a
   dedicated ephemeral pool that the reconciler classifies as the `untrusted`
   group. Pointing the lane back at the persistent pool would undo this decision
   for every public repository at once.
3. **A contract test enforces the rule.**
   `scripts/ci-gate/public-pr-runner-isolation.py` evaluates every
   `pull_request` job's `runs-on` the way Actions does for a public repository.
   It follows local `uses:` calls and models job conditions, Actions' implicit
   `success()`, and skipped dependencies. It enumerates every organization
   variable a selector reads in two states: unset, and set to its class value.
   The untrusted family takes the hosted value; every other variable takes a
   persistent value. A chain such as `vars.CI_LANE_UNTRUSTED ||
   vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]'` is therefore a violation.
   A free reference compared to a literal (`vars.MODE == 'shared'`) is also
   tried as that literal. Any other comparison over a free reference (against
   another reference, a function result, or an ordering) evaluates both ways.
   Only standard GitHub-hosted image names count as ephemeral (`ubuntu-latest`,
   `ubuntu-NN.NN[-arm]`, `windows-latest|YYYY|11-arm`,
   `macos-latest|NN[-intel]`), because a self-hosted runner can carry any
   custom label.
   A reusable input that the caller sets from an expression is tried as an
   adversarial value, because a fork can edit the caller. A remote reusable
   call that a pull request can reach, or any selector the checker cannot
   resolve, is undetermined. It never passes as clean.
   The `platform` group runs the checker against the live tree, with its
   pending and reviewed lists, and
   `public-pr-runner-isolation.test.py` covers its behavior with fixtures. Both
   are registered in `scripts/actions-ci-groups.tsv`.
4. **Two canonical workflows are pending, ratcheted exactly by
   `scripts/ci-gate/public-pr-runner-isolation.pending.tsv`.**
   `actionlint.yml` sends public targets to `CI_RUNNER_FASTLANE`, which is
   persistent today. It is reached directly and through
   `actionlint-reusable-contract.yml`. `container-candidate.yml` falls through
   to `CI_LANE_FALLBACK` when the untrusted lane is unset, and it is reached
   through `container-candidate-reusable-contract.yml`.
   `hosted-selector-policy.py` pins both selectors byte for byte, either through
   `REVIEWED_SELECTOR_EXPRESSIONS` or through
   `CANONICAL_CONTAINER_CANDIDATE_EXPRESSION`. Concurrent
   [#1826](https://github.com/verJSON/.github/pull/1826) owns edits to that file,
   so both changes land after it. The required expressions are:

   - `actionlint.yml`: insert
     `github.event_name == 'pull_request' && fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') ||`
     after the private-repository branch.
   - `container-candidate.yml`: replace the final term with
     `github.event_name == 'pull_request' && fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') || fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]')`.

   Each pending row carries the SHA-256 of the job's `runs-on` value. A listed
   job whose selector changes is a violation again, so a row cannot mask a
   worse selector. The checker reports a fixed entry that is still listed as
   `STALE PENDING` and fails. It also fails on any unlisted job that violates
   the rule.
5. **Privileged events use a reviewed-job allowlist, not head detection.**
   `pull_request_target`, `workflow_run`, and `issue_comment` run the base
   workflow with base privileges, and fork activity can trigger them. Under
   any of these events, a reachable job that can resolve to a persistent or
   self-hosted runner fails, including a job reached through a local reusable
   call or a remote callee whose placement cannot be read. It passes only when
   `scripts/ci-gate/public-pr-runner-isolation.reviewed.tsv` lists it. Each
   entry is `path<TAB>job<TAB>review digest<TAB>justification`, keyed by
   workflow path (including the call chain and event), job id, and a SHA-256
   over:

   - the job's full normalized content;
   - the workflow-level trigger block (`on:`, including `types`, `branches`,
     and `paths`), `env`, `permissions`, and `defaults` it inherits;
   - every calling job on the path to it, together with that caller's
     inherited settings;
   - the bytes, at the scanned tree, of every repository path the job
     sparse-checks-out, executes (`bash|sh|python3|node <path>`, `./<path>`),
     or uses as a local action. The scan recurses into composite action steps,
     action entry points, and the scripts those scripts run.

   Any edit to any of these makes the entry `STALE REVIEWED`, which fails
   until someone re-reviews it. A job may execute a path the scanner cannot
   resolve statically: a variable path that is not authored inline, `python
   -m`, or a path inside a checkout whose repository or ref is selected at
   runtime. Its entry then needs an explicit fifth field, `dynamic:
   <justification>`. Without that field the job is unclassifiable and fails,
   and a `dynamic:` field on a fully resolvable job is stale. A remote callee
   must be pinned to a full 40-hex commit SHA; no entry can accept a branch
   or tag, because either can change after review. An entry that no longer matches a job needing
   review is also stale. Jobs that resolve only to standard hosted images pass
   without an entry.

   **Why this replaces pattern detection.** Three review rounds each found
   head-materializing shapes the pattern detector missed. Examples include
   reading `$GITHUB_EVENT_PATH` with `jq` into `GITHUB_ENV` or `GITHUB_OUTPUT`,
   `actions/github-script` calling `downloadTarballArchive`, `git --git-dir`
   fetching `refs/pull`, and `gh repo clone` of the head repository. The set of
   ways a job can obtain head bytes is open-ended. An allowlist of reviewed job
   content is closed: anything new or changed fails until a person reads it.
   Head detection now only prints a `DIAGNOSTIC` line explaining why a job
   looks dangerous. It never decides pass or fail.

   `issue_comment` has no workflow in this repository. The one `workflow_run`
   workflow, `ai-promotion-retry.yml`, is a reviewed entry: it checks out only
   a trusted `Verjson/.github` revision. Workflow artifacts that a reviewed job
   downloads are part of what its reviewer must justify.

6. **Review procedure for adding or refreshing an entry.**
   1. Run `public-pr-runner-isolation.py --pending … --reviewed … .github/workflows`.
      The failing `VIOLATION` or `STALE REVIEWED` line names the path, job, and
      new review digest, and a `DIAGNOSTIC` line gives the head-detection
      result.
   2. Read the whole job and everything it inherits or calls, including the
      trigger block and every repository script and local action the digest
      covers. Record three things: what it checks out (repository, ref,
      paths); what it executes (inline code, repository scripts, actions, and
      any program it writes); and how every PR-derived value and downloaded
      artifact is used. For each path the checker reports as unresolvable,
      state in the `dynamic:` field where its bytes come from at runtime and
      why a pull request cannot choose them.
   3. Prefer moving the job to a hosted runner. List it only when it needs the
      trusted pool and no PR-controlled byte can reach execution: anything
      PR-derived is used only as an API identifier or as validated data.
   4. Add or replace the row with that digest and a one-line justification
      covering checkout, execution, and why PR content cannot reach execution.
      Never relist a stale row without re-reading the change.

### What this decision cannot do

For a `pull_request` run, GitHub reads the workflow file from the pull
request's merge commit. A fork can therefore edit `runs-on:` itself and request
`[self-hosted, general]`. No routing expression in this repository can prevent
that. Routing removes every default path to the pool. The controls that stop a
hostile fork are runner-group admission and the fork-approval review.

The operator option, which this decision does not take, is to set the
`DigitalOcean`, `GitHub`, `runner-fleet-recovery`, and `verjson-runner-canary`
runner groups to `allows_public_repositories: false`. ADR 0054's rollback
section explains the prerequisite. Every trusted job that a public repository
still routes to the pool would then queue forever, with no diagnostic. Those
jobs must move to hosted capacity first: push and schedule jobs on
`CI_RUNNER_FASTLANE` and `CI_LANE_TRUSTED`, the privileged merge path on
`CI_LANE_PRIVILEGED`, and `runner-admission-reconcile`. The owner decides
whether to take this option.

## Consequences

- No default path sends pull-request code in this repository to a persistent
  runner, apart from the two ratcheted entries above. The contract test detects
  a regression to a fallback chain or to a reusable workflow that honors a
  caller runner.
- `actions-ci` push runs move from the persistent pool to hosted capacity at no
  metered cost. They now compete for the organization's 60 standard hosted
  slots.
- The untrusted lane can no longer be rolled back to the persistent pool.
  `runner-admission-reconcile.sh` still accepts a persistent untrusted selector,
  so live enforcement of that rule is a follow-up tracked in #1831.
- The same guarantee for public adopters' calls to the canonical reusable
  workflows is a follow-up tracked in #1831. Those include `node-ci`, `ui-ci`,
  `helm-ci`, `pulumi-ci`, `generated-artifacts`, `repo-hygiene`, and
  `changelog-validate`. It needs the same event-aware branch and
  `hosted-selector-policy.py` allowlist entries.

## Rollback

Revert the pull request. The previous selector puts public pull-request code
back on the persistent pool. Treat that as a security regression and record it
with a tracked cause.
