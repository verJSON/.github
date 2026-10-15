# 0234 — Route secretless trusted-ref builds to the hosted untrusted lane

- **Date:** 2026-10-15
- **Status:** Accepted
- **Category:** runner topology — **sensitive class**
- **Decided by:** organization owner, 2026-10-15: canonical `node-ci` routes the
  trusted-ref secretless jobs that need hosted capacity itself, instead of rejecting a
  self-hosted selector
- **Supersedes in part:** [ADR 0097](../0097-secretless-trusted-ref-node-validation/README.md)
  ("the trusted-ref build job follows the normal trusted runner route or an explicit
  caller runner") and the 2026-09-20 correction to
  [ADR 0146](../0146-restore-absent-secretless-compatibility-targets/README.md)
  (reject a trusted-ref compatibility call whose eligibility runner is not hosted)
- **Related:** [ADR 0033](../0033-self-hosted-runner-policy-by-visibility/README.md),
  [ADR 0040](../0040-runner-lanes-and-admission-axes/README.md),
  [ADR 0041](../0041-shared-admission-hosted-and-self-hosted/README.md),
  [ADR 0086](../0086-secretless-node-pr-validation/README.md),
  [ADR 0147](../0147-route-untrusted-ci-to-ephemeral-hosted-runners/README.md),
  [ADR 0209](../0209-hosted-tool-cache-trust-is-a-tree-property/README.md)

## Context

A `secretless-trusted-ref` call runs the same credentialless `build-test` pipeline as
`secretless-pr`: a Bubblewrap lifecycle rebuild, a Bubblewrap candidate script plan,
and optional compatibility lanes. Each of those exits unless
`RUNNER_ENVIRONMENT=github-hosted` (`secretless lifecycle rebuild requires a
GitHub-hosted runner`, `secretless candidate scripts require a GitHub-hosted Linux
runner`), and the sandbox is provisioned only on hosted runners. ADR 0086 and ADR 0147
already give `secretless-pr` precedence on `CI_LANE_UNTRUSTED`, which ADR 0147 pinned
to the ephemeral `["ubuntu-24.04"]`. The trusted-ref build did not get the same
precedence: ADR 0097 left it on the caller's `runner` input or the trusted lane.

In this organization both of those resolve to `["self-hosted","general"]`
(`CI_RUNNER_FASTLANE`, `CI_LANE_TRUSTED`, `CI_LANE_FALLBACK`), so push validation of
`main` was unsatisfiable by construction for every secretless adopter:

- verjson-cli passes `runner: vars.CI_RUNNER_FASTLANE || '["ubuntu-24.04"]'`, the only
  runner input the hosted-selector consumer policy admits (#1548). The fallback never
  applies, and ADR 0146's eligibility guard failed every push since 2026-09-23 (runs
  38747282020, 38810456465). The #1548 amendment to ADR 0147 assumed a GitHub-hosted
  fastlane that the organization does not configure.
- AiB passes no runner, falls to `CI_LANE_TRUSTED`, and fails later in `build-test`
  with `secretless lifecycle rebuild requires a GitHub-hosted runner` (run 38811810150,
  runner `gha-general-1`).

ADR 0146's guard tested the *eligibility* job's `runner.environment` as a proxy for
the *build* job's runner. It was a faithful proxy only while both jobs shared one route,
and it covered only compatibility ranges although every secretless build needs a
hosted runner.

## Decision

1. **`build-test` routes either secretless mode to the hosted untrusted lane.** Its
   selector becomes
   `(inputs.secretless-pr || inputs.secretless-trusted-ref) && fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') || <unchanged ADR 0033 tiers>`.
   The secretless term precedes the caller's `runner` input, so a self-hosted runner
   input or trusted lane cannot place the credentialless build on persistent capacity.
   The same expression reaches `node-ci-protected.yml` through
   `scripts/gen-node-ci-protected.py`.
2. **Only that job moves.** `eligibility` keeps the caller's route, the secretless
   acquisition job keeps its existing `CI_LANE_UNTRUSTED` route, and non-secretless
   calls are unchanged. Hosted spend grows by exactly the trusted-ref build job of
   adopters that opt into secretless trusted-ref validation, which today cannot
   succeed anywhere else.
3. **Eligibility refuses early when that lane is not hosted.** ADR 0146's
   `runner.environment` guard is replaced by `Require a GitHub-hosted secretless build
   lane`, which runs for either secretless mode and reads the same
   `vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]'` text `build-test` resolves. It admits
   only a single standard GitHub-hosted Linux image label (`ubuntu-latest` or
   `ubuntu-NN.NN`, as an exact JSON string or one-element array with no whitespace,
   matching the exact-bytes rule `runner-admission-reconcile.sh` applies under ADR
   0147) and fails before dependency acquisition for anything else: a self-hosted,
   multi-label, non-Linux, empty, multi-line, or malformed selector. It matches under
   `LC_ALL=C`, because bash bracket ranges follow the runner's collation and under
   `en_US.UTF-8` `[0-9]` also matches fullwidth digits. It never echoes the variable's
   value. The runtime
   `RUNNER_ENVIRONMENT` checks inside `build-test` remain the second, authoritative
   layer.

### Why `CI_LANE_UNTRUSTED` rather than another hosted selector

- `CI_LANE_UNTRUSTED` is the lane ADR 0147 defines as ephemeral GitHub-hosted and that
  `runner-admission-reconcile.sh` holds at exactly `["ubuntu-24.04"]`. The credentialless
  build executes repository and dependency lifecycle code, which is the workload that
  lane isolates, and the secretless acquisition job already runs there in both modes,
  so the whole secretless pipeline shares one placement and one reconciled variable.
- `CI_RUNNER_ISOLATED` is an ADR 0147 alias of the same lane; naming it would add a
  second variable to keep in step without adding isolation.
- `CI_LANE_PRIVILEGED` is the credential-bearing lane. The build job deliberately holds
  no credential, so placing it there would blur the boundary ADR 0086 created.
- A literal `ubuntu-24.04` would need a new exact-site inventory exception in
  `runner-routing-policy.test.sh` and would ignore a provider move that ADR 0040 makes a
  variable flip.

The trade-off is that `CI_LANE_UNTRUSTED` is a variable: ADR 0147's rollback path
restores it to `["self-hosted","general"]`. Under this decision such a rollback makes
every secretless call fail in eligibility, before acquisition, instead of placing the
credentialless build on persistent capacity or failing it late. That is the intended
fail-closed behavior; a rollback now needs a reviewed alternative for secretless
adopters rather than silently changing their placement.

## Consequences

- verjson-cli and AiB push validation becomes satisfiable once each regenerates its
  caller at a contract SHA containing this decision. verjson-cli is the first adopter;
  its `runner` input continues to route `eligibility` only.
- `secretless-pr` gains the eligibility hosted-lane check as well. Its placement is
  unchanged.
- The hosted-selector policy's reviewed expression for `build-test` is replaced, not
  supplemented: the previous `inputs.secretless-pr && …` shape no longer occurs in any
  canonical workflow.
- `scripts/ci-gate/node-ci-secretless-hosted-lane.test.py` executes the eligibility
  guard steps of both generated workflows against stubbed inputs and variables, and
  `scripts/ci-gate/runner-routing-policy.test.sh` evaluates the `build-test` and
  `eligibility` selectors for both secretless modes.

## Rollout

No organization variable or ruleset changes. Adopters pick the routing up by
regenerating or repinning their `node-ci.yml` caller to a contract SHA that contains
this decision; verjson-cli first, then AiB and any other `secretless-trusted-ref`
adopter. Acceptance is a green push run on `main` whose `ci / build-test` job reports a
GitHub-hosted image while `ci / eligibility` still reports the caller's self-hosted
runner.

## Rollback

Revert the routing and guard together. Reverting only the routing restores the
unsatisfiable state; reverting only the guard leaves a hosted-lane misconfiguration to
fail after acquisition.
