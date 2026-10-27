# 0242 — Inline app-key validation in the required gate workflow

- **Date:** 2026-10-27
- **Status:** Accepted
- **Issue:** [verJSON/.github#1831](https://github.com/verJSON/.github/issues/1831)
- **Category:** runner placement and secret handling — **sensitive class**
- **Related:** [ADR 0171](../0171-inherited-reusable-environment-context/README.md),
  [ADR 0235](../0235-public-pull-request-code-uses-ephemeral-runners/README.md)
- **Supersedes in part:** ADR 0171's 2026-09-27 decision to call
  `app-key-environment.yml` at a pinned remote revision from `gate-rearm.yml`.
  The required-workflow startup constraint and the prohibition on a relative
  reusable call remain in force.

## Context

The pinned `app-key-environment.yml` revision used by `gate-rearm.yml` placed
`validate-ai-review-key` through caller-controlled `CI_LANE_TRUSTED`,
`CI_LANE_FALLBACK`, or `CI_LANE_UNTRUSTED` variables. A public or unresolved
repository could therefore select a persistent runner for a job that receives
the environment-bound RSA private key. Pinning the callee made the source
revision immutable but did not make its runner choice safe for public callers.

ADR 0171 records a canary in which a relative reusable-workflow call from the
organization-required workflow started zero jobs. Replacing the absolute pin
with `uses: ./.github/workflows/app-key-environment.yml` is not a safe option.

## Decision

1. `gate-rearm.yml` keeps the keyless role/environment policy check and the
   environment-bound private-key validation as two inline jobs. Their shell
   bodies must remain byte-identical to the corresponding jobs in
   `app-key-environment.yml`; the `platform` contract test enforces parity.
2. The keyless policy job runs on `ubuntu-24.04`, has only `actions: read` and
   `contents: read`, and must succeed before key validation starts.
3. `validate-ai-review-key` binds the selected `ai-review-app` environment,
   checks the resolved key without checking out repository content, and has
   only `contents: read`. Public and unresolved pull-request callers select
   the literal hosted runner before lane variables; only an explicitly private
   repository may use its trusted lane, with a hosted fallback. The key is
   available only to this environment-bound job.
4. The arm job waits for both validators and treats either failure or skip as
   a policy failure, while retaining its existing terminal failure receipt
   behavior.
5. The runner-acquisition watchdog selects the hosted runner for public or
   unresolved pull-request callers. Its admission condition allows that
   hosted path even when a stale `CI_LANE_UNTRUSTED` value names a persistent
   runner; private calls remain admitted only when the configured lane is the
   exact hosted value.
6. Other workflows continue using `app-key-environment.yml` as the canonical
   reusable source. This decision removes only `gate-rearm.yml`'s remote
   validator edge and the corresponding reviewed exception.

## Consequences

The required-workflow entrypoint no longer depends on an immutable nested
workflow pin, and public or unresolved key-validation jobs cannot be redirected
to a persistent runner through lane variables. The inline scripts duplicate a
small amount of workflow YAML; the byte-for-byte parity test is the maintenance
boundary. Changes to the reusable validator must update the inline copy in the
same PR.

No live required-workflow canary receipt is recorded by this decision alone.
Before changing an organization ruleset revision, the exact merged revision
must start successfully as the required workflow and through a supported
public reusable caller, and the run/job receipts must be retained.
