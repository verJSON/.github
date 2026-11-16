---
date: 2026-11-16
id: 20261116T203145Z
refs: 2141
impact: patch
title: Refresh release.yml verify runner-isolation backlog digest
---

Refresh the `#1831` pending backlog digest for `release.yml` `workflow_dispatch`/`verify` after `runner-routing-policy.test.sh` gained the `post-publish-smoke` inventory entry. That script is bound into verify's `tree_programs` closure, so the prior digest no longer matched and runner-isolation failed closed. Classification and unresolved reasons are unchanged.

Part of #2141.
