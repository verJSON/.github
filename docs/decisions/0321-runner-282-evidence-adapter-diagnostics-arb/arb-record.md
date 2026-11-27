# ARB chair record for runner release-evidence diagnostics

All five chartered provider seats accepted the source-only recommendation for [runner #282](https://github.com/verJSON/verjson-git-runners/pull/282) under [runner #197](https://github.com/verJSON/verjson-git-runners/issues/197) in round 1 of two. The validated ledger records one decision.

Gate R canonical fence SHA-256 `ee59130f62a4869858754373ca310a279becee4621f4ea303041ebf846de71c1`. Ledger slate SHA-256 `c9bb496980182e935b35917749362780b795f91e2b382b3162ab7d8999498972`.

```canonical-slate-json
{"base":"8b711792865dcd097572f4e416916c1672e3e6f6","candidate_tree":"43287e5a69f5a60d6f518f252898887b2e92fc08","delta_sha256":"c5e7d961e9c07b40a74baf21461b26653e943c4d73b91d03b02a757212431f84","head":"a81f75df15aa1547167c26ba599ec26231aba546","issue":"verJSON/verjson-git-runners#197","items":[{"acceptance":"Qualify exactly the four source paths on runner #282 head a81f75df15aa1547167c26ba599ec26231aba546 and its stated native candidate/tree for fixed metadata, manifest-download and checkout-identity diagnostics. Preserve manifest digest and fail-closed checks, the real relay tests and existing shell job. The earlier hosted failure remains unattributed. Before an ordinary protected source merge, land an independently reviewed policy chair archive, verify applicable hosted checks and platform rules, re-read candidate parents and tree, and post a subject-applicable exact-head citation before the separate approval receipt. Obtain separate authorization for the source-only publisher window. Verify publisher containment before landing, recovery readiness and terminal-state handling before running the window, then restoration and zero candidate runs after landing. An unresolved recovery defect blocks that window. Any changed subject or stale approval requires requalification. This recommendation grants no release, publication, deployment, protected rerun, host or credential authority.","id":"S1"}],"ledger_slate_sha256":"c9bb496980182e935b35917749362780b795f91e2b382b3162ab7d8999498972","native_candidate":"d33862900335ddef841a75e22e7abdb148880a31","pr":"verJSON/verjson-git-runners#282","run_id":"arb-runner-evidence-282-a81f75df15-20261127","source_delta":[".github/workflows/test.yml","NEXT/2026-11-27-issue-20261127T154720Z-197-sanitize-evidence-adapter-diagnostics.md","scripts/runner-deployment-evidence.py","tests/runner_deployment_evidence_test.py"],"status":"APPROVED"}
```

## Decision

Qualify exactly the four source paths on runner #282 head a81f75df15aa1547167c26ba599ec26231aba546 and its stated native candidate/tree for fixed metadata, manifest-download and checkout-identity diagnostics. Preserve manifest digest and fail-closed checks, the real relay tests and existing shell job. The earlier hosted failure remains unattributed. Before an ordinary protected source merge, land an independently reviewed policy chair archive, verify applicable hosted checks and platform rules, re-read candidate parents and tree, and post a subject-applicable exact-head citation before the separate approval receipt. Obtain separate authorization for the source-only publisher window. Verify publisher containment before landing, recovery readiness and terminal-state handling before running the window, then restoration and zero candidate runs after landing. An unresolved recovery defect blocks that window. Any changed subject or stale approval requires requalification. This recommendation grants no release, publication, deployment, protected rerun, host or credential authority.

## Source and verification

Runner base `8b711792865dcd097572f4e416916c1672e3e6f6`, PR head `a81f75df15aa1547167c26ba599ec26231aba546` and native merge candidate `d33862900335ddef841a75e22e7abdb148880a31` were re-read. The candidate parents are the base followed by the head. Head and candidate have the identical tree `43287e5a69f5a60d6f518f252898887b2e92fc08`. The exact binary diff from base to head has SHA-256 `c5e7d961e9c07b40a74baf21461b26653e943c4d73b91d03b02a757212431f84`. The four changed paths are:

- .github/workflows/test.yml
- NEXT/2026-11-27-issue-20261127T154720Z-197-sanitize-evidence-adapter-diagnostics.md
- scripts/runner-deployment-evidence.py
- tests/runner_deployment_evidence_test.py

The source review, fixed diagnostics, reachability of unchanged release-byte checks, immutable merge gate and the restricted workflow test command were inspected against the same native candidate. An independently reviewed exact-head pre-push receipt was reported to the chair. The chair ran all 31 offline adapter tests and checked the full stderr of 16 failing adapter samples at the native head. Each captured sample comprised a single fixed annotation without a URL, fixture secret or traceback. The new unit-suite command precedes the existing final readiness gate.

The public run 46026637316 failed at the consumer evidence subprocess before the host-export transport. Several distinct simulated conditions previously yielded the same public failure. The hosted cause is unresolved, and the fix improves only the stated evidence stages. A future credential, live runner operation, provider action, authorization change or protected dispatch is outside the recommendation.

## Membership and ballots

The provider identities below report metadata from actual responses. Each chartered member saw the same pinned packet, gave an independent review and voted on a common digest.

| Provider | Requested | Structured served model | Vote | Raw ballot SHA-256 |
|---|---|---|---|---|
| anthropic | claude-fable-5-2 | claude-fable-5-2 | ACCEPT | 78ce996100bd736c0cd9b4ee28f381a847d3380e05a767923a896f58bf6aca16 |
| openai | gpt-6.2-astra | Unreported; ledger v2 identity gap | ACCEPT | 2b1c130f94191ae8b420e38ad3d82a0de57bf4ab51202cf508e531202e7fa808 |
| google | gemini-4-neon-xhigh | gemini-4-neon-xhigh | ACCEPT | 93f26326c1767f2d897538e9df2e134b9b3cdc4099f476d6cd82b7375ff2a62d |
| cursor | composer-2.5 | Composer 2.5 | ACCEPT | 3c95ed19a1cd6c94dd834de0506e3b90c80743590a5e0560c2993973d9442c28 |
| xai | grok-4.7-xhigh | Grok 4.7 256K Extra High | ACCEPT | 67f3b58fc2bc01e4afa4f8eda13598be527b03b9234f331839aca98387c800b8 |

DeepSeek V4 Pro is outside the counted roster. The OpenCode provider invocation for the published Pro model produced an explicit error and no model response. The configured Flash route answered the content probe at a different tier. No reset window was reported. [Provider model release history](https://api-docs.deepseek.com/updates/)

The ledger validator compared this final ballot set with the private round database and recorded consensus. The package includes immutable source pins, candidate references, raw response digests and the verified work-start grounding. The governance baseline is ADR 0271. The earlier subject-bound decision for runner #281 is ADR 0320. The verJSON/.github archive-only lane is described in ADR 0309. The runner uses merge admission policy at `dc3008626f4b7da78bdf5e88da9c46fe2510f500`.

## Conditions and communication

The operating team must qualify its source-only window for this exact merge. Publisher containment, recovery, all applicable platform checks and receipts require verification under existing controls. Board agreement conveys no merge command or infrastructure authority.

All five independent reviews found no requirement for a source change at this exact head. The chair checked the adapter main catch at line 224 and observed full stderr for all 16 synthetic failures represented by six checked-in relay tests. Each contained exactly one fixed annotation and no fixture secret, URL or traceback. This qualifies only those covered failure paths. The final wording makes verified publisher containment and recovery readiness explicit, retains current checks, distinguishes pre- and post-merge proof, requires rereading immutable source coordinates, and leaves the earlier hosted cause unknown. A changed head or native candidate requires fresh qualification. An unresolved recovery defect may not be carried into a source-only window. These conditions preserve existing policy without creating a new standing rule.

The adopted v2 schema preserved immutable source coordinates, dissent dispositions, actual response hashes and the OpenAI identity gap. The discovery harness considers a zero CLI exit separately from a content-bearing provider response. Checking the structured error event prevented a non-answer from being counted. No schema change is indicated.

No dissent was retained.

The shared operator account anchors publication time. Individual GitHub accounts do not attest the provider identity. [Work-start record](https://github.com/verJSON/verjson-git-runners/issues/197#issuecomment-7222413373)

## Standards grounding
- status: verified
- reason: work_start_baseline
- config_origin: installed
- config_provenance: installer-default
- source: verJSON/verjson-docs:governance/architecture-review-context.json@main
- config_sha: bb94a8c83eb48c4e84b46d4910a6394f598f2337
- manifest_sha256: 5bbce9fdbc2545a514dc13583f0f576be7b86e6982a91fc79e03576cdf1e0ad9 (resolver-manifest-v1)
- binding_sha256: c9d0c79e2064b8cccb5afebec0af7c8dd0e937d2ab556471710d1769b28b27e8 (resolver-binding-v1)
- canonical_complete: true
- baseline_eligible: true
- eligibility_reason: work_start_baseline
- work_started_at: 2026-11-27T15:43:56Z
- baseline_observed_at: 2026-11-27T15:43:54Z
- sources:
  - engineering-standards VerJSON/verjson-agents@7dc3fe98295b051896eef56ca5182410cd60ce14:CANON.md [git_commit, available]
  - code-style VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:agents/code-style.md [git_commit, available]
  - ci-tiers VerJSON/.github@c1b47ae6d0612ccdb6db0fcee82956ee2cfd101f:docs/ci-workflow-tiers.md [git_commit, available]
  - node-workflows VerJSON/.github@c1b47ae6d0612ccdb6db0fcee82956ee2cfd101f:docs/node-workflows.md [git_commit, available]
  - protected-reviews VerJSON/.github@c1b47ae6d0612ccdb6db0fcee82956ee2cfd101f:docs/ruleset-conformance.md [git_commit, available]
  - merge-admission-decision VerJSON/.github@c1b47ae6d0612ccdb6db0fcee82956ee2cfd101f:docs/decisions/0257-merge-admission-requires-the-exact-head-review-receipt/README.md [git_commit, available]
  - merge-admission-runbook VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:agents/merge-admission.md [git_commit, available]
  - delivery-workflow VerJSON/verjson-agents@7dc3fe98295b051896eef56ca5182410cd60ce14:rules/workflow.md [git_commit, available]
  - infrastructure-overview VerJSON/.github@c1b47ae6d0612ccdb6db0fcee82956ee2cfd101f:docs/infrastructure/README.md [git_commit, available]
  - runner-routing VerJSON/.github@c1b47ae6d0612ccdb6db0fcee82956ee2cfd101f:docs/runner-routing.md [git_commit, available]
  - runner-lanes-decision VerJSON/.github@c1b47ae6d0612ccdb6db0fcee82956ee2cfd101f:docs/decisions/0040-runner-lanes-and-admission-axes/README.md [git_commit, available]
  - platform-composition VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:architecture/platform-composition.md [git_commit, available]
  - capability-catalog VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:catalog/packages.md [git_commit, available]
  - security-practices VerJSON/verjson-agents@7dc3fe98295b051896eef56ca5182410cd60ce14:rules/security.md [git_commit, available]
  - security-policy VerJSON/.github@c1b47ae6d0612ccdb6db0fcee82956ee2cfd101f:SECURITY.md [git_commit, available]
  - dependency-principles VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:architecture/dependency-principles.md [git_commit, available]
  - ownership-boundaries VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:architecture/ownership-boundaries.md [git_commit, available]
  - decision-placement VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:governance/decision-placement.md [git_commit, available]
  - agent-message-contracts-decision VerJSON/verjson-agents@7dc3fe98295b051896eef56ca5182410cd60ce14:docs/decisions/0036-versioned-schema-contracts-for-agent-messages/README.md [git_commit, available]
  - agent-message-contracts-adoption VerJSON/verjson-agents@7dc3fe98295b051896eef56ca5182410cd60ce14:contracts/agent-messages/adoption.json [git_commit, available]
  - agile-ai-manifesto VerJSON/agile-ai-manifesto@15d91cb19be0b3889165b7a7ca7bb905afbcd831:public/index.html [git_commit, available]
  - tda-review-guide VerJSON/verjson-docs@bb94a8c83eb48c4e84b46d4910a6394f598f2337:governance/technical-design-authority.md [git_commit, available]
- gaps: none
