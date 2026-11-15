---
date: 2026-11-15
issue: 1861
impact: minor
title: Verify trusted checks produced by organization ruleset-required workflows
---

Terminal privileged promotion now accepts a trusted check produced by an organization
ruleset-required workflow, declared with `"workflow_repository":"Verjson/.github"`, only
when the live organization ruleset pins exactly one revision of its path that is
reachable from `main`, GitHub reports that the run executed exactly that file at that
revision, and the check is on the exact authorized head. This makes the
`verjson-cli-projects` checks `ci / build-test`, `ci / eligibility`, and `admission`
expressible as trusted checks instead of the consumer `ci.yml` binding that never
reports on a pull-request head.

The generator, the gate, and fleet conformance accept the same closed fifth key; the
consumer path now also binds each run's `workflow_url` and each check's `head_sha`, so a
required-workflow run cannot satisfy a consumer declaration or the reverse. Refusals are
fail-closed for absent, ref-only, ambiguous, repository-level, unreachable, malformed,
or unreadable provenance, including an evaluate-only ruleset. `verjson-cli-projects`
regenerates both privileged callers at this merge SHA to rebind those three contexts
and keep `changelog / validate` and `changelog-contract` (ADR 0081, 2026-11-15 correction).

Full-checkout bytes seen by `release.yml` verify and `capability-floor-observe`
change with the privileged-merge provenance helpers, so their ADR 0235 pending
backlog digests are refreshed.

Required-workflow provenance helpers change the settable-guard inventory; `GUARD_COUNT` moves to 566.
