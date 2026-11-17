---
date: 2026-11-17
id: 20261117T013927Z
impact: patch
title: Add advisory quality-review reusable workflow and canary caller
---

Part of #1820. Adds `quality-review.yml` (workflow_call, no head checkout, fork/draft
skip, public hosted runner), a generator for adopter callers, and an in-repo canary
caller. gitleaks binary pin and sticky comments remain later; absent scanner /
secret paths stay green via not-obtained.
