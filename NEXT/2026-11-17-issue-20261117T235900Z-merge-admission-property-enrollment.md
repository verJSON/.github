---
date: 2026-11-17
id: 20261117T235900Z
impact: patch
title: Enroll managed repos under merge-admission property ruleset scope
---

Retarget org ruleset `merge-admission-required` (27387670) from the `.github`
canary `repository_id` to `verjson-merge-admission=enforced` with paused
repositories excluded, after managed callers and bound-App SUCCESS landings.

Part of #1945.
