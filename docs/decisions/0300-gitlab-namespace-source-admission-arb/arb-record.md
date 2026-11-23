# Exact CI #370 GitLab namespace source delivery review

Run `arb-ci-370-namespace-source-20261123-02`, revision 2. The chair did not vote. This record recommends conditional source delivery and a separate archive. The board grants no operational authority.

Canonical fence SHA-256 `9a4247c7348907a46afcc2a305192eb44f1b828fe7d93b0185b443e40bee62ad`. State-backed validator slate SHA-256 `241d06044430b9e285c93de499e27523091f2be89d667366367c3f358da5273e`.

```canonical-slate-json
{"decision_rule":"unanimous","revision":2,"run_id":"arb-ci-370-namespace-source-20261123-02","schema_version":1,"source_pins":[{"id":"policy","ref":"main","repo":"VerJSON/.github","role":"baseline","sha":"bb1a764758edb1d25c1bcd5ea90becebc9471a81"},{"id":"ci-base","ref":"main","repo":"VerJSON/verjson-ci","role":"baseline","sha":"68facb416c6751408061ff15701f895fd6cd79a6"},{"id":"ci","ref":"#370 exact source","repo":"VerJSON/verjson-ci","role":"primary","sha":"fd5f9c4fd1939d7a304554df5722e4e77d9327b2"}],"status":"APPROVED","subjects":[{"candidate_id":"A1","conditions":"Conditionally support exact immutable CI #370 head fd5f9c4fd1939d7a304554df5722e4e77d9327b2, base 68facb416c6751408061ff15701f895fd6cd79a6, tree e4241e4c2a3be32f8ebcad44fb4bf7a6109f8e34. Only source/code/tests/docs for exact GitLab casing in registry gateway, runner promotion broker, and project2 REST+SSH backend, with GitHub issuer/numeric IDs/workflow unchanged. Installed production CLI storage at @verjson/ci/bin differs from the staged private @verjson/ci-entrypoint package. The supported normal promotion broker is blocked by the active release hold in the exact built package. Keep the current production URL and all active services, with no alternate entrypoint, hold bypass or partial rollout until the separate supported broker delivery under ADR0062 / CLI #187 is qualified. No live rename, config, installation, restart, credential, runtime authority, signing, publication, Nexus or private consumer changes. The operator-observed display name changed while the path still reads Verjson. Old-case source controls on unrelated project2 candidate/private release remain held and require separate authority. Before source admission, require fresh exact-hosted non-admission CI including actual ready checkout and current candidate, verified policy and permissions, an authentic ordered exact-head citation tied to an immutable verJSON/.github policy archive, followed by the exact-head independent review receipt. Use any genuinely sufficient pre-existing approved evidence if available. A2 is one potential way to transport this exact board record and does not add another GitHub gate. No GitLab production job is an admission prerequisite. Then require bound App SUCCESS, current CLEAN and canonical assert exit0/true, then ordinary no-admin/no-auto matched-head merge; hold on drift or unavailable evidence. Board not live activation and cannot itself post or merge.","title":"Exact source-only ordinary admission for CI #370","votes":{"anthropic":"ACCEPT","cursor":"ACCEPT","google":"ACCEPT","openai":"ACCEPT","xai":"ACCEPT"}},{"candidate_id":"A2","conditions":"Prepare a canonical record only for existing CI #30 and #370, with immutable head/base/tree, private state-backed v2 ledger, five actual votes, four evidenced served identities with explicit gap if any, and validated fence run and digest. Require separate owner scope and exact authorized path/readback, genuine independently reviewed protected archive admission and byte proof before source #370 cites it. Existing archive-only self-head transport precedent does not transfer authority; allow no prospective self-head citation unless separately expressly authorized under current policy solely for the documentation transport. Never post, push, merge or publish on this board's authority. If a genuine existing ordinary receipt fulfills current policy, prefer it to duplicated publication. No live credentials, namespaces, runner settings, release, issuer or operational mutation.","title":"Prospective documentation-only archive and exact receipt linkage","votes":{"anthropic":"ACCEPT","cursor":"ACCEPT","google":"ACCEPT","openai":"ACCEPT","xai":"ACCEPT"}}],"validator_slate_sha256":"241d06044430b9e285c93de499e27523091f2be89d667366367c3f358da5273e"}
```

| Provider | Served identity or recorded gap | A1 | A2 |
|---|---|---|---|
| anthropic | claude-fable-5-2 | ACCEPT | ACCEPT |
| openai | Requested gpt-6.2-astra. Structured response does not attest one served model. | ACCEPT | ACCEPT |
| google | gemini-4-neon-xhigh | ACCEPT | ACCEPT |
| cursor | Composer 2.5 | ACCEPT | ACCEPT |
| xai | Grok 4.7 256K Extra High | ACCEPT | ACCEPT |

