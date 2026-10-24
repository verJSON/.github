---
date: 2026-10-24
id: 20261024T134640Z
refs: 1869
impact: patch
title: Adopt the shared Node CI script-plan parser
---
Canonical secretless Node CI callers now use the shared parser action pinned to its immutable merged commit for plan normalization and service-grant decisions. The workflow executes the normalized plan while validating its root or nested manifest, and the protected twin and conformance fixture are regenerated from the canonical source.
