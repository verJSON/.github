---
date: 2026-10-13
issue: 1758
impact: patch
title: Repair this repository's release verification header match
---

`scripts/release-verify.sh` again finds the pinned generator header in this repository's own release caller. It still matched the retired `Verjson/.github` spelling, so after #1702 it rejected every self-release at `verify` with "carries no package-release pin header". `scripts/release-verify.test.sh` now runs the hook in a disposable repository against the real generator, covering a matching caller, a hand-edited caller, the retired spelling, an empty dispatched version, and a failing verification command.
