---
date: 2026-10-15
issue: 1385
impact: patch
title: Secret policy keeps the AI review provider keys at visibility all and manifests JEV_API_KEY
---

`config/org-actions-secret-policy.json` now records `DEEPSEEK_API_KEY` and `OPENAI_API_KEY` at visibility `all`, matching the live secrets. `ai-review-merge.yml` reads both in each caller repository's context, and the owner decided on 2026-10-15 that any repository may select either provider through `AI_REVIEW_PRIMARY_PROVIDER` without a per-repository grant. The earlier `selected: [Verjson/.github]` target would have left both empty in every other repository's review. `JEV_API_KEY` is manifested at its current visibility. #1820 narrows it to `selected`, with that list as the repositories authorized to send diffs to the provider.
