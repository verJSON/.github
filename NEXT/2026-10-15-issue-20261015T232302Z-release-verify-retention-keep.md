---
date: 2026-10-15
id: 20261015T232302Z
impact: minor
title: Forward the package retention window to the release verification hook
---

Generated `release-node`, `release-artifact`, and `release-snapshot` callers now pass `CI_PACKAGE_RETENTION_KEEP` into the clean `env -i` environment of the release verification step, so a repository `scripts/release-verify.sh` sees the same retention window the post-publish retention job deletes against. Consumers pick it up by regenerating their adopter set at the new contract SHA; verjson-ci#298's retention-aware release hold is the first consumer.

The generated contract test pins the new verify-step environment member and the updated verification script digests per mode, and `scripts/ci-gate/release-verify-retention-keep.test.py` executes the generated step with an environment-printing hook. ADR 0220 records the refinement.
