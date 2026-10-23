# 0240 — Installation policy versus contract identity

- **Date:** 2026-10-22
- **Status:** Accepted
- **Issue:** [Verjson/.github#1869](https://github.com/Verjson/.github/issues/1869) (ARB change 2)
- **Category:** runner topology / trust profile — **sensitive class** (runner admission)
- **Related:** [ADR 0033](../0033-self-hosted-runner-policy-by-visibility/README.md),
  [ADR 0191](../0191-contract-releases-are-the-unit-of-adoption/README.md),
  [ADR 0234](../0234-route-secretless-trusted-ref-builds-to-the-hosted-lane/README.md),
  [ADR 0238](../0238-node-ci-cost-levers/README.md),
  [ADR 0239](../0239-unified-untrusted-execution-boundary/README.md)

## Context

The 2026-10-22 ARB found about 64 `github.repository_owner (!=|==) 'Verjson'` literals
in `runs-on` and `runner` expressions, in roughly a dozen spellings pasted across
`node-ci`, `helm-ci`, `pulumi-ci`, `ui-ci`, `ai-review-merge` and the container and
release workflows (`release.yml` spells it `'verJSON'`). The `@verjson` npm scope,
fixed registries, and the checkout of `Verjson/.github` as the contract repository are
the same kind of fact. An organization outside Verjson, or a fork of the contract
repository, cannot use its own lanes without editing the engine.

Two different things were fused:

- **Contract identity.** `Verjson/.github` as the contract source, pinned immutable
  SHAs, GitHub App ids, and the repository id in `verify-arm-receipt.sh`. These name
  who publishes and attests the contract. They are legitimately fixed.
- **Installation policy.** Which owner is "ours", which lanes exist, which npm scope
  and registry apply. These belong to whoever installs the contract.

## Decision

1. **The built-in `Verjson` profile is the default of an optional installation
   profile.** One organization or repository variable, `CI_PROFILE_OWNER`, names the
   owner whose lane variables apply. Unset or empty means `Verjson`, so every existing
   adopter resolves exactly as before. A foreign owner (`repository_owner` differing
   from the profile) is hosted, as today.
2. **Selector shapes are spelled once**, in `scripts/runner_lane_catalog.py`.
   `runs-on` cannot call an action, and a resolver job would add a job and latency to
   every workflow and defeat the static analyzers, so the catalog is a generator input:
   `scripts/gen-runner-lane.py` stamps each `# runner-lane: <shape>` marked `runs-on:`
   line and `--check` (run by `runner-lane-resolver.test.py` in the `platform` group)
   fails on any hand edit or stale line.
3. **Profile membership is not trust.** Adversarial review by two Codex models
   (`gpt-6-astra`, `gpt-6.1-sol`) reproduced that matching the profile and then reading
   `CI_LANE_FALLBACK` moves a public pull request's code from the hosted lane onto a
   persistent one in an adopter that sets a self-hosted fallback and no untrusted lane.
   The built-in profile keeps the legacy tail (`CI_LANE_UNTRUSTED || CI_LANE_FALLBACK`)
   because Verjson reconciles its untrusted lane as ephemeral (ADR 0147). Any other
   profile gets `CI_LANE_UNTRUSTED || ubuntu-24.04` and never the fallback. The
   guarantee covers the lane variables only: an explicit caller `runner` input still
   wins first in `caller-runner` (ADR 0033), so a caller that passes a persistent
   selector gets it. That input is caller policy, not a profile default.
4. **The secretless term still precedes the caller's `runner` input** (ADR 0234, 0238)
   in every shape that has one. The catalog's `secretless-first` shape holds it.
5. **Variable semantics.** A reusable workflow reads the caller's `vars`, so a fork of
   the contract repo does not change an external caller's profile. Anyone who can set
   `CI_LANE_*` can set `CI_PROFILE_OWNER`; a pull-request author from outside the
   repository cannot. Mismatch, empty, or missing values fail toward hosted.
6. **Contract-repo `uses:` references stay generated literals.** Workflow syntax
   forbids expressions in `uses:`, so a contract fork is adopted by regenerating the
   full adopter set at the fork's SHA (ADR 0191), never by a variable.

## Rollout (each independently mergeable)

1. Catalog, generator, check, analyzer allowlist, behavioral matrix, applied to
   `helm-ci.yml` (this change; `node-ci` is left for the concurrency and test-level
   change that follows).
2. Roll the shapes across the remaining workflows, each with its protected twin and
   regenerated digests.
3. Profile variables for npm scope, registry and contract repository where they are
   adopter policy, defaulting to today's values.
4. Generator support for adopter callers and a conformance golden test.

## Consequences

- The built-in caller-runner selector remains equivalent to its frozen legacy
  spelling over the input matrix in `scripts/ci-gate/runner-lane-resolver.test.py`.
  The profile-aware `secretless-first` shape keeps its profile tail. The
  `secretless-first-legacy-tail` migration shape wired to node-ci is tested for
  its eligibility gate, hosted fallback, and unchanged non-secretless tail.
- `hosted-selector-policy.py` keeps exact expression literals because
  actionlint sparse-checks out the analyzer and cannot import the catalog. The
  resolver test checks catalog-to-allowlist consistency; agreement proves
  consistency, not safety. Frozen legacy strings and negative scenarios remain
  an independent behavior oracle.
- The 2026-10-22 rebinds of the `helm-ci.yml`, `actionlint.yml`, and
  `release.yml` `verify` digests were mechanical: the latter two bind
  scanned-tree bytes that include `helm-ci.yml`. The #1869 amendment rebinds
  existing node-ci, protected node-ci, actionlint, and release verify rows for
  changed workflow or scanned-tree bytes. No pending row was added or widened.
- Inherited, deliberately not fixed in the 2026-10-22 change because that
  change preserved built-in behavior: a falsy parsed `CI_LANE_UNTRUSTED`
  (`null`, `false`, `0`, or `""`) could make a secretless term fall through to
  caller `runner`. Both Codex reviewers reproduced this on the pre-profile
  expression. This needed a separate decision about fail-closed validation
  owned by `runner-admission-reconcile`; the 2026-10-23 amendment below records
  the selected selector-level repair and why it does not add a new variable.
- Previously not modeled: Actions compares strings case-insensitively while
  the resolver evaluator did not. The evaluator now models case-insensitive
  string comparison and JSON parsing/truthiness. A live cross-organization
  execution still needs the canary described by ADR 0210's exit condition; the
  documented rule that self-hosted reusable workflows require a matching owner
  remains.

## 2026-10-23 amendment — fail-closed secretless runner selection

The 2026-10-22 Consequences entry's secretless-first-legacy-tail reference
describes the migration shape introduced here for node-ci; it preserves
node-ci's existing non-secretless selector tail. Decision 4's profile-aware
secretless-first shape is prospective and has no current workflow caller.

Issue #1869 identified that fromJSON of false, zero, null, or an empty JSON
string is falsy in a runs-on expression. Without a following secretless-only
branch, the expression could continue to caller-provided runner.

The profile-aware secretless-first shape uses the untrusted lane only when
either secretless input is true and needs.eligibility.result equals success.
A second secretless-only branch returns literal ubuntu-24.04 before its
profile-aware tail. The secretless-first-legacy-tail shape applies the same two
branches before node-ci's exact existing non-secretless tail; the remaining
profile-aware node-ci rollout is tracked by the original decision. Both shapes
keep the secretless selector ahead of caller-provided runner.

A new organization variable would still be mutable, string-valued caller
configuration whose parsed value can be falsy or malformed. It would add a
validation surface without removing the need for a fail-closed selector.
Keeping the fallback at the runs-on boundary handles those values while the
existing eligibility job remains responsible for validating the raw lane.
The repair therefore adds no new variable.

The selector-level repair does not move validation out of
runner-admission-reconcile: successful eligibility still requires the
configured lane to be a single standard hosted image. The existing eligibility
job validates the raw lane before dependency acquisition. build-test remains
always() and retains its dependency-failure guard before checkout, so
eligibility or acquisition failure is reported as a failed required check. The
complete non-secretless tail remains byte-for-byte unchanged. ADR 0234 receives
the same dated correction to its hosted-capacity invariant.
