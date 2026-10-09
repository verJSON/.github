# 0222 — Documentation diffs skip the heavy actions-ci matrix

- **Date:** 2026-10-09
- **Status:** Accepted
- **Issue:** [verJSON/.github#1734](https://github.com/verJSON/.github/issues/1734)
- **Category:** required-check scope (sensitive class)

## Context

`shell-tests` is the required check for this repository (ADR 0058). `actions-ci` scheduled that check's full matrix for every documentation path because repository-wide scanners read Markdown. A docs-only pull request, and the matching push to `main`, each held a general runner for about 25 minutes, almost all of it in release-caller and merge-gate contracts that do not read those files.

## Decision

`actions-ci` classifies the diff before it schedules work.

1. A diff is documentation-only when every changed path is under `docs/`, `NEXT/`, or `CHANGELOG/`, or is Markdown, and no path is under `scripts/` or `.github/` or is a shell script.
2. Documentation-only diffs run the documentation contract group and the pull-request ADR number check. They do not run `shell-test-groups` or the hosted compatibility job.
3. Any other diff, an empty file list, an unknown event, or a new-branch push with no base commit runs the heavy matrix. Failing closed keeps a classifier bug from skipping release-caller coverage.
4. `shell-tests` stays the unmatrixed required context. It fails when a documentation diff runs the heavy jobs, and it fails when a heavy diff skips them.

## Consequences

A Markdown-only change no longer waits on `changelog-caller-contract` or the merge-gate behavioral suite. A change that also touches a workflow, a script, or `scripts/` still runs the full matrix, including on the push to `main`.
