# Reusable-workflow versioning & release

How the org reusable workflows in `Verjson/.github/.github/workflows/*.yml`
(`helm-ci`, `pulumi-ci`, `ui-ci`, `node-ci`, `node-release`, `notify-umbrella`, …)
are versioned, how caller repos should pin them, and how a release is cut. This is
the operational reference for the decision recorded in
[ADR 0014](decisions/0014-reusable-workflow-versioning/README.md), updated by
[ADR 0219](decisions/0219-retire-moving-major-tags-for-contract-releases/README.md).

## TL;DR

- **Exact/reproducible:** choose a supported release, declare its
  `contract_version`, and pin its commit SHA, never `@main` or `@vX`.
  `uses: Verjson/.github/.github/workflows/helm-ci.yml@<release-commit-sha>`
- **Contract adopters:** declare `contract_version` and pin the release commit SHA.
  Review each update through a pull request.
- **Legacy aliases:** existing `@v2` and `@v3` tags stay at their current targets.
  New releases do not move them; migrate callers to release commits.
- Releases are dispatched with an explicit `vX.Y.Z` version. No release or tag
  movement follows a plain push to `main`.

## Why pin (not `@main`)

Every consumer repo references these workflows by a *mutable* ref. A push to `main`
here reaches **every caller at once** — the exact risk called out in
[ADR 0010](decisions/0010-platform-templates-consume-reusable-workflows/README.md)'s
Consequences ("a breaking change to a reusable can reach every template at once").
Pinning to a tag turns that org-wide blast radius into a per-repo, Renovate-driven,
reviewable bump.

An exact `vX.Y.Z` tag is the SemVer audit point. Contract adopters use its
commit SHA, declared as `contract_version`, so a caller cannot execute mutable
transitive code. Existing `vX` aliases are static legacy references.

## Versioning scheme

Semantic versioning of the `.github` repo as a whole (all reusables share one version
line — they ship together):

| Bump      | When                                                             | Example         |
| --------- | --------------------------------------------------------------- | --------------- |
| **major** | breaking change to any reusable's inputs/behavior/contract      | `v1.4.2 → v2.0.0` |
| **minor** | new reusable, or a **new optional input** (existing callers unaffected) | `v1.4.2 → v1.5.0` |
| **patch** | bug fix, internal refactor, runner/pin bump inside a reusable   | `v1.4.2 → v1.4.3` |

Release references:

- **`vX.Y.Z`** — immutable, one per release. The audit point.
- **`vX`** — existing aliases remain at their last target; no new release moves them.

Release commit SHA callers take updates through reviewed pull requests. Every
caller opts into a new major and a new supported release explicitly.

## How callers pin

```yaml
# .github/workflows/ci.yml in a consumer repo
jobs:
  helm:
    uses: Verjson/.github/.github/workflows/helm-ci.yml@<release-commit-sha> # declared contract_version
    with:
      release-name: my-chart
```

### Renovate's role

The org already runs Renovate (`config:recommended`, which enables the
`github-actions` manager). With callers on an exact release or digest it can
propose reviewed updates. Callers still using `@v2` or `@v3` must migrate to a
supported release SHA through a reviewed pull request; those aliases no longer
advance.

Caller edits are owned by each consumer repository; publishing a release here
does not rewrite their workflow files.

## Cutting a release

1. Land the change on `main` after review and green CI.
2. Choose the exact `vX.Y.Z` version for the repository's contract release.
3. Dispatch the pinned `.github/workflows/release.yml` with that version and the
   intended fragments. Its verification and publication jobs use the same
   dispatch commit. A concurrent change to `main` makes publication fail closed;
   re-dispatch from the new head after verification.
4. Read back the immutable release commit, `vX.Y.Z` tag, and changelog snapshot.
   Update consumers through reviewed PRs that declare `contract_version` and
   pin that release commit SHA.

Existing `vX` tags are retained at their current targets. The release process
neither creates nor moves them.
