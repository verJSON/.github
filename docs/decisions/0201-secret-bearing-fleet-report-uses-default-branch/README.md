# 0201 — Keep the full fleet report in a private workflow run

- **Date:** 2026-09-22

## Context

Issue #1506 needs a periodic report containing adopter names, workflow paths, and pinned contract SHAs. `Verjson/.github` is public, so its workflow summaries, logs, and artifacts are visible to repository readers. The inventory also reads private organization repositories. Publishing its full output from the public repository would disclose private adopter metadata.

## Decision

The secret-bearing job uses fixed ephemeral GitHub-hosted capacity rather than a shared persistent runner. The reusable workflow accepts only scheduled runs or pushes to `main`; repository and ref checks alone do not establish the event trust boundary. Inventory fields use quoted TSV serialization, and report cells neutralize Markdown controls before rendering fleet-controlled paths.

`Verjson/.github` provides the inventory as a reusable workflow pinned by immutable commit SHA. The scheduled caller lives in the private `verjson-agents` repository, so its run summary, logs, and artifacts retain that repository's access controls. The reusable workflow refuses callers other than `Verjson/verjson-agents`, uses the passed read-only App credentials, and checks out its own source at `job.workflow_repository` and `job.workflow_sha`. This binds inventory code to the immutable workflow revision selected by the caller, so a caller cannot choose different code to run with the App token. The private caller runs on the default-branch schedule and on matching pushes to `main`; it has no `workflow_dispatch` trigger.

## Consequences

Persistent organization runners never receive the App private key. Manual and pull-request-target events cannot reach the token-minting job, and crafted workflow paths remain inert data in the private report.

The report remains actionable to authorized maintainers without publishing private repository names or pin state. A caller update must keep its reusable-workflow reference pinned to a reviewed immutable `.github` commit; the reusable workflow checks out that exact revision as its inventory implementation. Manual branch-selected runs remain unavailable while the caller uses the App private key.

## Amendment (2026-10-15, #1792) — the caller passes a client ID, not an App ID

The report shipped a guard requiring `app_client_id` to be numeric, then passed the value to
`actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1` (v3.2.0) as
`client-id`. That input is the App client ID (`Iv23li…`, or legacy `Iv1.…`); the numeric
value is the deprecated `app-id`. `verjson-agents` passes the organization's real
`RENOVATE_COMPATIBILITY_CLIENT_ID`, so every scheduled run since 2026-09-28 stopped at the
guard (verjson-agents run 38294876315). ADR 0119 already requires that credential to be
present and non-numeric before the token action runs.

The guard now refuses a numeric legacy App ID by name and otherwise accepts only
`^Iv[A-Za-z0-9.]{8,126}$`, the merge gate's client-ID pattern. The positive pattern refuses
empty, whitespace, newline, and shell-metacharacter values rather than relying on a
numeric deny-list. `scripts/ci-gate/fleet-contract-inventory.test.py` executes the
workflow's own step against each class.
