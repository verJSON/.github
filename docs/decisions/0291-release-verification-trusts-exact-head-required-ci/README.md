# 0291 — Release verification may trust exact-head required CI instead of re-running the suite

- **Date:** 2026-11-20
- **Status:** Accepted. Directed by the owner under
  [verJSON/unrwly#321](https://github.com/verJSON/unrwly/issues/321) item 8
  ("require CI and skip the rerun"), recorded for unrwly in its ADR 0024 / 0025,
  and accepted here as the organization release-caller contract that makes that
  decision implementable.
- **Issue:** [#2260](https://github.com/verJSON/.github/issues/2260)
- **Extends:** [ADR 0062](../0062-release-verifies-before-it-tags/README.md),
  [ADR 0220](../0220-confine-release-credentials-to-dependency-acquisition/README.md),
  [ADR 0244](../0244-manually-dispatched-fragment-derived-release/README.md),
  [ADR 0229](../0229-required-contexts-need-success-and-finished-runs/README.md)
- **Category:** release verification and GitHub read authority — **sensitive class**
  (changes the approved release verify permission set and what a release proves)

## Context

ADR 0062's generated release callers run a verify job before the irreversible
snapshot. The default verify path runs the repository suite (`npm test` or an
executable `scripts/release-verify.sh`). ADR 0220 then pinned that job to
**exactly** `permissions: contents: read`, refused `GITHUB_TOKEN` in the
verification step environment, and scrubbed the token from the adopter hook via
`run_clean` / `env -i`. That boundary is correct for package credentials and for
keeping repository-controlled hooks away from write-capable tokens.

It also made one useful read impossible: the verify job cannot call the Checks
API. Adopters whose required CI already ran the same suite on the exact dispatch
SHA therefore re-run minutes of identical work inside release verification.
unrwly measured about five of 6.5 minutes on the v1.0.0 verify path repeating
`npm test` and privileged database groups that `tests` (and the lanes behind it)
had already passed on that `main` commit.

unrwly's owner decision under #321 item 8 is: **require CI and skip the rerun**.
unrwly ADR 0024 added a required aggregate check named `tests`; ADR 0025 put
`tests` (with `typecheck` and `lint`) on ruleset `main-requires-ci`. The
release-side half stayed blocked on this repository: without an approved
`checks: read` grant and a generator-controlled assert step, a custom hook cannot
honestly skip the suite.

## Decision

1. **Opt-in exact-head CI trust.** `scripts/gen-changelog-caller.sh` accepts
   repeated `--require-ci-context <name>` for `release-node`, `release-artifact`,
   and `release-snapshot`. When at least one context is named, the generated
   caller:
   - sets workflow and verify-job permissions to exactly
     `{ contents: read, checks: read }` (and no other permission);
   - emits a generator-owned step, **Assert required CI on the release head**,
     immediately before **Run the release verification suite**;
   - that step receives `GITHUB_TOKEN: ${{ github.token }}` only in its own
     step `env` (same scrubbing pattern as **Resolve restart-safe release
     state**), calls the pinned helper
     `scripts/assert-release-head-ci.sh` from the already-checked-out selection
     contract path (`.changelog-contract`), then unsets the token before exit;
   - leaves the verification suite step's environment equal to today's
     `EXPECTED_RELEASE_VERIFICATION_ENV` (still no token; ADR 0220's hook
     boundary unchanged).

2. **What the assert proves.** For SHA `github.sha` (and, when
   `inputs.expected_head` is non-empty, after the existing bind step has already
   required it to equal that SHA), every named context must have at least one
   check run that:
   - was produced by GitHub Actions (`integration_id` / app id **15368**);
   - targets that exact commit SHA;
   - has `status == completed` and `conclusion == success` (compared
     case-insensitively);
   - is the latest completed run of that context name on the SHA when more than
     one exists (highest numeric `id` wins).

   Fail closed, naming the context and reason, when a context is missing; still
   queued, in progress, or waiting; completed with any conclusion other than
   `success` (including `skipped`, `neutral`, `cancelled`, `timed_out`,
   `action_required`, `stale`, `failure`); produced only by another app; or when
   the Checks API cannot be read. Do not wait for pending runs: the operator
   re-dispatches after CI is green. Do not accept commit statuses as a substitute
   for check runs.

3. **Default callers stay on today's contract.** With no
   `--require-ci-context`, permissions remain exactly `{ contents: read }`, no
   assert step is emitted, and the suite / hook behavior is unchanged. The
   embedded generator contract continues to reject any other permission set.

4. **What the release hook must still prove when CI trust is enabled.** The
   assert replaces only application-suite evidence. The verification hook (or
   default Node sequence) must still prove release-specific properties the merge
   CI did not bind to the stamped dispatch version and offline release image:
   - offline / credentialless dependency install from the acquired cache
     (ADR 0220 / adopter offline ADRs);
   - version stamp visible to the build (ADR 0062);
   - migrations against an owned empty database when the adopter's hook owns
     one;
   - production build (and lint/typecheck only when the adopter has not named
     those as required CI contexts—if they are named, the assert covers them and
     the hook may omit the duplicate);
   - any adopter-specific release guards (Compose model pins, image build,
     broker contracts) that are not part of the named CI contexts.

   The hook must **not** re-run `npm test` or privileged database groups that the
   named contexts already proved. Adopter documentation and the generated
   caller header say so next to the flag.

5. **unrwly's required contexts.** For unrwly's `release-snapshot` caller the
   approved context list is exactly:

   - `tests`
   - `typecheck`
   - `lint`

   matching ruleset `main-requires-ci` after unrwly ADR 0025. `tests` is the
   aggregate that fails unless every test lane ran and passed (unrwly ADR 0024);
   it is the suite evidence this decision substitutes for. `ci-db-gate` and the
   individual lane job names are not accepted substitutes.

6. **Digest and contract updates.** Adding the assert step and the optional
   `checks: read` permission re-records, under this ADR's control:
   - the verify-job / workflow permission predicate in the embedded generator
     contract (exact map is either `{contents: read}` or
     `{contents: read, checks: read}` with the assert step present in the latter
     case only);
   - `APPROVED_RELEASE_VERIFICATION_SCRIPT_SHA256` only if the verification
     script body changes (it should not for this decision);
   - a new `APPROVED_RELEASE_CI_ASSERT_SCRIPT_SHA256` pin for the assert step
     body in each mode that can emit it.

   Pre-credential digests (`APPROVED_RELEASE_PREACQUISITION_STEPS_SHA256`) are
   unchanged: the assert runs after acquisition, before the hook.

## Non-goals

- This ADR does not remove or weaken ADR 0220's package-token boundary.
- It does not grant `checks: write`, `contents: write`, or any App token to
  verify.
- It does not make CI trust the default for every adopter.
- It does not change merge rulesets; adopters that want the skip must already
  require the named contexts on the default branch (as unrwly did in ADR 0025).
- It does not authorize hand-editing a generated `release.yml`; adopters
  regenerate from one reviewed contract revision that includes this flag.

## Consequences

- unrwly can regenerate its release caller with
  `--require-ci-context tests --require-ci-context typecheck --require-ci-context lint`,
  slim `scripts/release-verify-container.sh` to release-specific proofs, and
  close #321 item 8 once that pin ships.
- A release dispatched before CI finishes on `github.sha` fails at the assert
  step instead of re-running the suite or tagging on incomplete evidence.
- Expanding or renaming a required context is a deliberate generator regeneration
  plus ruleset change; a stale context name fails closed.
- Other adopters keep re-running their suites until they opt in.

## Verification

- Unit tests for `scripts/assert-release-head-ci.sh` cover success, missing
  context, non-success conclusions, wrong app id, pending status, and multiple
  runs of one name (latest id wins).
- `scripts/ci-gate/changelog-caller-contract.test.sh` accepts the dual
  permission map, requires the assert step iff contexts were requested, rejects
  `checks: write` / `contents: write`, and rejects an assert step that keeps
  `GITHUB_TOKEN` in the verification suite environment.
- One disposable generation of `release-snapshot` with the three unrwly
  contexts shows the assert step and permission map; without flags, today's
  permissions and step list (names) remain.
- `scripts/gen-changelog-caller.sh` accepts `--require-ci-context` and emits the
  assert step plus `{contents: read, checks: read}` when at least one context is
  named.
