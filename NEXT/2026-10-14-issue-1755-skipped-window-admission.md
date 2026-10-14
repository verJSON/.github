---
date: 2026-10-14
issue: 1755
impact: patch
title: Refuse admission while a required context is only skipped or a workflow run is unfinished
---
`scripts/assert-mergeable-head.sh` no longer admits a head in the window after a draft pull request is marked ready: Gate A now requires a SUCCESS for every required context (a SKIPPED or NEUTRAL report alone is not evidence the work ran), and treats any workflow run on the head whose status is not `completed` as pending (ADR 0229). The passing conclusion set is unchanged; a draft-time SKIPPED plus a ready-time SUCCESS (#1753's documentation-only shape) still passes.

The workflow-run inventory is now read and type-validated for every head, so Actions (read) is required unconditionally (superseding ADR 0226's conditional read); a completed run with no conclusion, a malformed record, or a failed read exits `1` and names the grant. A survey of 296 merged pull requests across the 37 organization repositories with required checks found none whose required contexts lacked a SUCCESS at merge.
