---
date: 2026-11-13
issue: 2066
impact: patch
title: Recover deleted protected type-surface baselines through declaration updates
---

Allow a protected type-surface declaration repair PR to select a newer published stable version when retention has deleted the version on its base, while binding the exact base, head, declaration, and artifact in the protected receipt.

ADR 0262 limits recovery to the declaration and its `NEXT/` entry; all other PRs continue to use the base declaration. The generated protected Node workflow and its regression tests are updated together. The base and head declaration lookups percent-encode their commit refs to satisfy the ref-URL contract check.
