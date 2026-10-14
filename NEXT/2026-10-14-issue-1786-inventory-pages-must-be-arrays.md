---
date: 2026-10-14
issue: 1786
impact: patch
title: Refuse inventory pages whose collection is not an array
---
`scripts/assert-mergeable-head.sh` now checks that every page from the check-run, commit-status, and workflow-run endpoints is an object whose collection is an array. Any other page fails closed with exit `1` before the page counts are reconciled. Previously the walk counted a collection with `length`, which also accepts a string, an object, or a number. A single page whose wrongly typed collection matched `total_count`, or a null collection on any page, was therefore read as an empty or forged inventory, and the admission gate printed `true`.

The check-run annotations that Gate B reads for ADR 0156 have no `total_count` to reconcile. A successful response that was empty, blank, or not a JSON array on every page used to be read as "no deferral annotation". It now fails closed with exit `1`. A walk of `[]` pages still means none.

Empty array pages behave as before, and all 131 existing fixtures produce byte-identical output. ADR 0229 is amended with the evidence.
