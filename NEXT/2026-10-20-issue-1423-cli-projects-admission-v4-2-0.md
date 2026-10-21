---
date: 2026-10-20
issue: 1423
impact: patch
title: Admit the v4.2.0 CLI Projects CI caller
---

The CLI Projects required workflow now admits only the push-only `ci.yml` caller generated at the v4.2.0 contract release (`node-ci.yml@a89fe1f0`, sha256 `d23be004…`) instead of the v3.0.2 caller (`9e4ca97c…`), so `Verjson/verjson-cli-projects` can adopt the current release without `contract-version.py verify` reporting `PIN_MISMATCH`. Organization ruleset 21830367 keeps running the previous revision until it is rotated to this change's merge commit; Verjson/verjson-cli-projects#159 is the single bridging pull request.

The rotation-only `rollout_legacy_consumer_workflow_sha256` stays at `721856f9…`, the caller on the consumer's `main` today, because the rotation verifier must still confirm that caller until #159 lands; it is set to `null` after #159 merges (ADR 0212, 2026-10-20 amendment).
