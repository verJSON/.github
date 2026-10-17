---
date: 2026-10-17
id: c4b5a059
impact: minor
title: Make actions-ci row lines unforgeable and add structured runtime records
---
The actions-ci group runner now replays each command's output with a `| ` line prefix and always starts its row line on a fresh line, so command output that ends without a newline or prints `##[endgroup]` can no longer swallow, hide, or forge the `actions-ci command group=…` row that the verjson-observability#271 collector parses. The row line's own format is unchanged.
