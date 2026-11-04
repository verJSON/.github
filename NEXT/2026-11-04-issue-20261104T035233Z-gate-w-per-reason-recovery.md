---
date: 2026-11-04
id: 20261104T035233Z
refs: 1905, 2008
impact: patch
title: Gate W refusals state the runbook's recovery for each reason
---

`scripts/assert-mergeable-head.sh` now ends every Gate W refusal with the recovery
`docs/arm-recovery-runbook.md` gives for each reason it names, instead of advising a
re-arm for all of them. `cancelled` and `stale` point at the close-and-reopen
recoveries, `absent` checks enrollment and the base-ref rule first, `not successful`
fixes the cause first, and `unbound`, `ambiguous`, `undated`, `changed`, an unreadable
rule, and duplicate paths say not to re-arm, because their evidence cannot be
attributed. The exit code (3) and the `Gate W:` prefix are unchanged.

The script keeps one reason-to-recovery table, and the required-workflow test fails
when it and the runbook's *Recovery by refusal reason* table disagree. The runbook now
lists duplicate paths in its no-re-arm row. Part of #1905; follows #2008.
