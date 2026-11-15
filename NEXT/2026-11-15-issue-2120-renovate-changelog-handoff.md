---
date: 2026-11-15
issue: 2120
impact: patch
title: Report when a Renovate pull request still needs changelog context
---

Renovate dependency pull requests that lack a valid NEXT fragment now get a
`needs-changelog-context` annotation, and the organization Renovate preset
tells the agent how to author that fragment. The changelog merge gate is unchanged.

ADR 0243 now points this handoff at #2120. `check-pr` still rejects a missing
fragment for every author, and no workflow writes the fragment. The annotation
is informational (`continue-on-error`) on `changelog-validate.yml`, so it
appears when an adopter's changelog caller pin includes this workflow. The
preset text is the signal on the pull request itself. The annotation encoder
runs under `python3 -I`, and the handoff contract tests use file-backed grep
so they do not reintroduce pipe-fed `grep -q` under #1430/#1445.
