---
date: 2026-11-02
id: 20261102T160716Z
impact: patch
title: Disclose the shared workspace writer assumption
---

Warn secretless CI users that competing host writers can redirect shared
workspace mounts and include the assumption in the job summary. Part of #1966.
