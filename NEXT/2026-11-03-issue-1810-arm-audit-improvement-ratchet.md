---
date: 2026-11-03
issue: 1810
impact: patch
title: Stop scheduled audits from reddening when a recorded finding improves
---
`scripts/audit-finding-state.py` now accepts a recorded item set (`finding_class` plus the full sorted `items`) on an expectation entry. When the observed set for that class is a strict subset, the run is a `match` with a `::notice` and a step-summary entry to re-record. When it is empty, the run reads as no finding and asks for the expectation to be removed. Any new item, other class, expired entry, or missing evidence still drifts, and malformed or contradictory evidence is undetermined (ADR 0189, 2026-10-21 amendment). Structured items reject every C0 control character and DEL.

The authorization-arm audit now reports `finding_classes` on stdout for every class it evaluated, empty ones included, beside its one `ERROR:` line per finding. The main-protection suspension (#1933) stays a constant line compared by exact text. The recorded #1810 entry keeps its finding and fingerprint and gains the 20-item set its own `items=` digest commits to, recovered and verified against that digest. Re-recording it against the current fleet is separate work. The ratchet was prompted by scheduled run 39619145007 on 2026-10-21, which went red because the finding improved from 20 to 17.
