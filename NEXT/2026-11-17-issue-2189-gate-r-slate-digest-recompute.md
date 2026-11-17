---
date: 2026-11-17
issue: 2189
impact: minor
title: Gate R recomputes SHA-256 over canonical-slate-json
---

Bind the arb-consensus citation slate digest to SHA-256 of the archive's
`canonical-slate-json` fence body (single JSON line, matching `run_id`).
Fail closed on missing fence, wrong encoding, decode/schema errors, or digest
mismatch. Amend ADR 0271. Fixes #2189.
