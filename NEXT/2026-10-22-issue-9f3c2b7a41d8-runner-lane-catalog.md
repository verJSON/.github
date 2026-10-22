---
date: 2026-10-22
id: 9f3c2b7a41d8
impact: minor
title: Add a portable installation profile and one runner-lane catalog
---

`CI_PROFILE_OWNER` (default `Verjson`) names the owner whose lane variables apply, replacing the hard-coded owner literal in `helm-ci.yml`'s `runs-on`. The expression is stamped from `scripts/runner_lane_catalog.py` by `scripts/gen-runner-lane.py` and held to the frozen legacy spelling for the built-in profile; any other profile never reaches `CI_LANE_FALLBACK` for untrusted events. ADR 0240. Part of #1869.
