---
date: 2026-10-15
id: ccc5c6d2
impact: patch
title: The generated-caller Renovate fence covers every release caller name
---

`renovate/generated-callers.json` now also fences `.github/workflows/release-*.yml`, so Renovate no longer edits an adopter's extra generated release caller. verjson-cli's `release-cli-schema.yml` was the case that slipped through. Renovate cannot match a file by its generated header: `matchJsonata` sees only the dependency, and `matchFileNames` sees only the path. The fence therefore stays a glob, kept inside the subset that Verjson/verjson-agents' resolution model understands. This repository's hand-written `release-app-canary.yml`, `release-dispatch.yml`, and `release-propose.yml` share the prefix. `renovate.json` reopens only their step actions, in a first rule, so the grouped digest PR keeps moving them. `scripts/ci-gate/renovate-generated-callers.test.py` now reads the preset as globs and places a release caller at each recorded adopter name. It also requires that every fenced hand-written workflow here that Renovate reads is reopened, and that every reopen is narrow, first, live, and free of bare-SHA pins. ADR 0232 records the amendment, the organization survey, and the remaining `publish.yml`-style gap. Part of [Verjson/.github#1812](https://github.com/Verjson/.github/issues/1812).
