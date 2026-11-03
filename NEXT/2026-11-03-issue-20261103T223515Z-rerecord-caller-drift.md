---
date: 2026-11-03
id: 20261103T223515Z
refs: 1810, 1992
impact: patch
title: Re-record the authorization-arm caller drift at its live value
---

Maintenance: the scheduled `arm-audit` job adjudicates the #1810 consumer-drift finding
again. The main-protection suspension image (#2002, ADR 0254) lets the audit reach this
check once more, and the finding grew from 20 to 35 missing generated adopter callers across
23 armed repositories. The recorded 20-item entry was therefore stale beside a new
finding. `config/audit-expected-findings.json` now records the live text, fingerprint
`367c4bb7…`, and its full sorted item set (`items=sha256:bd945149…`).

Of the 35, 12 were already recorded (9 `ai-review-label-rearm.yml`, 3
`ai-promotion-retry.yml`). 8 recorded `ai-review-label-rearm.yml` callers have been
regenerated since. 23 `ai-review-lifecycle-rearm.yml` callers are new, because #1993 added
that caller to the completeness set; consumers regenerate it under #1982. Actions-paused
repositories are still counted, so the finding stays conservative. The digest was
cross-checked against an independent per-repository enumeration of each armed
repository's default-branch tree, as the
[ADR 0189](../docs/decisions/0189-scheduled-audits-adjudicate-findings-against-a-reviewed-expectation/README.md)
2026-10-18 amendment requires. The entry keeps its 2026-12-31 expiry.
