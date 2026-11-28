---
date: 2026-11-27
id: 20261127T174050Z
refs: 1954
title: Harden signed release snapshot verification boundaries
impact: patch
---

Require an exact Git tag and an authorized source-to-destination route when
verifying signed snapshot receipts. Preserve the observed dispatch context and
report signature verifier outages as typed failures. Part of #1954.
