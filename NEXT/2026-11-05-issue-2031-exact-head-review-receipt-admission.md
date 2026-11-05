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
skip it and could let an older receipt govern past a newer verdict. Gate R reports such
a failed read as exit `1`, like its other API failures, and the terminal merge still
refuses. A checkout-isolation
check now fails CI when a job reads a file its sparse `.gate-trust` checkout omits.
No ruleset, property, or required check changes. Native merges stay with #1990.
Callers that route by exit code need a row for `6`.

ADR 0257 is accepted by owner decision and makes independence identity-neutral: once
#1964 switches on, a receipt counts only when its reviewer's acting identity is distinct
from the head's author and pushers, whether either is a human or an agent. Admin-merge
follows the identity's role, not its class. It supersedes the parts of ADR 0202's
2026-10-22 amendment that distinguished the two. Until #1964 issues separate identities, agents
author and review through one account. Such a receipt still passes as the named,
time-boxed `shared-identity-review` exception, and Gate R prints a notice naming it when
the reviewer is the pull request's author. App-authored pull requests, such as
Renovate's, get no exemption.
