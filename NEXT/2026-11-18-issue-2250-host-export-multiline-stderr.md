---
date: 2026-11-18
issue: 2250
impact: patch
title: Relay the last safe host-export diagnostic
---

Dry-run 44253156883 reported only `canonical host evidence transport failed`
after the doctl auth probe. The controller required the entire transport
stderr blob to be one line, so a warning or a non-UTF-8 doctl traceback hid
the real diagnostic. Relay the last secret-free line, and decode doctl and
CLI output with replacement characters so a banner cannot escape as a
traceback.

Fixes #2250. Part of verJSON/verjson-git-runners#197.
