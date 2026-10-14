# 0228 — Every workflow starts its inline Python interpreters isolated

- **Date:** 2026-10-13
- **Status:** Accepted
- **Issue:** [verJSON/.github#1752](https://github.com/verJSON/.github/issues/1752)
- **Extends:** [ADR 0086](../0086-secretless-node-pr-validation/README.md)
- **Category:** CI execution boundary (defensive hardening)

## Context

ADR 0086's 2026-10-13 amendment requires the Node CI workflows to start every inline
Python interpreter with `-I`. PR #1750 (#1749) added a contract test that enforced this
for `node-ci.yml` and `node-ci-protected.yml` only.
A count during that review found non-isolated starts in most other workflows and in
the release callers `scripts/gen-changelog-caller.sh` emits for adopters.

Whether a given step's working directory is writable by a pull request varies by
workflow and changes as workflows evolve. Isolation is cheap and correct regardless,
so the invariant should not depend on that classification.

## Decision

Every Python interpreter a workflow starts — under `.github/workflows/` and in every
workflow a `scripts/gen-*` generator emits — starts isolated: `python3 -I`, including
`-m` modules, `-c` programs, stdin programs, script files, and interpreters held in
variables. A `.py` file is never started through its shebang.

- A helper that needs a module beyond the standard library and the interpreter's own
  site-packages names it explicitly. Repository scripts that import a sibling insert
  their own directory into `sys.path` before that import; the hosted-selector policy
  receives its pinned PyYAML directory as `--dependency-path` instead of `PYTHONPATH`,
  and a missing parser is undetermined (exit 2), not a policy violation.
- A step that relied on a `PYTHON*` variable requests the behavior on the command
  line: the release version guard uses `-X utf8` rather than `PYTHONUTF8`.
- `scripts/ci-gate/workflow-isolated-python.test.py` (renamed from the Node CI test)
  enforces the invariant across all committed workflows and all rendered generator
  workflows. Every `scripts/gen-*` generator must be classified as rendered or as
  emitting no workflow. It also starts every repository script a workflow runs
  isolated, from an unrelated directory, and requires its module body to import.
- Exceptions require a written reason and fail once they match nothing. The only
  entries are this repository's own release caller, which is generator output pinned
  to an earlier contract commit and is never hand-edited; its next canonical repin
  regenerates it isolated and retires the entries.

## Alternatives rejected

- **Classify each step first and isolate only checkout-writable ones.** The
  classification is not durable, and the cost of isolation is one flag.
- **Keep `PYTHONPATH` for the policy dependency and exempt that step.** An exemption
  would make the contract's single rule conditional; an explicit argument keeps the
  dependency directory visible and validated where it is used.
- **Hand-edit the pinned self release caller.** Generated callers are regenerated at
  one contract commit, never edited; the self-expiring exception records the gap.

## Consequences

- Reusable workflows pick the change up when each adopter next repins to a contract
  commit that contains it; generated release callers change bytes, so adopters
  regenerate the full generated set at that commit.
- A required workflow stored by an organization ruleset runs its stored revision until
  that rule is rotated.
- Shell indirection the scanner cannot see lexically (command substitution, `eval`,
  functions, aliases, interpreter paths computed inside Python) still needs review.

## Verification

`python3 scripts/ci-gate/workflow-isolated-python.test.py` passes on this tree and
fails when `-I` is removed from a non-Node workflow, from a generator-emitted step,
when a new non-isolated step or a shebang-started script is added, when a sibling
import loses its explicit directory, or when an exception no longer matches.
