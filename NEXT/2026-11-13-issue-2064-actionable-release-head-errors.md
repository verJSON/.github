---
date: 2026-11-13
issue: 2064
title: Make blank release dispatch errors actionable
impact: patch
---

Keep the stated-head guard fail-closed while showing the dispatched commit, a local release preview that verifies a full, clean checkout at that SHA and downloads the pinned contract script into a separate disposable directory, and a default-branch re-dispatch command that preserves the selected version, prefix, component, fragments, and selector digest. Regenerate the canonical release caller at the same immutable pin and clarify in the dispatch form that blank versions require the commit being previewed.
