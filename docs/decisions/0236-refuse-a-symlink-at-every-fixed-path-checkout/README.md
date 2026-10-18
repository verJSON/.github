# 0236 — Refuse a symlink at every fixed-path checkout

- **Date:** 2026-10-18
- **Status:** Accepted
- **Category:** merge-gate behavior and runner state — **sensitive class**

## Context

[ADR 0220](../0220-confine-release-credentials-to-dependency-acquisition/README.md)'s 2026-10-18 amendment (#1847) found that `actions/checkout` follows a symlink at its `path:`. At the pinned v7 (`3d3c42e5`), `prepareExistingDirectory` lists the directory with `readdir`, which follows a link at the path itself, and removes every entry when the directory is not already the requested repository. It then clones into the link's target. #1847 fixed the generated release callers. Its review listed the remaining fixed-path checkouts in this repository's workflows that run after caller- or pull-request-authored content can be in the workspace. On a persistent self-hosted runner the workspace also survives between jobs, so content a previous job checked out is still there when the next job's first checkout runs.

## Decision

1. **A refusal step runs immediately before every checkout whose `path:` is a literal.** It is one shape everywhere:

   ```yaml
   - name: Refuse a symlink at the <path> checkout path
     run: |
       if [ -L <path> ]; then echo "::error::<path> is a symlink; actions/checkout would empty its target"; exit 1; fi
   ```

   No step runs between the refusal and its checkout, and the refusal runs under the checkout's own `if:`. A checkout that would still start after a failed refusal, because the refusal is `continue-on-error` or the checkout's condition calls `always()`, `failure()`, or `cancelled()`, must also require `steps.<refusal id>.outcome == 'success'`. Any later step that reads from the path under those conditions must require the checkout's own `outcome == 'success'`. A workflow step cannot share code with another workflow before anything is checked out, which is what the step protects, so the shared mechanism is this fixed shape and the test that executes it rather than a script or a composite action.

2. **It refuses a symlink, not a directory.** #1847's release-caller step refuses both, because a release tree never legitimately carries those paths. Here a persistent runner keeps a real `.gate-policy` or `.gate-trust` directory from the previous run, and checkout either reuses it or removes its entries with a recursive `rm` that does not follow nested links. Refusing a directory would fail the second run on every persistent runner and protect nothing.

3. **`scripts/ci-gate/fixed-path-checkout-refusal.test.py` is the enforcement.** For each site it plants a symlink to a stand-in for runner state and executes the step immediately before the checkout from the workflow's own text. That step counts as the refusal only when it fails with the link, succeeds without it, and succeeds with a real directory. The test then applies Actions' step-condition rules (`always()`, `continue-on-error`, `steps.<id>.outcome`) to decide whether the checkout still runs, emulates checkout's readdir-and-remove when it does, and requires the runner state to survive.

## Merge gate (`ai-review-merge.yml`)

The merge gate is the sensitive part of this decision.

- **`gate` `.gate-tools`, the reachable site.** `gate` checks out the pull-request head at the workspace root and, after the review, fetches the telemetry helpers into `.gate-tools`. A committed `.gate-tools` symlink would make that fetch empty its target. That target could be runner state on a private repository's persistent lane. It could also be `.gate-trust`, which would replace the immutable arm verifier with a sparse `main`. The telemetry block must never block the gate, so its refusal is `continue-on-error`. The fetch requires `steps.gate-tools-path.outcome == 'success'`, and the payload builder, which executes `.gate-tools/scripts/ci-telemetry/parse-claude-execution.sh`, requires `steps.gate-tools.outcome == 'success'`. Without that last condition, the builder's `always()` would run a pull-request script through the link whenever the refusal skipped the fetch.
- **`preflight` `.gate-recovery` and `.gate-policy`, `gate` `.gate-trust`, `complete-authorization` `.gate-trust`.** None of these follows a pull-request checkout in its own job. On a persistent runner the workspace still holds the previous job's root checkout, which can be a pull-request head, so a link can be present when they run. They get the same refusal. Each refusal fails its job, which fails closed.
- **No ruleset rotation is needed for the gate to take effect.** Organization ruleset 20722935 runs `gate-rearm.yml` at its stored commit. `gate-rearm.yml` dispatches each repository's own `ai-review-merge.yml`: in this repository that is this workflow on the default branch, effective on merge, and in an adopter it is a generated caller pinned to a contract commit, effective once regenerated there. The arm forces a regeneration only for callers older than `MINIMUM_SAFE_REVIEW` (ADR 0227). Raising that floor to this change's merge commit is a `gate-rearm.yml` edit. It needs the merge SHA, so it cannot be made here, and it takes effect only after the owner rotates 20722935. Whether to treat this hardening as a floor-raising security fix is left to that follow-up.

