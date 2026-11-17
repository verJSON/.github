# Protected runner deployment operations

The deployment contract is a separately dispatched transition from an immutable
release manifest to an existing DigitalOcean runner fleet. It never builds an image,
creates capacity, resizes a Droplet, or accepts a tag. Live adoption and fleet-specific
values belong in the consumer repository; this repository stores no deployment token.

## Generate the adopter contract

Acquire `scripts/gen-container-deployment.sh` from one reviewed 40-character
`Verjson/.github` commit and generate all five artifacts at that same commit:

```sh
scripts/gen-container-deployment.sh workflow <contract-sha> container-deployment.json > .github/workflows/container-deployment.yml
scripts/gen-container-deployment.sh controller <contract-sha> > scripts/container_deployment_controller.py
scripts/gen-container-deployment.sh transport <contract-sha> > scripts/container_deployment_transport.py
scripts/gen-container-deployment.sh preflight <contract-sha> > scripts/container_deployment_preflight.py
scripts/gen-container-deployment.sh receipt-schema <contract-sha> > scripts/deployment-receipt.schema.json
scripts/gen-container-deployment.sh contract-test <contract-sha> container-deployment.json > scripts/container-deployment-contract.test.sh
```

Commit a reviewed `container-deployment.json`. Its fleet selector fixes the existing
lane, project, ordinary canary, deterministic runner inventory, minimum available
capacity, time bounds, runner group, labels, tools, and consumer-owned evidence/probe
scripts. The local CLI selector is exactly `verjson-cloud`; the controller resolves it
only from the immutable acquisition root supplied by the reusable workflow. Run the
generated contract test in CI.

The reusable workflow acquires `@verjson/cli-cloud` from the immutable contract
checkout using the contract's committed npm lockfile. GitHub Packages receives only
the job token with `packages: read`; `npm ci --ignore-scripts` verifies the locked
SHA-512 dependency graph and canonical npm/GitHub Packages URLs before the local CLI
is admitted. The exact Node runtime and setup action are pinned. Each job uses a unique
cache under `RUNNER_TEMP`; an always-run final step removes that cache, the contract
archive, and the extracted dependency tree. The consumer must not install, cache, or
select another deployment CLI.

Configure the consumer's `production` environment to admit protected branches only.
It is a credential and audit boundary, not a human approval gate: no environment
reviewer is required and administrator bypass may remain enabled. Store the
project-scoped `DIGITALOCEAN_RUNNER_FLEET_TOKEN` and
`GH_RUNNER_REGISTRATION_APP_PRIVATE_KEY` only there. Configure
`GH_RUNNER_REGISTRATION_APP_CLIENT_ID` and
`GH_RUNNER_REGISTRATION_APP_INSTALLATION_ID` as repository or environment variables.
Install `org-gh-runner-registration` on selected repositories only, with organization
self-hosted runners read/write. Review those settings
and credential scope as sensitive changes; never print their values.

