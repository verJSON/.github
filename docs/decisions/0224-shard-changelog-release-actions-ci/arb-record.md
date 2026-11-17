# ARB chair record — early retired-caller scan (#1977 A-3)

## Standards grounding

- status: grounded
- binding_sha256: `e66630c906946b5a9fd298a14d8dcceee0cc7d279dd92da658e65053acfa0087`
- work-start anchor:
  https://github.com/verJSON/.github/issues/1977#issuecomment-6948041332

## Charter

- **Run id:** `arb-1977-2026-11-17`
- **Issue:** [verJSON/.github#1977](https://github.com/verJSON/.github/issues/1977)
- **Question:** Accept ADR 0224 amendment 2026-11-17 placing
  `scan_retired_callers` immediately after the generated-set pin check and the
  `.github/CODEOWNERS` layout check, before expensive workflow-shape and
  fixture loops?
- **Non-goals:** Changing ADR 0224 Decisions 2–3; scanner-only test path;
  fleet ruleset changes.
- **Pin:** verJSON/.github@5383b5e2bb6d218f105ae4fb7360960771064b06 (PR #2195)
- **Roster:** Advisory seats A (Composer) and B (Composer) returned durable
  ballots. Decision rule: unanimous among returned advisory seats (same
  pattern as ADR 0271 / 0272 when CLI quorum is unavailable).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `fa111ceb1f12c109ca4ca4e5e0ffd8432b44c6a0eed01ca409d63271a575c125`
  over:

  ```json
  {"issues":["verJSON/.github#1977"],"items":["S-1"],"run_id":"arb-1977-2026-11-17","status":"APPROVED"}
  ```

- **Ratification:** Owner ARB-delegation; approval path ADR 0271 + ADR 0202,
  not personal CODEOWNERS.

## Adopted slate

- **S-1 accept_amend:** Accept the early retired-caller scan placement and
  dual-fault diagnostic ordering in ADR 0224 amendment 2026-11-17. CI
  refined the call site to immediately after the CODEOWNERS ok line so
  CODEOWNERS symlink diagnostics keep precedence; retired-writer mutations
  still fail before fixture noise.

## Ballots (advisory)

| Seat | S-1 |
| --- | --- |
| A (Composer) | ACCEPT |
| B (Composer) | ACCEPT |

## Dissent

None.
