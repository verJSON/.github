# 0272 — Archive custody / credential ARB ratification for Gate R

- **Date:** 2026-11-17
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run
  `arb-custody-cred-2185-2026-11-17`; slate digest
  `0566372c5e9a0ce9d0403ec1068e0a0bdf903f37990fd7b56c1ae4606a7f0e8e`).
- **Issue:** [#2188](https://github.com/verJSON/.github/issues/2188)
- **Related:**
  [verJSON/verjson-agent-identity#72](https://github.com/verJSON/verjson-agent-identity/issues/72),
  [#73](https://github.com/verJSON/verjson-agent-identity/issues/73),
  [#75](https://github.com/verJSON/verjson-agent-identity/issues/75),
  [verJSON/.github#2185](https://github.com/verJSON/.github/issues/2185)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md)
  (Gate R archive locator surface)
- **Category:** merge authorization / custody policy archive (sensitive
  class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

Cross-repo custody and credential questions for identity-library
`keyCustodySupport`, credential-release enablement, and the
`require_code_owner_review` bit under ADR 0271 were adjudicated by ARB
run `arb-custody-cred-2185-2026-11-17`. Gate R (ADR 0271 / #2184) accepts
only archive locators of the form
`verJSON/.github@<40-hex>:docs/decisions/<adr-slug>/arb-record.md` and
requires the file contents to contain both the run id and the 64-hex
slate digest. This ADR publishes that chair record on the org archive
path so identity and Gate R receipts can cite it.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0272-custody-credential-arb-ratification/arb-record.md`
   with run id `arb-custody-cred-2185-2026-11-17` and slate digest
   `0566372c5e9a0ce9d0403ec1068e0a0bdf903f37990fd7b56c1ae4606a7f0e8e`.
2. **Adopted slate (unchanged):** S-1 `flip_now` (custody support), S-2
   `keep_disabled` (credential release), S-3 `keep`
   (`require_code_owner_review`).
3. **Credential release stays disabled.** This archive does not authorize
   enablement; G-5/G-7/G-8 gaps remain on
   [verJSON/verjson-agent-identity#73](https://github.com/verJSON/verjson-agent-identity/issues/73).
4. **Approval path** is ADR 0271 / ARB + ADR 0202 — not personal
   CODEOWNERS.

## Non-goals

- Enabling credential release or HTTP ready.
- Dropping `require_code_owner_review` from `main-protection`.
- Changing Gate R marker bytes.

## Consequences

- Identity PR receipts and Gate R COMMENT citations can point at this
  archive locator once the landing commit SHA is known.
- Constraint follow-on for G-3 second-adapter conformance remains
  [verJSON/verjson-agent-identity#75](https://github.com/verJSON/verjson-agent-identity/issues/75).

## Verification

- `arb-record.md` contains plaintext run id and slate digest.
- Slate digest matches SHA-256 of the canonical JSON recorded in the
  chair record.
- Related identity work:
  [verJSON/verjson-agent-identity#74](https://github.com/verJSON/verjson-agent-identity/pull/74).

[arb-record]: ./arb-record.md
