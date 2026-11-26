# 0310 — Admission ARB for verJSON/.github#2287 (archive-only lane, ADR 0309)

- **Date:** 2026-11-26
- **Status:** Accepted. Ratified by the Architecture Review Board: `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-dotgithub-pr2287-e51b786ef9-20261126-01`; fence digest `a9d00adcce885aa6fadba64e781934024dff7d9745302108fc8c69c975ccca70`.
- **Authority:** [owner ratification on #2287](https://github.com/verJSON/.github/pull/2287#issuecomment-7183499120), the [companion chair record](./arb-record.md), [ADR 0305](../0305-wave-zero-runner-archive-admission/README.md), [ADR 0308](../0308-verjson-dotgithub-2280-archive-admission-arb/README.md) and [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md).
- **Clarifies:** [ADR 0309](../0309-standing-archive-only-admission-lane/README.md) (this README only; ADR 0309's text changes only in its Status line).
- **Related:** [verJSON/.github#2287](https://github.com/verJSON/.github/pull/2287), [verJSON/.github#2285](https://github.com/verJSON/.github/issues/2285), [verJSON/.github#2286](https://github.com/verJSON/.github/issues/2286), [verJSON/.github#2283](https://github.com/verJSON/.github/issues/2283), [verJSON/.github#2288](https://github.com/verJSON/.github/issues/2288), [verJSON/.github#2289](https://github.com/verJSON/.github/issues/2289), [verJSON/.github#1964](https://github.com/verJSON/.github/issues/1964)
- **Category:** archive admission (governance: Gate R citation sources)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

#2287 adds the standing archive-only admission lane that ADR 0309 records. It changes scripts, so it is outside its own lane, and the lane is not on `main`, so Gate R evaluates #2287 under `main`'s citation requirement. No archived record admits it. Run 23 reviewed #2287 at head `e51b786ef932bc2e0e577ba65dabd728cf88dc2f` (tree `78c8d39dc7de5c92f980f1d654216a4beb310757`, base `d300ac04d6d2dcbe0f39485c75472b274eedaa5d`) and approved it with constraints. Its ADMIT condition requires the owner's express ratification first. It also limits the final revision to one delta commit on `e51b786e`, followed by an exact-head citation of this record and a fresh receipt. Run 23 supersedes run 22 (`arb-dotgithub-pr2287-ac4ff510f6-20261125-01`, head `ac4ff510`) for #2287.

## Decision

1. **Archive the run-23 chair record** at `docs/decisions/0310-verjson-dotgithub-2287-archive-lane-admission-arb/arb-record.md`, byte-identical to the chair artifact (whole-file SHA-256 `9c45f6daec20b0e7f917ea9eacf160a1be2c6b432ff86efc3f03ebfc897c0e47`). This value is a consistency anchor the publisher writes, not provenance (ADR 0309 Consequences).
2. **One use, for #2287 alone.** Citing this record at #2287's reviewed head is the ADR 0305/0308 one-use exception. That exception takes effect before ADR 0309 does. The final revision is one commit whose only parent is `e51b786e`. It sets ADR 0309's Status line to Accepted, adds this README and record, regenerates the index row, and edits the 2285 NEXT fragment's parenthetical. Every script, test and other ADR 0309 line stays byte-identical to `e51b786e`.
3. **Terminal basis.** The terminal basis is run 23's review of #2287 together with the owner's express ratification in the full board form ([comment 7183499120](https://github.com/verJSON/.github/pull/2287#issuecomment-7183499120)). The owner's verbatim choice is `Ratified`. This sets no precedent that a companion record may admit a gate change without its own board.
4. **Merge method.** #2287 lands with a merge commit using `--match-head-commit` through ordinary protection, never `--admin`.
5. **After landing.** ADR 0309's process rule governs every citation once #2287 lands and run 23's ADMIT (5) is verified. ADMIT (5) checks the native ruleset result and the merge commit's parents (`main` and the reviewed head). It also checks that the landed 0310 `arb-record.md` and `scripts/ci-gate/arb-consensus-citation.sh` equal the reviewed bytes. No PR uses the lane before then.
6. **Admission authorizes nothing else:** no `--admin`, no merge of any other head, and no change to ADR 0309 beyond its Status line.

## Clarifications to ADR 0309

The next ADR that amends ADR 0309 folds these into its text.

- **Authority line.** The parenthetical in ADR 0309's Authority line ("pending; the Status line links it when this ADR becomes Accepted") is historical. The Status line now links the ratification.
- **Scope (ADR 0309 Decision 1).** "In any other repository nothing changes" covers lane selection and file-list reads only. The shared record reader (`read_record_text`, `parse_slate_fence`) also changes how citations are verified, in every promotion-enabled repository.
- **ADR 0271 Decision 5.** Besides changing how Gate R enforces ADR 0271 Decision 3, the lane also lifts, for lane PRs, Decision 5's requirement of a preceding arb-consensus citation COMMENT.
- **Direction.** The ratification comment confirms the owner's 2026-11-25 direction that ADR 0309's Context cites.

## Citable commits (N3)

- A citable "landed `main` commit" is a commit on `main`'s first-parent chain, or a commit whose cited blob equals the blob at such a commit.
- [#2283](https://github.com/verJSON/.github/issues/2283) is not yet in force. The process rule that citations name only landed `main` commits stays in force even after #2283 lands, until a machine check enforces this definition. The first-parent requirement is recorded on [#2283](https://github.com/verJSON/.github/issues/2283#issuecomment-7178623685).
- #2287's merge-commit choice is separate from standing lane policy. A lane PR uses a merge commit only when its branch is a single commit on the base; otherwise it squashes.

## Independent provenance channel (TRUST)

- The receipt reviewer's independent channel must predate the lane PR's first commit and be outside the PR's control. Examples are the chair's board-outcome comment on the source PR, posted when the board closed, and the chair's run-directory hash file.
- The reviewer's comment links that channel and gives its timestamp. It also names the hash, the run id, the fence digest and the board's disposition, as ADR 0309 requires.
- The comparison is byte for byte when the reviewer holds the artifact, and by whole-file SHA-256 when it holds only a hash.
- The reviewer confirms that the cited board decided that target.

## Record compatibility

A harness sourced `read_record_text` and `parse_slate_fence` verbatim from `e51b786e` and ran them through live Contents API reads. The old decode and parser were extracted verbatim from `d300ac04`. The harness ran both over every `docs/decisions/*/arb-record.md` at `main` `d300ac04`: **37 records, 37 identical `(digest, run_id)` results, 0 mismatches.** Every record was served `base64`; the largest is 108,393 bytes. No open verJSON/.github PR's review carried an arb-consensus citation when the reviews were scanned on 2026-11-26, so there was no other `archive_sha` to check.

The Contents API inlines `content` only for files up to 1 MB. A larger file is served with encoding `none`, so the strict reader refuses it ("is encoded as none, not base64") on both the lane path and the citation path. A chair record above 1 MB cannot be archived or cited until the reader gains a raw-media path.

## Defect injection (run 23, D1–D23)

The chair applied 26 mutants against the suite at `e51b786e`. Each applied, and 23 failed the suite.

| Mutant | Failing tests | Mutant | Failing tests |
| --- | --- | --- | --- |
| D1 no head binding | 2 | D13 file-list failure falls back | 1 |
| D2 `$` anchor | 1 | D14 nested NEXT accepted | 2 |
| D3 no tree mode check | 4 | D15 index any status | 1 |
| D4 no page limit | 1 | **D15b index removed accepted** | **0 (survived)** |
| D4b paginate | 1 | **D15c index renamed accepted** | **0 (survived)** |
| D5 no pairing | 2 | D16 executable mode accepted | 1 |
| D6 no served-path check | 1 | D17 no served-type check | 2 |
| D7 lane exit 3 falls back | 5 | D18 NUL accepted | 1 |
| D8 no record parse | 2 | D19 invalid UTF-8 accepted | 1 |
| D9 record any status | 3 | D20 any encoding | 1 |
| D10 lane any repo | 1 | D21 malformed falls back | 1 |
| D11 no record required | 1 | **D22 lenient base64** | **0 (survived)** |
| D12 truncated tree accepted | 1 | D23 directory not refused | 1 |

The shipped code refuses all three survivors; the suite has no test that pins them. The tests that do are tracked in [#2288](https://github.com/verJSON/.github/issues/2288). They land in the first PR that changes `scripts/ci-gate/arb-consensus-citation.sh` after #2287, before any other lane change.

## Follow-ups

- [#2286](https://github.com/verJSON/.github/issues/2286), [N1 comment](https://github.com/verJSON/.github/issues/2286#issuecomment-7178623258): leading `^` anchors (`\A`), the gate runner's jq and Oniguruma versions, and newline-prefix lane tests.
- [#2283](https://github.com/verJSON/.github/issues/2283), [N3 first-parent note](https://github.com/verJSON/.github/issues/2283#issuecomment-7178623685): refuse an `archive_sha` not reachable from `main` by first-parent ancestry.
- [#2288](https://github.com/verJSON/.github/issues/2288) covers the N2 parser and the TESTS item. It validates the base64 alphabet inside jq and refuses `NaN`/`Infinity`. It requires `run_id` to match the citation grammar, and adds the D15b/D15c/D22 and further lane tests on both paths. Until it lands, the lane admits records under the existing lenient `NaN` and first-line `run_id` parse.
- [#2289](https://github.com/verJSON/.github/issues/2289): check that the modified index equals `gen-adr-index.sh` output at the head, and that each added NEXT fragment passes `changelog.py validate`.

## Ratification evidence

- **Form.** A publisher attestation comment on #2287 ([7183499120](https://github.com/verJSON/.github/pull/2287#issuecomment-7183499120), 2026-11-26T05:27:16Z). It quotes the verbatim request put to the owner, which is an expanded form of the record's request to the owner: it names all nine item-22 points, including those the record's request block left out, and offers a whole-request alternative (a new head and board) in place of the block's per-item rejection option, and the owner's verbatim reply, `Ratified`. This is the form ADR 0305 and ADR 0308 used. It supersedes an earlier, shorter request ([7181605861](https://github.com/verJSON/.github/pull/2287#issuecomment-7181605861), reply `Ratified (Recommended)`), which named the test gap, the stale wording and part of the retained dissent only by reference. Its authorship cannot be machine-verified until [#1964](https://github.com/verJSON/.github/issues/1964) (ADR 0257 Decision 8).
- **Scope.** The full request covers four points, each spelled out:
  - the lane and ADR 0309 at `e51b786ef932bc2e0e577ba65dabd728cf88dc2f`, including the stricter shared reader that applies to citations in every promotion-enabled repository;
  - the one-use admission of #2287 alone, under this delta and its procedure, merged with a merge commit and setting no precedent;
  - the debt recorded here and tracked in #2286, #2283, #2288 and #2289: the lenient NaN and first-line run_id parse, the leading anchors and jq version, the first-parent rule, the surviving mutants D15b, D15c and D22, the missing generator check, and the stale wording in ADR 0309's Authority line and lines 15 and 21, the NEXT fragment and the PR body;
  - run 23's ruling of codex's retained REVISE on LANE, R22, N2 and M1 as kind (b).

  It also offered the alternative of fixing the parser on a new head under a new board.
- **Timing.** The delta was first prepared after the earlier ratification ([7181605861](https://github.com/verJSON/.github/pull/2287#issuecomment-7181605861), 2026-11-26T03:19:13Z), which states that nothing in the delta had been prepared or pushed before it. The delta was then amended to link the full-form ratification ([7183499120](https://github.com/verJSON/.github/pull/2287#issuecomment-7183499120), 2026-11-26T05:27:16Z). That comment says a local delta already existed and would be updated to link it, and it predates the final commit.
- **Link.** ADR 0309's Status line links the comment.
- **Verification.** The independent reviewer verifies the comment's form, scope and timing and the Status-line link, then posts that verification before the citation (run 23 ADMIT (2)).

## Run-22 and run-23 kind (b) items

The run-22 record is ADR 0310's predecessor run (`arb-dotgithub-pr2287-ac4ff510f6-20261125-01`). "Fixed" cites `e51b786e`. `citation.sh` is `scripts/ci-gate/arb-consensus-citation.sh`, `test.sh` is `scripts/assert-mergeable-head.review-receipt.test.sh`, and `0309` is ADR 0309's README.

| Run | Item | Status |
| --- | --- | --- |
| 22 | 1 LANE leading `^`, newline-prefix test | Assigned: [#2286](https://github.com/verJSON/.github/issues/2286#issuecomment-7178623258) |
| 22 | 2 LANE merge method until #2283 | Carried in ADR 0310 (Citable commits) |
| 22 | 3 TRUST four points | Fixed: `0309:46`, `0309:47`, `0309:48`; channel in ADR 0310 |
| 22 | 4 FIT (a)–(c) | Fixed: `0309:5` (no quotation), `0309:44`, `0309:50` |
| 22 | 5 TESTS index/mode/gitlink | Fixed: `test.sh:619`, `test.sh:627`, `test.sh:636`; removed/renamed index and NEXT `100755` assigned to [#2288](https://github.com/verJSON/.github/issues/2288); D1–D23 carried in ADR 0310 |
| 22 | 6 ADMIT reviewer confirms evidence | Carried in ADR 0310 (Ratification evidence) |
| 22 | 7 LANE `^` and merge method | Assigned: [#2286](https://github.com/verJSON/.github/issues/2286); merge method carried in ADR 0310 |
| 22 | 8 TRUST Status line names ADR 0310 | Carried in ADR 0310 (this delta's Status line) |
| 22 | 9 FIT reviewer stands in for chair record | Fixed: `0309:44` |
| 22 | 10 TESTS Q1 and P1 | Fixed: `test.sh:544`, `test.sh:619`–`test.sh:653` |
| 22 | 11 ADMIT Status links, quotation, merge method | Fixed: `0309:5`; Status line and merge commit carried in ADR 0310 |
| 22 | 12 P1 fewer than 100 rows | Fixed: `citation.sh:215`, `citation.sh:238`, `test.sh:544`, `test.sh:397` |
| 22 | 13 P2 strict Contents parse | Fixed: `citation.sh:69`, `citation.sh:98`, `citation.sh:102`; `test.sh:323`, `test.sh:333`, `test.sh:342`, `test.sh:350` |
| 22 | 14 T1 four points, fold later | Fixed: `0309:46`–`0309:48`; fold carried in ADR 0310 |
| 22 | 15 F1 | Fixed: `0309:5` |
| 22 | 16 Q1 tests | Fixed: `test.sh:619`, `test.sh:627`, `test.sh:636`, `test.sh:643`, `test.sh:653`; removed/renamed index assigned to [#2288](https://github.com/verJSON/.github/issues/2288) |
| 22 | 17 AD1 terminal basis, no precedent | Carried in ADR 0310 (Decision 3) |
| 22 | 18 LANE `^` and merge method | Assigned: [#2286](https://github.com/verJSON/.github/issues/2286); merge method carried in ADR 0310 |
| 22 | 19 TRUST T1 and fold | Fixed: `0309:46`–`0309:48`; fold carried in ADR 0310 |
| 22 | 20 FIT generator check, header relabel | Assigned: [#2289](https://github.com/verJSON/.github/issues/2289); relabel fixed at `0309:7` |
| 22 | 21 TESTS Q1, D15/D16 | Fixed: `test.sh:619` (D15), `test.sh:627` (D16); D1–D23 carried in ADR 0310 |
| 22 | 22 ADMIT dissent and new-head option in request | Carried in ADR 0310 (Ratification evidence) |
| 22 | 23 P1 | Fixed: `citation.sh:238`, `test.sh:544` |
| 22 | 24 P2 bytes preserved | Fixed: `citation.sh:69`–`citation.sh:102`; lane-path NUL/malformed tests assigned to [#2288](https://github.com/verJSON/.github/issues/2288) |
| 22 | 25 F1 | Fixed: `0309:5` |
| 22 | 26 AD1 | Carried in ADR 0310 (Decision 3, Ratification evidence) |
| 23 | 1 LANE N1 comment | Assigned: [#2286](https://github.com/verJSON/.github/issues/2286#issuecomment-7178623258) |
| 23 | 2 LANE landed `main` commit, merge method | Carried in ADR 0310 (Citable commits); [#2283 note](https://github.com/verJSON/.github/issues/2283#issuecomment-7178623685) |
| 23 | 3 LANE record compatibility, 1 MB | Carried in ADR 0310 (Record compatibility) |
| 23 | 4 TRUST channel | Carried in ADR 0310 (Independent provenance channel) |
| 23 | 5 FIT Status line, four points | Carried in ADR 0310 (Clarifications; this delta's Status line) |
| 23 | 6 FIT NEXT edit, PR body, generator issue | Carried in ADR 0310 (this delta's NEXT edit); assigned: [#2289](https://github.com/verJSON/.github/issues/2289) |
| 23 | 7 TESTS follow-up, D1–D23 | Assigned: [#2288](https://github.com/verJSON/.github/issues/2288); D1–D23 carried in ADR 0310 |
| 23 | 8 R22 table, comments | Carried in ADR 0310 (this table); [#2286](https://github.com/verJSON/.github/issues/2286#issuecomment-7178623258), [#2283](https://github.com/verJSON/.github/issues/2283#issuecomment-7178623685) |
| 23 | 9 LANE record compatibility | Carried in ADR 0310 (Record compatibility) |
| 23 | 10 TRUST channel, comparison, target | Carried in ADR 0310 (Independent provenance channel) |
| 23 | 11 FIT four points, PR body, generator issue | Carried in ADR 0310 (Clarifications); assigned: [#2289](https://github.com/verJSON/.github/issues/2289) |
| 23 | 12 R22 table | Carried in ADR 0310 (this table) |
| 23 | 13 ADMIT five points | Carried in ADR 0310 (Decisions 2, 3, 5; Ratification evidence) |
| 23 | 14 N1 | Assigned: [#2286](https://github.com/verJSON/.github/issues/2286#issuecomment-7178623258) |
| 23 | 15 N2 | Assigned: [#2288](https://github.com/verJSON/.github/issues/2288) |
| 23 | 16 N3 | Carried in ADR 0310 (Citable commits; Clarifications); [#2283 note](https://github.com/verJSON/.github/issues/2283#issuecomment-7178623685) |
| 23 | 17 LANE `NaN`, `run_id` grammar | Assigned: [#2288](https://github.com/verJSON/.github/issues/2288); interim disclosure carried in ADR 0310 (Follow-ups) |
| 23 | 18 TRUST channel, comparison | Carried in ADR 0310 (Independent provenance channel) |
| 23 | 19 FIT Status line, three points, NEXT, generator issue | Carried in ADR 0310 (Clarifications; this delta); assigned: [#2289](https://github.com/verJSON/.github/issues/2289) |
| 23 | 20 TESTS lane tests, D1–D23 | Assigned: [#2288](https://github.com/verJSON/.github/issues/2288); D1–D23 carried in ADR 0310 |
| 23 | 21 R22 table, comments, N2 issue | Carried in ADR 0310 (this table); [#2286](https://github.com/verJSON/.github/issues/2286#issuecomment-7178623258), [#2283](https://github.com/verJSON/.github/issues/2283#issuecomment-7178623685), [#2288](https://github.com/verJSON/.github/issues/2288) |
| 23 | 22 ADMIT request items, owner's choice | Carried in ADR 0310 (Decision 3; Ratification evidence) |
| 23 | 23 N1 | Assigned: [#2286](https://github.com/verJSON/.github/issues/2286#issuecomment-7178623258) |
| 23 | 24 N2 | Assigned: [#2288](https://github.com/verJSON/.github/issues/2288) |
| 23 | 25 N3 | Carried in ADR 0310 (Citable commits); [#2283 note](https://github.com/verJSON/.github/issues/2283#issuecomment-7178623685) |

## Consequences

- #2287's exact-head Gate R citation may name `verJSON/.github@<reviewed-head>:docs/decisions/0310-verjson-dotgithub-2287-archive-lane-admission-arb/arb-record.md`. That is the one use this ADR admits. A fresh ADR 0202 receipt for that head follows the citation.
- After #2287 lands and ADMIT (5) is verified, later citations of this record, or of any archive, name a verified landed `main` commit as defined above, never a branch commit.

[arb-record]: ./arb-record.md
