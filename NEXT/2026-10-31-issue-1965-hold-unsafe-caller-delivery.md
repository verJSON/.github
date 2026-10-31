---
date: 2026-10-31
issue: 1965
impact: major
title: Hold unsafe bootstrap caller delivery
---

Reject bootstrap apply for manifests containing generated callers before local
or remote writes, pending shared ownership and conflict-safe delivery. Keep
read-only caller inspection and organization-only apply available.
Preserve partial-effect receipts and mark failed dispatched writes uncertain.
