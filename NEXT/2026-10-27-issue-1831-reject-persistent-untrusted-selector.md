---
date: 2026-10-27
issue: 1831
impact: patch
title: Reject persistent untrusted selector
---
The runner-admission reconciler now reports an untrusted selector that resolves
to the persistent general lane as known drift before runner-group inspection.
The scheduled workflow can record the drift through its existing issue path.
Hosted routing and explicitly classified untrusted-group routing remain valid.
This check detects selector drift; it does not attest physical runner
ephemerality or enforce workflow routing.
