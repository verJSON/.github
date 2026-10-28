---
date: 2026-10-27
issue: 1814
impact: patch
title: Include generated label re-arm callers in capability-floor observation
---

Capability-floor discovery now tracks the generated AI review label re-arm callers in verjson-cli-cloud and AiB, so their pinned `gate-rearm.yml` revisions are included in the audit. The first required run exposed that ruleset 20722935 still pinned the pre-dispatch-split gate revision; its SHA was rotated to current main without changing enforcement or scope.
