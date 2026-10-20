---
date: 2026-10-19
issue: 1816
impact: minor
title: Manage per-repository sensitive-path admin approval as native required-reviewer rulesets
---

`config/required-reviewer-rulesets.json` now holds the reviewed desired state for organization rulesets that require an `@verJSON/admins` approval on sensitive paths, and `scripts/required-reviewer-ruleset.py` renders, checks, and (with an explicit acknowledgement) applies it. Canonical CODEOWNERS stays the single `* @verJSON/devs` default-ownership rule; ADR 0210 is amended to record the decision. Nothing is applied by this change.

The config covers `verjson-cli-cloud`, `verjson-cli-projects`, `verjson-cli` and `verjson-object-storage`, the four repositories whose hand-written admin routes canonical regeneration drops. Only the release-authorization App bypasses; organization owners deliberately do not. `config/org-ruleset-conformance-policy.json` registers each ruleset's bypass contract by name (`"ruleset_id": null`), which `scripts/org-ruleset-conformance.py` now accepts, because the ruleset has no id until an org owner applies it.
