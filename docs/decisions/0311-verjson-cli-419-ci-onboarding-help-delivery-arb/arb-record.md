# verJSON/verjson-cli#419 delivery board (run 24): head 0207332

Run `arb-verjson-cli-pr419-02073324c6-20261126-01`, round 2 of 3. **Status: APPROVED_WITH_CONSTRAINTS. Merge decision: APPROVED** for verJSON/verjson-cli#419 at exactly `02073324c65d55f80c0ab932f9dd5dc6a4eea9e0` (base `main` `025bc469f`, release v2.2.0; merge base `abd10851e`), through ordinary protection with `--match-head-commit`, never `--admin`. This is a delivery board: no companion ADR and no owner ratification apply. The chair was non-voting.

The owner decision rule is met for every candidate in the final round: claude (required, verified) ACCEPTs all 6 candidates; advisory counts are below. No kind (a) item was upheld. This board grants no merge, push, posting or review authority.

ADR-style fence SHA-256 `8df64617190c9461be7b52460b3f396a5aeb421463471fc33a0ae39e59609f42`. Final-round validator slate SHA-256 `60536b75f5f61cca5a458567217083ecf596a969d6813e5da3a4a8e876844936` (ledger schema version 2). Validator: `validate_ledger.py ledger-final.json --state run-cont.sqlite` prints "ARB ledger valid" and exits 0; JSON report `APPROVED`, `run_state_verified: true`.

