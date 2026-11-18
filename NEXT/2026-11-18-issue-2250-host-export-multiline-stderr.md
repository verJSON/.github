---
date: 2026-11-18
issue: 2250
impact: patch
title: Relay only allowlisted host-export diagnostics
---

Dry-run 44253156883 reported only `canonical host evidence transport failed`
after the doctl auth probe. The controller required the entire transport
stderr blob to be one line, and the transport relayed the *first* line of
doctl and CLI output, so a warning, banner, or non-UTF-8 decode traceback hid
the real diagnostic.

Host-export and adapter diagnostics now relay only allowlisted forms
(ADR 0198, 2026-11-18 amendment):

- doctl failures map to fixed messages (`doctl access token is required`,
  `doctl authentication rejected (HTTP 401)`, `doctl API request rejected
  (HTTP <status>)`, `doctl probe failed`); doctl's own text is never relayed.
  The probe reads stdout too, where `--output json` writes the error.
- The deployment CLI's last `✗`-marked error line and an adapter command's
  first `::error::` line relay only as short plain prose (letters, digits,
  spaces and `.,:;'()/_+-`): no URL, query, assignment, double quote, brace,
  command flag, token-shaped run, credential marker, or line of a secret the
  process holds. An unmarked crash relays nothing.
- The transport marks its deliberate exit line `deployment transport: `; the
  controller relays only that marked line, in the same form, and nothing when
  any traceback is present.

A nonconforming line is withheld, never replaced by an earlier one. doctl,
the deployment CLI, and baseline attestation output decode with replacement
characters. The controller imports the allowlist from the transport, which
the generator emits and digest-checks beside it, so one definition governs
both.

Fixes #2250. Part of verJSON/verjson-git-runners#197.
