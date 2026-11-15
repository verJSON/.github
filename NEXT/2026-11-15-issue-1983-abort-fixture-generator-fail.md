---
date: 2026-11-15
issue: 1983
impact: patch
title: Abort caller-contract shards when a must-succeed generator call fails
---

`write_generated_fixture` and `read_generated_fixture` now exit the shard when generation fails or emits nothing, so an empty release workflow cannot masquerade as a valid rejection while `fail` is suspended between cases.

Every must-succeed redirect in `scripts/ci-gate/changelog-caller-contract.test.sh` routes through those helpers; expected-refusal probes keep their explicit status checks. Follow-up to #1974 for the remaining fixture-generation sites board action A-10 identified. Closes #1983.

Caller-contract fixture abort coverage changes full-checkout bytes seen by
`release.yml` verify, so the ADR 0235 pending backlog digest moves to
`sha256:1dbf9d08…`.

Fixture-abort coverage changes the settable-guard inventory scanned from the caller-contract tests; `GUARD_COUNT` moves from 543 to 551.