```canonical-slate-json
{"adjudications":[{"adjudicated_by":"claude","finding":"codex round-1 REVISE on TESTS","outcome":"dismissed as merge blocker (kind (b))","reason":"T1 is kind (b); my reasons are under T1. Each new behavior has a test that fails when the behavior is removed. The bare form is pinned at tests/dispatch.test.ts:146-152 (R1 was caught). The help-command forms are pinned at tests/dispatch.test.ts:154-161 (R2-R5 were caught). The short flag was caught by R8. The new sentence and the reworded opening are pinned in the unit tests (D1 and D2 were caught). No new test cannot fail, and none is a ladder of earlier tests. The surviving R6 and R7 mutations are coverage debt: they show the 127 boundary is not pinned, but no route is wrong at the head.","round":2},{"adjudicated_by":"claude","finding":"codex round-1 REVISE on M1","outcome":"dismissed as merge blocker (kind (b))","reason":"No kind (a) defect remains. ROUTE has no wrong handler and no regression (src/routing.ts:10-25). MSG makes no false statement (src/dispatch.ts:36-38). TESTS has no test that cannot fail and no new behavior left unpinned; T1 is kind (b) for the reasons given under T1. DOCS makes no false claim (README.md:87 and the NEXT fragment at lines 9-13), and 'component: ci' is kind (b) by the board's own rule. Codex's round-1 REVISE on M1 rests entirely on T1, so it is dismissed as a merge blocker and its fix is recorded. #419 may merge at exactly 02073324c65d55f80c0ab932f9dd5dc6a4eea9e0 through ordinary protection, using --match-head-commit and never --admin. This ballot grants no merge authority.","round":2}],"board_kind":"delivery","decision_rule":"owner charter (2026-11-24, as runs 18-23): claude required; codex, cursor-agent, agy advisory; each candidate needs claude ACCEPT and >=2 of 3 advisory ACCEPT; every advisory REVISE/REJECT adjudicated by claude in a later ballot as kind (a) or kind (b); chair-enforced on ledger v2","defect_injection":{"D1-no-schema-sentence":"caught","D2-old-opening":"caught","R1-bare-not-routed-native":"caught","R2-no-help-command":"caught","R3-help-alone-not-native":"caught","R4-help-schema-not-native":"caught","R5-help-flag-not-native":"caught","R6-help-any-subject-native":"survived","R7-any-3-arg-native":"survived","R8-short-flag-dropped":"caught"},"kind_a_blocking":[],"kind_a_disputed":[],"kind_b_debt":["ROUTE (claude, round 1; follow-up issue): Decide whether 'ci onboarding <unsupported> --help|-h' and 'ci onboarding help <unsupported>' should show the onboarding group help, or keep the 127 response. Record the decision as tests either way.","MSG (claude, round 1; follow-up issue): Give a schema invocation with extra or unknown arguments its own error, for example 'verjson ci onboarding schema takes no arguments', using commander's own unknown-option or excess-argument handling, and add an end-to-end test for it. Optionally assert assert.equal(code, 127) in tests/dispatch-unit.test.ts:280.","TESTS (claude, round 1; this PR): Add ['plan'] and ['help', 'plan'] to the args list in tests/dispatch.test.ts:164-169, then confirm that R6 and R7 each fail the suite.","DOCS (claude, round 1; this PR): Remove 'component: ci' from this fragment, or set it explicitly at the next release dispatch through the 'fragments' input with the #418 and #187 files, so the patch release notes include both. Then file one issue to decide whether this single-package repository should use component streams at all.","M1 (claude, round 1; this PR): Close the TESTS and DOCS items in this PR at a new head, or record them on #418 before release. File one follow-up issue for the MSG and ROUTE design choices.","TESTS (claude, round 2; this PR): Apply the fix recorded under T1.","DOCS (claude, round 2; this PR): Choose one of two fixes. Remove 'component: ci' from this fragment and confirm the unscoped release plan selects it. Or, at the next release dispatch, select the #418 and #187 fragments explicitly through the 'fragments' input. Then open one issue to decide whether this single-package repository should use component streams at all.","T1 (claude, round 2; this PR): At tests/dispatch.test.ts:154-161, assert the exact Usage line for each form. 'help schema' should print 'Usage: verjson ci onboarding schema [options]'. The forms 'help', 'help --help' and 'help -h' should print 'Usage: verjson ci onboarding [options] [command]'. Add ['plan'], ['help', 'plan'] and ['help', 'schema', '--help'] to the table at tests/dispatch.test.ts:164-169. At tests/dispatch-unit.test.ts:280, capture the result and assert.equal(code, 127). Then re-run R6 and R7, and a mutation that shows group help in place of schema help, and confirm each one fails. Do this on a new head of this PR before merge if practical; otherwise record it on #418 before the next patch release.","M1 (claude, round 2; this PR): Land the T1 and DOCS fixes on this PR if it gets a new head (that new head needs a fresh delivery adjudication), or record them on #418 before the next patch release. File one follow-up issue covering the MSG and ROUTE design choices."],"ledger_status":"APPROVED","max_rounds":3,"member_identity":{"agy":"unverified (identity_gap); JSON response carries no served-model field","claude":"verified claude-opus-5-5 (probe 2026-11-26T05:57:28Z; response modelUsage named exactly claude-opus-5-5/claude-opus-5-5 in rounds 1/2)","codex":"unverified (identity_gap); requested gpt-6.2-sol; codex exec output names no served model","cursor-agent":"unverified (identity_gap); JSON result carries no served-model field"},"merge_conditions":"Merge only at exactly 02073324c65d55f80c0ab932f9dd5dc6a4eea9e0 through ordinary protection: the organization merge guard on that exact head, --match-head-commit, never --admin. No companion ADR and no owner ratification apply (delivery board).","merge_decision":"APPROVED","next_action":"RUN_VERIFICATION","prompt_transport":{"agy":"one-line instruction to read a 0400 workspace copy of the prompt","claude":"prompt file on stdin (claude -p)","codex":"prompt file on stdin (codex exec -)","cursor-agent":"prompt file on stdin (cursor-agent --print --mode ask)"},"retained_dissent":[],"round_slate_sha256":{"r1":"f3d8c66c951bdb3650ef9cc3c12d07de7723349b9d3065f8272ef1cb3d48274a","r2":"60536b75f5f61cca5a458567217083ecf596a969d6813e5da3a4a8e876844936"},"rounds_used":2,"run_id":"arb-verjson-cli-pr419-02073324c6-20261126-01","schema_version":2,"source_pins":[{"id":"pr-head","ref":"fix/418-ci-onboarding-availability (PR #419)","repo":"verJSON/verjson-cli","role":"primary","sha":"02073324c65d55f80c0ab932f9dd5dc6a4eea9e0"},{"id":"main-base","ref":"main","repo":"verJSON/verjson-cli","role":"baseline","sha":"025bc469fb93f554211c96846713c739cc38d7f0"}],"status":"APPROVED_WITH_CONSTRAINTS","subjects":[{"base_sha":"025bc469fb93f554211c96846713c739cc38d7f0","head_sha":"02073324c65d55f80c0ab932f9dd5dc6a4eea9e0","issue":418,"merge_base_sha":"abd10851ed3494456b4261c786fdbe6d33e8b398","merge_decision":"APPROVED","pr":419,"repo":"verJSON/verjson-cli"}],"supermajority_met":true,"tally_final_round":{"DOCS":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true},"M1":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true},"MSG":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true},"ROUTE":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true},"T1":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true},"TESTS":{"advisory":{"agy":"ACCEPT","codex":"ACCEPT","cursor-agent":"ACCEPT"},"advisory_accepts":3,"claude":"ACCEPT","supermajority_met":true}},"validator_slate_sha256":"60536b75f5f61cca5a458567217083ecf596a969d6813e5da3a4a8e876844936"}
```

