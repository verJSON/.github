# 0309 — Standing archive-only admission lane for ARB chair records

- **Date:** 2026-11-25
- **Status:** Proposed — pending the owner's ratification of this ADR.
- **Authority:** owner direction 2026-11-25 (see the ratification comment); this ADR takes effect for merge admission once the owner ratifies it.
- **Extends:** [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md) (Gate R's arb-consensus citation). Supersedes nothing.
- **Changes:** how Gate R enforces [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md) Decision 3 for archive-only pull requests (see Consequences).
- **Related:** [verJSON/.github#2285](https://github.com/verJSON/.github/issues/2285), [verJSON/.github#2286](https://github.com/verJSON/.github/issues/2286), [ADR 0305](../0305-wave-zero-runner-archive-admission/README.md), [ADR 0308](../0308-verjson-dotgithub-2280-archive-admission-arb/README.md), [ADR 0202](../0202-bind-autonomous-merge-to-independent-review-receipt/README.md), [ADR 0257](../0257-merge-admission-requires-the-exact-head-review-receipt/README.md), [verJSON/.github#2283](https://github.com/verJSON/.github/issues/2283)
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
   - an *added* top-level `NEXT/<name>.md` fragment whose name starts with a letter or digit and uses only letters, digits, `.`, `_` and `-`.

   Each pattern must match the whole path, so a trailing newline or control character falls outside the shape.

   The pull request adds at least one `arb-record.md`, and every ADR directory it touches adds both `README.md` and `arb-record.md`.
3. **Archives stay immutable.** A record or ADR README with any status other than `added` (modified, removed, renamed, copied) is outside the shape.
4. **Regular files only.** Every listed path that is not removed must be a regular, mode-`100644` blob in the head's recursive tree (`git/trees/<head>?recursive=1`). A symlink, executable, submodule or absent entry, or a truncated tree, falls outside the shape.
5. **Records parse as Gate R parses them.** Each added `arb-record.md` is read at the exact head through the Contents API and must pass the same `canonical-slate-json` fence and `run_id` checks a cited archive passes. The lane and the citation path share that code. It parses the response once, requiring a `file` served at the requested path with `base64` encoding (never a symlink's target), and decodes the bytes before Bash sees them, refusing a record that contains a NUL byte or is not valid UTF-8.
6. **One request, bound to the exact head.** The files endpoint answers for the pull request's current head and the base it computes against, and pages are separate requests against a base that can move between them. The list is therefore read in one request of up to 100 rows; a full page of 100 falls back to the citation requirement. Straight after the read, every entry's `contents_url` must name the exact head under evaluation (a removed file's names the base), so a list of any other head cannot vouch for it, whatever its shape.
7. **Fail closed.** A file list that cannot be read or is malformed, a list that does not describe the exact head, a head tree that cannot be read, or an added record that cannot be read or whose Contents API response is malformed leaves the citation step unanswered (exit 3), never passed.
8. **Fallback, not refusal.** Any other pull request falls back to the existing citation requirement, so it can still cite. Its refusal names the reason the lane did not apply.
9. **Visible.** A pass through the lane prints a `::notice::` naming the lane, which the receipt verifier and Gate R re-emit.

The lane removes only the citation requirement. The ADR 0202 exact-head independent-review receipt is still required.

## Consequences

- An archive-only pull request needs only the exact-head independent-review receipt. Archives no longer wait for a second board or a per-use owner ratification; ADR 0305 and ADR 0308 remain the records of their own uses.
- **ADR 0271 enforcement changes for archive-only pull requests.** ADR 0271 Decision 3 makes the ledger or chair record the approver, and Gate R enforces that through the citation. For an archive-only pull request the receipt reviewer's check stands in for a chair record; no record approves the archive itself. ADR 0271 Decision 1 still forbids decision text in a lane pull request, and only the receipt reviewer enforces that.
- **The gate does not inspect content beyond the records.** It does not read the README, the decision index or the NEXT fragments, and does not check the index or fragments against their generators. One receipt could therefore land new ADR text alongside an archive; the receipt reviewer is the only check that those files describe the archive and decide nothing else.
- **Provenance rests on the receipt reviewer.** The gate has no chair artifact to compare against, and a later citation verifies only the archived slate digest, so an archive admitted without a provenance check would let a fabricated record approve future merges. Each archive's README states its chair artifact's whole-file SHA-256; that value is a consistency anchor the publisher writes, not provenance. Before posting a receipt, the receipt reviewer obtains the chair artifact, or its whole-file SHA-256, through a channel independent of the pull request, checks each added `arb-record.md` byte for byte against it, and names in a review comment the channel, the hash, the run id, the fence digest and the board's disposition.
- **Until [#1964](https://github.com/verJSON/.github/issues/1964)**, ADR 0257 Decision 8 lets the receipt reviewer share the publisher's account; provenance then rests on that account's attestation.
- **Publishing is not authorizing.** Archiving a record does not permit citing it for a target its board did not decide.
- The archive-only shape is measured against the pull request's current base, as GitHub computes its changed files; only the head is pinned. A base that moves can change which files count as changed, so the shape holds for the head that is evaluated against the base GitHub reports at that time.
- **[#2283](https://github.com/verJSON/.github/issues/2283) is not yet in force.** It tracks having Gate R refuse an `archive_sha` that is not reachable from `main`. Until it lands, citations name only landed `main` commits, by process, never a branch commit. A lane pull request lands with a merge commit, or by squash only when the head a citation names is otherwise reachable from `main`.
- **Follow-ups.** Checking the index and NEXT fragments against their generators is not part of this lane. Leading `^` anchors and the runner's jq version are tracked on [#2286](https://github.com/verJSON/.github/issues/2286).
- Any change to the archive shape, such as editing an archived record, still needs a citation.