Commit stable numeric App and installation IDs, check names, and workflow paths under consumer
`container-deployment.json` `reviewAuthority` object. These reviewed-tree values are the
only trust roots; Actions variables cannot redirect them. Generate code, security, AI
adapter, shared trusted publisher, and helper with `gen-container-deployment.sh` modes
`code-review-workflow`, `security-review-workflow`, `ai-review-workflow`,
`review-producer-workflow`, and `review-producer` at the same immutable contract SHA.
The three App IDs and installation targets must differ. In every caller repository,
store each private key only in its matching
`runner-deploy-{code,security,ai}-review-publisher` protected environment. Reusable
workflow environments and secrets belong to `github.repository` (the caller), while the
serialized `job.workflow_repository/ref/sha` fields identify the called reusable workflow.
Generated adapters pin the reusable producer to the exact contract SHA. Analysis has no
publisher credential. An uncredentialed prerequisite verifies the live caller-owned
environment and exact default ref before the environment job can start. GitHub releases
environment secrets when that job starts; an in-job check cannot gate initial release.
The credentialed job therefore relies on GitHub's native protected-branch environment
admission, then rechecks the content-addressed policy for TOCTOU/audit and validates
the parsed `job.workflow_repository/ref/sha` before loading code or minting a token.
Because actionlint 1.7.7's static `job` schema predates those GitHub fields, the workflow
passes only `toJSON(job)` to a nonsecret step, validates the three fields with `jq`, and
exposes bounded outputs. It checks out the validated canonical repository at the
validated called-workflow SHA. Caller `github.workflow_*` and inputs cannot select
producer code. The PR and consumer config remain API data. The
environment-policy digest is SHA-256 over the sorted compact JSON projection
`{id,name,deployment_branch_policy,protection_rules}` with no trailing newline; shell
producers use `jq -j -cS` and deployment uses the same canonical-byte helper. The
machine-readable claim binds repository ID, PR exact
head/tree/diff, immutable producer commit/tree, installation ID, workflow run/attempt.
The AI adapter references a pinned App-owned terminal-green source check on that head.
Dispatch producers from the merged default-branch commit, never a PR ref. They reconcile
the prior unprivileged exact-PR-head reviews into App checks on that deployed commit.
Deployment resolves exactly one associated PR and requires its reviewed
tree to equal the checked-out default-branch tree byte-for-byte. Merge, squash, and
rebase commit IDs may differ; conflict-resolution or unrelated-tree drift fails closed.
The canonical workflow re-fetches the checks, workflow runs, artifacts, actors,
environment policy, and bypass basis through GitHub before admitting mutation.
The environment has exactly GitHub's `branch_policy` protection rule, including its live
ID/node metadata, and no reviewer, timer, or custom rule. This makes
`environmentBypassed: false` observable while administrator bypass remains available.

## Dry-run and deployment

Dispatch with `dry-run: true` first. The mutation-free dry-run binds to the protected
`production` environment, resolves the immutable manifest, and uploads the exact
redacted canary-first host plan. The controller receives only dedicated read-only
host-observation inputs from that environment; it does not receive runner-control
or fleet-write deployment credentials and does not mutate an external system.
Reject any plan with an unexpected host, baseline, signer, source ref,
release-contract pin, label/tool requirement, or capacity floor.

The manifest identity is the bare `sha256:<64 lowercase hex>` digest of the canonical
`release-manifest.json` GitHub Release asset. A registry-qualified reference, image
digest, or tag is not a release identity and is rejected before evidence collection.

The evidence adapter supplies `manifestBytes` as the exact UTF-8 release asset text,
including its original whitespace, escapes, and final newline, alongside the parsed
`manifest` object. Read bytes and decode UTF-8 without newline translation; do not
serialize the object again. The controller hashes these bytes against both the selected
identity and the verified attestation subject, then checks that the parsed document
matches `manifest`. Text is limited to 1 MiB; duplicate keys, non-finite numbers,
invalid UTF-8/JSON, and mismatched objects fail closed. Existing evidence may omit
`manifestBytes` only when compact sorted UTF-8 serialization of `manifest` actually
hashes to the selected asset identity. Plan and receipt digests remain canonical JSON
digests, separate from the released asset's byte digest.

Dispatch the same manifest digest and fleet selector with `dry-run: false`. The job waits
at `production`, re-collects current evidence, restores and validates any retained
append-only chain for the exact run attempt, head, manifest, and plan, or retains a new
revision `0000` before the first mutation. It then calls
`verjson-cloud runner update --only <host>` one host at a time. The
canary's exact routed probe and observation window must pass before a second host starts.
Every later revision binds the canonical digest of its predecessor. A post-update
revision is retained before probing, and each bounded host phase uploads progress. Fleet
timing must leave at least 15 minutes of the 90-minute job unused.

The canary receipt records observation as `pending` after its probe passes and records a
second revision as `passed` only after the full window returns. Cancellation while the
window is pending repeats the bounded observation on resume; it cannot make a later host
eligible.

## Pause, resume, and interruption

Do not cancel a healthy update merely because the observation window is quiet. To pause
before deployment, retain the dry-run plan and withhold the mutating dispatch. Once mutation
has started, a failed drain, admission check, probe, timeout, or operator cancellation
stops the state machine; it never skips to another host.