## Decision rule and how it was enforced

Owner charter (2026-11-24, as runs 18-23): claude is the only required member; codex, cursor-agent and agy are advisory and unverified; each candidate needs claude ACCEPT **and** at least 2 of 3 advisory ACCEPTs; every advisory REVISE or REJECT is adjudicated by claude in a later ballot. The ledger v2 engine sees only claude, so the chair enforced the rule from the recorded advisory ballots, each bound to its raw-response sha256. Ledger schema v2 because the installed skill's adoption manifest lists only v2.

Round 1 closed APPROVED on claude alone in `run.sqlite`, which made that engine run terminal while codex's dissent still needed claude's adjudication, so later rounds ran in the continuation state file `run-cont.sqlite`; the three-round cap was chair-enforced:

| Board round | Slate revision | State file | Slate SHA-256 |
|---|---|---|---|
| 1 | 1 | `run.sqlite (engine round 1)` | `f3d8c66c951bdb3650ef9cc3c12d07de7723349b9d3065f8272ef1cb3d48274a` |
| 2 | 2 | `run-cont.sqlite (engine round 1)` | `60536b75f5f61cca5a458567217083ecf596a969d6813e5da3a4a8e876844936` |

## Adjudications

- **codex round-1 REVISE on TESTS** — claude, round 2: dismissed as merge blocker (kind (b)). Reason: T1 is kind (b); my reasons are under T1. Each new behavior has a test that fails when the behavior is removed. The bare form is pinned at tests/dispatch.test.ts:146-152 (R1 was caught). The help-command forms are pinned at tests/dispatch.test.ts:154-161 (R2-R5 were caught). The short flag was caught by R8. The new sentence and the reworded opening are pinned in the unit tests (D1 and D2 were caught). No new test cannot fail, and none is a ladder of earlier tests. The surviving R6 and R7 mutations are coverage debt: they show the 127 boundary is not pinned, but no route is wrong at the head.
- **codex round-1 REVISE on M1** — claude, round 2: dismissed as merge blocker (kind (b)). Reason: No kind (a) defect remains. ROUTE has no wrong handler and no regression (src/routing.ts:10-25). MSG makes no false statement (src/dispatch.ts:36-38). TESTS has no test that cannot fail and no new behavior left unpinned; T1 is kind (b) for the reasons given under T1. DOCS makes no false claim (README.md:87 and the NEXT fragment at lines 9-13), and 'component: ci' is kind (b) by the board's own rule. Codex's round-1 REVISE on M1 rests entirely on T1, so it is dismissed as a merge blocker and its fix is recorded. #419 may merge at exactly 02073324c65d55f80c0ab932f9dd5dc6a4eea9e0 through ordinary protection, using --match-head-commit and never --admin. This ballot grants no merge authority.

## Kind (b) items (non-blocking; for this PR before merge where marked, otherwise follow-up issues)

