---
date: 2026-10-06
issue: 1698
impact: patch
title: Emit the canonical organization spelling in generated callers
---

The changelog caller generator now emits `verJSON` for organization-owned repositories, workflow references, renderer URLs, release runner selection, and CODEOWNERS. The npm scope and registry URLs remain unchanged. Contract tests cover the renamed output and the generated CODEOWNERS source.
