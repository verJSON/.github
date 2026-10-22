---
date: 2026-10-22
id: 20261022T223000Z
impact: patch
title: Record the agent and human attribution policy in ADR 0202
---

ADR 0202 now records that agent work carries a composite human, model, and agent identity, while review approval and admin-merge require a direct human session. The delegating human may approve as a named `delegation-owner-review` exception, bound to PR, head, base, and diff digest. Documentation only: no gate code or ruleset changes, and the four suspended reviewer rulesets stay suspended until direct-human approval events are verifiable.
