# 0320 — Board review of the runner adopter for doctl status diagnostics

- **Date:** 2026-11-27
- **Status:** Accepted. Five seats approved both items in round one of the [chair record](arb-record.md).
- **Scope:** [Runner PR #281](https://github.com/verJSON/verjson-git-runners/pull/281) under [runner canary issue #197](https://github.com/verJSON/verjson-git-runners/issues/197).

The board qualified the runner deployment adopter at `74384a9cb3468d65e986d2ce5c8c8d1f4d46d51c`. All eleven artifacts reproduced from protected policy commit `9026a267b3bd5a22d97b6ed775bd811ebb24de38`. Nine changed. The original protected run stopped during inventory, and its underlying provider response remains unknown. [Canonical fix](https://github.com/verJSON/.github/pull/2299) · [Canonical source decision](../0319-dotgithub-2299-doctl-diagnostics-arb/README.md)

A push to runner main normally starts candidate publication. S2 requires a separately authorized window, verified containment and recovery, current checks and ordinary protection. The chair separately documented an outstanding recovery fault in the proposed procedure. Live runner execution and rollback remain outstanding. [Source decision and conditions](arb-record.md)

## Provenance

The chair supplied the original record before this archive commit. Its 20,699 bytes have SHA-256 `447d6a9a4c52335df4e7f7712b6234dc3f439071f8d7698562b60e9df18bd3c0`. Run `arb-runner-adoption-197-74384a9cb3-20261127` has canonical fence SHA-256 `e107f01db8085e680cc6f3a70ec0a923446b4554fc1bb1ce575c705af3bc338b` and ledger slate SHA-256 `1de68576711810ec5f3c18e2ac34f0b4ea16ee6c890d08e2abdc0280d2e7bcc5`. Four seats identified the model that served their request. OpenAI omitted that metadata and DeepSeek was unavailable before the board convened. [Timestamped chair outcome](https://github.com/verJSON/verjson-git-runners/pull/281#issuecomment-7221554198) · [Archive admission](../0309-standing-archive-only-admission-lane/README.md)
