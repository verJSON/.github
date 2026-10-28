---
date: 2026-10-27
id: 20261027T091057Z
impact: patch
title: Inline gate-rearm app-key validation
---
`gate-rearm.yml` validates its main-only environment policy and AI-review App key in separate inline jobs. The validator bodies are tested against `app-key-environment.yml`; key validation remains environment-bound and runs without a checkout. Public and unresolved pull-request callers select GitHub-hosted runners before lane variables, and watchdog admission follows that placement even when a stale untrusted-lane variable names a persistent runner. Rejected `verjson-ci` profiles fail on a fixed hosted runner before any work starts. ADR 0242 records the #1831 security decision and supersedes the pinned nested-call choice in ADR 0171.
