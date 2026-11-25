# 0308 — Admission ARB for verJSON/.github#2280 (archives 0306 and 0307)

- **Date:** 2026-11-25
- **Status:** Accepted. Ratified by the Architecture Review Board: `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-dotgithub-pr2280-76f611a9a2-20261125-01`; slate digest `aecdc822c1eec518a95b15a5f6f94642dabf9f4c68088af5c58cac4b69600c25`.
- **Authority:** [owner ratification on #2280](https://github.com/verJSON/.github/pull/2280#issuecomment-7172612935), the [companion chair record](./arb-record.md), [ADR 0305](../0305-wave-zero-runner-archive-admission/README.md) and [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md).
- **Clarifies:** ADR 0306 and ADR 0307 (README statements only; the archived records are unchanged).
- **Related:** [verJSON/.github#2280](https://github.com/verJSON/.github/pull/2280), [verJSON/.github#2283](https://github.com/verJSON/.github/issues/2283), [verJSON/.github#2284](https://github.com/verJSON/.github/issues/2284)
- **Category:** archive admission (governance: Gate R citation sources)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

#2280 archives two delivery ARB chair records: [ADR 0306](../0306-verjson-cli-411-vaultwarden-lifecycle-delivery-arb/README.md) for verjson-cli#411 and [ADR 0307](../0307-verjson-agents-558-arb-ledger-v3-delivery-arb/README.md) for verjson-agents#558. Like any PR to this repository, it needs its own exact-head arb-consensus citation to an archived record, and no archived record yet admits it. ADR 0305 resolved the same circularity for #2279 with a one-use companion record that the owner ratified. Run 21 reviewed #2280 at head `76f611a9a27dbd3a2e5863fd387773e11f2a6372` under the owner's 2026-11-24 charter, the one ADR 0306 records. It approved publication on bound conditions, the first of which is owner ratification of this one-use procedure.

The owner gave that ratification on 2026-11-25 in reply to a request that named #2280, the head, the one-use ADR 0308 companion-citation procedure and the four disclosures below. The publisher's attestation on #2280 records the request, the options and the owner's verbatim reply ([comment 7172612935](https://github.com/verJSON/.github/pull/2280#issuecomment-7172612935)). Nothing in this revision was prepared before that comment.

## Decision

1. **Archive the run-21 chair record** at `docs/decisions/0308-verjson-dotgithub-2280-archive-admission-arb/arb-record.md`, byte-identical to the chair artifact (SHA-256 `0740ecfb7ab3030cf7dba09c07cc96f43dfb7ddb7edde3a24283bf3dde6933c0`).
2. **One use, for #2280 alone.** The final revision differs from `76f611a9` only by this README, that record, the regenerated index row and one NEXT fragment. #2280's exact-head citation names only this record at the reviewed commit, and a fresh independent-review receipt for that head follows it. Merging goes through ordinary protection, never `--admin`. This sets no precedent for any other PR; a standing archive path needs its own decision.
3. **Merge method.** The repository allows merge commits, so #2280 lands with one. `519e3c9`, the branch commit verjson-cli#411's citation names, then becomes reachable from `main`.
4. **Admission authorizes nothing else:** no merge of verjson-agents#558, verjson-cli#411 or any other head, no `--admin`, and no change to the 0306 or 0307 records.

## Recorded facts and clarifications (run-21 kind (b) items)

- **ADR 0306's citation locator (A2 (i), L1).** verjson-cli#411 merged citing `519e3c9`, an unprotected branch commit, before this archive landed: the reverse of ADR 0305's order. The protected archive is the landed `main` commit, whose 0306 `arb-record.md` bytes equal SHA-256 `fd547377a3f4a66bbe3195dd109453850773ef6deea5554ca607aba18c6fcdf8`. Decision 3 makes `519e3c9` reachable from `main`. [#2283](https://github.com/verJSON/.github/issues/2283) tracks having Gate R refuse an `archive_sha` that is not on `main`.
- **The split reading belongs to the chair (A2 (ii), L1).** ADR 0306 README line 24 and ADR 0307 README line 16 read a restated round-3 dissent as already adjudicated. That reading is the chair's. The owner has not ratified it, and until the owner rules it is no precedent for later boards. codex's retained dissent remains open for the owner: on run 18's B2, L3 and M1, on run 20's B6, L1–L7 and M1, and its run-21 round-3 REVISE on A2, A5, L1, L3 and M1.
- **What the #558 ratification covers (A3, L2).** The owner comment at verjson-agents#558 [7163523808](https://github.com/verJSON/verjson-agents/pull/558#issuecomment-7163523808) ratifies the ledger v2 and v3 contract manifests, not the run-20 board outcome. Owner ratification of that outcome is pending; under ADR 0271 it is not a merge condition. ADR 0307 says "the PR's last two commits record that ratification", which is imprecise. After `b10818a`, the head the owner ratified against, `e0ae56e` records the ratification: the evidence URL and effective date in `ledger-adoption.json`, the matching tests, ADR 0044's status, and related wording in `protocol.md` and the NEXT fragment. `d782499` removes a private URL from `protocol.md`. Both manifests are unchanged.
- **#558 stays guarded (L2).** verjson-agents#558 remains subject to the org merge guard: exact head `d782499c8168f46993a9125f9942faaf96bc7ebf`, required checks, approvals and `--match-head-commit`, as 0307 `arb-record.md` line 9 records.
- **Deliberate disclosures (A5, L3).** The owner ratified publishing these four items in this public repository: the Vaultwarden posture sentence at 0306 `arb-record.md` line 73, codex's dissent scenario, about 87 private verjson-cli `path:line` citations, and the run-directory artifact names. verJSON/verjson-cli and verJSON/verjson-agents are private, so their source links return 404 to public readers. The archived bytes are immutable: any later redaction needs a new board. The L3 remedy reordering is tracked from verjson-cli#410. [#2284](https://github.com/verJSON/.github/issues/2284) tracks disclosure guidance for future chair records.
- **Run-18 call accounting (A1).** Run 18 made 14 member calls: 11 recorded and 3 malformed, all three from cursor-agent. 12 latest-per-member-per-round responses were hashed, and the final tally rests only on the 4 recorded round-3 ballots.
- **Follow-up (A2, round 3).** After #2280 lands, a separate docs PR adds only a `Clarified by: ADR 0308` header line to the 0306 and 0307 READMEs, leaves both `arb-record.md` SHA-256 values unchanged, and passes `gen-adr-index.sh --check`.

## Consequences

- #2280's exact-head Gate R citation may name `verJSON/.github@<reviewed-head>:docs/decisions/0308-verjson-dotgithub-2280-archive-admission-arb/arb-record.md`. That is the one use this ADR admits.
- After #2280 lands, the publisher verifies that the landed 0306, 0307 and 0308 `arb-record.md` bytes equal their reviewed SHA-256 values at the `main` commit. Only then do verjson-agents#558 and later citations name that landed `main` commit, never a branch commit.

[arb-record]: ./arb-record.md
