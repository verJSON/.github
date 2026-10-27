---
date: 2026-10-27
id: 3b7ca4f6d2e1
impact: patch
title: Reject public calls to release and container publishing workflows
---
Part of #1831. The reusable Node release and container candidate publishing
workflows now fail a hosted, permissionless guard for public pull-request
events. Every existing job depends on that guard and checks its result,
including jobs whose normal conditions use `always()`. The guard contract
covers all five public pull-request event types.
