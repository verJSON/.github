# 0198 — Confine runner host-evidence credentials to the protected deployment environment

- **Date:** 2026-09-21
- **Status:** Accepted
- **Issue:** [#1451](https://github.com/Verjson/.github/issues/1451)

## Context

The controller now uses the supported `@verjson/cli-cloud@1.0.0`
`runner-host-evidence` API for baseline, capacity, and post-update observations.
The API needs an SSH private key, a DigitalOcean read-only context, pinned
known-host data, and a GitHub token with organization self-hosted-runner read
authority. The existing DigitalOcean fleet token and runner-control token have
write authority and are not host-observation credentials.

The reusable deployment workflow runs collection and mutation in its trusted
parent process. GitHub Actions gives a job one environment boundary, so these
controller operations use the existing `production` environment, which already
gates the mutation path. A mutation-free dry run also reads the same host facts
and must cross that protected boundary before the runner credentials are
available. Caller workflows must not receive or forward private keys.

## Decision

- Use a dedicated GitHub App installation for host export. Its exact permissions
  are `metadata: read`, `contents: read`, `attestations: read`, and
  `organization_self_hosted_runners: read`. Configuration supplies reviewed App
  and installation IDs; a generated value is never treated as production
  authority.
- Store `RUNNER_HOST_EVIDENCE_APP_PRIVATE_KEY`,
  `RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY`,
  `RUNNER_HOST_EVIDENCE_DOCTL_CONFIG`, and
  `RUNNER_HOST_EVIDENCE_KNOWN_HOSTS` only in the protected `production`
  environment. The two host credentials must be read-only, and the known-hosts
  value must contain operator-verified pins. Do not copy these values into
  organization or repository secrets or pass them through a caller workflow.
- Bind both dry-run and deployment jobs to `production`. Map the host-export
  secrets only onto controller collection and execution steps. The transport
  mints a short-lived App JWT internally, validates the exact installation and
  permission set, and narrows its installation token to the reviewed release
  repository. The JWT and App private key never enter a request or child
  environment.
- The transport gives the CLI only the read-only GitHub installation token and
  routes the SSH key by a mode-0600 temporary file path. It writes the
  DigitalOcean config and known-host pins beneath an isolated temporary home,
  rejects credential material echoed in CLI output, and removes these files
  after use. Missing or incomplete authority fails closed before a receipt is
  written. Host observation never falls back to
  `DIGITALOCEAN_RUNNER_FLEET_TOKEN` or `GH_RUNNER_CONTROL_TOKEN`.

## Consequences

The code path is testable without host credentials, but no live host evidence is
claimed until operators provision the dedicated App identity and protected
environment secrets, update the reviewed deployment configuration with actual
IDs and host selectors, and verify the host pins. This ADR does not provision
those values or assert that production has them. The existing production
environment binding is a sensitive workflow surface; any later move to a
separate host-observation environment must preserve the same parent-owned
credential boundary and synchronous evidence binding.

## 2026-09-21 implementation follow-up

The host-export App signing key, SSH key, DigitalOcean configuration, and known-host
files now live under the Actions `RUNNER_TEMP` directory, which the runner empties at
the start and end of each job. Missing, relative, or unusable `RUNNER_TEMP` fails closed
before private credentials are written. The controller forwards `RUNNER_TEMP` through
its child-process allowlist so the transport uses the same job-scoped directory. The
transport retains a mode-0700 temporary directory and deletes it on normal return or
failure. The controller also verifies that the host-export request's `planDigest` equals
the canonical digest of the admitted plan before invoking transport. These checks
complete the accepted boundary without changing credential authority or the supported
observation API. Follow-ups #1526 and #1527 are delivered under #1451.

The reusable workflow rejects non-default-branch calls in a credential-free job
before either production-environment job starts. The controller validates all three
review gates and reconstructs a submitted plan from reviewed configuration and
admission evidence before persisting its receipt; a caller-supplied plan digest
alone is not admission authority.

## 2026-09-26 amendment: the generated caller inherits the secret context

**Status:** Accepted. Amends the Decision above; #1451.

The first genuinely CI-driven dry-run of `container-deployment.yml`
([verjson-git-runners run 36279086275](https://github.com/Verjson/verjson-git-runners/actions/runs/36279086275),
2026-09-26) reached the controller with all four `RUNNER_HOST_EVIDENCE_*` variables
empty and failed closed with `read-only host observation authority not provisioned`,
although every one of those secrets has existed in the caller repository's protected
`production` environment since 2026-09-24. The environment's protection rule fired
and was approved, so the job was bound to `production`; its secrets still did not
resolve. That is the mechanism [ADR 0171](../0171-inherited-reusable-environment-context/README.md)
recorded on 2026-09-15 for the App-key jobs, citing
[actions/runner#4453](https://github.com/actions/runner/issues/4453): an environment
binding inside a reusable workflow never resolves the caller repository's
environment secrets unless the caller inherits its secret context.

The Decision above said "do not ... pass them through a caller workflow", and the
generator's contract test enforced it by forbidding any `secrets:` line in the caller.
Read literally, that made the accepted design unrunnable. The intent — the caller
never names, maps, stores, or forwards an individual credential — stands. What
changes:

- `scripts/gen-container-deployment.sh` emits exactly `secrets: inherit` on each
  reusable edge it generates (the deployment caller's `deploy` job and the review
  callers' `produce` job), per ADR 0171's rule that every reusable edge leading to an
  App-key-consuming job inherits. No secret is named in a caller.
- The generated contract test now rejects any `secrets.` reference, any
  `RUNNER_HOST_EVIDENCE_` name, any `environment:` binding, any non-`inherit`
  `secrets:` value, and a caller that lacks the inheritance; the meta-test
  exercises each rejection.
- Storage is unchanged: the four host-evidence secrets live only in `production`,
  are mapped only onto controller collection and execution steps inside the
  reusable workflow, and never enter a request or child environment.

Inheritance widens what the called workflow *could* read to the caller's
repository and organization secrets plus whatever the environment each job binds
exposes. For the `production`-bound jobs that is, as read from the environment on
2026-09-26, the fleet-write DigitalOcean token, the runner-registration App key,
and the four host-evidence secrets; the review App keys live in their own
`runner-deploy-*-review-publisher` environments and are reached only by the
review callers' `produce` job. ADR 0171 accepted that trade-off for the same
reason it applies here: the called workflow is pinned to one reviewed immutable
contract SHA, references secrets only by fixed name on fixed steps, and never
serializes the secrets context or exposes it to PR-controlled code. The narrower
alternative — a named `secrets:` map on the caller — would put every credential
name into the consumer file and still resolve the same values; it adds review
surface without reducing access. Splitting `production` into per-purpose
environments would reduce the inherited set and remains open under
verjson-git-runners#197's Pulumi-managed environment policy.

## 2026-11-18 amendment: operator diagnostics relay only allowlisted forms

[#2250](https://github.com/verJSON/.github/issues/2250) /
[#2251](https://github.com/verJSON/.github/pull/2251). Dry-run
[44253156883](https://github.com/verJSON/verjson-git-runners/actions/runs/44253156883)
hid the doctl auth failure behind `canonical host evidence transport failed`,
and review of the first fix found its denylist could relay an uncaught
exception repr carrying a command line, a signed or credentialed URL, or an
HTTP body into the Actions log. The delivery ARB's second run (Claude and
Codex seats) asked for an allowlist instead.

Every diagnostic that leaves the transport, or that the controller relays
from a child, now fits an allowlisted form defined once in
`scripts/container_deployment_transport.py` and imported by the controller:

- doctl output maps to fixed messages (`doctl access token is required`,
  `doctl authentication rejected (HTTP 401)`, `doctl API request rejected
  (HTTP <status>)`, `doctl probe failed`); doctl's own text is never relayed.
- The CLI's last `✗`-marked error line, an adapter command's first
  `::error::` line, and the transport's own `deployment transport: `-marked
  exit line relay only as short plain prose: no URL, query, assignment, double
  quote, brace, command flag, token-shaped run, credential marker, or line of a
  provisioned secret. An unmarked or nonconforming line relays nothing and is
  never replaced by an earlier line; any traceback withholds the transport's
  output entirely.

This supersedes the "one sanitized CLI/adapter stderr line" relay detail
recorded with [ADR 0280](../0280-host-export-node-cli-stderr-delivery-arb/README.md).
The secret storage, routing and echo-rejection decisions above are unchanged.
Evidence: the controller and transport suites, including a test that runs
the real transport process, plus a mutation table in which removing any one of
27 guards fails a test.
