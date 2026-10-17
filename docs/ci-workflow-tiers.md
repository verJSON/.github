# Pull request CI tiers

Run quick feedback whenever a workflow's existing path filters match, including
on draft pull requests. Defer long integration and candidate-build work until
the author marks the pull request ready for review.

## Tier 1: fast checks

Tier 1 includes linting, type-surface checks, quick admission checks, and short
contract tests. These jobs run while a pull request is a draft.

| Workflow or job | Tier 1 work |
| --- | --- |
| `actionlint` and `actionlint reusable-call contract` | Workflow lint and reusable-call checks |
| `Authn required type surface` | Required type-surface build and compatibility check |
| `actions-ci` | `change-scope`, `docs-contracts`, `adr-number-collision`, and `runner-isolation` |
| `CLI projects required package surface` | `admission` validates the exact consumer pull-request identity |
| `runner-isolation` | ADR 0235's runner-isolation contract, on every pull request with no path filter |

## Tier 2: heavy checks

Tier 2 includes full test suites, long integration tests, and candidate builds.
Each Tier 2 job has a job-level draft guard, so GitHub skips it before assigning
a runner. Existing path filters and job predicates remain in effect.

| Workflow or job | Tier 2 work |
| --- | --- |
| `actions-ci` | `shell-test-groups`, `hosted-compatibility-tests`, and the `shell-tests` required-check aggregate |
| `CLI projects required package surface` | Protected full Node CI at both supported Node versions, plus `package-surface` validation |
| `container candidate reusable-call contract` | The reusable candidate build validation in `validate` |

The candidate `publish` job is `workflow_dispatch`-only and is not part of the
pull-request tiering. `pull_request_target` authorization and merge-gate
workflows are also outside this policy.

## Draft and ready transitions

Tier 2 workflows that declare explicit `pull_request.types` include
`ready_for_review` so their skipped work starts when a draft becomes ready.
They also include `converted_to_draft`; per-pull-request concurrency cancels an
in-flight Tier 2 run when the author converts it back to draft. Keep push and
manual-dispatch behavior unchanged.

GitHub's default `pull_request` activity types are `opened`, `synchronize`, and
`reopened`, so `ready_for_review` must be requested explicitly. A skipped job
has a successful status for required-check purposes, but drafts cannot merge;
the ready event must create a new run of the required Tier 2 checks. See the
[pull request event reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)
and
[required-check guidance](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks#handling-skipped-but-required-checks).

Keep workflow classifications and their draft/ready conformance assertions in
`scripts/ci-gate/pr-ci-tiers.test.py`, registered in
`scripts/actions-ci-groups.tsv`.

## Runner placement for pull-request code

Tiers decide *when* a pull-request job runs; placement decides *where*. This
repository is public, so every job either tier starts runs code a pull request
controls and must land on an ephemeral runner: the untrusted lane
`fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]')` or a literal
GitHub-hosted image, never a selector that can fall through to the persistent
self-hosted pool ([ADR 0235](decisions/0235-public-pull-request-code-uses-ephemeral-runners/README.md)).
`scripts/ci-gate/public-pr-runner-isolation.py` enforces that for every job a
`pull_request` event can reach, including jobs in local reusable workflows,
by enumerating each organization variable as unset and set.
Under `pull_request_target`, `workflow_run`, `issue_comment`, `fork`,
`issues`, and `discussion*`, and under the relays `workflow_dispatch`,
`workflow_call`, and `repository_dispatch`, every job that can reach a
persistent runner must instead be listed, by a digest of its reviewed content,
in `scripts/ci-gate/public-pr-runner-isolation.reviewed.tsv`. A relayed job
that materializes, or may materialize, a pull request head cannot be listed at
all. `scripts/ci-gate/public-pr-runner-isolation.pending.tsv` holds the exact,
digest-ratcheted list of canonical reusable workflows and relayed jobs still
pending that change ([#1831](https://github.com/verJSON/.github/issues/1831)).
`actions-ci`'s `runner-isolation` job runs the check inside the required
`shell-tests` aggregate, which fails unless that job succeeded. The standalone
`runner-isolation` workflow also runs it on every pull request and every push
to `main`, because its digests bind tracked files outside `actions-ci`'s path
filter.
