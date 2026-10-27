---
date: 2026-10-27
id: 20261027T073154Z
refs: 1831
impact: patch
title: Make the public verjson-ci adapter locally auditable
---
Part #1831. The canonical GitHub adapter now runs its `public-v1` profile locally from a hash-verified snapshot of the pinned `verjson-ci` workflow. Public pull requests use an explicit GitHub-hosted runner; public repositories fail explicitly when they request a protected profile. The protected candidate profile remains delegated only for the private canonical repository's main-branch dispatch. Scenario values pass through the environment before shell use, and the arbitrary result-copy path is not exposed.
