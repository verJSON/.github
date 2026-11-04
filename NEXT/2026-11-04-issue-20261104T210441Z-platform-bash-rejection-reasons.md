---
date: 2026-11-04
id: 20261104T210441Z
refs: 1994
impact: patch
title: Platform Bash negative tests name the guard that must reject them
---

The negative tests in the `platform` CI group's Bash files now assert why they
were rejected: the `UNDETERMINED:` or `::error::` line, the validator's
`sys.exit` reason, or the generator's message, together with the exit status the
script defines. Before this, any nonzero exit passed, so a guard could stop working
while an earlier, unrelated check kept the test green. One such case was live: the
test that PR-supplied scopes cannot expand the trusted package policy was rejected by
the earlier approved-package scope check, never by the policy comparison. Its
fixture now reaches the policy guard, and a new case covers that guard's other half,
package approvals the policy does not list.

Guards in two scripts that rejected without a reason, or with one shared reason,
now give their own. Their accept/reject behavior is unchanged:

- `runner-admission-reconcile.sh`: an invalid larger-runner allowlist or hosted-runner
  inventory now names the check that failed, such as `an id is duplicated` or
  `paginated count mismatch`, after the existing message.
- `gen-renovate-compatibility-caller.sh`: a rejected package, candidate or stack
  profile now prints what was wrong, without echoing the rejected value.

`organization-neutral-ci-contract.test.sh` asserted two absences with a bare
`! grep` under `set -e`, which can never fail. They now fail when the reference is
present or the file cannot be read.

Part of #1994. The container contract files and the files #1996 is editing are left
to later legs.
