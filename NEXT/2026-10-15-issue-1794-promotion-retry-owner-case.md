---
date: 2026-10-15
issue: 1794
impact: patch
title: Terminal promotion accepts the canonical workflow repository in any ASCII case
---

`ai-privileged-merge.yml`'s "Authorize terminal merge from trusted metadata" step compared `job.workflow_repository` to the literal `Verjson/.github` byte for byte. Since the 2026-10-02 organization rename, a promotion that reaches the workflow through `ai-promotion-retry.yml`'s local `./` call reports `verJSON/.github`, so every retry failed with `promotion is not executing from the canonical workflow repository` (Verjson/AiB run 38790031475, PR #341 head `51e5add9`) and a pull request whose CI outlasted the direct privileged merge never promoted. The step now compares the repository as an ASCII-case-insensitive GitHub identity, as ADR 0213 records: `jq`'s `ascii_downcase` leaves non-ASCII untouched, so another owner, another repository, and a Unicode lookalike are still refused.

Adopters receive the fix only when their privileged-merge and promotion-retry callers move to a revision containing it; Verjson/AiB still calls both workflows at `9f8d55c5`.
