---
date: 2026-10-10
issue: 1747
impact: patch
title: Grant actions read to the generated Node release publish job
---
The `release-node` caller now grants its `publish` job `actions: read`, which `node-release.yml`'s `release` job has declared since #1730. Without it the dispatch ended as a zero-job `startup_failure`, because a called workflow cannot exceed its caller's grant. The release-caller contract test now compares the grants of every generated mode that calls a reusable workflow with the permissions that workflow's jobs declare, recursing into nested reusable workflows with the calling job's grant as the cap and reporting cycles. It fails a calling job that declares no permissions instead of assuming write-all, reports a call it cannot resolve to this repository's workflows, classifies every mode the generator dispatches so a new one cannot skip the comparison, and proves by mutation that it rejects a caller that withholds `actions: read` and a nested callee that over-asks. Adopters regenerate their release caller at a contract SHA containing this fix.