## A1 · Exact source-only ordinary admission for CI #370

Conditionally support exact immutable CI #370 head fd5f9c4fd1939d7a304554df5722e4e77d9327b2, base 68facb416c6751408061ff15701f895fd6cd79a6, tree e4241e4c2a3be32f8ebcad44fb4bf7a6109f8e34. Only source/code/tests/docs for exact GitLab casing in registry gateway, runner promotion broker, and project2 REST+SSH backend, with GitHub issuer/numeric IDs/workflow unchanged. Installed production CLI storage at @verjson/ci/bin differs from the staged private @verjson/ci-entrypoint package. The supported normal promotion broker is blocked by the active release hold in the exact built package. Keep the current production URL and all active services, with no alternate entrypoint, hold bypass or partial rollout until the separate supported broker delivery under ADR0062 / CLI #187 is qualified. No live rename, config, installation, restart, credential, runtime authority, signing, publication, Nexus or private consumer changes. The operator-observed display name changed while the path still reads Verjson. Old-case source controls on unrelated project2 candidate/private release remain held and require separate authority. Before source admission, require fresh exact-hosted non-admission CI including actual ready checkout and current candidate, verified policy and permissions, an authentic ordered exact-head citation tied to an immutable verJSON/.github policy archive, followed by the exact-head independent review receipt. Use any genuinely sufficient pre-existing approved evidence if available. A2 is one potential way to transport this exact board record and does not add another GitHub gate. No GitLab production job is an admission prerequisite. Then require bound App SUCCESS, current CLEAN and canonical assert exit0/true, then ordinary no-admin/no-auto matched-head merge; hold on drift or unavailable evidence. Board not live activation and cannot itself post or merge.

## A2 · Prospective documentation-only archive and exact receipt linkage

Prepare a canonical record only for existing CI #30 and #370, with immutable head/base/tree, private state-backed v2 ledger, five actual votes, four evidenced served identities with explicit gap if any, and validated fence run and digest. Require separate owner scope and exact authorized path/readback, genuine independently reviewed protected archive admission and byte proof before source #370 cites it. Existing archive-only self-head transport precedent does not transfer authority; allow no prospective self-head citation unless separately expressly authorized under current policy solely for the documentation transport. Never post, push, merge or publish on this board's authority. If a genuine existing ordinary receipt fulfills current policy, prefer it to duplicated publication. No live credentials, namespaces, runner settings, release, issuer or operational mutation.

## Prior-round adjudication

The chair adopts the distinction that A2 and any GitLab production job cannot satisfy or add to the GitHub source gates. The canonical Gate R script at bb1a764 scripts/ci-gate/arb-consensus-citation.sh:107–113 expressly requires the exact ordered citation to resolve an immutable policy-repo chair archive. Removing that requirement would contradict governing policy. A1 does not depend on the separately proposed A2 transport if another valid exact-head governed basis is available. Runtime rollout holds and all ordinary source checks stay unchanged.

## Evidence and communication contract

The earlier candidate was invalidated before any ballots. No earlier review or vote is counted. This assessment pin is exact corrected source fd5f9c4fd1939d7a304554df5722e4e77d9327b2 and tree e4241e4c2a3be32f8ebcad44fb4bf7a6109f8e34. Two corrections document independently observed staged runtime: the privately packaged normal promote-broker command returns release-held with exit 2, with no listener, despite the gateway's successful local configuration check. Production URL and coordinated slot activation stay HOLD until supported delivery is qualified under ADR0062/CLI #187. No alternative entry point, disabling hold, publication, credential, runner, infrastructure, or live source change is approved. Live readback distinguishes changed display name and unchanged stored path, while detached case-only model semantics are fixture-only. Required hosted CI means the actual GitHub source candidate on latest authorized base, not a GitLab production job. Unanchored technical review remains evidence without a human or independent GitHub identity claim.
Remote draft #370 was independently read at exactly fd5f9c4f on 68facb41 with no auto-merge request. The authenticated #30 work-start anchor 7110305436 and unchanged policy manifest independently verified as baseline eligible for this corrected source case. The separate exact-head technical source review passed. Account identity is shared, with no per-agent GitHub identity represented.
The chair independently checked the stable GitHub case-fold helper and exact regression test at tools/release/github-identity.mjs:21 and tools/registry-gateway/github-identity.test.mjs:79. The changed GitLab-only case gate keeps numeric-ID, issuer, audience and immutable GitHub workflow checks distinct. No provider is asked to infer live feature readiness from the empty local GitLab fixture. Its on-instance update service can separately deny a rename with native image or group npm assets; this remains an untested production condition and is moot while broker delivery is held.

