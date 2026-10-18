---
date: 2026-10-18
id: 81666b48
impact: patch
title: Refuse a planted symlink before every fixed-path checkout
---

`changelog-validate.yml` now refuses a symlink at `.changelog-contract` before checking the contract out. `actions/checkout` follows a symlink at its `path:` and empties the target, so a pull request that committed one could aim that delete at runner state on a persistent runner (ADR 0236; ADR 0220's 2026-10-18 amendment).

`scripts/ci-gate/fixed-path-checkout-refusal.test.py` plants the link, executes the workflow's own steps, and fails when a checkout would follow it.
