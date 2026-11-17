---
date: 2026-11-17
id: 20261117T004504Z
impact: minor
title: Add quality-review payload filter and gitleaks fail-closed scanning
---

Part of #1820. Filters lockfiles, env, vendored, binary, and generated paths from
PR diffs; chunks under the provider token cap and total budget; and fails closed
when gitleaks is absent, digest-mismatched, or errors. Behavioral tests registered
in actions-ci. No workflow yet.
