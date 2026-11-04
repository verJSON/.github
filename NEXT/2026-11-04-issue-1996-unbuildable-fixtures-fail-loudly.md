---
date: 2026-11-04
issue: 1996
impact: patch
title: Unbuildable test fixtures and ignored exit codes fail loudly instead of passing
---

Eleven shell test suites now check that each fixture, mutant, extraction and
generator output was actually built before trusting a verdict on it. A failed
build now fails with a message that names it. It can no longer count as a pass,
count as the expected rejection, or end the run silently under `set -e`.

- Mutators check their exit status and that the mutant is non-empty and differs
  from its source. This covers `privileged-merge-pin`, `actions-ci-groups`,
  `node-workflow-controls`, and the caller and lock mutants in the container
  release and deployment contracts.
- The `@main` docs scan in `immutable-release-docs` checks the awk exit status
  and requires at least one YAML example to have been read.
- `fleet-watchdog` asserts the watchdog's exit code before every "cancels
  nothing" assertion. A watchdog that exits 3 no longer passes them.
- Absence and refusal assertions in `privileged-merge-pin` and
  `complete-authorization` now require the specific exit code or refusal
  message. Before, a crashed script was enough. The generator rejections in
  `container-release-contract` and the asset and publish refusals in
  `release-node-assets` now name their exit status and reason too.
- Line captures in the `changelog-pr-gate-generator` and
  `container-release-contract` ordering checks, and in the
  `container-deployment-contract` condition count, now report a missing match
  through their own guard instead of aborting under `pipefail`.
- The five `set -e` suites now name the failing line and command in an `ERR`
  trap. Their bare `! grep` assertions could never fail under `set -e`, so they
  now go through `assert_absent`, which also rejects a grep that cannot read
  its input.

Making these checks live exposed fixtures that never reached the guard they
named. In `release-node-assets`, the more-than-16-assets case was rejected as
invalid JSON, and the 250 MiB case was rejected for a duplicate basename. In
`actions-ci-groups`, the duplication mutant used a retired group name. All three
now reach their own guard. `container-release-contract`'s no-build assertion
had been inert. Its `docker build` pattern also matched the required
`docker buildx imagetools` retag. It is now an allowlist: every `docker`
command in the release workflow must be `docker buildx imagetools`. Other
build tools are named separately. No production behavior changes.
