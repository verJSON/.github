---
date: 2026-10-26
id: 20261026T155037Z
refs: 1817
impact: patch
title: Add isolated PostgreSQL schema validation profile
---
Part #1817. Add opt-in `postgres-schema-validation-17.11` profile three fixed databases and socket-only URLs schema snapshot generation, migration replay, and reference comparison. Trusted setup uses pinned PostgreSQL 17.11 server and client bundle, seeds only fixed auth/storage prelude in migration database, grants `CONNECT` to non-administrative candidate role on each database, and grants `TEMP` only migration replay. Hosted acceptance exercises Drizzle generation, migration replay, normalized schema comparison, database isolation, and AppArmor boundary. Profile runs still have no network egress or Docker access.

The client wrappers now pass their fixed executable names through the pinned loader, so PostgreSQL startup can validate the wrapper while copied ELF payloads remain non-executable and read-only. Staging fails closed if the loader does not support this, and hosted acceptance exercises ordinary `psql` startup plus a real schema-only `pg_dump`.
