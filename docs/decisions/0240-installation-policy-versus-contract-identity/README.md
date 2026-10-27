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

- Behavior for the built-in profile is held equal to the frozen legacy spelling over
  the full input matrix in `scripts/ci-gate/runner-lane-resolver.test.py`.
- The analyzer allowlist in `hosted-selector-policy.py` carries the new literals by
  hand because the actionlint job sparse-checks out only the analyzer, so it cannot
  import the catalog. The test holds the two to each other. Agreement between a
  generated file and its allowlist is consistency, not safety; the frozen legacy
  strings and the negative scenarios are the independent oracle.
- Rebinding the `public-pr-runner-isolation.pending.tsv` digests for `helm-ci.yml`,
  `actionlint.yml` and `release.yml` is mechanical: the latter two bind scanned-tree
  bytes that include `helm-ci.yml`. No pending row was added or widened.
- Inherited, deliberately not fixed here because it would change built-in behavior:
  `fromJSON` of a falsy `CI_LANE_UNTRUSTED` (`null`, `false`, `0`, `""`) is falsy, so
  the secretless term can fall through to a caller `runner`. Both Codex reviewers
  reproduced it on the pre-profile expression. Needs a separate decision (fail-closed
  validation of the variable, owned by `runner-admission-reconcile`).
- Not modeled: Actions compares strings case-insensitively; the test evaluator does
  not. Live cross-organization execution, and the documented rule that self-hosted
  reusable workflows are reachable by matching owner only, still need a canary outside
  the organization (ADR 0210 exit condition).

## 2026-10-23 amendment — fail-closed secretless runner selection

Issue #1869 resolves the inherited falsy-lane fall-through recorded above.
For a secretless invocation, the selector first uses `CI_LANE_UNTRUSTED`
only when eligibility succeeds, then selects literal `ubuntu-24.04` before any
caller-supplied runner tail. This handles missing, failed, skipped, or falsy
parsed values at `runs-on`; eligibility continues to validate the raw lane
before dependency acquisition.

The profile-aware `secretless-first` catalog shape remains prospective. The
`secretless-first-legacy-tail` migration shape adds the same gate and fallback
to node-ci while preserving its exact existing non-secretless selector tail.
The resolver evaluator now models case-insensitive Actions string comparisons
and JSON parsing/truthiness. Exact-site checks and independent mutations bind
the fallback to the node-ci build-test job and its generated twin.

Existing pending isolation entries were rebound for node-ci, its protected
twin, actionlint, and release verification closure; no row was added or widened.
A new organization variable was unnecessary because it would still be mutable
caller input and retain the same need for selector-level fallback. Eligibility
or acquisition failures continue to fail required build-test before checkout.
ADR 0234 receives the corresponding dated correction to the hosted-capacity
invariant.

## 2026-10-27 amendment — profile-aware Node CI selectors

The Node CI `eligibility` job now uses the catalog's `caller-runner` shape,
and `build-test` uses `secretless-first`. This completes the Node CI part of
rollout step 2: custom profiles use their strict untrusted lane without
inheriting `CI_LANE_FALLBACK`, while the built-in Verjson profile retains its
legacy routing behavior. The secretless eligibility-gated lane and literal
hosted fallback remain ahead of caller runner input. The protected workflow
twin is regenerated from `node-ci.yml`; exact-site tests bind both selectors
to their job markers in both files.
