---
date: 2026-10-24
id: 20261024T141139Z
refs: 1869
impact: patch
title: Bound build-once artifacts to same-run credentialless CI
---
ADR 0238 records the owner-selected build-once direction and the ARB's conditional controls: same-run artifact identity, trusted host-side export and receipts, isolated credentialless consumers, confidentiality gates, and no promotion reuse. Unsafe or network-dependent plans retain per-lane builds. The #1692 deployment candidate chain remains separate.
