---
date: 2026-11-13
issue: 2064
title: Make blank release dispatch errors actionable
impact: patch
---

Keep the stated-head guard fail-closed by binding its local preview to the exact pinned contract ref and rejecting a mismatch. Show the dispatched commit, verify a full, clean checkout at that SHA, and download and decode the pinned script with isolated Python outside the checkout. Preserve the selected version, prefix, component, fragments, and selector digest in the default-branch re-dispatch command; clarify that a blank version requires the commit being previewed and regenerate the canonical release caller at the same immutable pin.
