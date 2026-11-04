---
date: 2026-11-04
issue: 1994
impact: patch
title: Merge-gate negative tests name the guard that must reject them
---

Every negative test in the `merge-gate` CI group now asserts the reason it was
rejected for: an `::error::` line, an exception message, an extraction kind, or the
contract line that raised. Before this, a test passed on any nonzero exit, so a
guard could stop working while an earlier, unrelated check kept the test green.
This change covers each guard a test exists for and leaves the guards themselves
unchanged in what they accept.

Some guards rejected without saying why. They now print an `::error::` line, and
their accept/reject behavior is unchanged:

- `gate-rearm.yml`: the review-dispatch ref guard, a failed PR-metadata read, a
  re-promotion check that names no arm run or no valid arm run attempt, an unproven
  pending authorization, and a failed bootstrap check-completion request.
- `ai-review-merge.yml`: the promotion dispatch's identity and default-branch guards,
  and a DeepSeek pass whose live-head lookup fails.
- `ai-post-merge.yml`: the authorization-check guard and the review-run guard.
- `verify-arm-receipt.sh`: the receipt workflow-SHA guard. Its three arm-run
  provenance guards now say which part did not match: the run's identity, the
  required-workflow ruleset source, or the workflow ID.

`review-policy-envelope.py` no longer reports a duplicate field as an undecodable
envelope. In `event-driven-authorization.test.py`, the `for ((;;))` polling
mutation now reaches the loop detector it targets. It used to be caught first by the
endpoint count.

Part of #1994; `privileged-merge-pin.test.sh` is left to #1996.