1. ROUTE (claude, round 1; follow-up issue): Decide whether 'ci onboarding <unsupported> --help|-h' and 'ci onboarding help <unsupported>' should show the onboarding group help, or keep the 127 response. Record the decision as tests either way.
2. MSG (claude, round 1; follow-up issue): Give a schema invocation with extra or unknown arguments its own error, for example 'verjson ci onboarding schema takes no arguments', using commander's own unknown-option or excess-argument handling, and add an end-to-end test for it. Optionally assert assert.equal(code, 127) in tests/dispatch-unit.test.ts:280.
3. TESTS (claude, round 1; this PR): Add ['plan'] and ['help', 'plan'] to the args list in tests/dispatch.test.ts:164-169, then confirm that R6 and R7 each fail the suite.
4. DOCS (claude, round 1; this PR): Remove 'component: ci' from this fragment, or set it explicitly at the next release dispatch through the 'fragments' input with the #418 and #187 files, so the patch release notes include both. Then file one issue to decide whether this single-package repository should use component streams at all.
5. M1 (claude, round 1; this PR): Close the TESTS and DOCS items in this PR at a new head, or record them on #418 before release. File one follow-up issue for the MSG and ROUTE design choices.
6. TESTS (claude, round 2; this PR): Apply the fix recorded under T1.
7. DOCS (claude, round 2; this PR): Choose one of two fixes. Remove 'component: ci' from this fragment and confirm the unscoped release plan selects it. Or, at the next release dispatch, select the #418 and #187 fragments explicitly through the 'fragments' input. Then open one issue to decide whether this single-package repository should use component streams at all.
8. T1 (claude, round 2; this PR): At tests/dispatch.test.ts:154-161, assert the exact Usage line for each form. 'help schema' should print 'Usage: verjson ci onboarding schema [options]'. The forms 'help', 'help --help' and 'help -h' should print 'Usage: verjson ci onboarding [options] [command]'. Add ['plan'], ['help', 'plan'] and ['help', 'schema', '--help'] to the table at tests/dispatch.test.ts:164-169. At tests/dispatch-unit.test.ts:280, capture the result and assert.equal(code, 127). Then re-run R6 and R7, and a mutation that shows group help in place of schema help, and confirm each one fails. Do this on a new head of this PR before merge if practical; otherwise record it on #418 before the next patch release.
9. M1 (claude, round 2; this PR): Land the T1 and DOCS fixes on this PR if it gets a new head (that new head needs a fresh delivery adjudication), or record them on #418 before the next patch release. File one follow-up issue covering the MSG and ROUTE design choices.

## Retained dissent

None.

## ROUTE · Routing correctness: every 'ci onboarding' argv shape reaches the right handler, and no other CLI route changes

routeChild returns undefined (commander handles it natively) for: bare 'ci onboarding'; 'ci onboarding help'; 'ci onboarding help schema|--help|-h'; 'ci onboarding schema'; 'ci onboarding --help|-h'; 'ci onboarding schema --help|-h'. Every other 'ci onboarding ...' argv routes to the ci child, which prints the unavailable message and exits 127. No other route (identity, cloud, runners, artifacts, project, other 'ci' commands, top-level help) changes. Kind (a): a shape that reaches the wrong handler with a user-visible regression (for example a supported form that now errors, or an unsupported form that now reaches commander and fails differently). CHAIR POINTS TO WEIGH (see FACTS): 'ci onboarding help help', 'help plan', 'help schema --help', '-h schema' and '--help extra' exit 127 with the unavailable message (all but 'help help'/'help plan' are byte-identical in behavior to the merge base; at the base those two also exit 127); bare 'ci onboarding' prints help on stderr and exits 1, the same commander group convention bare 'ci' and 'project' follow.

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=not on slate) |
|---|---|
| 1 | claude:A / codex:A / cursor-agent:A / agy:A |
| 2 | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**. Key source: `verJSON/verjson-cli@0207332:src/routing.ts:10`, `verJSON/verjson-cli@0207332:src/routing.ts:12`.

## MSG · The CI unavailable message is accurate, and its tests pin it

The message says the invoked onboarding command is unavailable in this release, why, where it is tracked (#187), and that only 'verjson ci onboarding schema' is available; every statement is true for the release that ships it. Kind (a): a false statement. CHAIR POINT TO WEIGH: 'ci onboarding schema extra' and 'schema --unknown' also print this message ('this ... command is not available ... only ... schema is available'), which is literally true of the invocation but may read as contradictory to a user who typed 'schema'; this was 127 with the older message at the base too.

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=not on slate) |
|---|---|
| 1 | claude:A / codex:A / cursor-agent:A / agy:A |
| 2 | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**. Key source: `verJSON/verjson-cli@0207332:src/dispatch.ts:36`, `verJSON/verjson-cli@0207332:src/dispatch.ts:38`.

