---
date: 2026-10-26
id: 20261026T031853Z
refs: 1869
impact: patch
title: Add a constrained PostgreSQL 17.11 sandbox profile
---
Part #1869. Add an explicit, default-off amd64 PostgreSQL 17.11 profile pinned to the reviewed manifest digest. Trusted setup provisions an isolated per-run database with no network or published ports, stages only the same-image `psql` and `pg_dump` clients, and exposes them with the Unix socket through read-only Bubblewrap mounts. The candidate role can use `app_test` and create objects in `public`, but cannot administer the server. The profile rejects legacy service requests before starting any service. When selected, it rejects `producer`/`consumer` ArtifactSet roles and direct or protected-baseline-resolved `secretless-compatibility-ranges` requests before credentialed dependency acquisition. Ordinary `level` runs remain allowed; profile runs retain `--unshare-net` and fail closed on runtime boundary changes. The hosted acceptance fixture pushes a small Drizzle schema to one fresh database, applies routines and the matching migration to a second, then compares normalized dumps. The external canary's `postgres:17.6` reference is adopter drift, not a reason to downgrade the canonical profile. Local execution does not establish hosted AppArmor acceptance.
