---
date: 2026-10-07
issue: 1712
impact: patch
title: Confine release credentials to dependency acquisition
---

Generated Node release workflows now authenticate only when acquiring private dependencies. npm install runs with lifecycle scripts disabled while NODE_AUTH_TOKEN is present; scripts run later after the token is cleared. Package preparation, version stamping, and release verification also run without it. Restart-safe tag lookup uses process-scoped Git authentication, and checkouts do not persist credentials.