## TESTS · Tests pin the new behavior, one behavior per test, and fail when it is removed

Apart from T1 as adjudicated: Each new behavior has a test that fails when the behavior is removed (chair defect injection R1-R5, R8, D1, D2 caught), each test asserts the exit code and a specific output, and no test is a ladder of earlier ones. Kind (a): a test that cannot fail, or a new behavior no test pins. CHAIR POINTS TO WEIGH: R6 (any 'help <x>' native) and R7 (any 3-argument 'ci onboarding <x>' native, so 'ci onboarding plan' with no file would become commander's 'unknown command' exit 1) survive: the unsupported-forms test only uses 4-argument shapes; the help-command test loops over four argv forms of one behavior.

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=not on slate) |
|---|---|
| 1 | claude:A / codex:R / cursor-agent:A / agy:A |
| 2 | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**. Key source: `verJSON/verjson-cli@0207332:tests/dispatch.test.ts:146`, `verJSON/verjson-cli@0207332:tests/dispatch.test.ts:154`.

## DOCS · README and NEXT fragment are accurate; whether 'component: ci' keeps the fragment out of CLI releases

Every README and fragment claim matches the behavior at the head (bare and help forms print help; plan and verify-canary exit 127 with a message naming 'schema'), and the fragment is valid for the changelog contract. Kind (a): a false doc claim. Per the board question, if 'component: ci' keeps the fragment out of default (unscoped) CLI releases, that is recorded as kind (b), never a blocker. CHAIR FACTS: release.yml's empty component input selects only unscoped fragments; #407's component-ci fragment was not consumed by v2.2.0, whose changelog lists only #410.

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=not on slate) |
|---|---|
| 1 | claude:A / codex:A / cursor-agent:A / agy:A |
| 2 | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**. Key source: `verJSON/verjson-cli@0207332:README.md:87`, `verJSON/verjson-cli@0207332:NEXT/2026-11-25-issue-418-ci-onboarding-help-and-availability.md:4`.

## T1 · codex round-1 TESTS finding: the help-command test's assertion is shared by the group help and the schema help, so 'ci onboarding help schema' could show the wrong page and pass; the unsupported table has no 3-argument shape or 'help <unsupported>' (R6/R7 survive); the new unit test discards the exit code

CHAIR-VERIFIED FACTS: the string 'Print the CI onboarding request and contract JSON schemas' appears once in the group help (as the schema command's description) and in the schema help, so the help-command test's assertion cannot distinguish them; at the head 'ci onboarding help schema' does print 'Usage: verjson ci onboarding schema [options]' (correct page). The page shown is chosen by commander's built-in help command, which this PR does not change; the PR's routing change is what the test must pin (R2-R5 caught). R6/R7 survive as reported; at the head 'ci onboarding plan' and 'help plan' exit 127 (correct). The unit test at the new sentence discards exitCodeOf's result; the 127 exit is pinned end-to-end by the unsupported table. Under the calibration, kind (a) is 'a test that cannot fail' or a wrong route; these tests can fail (mutations caught) and no route is wrong at this head. If kind (b), the fix (this PR or a follow-up before release): assert the exact Usage line per help form ('Usage: verjson ci onboarding schema [options]' for 'help schema', 'Usage: verjson ci onboarding [options] [command]' for the others); add ['plan'], ['help', 'plan'] and ['help', 'schema', '--help'] to the unsupported table; assert 127 in the unit test; re-run R6/R7 to show they fail. If kind (a), those changes on a new head and a new board. ADJUDICATION (claude rules; advisory members vote too): ACCEPT means codex's round-1 TESTS/M1 REVISE is dismissed as a merge blocker (kind (b)) and the stated fix is recorded; REVISE upholds it as kind (a), which blocks merge at 0207332 (state the concrete scenario and correction).

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=not on slate) |
|---|---|
| 1 | claude:— / codex:— / cursor-agent:— / agy:— |
| 2 | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**. Key source: `verJSON/verjson-cli@0207332:tests/dispatch.test.ts:154`, `verJSON/verjson-cli@0207332:tests/dispatch.test.ts:159`.

