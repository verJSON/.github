---
date: 2026-10-15
id: f5f026ef
impact: patch
title: Route secretless trusted-ref builds to the hosted untrusted lane
---

Canonical `node-ci.yml` (and the generated `node-ci-protected.yml`) now routes the `build-test` job of a `secretless-trusted-ref` call to `CI_LANE_UNTRUSTED`, the same ephemeral GitHub-hosted lane secretless pull-request builds already use, ahead of a caller's `runner` input. Every other job, including `eligibility`, keeps the caller's route, so push validation pays for hosted capacity only in the one job whose lifecycle rebuild and candidate sandbox refuse to run elsewhere.

Before this, a trusted-ref build followed the caller's runner or the trusted lane. With the organization's `CI_RUNNER_FASTLANE` and `CI_LANE_TRUSTED` both selecting `["self-hosted","general"]`, every adopter's push validation on `main` was unsatisfiable: verjson-cli failed the eligibility guard (runs 38747282020, 38810456465) and AiB failed `secretless lifecycle rebuild requires a GitHub-hosted runner` (run 38811810150). ADR 0146's eligibility guard, which read the eligibility job's own runner as a proxy for the build's, is replaced by `Require a GitHub-hosted secretless build lane`: in either secretless mode it fails before dependency acquisition unless `CI_LANE_UNTRUSTED` selects exactly one standard GitHub-hosted Linux image, matched under `LC_ALL=C` and never echoed. Adopters pick this up by regenerating their caller; verjson-cli is first. See [ADR 0234](../docs/decisions/0234-route-secretless-trusted-ref-builds-to-the-hosted-lane/README.md).

The hosted-selector policy allowlist, the routing-policy evaluator, the generated pre-ADR-0178 counterexample, and the `node-ci.yml` identity digest move with the expression.
