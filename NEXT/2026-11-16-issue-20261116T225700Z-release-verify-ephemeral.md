---
date: 2026-11-16
id: 20261116T225700Z
impact: patch
title: Route release verify off the persistent pool for public repositories
---

Part of #1831. `gen-changelog-caller.sh` `release_runner_expr` now places
public repositories on literal ubuntu-24.04 before the private Verjson
CI_RUNNER_DEFAULT tail, so release verify (and the matching snapshot/publish
runner inputs) cannot land public dispatch on the persistent pool while both
halves of a release still share one pool (#465). The live verify pending row
leaves; publish and snapshot remain remote-callee backlog rows.
