---
date: 2026-11-05
id: 20261105T002034Z
refs: 1994
impact: patch
title: The changelog caller contract's negative tests name the guard that must reject them
---

The negative tests in `scripts/ci-gate/changelog-caller-contract.test.sh` now assert
why they were rejected, together with the exit status the script defines. Before this,
any nonzero exit passed, so a guard could stop working while an earlier, unrelated check
kept its test green.

- Generator refusals assert exit 2 and the generator's own
  `gen-changelog-caller.sh: <reason>` line.
- Emitted-suite cases assert exit 1 and the invariant's reason in the failure report,
  from the first `FAIL` line on, so an `ok` line cannot satisfy it.
- The release version guard asserts exit 1 and its `::error::` line.
- The renderer's argument refusal asserts exit 2 and its own line.
- The generated ADR-index suite asserts the two calendar-date cases that the
  impossible-date generator breaks.
- The generator's injected-failure cases assert the injected function's stderr
  sentinel, a nonzero exit, and empty output.

With its named check removed, ten emitted-suite cases were still rejected by another
check, so the old test stayed green. Examples are scope drift, an omitted package stamp,
a retired workflow, a matrixed job, an approved-OS matrix, and a deleted private-package
refusal. Every existing assertion label is kept.

`gen-changelog-caller.sh` now says when `--scope`, `--node-version`, `--default-prefix`
or `--default-component` was repeated, before the shared usage text. Its accept/reject
behavior and its generated output are unchanged.

Part of #1994.
