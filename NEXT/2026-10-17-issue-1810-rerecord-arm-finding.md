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

The finding text the adjudicator fingerprints now ends with `items=sha256:<hex>`, a digest
of the full sorted item list, so a same-count change among the items hidden behind
`(plus N more)` is now drift rather than a silent match
([ADR 0189](../docs/decisions/0189-scheduled-audits-adjudicate-findings-against-a-reviewed-expectation/README.md)
amendment of 2026-10-18).
