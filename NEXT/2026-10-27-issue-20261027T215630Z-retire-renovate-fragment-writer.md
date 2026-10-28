---
date: 2026-10-27
id: 20261027T215630Z
refs: 1869
impact: patch
title: Require agent-authored changelog context for dependency updates
---
The canonical changelog contract no longer generates release fragments from
Renovate metadata. Agents must exercise dependency upgrades and describe their
behavior in `NEXT/`; the existing required changelog validation remains in
force. The generated adopter contract uses a bounded, source-wide workflow scan
that rejects case-insensitive raw, folded, aliased, and supported escaped
mentions of the retired path, including comments and run bodies. This guard
normalizes escaped CRLF, CR, LF, NEL, line separator, and paragraph separator
continuations, and stops at the workflow-count limit before inspecting another
candidate. It intentionally overmatches inert mentions. ADR 0243 supersedes
the automated writer decision in ADR 0104.
