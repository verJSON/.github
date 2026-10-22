# 0239 — Unified untrusted-execution boundary for UI and Pulumi pull requests

- **Date:** 2026-10-22
- **Status:** Accepted
- **Issue:** [Verjson/.github#1869](https://github.com/Verjson/.github/issues/1869) (ARB change 1)
- **Category:** untrusted PR execution / cloud and package credentials (sensitive class)
- **Extends:** [ADR 0086](../0086-secretless-node-pr-validation/README.md)
- **Related:** [ADR 0234](../0234-route-secretless-trusted-ref-builds-to-the-hosted-lane/README.md),
  [ADR 0235](../0235-public-pull-request-code-uses-ephemeral-runners/README.md)

## Context

ADR 0086 gave `node-ci` a rule: pull-request-authored code never runs in a job that holds
a package, Git, cloud, or OIDC credential. Two sibling reusable workflows did not follow it.

- `pulumi-ci` admitted any same-repository pull request to the credentialed `preview` job,
  which ran `npm ci` with package and Git tokens and then cloud-authenticated Pulumi on the
  PR's code.
- `ui-ci` passed its package and Git tokens to `setup-verjson-node`, which exported them to
  every later step of the job (`GITHUB_ENV`), and checked out with a persisted token before
  any repository command ran.

"Same repository" is not "reviewed": agent-authored and any write-access branch is
same-repository by construction.

## Decision

One classifier, one rule.

**Trusted ref** = `push` or `workflow_dispatch` whose ref is exactly
`refs/heads/<repository default branch>`. A missing default branch, a tag, any other branch,
a fork PR, `pull_request_target`, `merge_group`, and every unlisted event are untrusted
(fail closed). Fork PRs never receive credentials under any option.

### pulumi-ci (this change)

- `validate` is unchanged (credential-free, `contents: read`) and always runs.
- The credentialed `preview` is admitted for a trusted ref with all cloud secrets present
  (unchanged for default-branch pushes), or for a same-repository `pull_request` only when
  the caller sets the new opt-in `preview-environment` AND the admission job confirms
  through the API that the environment exists with a required-reviewers rule that prevents
  self-review. The lookup uses a new optional secret, `environment-read-token`, rather than
  a new `permissions` entry, so existing default-branch callers with narrow explicit
  permissions keep starting; without the secret the reviewed route fails closed. The name is caller-workflow data a PR can edit, so it is constrained to
  `[A-Za-z0-9_][A-Za-z0-9._-]{0,63}` before reaching an output file or API path, and a missing,
  unreadable, or unprotected environment fails closed. The job then runs inside that
  environment, so its reviewers are the human gate. The job's `environment` is derived from
  the event and input, never from a step output: GitHub drops an output equal to a secret
  value and a PR author controls the caller's secret mapping, so an output-carried name
  could be redacted into "no environment". Every route still requires all three
  cloud secrets at admission. The environment is an approval gate, not secret scoping:
  reusable-workflow secret aliases (`gcp-wip`) are caller-mapped and hyphenated names
  cannot be environment secrets.
- `validate` and `preview` check out `github.sha` (for a PR, the merge commit fixed at
  trigger time) rather than the moving merge ref, and the preview log states the executed
  and PR head SHAs. Approval authorizes that run only; a new push needs a new approval.
- Job names (`validate`, `preview-admission`, `preview`) and matrix names are unchanged.

### ui-ci (implemented 2026-10-22)

A first step, `Classify event trust`, applies the same trusted-ref table as pulumi-ci
(a test executes both and requires agreement) and emits a credential mode:

- `job` (trusted ref): the package and Git tokens stay job-wide through the pinned
  `setup-verjson-node` composite, exactly as before.
- `install` (every other event, default): the composite runs with no credential input, and
  the tokens are step-scoped env on the two install steps only, with an env-supplied Git
  credential helper (installed only when a Git token exists). The workflow hands lint,
  typecheck, test, and build no token; it cannot stop install-time code from persisting one
  (`GITHUB_ENV`, files). The checkout no longer persists its token (the default ref
  already fetches the event commit by SHA). A trusted ref keeps the checkout and Git
  configuration unchanged.
- `none` (opt-in `credentialless-untrusted: true`, and always for a fork, a pull request with
  no readable head repository, and `merge_group`, which can carry a fork PR): no package, Git,
  or submodule token at all; the submodule checkout uses the job token. Dependencies must be
  public or vendored.

In `install` mode the checkout still uses `submodules-token`, so a same-repository PR can
add a `.gitmodules` entry for any repository that token reads, and later steps can read the
result; only `none` mode closes that, which is why private-schema adopters are told to treat
`credentialless-untrusted` as the strong setting. The `install` mode is a reduction of
exposure, not isolation: install commands and package
lifecycle scripts still run with the token, and a repository `.npmrc` can interpolate it. Job
`build-test` is unchanged. Repositories that need the strongest guarantee move to `node-ci`
`secretless-pr` (ADR 0086), which this ADR prefers over a third copy of its acquisition
machinery; existing `schema-dir` submodules are not supported there and need a package or
pinned auxiliary-source migration first.

## Tightenings (breaking for some adopters)

| Behavior | Before | After |
| --- | --- | --- |
| Same-repo PR credentialed preview | automatic | only with `preview-environment` |
| Push to a non-default branch preview | automatic | credential-free validation only |
| ui-ci tokens on non-trusted events | job-wide env for every later step | install steps only (or none with `credentialless-untrusted`) |
| `workflow_dispatch` on default branch | validation only | credentialed preview (a widening: dispatch callers now get a live preview) |

## Migration

1. Adopters previewing on PRs: create a GitHub environment in the caller repository with
   required reviewers and self-review prevented (advisable: admin bypass off), pass
   `preview-environment: <name>`, and map `environment-read-token` (fine-grained
   Actions: read on that repository). Other secrets stay mapped as today.
2. Bind the cloud workload-identity trust to the repository and that environment (the OIDC
   `environment` claim) or the default-branch ref, not to the repository alone. This is the
   control that survives a PR editing the caller workflow.
3. ui-ci adopters whose lint, test, or build step needs the package token (for example a
   command that installs or fetches private packages) must move that work into
   `install-command`, or accept `credentialless-untrusted: false` only on trusted refs.
4. Adopters pushing previews from feature branches: dispatch from the default branch or use
   the environment route on a PR.
5. Keep `validate` required. A skipped `preview` reports success, so only `validate` proves
   the PR was checked.

## Consequences and residual risk

- A PR can edit the caller workflow and bypass any reusable-workflow rule, reaching
  repository-scoped secrets (Pulumi and package tokens) directly. Only the OIDC environment
  binding (migration step 2) defends the cloud side; the remaining repository secrets
  (Pulumi and package tokens) stay reachable by a PR-edited caller until the caller moves
  them to environment-scoped secrets in its own job; branch or CODEOWNERS protection on
  workflow files gates the merge, not the PR's own run. The optional
  `environment-read-token` is exposed to the admission step only when a PR names a
  preview environment, so mapping a different secret to it is no new capability.
- The reviewed route runs PR code with credentials after approval; it relies on the reviewer
  reading the diff. Approval of a deployment is not proof of review, and applies to one run.
- Runner placement for `validate` follows ADR 0033 and the #1831 relay backlog, unchanged
  here. A private Verjson repository's unapproved PR still validates on the persistent
  general pool, so a poisoned runner-owned cache could later reach a credentialed run on
  that worker. Moving untrusted validation to an ephemeral lane is tracked by #1831.
- Admin bypass of environment protection is not checked by the admission job.
- Not exercised live: no canary yet proves the API check, empty-environment, or approval
  behavior under GitHub's real semantics. The tests execute the real admission step and the
  real jq predicate against fixture responses and assert the workflow wiring; `environment: ''` for trusted refs is
  assumed to mean no environment.

## Rollback

Revert the change; callers keep working. The `preview-environment` input is ignored by older
copies, which would again auto-admit same-repository PRs.
