---
date: 2026-10-17
issue: 1810
impact: patch
title: Re-record the authorization-arm finding after verjson-editor regenerated
---

Maintenance: the scheduled `arm-audit` job adjudicates the reduced finding again.
verjson-editor landed its four generated callers, including the `ai-review-merge.yml`
admission caller. The live finding therefore shrank to 20 missing generated callers across
18 armed repositories (17 `ai-review-label-rearm.yml`, 3 `ai-promotion-retry.yml`). Any
change to the finding counts as drift, so the job failed on 2026-10-16 and 2026-10-17
until `config/audit-expected-findings.json` recorded it. The remaining callers are
consumer regeneration tracked by #1810.
