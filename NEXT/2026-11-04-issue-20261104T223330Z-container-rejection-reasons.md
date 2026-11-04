---
date: 2026-11-04
id: 20261104T223330Z
refs: 1994
impact: patch
title: Container contract negative tests name the assertion that must reject them
---
The container release contract suite now checks why each negative case is rejected, by the generated contract test's own failure line. Before this, any nonzero exit or any exception passed, so a guard could stop working while an earlier, unrelated check kept its test green.

- The contract test that `scripts/gen-container-release.sh` generates had bare `! grep` absence checks. `set -e` exempts a negated command, so those checks could never fail, and only the caller's digest pin refused the drift they named. The generated test now runs them through an `assert_absent` helper, which also fails when grep cannot read its input. It reports every failure with a `container-release contract FAILED:` line: digest drift names the file and both digests, and any other failing assertion names its line and command. Accept and reject behavior is otherwise unchanged. Adopters pick this up when they regenerate at a contract that includes it.
- The release suite's caller-mutation and allowlist cases re-pin the caller digest to the mutant, so each reaches its own assertion. The package-permission and appended-inheritance mutants have no assertion besides the digest pin, so they stay pinned and assert that pin.
