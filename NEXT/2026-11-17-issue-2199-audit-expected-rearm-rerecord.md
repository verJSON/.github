---
impact: patch
title: Re-record arm audit expectations after lifecycle-rearm shrink
---

Part of #2199 / #2200. Replaces the stale #1810 quiet expectation with the
2026-11-17 live incomplete-caller item set (27) and records the admission-caller
finding for authz/vault. Expands `arm_property_migration.repositories` to the
live legacy arm cohort.