An interrupted run retains at least its admitted revision. Seal an `interrupted` revision
from that exact attempt before recovery. The evidence adapter must retrieve the complete
contiguous revision chain, exact retained plan, and keyed
`<attempt>/<artifact>@sha256:<artifact-digest>` authority over both.
Resume only when the current fleet agrees with every retained final-fleet entry and the
attempt, checked-out head, manifest, and canonical plan digest are unchanged. The
controller skips only transitions whose update and routed probe are both verified. An
unknown post-mutation state fails closed until live evidence seals it; never restart from
a guessed host or mutable tag.

The protected workflow runs the controller's `reconcile` command after restoring a
retained chain. For each unknown host, the evidence adapter must report its immutable
manifest identity, release, and deployed image digest. Exact selected-release evidence
seals a `reconciled` transition and resumes at probe/observation without another drain;
exact attempt-baseline evidence removes the uncertain transition and permits a new
bounded update only when `releaseManifestBytes` contains the exact UTF-8 asset text,
matches the parsed `releaseManifest` object and the recorded baseline
manifest identity and its reviewed variant index digest exactly matches the live deployed
digest. A missing baseline manifest, any other release, inconsistent manifest, or absent
or unrelated digest stops with an operator-safe error. Preserve the chain and quarantine
the host until evidence is repaired or an independently approved rollback is dispatched.

## Rollback

Rollback is a new, independently approved `production` dispatch with `action: rollback`.
Select the exact manifest identity recorded as `observedDeployedRelease` on the failed or
interrupted attempt and supply that attempt's retained canonical receipt digest. The
trusted parent transport retrieves the prior `runner-deployment-*-{admitted|progress|final}`
Actions artifact and binds `rollbackSource` before host export; the evidence adapter must
not call GitHub for that receipt and never receives `GITHUB_TOKEN` / `GH_TOKEN`. The digest
is never a dispatcher-controlled filesystem path. The controller rejects a predecessor from
another attempt, a changed baseline, or a receipt-digest mismatch. The same canary,
sequential capacity floor, probes, and admission checks apply. If the exact baseline
manifest or evidence is unavailable, stop and escalate; do not rebuild or use a tag as
recovery authority.

## Credential rotation and incidents

Rotate `DIGITALOCEAN_RUNNER_FLEET_TOKEN` and the `org-gh-runner-registration` App
private key in the consumer environment without editing callers
or generated files. Confirm the replacement retains only the named existing-fleet update,
runner admission, and evidence permissions; it must not authorize capacity creation or
unrelated environments. Revoke the old credential after a mutation-free dry-run and a
reviewed environment audit.

For signer, digest, fleet-baseline, transaction-lock, capacity, or rollback-verification
failures, preserve all receipt artifacts and the workflow URL, quarantine the affected
runner from scheduling, and escalate to the runner-fleet owner and security owner. Do not
retry until the retained receipt and live state explain every host. Never include secret
or organization-variable values in an incident record.

## Transport host-export readiness

[The GitHub transport broker](deployment-github-transport.md) retrieves signed
release manifests, dispatches representative canary evidence, and now routes
read-only host observations through `@verjson/cli-cloud@1.1.0`'s
`runner-host-evidence` API. The generated transport remains a parent-owned
capability, not an arbitrary child adapter or a replacement for complete fleet
evidence.

The API blocker [verjson-cli-cloud#504](https://github.com/Verjson/verjson-cli-cloud/issues/504)
is closed; #510 delivered the installed API. Issue #1451 wires baseline,
capacity, and post-update exports through the controller and canonical
transport. The workflow binds the observation steps to the protected
`production` environment and fails closed when required host inputs are absent.

Production still requires operator provisioning and verification of the
host-export App identity/private key, read-only SSH private key, DigitalOcean
read-only configuration, and known-host pins. None are generated or assumed by
this change. Live #1362 consumer exercise and deployment acceptance remain
unverified; never reuse the fleet-write token or runner-control token for host
observation. Until live acceptance completes, related readiness work in #1281,
runner#197, and #629 remains open.
