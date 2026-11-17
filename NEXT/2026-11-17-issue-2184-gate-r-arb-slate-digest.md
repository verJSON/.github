---
title: Gate R parses ARB run id + slate digest from preceding COMMENT
---

Require a preceding exact-head COMMENTED review whose body is the canonical
`arb-consensus:v1` citation marker (run id + 64-hex slate digest + archive
locator). Verify the digest against the immutable archived chair/ledger file.
Keep the ADR 0202 receipt marker digest-free. Fixes #2184 / part of #2182.
