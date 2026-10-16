---
date: 2026-10-15
id: f26743133a
impact: patch
summary: "`scripts/assert-mergeable-head.sh` now sets aside a cancelled run displaced from a declared concurrency family by a later successful run of that family, and an aggregator failure that completed after the latest cancelled check of its run when the newest later run of the same workflow re-did that check to SUCCESS (ADR 0226 amendments, 2026-10-15)."
title: Admit heads whose cancelled run was displaced by another re-arm workflow or left an aggregator failure behind
---
`scripts/assert-mergeable-head.sh` no longer refuses a green pull request because GitHub replaced one pending run of the `ai-review-arm-<PR>` concurrency group with a newer run of a different workflow in that group: Gates A and C also set aside a cancelled check run when its workflow run belongs to a declared concurrency family and a later-created run of that family on the same head succeeded (ADR 0226, amendment 2026-10-15). Family membership comes only from a checked-in map that a test derives from the workflows and generators declaring and inheriting the group.

Evidence: Verjson/verjson-agents#432 head `ae5f8326` — run 38787495945 (`gate re-arm`) was cancelled before any runner or step when 38787540131 and then 38787543084 (`AI review lifecycle re-arm`, success) joined the group, and the gate exited `5` on `rearm / event-policy=CANCELLED` until a manual `gh run rerun`.

The same amendment sets aside a non-CANCELLED check of a superseded cancelled run -- an `if: always()` aggregator that concluded FAILURE after the cancel (Verjson/.github#1824, run 38803591718) -- but only when it completed after the latest CANCELLED check of that run (with none, it stays red) and the newest later successful run of the same workflow, event, and head reports that same check as SUCCESS; a genuine failure that finished before the cancel stays red, and a later run that skipped or omitted it vouches for nothing, so #1824's draft-tier successor still (correctly) leaves the head refused.
