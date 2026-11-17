# 0270 — Advisory quality review through a provider-neutral evaluation port

- **Date:** 2026-11-17
- **Status:** Accepted (records owner decisions of 2026-10-15 on [#1820](https://github.com/verJSON/.github/issues/1820); ARB chair confirmation in the default-pm session).
- **Issue:** [verJSON/.github#1820](https://github.com/verJSON/.github/issues/1820)
- **Related:** verjson-agents ADR 0032 (local skill, per-payload consent), [#1385](https://github.com/verJSON/.github/issues/1385) (secret visibility), [#1810](https://github.com/verJSON/.github/issues/1810) (adopter callers)
- **Category:** CI advisory evaluation (non-required check)

## Context

Canonical CI should be able to run an advisory quality review of a pull
request's filtered diff when a repository opts in. Jev (TypeSafe) is the first
hosted evaluator. Similar evaluators must be swappable without changing the
rubric, workflow, or callers. The review must never become a required check or
feed merge authorization.

## Decision

1. **Provider-neutral port.** `scripts/quality_review/` exposes
   `evaluate(state, questions) -> answers` with two question kinds:
   `probability` (yes/no likelihood in `[0, 1]`) and `level` (ordinal label plus
   confidence in `[0, 1]`). Adapters live under `providers/`. Provider output is
   untrusted; only validated scalars reach the report.
2. **Manifests.** `config/quality-review-providers.json` maps provider ids to
   endpoint, adapter, secret name, pinned default model (never `*-latest`), and
   request token cap. `config/quality-review-rubric.json` holds provider-neutral
   dimensions, screening threshold, confidence floor, and severity levels.
3. **Standing consent.** A repository's two-key opt-in — mode variable set and
   membership on the provider secret's `selected` list — is standing
   authorization to send its filtered, secret-scanned PR diffs to the configured
   provider. This extends verjson-agents ADR 0032 into CI.
4. **Advisory only.** Quality review is never a required check, never feeds
   `ai-review-merge.yml` or its prompt, and never authorizes or blocks a merge.
   Outage, rate-limit, and schema-violation paths leave the job green and report
   that quality evidence was not obtained.
5. **Disclosure boundary.** Only the filtered PR diff is sent. Lockfiles,
   generated, vendored, binary, and env files are dropped. gitleaks, pinned by
   digest, scans every chunk fail-closed: absent or failing scanner means
   nothing is sent; a finding drops that chunk.

## Consequences

Adding a provider means an adapter, a manifest entry, and a secret — not a
rubric or workflow change. `JEV_API_KEY` stays at `selected` visibility with the
canary repository list. Live workflow and canary rollout remain later #1820
slices.

## Verification

- Shared contract suite covers the fake provider and the jev adapter against a
  stub HTTP server (`scripts/quality_review/providers/contract.test.py`).
- Manifests refuse `*-latest` models and load through `quality_review.config`.
