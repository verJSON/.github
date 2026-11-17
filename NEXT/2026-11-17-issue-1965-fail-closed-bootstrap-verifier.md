---
date: 2026-11-17
issue: 1965
impact: patch
title: Fail-closed bootstrap caller delivery verifier
---

Harden the canonical CI bootstrap as a verifier-only consumer: reject output and
parent-path symlinks (including dangling) during inspection, keep apply held for
non-empty callers, and refuse delivery previews with `readyForApply: false` so
draft verjson-ci writer preimages cannot authorize local apply.

Part of #1965 and ADR 0251. Conflict-safe delivery remains with verjson-ci#295 /
held draft verjson-ci#333; this change does not reopen a filesystem writer.
