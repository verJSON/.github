---
date: 2026-11-27
id: 20261127T193144Z
refs: 1954
title: Bind GitHub snapshot receipt delivery to the exact run attempt
impact: patch
---

Require a protected App claim for the attempt and its envelope artifact. Check
the provider run metadata and the actual archive and envelope digests before
passing the delivery to the signature verifier. Part of #1954.
