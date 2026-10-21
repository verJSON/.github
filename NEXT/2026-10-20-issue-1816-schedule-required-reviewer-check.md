---
date: 2026-10-20
issue: 1816
impact: patch
title: Run the required-reviewer ruleset check in the daily conformance audit
---
The daily `org-ruleset-conformance.yml` audit now runs `required-reviewer-ruleset.py check`, so a deleted, edited, duplicated or undeclared `*-sensitive-path-admin-review` ruleset fails the next scheduled run instead of staying invisible until someone runs `check` by hand (ADR 0210 rollout step 4).

The step reuses the sibling audits' `ORG_ADMIN_TOKEN` binding and runs only the read-only `check` mode. It runs even when an earlier audit in the job failed, so another finding cannot mask it. `scripts/required_reviewer_ruleset_test.py` executes the workflow's own step against a stubbed `gh` for conformant, deleted, drifted, undeclared and API-error states. `scripts/ci-gate/privileged-scheduled-workflows.test.py` rejects `continue-on-error`, `|| true`, a mutating mode, an extra credential, a dropped step and a dropped `!cancelled()` guard. ADR 0210 records the amendment; #1816 stays open for the remaining rehearsal evidence.
