---
date: 2026-10-15
issue: 1794
impact: patch
title: Terminal promotion and CLI projects admission compare repository identities in any ASCII case
---

`ai-privileged-merge.yml`'s "Authorize terminal merge from trusted metadata" step compared `job.workflow_repository` to the literal `Verjson/.github` byte for byte. Since the 2026-10-02 organization rename, a promotion that reaches the workflow through `ai-promotion-retry.yml`'s local `./` call reports `verJSON/.github`, so every retry failed with `promotion is not executing from the canonical workflow repository` (Verjson/AiB run 38790031475, PR #341 head `51e5add9`) and a pull request whose CI outlasted the direct privileged merge never promoted. The step now compares the repository as an ASCII-case-insensitive GitHub identity, as ADR 0213 records: `jq`'s `ascii_downcase` leaves non-ASCII untouched, so another owner, another repository, and a Unicode lookalike are still refused.

The generated CLI projects required workflow (`scripts/gen-node-required-workflow.py`, regenerated into `.github/workflows/cli-projects-package-surface-required.yml`) had the same defect twice in its admission step: `github.repository` and the pull request API's `head.repo.full_name` were compared byte for byte to `Verjson/verjson-cli-projects`, and both now report `verJSON/verjson-cli-projects`, so every verjson-cli-projects pull request since the rename failed admission (run 38432170170). Both comparisons are now ASCII-case-insensitive identity comparisons. The consumer caller digest the step pins is unchanged. The organization ruleset runs this workflow from `refs/heads/main`, so the merge takes effect without a repin.

Adopters receive the terminal promotion fix only when their privileged-merge and promotion-retry callers move to a revision containing it; Verjson/AiB still calls both workflows at `9f8d55c5`.
