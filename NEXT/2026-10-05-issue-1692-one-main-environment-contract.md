---
date: 2026-10-05
issue: 1692
title: One main environment contract
impact: patch
---

Index the proposed canonical deployment contract and the current generator gap. The proposed dev path would deploy the current `main` HEAD through a validated, lightweight path targeting p95 under five minutes; its image may have a different digest from the later release candidate. Nonprod and prod would promote the same fully verified pinned digest set with separate environment controls. CLI adoption depends on this canonical contract.
