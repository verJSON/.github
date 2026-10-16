---
date: 2026-10-16
id: 20261016T145143Z
impact: patch
title: Record the live receipts for both #1828 required-workflow rotations
---

ADRs 0151 and 0153 now record the exact-head `verify-run` receipts that complete the
rotations of rulesets 21750617 (authn) and 21830367 (CLI projects) to `c78fcd4e` (Part of #1423). Both
exited 0 against fresh organization-rule runs. ADR 0153 also records why the first CLI
projects run was refused: its head still carried the pre-generator caller.
