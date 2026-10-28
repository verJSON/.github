---
date: 2026-10-28
issue: 1942
impact: patch
title: Guard atomic release pushes against branch ref changes
---

Require a direct-child release commit and compare the default branch against the dispatch commit during the atomic branch-and-tag push. Document the unobservable ABA boundary and the proposed identity-backed release-manager authority.
