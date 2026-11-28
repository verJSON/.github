---
date: 2026-11-19
issue: 1186
impact: patch
title: Record the public adoption package audit status
---

ADR 0175 records a pinned re-audit of the four proposed public npm packages. Their
MIT licenses and locked runtime closures pass the checked criteria, but current
release callers still target GitHub Packages; keep all four private until npmjs
provenance and full artifact secret review are established.

This is a partial #1186 delivery. No registry, release workflow, publication, or
live infrastructure state changed.
