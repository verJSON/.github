# 0259 — Stage the locked Prisma schema engine outside the offline candidate sandbox

- **Date:** 2026-11-10
- **Status:** Proposed. Implemented for review in [#2052](https://github.com/VerJSON/.github/issues/2052).
- **Issue:** [verJSON/.github#2052](https://github.com/VerJSON/.github/issues/2052)
- **Blocked consumer:** [verJSON/unrwly#99](https://github.com/VerJSON/unrwly/issues/99) and [draft PR #194](https://github.com/VerJSON/unrwly/pull/194)
- **Related:** [ADR 0086](../0086-secretless-node-pr-validation/README.md), [ADR 0095](../0095-bounded-secretless-node-cache-transfer/README.md), [ADR 0239](../0239-unified-untrusted-execution-boundary/README.md)
- **Category:** CI execution boundary (sensitive class: external executable staged for untrusted scripts)

## Context

The catalog database lane runs `prisma migrate deploy` from a credentialless,
network-isolated Node CI script plan. The workflow installs dependencies with
scripts disabled, so Prisma's normal install-time engine download does not run.
When the migration script starts, Prisma otherwise tries to fetch the locked
schema engine and fails inside the sandbox. This blocks [unrwly#99] and its
catalog database checks.

Allowing candidate scripts to reach the public network would change the
secretless execution boundary for every caller of the reusable workflow. The
required capability is narrower: provide the exact locked engine to the
existing offline sandbox before any candidate script executes.

## Decision

1. Trusted workflow code reads only the caller's root `package-lock.json`.
   Lockfile versions 2 and 3 are supported. When Prisma engine metadata is
   present, the lock must contain one `@prisma/engines` package, a matching
   `@prisma/engines-version` package, and consistent `prisma` package versions
   when the CLI package is present. The engine-version suffix must be a
   lowercase 40-hex commit.
2. Engine staging supports only a GitHub-hosted Linux x64 runner identified as
   Ubuntu 24.04 with OpenSSL 3.0. It maps that runner to Prisma's
   `debian-openssl-3.0.x` target. Other metadata or targets fail before a
   binary download and before candidate scripts run.
3. The trusted stager constructs the artifact and checksum URLs from the
   validated commit and target under the fixed HTTPS host
   `binaries.prisma.sh`. It rejects redirects, bounds both downloads and the
   decompressed executable, checks the published checksum's target path, and
   verifies the compressed artifact's SHA-256 before accepting an x86_64 ELF
   executable. The published checksum detects transfer or artifact mismatch;
   it is served by the same provider as the binary and is not an independent
   signature of the provider's origin.
4. The stager performs no candidate script execution. It writes only
   `schema-engine` and a run-scoped owner marker in a private directory under
   `RUNNER_TEMP`. The candidate sandbox sees the engine through a read-only
   Bubblewrap bind at `/run/verjson-prisma/schema-engine` and receives that
   fixed path as `PRISMA_SCHEMA_ENGINE_BINARY`. The host path, stager, and
   owner marker are removed from the candidate environment. The generated
   protected workflow carries the same binding through its trusted candidate
   helper, with the run identity retained only in the trusted step environment.
5. The existing candidate network and credential policy remains unchanged.
   In particular, the Prisma engine does not receive external network access;
   database scripts retain only the service access already granted by their
   selected sandbox profile.
6. An `always()` cleanup step runs after the script plan, including after
   failure or cancellation. Cleanup validates the exact run ID and attempt in
   a complete owner marker. It also reclaims an empty run-scoped directory or
   a partial owner marker left by interruption during setup, provided no engine
   is present. It rejects symlinks and unexpected files and removes only that
   run's stage. An exit trap removes the downloaded helper even if cleanup
   fails, while preserving the failure status.

## Consequences

- Prisma migrations continue to use `prisma migrate deploy`; the workflow only
  supplies the engine binary that the locked CLI expects.
- Non-Prisma npm jobs do not download or stage an engine. This implementation
  reads npm lockfile versions 2 and 3; it does not add an equivalent metadata
  reader for pnpm or Yarn lockfiles.
- The engine checksum is verified against Prisma's published checksum on the
  fixed binary host. The trust root remains that provider and HTTPS; this does
  not claim an independent publisher signature.

## Standards grounding

- Status: grounded, no gaps.
- Catalog/config: `VerJSON/verjson-docs@c699fb4cb3477690ee7f319e6cde647c84d76bde`.
- Workflow and security contract: `VerJSON/.github@635aca975b1aa93660138875ba9616b27693476f`.
- Engineering and security guidance: `VerJSON/verjson-agents@ca4cfe2e8ce582923420baf46ad9fafe19a9386f`.