The state-backed v2 ledger kept voters distinct, recorded failed calls and retained uncertainty. No communication schema change is indicated. Raw transcripts and operational data remain private. No per-agent GitHub identities are asserted.

## Standards grounding
- status: verified
- reason: work_start_baseline
- config_origin: installed
- config_provenance: installer-default
- source: verJSON/verjson-docs:governance/architecture-review-context.json@main
- config_sha: 57932dc678cb089a99c19e6eb3aba78329933eab
- manifest_sha256: eb6c9b7db1f24ef1b03b980380bd3a73ca639f37d53b1c46fe07e5c7c0e97943 (resolver-manifest-v1)
- binding_sha256: f4e8bf263d16ca6a2a3776f226847fcbb27b183a608a18339c46aaf5bdbd5cf8 (resolver-binding-v1)
- canonical_complete: true
- baseline_eligible: true
- eligibility_reason: work_start_baseline
- work_started_at: 2026-11-23T18:32:21Z
- baseline_observed_at: 2026-11-23T18:11:21Z
- sources:
  - engineering-standards VerJSON/verjson-agents@d60772e6beb2e54f600322d2cdf71c0b4c1c0dd1:CANON.md [git_commit, available]
  - code-style VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:agents/code-style.md [git_commit, available]
  - ci-tiers VerJSON/.github@bb1a764758edb1d25c1bcd5ea90becebc9471a81:docs/ci-workflow-tiers.md [git_commit, available]
  - node-workflows VerJSON/.github@bb1a764758edb1d25c1bcd5ea90becebc9471a81:docs/node-workflows.md [git_commit, available]
  - protected-reviews VerJSON/.github@bb1a764758edb1d25c1bcd5ea90becebc9471a81:docs/ruleset-conformance.md [git_commit, available]
  - merge-admission-decision VerJSON/.github@bb1a764758edb1d25c1bcd5ea90becebc9471a81:docs/decisions/0257-merge-admission-requires-the-exact-head-review-receipt/README.md [git_commit, available]
  - merge-admission-runbook VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:agents/merge-admission.md [git_commit, available]
  - delivery-workflow VerJSON/verjson-agents@d60772e6beb2e54f600322d2cdf71c0b4c1c0dd1:rules/workflow.md [git_commit, available]
  - infrastructure-overview VerJSON/.github@bb1a764758edb1d25c1bcd5ea90becebc9471a81:docs/infrastructure/README.md [git_commit, available]
  - runner-routing VerJSON/.github@bb1a764758edb1d25c1bcd5ea90becebc9471a81:docs/runner-routing.md [git_commit, available]
  - runner-lanes-decision VerJSON/.github@bb1a764758edb1d25c1bcd5ea90becebc9471a81:docs/decisions/0040-runner-lanes-and-admission-axes/README.md [git_commit, available]
  - platform-composition VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:architecture/platform-composition.md [git_commit, available]
  - capability-catalog VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:catalog/packages.md [git_commit, available]
  - security-practices VerJSON/verjson-agents@d60772e6beb2e54f600322d2cdf71c0b4c1c0dd1:rules/security.md [git_commit, available]
  - security-policy VerJSON/.github@bb1a764758edb1d25c1bcd5ea90becebc9471a81:SECURITY.md [git_commit, available]
  - dependency-principles VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:architecture/dependency-principles.md [git_commit, available]
  - ownership-boundaries VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:architecture/ownership-boundaries.md [git_commit, available]
  - decision-placement VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:governance/decision-placement.md [git_commit, available]
  - agent-message-contracts-decision VerJSON/verjson-agents@d60772e6beb2e54f600322d2cdf71c0b4c1c0dd1:docs/decisions/0036-versioned-schema-contracts-for-agent-messages/README.md [git_commit, available]
  - agent-message-contracts-adoption VerJSON/verjson-agents@d60772e6beb2e54f600322d2cdf71c0b4c1c0dd1:contracts/agent-messages/adoption.json [git_commit, available]
  - agile-ai-manifesto VerJSON/agile-ai-manifesto@15d91cb19be0b3889165b7a7ca7bb905afbcd831:public/index.html [git_commit, available]
  - tda-review-guide VerJSON/verjson-docs@57932dc678cb089a99c19e6eb3aba78329933eab:governance/technical-design-authority.md [git_commit, available]
- gaps: none
