---
date: 2026-11-18
issue: 2250
impact: patch
title: Relay the final safe host-export diagnostic
---

Dry-run 44253156883 reported only `canonical host evidence transport failed`
after the doctl auth probe. The controller required the entire transport
stderr blob to be one line, and the transport relayed the *first* line of
doctl and CLI output, so a warning, banner, or non-UTF-8 decode traceback hid
the real diagnostic.

Both layers now relay only the final output line, and only when it is short,
printable, free of credential markers, and contains no line of a provisioned
secret; an unsafe final line is withheld rather than replaced by an earlier
one. The controller withholds any traceback (including exception groups),
whose final exception repr can carry a command line. doctl, the deployment
CLI, and baseline attestation output decode with replacement characters.

Fixes #2250. Part of verJSON/verjson-git-runners#197.
