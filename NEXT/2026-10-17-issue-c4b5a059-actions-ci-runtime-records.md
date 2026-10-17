---
date: 2026-10-17
id: c4b5a059
impact: minor
title: Make actions-ci row lines unforgeable and add structured runtime records
---
The actions-ci group runner now replays each row's output inside a random-token `::stop-commands::` block with every line, and every bare-carriage-return segment, prefixed by `| `, so command output can no longer hide, mask, or forge the `actions-ci command group=…` row line that the verjson-observability#271 collector parses; the row line's format is unchanged. Each `shell-test-groups` cell also uploads a seven-day `actions-ci-runtime-records-<run>-<attempt>-<group>` artifact holding one JSON record per row (status, elapsed, budget, timestamps, timeout, background processes, runner identity), written by the runner after each row exits to a path no row is told.

Slice 4 of verjson-observability#271; see the 2026-10-17 amendment to ADR 0225 for the threat model, why no HMAC was added, and the live receipt from Verjson/demo-repository run 39066757142.
