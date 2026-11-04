---
date: 2026-11-04
id: 20261104T194358Z
refs: 1994
impact: patch
title: Platform Python negative tests name the guard that must reject them
---

The negative tests in the `platform` CI group's Python files now assert why they
were rejected: the exception message or the exact exception the test injected,
the `::error::` line, the argparse error, or the `jq -e` verdict. Where a guard
rejects silently and lives in a generated or required workflow, the test reads the
guard that stopped the script from its `bash -x` trace instead. The bare
`AcquisitionError` of the compatibility-sandbox acquirer carries no message. Where a
second guard would also reject a case, its test names the guard condition whose
`raise` fired. Before this, any nonzero exit or
any exception of the right type passed, so a guard could stop working while an
earlier, unrelated check kept the test green.

Two guards that rejected without a reason now give one. Their accept/reject
behavior is unchanged:

- `app-key-environment.yml` and its inline copy in `gate-rearm.yml`: the main-only
  App key admission preflight now prints an `::error::` line naming which check
  failed. These are the role, the environment name, the ref, the default branch, an
  unreadable environment or branch-policy record, and a policy that is not main-only.
- `app-key-environment-audit.py`: an environment whose name or ID is wrong no longer
  shares the branch-admission message. It reports
  `<environment> environment identity is unavailable`.

Part of #1994. The container contract files and the files #1996 is editing are left
to later legs.
