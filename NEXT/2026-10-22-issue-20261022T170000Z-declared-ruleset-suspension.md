---
date: 2026-10-22
id: 20261022T170000Z
impact: patch
title: Let the required-reviewer audit expect a declared, dated ruleset suspension
---

The daily required-reviewer audit accepts a per-ruleset `suspended` declaration (`reason`, `since`, `reenable_when`) that makes it expect `disabled` for that ruleset, still verifies every other property, and prints a `SUSPENDED:` line on every run. Declared for all four sensitive-path-admin-review rulesets, which the owner disabled on 2026-10-22 until agent and human identity attribution exists; any undeclared ruleset is still required active. ADR 0210, 2026-10-22 amendment.
