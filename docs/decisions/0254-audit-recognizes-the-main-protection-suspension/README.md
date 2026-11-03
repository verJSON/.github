# 0254 — The arm audit recognizes the main-protection suspension as a reviewed state

- **Date:** 2026-11-03
- **Status:** Accepted
- **Issue:** [verJSON/.github#1992](https://github.com/verJSON/.github/issues/1992) (owner hold: [#1933](https://github.com/verJSON/.github/issues/1933); consumer drift: [#1810](https://github.com/verJSON/.github/issues/1810))
- **Category:** rulesets / branch protection (sensitive class)
- **Amends:** [ADR 0094](../0094-arm-required-by-its-own-property-scoped-ruleset/README.md)
- **Related:** [ADR 0188](../0188-contract-audits-assert-fields-against-a-growing-vendor-schema/README.md), [ADR 0189](../0189-scheduled-audits-adjudicate-findings-against-a-reviewed-expectation/README.md)

## Context

On 2026-10-28 the owner disabled `main-protection` (`18098028`) until distinct
agent identities and their review policy exist (#1933). Its targeting, rules,
and bypass actors were left unchanged; only `enforcement` moved to `disabled`.

ADR 0094 gave `scripts/ai-review-required-workflow-audit.py` two reviewed states,
`ready` and `split`, and derived the postimage from the preimage so that a
contract which "also relaxes enforcement" is rejected rather than reviewed by
eye. A disabled `main-protection` matches neither image, so since 2026-10-29 the
daily `arm-audit` has stopped at its first check (latest failing run
41614952177). Nothing after it has been evaluated: not the #1810 consumer-drift
finding, and not any new arm or adopter regression. #1933's follow-through asks
for the audit to "explicitly recognize and report the temporary state rather
than silently masking drift".

The Architecture Review Board ran Charter D on #1992 (ARB RUN-D, 2 rounds,
unanimous 3/0/0 on slate revision 2, digest `228f9f53…`). The owner ratified it
on 2026-11-03. This ADR records that decision.

## Decision

1. **A third, derived image.** The audit derives `suspended` from the split
   postimage by setting only `enforcement` to `disabled`. It is computed in code
   and never declared: a contract carrying a `suspended` key is rejected. The
   reviewed protection images must themselves be `active`, so the derived image
   differs from the postimage in exactly that one field and a disabled ruleset
   can never read as `split`.
2. **One permitted difference.** Every other field the postimage asserts must
   still match. Keys no image asserts stay the ADR 0188 residual, reported
   through `unpinned_live_fields` exactly as in `split`. An enforcement other
   than `active` or `disabled`, and the pre-split image with enforcement
   disabled, match no image and fail.
3. **Behaves as `split`, but is never `split`.** Arm-ruleset exclusivity, carrier
   selection, the unpinned-field report, and every later check run exactly as
   in `split`, fail-fast and in the same order. The report state reads
   `suspended`, or `suspended-legacy-selector`. A caller expecting `split` does
   not accept it, so no render mode runs from it: not the split, arm,
   rollback, or any property-migration payload.
4. **Loud, constant, and adjudicated on its own.** While suspended the audit
   reports one constant finding, `main-protection (18098028) matches the
   reviewed suspended image (#1933)`. The audit now prints one `ERROR:` line per
   finding instead of joining them with ` | `, so ADR 0189 fingerprints each
   finding separately and the suspension's fingerprint does not move when the
   #1810 finding does. Its expectation entry cites #1933 and expires 30 days
   after recording; extending it is a new reviewed diff.
5. **Removal on restoration.** When #1933 restores enforcement, the live ruleset
   matches the postimage again, the suspension finding stops reproducing, and
   the expectation goes stale: the scheduled run stays red until the entry, and
   the test binding it to the audit's text, are removed. The derived image
   itself stays, so a later reviewed suspension is recognized the same way.

### Why this does not reopen ADR 0094's rejection

ADR 0094 rejects a contract that relaxes enforcement *while presenting itself as
"just moving the workflows rule"*. That is a disguised difference, reviewable
only by eye. The suspended image is the opposite: its only difference from the
postimage is fixed in code, named in its own state, reported on every run as a
finding, and tied to an owner hold with an expiring acknowledgement. The
postimage itself is unchanged and is still derived from the preimage, so a
contract that moves the workflows rule and relaxes enforcement in the same
change is still rejected.

### Alternatives rejected

- **Keep the job red until #1933 resolves.** Every new finding stays hidden
  behind the known one, and nothing past `main-protection` is checked.
- **Record the current "matches neither image" finding (`f6804917…`) as
  expected.** The recorded #1810 entry would then go stale, so the job could go
  green only by dropping it, and the audit would still check nothing beyond
  `main-protection`.

## Consequences

- The daily audit evaluates the arm, adopters, and the replacement workflow
  again while the hold lasts, so #1810 can be re-recorded from a live run. That
  re-record is a separate reviewed change, not part of this one.
- A suspended `main-protection` is now an accepted, reported state rather than a
  hard failure. The control against misuse is that the state is narrow (one
  field), loud (a finding on every run), and bounded (an expiring entry).
- Branch protection itself is not restored by this change. The risks #1933
  records, including unprotected default branches, remain the owner's hold.
- Follow-ups that apply to `split` as well, out of scope here: pin
  `require_extra_approval_for_unattributed_changes` after a separate review of
  its value; report independent prerequisite failures together instead of
  stopping at the first; break ties in the nearest-image message toward
  `suspended`.
