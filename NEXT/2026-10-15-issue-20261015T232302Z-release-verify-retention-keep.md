---
date: 2026-10-15
id: 20261015T232302Z
impact: minor
title: Forward the package retention window to the release verification hook
---

Generated `release-node`, `release-artifact`, and `release-snapshot` callers now pass `CI_PACKAGE_RETENTION_KEEP` into the clean `env -i` environment of the release verification step, so a repository `scripts/release-verify.sh` sees the same retention window the post-publish retention job deletes against. Consumers pick it up by regenerating their adopter set at the new contract SHA; verjson-ci#298's retention-aware release hold is the first consumer.

The generated contract test pins the new verify-step environment member and the updated verification script digests per mode, and `scripts/ci-gate/release-verify-retention-keep.test.py` executes the generated step with an environment-printing hook. ADR 0220 records the refinement.

The retention guards in `node-release.yml` and `container-release.yml` now accept only 1 to 999 in ASCII digits (`^[123456789][0123456789]{0,2}$`). The previous `[1-9]` range follows locale collation and admitted U+0663 under `en_US.UTF-8`, and it had no length cap. A drift test binds those guards and the generated verify step to one expression and one guard. Every other numeric guard on an organization variable or dispatch input received the same fix. That includes the AI review budgets, which awk had accepted as strings, and gate-rearm's half takes effect once ruleset 20722935 is rotated.

Every shell guard in the workflows, generators, and generated callers now spells out its ASCII character classes. Letter ranges admitted `é` and fullwidth letters under `en_US.UTF-8`, as digit ranges admitted `٣`. A window outside 1-999 now fails `verify` and node-release retention. Container retention stays `continue-on-error`, and both retention jobs now record the skip in the run summary.

Retention is now an explicit policy value. `CI_PACKAGE_RETENTION_KEEP` accepts an integer from 1 to 999, or the exact lowercase word `all`, which disables retention. Every site parses the same language: the generated verify step, both retention jobs, and `package_retention.py --keep`. With `all`, nothing is deleted and the run summary says so. Any other value fails closed with an error that names the accepted forms.

The en_US.UTF-8 checks for these guards fail closed in CI when that locale cannot be reproduced, so actions-ci runs them on the hosted `ubuntu-24.04` image in `hosted-compatibility-tests`. Persistent fleet images lack the locale source data. The manifest contract derives that group's membership from the importers: every tracked file under `scripts/` that imports `ascii_digit_locales`, in any spelling, must be exactly the set of scripts the `locale-collation` rows run, and a reference it cannot classify fails closed. A ci-gate script named only in a YAML comment, or in a shell comment inside a `run` block, no longer counts as reachable in Actions.
