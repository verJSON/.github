---
date: 2026-10-15
id: 20261015T230400Z
impact: patch
title: Repin the authn required workflow to node-ci-protected v4.0.0
---

Hardening: the generated `authn-type-surface-required.yml` now calls
`node-ci-protected.yml` at `576707fe` (v4.0.0), which contains #1750 and #1759,
instead of `3f8fb4ba`. Organization ruleset 21750617 keeps running the old revision
until an operator rotates it to this change's merge commit
([ADR 0151](../docs/decisions/0151-authn-type-surface-required-workflow/README.md)).

The authn rotation tool can now rotate the existing ruleset. Previously it could only
create one, so the earlier activation needed a one-off procedure. `dry-run` and `apply`
read the predecessor from the live ruleset, admit it with the same canonical-revision
check as the CLI projects tool, and restore and verify it if the write fails. A new
read-only `audit` mode backs the scheduled `required-workflow-audit` job. The finished
`stage-release-bypass` transition is retired along with its hard-coded `f2f425e9`
predecessor.
The package type-surface policy now delegates this ruleset's required-workflow revision
(`"sha": null`) instead of pinning `dc6b2942`, so a rotation no longer turns its audit red.
