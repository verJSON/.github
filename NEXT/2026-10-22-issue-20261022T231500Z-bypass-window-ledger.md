---
date: 2026-10-22
id: 20261022T231500Z
impact: patch
title: Record the 2026-10-22 bypass window ledger and restore checklist in ADR 0210
---

ADR 0210 now carries the owner's 2026-10-22 standing bypass authorization, a ledger of every bypass (four suspended reviewer rulesets, the verjson-cli-projects#159 Gate C exception, docs PRs merged on `assert-mergeable-head` exit 3), a measurable exit condition, and a restore checklist. The four pre-change ruleset images are committed under the ADR's `rollback/` directory. Documentation only: no gate code or ruleset changes.
