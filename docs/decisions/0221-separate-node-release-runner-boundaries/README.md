# 0221 — Isolate Node release jobs on fresh hosted runners

- **Date:** 2026-10-09
- **Issue:** [#1724](https://github.com/Verjson/.github/issues/1724)
- **Supersedes:** the shared runner-pool clause for Node publication in [ADR 0069](../0069-node-publication-consumes-contract-version/README.md)
- **Category:** runner topology and package credentials — **sensitive class**

## Context

ADR 0069 routes generated release verification, changelog snapshot, and Node
publication through the caller's selected runner pool. Issue #1724 separates
Node package preparation from publication because preparation executes
repository and dependency scripts while publication and retention require write
permissions.

A persistent shared runner cannot provide the required credential boundary.
Checking process ancestry misses same-user processes, and scanning `/proc` is a
point-in-time best effort that can miss processes started later or whose
environment is unreadable. Repository-controlled lifecycle and build steps must
not run where another process can inspect the package token.

## Decision

Every job in `node-release.yml` runs on a fresh GitHub-hosted `ubuntu-24.04`
runner. The legacy `runner` input remains accepted so existing generated callers
continue to validate, but it is ignored. The generated caller may continue to
route changelog verification and snapshot through its configured runner policy;
the Node release workflow always uses hosted runners.

The preparation job masks `NODE_AUTH_TOKEN` at job scope and exposes the secret
only to `npm ci --ignore-scripts`. Dependency lifecycle scripts, metadata
preparation, version stamping, builds, and packing run without the token. The
publisher and retention jobs keep their existing permissions, artifact

Each job's first step checks `runner.environment` and stops unless GitHub
reports `github-hosted`. The label `ubuntu-24.04` alone does not prove runner
type because a self-hosted runner can carry that custom label; the runtime
guard protects the token-bearing publisher and retention jobs from label
collisions.
validation, publication, provenance, and restart-safety behavior.

This supersedes only ADR 0069's shared runner-pool statement for Node release.
The immutable contract pinning, release version, tag, and restart-safety
decisions remain in force.

## Consequences

All Node release work requires available GitHub-hosted runner capacity, which
increases hosted runner usage for callers that previously ran preparation on
self-hosted machines. Callers that require private network access during
preparation must make those dependencies available to the hosted job or defer
release until they can. A caller-selected persistent runner is not a supported
credential boundary.
