---
date: 2026-11-16
id: 20261116T194900Z
refs: 2141
impact: patch
title: Review the post-publish-smoke settable-value guard
---

Register the generator heredoc grep for `post-publish-smoke: ⟦gen:post_publish_smoke⟧` in the reviewed unclassifiable inventory and bump `GUARD_COUNT`, so locale-collation CI accepts the #2141 opt-in without weakening the guard scanner. The expansion remains a generation-time true/false from `post_publish_smoke_mode` only.

Part of #2141.
