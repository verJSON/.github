# 0078 — Container releases promote immutable candidates before protected deployment

- **Date:** 2026-08-08
- **Issue:** [#626](https://github.com/Verjson/.github/issues/626)
- **Follow-ups:** [#628](https://github.com/Verjson/.github/issues/628), [#627](https://github.com/Verjson/.github/issues/627), [#629](https://github.com/Verjson/.github/issues/629)
- **Category:** release authority, production credentials, and runner deployment — **sensitive class**
- **Status:** Accepted
- **Superseded in part by:** [ADR 0144](../0144-exact-head-runner-deployment-review/README.md) for deployment review authorization and credential separation

## Context

Runner images currently move from a merge to a registry publication without an
organization contract distinguishing a candidate, a stable release, and a production
deployment. A mutable tag can therefore look deployable even though it names no
independently authorized release. The operator-driven DigitalOcean rollout also lacks a
portable receipt binding the deployed fleet to the exact attested image set.

This decision resolves [#626](https://github.com/Verjson/.github/issues/626). The
implementation is deliberately split into immutable candidate publication
([#628](https://github.com/Verjson/.github/issues/628)), exact-digest stable promotion
([#627](https://github.com/Verjson/.github/issues/627)), and protected deployment
([#629](https://github.com/Verjson/.github/issues/629)). Consumer adoption remains a
separate change in `Verjson/verjson-github-runner`; this repository does not edit that
consumer.

## Decision

The organization adopts this one-way lifecycle:

```text
pull-request validation
  -> immutable main candidate
  -> explicitly promoted stable release
  -> independently approved production deployment
  -> one canary
  -> sequential fleet rollout or manifest-based rollback
```

Each transition consumes the signed receipt from the preceding transition and emits a
new retained receipt. No transition rebuilds an image.

### Version and image identity

Each consumer commits a reviewed release configuration containing one exact
`nextStableVersion`. It is a `MAJOR.MINOR.PATCH` SemVer greater than the latest stable
release. Changing the next stable line therefore passes through the consumer's normal
pull-request controls; a workflow input or an arbitrary branch cannot select it.
The publisher configuration surface, including the opt-in runner image cleanup setting,
is described by [candidate-config.schema.json](./candidate-config.schema.json).

A successful build of the consumer's default branch has the SemVer prerelease identity
`<nextStableVersion>-rc.<run_id>.<run_attempt>`. The GitHub run ID and attempt are numeric,
so the identity is valid SemVer and unique across reruns. The candidate receipt binds
that identity to the default-branch commit, workflow identity, run, complete declared
variant/platform matrix, OCI index digests, platform manifest digests, and provenance.
Candidate artifacts and receipts are append-only. A rerun may reconcile byte-identical
state but must fail on any identity or digest mismatch.

Stable SemVer is created only by an explicit dispatch from the consumer's protected
default branch. The requested stable version must equal the candidate's recorded
`nextStableVersion`. Promotion copies or retags the exact candidate digest set and emits
an immutable release manifest conforming to
[`release-manifest.schema.json`](./release-manifest.schema.json). It does not invoke a
Dockerfile, Bake, or build command. Every declared variant and platform is verified
before the signed release manifest is published; a missing, extra, unattested, or
divergent digest fails the release without partial promotion. Registry aliases never
constitute promotion: the signed, complete manifest is the sole authoritative stable
release record.

The candidate schema validates historical schema-v2 and v3 records and current v4
candidates; the release schema validates historical v2 and current v3 release manifests.
Candidate v4 requires a publication timestamp and verified destination receipts on every
image. GAR receipts also bind the index provenance referrer and each platform SBOM
referrer to the recorded attestation digests. Historical v2 and v3 candidates remain
readable, but the release validator requires v4 and directs callers to rebuild older
candidates before promotion. The candidate schema rejects v3-only fields when
`schemaVersion` is 2. Promotion keeps the validated candidate digest in the release
manifest and projects destination receipts to their stable provider, repository,
digest, expiry, and verification fields. GAR evidence-referrer records remain bound by
that candidate digest instead of being copied into the unchanged release schema.

Convenience tags such as `candidate`, `stable`, a major line, or `latest` may remain for
humans and development tools. They are mutable aliases, are excluded from manifests,
and are never accepted as release or deployment inputs. Automation resolves only
manifest-recorded `sha256` digests.

### Independent deployment and rollback

Production deployment is a separate explicit dispatch that accepts an immutable release
manifest identity, revalidates its signature/provenance and complete digest set, and
waits at the consumer repository's protected environment named `production`. The
credential-bearing reusable job names that canonical environment directly; neither a
caller input nor a manifest can override it. Release authority does not imply deployment
authority. A merge and a stable promotion cannot bypass that human-visible environment
approval.

The generated contract and its preflight fail closed unless all of these are true:

- the dispatch ref is exactly the protected default branch and the `production`
  environment admits protected branches only;
- the environment has at least one required reviewer, prevents self-review, and disables
  administrator bypass;
- the completed environment review names a reviewer different from the workflow
  dispatcher; and
- only the reusable workflow's post-approval mutation step receives the consumer
  environment secret named `VERJSON_RUNNER_DEPLOY_TOKEN`. The generated caller cannot
  pass a token, `secrets: inherit`, an environment name, or an alternate secret name.

The preflight verifies those settings through the GitHub API before the reusable job can
reach production credentials. The adopter contract test rejects a caller/workflow shape
that weakens any invariant, and the deployment receipt records the dispatcher, actual
reviewer, workflow run, and environment protection rule used. The fail-closed policy is
executable as `scripts/container_deployment_preflight.py`; #629 wires normalized GitHub
API evidence into that pinned validator rather than reimplementing the decisions in
workflow shell.

`previousRelease` in a release manifest is the immutable predecessor in the stable
release line. It is not fleet state and never changes after promotion. Every deployment
attempt independently observes the fleet and persists an `admitted` receipt containing
that `observedDeployedRelease` baseline **before the first `verjson-cli-cloud` mutation**.
Only after durable receipt storage is confirmed may the canary start. Progress and final
receipts are append-only revisions of that attempt; each repeats the baseline and records
completed runner transitions, binds the preceding revision's canonical digest, and is
rejected if it changes the attempt identity, checked-out head, canonical plan digest, or
observed baseline. The controller restores only a complete contiguous chain returned with
keyed attempt/artifact authority and only when live fleet evidence agrees with its final
state. A failure retains a
`failed` receipt, and an abrupt stop
that cannot finalize still retains the pre-mutation `admitted` receipt. Recovery first
seals an `interrupted` revision from that retained baseline, without touching a runner;
that revision is the authoritative rollback input. All revisions conform to
[`deployment-receipt.schema.json`](./deployment-receipt.schema.json). An unexpected fleet
baseline is drift, not an implicit upgrade path.

After approval, the deployment controller selects one explicitly configured canary from
the target environment. It admits only an online, idle runner, updates it through
`verjson-cli-cloud`, and runs the consumer-owned representative probe. The observation
window and success criteria are committed consumer inputs. Only a passing canary permits
the remaining runners to update one at a time in a deterministic order, with readiness
and the same representative probe checked after each update. The first interruption,
timeout, digest mismatch, or probe failure stops the rollout; it never skips a failed
runner or continues in parallel. A verified update is retained before its probe; a failed
or timed-out probe records the selected actual release, while indeterminate mutation
evidence records the runner state as unknown rather than claiming the predecessor.
An unknown state becomes runnable only through an append-only reconciliation revision
bound to live manifest, release, and image-digest evidence. Selected-release evidence is
bound to the admitted target manifest and variant digest; baseline evidence must provide
exact released manifest bytes whose recorded identity and reviewed variant index digest
match the live host exactly. A passing canary remains observation-pending in retained
state until the complete window is durably recorded; resume repeats a pending window and
cannot advance to the next host.

Rollback is an independently approved deployment of the exact
`observedDeployedRelease` from the failed or interrupted **attempt being recovered**,
never from the last successful deployment. The rollback receipt binds that attempt ID
and its canonical receipt digest (UTF-8 JSON with sorted keys and no insignificant
whitespace), and `scripts/container_deployment_preflight.py` rejects
the rollback unless `selectedRelease` equals the bound attempt's observed baseline. A
partial B-over-A failure therefore selects A even if A's older successful receipt had
observed X. Rollback neither rewrites B's release manifest nor rebuilds an image; it
revalidates A's retained manifest and provenance and follows the same canary and
sequential protocol. If the attempt baseline or exact manifest is unavailable,
automation fails closed; a mutable tag is not a recovery mechanism.

### Ownership and credential boundary

- `Verjson/.github` owns reusable policy, schemas, generated callers, contract tests,
  attestation gates, approval sequencing, receipt formats, and rollback protocol.
- Consumer repositories own Dockerfiles, Bake targets, registry image names, variant and
  platform matrices, smoke probes, next-stable configuration, target environments, and
  thin generated callers pinned to immutable contract commits.
- `verjson-cli-cloud` owns runner update mechanics, idle/readiness enforcement, digest
  verification on the target, and its safety invariants. Workflows orchestrate that CLI;
  they do not reproduce its behavior in shell.
- Each consumer's protected GitHub environment owns its narrowly scoped production
  credential and approvers. Its `VERJSON_RUNNER_DEPLOY_TOKEN` authorizes only the named
  consumer fleet operations required by `verjson-cli-cloud`, not account-wide resource
  creation or unrelated environments. `Verjson/.github` stores no broad DigitalOcean
  credential, reusable workflows receive no organization-wide production secret, and
  candidate or release jobs receive no deployment credential.

The environment, credential scope, and production approver rule are mandatory human
review points for every adopter. Changing them is sensitive work and requires a consumer
ADR or a superseding organization decision. Creating capacity or incurring DigitalOcean
spend remains an explicit operator action and is outside this contract.

### Generated callers and contract versions

Adopters do not copy workflows. Each of the three lifecycle stages is a separately
versioned contract: candidate publication, container release, and runner deployment.
Their thin callers are generated from an immutable `Verjson/.github` contract commit and
pin the reusable workflow at that same commit. A candidate-contract update does not
silently move a deployment contract; adopters regenerate and review each selected stage.

The implementation follows the canonical changelog precedent: use
`Verjson/.github/scripts/gen-changelog-caller.sh` for the repository's changelog
workflow, renderer, and contract-test outputs, all at one immutable contract SHA.
Handwritten substitutes are forbidden. The container delivery follow-ups add a dedicated
generator whose output set contains the selected thin workflow caller, a manifest
renderer/validator, and an adopter contract test. Every file in one generated output set
records and enforces one immutable contract SHA; a mixed pin or edited generated file
fails the contract test. The generator itself never writes consumer credentials or
environment approvers.

Schema validation is necessary but cannot express uniqueness by object property. Every
candidate and release therefore also runs `scripts/container_release_manifest.py`
against the protected default branch's reviewed consumer configuration. It rejects
duplicate image variants, duplicate `(os, architecture, variant)` platform identities,
and any missing, extra, or substituted repository, variant, platform, provenance
predicate, or builder identity. The generated contract test must invoke this pinned
validator; a schema-only check is not a valid implementation.

The release manifest has its own integer `schemaVersion`. Incompatible receipt changes
increment that version and reusable workflows reject unsupported versions. Workflow
contract pins and manifest schema versions are related evidence but are not conflated.

### Audit retention

Candidate receipts, release manifests, deployment receipts, and rollback receipts are
uploaded as immutable attestations and retained according to the consumer's release
retention policy. The stable release keeps its manifest alongside the release record;
deployment and rollback receipts record the environment, approver-visible workflow run,
selected manifest, per-runner before/after digest, probe result, and timestamps. Retention
must cover the currently deployed release and its previous verified release, so rollback
does not depend on an expired workflow artifact.

### Amendment — 2026-10-09 (#1726): retain candidate evidence with stable releases

Stable promotion uploads the exact signed candidate artifact ZIP as `candidate-manifest.zip` on the GitHub Release and verifies the asset when resuming an existing release. The ZIP digest remains `candidateManifestDigest`, preserving the complete candidate receipt, including GAR referrer inventories, beyond the Actions artifact retention window. The pre-credential reconciliation hook must leave all pre-existing untracked and ignored release inputs byte- and mode-identical. This does not extend registry retention or allow promotion after a candidate destination expires.

## Threat model

### Amendment — 2026-08-09: restart-safe stable promotion (#627)

Stable promotion is dispatch-only and consumes an immutable candidate-manifest digest.
All Git, changelog, release, matrix, provenance, and registry state is preflighted before
the first alias write. Alias writes follow deterministic variant order and may be retried
only when every existing alias already names its recorded candidate digest; a divergent
or unreadable alias fails closed for operator quarantine. The immutable release manifest,
not mutable aliases, is the deployment input. Promotion never rebuilds an image and never
invokes deployment. The release job uses the separately scoped `VERJSON_RELEASE_TOKEN`
because the repository ruleset does not grant `GITHUB_TOKEN` release authority. Candidate
admission binds the successful source run, protected source ref and commit, reusable
workflow signer and immutable contract digest through GitHub artifact and OCI attestation
verification; the reviewed config is read from that candidate source commit. Git commit
and annotated tag publish atomically. A retry reconciles exact committed manifest,
snapshot, tag, aliases and GitHub Release attachment, while any divergence stops before
the next mutation.

| Threat | Required control | Failure behavior |
| --- | --- | --- |
| Mutable or replaced tag | Deploy only schema-validated `sha256` digests from a verified release manifest; tags are display aliases | Reject tag inputs and digest disagreement |
| Digest substitution | Bind variant, platform, digest, source run, and provenance identity in signed receipts; resolve registry state again at each transition | Stop before promotion or mutation |
| Untrusted caller input or arbitrary branch | Require the exact protected default branch; validate caller repository, workflow pin, schema, and the complete matrix against reviewed configuration with the semantic validator | Reject before credentials or write permission are available |
| Broad or leaked production credential | Keep a least-privilege credential only in the consumer production environment and grant it solely to the deployment job after approval | Candidate/release jobs cannot authenticate to production |
| Environment bypass or self-approval | Preflight requires protected-branch admission, a required reviewer distinct from dispatcher, self-review prevention, and disabled admin bypass; the generated caller cannot override the environment | No credential-bearing job without independent environment approval |
| Duplicate matrix identity | Semantic validation rejects duplicate variant and platform tuple keys before comparing the exact reviewed matrix and provenance identities | Reject before promotion or deployment |
| Partial promotion | Verify the complete declared matrix before publishing stable references and make publication restart-safe against exact existing state | Fail without a partially authoritative release |
| Rollout interruption | Persist the attempt's observed baseline before mutation, update one runner at a time, retain append-only progress, and stop on the first non-success | The admitted/failed attempt receipt remains a recoverable authority |
| Rollback drift | Bind rollback to the failed/interrupted attempt receipt and require its selected release to equal that attempt's observed baseline; revalidate provenance and each target digest | Fail closed if receipt identity, fleet state, evidence, or exact artifacts disagree |
| Compromised reusable caller | Generate callers and contract tests together at one immutable contract SHA; reject edits and mixed pins | CI blocks the consumer change |
| Compromised update implementation | Keep safety invariants in `verjson-cli-cloud` and require its provenance enforcement before deployment adoption | #629 remains blocked until the upstream invariant exists |

The highest-blast-radius review points are the production environment binding, secret
scope, manifest-to-digest validation, and the call into `verjson-cli-cloud`. These retain
a human gate even after the generated contract is established.

## Migration plan for `Verjson/verjson-github-runner`

1. Land #628 and generate the candidate caller at an immutable contract SHA. Commit the
   reviewed next-stable line and declared matrix. Wire the schema plus semantic validator
   into the generated contract. Preserve the current digest receipt as
   source evidence while dual-writing the new candidate receipt; disable any appearance
   that a merge publishes stable state. A merge may publish only an immutable candidate.
2. Compare the new receipt's complete variant/platform digests with the existing build
   receipts before retiring the old receipt shape. Do not infer equivalence from tags.
3. Land #627 and generate the release caller at its immutable contract SHA. Exercise an
   explicit promotion of one retained, attested candidate in a non-production namespace,
   proving that the build does not run and an incomplete matrix publishes nothing.
4. After the upstream `verjson-cli-cloud` provenance-enforcement dependency is available,
   land #629 and generate the deployment caller. Configure the protected production
   environment with protected-branch-only admission, self-review prevention, required
   independent reviewers, and admin bypass disabled. Scope `VERJSON_RUNNER_DEPLOY_TOKEN`
   to the named fleet, then configure the canary, observation window, probes, and
   deterministic fleet order in the consumer repository.
5. Perform a protected canary deployment, sequential rollout, induced-stop exercise, and
   approved rollback to the recorded previous manifest. Retain all receipts. Only then
   remove the legacy operator path and old receipt reader.

No step changes an unmanaged consumer repository from this ADR PR. Each adoption step is
reviewable and reversible before the production dispatch; the production environment
approval remains an explicit hold on the live mutation.

## Consequences

Merges can continue producing useful candidates without claiming release or deployment
authority. Stable versions become coherent promotions of already attested bytes, and a
production fleet can be explained from retained manifests and receipts. The cost is a
three-stage generated contract and additional retained evidence. Promotion and rollout
take longer because completeness, approval, canary observation, and sequential probes
are deliberate gates.

GHCR remains the initial registry. This decision neither selects a replacement registry,
reimplements runner updates, nor grants permission to add fleet capacity or spend.

## Amendment (2026-08-09) — candidate publication contract (#628)

The candidate stage is implemented by the reusable
`.github/workflows/container-candidate.yml` and the generated three-file adopter set
from `scripts/gen-container-candidate.sh`: caller, semantic validator, and contract
test. The reusable workflow separates a credential-free pull-request build from the
default-branch publication job. The latter alone receives `packages: write` and
`id-token: write`, and only the reviewed repository's `ghcr.io/<owner>` namespace is
admitted.

Each successful default-branch run publishes commit-addressed and unique SemVer `rc`
identities, records index and platform digests, embeds BuildKit SBOM/provenance, emits
GitHub OIDC provenance, and retains one attested candidate manifest. Derived variants
name a reviewed base variant and are bound to the exact base index digest assembled in
the same run. The validator rejects partial matrices, duplicate identities, mutable or
malformed digests, wrong repositories/refs/workflows, mixed release-line inputs, and
cross-namespace publication. Stable promotion and deployment remain the separate #627
and #629 stages; this amendment grants neither authority.

The source-commit tag is reconciled under commit-scoped workflow concurrency: an absent
tag is created from the built index digest, an identical tag is accepted, and a
different digest or an inconclusive registry read fails closed without replacing it.
The manifest records the attestation ID returned by GitHub's provenance action and
derives its signer identity from the pinned reusable workflow; reviewed configuration
selects the expected predicate but is not treated as observed attestation evidence.

## Amendment (2026-08-16) — attested, race-safe release publication (#847)

Candidate schema version 2 binds a GitHub attestation identity to the BuildKit-produced
SPDX 2.3 document for every platform digest. Publication jobs and generated callers
receive only `attestations: write`, `id-token: write`, and the read or package scopes
their specific work requires. Release admission verifies the exact repository, reusable
workflow and contract digest, source ref and commit, predicate type, and subject digest
for image provenance and for every platform SBOM. Missing or substituted evidence stops
before alias mutation.

Release schema version 2 records the dispatch source commit. Releases are serialized by
consumer repository and version; after deterministic alias writes, the complete alias
set is read again and must match the selected candidate digests before the manifest is
attested or any Git commit, tag, or GitHub Release is published. Retries reconcile the
same manifest and fail closed on divergent Git, registry, receipt, or release state.

The canonical release generator emits the thin caller, promotion and manifest
validators, artifact extractor, attestation verifier, and adopter contract test at one
immutable contract SHA. The reusable workflow attests `release-manifest.json` before
Git publication. A downloaded release asset is verified with values recorded inside it:

```bash
manifest=release-manifest.json
repository="$(jq -r .source.repository "$manifest")"
contract="$(jq -r .release.workflow.contractCommit "$manifest")"
source_commit="$(jq -r .release.sourceCommit "$manifest")"
gh attestation verify "$manifest" --repo "$repository" \
  --predicate-type https://slsa.dev/provenance/v1 \
  --signer-workflow Verjson/.github/.github/workflows/container-release.yml \
  --signer-digest "$contract" --source-ref refs/heads/main \
  --source-digest "$source_commit"
```

## Amendment (2026-08-16) — parent-index evidence binding (#851)

The generated candidate caller grants `actions: read` so GitHub can satisfy every
called job's declared permission without a reusable-workflow startup failure. The
SBOM-signing job receives `packages: write` for `push-to-registry`, as do the base and
derived image publication jobs for their intentional registry writes; build-only
pull-request jobs retain read-only source authority. A checked-in reusable-call canary
keeps GitHub's own workflow parser and permission negotiation on the changed contract.
The canary explicitly selects an isolated GitHub-hosted runner so a shared self-hosted
workspace cannot turn permission/syntax evidence into a checkout race. This test-only
routing does not remediate the production runner isolation defect tracked in
`Verjson/verjson-github-runner#155`.

BuildKit evidence is resolved from the parent OCI index rather than a child platform
manifest. Deployable descriptors must equal the reviewed platform matrix exactly.
Every `unknown/unknown` descriptor must be a uniquely bound attestation manifest for
one reviewed platform digest, and every such manifest must contain exactly one SPDX
layer. Missing, duplicate, ambiguous, unbound, partially unknown, or non-attestation
descriptors fail before signing or manifest assembly. Evidence descriptors are
validated separately and never enter the deployable platform list.

## Amendment (2026-08-14) — protected runner deployment contract (#629)

The deployment stage is implemented by the reusable
`.github/workflows/container-deployment.yml` and the generated five-file adopter set
from `scripts/gen-container-deployment.sh`: a thin dispatch caller, controller,
authorization preflight, receipt schema, and contract test. Every generated artifact
binds one immutable deployment-contract commit. The caller passes no secret or
environment override; the reusable mutation job names `production` and exposes its
environment-scoped credential only to the controller execution step.

Receipt schema version 3 binds each revision to its ordinal, checked-out head, canonical
plan digest, deployment-contract commit, canonical immutable manifest identity, fleet
selector, ordinary canary identity, failure evidence, and verified-or-unknown final fleet
state. The admitted revision remains durable before mutation, and every progress revision
binds the canonical digest of its predecessor. The controller owns ordering, capacity,
worst-case timing with a 15-minute job margin, one-host progress phases, state transitions,
authority-checked resume, strict schema and semantic receipt checks;
`verjson-cli-cloud` continues to own drain, update, digest verification, and admission
mechanics. Consumer-owned evidence and representative-probe adapters are reviewed Python
argument vectors invoked without a shell.

Failure stops before an unstarted host. Recovery does not silently restore a guessed
predecessor inside that failed dispatch: rollback is a new protected dispatch bound to
the failed or interrupted attempt's observed baseline and receipt digest, then follows
the same canary and sequential protocol. This preserves independent approval while
satisfying the requirement that rollback use the previous verified digest without a
rebuild. Dry-run has no production credential or mutation adapter and uploads its exact
redacted plan for inspection; mutable tags and raw
digests are rejected at admission, workflow concurrency prevents overlap, and the fixed
CLI argument shape has no capacity or spend-increasing operation.

### Clarification (2026-08-18) — canonical manifest asset identity

The protected deployment input and every retained receipt use the bare
`sha256:<64 lowercase hex>` identity of the canonical `release-manifest.json` GitHub
Release asset. They do not accept the preceding registry-qualified OCI-manifest form.
This matches `verjson-cli-cloud`'s #258 admission boundary: the CLI resolves the exact
asset only from `Verjson/verjson-github-runner`, re-hashes its bytes, verifies the
canonical release signer, protected source ref and commit, and verifies each selected
image's provenance and SBOM before cloud inventory or runner mutation. Keeping the
orchestrator and mutation authority on the same identity prevents a reviewed plan from
being reinterpreted as a raw image digest at execution time.

### Clarification (2026-09-09) — preserve the attested release asset bytes (#1279, #629)

The canonical release writer publishes sorted, indented JSON with a final newline;
`actions/attest-build-provenance` attests that exact file. The controller previously
compared a compact serialization's digest with the asset identity, violating the
existing exact-byte boundary. Authenticated release-asset and attestation inspection
for `Verjson/verjson-github-runner` v0.2.1 (asset 532627568) confirmed subject digest
`sha256:4f5bb96e1fe07f7b56cfe124206ed85c4e59b9715b3b4e18b3054d890dd1ad32`;
compact reserialization instead produces
`sha256:cb3d413b928468fee715a7545567854455ab1b5fde88f701d0454ede2dae7532`.
The checked-in regression fixture preserves that published asset unchanged.

Restore the invariant by accepting `manifestBytes`, the original UTF-8 asset text,
alongside `manifest` in trusted evidence. Bound text to 1 MiB, hash the original bytes,
reject ambiguous or malformed JSON, and compare canonical representations of the
decoded object and `manifest` (not language-level object equality). Keep source
repository, protected ref, signer workflow, contract pin, expiry, and attestation
subject checks. Baseline reconciliation uses the equivalent `releaseManifestBytes`
and `releaseManifest` pair. Selected-release reconciliation, resume, and rollback
retain the same raw asset identity; canonical plan and receipt hashing is unchanged.

Existing compact-byte evidence remains valid only if its exact canonical serialization
already hashes to the recorded asset digest. This compatible extension avoids changing
historical releases or substituting a normalized digest for a verified attestation.
Adapters must preserve downloaded bytes rather than reconstructing them. The byte
contract is reusable for future GitLab-hosted workers; no provider-specific controller
generalization is needed for this correction. The defect is tracked by
[#1279](https://github.com/Verjson/.github/issues/1279); delivery remains tracked by
[#629](https://github.com/Verjson/.github/issues/629) and the runner adopter
[#197](https://github.com/Verjson/verjson-git-runners/issues/197).

### Clarification (2026-08-18) — immutable deployment CLI acquisition

The reusable workflow obtains the deployment CLI from the same immutable contract
commit. That commit contains an npm v3 lockfile fixing `@verjson/cli-cloud` and every
transitive dependency to canonical `registry.npmjs.org` or `npm.pkg.github.com` HTTPS
URLs and SHA-512 integrity values. Every non-root lock entry is validated before npm
runs; missing integrity, weaker hashes, Git/file sources, plain HTTP, alternate hosts,
credentials, ports, query strings, and fragments fail closed.

Acquisition uses an exact Node runtime and action pin, only `packages: read`, and
disables lifecycle scripts, audit, and funding requests. Its npm cache is unique to the
job run and attempt beneath `RUNNER_TEMP`. The caller's literal reusable-workflow SHA
and `contract-ref` must match before the contract archive is fetched, and the
controller accepts the executable only within that immutable acquisition root.
Always-run cleanup removes the archive, extracted dependency tree, and isolated cache
after dry-run or production completion. A missing package, changed tarball, lock drift,
absent binary, or cleanup path escape stops without selecting another CLI or retaining
cross-job package residue.

## Amendment (2026-08-09) — private candidate dependency boundary (#690)

Candidate consumers may declare an exact `privateNodePackages` allowlist of
`@verjson/*` names. A separate trusted acquisition job binds that allowlist to both the
reviewed pull-request base configuration and a lockfile v2/v3 whose entries use only
canonical npm or GitHub Packages URLs with exact SHA-512 integrity. Forks cannot request
private packages, project-controlled `.npmrc` files are rejected, and acquisition uses
an isolated npm environment with lifecycle scripts disabled.

The acquisition credential is passed explicitly to that job alone. It is never a Docker
build argument, BuildKit secret, cache, or environment value. The job exports only a
credential-free `node_modules` tree whose artifact identity includes the workflow run,
attempt, and lockfile digest. Every build job checks that digest against its checked-out
lockfile before exposing the tree as the `verjson_node_modules` named context. This
permits exact private dependencies without moving package credentials or lifecycle
execution into pull-request-controlled Docker commands.

## Amendment (2026-08-15) — quota-independent private dependency handoff (#830)

The credential-free `node_modules` tree crosses the trusted acquisition boundary through
the repository cache service, not organization artifact storage. The acquisition job
creates a 256-bit random exact-run-attempt key and exposes it only as a downstream job
output. Build jobs restore only that complete key, use no prefix fallback, and fail closed
on a miss before checking the embedded lockfile digest. A stable workspace-relative path
keeps the cache version identical across runners, while job-local cleanup removes the
restored transfer directory on every step outcome.

This preserves the #690 credential boundary while isolating transient handoff capacity
from the organization artifact quota. Cache entries are immutable and auto-evict from the
consumer repository's quota; rollback pins callers to the preceding contract SHA.

## Amendment (2026-08-16) — private dependencies remain optional (#839)

The trusted Node acquisition job runs only when the reviewed candidate configuration
requests at least one private package. With an empty or absent `privateNodePackages`
allowlist, the candidate workflow requires no lockfile, npm installation, transfer
cache, base-branch configuration, or `node_modules` build context. Build jobs treat the
skipped acquisition as an intentional credential-free path while still requiring
successful configuration preparation.

A non-empty allowlist retains every #690 and #830 boundary: same-repository pull
requests, exact equality with the reviewed base configuration, lockfile and integrity
validation, isolated lifecycle-free acquisition, exact cache restoration, digest
verification, and unconditional cleanup. First adoption is therefore supported only
without credential use; introducing private packages remains a separately reviewed
second step.

## Amendment (2026-08-17) — stable empty dependency context for non-Node adopters (#837)

Every candidate Docker build receives the `verjson_node_modules` named context. Build
jobs create it as an empty, credential-free directory before any optional private-package
cache restoration. Non-Node first adopters can therefore use the same Docker build
interface without a package graph, lockfile, npm execution, or credential. When a
reviewed non-empty allowlist requests private packages, the existing exact cache restore,
lock-digest validation, credential isolation, and cleanup boundaries populate that same
context and continue to fail closed.

## Amendment (2026-08-16) — generated callers separate validation from publication (#869)

The canonical candidate generator emits distinct reusable-workflow caller jobs for
pull-request validation and trusted default-branch publication. Public-only candidate
configurations give validation only `actions: read` and `contents: read`, and forward
no package token on either event path. Publication alone receives package write,
attestation write, and OIDC authority, guarded by an exact main-branch push condition.

Configurations with an explicitly reviewed non-empty `privateNodePackages` allowlist
retain the existing acquisition boundary: validation may read packages and receives
only `NODE_AUTH_TOKEN`, while publication receives the same acquisition token plus its
publication permissions. Generated contract tests bind both event-specific permission
maps and reject public-only callers that expose package credentials. This restores the
credential-free pull-request invariant recorded above without weakening private-package
admission or granting stable release authority.

The repository's reusable-call canary applies the same trust split. Its privileged
manual publication job runs only when the dispatch ref is the repository's current
default branch, so a writer cannot select branch-local reusable workflow or candidate
configuration code and grant it package, attestation, or OIDC authority.

## Amendment (2026-08-24) — compact complete platform SBOM predicates (#1047)

The candidate publisher serializes each exact platform's complete SPDX 2.3 document as
compact JSON before handing it to GitHub's attestation action. JSON whitespace carries
no package evidence, but the former pretty-printed serialization pushed otherwise valid
Node and Python predicates over GitHub's 16 MiB input boundary.

Compaction must preserve the entire parsed SPDX object and its exact platform binding;
truncation, package filtering, fallback platform selection, and alternate SPDX identities
remain forbidden. The canonical helper rejects malformed identities and any compact
document still larger than 16 MiB before OIDC signing or registry publication. This is a
bounded encoding correction to the existing evidence contract, not a reduction in SBOM
scope or a new trust decision.

## Amendment (2026-08-24) — release executes the pinned changelog engine (#1013)

The container release workflow now validates its 40-lowercase-hex `contract-ref` and
requires `job.workflow_ref` (the identity of the reusable workflow defining the current
job, rather than the caller-associated `github.workflow_ref`) to equal the exact canonical
`Verjson/.github/.github/workflows/container-release.yml@<contract-ref>` identity before
any checkout. It then checks out only `scripts/changelog.py` from that exact
`Verjson/.github` commit into a separate credentialless path. The terminal release step
executes that pinned copy instead of assuming the consumer has independently vendored a
root `scripts/changelog.py`.

This restores ADR 0038's immutable engine boundary for documented container-release
adopters. A clean generated adopter therefore has every release component it needs,
while a branch, tag, malformed ref, valid-but-different SHA, wrong workflow source/path,
consumer-local script, persisted checkout token, or contract checkout placed after
App-token minting fails the semantic contract. The
Release App remains confined to the terminal protected Git and GitHub Release operation;
this correction neither widens its Contents-only permission nor changes the job token's
separate package authority.

## Amendment (2026-08-24) — attested exact-SHA retry reuse (#1055)

A rerun must inspect the immutable `sha-<source commit>` identity before invoking a
builder. When it is absent, publication follows the normal build, attestation, and
immutable-tag reconciliation path. When it exists, the publisher may skip rebuilding
only after proving that the digest is a complete OCI index for the exact variant and
reviewed platform matrix, including BuildKit's per-platform attestation evidence, and
that GitHub verifies exactly one SLSA provenance attestation for that digest.

Verification binds the attestation to the caller repository, protected source ref,
exact source commit, canonical publication workflow, and executing immutable contract
commit. The repository and tag are derived from reviewed configuration and
`GITHUB_SHA`; neither is a dispatch or caller-selected retry input. Derived variants
also require their verified BuildKit provenance to contain exactly one resolved base
material whose repository and digest equal the base variant's immutable exact-SHA
identity, and bind their receipt to that same digest. Missing, ambiguous, malformed,
unprovenanced, partially indexed, platform-divergent, or unreadable state stops before
a mutable alias is written.

After successful verification, a retry may reconcile only its run-attempt candidate
alias to the already immutable digest and emits a deterministic identity for the
verified attestation bundle. The `sha-<source commit>` guard remains append-only: a
fresh build whose digest differs from an existing commit identity is still rejected and
never overwrites it. This makes retries restart-safe without treating reproducible
rebuilding as an identity guarantee or weakening the immutable-tag quarantine signal.

## Amendment (2026-08-25) — live reusable-workflow identity canary (#1059)

The `job.workflow_ref` contract must also be proven against GitHub's live reusable-call
context, not only a simulated job object. A manual canonical canary therefore calls the
exact reviewed container-release workflow revision with a fixed malformed candidate
identity. The expected receipt is deliberately fail-closed: the reusable job starts,
the workflow-identity guard succeeds, and the fixed malformed candidate fails the
existing format check before artifact API access, App-token mint, package mutation, or
release work. The reusable workflow's pinned checkout, Buildx setup, and registry-login
actions precede that format check, so retained receipts must show those exact reviewed
steps and must not claim the probe was credentialless. The registry credential remains
the repository job token; the supplied Release App private key is not consumed because
the token-mint step is unreachable.

The canary exposes no dispatch inputs, pins the `uses` target and `contract-ref` to the
same immutable SHA, passes only the role-based Release App credential contract, and has
no steps of its own. Its semantic contract rejects a mutable or mismatched revision,
caller-selected candidate data, a syntactically usable candidate identity, inherited
secrets, and any additional job surface. A live run is accepted only with retained
run/job logs showing the guard passed and the fixed next-step rejection; an overall
successful run, artifact API request, App-token mint, registry mutation, or release
mutation is a canary failure.
## Amendment (2026-10-02, #1682) — compare deployment-review repository identity safely

The reusable deployment-review producer accepts GitHub's ASCII case variants for
the canonical `Verjson/.github` repository in `job.workflow_repository` and its
receipt validation. Restrict the value to an ASCII owner/repository slug before
case folding; retain the exact producer workflow path, immutable 40-character
workflow SHA, and publisher-environment policy digest. This corrects API casing
without allowing a different repository or changing review authority. The
producer tests cover mixed-case identity and reject a Unicode lookalike.

The identity correction is tracked in [#1682](https://github.com/Verjson/.github/issues/1682).
