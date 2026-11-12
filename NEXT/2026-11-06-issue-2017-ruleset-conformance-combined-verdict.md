---
date: 2026-11-06
issue: 2017
impact: patch
title: Test the ruleset audit's verdict when drift and an unreadable paused state coexist
---
`scripts/org_ruleset_conformance_test.py` now covers an organization where one ruleset has drifted and another's paused state cannot be read. The audit must still report the drift, report the unreadable state, and exit 2 (verdict unknown), never 1 or 0. Existing tests covered each condition alone, so a regression that hid the drift or let its exit 1 win would have passed. Three such injected regressions each fail the new test (#2017).
