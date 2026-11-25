# 0309 — Standing archive-only admission lane for ARB chair records

- **Date:** 2026-11-25
- **Status:** Proposed — pending the owner's ratification of this ADR.
- **Authority:** owner direction on 2026-11-25 ("Yes, set up a standard archive path"), recorded in [verJSON/.github#2285](https://github.com/verJSON/.github/issues/2285); this ADR takes effect for merge admission once the owner ratifies it.
- **Extends:** [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md) (Gate R's arb-consensus citation). Supersedes nothing.
- **Related:** [ADR 0305](../0305-wave-zero-runner-archive-admission/README.md), [ADR 0308](../0308-verjson-dotgithub-2280-archive-admission-arb/README.md), [ADR 0202](../0202-bind-autonomous-merge-to-independent-review-receipt/README.md), [ADR 0257](../0257-merge-admission-requires-the-exact-head-review-receipt/README.md), [verJSON/.github#2283](https://github.com/verJSON/.github/issues/2283)
- **Category:** merge admission (governance: Gate R citation sources)

## Context

ADR 0271 makes Gate R require, on every promotion-enabled pull request, an exact-head arb-consensus citation that names an ARB chair record archived in this repository. A pull request that only archives a chair record copies a decision a board has already reached. Requiring it to cite yet another archived record is circular: no archived record admits the archive itself.

ADR 0305 (for #2279) and ADR 0308 (for #2280) broke that circle one use at a time. Each convened a second board and obtained a per-use owner ratification for a self-citing companion record. Every downstream citation then waited on owner action for the archive it needed. On 2026-11-25 the owner asked for a standing archive path instead ([#2285](https://github.com/verJSON/.github/issues/2285)).

## Decision

`scripts/ci-gate/arb-consensus-citation.sh` admits an archive-only pull request without a citation, under these rules:

1. **Scope.** The lane applies only when the target repository is verJSON/.github, compared case-insensitively as GitHub compares names. In any other repository nothing changes and the pull request's files are not read.
2. **Shape.** Every file the pull request changes is one of:
   - an *added* `docs/decisions/NNNN-<slug>/arb-record.md`;
   - an *added* `docs/decisions/NNNN-<slug>/README.md`;
   - the *modified*, generated index `docs/decisions/README.md`;
   - an *added* top-level `NEXT/*.md` fragment.

   The pull request adds at least one `arb-record.md`, and every ADR directory it touches adds both `README.md` and `arb-record.md`.
3. **Archives stay immutable.** A record or ADR README with any status other than `added` (modified, removed, renamed, copied) is outside the shape.
4. **Records parse as Gate R parses them.** Each added `arb-record.md` is read at the exact head through the Contents API and must pass the same `canonical-slate-json` fence and `run_id` checks a cited archive passes. The lane and the citation path share that code.
5. **Bound to the exact head.** The files endpoint answers for the pull request's current head. Every entry's `contents_url` must name the exact head under evaluation, so a list of any other head cannot vouch for it.
6. **Fail closed.** A file list that cannot be read or is malformed, a list that does not describe the exact head, or an added record that cannot be read leaves the citation step unanswered (exit 3), never passed. GitHub lists at most 3000 files, so a list of that length is treated as possibly truncated and is not an archive.
7. **Fallback, not refusal.** Any other pull request falls back to the existing citation requirement, so it can still cite. Its refusal names the reason the lane did not apply.
8. **Visible.** A pass through the lane prints a `::notice::` naming the lane, which the receipt verifier and Gate R re-emit.

The lane removes only the citation requirement. The ADR 0202 exact-head independent-review receipt is still required.

## Consequences

- An archive-only pull request needs only the exact-head independent-review receipt. Archives no longer wait for a second board or a per-use owner ratification; ADR 0305 and ADR 0308 remain the records of their own uses.
- The receipt reviewer carries the provenance check the gate cannot perform. The gate has no chair artifact to compare against, so the reviewer verifies that each added `arb-record.md` is byte-identical to its chair artifact (SHA-256), and that the README, index row and NEXT fragment describe only that archive. A later citation verifies only the archived slate digest, so an archive admitted without that check would let a fabricated record approve future merges.
- [#2283](https://github.com/verJSON/.github/issues/2283), which requires a cited archive to be on `main`, complements this lane: the archive lands through ordinary protection before any citation names it.
- Any change to the archive shape, such as editing an archived record, still needs a citation.
