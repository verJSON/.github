# ARB chair record — ARB consensus as approval authority (2026-11-17)

## Standards grounding

Staged / ungrounded: organization catalog hydration was not completed in this
session; this record binds to pinned repository SHAs and issue references only.

## Charter

- **Run id:** `arb-0271-2026-11-17`
- **Issue:** [VerJSON/.github#2182](https://github.com/verJSON/.github/issues/2182)
- **Question:** What durable evidence replaces personal CODEOWNERS and
  direct-human approval identity for Verjson autonomous delivery and identity
  mutation approval?
- **Pins:** ADRs 0202, 0210, 0257, 0269; verjson-agent-identity#36;
  verjson-docs ADR 0011 standing instruction
- **Rule:** Unanimous on consolidated slate among three advisory seats (CLI ARB
  quorum unavailable / provider usage limited this session).
- **Status:** `APPROVED_WITH_CONSTRAINTS`
- **Slate digest (SHA-256):**
  `e9cc47d2c9cb580380abf0f83ce8252a90409f4698855129d3b82e47c1d57e1d`
  over:

```canonical-slate-json
{"issue":"VerJSON/.github#2182","items":["S-1","S-2","S-3-rev","S-4","S-5-rev"],"run_id":"arb-0271-2026-11-17","status":"APPROVED_WITH_CONSTRAINTS"}
```
- **Ratification:** Owner has directed that ARB—not personal CODEOWNERS—is the
  approval authority for this class of decision. Chair records ADR 0271 as
  **Accepted** under that delegation.

## Adopted slate

- **S-1:** Approval authority for architecture ADRs and autonomous merge
  adjudication is ARB consensus (multi-model ledger + chair record + slate
  digest), published through the existing ADR 0202 exact-head receipt.
- **S-2:** Personal CODEOWNERS are not approval authority. Generated
  `* @verJSON/devs` remains an ADR 0210 contract member for ruleset plumbing /
  contact routing only.
- **S-3-rev:** Autonomous delivery approval kind is `arb-consensus`. The carrier
  is the ADR 0202 receipt; a maintain/admin `User` posts ARB adjudication, not
  personal opinion. Customer IdP direct-human evidence ownership
  (verjson-authn / lifecycle) is unchanged. Non-PR identity-mutation decisions
  must cite the same ARB run id + slate digest in their decision record until a
  dedicated evidence surface exists. Relative to ADR 0257 Decisions 5–8, the
  **ledger / chair record is the approver**; the same-account maintain/admin
  publisher remains valid as the publisher of that adjudication (not as a
  personal CODEOWNERS vote).
- **S-4:** `delegation-owner-review` remains a named customer-policy exception
  only; it is never independent review and never substitutes for ARB consensus
  on Verjson autonomous delivery.
- **S-5-rev:** Receipt marker bytes unchanged. Preceding COMMENT review must
  cite ARB run id + slate digest. Gate parse of that digest is an explicit
  follow-on; until then operators archive the chair record with the receipt.

## Ballots (advisory)

| Seat | Overall | Notes |
| --- | --- | --- |
| A (Grok) | ACCEPT_WITH_CONCERNS | Accepted revised slate; gate-parse follow-on |
| B (Composer) | ACCEPT_WITH_CONCERNS | Drove S-3/S-5 revisions |
| C (Grok) | ACCEPT_WITH_CONCERNS | Accepted after S-3-rev 0257 publisher clarification |

## Integration constraints

1. **scope:** Autonomous merge verifier
   `scripts/ci-gate/independent-review-receipt.sh` (Gate R; ADRs 0202, 0257,
   0269). **vulnerability:** Until the gate reads the preceding COMMENT, a
   maintain/admin `User` can post the canonical marker without fail-closed proof
   of ARB run id + slate digest. **remediation_track:** Parse COMMENT against
   archived chair record / ledger manifest (tracked follow-on on #2182).
2. **scope:** Non-PR identity-mutation decision records.
   **vulnerability:** Citation is not machine-bound to the ledger archive.
   **remediation_track:** Dedicated evidence surface; until then decision
   records store run id, slate digest, and archive locator.

## Dissent

None on the revised slate. Follow-ons above are constraints, not blocking
defects for ADR acceptance.
