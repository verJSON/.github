# 0271 — ARB consensus is the approval authority; personal CODEOWNERS are not

- **Date:** 2026-11-17
- **Status:** Accepted — ratified by the Architecture Review Board under the
  owner's direction that ARB (not personal CODEOWNERS) is the approval authority
  for this class of decision (`APPROVED_WITH_CONSTRAINTS` in the
  [ARB chair record][arb-record]; run `arb-0271-2026-11-17`; slate digest
  `e9cc47d2c9cb580380abf0f83ce8252a90409f4698855129d3b82e47c1d57e1d`).
- **Issue:** [#2182](https://github.com/verJSON/.github/issues/2182)
- **Amends:** [ADR 0202](../0202-bind-autonomous-merge-to-independent-review-receipt/README.md)
  (2026-10-22 amendment items 2–3 for the autonomous delivery path);
  clarifies authority relative to
  [ADR 0210](../0210-code-owner-review-is-real-through-a-generated-codeowners-member/README.md)
- **Extends:** [ADR 0257](../0257-merge-admission-requires-the-exact-head-review-receipt/README.md),
  [ADR 0269](../0269-retire-owner-admin-merge-bypass/README.md)
- **Category:** merge authorization / organization policy (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

Autonomous delivery already consumes an ADR 0202 exact-head independent-review
receipt. Generated CODEOWNERS (`* @verJSON/devs`) is a required adopter contract
member under ADR 0210 so organization `require_code_owner_review` is not vacuous.
In practice, agents were still steered to ask a personal CODEOWNER or human
owner for approval, and ADR 0202's 2026-10-22 amendment still stated that
approval identity is a direct human session only. The owner directed that ARB
consensus—not personal CODEOWNERS—is the approval authority for Verjson
architecture decisions and autonomous merge adjudication, and that product
approval semantics for Verjson-gated mutation follow the same authority.

## Decision

1. **ARB consensus is the approval authority** for architecture ADRs and for
   autonomous merge adjudication across the managed fleet. Durable evidence is
   the multi-model ledger (or advisory-seat record when CLI quorum is
   unavailable), the chair record, and the slate digest, published through the
   existing ADR 0202 exact-head receipt surface.
2. **Personal CODEOWNERS are not approval authority** anywhere in the managed
   fleet. The generated `* @verJSON/devs` file remains a required ADR 0210
   contract member for ruleset plumbing and contact routing only. Asking a named
   human CODEOWNER or the repository owner for personal approval is not the
   autonomous delivery path.
3. **Autonomous delivery approval kind is `arb-consensus`.** This amends ADR
   0202 (2026-10-22) items 2–3 for that path: the maintain/admin `User` that
   posts the receipt publishes ARB adjudication, not personal opinion. Relative
   to ADR 0257 Decisions 5–8, the **ledger / chair record is the approver**; the
   same-account publisher remains valid as publisher of that adjudication.
   Customer IdP-bound direct-human session evidence ownership
   (verjson-authn issuance; verjson-identity-lifecycle session status) is
   unchanged and is not deleted by this ADR.
4. **`delegation-owner-review`** remains a named exception only where a customer
   policy still requires a human subject. It is never called independent review
   and does not substitute for ARB consensus on Verjson autonomous delivery.
5. **Receipt compatibility.** The exact marker line remains:

   `<!-- independent-review:v1 pr:<n> head:<40-hex> verdict:approved -->`

   Gate R / `scripts/ci-gate/independent-review-receipt.sh` are unchanged in the
   landing PR for this ADR. The preceding COMMENT review must cite the ARB run
   id and slate digest. Digest binding is defined in the 2026-11-17 amendment
   (#2189); operators still archive the chair record with the receipt.
6. **Non-PR identity-mutation decisions** must cite the same ARB run id and
   slate digest in their decision record until a dedicated evidence surface
   exists.

## Non-goals

- Dropping `require_code_owner_review` from `main-protection` (would further
  amend ADR 0210; follow-on).
- Changing customer IdP login or human-session revocation ownership.
- Library `keyCustodySupport` flip or credential-release enablement.

## Consequences

- Architecture and autonomous merge approval asks go to ARB + ADR 0202 receipt,
  never personal CODEOWNERS.
- Product control-plane approval kinds may admit `arb-consensus` (identity ADR
  0009 / verJSON/verjson-agent-identity#36).
- Gate R binds the citation slate digest to a recomputed SHA-256 over the
  archived canonical slate (amendment below); substring presence alone is not
  sufficient.

## Verification

- This ADR and its [ARB chair record][arb-record] land on `main` with an ADR
  0202 exact-head receipt whose preceding COMMENT cites run
  `arb-0271-2026-11-17` and slate digest
  `e9cc47d2c9cb580380abf0f83ce8252a90409f4698855129d3b82e47c1d57e1d`.
- Follow-on issues track any ruleset change to `require_code_owner_review`.

## Amendment 2026-11-17 — Gate R recomputes the slate digest (#2189)

`scripts/ci-gate/arb-consensus-citation.sh` must not accept a citation merely
because the archived chair record contains the 64-hex digest string. It
extracts the first Markdown fenced block whose info string is exactly
`canonical-slate-json`, takes the fence body with one trailing newline removed
if present, requires that body to be a single UTF-8 JSON object line whose
`run_id` string equals the citation run id, and accepts the citation only when
lowercase hex SHA-256 of those exact body bytes equals the citation's
`slate:` digest. Missing fence, multi-line body, JSON decode/schema failure,
`run_id` mismatch, or digest mismatch fails closed. The ADR 0202 receipt marker
stays digest-free.

[arb-record]: ./arb-record.md
