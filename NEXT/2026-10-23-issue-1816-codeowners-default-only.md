---
date: 2026-10-23
issue: 1816
impact: patch
title: Record that the canonical CODEOWNERS stays default-only while sensitive-path review is suspended
---

ADR 0210 gains a 2026-10-23 amendment deciding #1816's open question on merit: the generated `.github/CODEOWNERS` keeps the single rule `* @verJSON/devs`, no sensitive-path admin route is added, and no generator, digest or adopter changes. It records the option set, why a fleet-wide route would re-enable the suspended admin review, the accepted residual risk, and the rollout order. It also ledgers the unrecorded admin merges of #1894 and #1895. Part of #1816.
