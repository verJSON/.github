---
date: 2026-11-15
issue: 1843
impact: patch
title: Unwedge stale review bootstraps that share one pin across generated callers
---

A stale review-caller bootstrap can now move the generated gate re-arm, label re-arm, privileged merge, and promotion retry callers when each side matches the generator at that revision, and a contract test can swap only the stale SHA occurrences for the callers that moved. A consumer can update those five generated callers, leave node CI on the old pin, and still pass the mirrored contract test. Fixes #1843.

The embedded proof selects the gate and label template by ancestry so a newer template cannot be presented as an older revision's output. Privileged merge and promotion retry keep the required checks, workflow names, and runner labels from the protected base. The lane takes effect after organization ruleset 20722935 is rotated to this change. See ADR 0268.

The bootstrap subset-swap changes `gate-rearm.yml` arm job bytes, so the ADR 0235 reviewed digest for `gate-rearm.yml [workflow_call] arm` moves to `sha256:c4968da1…`.

Shallow actions-ci checkouts fetch historical generator revisions through
`shared_checkout_fetch ensure-commit` before `git show`. The embedded F-1R
guard template's `base_ref_path` site is allowlisted in the URI-encoding scan;
the live generator still emits a real `@uri` line the scan accepts.
