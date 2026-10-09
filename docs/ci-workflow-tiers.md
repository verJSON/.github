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
| `actions-ci` | `change-scope`, `docs-contracts`, and `adr-number-collision` |
| `CLI projects required package surface` | `admission` validates the exact consumer pull-request identity |

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
