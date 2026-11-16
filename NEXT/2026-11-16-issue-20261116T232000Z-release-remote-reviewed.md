---
date: 2026-11-16
id: 20261116T232000Z
impact: patch
title: Review release publish and snapshot off the relay backlog
---

Part of #1831. `release.yml` `publish` and `snapshot` leave the frozen
relay backlog as reviewed entries: both are SHA-pinned remote callees whose
placement the isolation checker cannot read. Publish's `node-release` callee
hardcodes ubuntu-24.04; snapshot shares `release_runner_expr` with verify
(#465). The live backlog is now empty.
