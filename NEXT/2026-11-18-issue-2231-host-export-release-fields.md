---
date: 2026-11-18
issue: 2231
impact: patch
title: Omit releaseVersion from host-export transport release object
---

Host-export requests built via `_release()` kept `releaseVersion` after the
transport fields were merged. Transport `validate_request` requires an exact
release key set without that field, so dry-runs failed closed as opaque
`canonical host evidence transport failed` before attestation. Emit the
transport-exact release object and relay a single short TransportError line.

Fixes #2231.
