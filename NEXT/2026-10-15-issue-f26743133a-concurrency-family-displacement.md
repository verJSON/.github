---
date: 2026-10-15
id: f26743133a
impact: patch
title: Admit heads whose cancelled re-arm run was displaced from its concurrency group by another re-arm workflow
---
`scripts/assert-mergeable-head.sh` no longer refuses a green pull request because GitHub replaced one pending run of the `ai-review-arm-<PR>` concurrency group with a newer run of a different workflow in that group: Gates A and C also set aside a cancelled check run when its workflow run belongs to a declared concurrency family and a later-created run of that family on the same head succeeded (ADR 0226, amendment 2026-10-15). Family membership comes only from a checked-in map that a test derives from the workflows and generators declaring and inheriting the group.

Evidence: Verjson/verjson-agents#432 head `ae5f8326` — run 38787495945 (`gate re-arm`) was cancelled before any runner or step when 38787540131 and then 38787543084 (`AI review lifecycle re-arm`, success) joined the group, and the gate exited `5` on `rearm / event-policy=CANCELLED` until a manual `gh run rerun`.
