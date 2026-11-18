---
date: 2026-11-18
issue: 2225
impact: patch
title: Case-fold host observation repository identity across controller and transport
---

After the organization rename, Actions and the GitHub API report `verJSON/...`
while reviewed deployment config may still carry `Verjson/...`. The deployment
controller and credential-separated transport now compare those `owner` /
`owner/name` identities with ASCII case-folding (ADR 0213). The controller
binds the host-export request to the reviewed spelling; the transport accepts
matching live API `login` / `full_name` display case. ADR 0274 archives the
delivery ARB chair record (`arb-host-obs-case-2026-11-18`) for Gate R.

Fixes #2225. Part of #629.
