---
date: 2026-10-27
id: 20261027T073154Z
refs: 1831
impact: patch
title: Make the public verjson-ci adapter locally auditable
---
Part #1831. The canonical GitHub adapter now runs its `public-v1` profile locally from a hash-verified snapshot of the pinned `verjson-ci` workflow. Public pull requests use an explicit GitHub-hosted runner; public repositories fail explicitly when they request a protected profile. The protected candidate profile remains delegated only for the private canonical repository's main-branch dispatch and retains the source callee's Verjson-only feature flag; unsupported candidate requests fail instead of leaving the required check skipped. Scenario values pass through the environment before shell use, and the arbitrary result-copy path is not exposed. The local artifact uploader follows the canonical pinned action version while the immutable source snapshot remains byte-verified at its recorded commit.
