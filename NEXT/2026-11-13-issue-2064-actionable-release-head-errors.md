---
date: 2026-11-13
issue: 2064
title: Make blank release dispatch errors actionable
impact: patch
---

Keep the stated-head guard fail-closed while showing the dispatched commit, a matching local release preview command, and a default-branch re-dispatch command that preserves the selected version, prefix, component, fragments, and selector digest. Clarify in the dispatch form that blank versions require the commit being previewed.
