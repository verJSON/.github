---
date: 2026-11-12
issue: 2005
impact: patch
title: Document the suspended main-protection finding in the arm rollout audit
---

`suspension_finding` in `scripts/ai-review-required-workflow-audit.py` now
carries a docstring, matching `suspended_image` beside it: it names the standing
finding reported while main-protection is suspended under the owner hold (#1933)
and why that finding leads the report. Documentation only; behavior is unchanged
(#2005).
