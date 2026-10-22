---
date: 2026-10-22
issue: 1423
impact: patch
title: Retire the CLI Projects legacy consumer caller digest
---

`config/cli-projects-required-node-ci.json` now sets `rollout_legacy_consumer_workflow_sha256` to `null`: verjson-cli-projects#159 landed the generated v4.2.0 caller on its default branch, so the rotation verifier accepts only the byte-exact generated caller. This is the second rotation described in ADR 0212; org ruleset 21830367 stays pointed at its prior revision (`audit` reports `rotation pending`) until it is rotated to this change's merge commit. Part of #1423.
