---
date: 2026-11-18
issue: 2225
impact: patch
title: Compare host observation repository identity case-insensitively
---

After the organization rename, Actions reports `verJSON/...` while reviewed
deployment config may still carry `Verjson/...`. The deployment controller now
compares those `owner/name` identities with ASCII case-folding (ADR 0213) and
binds the host-export request to the reviewed spelling, so preview dry-runs no
longer fail closed on display case alone.

Fixes #2225. Part of #629.
