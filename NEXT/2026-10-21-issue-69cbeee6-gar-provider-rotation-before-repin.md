---
date: 2026-10-21
id: 69cbeee6
impact: patch
title: Document the GAR provider rotation that must precede a publisher repin
---
The container candidate adoption guide now says to rotate a GAR destination's Workload Identity provider to the new contract SHA, and to have that rotation reviewed, before regenerating at that SHA. It gives the read-only `describe`, a SHA-only `update-oidc`, and two read-back diffs that must be empty. It also explains why neither a partial nor a repeated full rerun can repair a failed candidate publication: push a new commit instead.

ADR 0217's 2026-10-21 amendment records the `verjson-ci-main` provider condition, which is now pinned to `a89fe1f090545c15cdd6987f20119327206e2c59`. It covers the fail-closed `mirror-gar` refusals after Verjson/verjson-ci#303 repinned before the rotation, the post-rotation evidence runs, and the rule that every publisher repin needs a prior provider rotation. The ADR's original `f9b62501…` condition is historical.
