# 0135 — Observe live generated-caller capability floors with a repository-scoped App token

- **Date:** 2026-08-25
- **Status:** Accepted
- **Issue:** [#933](https://github.com/Verjson/.github/issues/933)
- **Extends:** [ADR 0114](../0114-generated-caller-capability-floor-audit/README.md)
- **Extends:** [ADR 0109](../0109-observe-first-renovate-compatibility-control-plane/README.md)
- **Superseded in part by:** [ADR 0233](../0233-capability-floor-records-provably-absent-callers/README.md) (2026-10-15)

## Context

ADR 0114 deliberately split caller-pin auditing into two stages. Stage A classifies a
trusted pins snapshot against reviewed capability floors using canonical Git ancestry.
It does not discover live consumer pins. As a result, a generated caller can still
remain stale until a human happens to exercise it. Issue #933 records repeated examples
across `toquorum`, `verjson-authn`, `viager-app`, and `verjson-cli-cloud`.

Live discovery crosses repository boundaries and handles an organization credential.
It therefore needs an explicit trust boundary before a workflow is introduced. The
organization already operates the `verjson-renovate-compatibility` GitHub App for an
observe-first compatibility control plane. Its live installation is organization-wide,
has no subscribed events, and has exactly read-only Actions, checks, contents, pull
requests, commit statuses, and metadata permissions. Its workflow credential contract
is `RENOVATE_COMPATIBILITY_CLIENT_ID` plus
`RENOVATE_COMPATIBILITY_APP_PRIVATE_KEY`; the obsolete PAT is absent.

This audit needs only repository contents read. Creating a second App would not reduce
the effective installation reach or permission required for discovery, while adding a
second private key and provisioning contract. Reusing the compatibility observer is
appropriate only if each token is narrowed at mint time to the reviewed repository list
and requests only contents read.

## Decision

Add a scheduled and manually dispatched, report-only Stage B workflow. It reads a
checked-in static allowlist that names every repository, generated caller path,
generator, and expected canonical reusable-workflow target. The list is the sole live
discovery scope. Repositories with no reviewed caller entry are not enumerated or
queried, and live organization repository discovery is forbidden.

The workflow reuses the Renovate Compatibility App without broadening its live
permissions. Before token minting, local validation derives the exact comma-separated
repository set from the reviewed allowlist. `actions/create-github-app-token` mints one
short-lived installation token for exactly those repositories with only
`contents:read`. The private key is delivered only to the mint action and the resulting
token only to the discovery step on the governed `CI_LANE_PRIVILEGED` hosted lane. The
workflow `GITHUB_TOKEN` remains `contents:read`.
No PAT, organization-admin token, write permission, issue creation, dispatch, branch,
commit, pull request, or consumer mutation is authorized.

For each allowlisted caller, discovery resolves the consumer's default branch to an
immutable commit, reads the caller at that commit through the contents API, and records
the source commit and blob identity. Extraction accepts only exact canonical
`jobs.<job>.uses: Verjson/.github/.github/workflows/<expected>@<40-lowercase-hex>`
reusable-workflow targets from a duplicate-key-rejecting YAML parse. Canonical-looking
text in steps, block scalars, inputs, mapping keys, or any other location is a decoy and
terminates discovery; so do malformed or multi-document YAML, aliases, anchors, tags,
ambiguous real targets, missing files, inaccessible repositories, malformed API
responses, mutable or mixed pins, unexpected canonical targets, duplicate allowlist
entries, non-Verjson repositories, and empty scope. Consumer content never becomes
shell syntax or token scope.

The resulting minimal `{repo, generator, pinned_sha}` snapshot feeds ADR 0114's Stage A
unchanged. Stale and unresolvable pins remain report findings, not workflow failures;
discovery and credential failures fail closed. The workflow publishes the snapshot,
source receipts, and Stage A report as an immutable run artifact and renders the report
in the workflow summary. Publication does not write to any consumer or tracker.

## Trust boundaries

- The allowlist is reviewed code on this repository's trusted default branch. It alone
  selects token repositories and caller paths; event payloads and consumer content do
  not influence scope.
- The App installation may cover all organization repositories, but the minted token is
  narrowed to the exact reviewed set and requests only contents read. No new live App
  permission is required or authorized by this decision.
- Consumer default-branch names are untrusted data used only as encoded GitHub API path
  input. Discovery binds each read to the resolved immutable commit before parsing.
- Consumer YAML is untrusted data. It is decoded under a size bound, never executed,
  parsed with safe constructors and duplicate-key rejection, and only real job-level
  reusable-workflow `uses` values may match the exact immutable canonical-call grammar
  and expected target. Canonical-looking scalar decoys fail closed.
- Stage A owns ancestry classification. Stage B does not reinterpret dates, PR numbers,
  tags, branches, or mutable references as compatibility evidence.
- Artifacts and summaries are observation receipts. They grant no mutation or merge
  authority and may contain repository names, paths, and public commit identifiers only.

## Consequences

Caller drift becomes visible on a daily cadence and on demand without granting the
observer a write path. Updating coverage requires a reviewed allowlist change, which is
intentionally less automatic than organization-wide repository enumeration. A private
repository removed from the App installation or an allowlisted caller removed without a
coordinated config update fails the run instead of silently shrinking coverage.

The workflow does not repair stale callers. An owner must adjudicate each report and use
the canonical generator in the repository they are authorized to modify. Unmanaged
consumer repositories remain read-only from this control plane.

## Rollback

Disable or remove the schedule/workflow and retain the last artifact receipt. No
consumer state needs reversal because this design performs no consumer mutation. App
permissions and installation selection are unchanged.

## 2026-10-15 amendment — absent callers and the canonical owner's display case (#1777)

[ADR 0233](../0233-capability-floor-records-provably-absent-callers/README.md)
supersedes the "missing files" condition above. A caller absent from a successful
`.github/workflows` listing at the source commit is now recorded as a non-conformance
finding and discovery continues. Every failed read, including any `404`, still fails
closed.

The exact-target invariant above also matched the owner `Verjson` byte for byte. The
organization login changed display case to `verJSON` on 2026-10-02, and the changelog
generator now emits that case. As a result, a correctly generated caller
(`verjson-observability` `changelog.yml`) read as "no canonical workflow reference",
and a display-case decoy in a step escaped decoy rejection. The owner is now compared
as a case-insensitive ASCII identity through `scripts/github_identity.py` (#1779).
Decoy detection uses the wider Unicode case-insensitive form of the canonical path.
Any canonical-looking spelling must therefore pass the exact grammar and the owner
identity check, so display-case decoys, lookalike owners, and non-ASCII folding
spellings all fail closed.
