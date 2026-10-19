---
date: 2026-10-19
id: e4c1a730
impact: patch
title: Update the pinned deployment CLI to @verjson/cli-cloud 1.3.0
---

The container deployment contract now pins `@verjson/cli-cloud` 1.3.0, up from 1.1.1. The package lock also picks up patch updates to its Pulumi, execa, js-yaml and pretty-ms dependencies; no dependency is added or removed. The `runner update` and `runner-host-evidence` commands this repository calls are unchanged between the two versions.
