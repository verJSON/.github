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

## Consequences

- A pull request that commits a symlink at one of these paths fails the job instead of deleting runner state.
- Paths built from `${{ github.run_id }}` are outside this decision: a pull request cannot know the run identifier when it is authored.
