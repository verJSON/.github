---
date: 2026-11-03
issue: 1992
impact: patch
title: Report the main-protection suspension as a reviewed state
---
The daily authorization-arm audit recognizes `main-protection` (18098028) disabled under the owner hold in #1933 as a third reviewed state, `suspended`, instead of stopping at its first check. It reports the suspension as one constant finding and then runs every later check exactly as it does for `split`, so the #1810 consumer-drift finding and any new regression reach the report again.

- The `suspended` image is derived from the split postimage with only `enforcement: disabled`; a contract may not declare one, and the reviewed protection images must be active. The report state reads `suspended`, or `suspended-legacy-selector`. No render mode runs from it.
- Everything else that keeps `split` red keeps `suspended` red: any other asserted field, any enforcement but `active` or `disabled`, the pre-split image disabled, arm-ruleset drift or non-exclusivity, and every later check, fail-fast in the same order. A mismatch now names the nearest of the three images.
- The audit now reports one `ERROR:` line per finding instead of joining them with ` | `, so each finding is fingerprinted and adjudicated on its own (ADR 0189). A single-finding run's text is unchanged.
- `config/audit-expected-findings.json` records the suspension finding under #1933, expiring 2026-12-03. Once enforcement is restored the entry goes stale and the scheduled run stays red until it is removed.
- The rollout contract now pins ADR 0252's Actions-paused exclusion (#1985) on the arm ruleset (both selector images) and on `core-checks-node`, which the live rulesets carry and which stopped the audit at the arm ruleset. A missing or widened exclusion is drift; removing it on ADR 0252's rollback is a contract change to `[]`.
- The replacement-workflow check now expects the arm job's `runs-on` routing exactly as #1927 merged it at `24db445f`: public pull-request events on a hosted image, everything else on the trusted lane. Any other routing is still drift.
- Decided by ARB RUN-D (#1992) and recorded in ADR 0254, which amends ADR 0094.