## Every other literal-path checkout

The test enumerates every `actions/checkout` step under `.github/workflows/` whose `path:` is a literal, so a new one fails until it has the refusal or an exemption. Except `repo-hygiene.yml`, described below, none of the following checks out pull-request content earlier in its own job. Where one runs on a persistent runner, a link can still arrive as residue from an earlier job, the same reach as the gate's pre-checkout sites. They all get the same step because it is cheap, and it leaves no exception to argue about later. They are `ai-privileged-merge.yml` `.gate-trust`, `changelog-contract-fleet-report.yml` `contract-source`, `changelog-release.yml` `.changelog-contract`, `container-release.yml` `.container-release-contract` and `.package-retention-contract`, `node-release.yml` `.verjson-npm-acquisition-contract`, `.node-release-contract` and `.package-retention-contract`, and `release-dispatch.yml` and `release-propose.yml` `.changelog-contract`.

`repo-hygiene.yml` `.repo-hygiene` is not low. Repositories call it from their CI workflow, which typically runs on `pull_request`, and it checks the repository under audit out at the workspace root with the event's default ref before it checks out the hygiene policy. That is `changelog-validate.yml`'s shape, and the #1847 review did not list it. It gets the same refusal. Six ADR 0235 relay-backlog digests are re-recorded because those jobs' bytes changed; no runner selection or head handling changed. They are `changelog-release.yml release`, `container-release.yml retention` directly and through `container-release-workflow-ref-canary.yml`, `release-dispatch.yml release-dispatch`, `release-propose.yml release-propose`, and `repo-hygiene.yml hygiene`.

`ai-promotion-retry.yml` `resolve` `.promotion-trust` runs on `CI_RUNNER_FASTLANE`, which read `["self-hosted","general"]` on 2026-10-18. It checks out nothing from a pull request, but on that persistent pool an earlier job's checkout can leave a link at `.promotion-trust`. The checkout would then empty the link's target, which could be runner state or another job's trusted checkout, and the job would run `review-policy-envelope.py` from whatever the clone left behind it. It gets the same refusal. An ADR 0235 *reviewed* entry binds this job's bytes, so the entry was re-reviewed under decision 6 rather than re-recorded. The checker's unresolved reasons are unchanged (fetch, head-materialization, runtime-checkout). The only change is a step that runs `[ -L .promotion-trust ]` and `echo`, reads nothing derived from a pull request, and adds no checkout, fetch, or execution. So the existing justification still holds, with one clause naming the refusal. The reviewed digest and the job's relay-backlog digest are both re-recorded. The approval decision 6 relies on is this pull request's own review under `main`'s ruleset.

Three sites are exempt. Each exemption fails the test once its site is protected, so it cannot outlive its reason.

- `release.yml` `verify` `.changelog-contract`: generated at `21eb48e7`, before #1847 added the release callers' refusal. Regenerating it at a contract commit that contains #1847 protects it.
- `cli-projects-package-surface-required.yml` `consumer` and `policy`: generated by `scripts/gen-node-required-workflow.py` and run by an organization ruleset at its stored commit, so a change needs that generator and a ruleset rotation.

## Consequences

- A pull request that commits a symlink at one of these paths fails the job instead of deleting runner state.
- A link left on a persistent runner fails that job on that runner until a later checkout of the repository removes it, or an operator does. This is deliberate: deleting the link silently would hide that it was planted.
- Paths built from `${{ github.run_id }}` are outside this decision: a pull request cannot know the run identifier when it is authored.
