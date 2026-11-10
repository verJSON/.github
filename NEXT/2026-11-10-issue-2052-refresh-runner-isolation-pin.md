---
date: 2026-11-10
issue: 2052
impact: minor
title: Refresh Node CI runner isolation pin
---
The runner-isolation inventory now pins the exact Node CI workflow snapshot after Prisma engine staging was added. The scan continues to report the existing persistent-runner placement as pending under #1831; this digest refresh does not clear that backlog.
