---
date: 2026-11-14
issue: 2067
impact: minor
title: Map Node CI caller secrets to repository secret names
summary: Generated Node CI callers can map declared secret inputs to valid repository secret names, and checkout credentials no longer persist into install scripts.
---

`ci-levels.json` accepts an object mapping reusable-workflow secret names to caller repository secret names while preserving the same-name array shorthand. Node CI still uses `submodules-token` for private submodule checkout, but no longer leaves the checkout authentication reference active for later install or test commands. See ADR 0264.
