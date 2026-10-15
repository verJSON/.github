---
date: 2026-10-15
issue: 1777
impact: patch
title: Record a provably absent generated caller instead of aborting capability-floor observation
---

Capability-floor discovery now lists each consumer's `.github/workflows` directory at the resolved source commit. An allowlisted caller missing from that successful listing becomes an `allowlisted-caller-absent` finding that names the repository as non-conforming, and discovery continues. It also matches the canonical `Verjson` owner case-insensitively, so callers generated under the `verJSON` display case are accepted. A 404 is never read as absence, and every API, access, or response failure still fails closed.

The scheduled observation had failed on every run since verjson-authn deliberately removed its local `gate-rearm.yml` (the organization ruleset injects the arm). The reviewed allowlist now names that repository's generated `ai-review-label-rearm.yml`, and its generator joins the deepseek-provider capability floor so the arm pin stays audited. [ADR 0233](../docs/decisions/0233-capability-floor-records-provably-absent-callers/README.md) supersedes ADR 0135's missing-file condition in part, and ADR 0135 records the owner-case amendment.
