# 0251 — Hold unprotected bootstrap caller delivery

- **Date:** 2026-10-31
- **Status:** Proposed
- **Issue:** [#1965](https://github.com/VerJSON/.github/issues/1965)
- **Category:** sensitive — generated workflow delivery

## Context

The legacy bootstrap replaces generated caller files without establishing
managed ownership or atomically comparing the expected prior content. Its
rollback can overwrite a subsequent editor, and organization settings may be
changed before a caller write fails. A local recheck or advisory lock does not
provide conditional replacement against arbitrary editors. The shared
cross-repository delivery work is coordinated with
[verjson-ci#295](https://github.com/VerJSON/verjson-ci/issues/295).

## Decision

Until conflict-safe, ownership-bound caller delivery is implemented and
reviewed, reject `apply` for any bootstrap manifest containing callers before
any local or remote mutation. The direct caller generator entry point also
rejects apply when callers are present. Keep read-only check and dry-run, and
allow separately authorized organization-only apply for manifests with no
callers. Reject dangling output symlinks during inspection.
Failed dispatched organization writes retain an uncertain outcome in the partial
receipt. The CLI emits that receipt even if exclusive receipt-file creation fails;
reconcile provider state before retrying.

This is a temporary hold, not the requested automatic rollout. It does not
authenticate managed ownership, implement a provider write, establish remote
ref concurrency, or make a plan safe to apply. Existing pinned copies remain
unaffected until explicitly adopted.

## Release from the hold

Replace the hold only with a shared delivery implementation that verifies
customer-authorized ownership and immutable generator identity, binds the
expected base and target, handles concurrent edits with a proven conditional
write, and records ambiguous or partial outcomes for reconciliation. Cover
custom files, symlinks, concurrent edits and rollback; separately verify
provider ref semantics and merge-candidate admission. The CLI and bootstrap
must use or verify the same contract, with a reviewed migration and live
provider evidence before claiming automatic deployment.
