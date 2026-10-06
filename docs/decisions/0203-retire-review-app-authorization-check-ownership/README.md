# 0203 — Retire review-App authorization-check ownership

- **Date:** 2026-09-23
- **Status:** Proposed
- **Related:** [ADR 0199](../0199-authorization-checks-fail-without-review-app-credentials/README.md)
- **Issues:** [#1540](https://github.com/Verjson/.github/issues/1540)
- **Supersedes:** [ADR 0200](../0200-temporary-legacy-authorization-check-write/README.md)

## Context

[ADR 0200](../0200-temporary-legacy-authorization-check-write/README.md)
temporarily granted the AI review App `checks:write` so receipt-bound review
runs could finish authorization checks created before
[ADR 0199](../0199-authorization-checks-fail-without-review-app-credentials/README.md)
moved check creation to GitHub Actions.

New arms now create Actions-owned checks, but live-state review on 2026-09-23
found three legacy in-progress checks owned by App `4528902`
(`ai-review-authorization`). Two belong to a merged or closed pull request. The
third remains required by the open
[verjson-git-runners#221](https://github.com/Verjson/verjson-git-runners/pull/221),
where the source arm completed and the paired review dispatch failed. Removing
legacy terminalization before that check becomes terminal or is replaced would
strand the open adopter.

This decision becomes Accepted only after every legacy check is terminal and a
fresh organization-wide query proves that no open pull-request head depends on
a review-App-owned authorization check. Until then ADR 0200 remains effective,
and this implementation must not merge.

## Decision

After the prerequisite is satisfied, GitHub Actions App ID `15368`, with slug
`github-actions`, will be the sole producer and terminalizer of
`AI review authorization` checks. Every receipt must carry explicit
`check_app_id` and `check_app_slug` fields bound to that identity; receipts that
omit those fields or name the review App will be rejected.

The verifier, gate rearm and orphan recovery, zero-provider recovery,
completion and fallback finalizer, privileged merge, promotion retry, and
post-merge evidence readers will all require the Actions-owned identity. The
completion workflow will use its caller-owned `GITHUB_TOKEN` to finish the
exact receipt-bound check. The dedicated AI review App token will no longer
request `checks:write`; its ID and slug will remain authoritative only for
authenticating the approval author, and the token will retain the content and
pull-request permissions needed to persist and verify that approval.

Exact repository, pull request, head SHA, check ID, external ID, arm run, run
attempt, details URL, receipt digest, and review-policy bindings remain
unchanged. Unknown, omitted, or legacy check ownership fails closed and cannot
authorize, promote, or merge.

## Consequences

- The review App cannot create, update, or complete repository check runs after
  this decision is accepted and implemented.
- An old review-App-owned check or receipt cannot be replayed through current
  recovery, completion, promotion, retry, or post-merge paths.
- Current Actions-owned checks still reach terminal failure through the
  always-run fallback when review credentials or approval persistence fail.
- `AI_REVIEW_APP_ID` and `AI_REVIEW_APP_SLUG` remain required where a trusted
  workflow verifies the dedicated App's approval author; they no longer
  identify an acceptable authorization-check owner.

Focused mutation tests cover missing `check_app_*` fields, legacy review-App
ownership, token-permission regression, exact Actions ownership, same-head
retry, recovery, promotion, and post-merge evidence.

## Amendment 2026-09-27: Actions-owned checks do not keep `details_url`

Issue [#1648](https://github.com/Verjson/.github/issues/1648). From the first arm
after this decision landed (403a1da, 2026-09-27 02:02 UTC) every dispatched review
failed preflight with `authorization check is not receipt-bound`, and every
`AI review authorization` check in the fleet stayed `in_progress`. GitHub does not
honor the `details_url` a check run is created with when the creator is the
GitHub Actions App (15368): the arm passes the arm-run URL, and the stored check
reads `https://github.com/<repo>/runs/<check_id>` (hub check 108559180272 and
`verjson-compliance-schema` check 108560905178, both read back live). A review-App
owned check kept the value (hub check 108529790004, 01:54 UTC, the last success).

The binding predicates were written for the review-App owner and required the
check's `details_url` to equal the arm-run URL. They now bind the live check to its
own stored page (`$GITHUB_SERVER_URL/<repo>/runs/<check_id>`) and derive the arm run
from `external_id` (`ai-review:v1:<repo>:<pr>:<head>:<run_id>:<attempt>:<nonce>`),
which GitHub stores verbatim. The receipt artifact keeps the arm-run URL in its own
`details_url` field; that field is compared with the arm run named by `external_id`,
never with the check. Changed sites: `scripts/ci-gate/verify-arm-receipt.sh`, the
two re-verification steps in `ai-review-merge.yml`, orphan recovery, the recovery
marker, hold-removal re-promotion and the in-progress path in `gate-rearm.yml`, and
promotion-retry eligibility. Each suite first failed with the live error against a
stubbed Actions-owned check before the change and passes after it; the promotion
retry and receipt suites gained checks that an arm-run URL on an Actions-owned
check, another check's page, a missing `external_id`, or a foreign pull-request
identity are rejected.

Because the arm runs at the SHA stored in ruleset 20722935 and consumers pin
`ai-review-merge.yml`, `ai-privileged-merge.yml`, and `ai-promotion-retry.yml`, the
fix is live only after that ruleset is rotated to the merge commit and a contract
release repins the adopters.

## 2026-10-06 — Bind fallback finalization to the exact head

Issue [#1650](https://github.com/verJSON/.github/issues/1650) identified a gap in
the always-run finalizer: it checked that the head field in `external_id` was a
valid SHA, but did not compare it with the authorized head. The finalizer now
requires both the check run's `head_sha` and the head in `external_id` to equal
`EXPECTED_HEAD_SHA` before it can update the check. A behavioral regression test
shows each mismatched field independently blocks the mutation. This restores
the existing exact-head requirement without changing check ownership or the
recovery policy.
