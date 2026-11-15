---
date: 2026-11-15
issue: 1869
impact: patch
title: Fetch secretless compatibility and rebuild lanes from pinned helpers
---

Node CI no longer inlines the secretless compatibility lane executor or the approved lifecycle rebuild program. Both the standard and protected workflows fetch `scripts/ci_secretless_compatibility_lanes.py` and `scripts/ci_secretless_rebuild_packages.py` at `JOB_WORKFLOW_SHA`, verify each SHA-256, and run it. The generator byte bounds drop from 490,000 to 390,000, 5,073 bytes above the 384,927-byte canonical workflow.

The compatibility move alone left `node-ci.yml` at 411,119 bytes and the bounds at 416,000. The rebuild program was the next heredoc, 22,485 bytes of Python. Moving it drops `node-ci.yml` from 411,119 to 384,927 bytes and `node-ci-protected.yml` from 406,822 to 380,630. The node-ci / node-ci-protected build-test relay backlog digests are refreshed for the moved bytes. The settable-guard inventory rises from 547 to 551 for the rebuild helper's `JOB_WORKFLOW_SHA` and digest checks in each workflow. See the 2026-11-15 amendments to ADR 0238.

The extracted helpers keep FunctionDefs and data-only bindings at module scope (for AST contracts and `-I` import probes) while running only the env/IO body under `__main__`. Assign and def order inside the candidate service-validation slice stays aligned with `run_protected_candidate_plan`, so rebuild-env byte-equality still holds.

Merged against current main, the settable-guard inventory is 559 (rebuild helper pins plus post-#2110 tree bytes); `GUARD_COUNT` tracks that.
