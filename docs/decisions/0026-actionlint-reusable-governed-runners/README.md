# 0026 — Reusable actionlint offers only governed runner choices

- **Date:** 2026-07-27
- **Issue:** [Verjson/.github#153](https://github.com/Verjson/.github/issues/153)
- **Category:** reusable CI / runner topology (sensitive class)

## Context

`.github/workflows/actionlint.yml` deterministically installs actionlint 1.7.7,
verifies its Linux archive checksum, and lints this repository on workflow or
actionlint-config changes. It could not be called by consumer repositories, so
they either copied the workflow or omitted deterministic Actions validation.

Making it reusable introduces two load-bearing boundaries. First, `runs-on` is
resolved in the caller's repository, so a free-form JSON label input would let
every caller invent a runner topology and make the shared contract impossible to
reason about. Second, called workflows do not control whether a caller's
path-filtered required check exists: an unmatched caller workflow produces no
check run and can leave branch protection pending indefinitely.

## Decision

Keep the existing `pull_request` and main-branch `push` triggers in the same file,
and add `workflow_call` as a third entry path. The local paths, GCP runner,
actionlint version, checksum, and repository-wide lint command remain unchanged.

Expose one boolean input, `github-hosted-runner`, instead of accepting arbitrary
runner labels:

- `false` (the default) selects `["self-hosted","GCP"]`, preserving the local
  route and the safe Verjson org default;
- `true` selects the fixed GitHub-hosted image `ubuntu-24.04`.

The boolean makes unsupported runner states unrepresentable at the workflow
boundary. Adding a new governed pool requires a reviewed workflow change rather
than an unbounded caller string. Consumer documentation shows an immutable
full-commit-SHA call and requires `contents: read`; nested Actions are likewise
full-SHA pinned.

Trigger policy stays with the caller. A caller may path-filter workflow/config
changes, but that called check must not be configured as required for paths where
the caller does not run. A required actionlint check instead needs an unfiltered
caller trigger. The documented stable caller and called job IDs produce the
`actionlint / actionlint` check context; renaming either changes that context.

`scripts/actionlint-reusable.test.sh` structurally pins the entry paths, bounded
runner mapping, full-SHA nested Action, deterministic version/checksum, and test
wiring. The workflow runs the real pinned binary against isolated inline valid,
malformed-YAML, and invalid-expression fixtures before the repository lint, so
both local and reusable executions prove the failure contract.
`.github/workflows/actionlint-reusable-contract.yml` is a real caller pinned to
the review-hardened implementation commit `bfecdd0111582d0ddada558e6b4d0cadd9b488bd`;
its path-filtered PR run proves the GitHub-hosted reusable-call seam end to end.

## Review hardening (2026-07-27)

Independent review found that the first draft invoked
`scripts/actionlint-behavior.test.sh` after checking out the caller. A reusable
call that kept the default self-hosted route could therefore execute a script
chosen by the caller on its persistent runner. The behavior fixtures now live
inline in the provider-owned workflow definition; no caller file is executed.
The structural test extracts that exact block, mutation-tests both failure
branches, and rejects script/source references.

The first live hosted contract run also showed that actionlint silently enables
ShellCheck when the host provides it, surfacing four intentional SC2016 literals
that the GCP development path did not see. The hosted route now requires
ShellCheck explicitly, and the intentional jq variables, Markdown backticks, and
literal DB placeholder carry line-scoped suppressions. The local self-hosted path
retains actionlint's prior optional-ShellCheck behavior.

## ShellCheck policy amendment (2026-08-04)

[Issue #362](https://github.com/Verjson/.github/issues/362) supersedes the final
sentence above: every local and reusable route now requires ShellCheck and passes
`-shellcheck=shellcheck` explicitly. The self-hosted image prerequisite landed in
`Verjson/verjson-github-runner#112`, so retaining an optional local path would no
longer preserve runner portability; it would leave Verjson's own security-sensitive
workflow shell less thoroughly checked than external consumers.

The workflow checks for `shellcheck` before linting and fails with a direct
toolchain error when a selected runner violates the image contract. The explicit
flag remains load-bearing because actionlint otherwise auto-detects ShellCheck on
`PATH`, making lint policy an accidental property of runner routing. Existing
findings were adjudicated before enabling the policy: the jq program's literal
dollar-prefixed variables retain a reasoned line-scoped SC2016 suppression, while
the ambiguous SC2015 boolean chains in gate bounds, final merge rechecks, and
budget telemetry were rewritten as explicit conditionals without changing their
fail-closed or best-effort behavior.

## Consequences

- Consumer repositories share one deterministic implementation instead of
  copying installer and checksum logic.
- The existing local check remains on the GCP pool and retains its path filters.
- Callers with no matching GCP pool can opt into the fixed GitHub-hosted runner
  without gaining arbitrary label control.
- Required-check configuration remains intentionally caller-local and must be
  reviewed together with caller path filters.
- A consumer call still executes the caller's checked-out workflows. Consumers
  choosing self-hosted GCP remain responsible for their own runner trust policy.

## Sensitive-hunk diff

```diff
 on:
   pull_request:
   push:
+  workflow_call:
+    inputs:
+      github-hosted-runner:
+        type: boolean
+        default: false

 jobs:
   actionlint:
-    runs-on: [self-hosted, GCP]
+    runs-on: ${{ inputs.github-hosted-runner && 'ubuntu-24.04' || fromJSON('["self-hosted","GCP"]') }}
```

## Amendment (2026-09-14, #1331) — the caller-executed behavior script no longer exists

The 2026-07-27 hardening above stands unchanged, and this amendment does not reopen it.

`scripts/actionlint-behavior.test.sh` is deleted. That is the hardening's own outcome
stated as a fact about the tree: the section describes a first draft that invoked the
script after checking out the caller, and records that the fixtures were moved inline into
the provider-owned workflow so no caller file is executed. The file naming the rejected
design was removed with it. The structural test that extracts the inline block, mutation-
tests both failure branches, and rejects script and source references is
`scripts/actionlint-reusable.test.sh`.

## Amendment (2026-10-17, #1827) — ShellCheck is pinned, not taken from the image

Requiring ShellCheck on every route did not make the verdict route-independent:
it made it depend on whichever ShellCheck the image ships. ADR 0235 moved a
public pull request's `actionlint` run to the hosted `ubuntu-24.04` image, whose
ShellCheck 0.9.0 reported 56 SC2015 chains (65 findings in all) that the pool
image's newer build does not, so the same tree failed on one route and passed
on the other. A local run of actionlint 1.7.7 over the tree reproduces it:
exit 1 with ShellCheck 0.9.0, exit 0 with 0.11.0.

`actionlint.yml` now installs ShellCheck the way it installs actionlint: a
pinned `SHELLCHECK_VERSION` (0.11.0) and the release asset's SHA-256, verified
before extraction, with only that binary put on the job `PATH`. The lint step
still fails closed without ShellCheck, and now also refuses any other version.
`actionlint-install.test.sh` executes the installer with a matching and a
mismatched digest, and `actionlint-reusable.test.sh` executes the lint step
against an image ShellCheck that reports 0.9.0. Adopters calling the reusable
workflow get the same pinned ShellCheck on every route, so a hosted caller's
verdict moves from 0.9.0 to 0.11.0; bumping the pin is a reviewed change like
the actionlint pin.
