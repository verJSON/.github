---
date: 2026-10-27
id: 9ab3cd614f07
refs: 1869
impact: patch
title: Route Node CI through installation runner profiles
---
Node CI eligibility and build jobs now use the installation runner-profile catalog, with the protected workflow generated from the same source.

Eligibility uses `caller-runner`, while `build-test` uses `secretless-first` so the eligibility-gated untrusted lane and hosted fallback remain ahead of caller runner input. The default Verjson profile preserves its existing routing; custom profiles use the strict untrusted lane without inheriting `CI_LANE_FALLBACK`. Exact-site coverage and isolation digests were updated under ADR 0240. Part of #1869.
