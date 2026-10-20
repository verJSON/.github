---
date: 2026-10-19
issue: 1816
impact: minor
title: Manage per-repository sensitive-path admin approval as native required-reviewer rulesets
---

`config/required-reviewer-rulesets.json` now holds the reviewed desired state for organization rulesets that require an `@verJSON/admins` approval on sensitive paths, and `scripts/required-reviewer-ruleset.py` renders, checks, and (with an explicit acknowledgement) applies it. Canonical CODEOWNERS stays the single `* @verJSON/devs` default-ownership rule; ADR 0210 is amended to record the decision. Nothing is applied by this change.
