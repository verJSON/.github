---
date: 2026-10-21
issue: 1861
impact: minor
title: Verify trusted checks produced by organization ruleset-required workflows
---

Terminal privileged promotion now accepts a trusted check produced by an organization
ruleset-required workflow, declared with `"workflow_repository":"Verjson/.github"`, only
when the live organization ruleset pins exactly one revision of its path that is
reachable from `main`, GitHub reports that the run executed exactly that file at that
revision, and the check is on the exact authorized head. This makes AI terminal merge
satisfiable for `verjson-cli-projects`, whose PR CI is an organization required workflow.

The generator, the gate, and fleet conformance accept the same closed fifth key; the
consumer path now also binds each run's `workflow_url` and each check's `head_sha`, so a
required-workflow run cannot satisfy a consumer declaration or the reverse. Refusals are
fail-closed for absent, ref-only, ambiguous, repository-level, unreachable, malformed,
or unreadable provenance. `verjson-cli-projects` must regenerate both privileged callers
at the merge SHA to rebind `ci / build-test`, `ci / eligibility`, and `admission`
(ADR 0081 2026-10-21 correction).
