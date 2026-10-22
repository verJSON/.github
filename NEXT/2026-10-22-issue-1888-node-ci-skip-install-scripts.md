---
date: 2026-10-22
issue: 1888
impact: minor
title: node-ci accepts secretless-skip-install-scripts for packages whose install scripts are never run
---

`node-ci.yml` and the generated `node-ci-protected.yml` accept `secretless-skip-install-scripts`, a newline-separated list of exact package names whose install scripts are deliberately not run in the offline secretless rebuild, such as an optional `canvas`. Every script-bearing installed package must be on exactly one of this list or `secretless-rebuild-packages`; overlap, stale, and absent entries fail closed, and the job log prints the skipped set.

Implements #1888 at the workflow level; the downstream repin of terptechpub/self-publish-ai-app#2041 is separate. Amends ADR 0086 (2026-10-22). Skipped packages are never passed to the package manager, so strictly less code runs.
