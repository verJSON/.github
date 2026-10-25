# 0241 — Preflight validation pins the configured GitHub App client-ID format

- **Date:** 2026-10-25
- **Status:** Accepted
- **Issue:** [Verjson/.github#1800](https://github.com/Verjson/.github/issues/1800)
- **Related:** [ADR 0099](../0099-release-app-replaces-admin-pat/README.md), [ADR 0201](../0201-secret-bearing-fleet-report-uses-default-branch/README.md)
- **Category:** GitHub App authentication boundary (sensitive class)

## Context

Canonical workflows minted GitHub App installation tokens with inconsistent
preflight checks. Some rejected only empty and numeric values, others used a
length and character pattern, and several had no guard adjacent to the mint.
The reconcile workflow also validated a writer-or-observer fallback while its
write token mint used only the writer ID.

ADR 0099 leaves the complete client-ID grammar to the pinned token action and
explicitly avoids assuming a character or length set. ADR 0201 later selected
`^Iv[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.]{8,126}$` for the fleet-report inventory boundary. This ADR
resolves the conflict for canonical preflight checks without claiming that the
pattern is GitHub's complete or permanent grammar.

## Decision

1. Apply `^Iv[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.]{8,126}$` to the organization's currently configured
   GitHub App client IDs. It preserves documented dotted `Iv1` IDs, rejects
   numeric App IDs, empty values, whitespace, shell metacharacters, non-ASCII
   characters, and values outside the configured length bounds. A future ID
   format that GitHub accepts may be rejected until the organization updates
   this policy and its tests.
2. `scripts/app_client_id_contract.py` is the source of truth for both the
   Python acceptance function and the generated shell guard. The generator
   places one guard in the same job immediately before every
   `actions/create-github-app-token` step. The guard binds `APP_CLIENT_ID` to
   that mint's exact `with.client-id`, copies the mint's `if` condition, sets
   `LC_ALL=C`, and fails on an active mint with a missing or malformed ID.
   Optional token paths may skip both guard and mint only when that exact mint
   is skipped by its condition. Mint conditions cannot use explicit status
   check functions, which suppress GitHub Actions' implicit `success()` gate
   ([status check functions](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#status-check-functions))
   and could otherwise let a mint run after its guard fails. A future path that
   needs one must add an explicit guard-success condition and pass architecture
   review.
3. `scripts/bootstrap-canonical-ci.py` and
   `scripts/ai-review-required-workflow-audit.py` use the same acceptance
   function at their configuration boundaries.
4. The pinned token action remains the authentication authority. This
   organization preflight rejects values outside current policy; it does not
   prove that an ID exists, matches a private key, or is accepted by GitHub.

This ADR supersedes only ADR 0099's pre-mint client-ID acceptance-policy
clause, which delegated the entire format check to the action after rejecting
empty and numeric values. It does not change the token action's authentication
authority or ADR 0099's release credential boundaries. It extends ADR 0201's
selected pattern to every canonical App-token mint, while retaining the scope
and future-format limitation stated here. The historical rationale in ADRs
0099 and 0201 remains unchanged.

## Consequences

- Every canonical token mint must remain covered by the generated guard and
  its order, condition, and client-ID binding checks.
- A change to configured client-ID formats requires a deliberate policy update
  to this ADR, the shared validator, the generated workflows, and behavioral
  tests.
- The action still determines whether the supplied credentials authenticate
  and can mint the requested installation token.
