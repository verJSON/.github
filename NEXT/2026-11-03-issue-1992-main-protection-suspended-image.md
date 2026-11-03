---
date: 2026-11-03
issue: 1992
impact: patch
title: Report the main-protection suspension as a reviewed state
---
The daily authorization-arm audit recognizes `main-protection` (18098028) disabled under the owner hold in #1933 as a third reviewed state, `suspended`, instead of stopping at its first check. It reports the suspension as one constant finding and then runs every later check exactly as it does for `split`, so the #1810 consumer-drift finding and any new regression reach the report again.

- The `suspended` image is derived from the split postimage with only `enforcement: disabled`; a contract may not declare one, and the reviewed protection images must be active. The report state reads `suspended`, or `suspended-legacy-selector`. No render mode runs from it.
- The audit now reports one `ERROR:` line per finding instead of joining them with ` | `, so each finding is fingerprinted and adjudicated on its own (ADR 0189). A single-finding run's text is unchanged.
- `config/audit-expected-findings.json` records the suspension finding under #1933, expiring 2026-12-03. Once enforcement is restored the entry goes stale and the scheduled run stays red until it is removed.
- Decided by ARB RUN-D (#1992) and recorded in ADR 0254, which amends ADR 0094.
