# Deployment GitHub transport boundary

The generated `scripts/container_deployment_transport.py` supplies authenticated
release-manifest retrieval, representative GitHub canary dispatch, and
read-only runner-host evidence through `@verjson/cli-cloud@1.1.0`'s
`runner-host-evidence` API. The controller uses the transport for baseline,
capacity, and post-update observations. Mocked tests establish protocol behavior;
they do not establish live deployment or rollback readiness. Production host
credentials and trust pins remain operator-owned prerequisites.

## Request and result contract

Invoke the generated module with `--request <json-path> --output <new-path>`.
The request is strict JSON; unknown, duplicate, missing, stale, and malformed
fields fail closed. `validate_request` is the authoritative schema:

| Fields | Required binding |
| --- | --- |
| `schemaVersion`, `operation` | Version 1; `manifest`, `probe`, read-only `host-export`, or parent-owned `deployment-receipt` |
| `attemptId`, `deploymentContractCommit`, `configDigest`, `planDigest` | Deployment run/attempt, immutable contract SHA, admitted configuration digest, approved plan digest (nullable only before plan approval for evidence reads). Omitted for `deployment-receipt`. |
| `action`, `rollbackOfAttempt` | Deploy with null predecessor, or rollback naming a different run/attempt. Omitted for `deployment-receipt`. |
| `fleetSelector`, `lane` | Separate fleet and lane identifiers, never interchangeable. Omitted for `deployment-receipt`. |
| `issuedAt`, `expiresAt` | Whole-second UTC timestamps; at most 30 minutes and not expired |
| `github` | Exact repository name/ID; App/installation IDs for App-minted roles. `deployment-receipt` binds repository name/ID only and authenticates with the job `GITHUB_TOKEN`. |
| `release` | Immutable historical signed-source repository, asset ID, raw manifest digest, variant/image digest, source commit/main ref, canonical signer workflow and immutable signer commit. Omitted for `deployment-receipt`. |
| `probe` | Exact runner ID/name/label, unique transaction UUID, workflow ID, reviewed canary tag and commit |
| `hostExport` | Project, DigitalOcean context and SSH-key selector, exact runner set, freshness limit, observation purpose, and selected runner for post-update evidence |
| `receiptDigest`, `purpose`, `sourceAttemptId`, `retainedReceiptAuthority` | `deployment-receipt` only: canonical receipt digest; `rollback` or `resume`; optional source attempt or retained authority locator |

Every operation carries the complete transaction request, including manifest
reads. Preplan evidence reads bind the admitted configuration with a null
`planDigest`; probe dispatch always requires the approved plan digest. The result binds its canonical `requestDigest`. Manifest results preserve
`manifestBytes` verbatim, verify its selected raw digest and parsed release
identity, and invoke `gh attestation verify` with exact repository, source and
reusable signer constraints. The verifier receives only a read token and a fresh
temporary HOME. `github.repository` is the current canonical API repository,
verified through `/repositories/{repositoryId}` against the reviewed stable ID.
`release.repository` is the historical name in the signed source and remains
unchanged in parsed-manifest validation and `gh attestation verify --repo`.
A repository rename must not rewrite historical provenance or rely on a name
alias alone; both identities and the stable ID remain bound in the request. This result is evidence to validate, not an authorization to
mutate a host.

Probe dispatch uses the existing canonical `runner-canary.yml` seven-input
contract and resolves the reviewed tag to the expected commit before dispatch.
It records an exclusive, fsynced adjacent `.dispatch-intent.json` before the
single dispatch POST. The replay baseline and each post-dispatch lookup use a
paginated workflow-run query whose trusted `total_count` must match every
retrieved record, with a maximum of 1,000 records and 40 workflow-run list
requests shared across the baseline and post-dispatch lookups. An incomplete,
over-sized, or over-budget query fails closed; the first 100 records are never
treated as the complete baseline. The transport reserves a lookup before
dispatch and stops polling when the shared request budget is exhausted. The
intent binds the full request, nonce, workflow commit, and pre-dispatch maximum
run ID. Existing output or intent refuses retry.
Polling accepts exactly one matching new run, attempt 1, exact workflow/ref/SHA,
successful representative job, runner ID/name/label, and authenticated artifact.
The ZIP must contain only the fixed receipt filename. Both archive and receipt
digests are verified; the receipt must agree with independent run/job metadata,
the requested release, nonce, timestamps, representative checks and capacity
floors. Receipt equality is type-sensitive: numeric health flags, Boolean run
attempts and floating-point IDs are rejected even when Python would compare
them equal to the expected Boolean or integer. Failed, rerun, replayed, ambiguous, expired or misrouted evidence fails.

