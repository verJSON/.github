---
date: 2026-11-04
id: 20261104T225627Z
refs: 1995
impact: patch
title: Reach the unreached guards in the generated container release contract
---
In the contract test that `gen-container-release.sh` generates, the reconciliation-allowlist guard, the hook's symlink check, the check that each allowlisted target exists, and the six pinned-helper digest checks are now reached. Deleting any of them left every test green before. The allowlist guard sits behind the caller's whole-file digest pin, so its fixtures re-pin that digest to the mutated caller, as regenerating at a contract that emitted that caller would. One widens the allowlist and one re-indents it, so the guard must still match the whole line. Each fixture asserts its guard's own message.
