---
date: 2026-10-17
id: 20261018T045749Z
refs: 1423
impact: patch
title: Accept the strict admission check in the bypassless cli-projects ruleset exception
---

Fix: the scheduled `Organization ruleset conformance` audit accepts ruleset 21830367
(`cli-projects-package-surface-required-workflow`) again. Since the 2026-10-15 rotation
added ADR 0212's strict `admission` status check, the audit had reported `required release
authorization bypass is absent`. No bypass actor is added.

Each bypassless required-workflow exception in `config/org-ruleset-conformance-policy.json`
now declares its exact status-check image, or `null`. The audit accepts one `workflows`
rule plus exactly that declared rule. A missing or non-strict check, a second check, a
binding to another App, or any other rule still fails
([ADR 0153](../docs/decisions/0153-cli-projects-package-surface-required-workflow/README.md)
amendment of 2026-10-17).
