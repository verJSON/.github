---
date: 2026-11-10
issue: 2052
impact: minor
title: Stage locked Prisma schema engines for offline CI
---
The reusable Node CI workflow verifies and stages a locked Prisma schema engine before offline candidate scripts run, then removes the run-owned executable after the script plan.

The generated protected workflow uses the same run-scoped stage through its trusted candidate helper. It binds the executable read-only and exposes only the fixed in-sandbox path to candidate scripts.

The trusted stager accepts supported npm lockfile metadata and the Ubuntu 24.04 x64 engine target, then verifies Prisma's published SHA-256. Candidate scripts keep their existing network and credential boundary. See [ADR 0259](../docs/decisions/0259-stage-locked-prisma-engine-outside-offline-sandbox/README.md) and [#2052](https://github.com/VerJSON/.github/issues/2052).
runner-isolation inventory digest now pins the updated Node CI workflow snapshot; the existing persistent-runner placement finding remains pending under [#1831](https://github.com/VerJSON/.github/issues/1831).
The downloader repeats the lowercase 40-hex commit check at the URL boundary, so malformed metadata cannot become a path segment.
