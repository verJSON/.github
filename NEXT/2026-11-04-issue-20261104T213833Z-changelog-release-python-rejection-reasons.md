---
date: 2026-11-04
id: 20261104T213833Z
refs: 1994
impact: patch
title: Changelog-release Python negative tests name the guard that must reject them
---

The negative tests in the Python files of the `changelog-release-1` through
`changelog-release-4` CI groups now assert why they were rejected. They check the
exact exception message, the exact exception the test injected, or the CLI's exit
status and stderr line. Before this, any exception of the right type or any
nonzero exit passed. A guard could stop working while an earlier, unrelated check
kept the test green.

These tests cover:

- the container deployment transport, controller and registry destinations;
- the private dependency, pnpm, Cosign and candidate-retry evidence checks;
- release promotion;
- the changelog engine's fragment and pre-contract migration checks;
- the tag-visibility and runner-canary workflow contracts;
- the changelog caller generator's refusals.

The deployment transport test said that host export never acquires credentials.
It used to build its host-export request with the probe's fields, so request
validation rejected it before the credential path ran. It now uses a real
host-export request, which is refused because the dedicated App key is missing.

No production code changes. Part of #1994. The container contract `.test.sh` files
and the remaining Bash files are left to the last leg.
