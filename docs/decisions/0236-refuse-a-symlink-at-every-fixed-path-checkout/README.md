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

   No step runs between the refusal and its checkout, and the refusal runs under the checkout's own `if:`. A workflow step cannot share code with another workflow before anything is checked out, which is what the step protects, so the shared mechanism is this fixed shape and the test that executes it rather than a script or a composite action.

2. **It refuses a symlink, not a directory.** #1847's release-caller step refuses both, because a release tree never legitimately carries those paths. Here a persistent runner keeps a real `.gate-policy` or `.gate-trust` directory from the previous run, and checkout either reuses it or removes its entries with a recursive `rm` that does not follow nested links. Refusing a directory would fail the second run on every persistent runner and protect nothing.

3. **`scripts/ci-gate/fixed-path-checkout-refusal.test.py` is the enforcement.** For each site it plants a symlink to a stand-in for runner state and executes the step immediately before the checkout from the workflow's own text. That step counts as the refusal only when it fails with the link, succeeds without it, and succeeds with a real directory. The test then applies Actions' step-condition rules (`always()`, `continue-on-error`, `steps.<id>.outcome`) to decide whether the checkout still runs, emulates checkout's readdir-and-remove when it does, and requires the runner state to survive.

## Merge gate (`ai-review-merge.yml`)

The merge gate is the sensitive part of this decision.

- **`gate` `.gate-tools`, the reachable site.** `gate` checks out the pull-request head at the workspace root and, after the review, fetches the telemetry helpers into `.gate-tools`. A committed `.gate-tools` symlink would make that fetch empty its target. That target could be runner state on a private repository's persistent lane. It could also be `.gate-trust`, which would replace the immutable arm verifier with a sparse `main`. The telemetry block must never block the gate, so its refusal is `continue-on-error`. The fetch requires `steps.gate-tools-path.outcome == 'success'`, and the payload builder, which executes `.gate-tools/scripts/ci-telemetry/parse-claude-execution.sh`, requires `steps.gate-tools.outcome == 'success'`. Without that last condition, the builder's `always()` would run a pull-request script through the link whenever the refusal skipped the fetch.
- **`preflight` `.gate-recovery` and `.gate-policy`, `gate` `.gate-trust`, `complete-authorization` `.gate-trust`.** None of these follows a pull-request checkout in its own job. On a persistent runner the workspace still holds the previous job's root checkout, which can be a pull-request head, so a link can be present when they run. They get the same refusal. Each refusal fails its job, which fails closed.
- **No ruleset rotation is needed for the gate to take effect.** Organization ruleset 20722935 runs `gate-rearm.yml` at its stored commit. `gate-rearm.yml` dispatches each repository's own `ai-review-merge.yml`: in this repository that is this workflow on the default branch, effective on merge, and in an adopter it is a generated caller pinned to a contract commit, effective once regenerated there. The arm forces a regeneration only for callers older than `MINIMUM_SAFE_REVIEW` (ADR 0227). Raising that floor to this change's merge commit is a `gate-rearm.yml` edit. It needs the merge SHA, so it cannot be made here, and it takes effect only after the owner rotates 20722935. Whether to treat this hardening as a floor-raising security fix is left to that follow-up.

## Consequences

- A pull request that commits a symlink at one of these paths fails the job instead of deleting runner state.
- Paths built from `${{ github.run_id }}` are outside this decision: a pull request cannot know the run identifier when it is authored.
