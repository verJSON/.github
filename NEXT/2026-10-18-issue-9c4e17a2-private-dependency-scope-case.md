---
date: 2026-10-18
id: 9c4e17a2
impact: patch
title: Accept the owner's scope spelling in container private-dependency download URLs
---

Fix: the container candidate's private-dependency acquisition
(`scripts/container_private_dependencies.py`) now accepts a GitHub Packages download URL
that records the organization's own spelling of the scope, such as
`https://npm.pkg.github.com/download/@verJSON/pg/1.2.3/<hash>`, for the lowercase package
`@verjson/pg`. Before this change, the scope was compared case-sensitively, so every lock
entry resolved with that URL was rejected as an unapproved private package. Only the scope
is folded, and only when it is ASCII: Python's `str.lower()` maps the Kelvin sign (U+212A)
to `k`, so a non-ASCII scope is rejected rather than folded. The package name, version,
approved-list membership, and the exact URL shape are still matched exactly, as the
generated release caller's private lock validator already does (#1713). Adopters pick up
the fix when their container-candidate caller is regenerated at a contract SHA that
includes it, because the caller pins this script by digest.
