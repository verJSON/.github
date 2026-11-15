---
date: 2026-11-15
issue: 1869
impact: patch
title: Keep secretless lane helpers import-safe without reordering validation
---

The extracted compatibility and rebuild helpers keep FunctionDefs and
data-only bindings at module scope (for AST contracts and `-I` import
probes) while running only the env/IO body under `__main__`. Assign and
def order inside the candidate service-validation slice stays aligned
with `run_protected_candidate_plan`, so rebuild-env byte-equality still
holds.
