# 0213 — Package type-surface rulesets use the release App only

- **Date:** 2026-10-01
- **Status:** Accepted
- **Issue:** [#1673](https://github.com/Verjson/.github/issues/1673)
- **Category:** rulesets, release authorization, package publication — sensitive class

## Context

The canonical node release workflow publishes an immutable changelog snapshot
and tag with one atomic push to the default branch. A type-surface ruleset is
still required because package exports are a compatibility contract. GitHub
cannot restrict a ruleset bypass to only changelog paths, so each package
type-surface ruleset must have one deliberate exception: the repository-scoped
`release-authorization` App (`4583107`).

The fleet was inconsistent. Identity-contracts allowed an organization
administrator and the merge App in addition to the release App; lifecycle had
no release bypass; authn had already adopted the release App; authz ran the
type-surface check without an enforcing ruleset.

## Decision

The organization-owned `config/package-type-surface-rulesets.json` is the
reviewed source of truth for managed package rulesets.
`scripts/package-type-surface-ruleset.py` renders and audits live images,
refuses unexpected preimages, requires the explicit
`APPLY-PACKAGE-TYPE-SURFACE-RELEASE-1673` acknowledgement for mutation, and
verifies the complete postimage. It never reads or prints a private key.

Every managed package type-surface ruleset has exactly this bypass actor:

```json
[{"actor_type":"Integration","actor_id":4583107,"bypass_mode":"always"}]
```

No organization administrator, merge App, Renovate App, or other integration
is a type-surface bypass. The private key remains confined to the canonical
snapshot job. The existing release-App canary remains the operational proof
that the pinned App token can perform the atomic release operation; the nightly
organization conformance workflow audits organization and package rulesets.

## Rollout and rollback

For each package, read the live ruleset and compare the exact reviewed
preimage, re-read immediately before mutation, write only the rendered desired
image, and read back the complete postimage. A missing authz ruleset is created
disabled, verified, activated, and verified again. The canary must pass before
the release is dispatched.

If a postimage or canary check fails, stop and preserve the live state for
inspection. Rollback restores the reviewed preimage through the same explicit
acknowledgement path; it never blindly overwrites a changed ruleset.

## 2026-10-15 amendment: ruleset sources compare as GitHub identities

**Status:** Accepted; [#1779](https://github.com/Verjson/.github/issues/1779).

The nightly package audit has failed on every run since 2026-10-02 with `source drifted`
for all four managed rulesets. That day the organization login changed its display case
from `Verjson` to `verJSON`. The rulesets API now reports
`source: verJSON` and `source: verJSON/<repository>`, while the reviewed policy records
`Verjson`. No ruleset moved. Run 38582820391 on 2026-10-14 shows the failure; the same
audit passes against the live organization with this change.

GitHub owner and repository names are case-insensitive identities, and no two can differ
only by case. Every audit that compares an organization or `owner/name` reported by
GitHub with a reviewed value now uses one shared helper, `scripts/github_identity.py`.
It case-folds the whole `owner` or `owner/name` string, so a different owner or a
different repository still fails closed. Case folding is sound only over ASCII:
U+212A KELVIN SIGN folds to `k` and U+017F LATIN SMALL LETTER LONG S folds to `s`. Both
the observed and the reviewed value must therefore use GitHub's name charset
`[A-Za-z0-9._-]`; a non-ASCII spelling is an invalid identity, never a match. ADR 0206's
2026-10-14 amendment applied the same rule to the authorization-arm cohort; that audit
keeps its own copy until its owning change adopts the helper.

The helper covers the ruleset `source` checks in this audit, in the Authn audit
(ADR 0151), and in the CLI projects audit (ADR 0153). It also covers the required-workflow
receipt URLs those two audits read: `workflow_url` and the admission check's
`details_url` embed the consumer repository. There only the repository segment compares as
an identity; the scheme, host, path, and run identifiers stay exact. The organization
secret-scope audit (ADR 0088) compares selected-repository grants the same way.

Reviewed literals remain byte-exact where both sides are authored in this repository: the
contract pins of `organization` and consumer `repository`, generated workflow text, and
`if: github.repository == '...'` guards. The Actions expression engine compares those
guards case-insensitively, so they keep working. The required workflow at
`verJSON/verjson-authn` run 38612624131 (2026-10-14) evaluated its
`github.repository == 'Verjson/verjson-authn'` guard as true. Its `eligibility` job then
ran and succeeded.
