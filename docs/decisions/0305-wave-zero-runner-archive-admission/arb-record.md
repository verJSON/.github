# Wave 0 archive admission for policy PR #2279

The archive-admission ARB reviewed the four-file [policy draft](https://github.com/verJSON/.github/pull/2279) at `e91bb23d2dc2a68f0761339a2f5243dfcf842617`, base `664e18cf2e74f5f240d5eb31b6a317a99668e0ba`, tree `dbde021fa4f7e9423458987a7cc24253795fcbcd`. It found no pre-existing protected chair record authorizing this archive. It recommended holding the draft pending a separate, one-use operator decision. The original Wave 0 source chair file has SHA-256 `e50672df4e017405bcabd1c15276de97a967ab74a8c9087fab10d0843ad16ecf`. [Original archive scope](https://github.com/verJSON/.github/blob/e91bb23d2dc2a68f0761339a2f5243dfcf842617/docs/decisions/0304-wave-zero-runner-source-adjudication/README.md#L16)

Run `arb-0304-archive-admission-20261124`, revision 2, reached **APPROVED** for the full advisory slate. Each of five seats accepted all three subjects. The chair did not vote. The state-backed ledger slate SHA-256 is `78f2dc0d84a5a13a8f3ed32ce38e073636d84839b31ade2eecb6eb918d4ebbb4`. The canonical transport fence SHA-256 is `535782ae376f5c7fa31157b229dc85ede20262891538fce658d67a37471c2416`. [Public board outcome](https://github.com/verJSON/.github/pull/2279#issuecomment-7138045904)

```canonical-slate-json
{"decision_rule":"unanimous","revision":2,"run_id":"arb-0304-archive-admission-20261124","schema_version":1,"source_pins":[{"id":"archive","ref":"refs/pull/2279/head","repo":"verJSON/.github","role":"primary","sha":"e91bb23d2dc2a68f0761339a2f5243dfcf842617"},{"id":"policy","ref":"refs/heads/main","repo":"verJSON/.github","role":"baseline","sha":"664e18cf2e74f5f240d5eb31b6a317a99668e0ba"}],"status":"APPROVED","subjects":[{"candidate_id":"A1","conditions":"Hold #2279 at e91bb23d2dc2a68f0761339a2f5243dfcf842617 on base 664e18cf2e74f5f240d5eb31b6a317a99668e0ba, tree dbde021fa4f7e9423458987a7cc24253795fcbcd, four documented paths. No already protected, independently verified chair record authorizing this archive transport was found in the designated canonical main inventory. R4 adjudicates #278, #279 and #2278 only. It cannot publish itself. Prior #2277/#370 permissions and a green check or owner account cannot supply the missing authority. This finding is bounded to the stated inventory, head and current policy. Keep all source PRs held for their own admission.","title":"Hold the current four-file archive for independent authority","votes":{"anthropic":"ACCEPT","cursor":"ACCEPT","google":"ACCEPT","openai":"ACCEPT","xai":"ACCEPT"}},{"candidate_id":"A2","conditions":"A chair or policy custodian could identify and independently verify an already protected applicable decision of a separately chartered ARB explicitly covering the archive, its scope, head/base/tree, paths and hashes. Verify protected provenance plus run and digest independently; the 40-hex locator and matching canonical slate bytes alone prove neither prior protected landing nor substantive authority. A maintain/admin user, including the existing shared account, can then publish the exact-head COMMENTED citation before the separate independent review receipt. The bound producer app enforces evidence and cannot create authority. Refresh gates and protections with bypass disabled; verify the actual passing native rule suite and landed bytes after any separately authorized landing. A new vote alone, second personal account, issue comment or unmerged branch does not satisfy this dependency.","title":"Distinguish the independent authority, its publisher and native enforcement","votes":{"anthropic":"ACCEPT","cursor":"ACCEPT","google":"ACCEPT","openai":"ACCEPT","xai":"ACCEPT"}},{"candidate_id":"A3","conditions":"Without an already admitted applicable record, #2279 remains held. This advice grants no authority for preparation, posting or a bootstrap. The smallest prospective change is a one-use, owner-ratified archive-only trust anchor with its own independent terminal authority, explicitly limiting how the companion decision itself can be admitted, if current policy permits that procedure after ratification. One option for explicit operator consideration is a companion ADR and distinct chair record limited to the original archive bytes, plus generated index and updated release note. The new one-time rule would have to explicitly say whether and why the independently reviewed final archive-only revision may cite that companion alone for its own initial transport. This would be a NEW expressly ratified bootstrap, not an available path now; never cite the prospective R4 record. A reusable class would require an additional, broader governance decision. Requiring every new companion to have an equally unpublished predecessor just moves the cycle. The operator must expressly ratify the exact archive-only design and its terminal basis before a changed draft or exceptional publication is pursued; if no permitted terminal basis exists or it is declined, keep #2279 held. A ratification comment alone is not the canonical record or a Gate R receipt. Any modified head requires fresh exact base/head/tree/path/hash review, ordered evidence, unchanged required checks and verified no-bypass landing. No scope transfer from #2277, changes to R4, general exception, live actions or source delivery authorization follows from these ballots.","title":"Terminate the archive dependency with a separately ratified, one-archive decision","votes":{"anthropic":"ACCEPT","cursor":"ACCEPT","google":"ACCEPT","openai":"ACCEPT","xai":"ACCEPT"}}],"validator_slate_sha256":"78f2dc0d84a5a13a8f3ed32ce38e073636d84839b31ade2eecb6eb918d4ebbb4"}
```

## Subsequent operator ratification

After the board reported its result, the delivery session put a single decision to the operator. The relevant request read:

> The remaining decision for this runner slice is whether you ratify the one-use procedure for #2279

The same message defined its scope:

> Ratification would authorize me to add the companion ADR and chair record, have the final commit independently reviewed, and cite only the companion to publish this archive through ordinary protection with bypass disabled.

The operator, `pyousefi`, replied:

> continue

This in-session reply ratifies A3’s one-use procedure for #2279. It establishes the terminal basis for initial archive publication: once the final archive-only revision is independently reviewed, that revision may cite this distinct companion chair record alone to publish the preserved Wave 0 source archive. The original source chair file must retain the SHA-256 above. The operator’s subsequent decision is recorded separately from the panel’s voted text. The [public proposal](https://github.com/verJSON/.github/pull/2279#issuecomment-7138045904) documents the requested procedure; the quoted exchange supplies the operator’s ratification. The shared publisher subsequently recorded the exchange on #2279. [Ratification attestation](https://github.com/verJSON/.github/pull/2279#issuecomment-7139810803)

The final base, head, tree, changed paths, both chair files and digests require independent verification. The citation on that exact head must identify only `docs/decisions/0305-wave-zero-runner-archive-admission/arb-record.md` at the independently verified immutable final commit. The independent review receipt must follow it. Verify the effective protections and required checks before ordinary admission with bypass disabled. After landing, verify a passing native ruleset result and landed bytes before citing the protected Wave 0 source decision elsewhere. Each source PR retains its own conditions. This record supplies no final-head attestation, merge receipt, production change or credential grant. [Evidence order](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/docs/decisions/0271-arb-consensus-is-approval-authority/README.md#L54)

## Independent panel

| Seat | Structured served identity or gap | A1 | A2 | A3 |
|---|---|---|---|---|
| anthropic | claude-fable-5-2 | ACCEPT | ACCEPT | ACCEPT |
| openai | Requested gpt-6.2-astra. The CLI did not report the served model. Counted under the version 2 identity-gap rule. | ACCEPT | ACCEPT | ACCEPT |
| google | gemini-4-neon-xhigh | ACCEPT | ACCEPT | ACCEPT |
| cursor | Composer 2.5 | ACCEPT | ACCEPT | ACCEPT |
| xai | Grok 4.7 256K Extra High | ACCEPT | ACCEPT | ACCEPT |

Four served identities were verified from structured responses. A sixth availability probe for DeepSeek Pro returned a provider error. In round 1, xAI requested removing wording that could imply the board granted permission to prepare another record. The chair accepted that change, and all five seats voted again in round 2. The operator ratified the proposed procedure after those votes.

## A1 · Hold the current four-file archive for independent authority

Hold #2279 at e91bb23d2dc2a68f0761339a2f5243dfcf842617 on base 664e18cf2e74f5f240d5eb31b6a317a99668e0ba, tree dbde021fa4f7e9423458987a7cc24253795fcbcd, four documented paths. No already protected, independently verified chair record authorizing this archive transport was found in the designated canonical main inventory. R4 adjudicates #278, #279 and #2278 only. It cannot publish itself. Prior #2277/#370 permissions and a green check or owner account cannot supply the missing authority. This finding is bounded to the stated inventory, head and current policy. Keep all source PRs held for their own admission.

- [verJSON/.github@e91bb23d · docs/decisions/0304-wave-zero-runner-source-adjudication/README.md:16](https://github.com/verJSON/.github/blob/e91bb23d2dc2a68f0761339a2f5243dfcf842617/docs/decisions/0304-wave-zero-runner-source-adjudication/README.md#L16)
- [verJSON/.github@e91bb23d · docs/decisions/0304-wave-zero-runner-source-adjudication/arb-record.md:37](https://github.com/verJSON/.github/blob/e91bb23d2dc2a68f0761339a2f5243dfcf842617/docs/decisions/0304-wave-zero-runner-source-adjudication/arb-record.md#L37)
- [verJSON/.github@664e18cf · docs/decisions/0302-identity-contracts-archive-admission/README.md:21](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/docs/decisions/0302-identity-contracts-archive-admission/README.md#L21)
- [verJSON/.github@664e18cf · docs/decisions/0300-gitlab-namespace-source-admission-arb/README.md:23](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/docs/decisions/0300-gitlab-namespace-source-admission-arb/README.md#L23)

## A2 · Distinguish the independent authority, its publisher and native enforcement

A chair or policy custodian could identify and independently verify an already protected applicable decision of a separately chartered ARB explicitly covering the archive, its scope, head/base/tree, paths and hashes. Verify protected provenance plus run and digest independently; the 40-hex locator and matching canonical slate bytes alone prove neither prior protected landing nor substantive authority. A maintain/admin user, including the existing shared account, can then publish the exact-head COMMENTED citation before the separate independent review receipt. The bound producer app enforces evidence and cannot create authority. Refresh gates and protections with bypass disabled; verify the actual passing native rule suite and landed bytes after any separately authorized landing. A new vote alone, second personal account, issue comment or unmerged branch does not satisfy this dependency.

- [verJSON/.github@664e18cf · docs/decisions/0271-arb-consensus-is-approval-authority/README.md:43](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/docs/decisions/0271-arb-consensus-is-approval-authority/README.md#L43)
- [verJSON/.github@664e18cf · docs/decisions/0271-arb-consensus-is-approval-authority/README.md:95](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/docs/decisions/0271-arb-consensus-is-approval-authority/README.md#L95)
- [verJSON/.github@664e18cf · scripts/ci-gate/arb-consensus-citation.sh:69](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/scripts/ci-gate/arb-consensus-citation.sh#L69)
- [verJSON/.github@664e18cf · scripts/ci-gate/arb-consensus-citation.sh:121](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/scripts/ci-gate/arb-consensus-citation.sh#L121)
- [verJSON/.github@664e18cf · docs/decisions/0257-merge-admission-requires-the-exact-head-review-receipt/README.md:117](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/docs/decisions/0257-merge-admission-requires-the-exact-head-review-receipt/README.md#L117)
- [verJSON/.github@664e18cf · docs/decisions/0260-native-merge-admission-through-a-producer-posted-check/README.md:36](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/docs/decisions/0260-native-merge-admission-through-a-producer-posted-check/README.md#L36)
- [verJSON/.github@e91bb23d · docs/decisions/0304-wave-zero-runner-source-adjudication/arb-record.md:37](https://github.com/verJSON/.github/blob/e91bb23d2dc2a68f0761339a2f5243dfcf842617/docs/decisions/0304-wave-zero-runner-source-adjudication/arb-record.md#L37)

## A3 · Terminate the archive dependency with a separately ratified, one-archive decision

Without an already admitted applicable record, #2279 remains held. This advice grants no authority for preparation, posting or a bootstrap. The smallest prospective change is a one-use, owner-ratified archive-only trust anchor with its own independent terminal authority, explicitly limiting how the companion decision itself can be admitted, if current policy permits that procedure after ratification. One option for explicit operator consideration is a companion ADR and distinct chair record limited to the original archive bytes, plus generated index and updated release note. The new one-time rule would have to explicitly say whether and why the independently reviewed final archive-only revision may cite that companion alone for its own initial transport. This would be a NEW expressly ratified bootstrap, not an available path now; never cite the prospective R4 record. A reusable class would require an additional, broader governance decision. Requiring every new companion to have an equally unpublished predecessor just moves the cycle. The operator must expressly ratify the exact archive-only design and its terminal basis before a changed draft or exceptional publication is pursued; if no permitted terminal basis exists or it is declined, keep #2279 held. A ratification comment alone is not the canonical record or a Gate R receipt. Any modified head requires fresh exact base/head/tree/path/hash review, ordered evidence, unchanged required checks and verified no-bypass landing. No scope transfer from #2277, changes to R4, general exception, live actions or source delivery authorization follows from these ballots.

- [verJSON/.github@e91bb23d · docs/decisions/0304-wave-zero-runner-source-adjudication/README.md:16](https://github.com/verJSON/.github/blob/e91bb23d2dc2a68f0761339a2f5243dfcf842617/docs/decisions/0304-wave-zero-runner-source-adjudication/README.md#L16)
- [verJSON/.github@e91bb23d · docs/decisions/0304-wave-zero-runner-source-adjudication/arb-record.md:37](https://github.com/verJSON/.github/blob/e91bb23d2dc2a68f0761339a2f5243dfcf842617/docs/decisions/0304-wave-zero-runner-source-adjudication/arb-record.md#L37)
- [verJSON/.github@664e18cf · docs/decisions/0302-identity-contracts-archive-admission/README.md:18](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/docs/decisions/0302-identity-contracts-archive-admission/README.md#L18)
- [verJSON/.github@664e18cf · docs/decisions/0302-identity-contracts-archive-admission/README.md:21](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/docs/decisions/0302-identity-contracts-archive-admission/README.md#L21)
- [verJSON/.github@664e18cf · docs/decisions/0271-arb-consensus-is-approval-authority/README.md:33](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/docs/decisions/0271-arb-consensus-is-approval-authority/README.md#L33)
- [verJSON/.github@664e18cf · docs/decisions/0269-retire-owner-admin-merge-bypass/README.md:43](https://github.com/verJSON/.github/blob/664e18cf2e74f5f240d5eb31b6a317a99668e0ba/docs/decisions/0269-retire-owner-admin-merge-bypass/README.md#L43)

## Verification

The chair replayed the original round-two SQLite state and checked all five raw ballot response hashes. The shared citation packet passed the official checker. The first xAI discovery attempt consulted installed review-procedure files and was excluded before ballots. A fresh isolated process reviewed the identical evidence packet without tool calls. The final ledger preserved immutable source pins, model identity gaps, all 15 votes and the round-one revision request. The shared owner account binds the public work-start timestamp. [Standards work-start anchor](https://github.com/verJSON/.github/pull/2279#issuecomment-7137610320)

## Standards grounding
- status: verified
- reason: work_start_baseline
- config_origin: installed
- config_provenance: installer-default
- source: verJSON/verjson-docs:governance/architecture-review-context.json@main
- config_sha: 57932dc678cb089a99c19e6eb3aba78329933eab
- manifest_sha256: dc3f48f925c4117bee023ce25208cc45c9492b3836233cd8983e35c7ea5fa5fc (resolver-manifest-v1)
- binding_sha256: 728de83f4b9f608b55c40cc91cc12ac56a36a24c72832de11691cd56215fbd04 (resolver-binding-v1)
- canonical_complete: true
- baseline_eligible: true
- eligibility_reason: work_start_baseline
- work_started_at: 2026-11-24T16:24:13Z
- baseline_observed_at: 2026-11-24T16:22:08Z
- current_head_status: grounded
- current_head_config_sha: 57932dc678cb089a99c19e6eb3aba78329933eab
- current_head_manifest_sha256: dc3f48f925c4117bee023ce25208cc45c9492b3836233cd8983e35c7ea5fa5fc (resolver-manifest-v1)
- current_head_binding_sha256: 728de83f4b9f608b55c40cc91cc12ac56a36a24c72832de11691cd56215fbd04 (resolver-binding-v1)
- sources:
  - engineering-standards VerJSON/verjson-agents@e0510d2f906fd404c623d5ff6995f42093381652:CANON.md [git_commit, available]
  - code-style VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:agents/code-style.md [git_commit, available]
  - ci-tiers VerJSON/.github@664e18cf2e74f5f240d5eb31b6a317a99668e0ba:docs/ci-workflow-tiers.md [git_commit, available]
  - node-workflows VerJSON/.github@664e18cf2e74f5f240d5eb31b6a317a99668e0ba:docs/node-workflows.md [git_commit, available]
  - protected-reviews VerJSON/.github@664e18cf2e74f5f240d5eb31b6a317a99668e0ba:docs/ruleset-conformance.md [git_commit, available]
  - merge-admission-decision VerJSON/.github@664e18cf2e74f5f240d5eb31b6a317a99668e0ba:docs/decisions/0257-merge-admission-requires-the-exact-head-review-receipt/README.md [git_commit, available]
  - merge-admission-runbook VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:agents/merge-admission.md [git_commit, available]
  - delivery-workflow VerJSON/verjson-agents@e0510d2f906fd404c623d5ff6995f42093381652:rules/workflow.md [git_commit, available]
  - infrastructure-overview VerJSON/.github@664e18cf2e74f5f240d5eb31b6a317a99668e0ba:docs/infrastructure/README.md [git_commit, available]
  - runner-routing VerJSON/.github@664e18cf2e74f5f240d5eb31b6a317a99668e0ba:docs/runner-routing.md [git_commit, available]
  - runner-lanes-decision VerJSON/.github@664e18cf2e74f5f240d5eb31b6a317a99668e0ba:docs/decisions/0040-runner-lanes-and-admission-axes/README.md [git_commit, available]
  - platform-composition VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:architecture/platform-composition.md [git_commit, available]
  - capability-catalog VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:catalog/packages.md [git_commit, available]
  - security-practices VerJSON/verjson-agents@e0510d2f906fd404c623d5ff6995f42093381652:rules/security.md [git_commit, available]
  - security-policy VerJSON/.github@664e18cf2e74f5f240d5eb31b6a317a99668e0ba:SECURITY.md [git_commit, available]
  - dependency-principles VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:architecture/dependency-principles.md [git_commit, available]
  - ownership-boundaries VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:architecture/ownership-boundaries.md [git_commit, available]
  - decision-placement VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:governance/decision-placement.md [git_commit, available]
  - agent-message-contracts-decision VerJSON/verjson-agents@e0510d2f906fd404c623d5ff6995f42093381652:docs/decisions/0036-versioned-schema-contracts-for-agent-messages/README.md [git_commit, available]
  - agent-message-contracts-adoption VerJSON/verjson-agents@e0510d2f906fd404c623d5ff6995f42093381652:contracts/agent-messages/adoption.json [git_commit, available]
  - agile-ai-manifesto VerJSON/agile-ai-manifesto@15d91cb19be0b3889165b7a7ca7bb905afbcd831:public/index.html [git_commit, available]
  - tda-review-guide VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:governance/technical-design-authority.md [git_commit, available]
- gaps: none
