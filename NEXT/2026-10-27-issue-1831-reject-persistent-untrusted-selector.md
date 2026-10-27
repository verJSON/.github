---
date: 2026-10-27
issue: 1831
impact: patch
title: Keep public CI and gate-key validation on hosted runners
---
The runner-admission reconciler now reports an untrusted selector that resolves
to the persistent general lane as known drift before runner-group inspection.
The scheduled workflow can record the drift through its existing issue path.
Hosted routing and explicitly classified untrusted-group routing remain valid.
This check detects selector drift; it does not attest physical runner
ephemerality or enforce workflow routing.

`gate-rearm.yml` now validates its main-only environment policy and App key in separate inline jobs, with both shell bodies checked against `app-key-environment.yml`. The key remains environment-bound and is checked without a checkout; public or unresolved pull-request calls select GitHub-hosted runners before lane variables. The watchdog admission rule follows that hosted placement even if a stale untrusted-lane variable names a persistent runner. ADR 0242 records the updated boundary and supersedes only ADR 0171's pinned nested-call decision.
