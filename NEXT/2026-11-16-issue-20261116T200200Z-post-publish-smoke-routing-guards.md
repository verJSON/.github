---
date: 2026-11-16
id: 20261116T200200Z
refs: 2141
impact: patch
title: Register post-publish-smoke in reusable and routing guards
---

Teach the public reusable-workflow job graph and the fixed hosted-selector / provenance inventories about `post-publish-smoke`, including `candidate-manifest`'s dependency on it, so platform CI accepts the #2141 opt-in job without relaxing public-call or runner-routing fail-closed checks.

Part of #2141.