## M1 · verJSON/verjson-cli#419 may merge at exactly 0207332

No kind (a) defect remains in ROUTE, MSG, TESTS or DOCS, so #419 may merge at exactly 02073324c65d55f80c0ab932f9dd5dc6a4eea9e0 through ordinary protection (the organization merge guard on that exact head, --match-head-commit, never --admin), with kind (b) items recorded as follow-ups. This is a delivery board: no companion ADR and no owner ratification are required. Approval needs claude ACCEPT and at least 2 of 3 advisory ACCEPTs in the final round, with every advisory REVISE or REJECT adjudicated by claude in a later ballot. This board grants no merge, push, posting or review authority itself.

| Round | claude / codex / cursor-agent / agy (A=ACCEPT, R=REVISE, —=not on slate) |
|---|---|
| 1 | claude:A / codex:R / cursor-agent:A / agy:A |
| 2 | claude:A / codex:A / cursor-agent:A / agy:A |

Final-round supermajority: claude ACCEPT; advisory ACCEPTs 3/3 (codex ACCEPT, cursor-agent ACCEPT, agy ACCEPT); **condition met**. Key source: `verJSON/verjson-cli@0207332:src/routing.ts:10`, `verJSON/verjson-cli@0207332:src/dispatch.ts:38`.

## Members, identity and transport

| Member | Role | Identity | Prompt transport |
|---|---|---|---|
| claude | required | verified claude-opus-5-5 (probe 2026-11-26T05:57:28Z; response modelUsage named exactly claude-opus-5-5/claude-opus-5-5 in rounds 1/2) | prompt file on stdin (claude -p) |
| codex | advisory | unverified (identity_gap); requested gpt-6.2-sol; codex exec output names no served model | prompt file on stdin (codex exec -) |
| cursor-agent | advisory | unverified (identity_gap); JSON result carries no served-model field | prompt file on stdin (cursor-agent --print --mode ask) |
| agy | advisory | unverified (identity_gap); JSON response carries no served-model field | one-line instruction to read a 0400 workspace copy of the prompt |

- **Probe:** `discover_providers.py probe` then `roster` at 2026-11-26T05:57:28Z: verified claude/claude-opus-5-5; advisory agy, codex and cursor-agent; none unavailable.
- **Prompts:** 0600 files in a private run directory (103,860, 123,253 bytes), never a single CLI argument. **Calls:** 8, all recorded on the first attempt; failed calls 0; no ballot repaired.

## Charter

- **Question:** may verJSON/verjson-cli#419 merge at exactly `02073324c65d55f80c0ab932f9dd5dc6a4eea9e0`?
- **Calibration:** kind (a) is a user-visible regression, a wrong route, a test that cannot fail, or a false doc claim; everything else is kind (b). Whether `component: ci` keeps the fragment out of CLI releases is kind (b) by the question.
- **Disclosure:** the repository is private; this record is written for archiving in public verJSON/.github and carries no secrets, host data or operator state. Issue #418's customer context was summarized to members without names.

## Evidence

Chair-verified, read-only:
- **Provenance:** PR `headRefOid` `02073324c65d55f80c0ab932f9dd5dc6a4eea9e0`; GitHub `baseRefOid` and merge base `abd10851e`; `main` `025bc469f` differs from the merge base only by release files. Checks at the head: 19 success, 8 skipped.
- **Tests:** on an export of the head, `tests/dispatch.test.ts` and `tests/dispatch-unit.test.ts` pass (37/37). The full-suite, typecheck, lint, changelog-contract and repo-check results and the three code-reviewer passes are as reported in the PR; the chair did not rerun the full suite.
- **Behavior table:** bare `ci onboarding` prints group help on stderr and exits 1; `help`, `help schema`, `help --help`, `help -h`, `--help`, `schema`, `schema --help` exit 0; `plan …`, `verify-canary …`, `schema extra`, `schema --unknown`, `help plan`, `help help`, `help schema --help`, `-h schema` exit 127 with the new message. Shapes outside `ci onboarding` behave identically at the merge base and the head.
- **Defect injection** (each mutation alone, both dispatch test files): R1-bare-not-routed-native caught; R2-no-help-command caught; R3-help-alone-not-native caught; R4-help-schema-not-native caught; R5-help-flag-not-native caught; R6-help-any-subject-native survived; R7-any-3-arg-native survived; R8-short-flag-dropped caught; D1-no-schema-sentence caught; D2-old-opening caught.
- **Release stream:** an empty `component` input selects only unscoped fragments; the `component: ci` fragment from #407 remained in `NEXT/` after v2.2.0, whose changelog lists only #410.

