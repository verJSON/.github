---
date: 2026-11-18
issue: 2225
impact: patch
title: Compare host observation repository identity case-insensitively
---

After the organization rename, Actions reports `verJSON/...` while reviewed
deployment config may still carry `Verjson/...`. The deployment controller and
credential-separated transport now compare those `owner` / `owner/name`
identities with ASCII case-folding (ADR 0213). The controller still binds the
host-export request to the reviewed spelling; the transport accepts matching
live API `login` / `full_name` display case so preview dry-runs no longer fail
closed on rename casing alone.

Fixes #2225. Part of #629.
