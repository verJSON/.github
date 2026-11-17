---
date: 2026-11-17
issue: 2159
impact: patch
title: Close AI-review follow-ups for admin probes and quality-review edges
---

Tighten `no-owner-admin-merge` GraphQL probes and path skips (#2159/#2160), map
non-serializable Jev state to `JevError` (#2165), comment the fixed gitleaks
temp filename (#2168), and use a more conservative token estimate (#2169).
Closes #2159 #2160 #2165 #2168 #2169; #2164 remains deferred to the workflow
caller that already budgets via `request_token_cap` + payload chunking.
