# 0097 — Extend secretless Node validation to trusted refs

- **Date:** 2026-08-12
- **Issue:** [Verjson/.github#761](https://github.com/Verjson/.github/issues/761)
- **Category:** package credentials / trusted-ref execution — **sensitive class**
- **Status:** Accepted

## Context

[ADR 0086](../0086-secretless-node-pr-validation/README.md) separated private
dependency acquisition from pull-request code, and
[ADR 0095](../0095-bounded-secretless-node-cache-transfer/README.md) reduced the
handoff to exact private cache blobs plus one immutable auxiliary sparse tree.
The canonical mode admitted only same-repository pull requests. Its normal
trusted-ref route instead ran `npm ci` with a package token, omitted the
auxiliary source, and could not reproduce the reviewed rebuild and script plan.

A private consumer needs post-merge and direct-push validation even when branch
protection cannot enforce review. Treating a writer-authorized push as trusted
does not justify running repository lifecycle or consumer scripts with package,
Git, cloud, or OIDC credentials. A handwritten push workflow would also fork the
canonical package allowlist, auxiliary identity, audit, and smoke boundaries.

## Decision

Add an opt-in `secretless-trusted-ref` input to `node-ci.yml`. It reuses the
same acquisition job, exact package and scope policy, lifecycle-script-free
private cache population, 80 MiB identity-bound transfer, optional immutable
auxiliary sparse tree, credential scrub, lifecycle rebuild allowlist, ordered
script plan, and exact-attempt cache handoff as `secretless-pr`.

Keep the event modes mutually exclusive. `secretless-pr` continues to admit only
same-repository `pull_request` events and reject forks. The trusted-ref mode
admits only `push` and explicit `workflow_dispatch`; it rejects pull requests,
`pull_request_target`, schedules, and every other event before package-token
use. A caller must expose PR and trusted-ref execution as separate conditional
jobs and explicitly map only `NODE_AUTH_TOKEN`; inherited secrets are outside
the contract.

The token-bearing acquisition job remains on the isolated lane. It may parse
repository-controlled lock and pin data, but its fixed canonical steps execute
no dependency lifecycle, repository hook, or consumer script. Exact protected
repository variables bind any non-default package policy and auxiliary identity,
so pushed input cannot expand what the token may acquire. The trusted-ref build
job follows the normal trusted runner route or an explicit caller runner, but
checkout credentials are not persisted and package, GitHub, cloud, and OIDC
paths are cleared through the job environment before `npm ci --ignore-scripts`,
approved rebuilds, or consumer scripts.

Bypass stability-days deferral for `push` only. A stale status attached to a
pushed commit cannot suppress post-merge or direct-ref validation. Manual
dispatch retains its existing explicit bypass, while every other event keeps
the eligibility action's prior status-check behavior.

## Consequences

- PR behavior and runner isolation are unchanged unless the new input is set.
- Trusted pushes validate the same private package bytes, auxiliary commit,
  rebuild allowlist, and ordered audit/smoke plan without exposing acquisition
  credentials to repository-controlled execution.
- A repository writer can change code on a pushed ref, but that code runs only
  after credential removal. Package and auxiliary acquisition remain limited by
  protected repository policy and immutable lock or commit identities.
- Callers need two visible event-gated jobs with `contents: read` and
  `statuses: read` permissions for both reusable invocations. The #824
  correction to ADR 0095 removed the former transfer-only `actions: write` grant.
- Secretless modes still reject schema submodules and project `.npmrc` files;
  consumers needing those must use another reviewed canonical contract.

## Adoption

Pin both caller jobs to one 40-hex `Verjson/.github` commit and pass identical
package scopes, package names, auxiliary source, rebuild packages, and script
plan. Use `secretless-pr: true` only in the `pull_request` job and
`secretless-trusted-ref: true` only in the `push`/`workflow_dispatch` job. If the
adoption also advances changelog artifacts, regenerate the workflow, renderer,
contract test, and release caller together with
`Verjson/.github/scripts/gen-changelog-caller.sh` at that same immutable SHA.

## Rollback

Disable `secretless-trusted-ref` to return trusted refs to the existing
credentialed install route, or remove the trusted-ref caller job while retaining
secretless PR validation. Neither rollback changes callers that omit the new
input.

## Amendment — 2026-08-30: isolate candidate runtime caches (#1201)

The protected required-workflow variant treats the verified runtime npm cache as a
read-only baseline, not as a shared working directory. Same-UID digest checks alone do
not make files immutable, so every exact candidate script runs inside a verified
root-owned Bubblewrap namespace. The namespace starts from a synthetic root and mounts
only the system runtime, narrow non-secret host configuration, the exact checked-out
workspace, and that script's fresh cache, home, and temporary directories. Host homes,
runner installation state, sibling workspaces and temporary trees, `/run`, Unix control
sockets, and host devices are absent; `/dev` is synthetic. Tool discovery ignores PATH entries
outside the canonical `RUNNER_TOOL_CACHE` populated by `actions/setup-node`. Every lexical and
resolved ancestor through every selected executable, package-content tree, and mount prefix must be
root-owned with no group/world-writable mode. Runner-owned setup-node caches fail closed: descriptor
pinning prevents prefix replacement but cannot prevent same-UID in-place content mutation. This is
an immutable standard-runner-image admission requirement, not a staging promise from a writable
source. Directory file descriptors bind the validated device/inode identities into the
namespace, closing a rename/swap race between validation and mount. Read-only tool mounts must not
equal or overlap any candidate-writable workspace, runner temporary, cache, home, or temporary path.
The lexical workspace must itself equal its canonical path. Missing or ambiguous tools fail closed,
so a repository-controlled PATH prefix cannot shadow `npm` or `node`. PowerShell is admitted only
through the root-owned, non-writable `/usr/bin/pwsh` link to a versioned
`/opt/microsoft/powershell/<version>` runtime, which is descriptor-bound read-only. Before
entry, the workflow
validates a bounded regular-file
inventory and digests, copies that inventory into the fresh cache, and validates source
and copy again. It redirects both uppercase and lowercase npm cache variables to that
copy. Symlinks, special files, path escape, inventory drift, excess files or bytes, copy
failure, missing or unsafe Bubblewrap, and cleanup failure all fail closed.

Protected candidate scripts are offline. Their Bubblewrap namespace has a fresh network
namespace and the workflow rejects database or cache-service environment state instead
of silently sharing the host network. This is intentionally narrower than the general
`node-ci` service-container contract: database-backed runner canaries and other networked
validation run through separately admitted workflows, not through this untrusted
required-workflow script plan. Tests prove that loopback and link-local metadata are
unreachable. Adding a service endpoint here requires a separately reviewed explicit
proxy/allowlist; shared host networking is not an accepted fallback.

The Bubblewrap PID namespace, shell supervisor, and Python supervisor remove per-script
and root directories on normal completion, script failure, `INT`, and `TERM`. After
every candidate leader exit, the Python supervisor requires its outer process group to
disappear, escalating to bounded `SIGKILL` and failing closed if `ESRCH` is not observed.
The PID namespace also terminates descendants that create a new session; the contract
does not claim that process-group polling alone covers those descendants. The exact
cleanup step sweeps the deterministic root even if in-step cleanup is interrupted, and
the namespace's read-only parent prevents candidate code from renaming that root.
Credential variables remain absent from every candidate process. This prevents one
ordered script from deleting or poisoning cache evidence consumed by a later script
while retaining the exact verified acquisition as the sole source for every isolated
copy.

## 2026-10-15 — build placement superseded by ADR 0234

The trusted-ref build job no longer follows the trusted runner route or a caller runner. [ADR 0234](../0234-route-secretless-trusted-ref-builds-to-the-hosted-lane/README.md) places it on the hosted `CI_LANE_UNTRUSTED` lane with the PR build, because its lifecycle rebuild and candidate sandbox require a GitHub-hosted Linux runner. Event admission, credential scrubbing, and the acquisition boundary in this decision are unchanged.
