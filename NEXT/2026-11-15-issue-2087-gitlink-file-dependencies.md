---
date: 2026-11-15
issue: 2087
impact: minor
title: Container acquisition accepts a pinned gitlink file dependency
---

Container candidate publication can install an npm `file:` dependency whose target is a git submodule pinned in the published commit ([ADR 0267](../docs/decisions/0267-container-gitlink-file-dependencies/README.md), #2087). The credentialed install stays `npm ci --ignore-scripts`. The submodule is fetched with the scoped token only for that checkout, and its own `npm ci` runs later with no package token so its `prepare` build is inside the sealed `node_modules` tree.

Git dependencies and every other lock entry without a registry tarball and sha512 integrity are still rejected. Callers that already pass `NODE_AUTH_TOKEN` also pass `submodules-token` from `SUBMODULES_TOKEN`. Adopters get the behavior by regenerating at a contract commit that contains this change.

Container-candidate workflow edits change the settable-guard inventory; `GUARD_COUNT` moves from 543 to 551.
