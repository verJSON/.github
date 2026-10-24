---
date: 2026-10-24
id: 20261024T131155Z
refs: 1869
impact: patch
title: Preserve Node CI script-plan input compatibility
---

The shared parser trims root-plan and nested-manifest JSON inputs as the prior workflow did and no longer applies byte limits that the inline parser did not have. Existing entry-count, credential-name, boolean, and path validation remains in place.
