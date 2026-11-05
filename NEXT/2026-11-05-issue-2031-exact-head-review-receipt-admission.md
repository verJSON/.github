---
date: 2026-11-05
issue: 2031
impact: minor
title: Refuse autonomous merges without the exact-head independent-review receipt
---

`scripts/assert-mergeable-head.sh` has a new Gate R (ADR 0257, #2031). On a
promotion-enabled repository, meaning one whose base ref carries the organization's
required `gate-rearm.yml` workflows rule, the assertion now exits `6` unless the head
carries a valid ADR 0202 independent-review receipt for that exact head. An autonomous
`--admin` or `--auto` merge there therefore needs the receipt, as the terminal merge App
already did.

The receipt check moves from `ai-privileged-merge.yml` into
`scripts/ci-gate/independent-review-receipt.sh`, and both paths run that one file. An
unreadable review listing is now refused with a named reason. A reviewer whose
repository permission cannot be read is also refused by name, where the search used to
skip it and could let an older receipt govern past a newer verdict. A checkout-isolation
check now fails CI when a job reads a file its sparse `.gate-trust` checkout omits.
No ruleset, property, or required check changes. Native merges stay with #1990.
Callers that route by exit code need a row for `6`.