## Observations and limits

- **Process metrics:** claude cost USD 1.01, 1.02.
- **Communication contract:** the v2 ballot carried each dissent with its correction and raw digest; no change indicated.

## Standards grounding
- status: grounded
- reason: null
- config_origin: installed
- config_provenance: installer-default
- source: verJSON/verjson-docs:governance/architecture-review-context.json@main
- config_sha: bb94a8c83eb48c4e84b46d4910a6394f598f2337
- manifest_sha256: 8fe9918701f793debc600c70edfa6e02bd3243f775818bb9c785f6e529d2fe96 (resolver-manifest-v1)
- binding_sha256: 031de822d35e41fadc1989346a656ee80d137cbcaa5292f4325bbbe49c96c78e (resolver-binding-v1)
- canonical_complete: true
- sources:
  - engineering-standards VerJSON/verjson-agents@7dc3fe98295b051896eef56ca5182410cd60ce14:CANON.md [git_commit, available]
  - code-style VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:agents/code-style.md [git_commit, available]
  - ci-tiers VerJSON/.github@c127264a41d301a2f3f2357201789254fccc44c4:docs/ci-workflow-tiers.md [git_commit, available]
  - node-workflows VerJSON/.github@c127264a41d301a2f3f2357201789254fccc44c4:docs/node-workflows.md [git_commit, available]
  - protected-reviews VerJSON/.github@c127264a41d301a2f3f2357201789254fccc44c4:docs/ruleset-conformance.md [git_commit, available]
  - merge-admission-decision VerJSON/.github@c127264a41d301a2f3f2357201789254fccc44c4:docs/decisions/0257-merge-admission-requires-the-exact-head-review-receipt/README.md [git_commit, available]
  - merge-admission-runbook VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:agents/merge-admission.md [git_commit, available]
  - delivery-workflow VerJSON/verjson-agents@7dc3fe98295b051896eef56ca5182410cd60ce14:rules/workflow.md [git_commit, available]
  - infrastructure-overview VerJSON/.github@c127264a41d301a2f3f2357201789254fccc44c4:docs/infrastructure/README.md [git_commit, available]
  - runner-routing VerJSON/.github@c127264a41d301a2f3f2357201789254fccc44c4:docs/runner-routing.md [git_commit, available]
  - runner-lanes-decision VerJSON/.github@c127264a41d301a2f3f2357201789254fccc44c4:docs/decisions/0040-runner-lanes-and-admission-axes/README.md [git_commit, available]
  - platform-composition VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:architecture/platform-composition.md [git_commit, available]
  - capability-catalog VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:catalog/packages.md [git_commit, available]
  - security-practices VerJSON/verjson-agents@7dc3fe98295b051896eef56ca5182410cd60ce14:rules/security.md [git_commit, available]
  - security-policy VerJSON/.github@c127264a41d301a2f3f2357201789254fccc44c4:SECURITY.md [git_commit, available]
  - dependency-principles VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:architecture/dependency-principles.md [git_commit, available]
  - ownership-boundaries VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:architecture/ownership-boundaries.md [git_commit, available]
  - decision-placement VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:governance/decision-placement.md [git_commit, available]
  - agent-message-contracts-decision VerJSON/verjson-agents@7dc3fe98295b051896eef56ca5182410cd60ce14:docs/decisions/0036-versioned-schema-contracts-for-agent-messages/README.md [git_commit, available]
  - agent-message-contracts-adoption VerJSON/verjson-agents@7dc3fe98295b051896eef56ca5182410cd60ce14:contracts/agent-messages/adoption.json [git_commit, available]
  - agile-ai-manifesto VerJSON/agile-ai-manifesto@15d91cb19be0b3889165b7a7ca7bb905afbcd831:public/index.html [git_commit, available]
  - tda-review-guide VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:governance/technical-design-authority.md [git_commit, available]
- gaps: none
