---
date: 2026-11-14
issue: 2069
title: Select the GitHub package-read token in generated Node CI callers
impact: patch
---

`ci-levels.json` can opt into the fixed per-run `GITHUB_TOKEN` for internal
package reads only with secretless acquisition on each ordinary level.
Generated callers keep `packages: read`, preserve the build-artifact
authorization-plan format, and leave the generic repository secret alias map
closed to `GITHUB_*` names and arbitrary expressions.
