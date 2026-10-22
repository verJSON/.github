---
date: 2026-10-22
id: 20261022T210000Z
impact: minor
title: Add a fail-closed diff classifier that selects a lighter node-ci script plan
---

`node-ci` accepts `secretless-ci-non-app-script-plan` and `secretless-ci-non-app-paths`. A secretless pull request whose every changed path is non-application runs the lighter plan inside the same job, so `<lane> / build-test` still reports; any unclassified path, API error, or malformed input keeps the full plan. Recorded in ADR 0238; part of #1869, which owns the issue-keyed fragment for the larger-runner change.
