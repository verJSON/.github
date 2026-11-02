---
date: 2026-11-02
issue: 1974
impact: patch
title: Caller-contract rejections assert their reason inside each case's job
---
`expect_rejection` in the changelog caller contract now requires `--reason`, the diagnostic the emitted suite must print, and checks it inside the case's own background job. Before, the main thread waited on 117 cases one at a time to grep each log, and 65 cases (CODEOWNERS, release trigger, snapshot, PR gate) passed on any nonzero exit. Optional `--pass` and `--fail` keep each existing label unchanged. Nineteen waits whose result was never read are removed, and six full-suite runs move from the main thread into the bounded job pool. With every expected reason deliberately altered, all 192 reason checks fail across the four shards; unaltered, all pass. ADR 0225 now names the shard helper's pin as the authoritative case count.
