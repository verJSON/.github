---
date: 2026-11-10
issue: 1905
impact: minor
title: Accept provider-bound pull_request runs for required workflows (#1905)
---

Gate W now accepts a required workflow run triggered by `pull_request` as well as
`pull_request_target` when GitHub binds the run to its required-workflow URL and
workflow id, and its head, branch, repository, freshness, and successful result
match. Runs from `push`, `merge_group`, and other events remain unbound. The
concurrency-family contract and Gate C behavior are unchanged.
