# 0245 — Ecosystem queue-bound candidate admission proposal (relocated)

- **Date:** 2026-10-28
- **Status:** Relocated proposal; not accepted
- **Issue:** [verJSON/.github#1945](https://github.com/VerJSON/.github/issues/1945)
- **Category:** merge-gate admission and protected-branch authority

The governing cross-repository proposal is now [verjson-docs ADR 0014, draft PR #34](https://github.com/VerJSON/verjson-docs/pull/34), under the published [decision-placement standard](https://github.com/VerJSON/verjson-docs/blob/782362bfca9eaf89ede2c8be770e708d45b0831d/governance/decision-placement.md). This path remains a stable pointer and an implementation-evidence index. Neither proposal is ratified. No workflow, ruleset, credential, or merge behavior changes through this pointer.

The [full proposal at commit c4a63a61](https://github.com/VerJSON/.github/blob/c4a63a6117bb8cf33176becbc9e0077eab68cf95/docs/decisions/0245-queue-bound-candidate-admission/README.md) preserves the earlier contract and dated observations. The [public configuration probe](evidence/2026-10-29-public-queue-probe.json), [private queue control](evidence/2026-10-29-private-queue-control.json), [isolation inventory](evidence/2026-10-29-canary-isolation.json), [PR-head dispatch observation](evidence/2026-10-29-isolated-pr-dispatch.json), and [canary plan](canary-plan.md) remain here as supporting evidence. The [2026-11-13 ARB record](https://github.com/VerJSON/.github/pull/1946#issuecomment-6855908212) approved conditions for a revised decision, not the ADR itself; its [placement addendum](https://github.com/VerJSON/.github/pull/1946#issuecomment-6856087055) explains this move.

The autonomous terminal helper remains disabled pending a reviewed non-bypass admission path. A proposed ecosystem decision cannot itself authorize queue enrollment or a merge.