An uncertain dispatch or timeout requires parent reconciliation using the saved
intent and authenticated run inventory. There is no automatic redispatch or
automatic cancellation, and this module does not implement transaction resume.
The eventual controller integration must persist and reconcile intents across
resumes before authorizing another probe. `--dry-run` permits only manifest
reads; probe dispatch is rejected before credentials are obtained.

## Authority and provisioning prerequisites

The trusted parent uses separate short-lived App JWTs for the manifest and
probe roles: `VERJSON_DEPLOYMENT_MANIFEST_APP_JWT` and
`VERJSON_DEPLOYMENT_PROBE_APP_JWT`. Host export uses a separate App private key;
the broker mints its short-lived JWT internally. Deployment-receipt retrieval
uses the workflow job `GITHUB_TOKEN` with `actions: read` only — no App JWT and
no installation token. No ambient `GH_TOKEN`, SSH agent, HOME configuration,
DigitalOcean token, or runner-control credential is authority for host
observation or receipt retrieval by the evidence adapter. The broker validates
each live App installation and its exact permissions, then scopes each
installation token to one repository.

| Role | Exact App permissions, including implicit metadata |
| --- | --- |
| Manifest | `metadata: read`, `contents: read`, `attestations: read` on the release repository |
| Probe | `metadata: read`, `contents: read`, `actions: write` on `Verjson/.github` |
| Host export | `metadata: read`, `contents: read`, `attestations: read`, `organization_self_hosted_runners: read` on the observation repository |
| Deployment receipt | Job `GITHUB_TOKEN` with workflow `actions: read`; omitted from the App `PERMISSIONS` table |

The host-export App identity is reviewed configuration; its private key is
`RUNNER_HOST_EVIDENCE_APP_PRIVATE_KEY`. The reusable workflow reads
host credentials from the existing protected `production` environment and maps
them directly to controller steps. The SSH key is written to a mode-0600
private temporary file and passed only as the required
`--read-only-ssh-private-key <path>` argument. The DigitalOcean read-only
configuration and verified known-host pins are isolated in a temporary home.
These values never enter the transport request, receipts, logs, or artifacts.
Missing inputs fail closed. The fleet-write token and runner-control token are
never reused for host observation.

Installation selection must be `selected`; broader App permissions are
rejected even if the proposed token could be narrowed. Existing runner-control
and review-publisher Apps are not substitutes. The generator creates no Apps,
keys, environments, or policies. Operators must provision the reviewed host
identity and observation values before live use. Keep credentials out of
caller mappings and do not use `secrets: inherit`. Audit and remove broader
repository or organization secret copies through staged rollout; environment
binding alone cannot prove secret provenance.

The broker retains App JWTs in its trusted parent process and never forwards an
App JWT or the probe write token to host readers, mutation subprocesses, or
manifest-verification children. Manifest verification receives only its
read-scoped token. HTTP redirects never carry Authorization; failures suppress
credential-bearing bodies, signed URLs, and subprocess stderr. The controller
rechecks each configured runner before retaining an admitted receipt and
invoking a mutating update. Each runner must be online, idle, in the admitted
reviewed group, and carry the configured labels and tools. Workload-owned probe
children receive only the adapter environment allowlist (`PATH`, locale,
temporary-directory, and TLS certificate settings); they receive no `HOME`,
SSH agent socket, deployment CLI locator, provider credential, or GitHub
runner-control credential. The deployment CLI receives its provider
runner-control credentials only in its separate control environment; those
values never reach workload adapters.

## Live acceptance prerequisites

`@verjson/cli-cloud@1.1.0` ships the observation-only
`runner-host-evidence` API tracked by
[verjson-cli-cloud#504](https://github.com/Verjson/verjson-cli-cloud/issues/504).
Issue #1451 wires that API through the canonical transport and controller for
baseline, capacity, and post-update evidence. The broker no longer rejects
`host-export`.

The current runner production environment does not contain the dedicated host
export App private key, read-only SSH private key, DigitalOcean read-only
configuration, or verified known-host pins. Until operators provision and
validate those inputs, the controller fails closed before claiming evidence.
The #1362 consumer exercise and real deployment/rollback acceptance remain
unverified. Do not substitute fleet-write or runner-control credentials, and do
not invent host identities or trust pins.

The transport's unit and contract tests use mocked external boundaries. They do
not prove live GitHub App, DigitalOcean, SSH, host-health, capacity, rollback,
or interrupted-transaction behavior. GitLab remains a future forge adapter
over shared portable mechanics under ADR 0162.
