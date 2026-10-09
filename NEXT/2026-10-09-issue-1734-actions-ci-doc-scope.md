---
date: 2026-10-09
issue: 1734
impact: patch
title: 'ci: skip the heavy actions-ci matrix for documentation diffs'
---

Documentation-only diffs run the documentation contracts and skip the release-caller, merge-gate, and hosted compatibility jobs. Any other diff, including an empty file list, still runs the heavy matrix, and the required `shell-tests` check fails if those two disagree.

See #1734 and ADR 0222.
