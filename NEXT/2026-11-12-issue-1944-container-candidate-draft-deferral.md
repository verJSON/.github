---
date: 2026-11-12
issue: 1944
impact: minor
title: Generated container-candidate callers defer validation on draft pull requests
---

`scripts/gen-container-candidate.sh` now skips the generated `validate` job while a pull request is a draft and triggers on `pull_request` types `opened`, `synchronize`, `reopened`, and `ready_for_review`, so marking the pull request ready runs the full multi-architecture candidate validation on its head ([ADR 0261](../docs/decisions/0261-generated-container-candidate-callers-defer-drafts/README.md), #1944). A new concurrency block cancels a running validation only on a new commit; push publication keeps a run-scoped group and is unchanged.

Required contexts stay fail-closed: a skipped `validate` call reports none of its nested `validate / …` contexts on a draft, so a required one is absent until the ready run reports it. Adopters adopt by regenerating their caller; the generated contract test now asserts the draft guard, the trigger types, and the concurrency block.
