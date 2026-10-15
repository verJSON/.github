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

## 2026-10-15 amendment: workflow shell steps compare runtime repository identities

**Status:** Accepted; part of [#1794](https://github.com/Verjson/.github/issues/1794).

The amendment above left one class uncovered. Shell steps compared a repository that the
run reports at runtime with a reviewed literal using byte-exact `[ ... = ... ]`. The
Actions expression engine does not apply to these comparisons. Two live failures came from
this class:

- `ai-privileged-merge.yml`'s "Authorize terminal merge from trusted metadata" step
  compared `job.workflow_repository` to `Verjson/.github`. When the step is reached
  through `ai-promotion-retry.yml`'s local `./.github/workflows/ai-privileged-merge.yml`
  call, the job context reports `verJSON/.github`. Verjson/AiB run 38790031475
  (PR #341, head `51e5add9`, both callers at `9f8d55c5`) failed there with `promotion is
  not executing from the canonical workflow repository`. The direct privileged merge had
  deferred on a pending `ci / build-test`, so the retry path could not promote any adopter
  pull request whose CI outlasted that merge.
- The generated CLI projects admission step (`scripts/gen-node-required-workflow.py`)
  compared both `github.repository` and the pull request's `head.repo.full_name` to
  `Verjson/verjson-cli-projects`. Both report `verJSON/verjson-cli-projects`, so every
  admission since the rename failed, as in run 38432170170 on 2026-10-13.

Both sites now compare the runtime value as an identity under this ADR's semantics: exact
equality after ASCII case folding. They use `jq`'s `ascii_downcase`, which leaves
non-ASCII characters untouched. A spelling that GitHub's name charset cannot express can
therefore never equal the ASCII literal. Bash's `${value,,}` is not equivalent. Under the
runner's `C.UTF-8` locale it folds U+0130 LATIN CAPITAL LETTER I WITH DOT ABOVE to `i`,
and Python's `casefold()` folds U+017F to `s`. The tests run under `C.UTF-8` and reject
both spellings, plus other owners, other repositories, prefix and regex-wildcard
spellings, and untrimmed values.

The reviewed literals remain byte-exact in the source, and every other comparison stays
exact. Comparisons where both operands come from the same run's context, such as
`gate-rearm.yml`'s `github.workflow_ref` against `github.repository`, are unaffected.
The one-time PR #769 migration record in `ai-review-merge.yml` matches a closed pull
request and is now unreachable. Neither changes.
