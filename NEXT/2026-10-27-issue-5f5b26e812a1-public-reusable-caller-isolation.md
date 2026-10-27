---
date: 2026-10-27
id: 5f5b26e812a1
impact: patch
title: Fail closed on public reusable-workflow calls
---

Canonical CI routes public pull-request execution to GitHub-hosted runners and fails rejected reusable-workflow calls explicitly, including when repository visibility is unavailable.
