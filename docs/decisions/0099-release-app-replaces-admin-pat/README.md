# 0099 — A dedicated release App replaces the admin PAT

> **2026-08-25 identity note:** ADR 0138 records the existing App's organization-neutral
> `release-authorization` slug, unchanged App/installation IDs and permission envelope,
> and its live ruleset bypass bindings. Historical names below remain unchanged.

- **Date:** 2026-08-13
- **Issue:** [Verjson/.github#329](https://github.com/Verjson/.github/issues/329)
- **Related:** [Verjson/.github#762](https://github.com/Verjson/.github/issues/762)
- **Supersedes:** ADR 0052

## Context

ADR 0052 restored canonical releases by passing `ORG_ADMIN_TOKEN` to the one job
that atomically pushes an immutable snapshot commit and its exact tag. It proved
that Contents write is necessary but does not bypass `main-protection`. The PAT
worked because its administrator identity bypassed the ruleset, but its authority
was far wider and longer-lived than one repository release required.

The organization now has a dedicated `verjson-release-authorization` GitHub App.
Its repository permissions are only Contents read/write and implicit Metadata
read. It has no Pull requests, Actions, Administration, or organization
permission, and it is an always-bypass actor on the exact organization
`main-protection` ruleset. The organization variable `RELEASE_APP_CLIENT_ID`
contains its `Iv...` client ID; `RELEASE_APP_PRIVATE_KEY` contains its private
key.

The App installation and both organization credential values are available to
the Verjson repository fleet. The owner explicitly chose this convenience-first
scope so canonical packaging repositories do not need manual admission whenever
one is added. That choice changes the threat boundary: any compromised trusted
workflow able to read the organization-wide private key could request an
installation token for another repository on which the App is installed.

## Decision

The canonical reusable release workflow mints its own short-lived installation
token with the audited immutable
`actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1`
pin. Every mint supplies exactly:

- the App client ID and private key;
- `owner: ${{ github.repository_owner }}`;
- `repositories: ${{ github.event.repository.name }}`; and
- `permission-contents: write`.

The pinned action's v3 contract deprecates legacy `app-id` and recommends
`client-id`. The workflow rejects an empty or all-numeric legacy ID, then
delegates the full client-ID grammar to that pinned action. In particular, it
does not assume an undocumented length or character set; the action's own dotted
`Iv1.0123456789abcdef` fixture remains supported. The checkout persists only
that minted token for the final atomic
push. The reusable contract no longer accepts `push_token`, and generated
callers no longer receive `ORG_ADMIN_TOKEN`; they pass only
`RELEASE_APP_CLIENT_ID` and `RELEASE_APP_PRIVATE_KEY`.

Repository binding in the canonical workflow is a defense against accidental or
future generator drift, not a cryptographic limit on the organization-wide
private key. A separate trusted workflow holding that key could ask for a wider
token. Selected-repository App installation and secret visibility would remove
most of that exposure but reintroduce per-repository provisioning. A central
token broker that derives the repository from an authenticated workflow identity
could retain convenient adoption with a stronger boundary, at higher operational
complexity. Neither is adopted here.

A permanent manual canary proves the authorization boundary before a production
release. It has no user-controlled repository, ref, or version inputs, runs only
from the default-branch revision through the existing trusted organization lane
and `VERJSON_LANE_FALLBACK` contract, and fixes its remote target to the
otherwise-absent `develop` branch plus a run-unique SemVer
prerelease tag. Organization ruleset `main-protection` (ID `18098028`) explicitly
targets `develop`, so an atomic push there exercises the same ruleset and named
App bypass as the canonical default-branch push without mutating `main`.

The canary creates an isolated local history and fragment, then invokes the exact
checked-in `scripts/changelog.py release` path to produce the snapshot commit and
annotated tag. It records a successful push before verifying both remote refs,
retains the run URL, App slug and installation, commit, tag, ruleset and proof
boundary in the Actions step summary, and cleans up under `always()` only after
both refs still resolve exactly to this run's commit and tag object. Cleanup is
one atomic deletion and then proves both refs absent; a mismatch is preserved for
investigation instead of deleting someone else's ref.

On 2026-08-13, the first two live attempts while completing issue
[#329](https://github.com/Verjson/.github/issues/329) exposed a persistent-runner
failure mode: checkout resolved the required default-branch SHA, but the expected
workspace file was absent. The canary therefore materializes
`scripts/changelog.py` from that exact commit's Git object into its run-unique
temporary directory with replacement objects disabled, then executes the
materialized copy. This restores the existing checked-in-CLI invariant without
trusting residual workspace contents or replacement refs and without weakening the
dispatch SHA binding.

This is strong live evidence for the exact organization ruleset and App bypass,
but it is not evidence about a rule scoped only to the default branch or the
default branch ref itself. The production release path retains its own
default-branch and immutable-snapshot guards.

## Consequences

- Release authorization is short-lived, repository-scoped at mint time, and
  limited to Contents write instead of an administrator PAT.
- The App's named ruleset bypass remains mandatory; Contents write alone still
  receives GH013.
- Organization-wide installation and credential availability make adoption
  automatic but preserve the trusted-workflow cross-repository risk described
  above.
- Consumers must regenerate both `release-node` and `contract-test` at the same
  immutable contract commit. Handwritten partial migration is unsupported.
- Closing the repository work does not by itself prove live authorization. A
  successful manual canary receipt is the operational evidence boundary for the
  shared ruleset and App bypass.

## Amendment (2026-08-13) — the Actions token returns to read-only (#784)

ADR 0038's 2026-08-01 amendment granted the release job's `GITHUB_TOKEN`
Contents write because that token supplied the checkout credential used by the
final atomic push. This ADR moved that push to the App token but initially left
the obsolete job grant in place.

This amendment supersedes only ADR 0038's obsolete token-source requirement
that the release job and caller grant `GITHUB_TOKEN` Contents write. It does not
supersede ADR 0038's requirement for a Contents-write release credential,
default-branch execution, immutable snapshots, or release serialization.

The release workflow and generated snapshot caller now grant their
`GITHUB_TOKEN`s only Contents read. The App-mint action authenticates from the
client ID and private key, requests its own repository-scoped Contents-write
installation token, and the release checkout persists that token for the push.
The separate canonical-contract checkout is the only remaining consumer of the
job token and requires only Contents read. This restores least privilege without
changing release behavior or the App's required ruleset bypass.

`scripts/changelog-release-permissions.test.sh` and
`scripts/changelog-release-app-token.test.py` pin both token boundaries, and the
generated adopter contract test rejects a snapshot caller that restores the
obsolete write grant.

## Amendment (2026-08-14) — every `~DEFAULT_BRANCH` ruleset carries the release bypass (#803)

The original decision named `main-protection` as the App's authorization
boundary. That was necessary but incomplete. A canonical release pushes its
verified snapshot commit directly to the default branch, so every organization
ruleset whose branch conditions include `~DEFAULT_BRANCH` evaluates that push.
Required-status-check and required-workflow rulesets can therefore reject a
release even when `main-protection` correctly names the App.

Issue [#803](https://github.com/Verjson/.github/issues/803) exposed this latent
failure on the first enforced adopter release after the newer rulesets were
created. The release transaction failed atomically, leaving no snapshot commit,
tag, or consumed fragment. Read-only inspection then found the same omission on
`core-checks-actions`; it remains part of #803 rather than a separate issue.
Issue [#731](https://github.com/Verjson/.github/issues/731) may change a required
context on one of these rulesets, but neither work item blocks the other's
policy invariant.

The release App's `Integration:4583107` identity in `always` mode is now required
on **every organization branch ruleset whose `ref_name.include` contains
`~DEFAULT_BRANCH`**, irrespective of its other conditions, rules, enforcement
mode, or existing bypass actors. Adding that one actor must preserve the complete
reviewed ruleset preimage: all other bypass actors, rules, conditions, target,
and enforcement remain unchanged.

This is an exact GitHub selector-token contract, not a semantic claim about every
rule that might affect a repository's default branch. The automated requirement
matches only `target: branch` plus a literal `~DEFAULT_BRANCH` include. It does
not classify `~ALL` or an explicit ref such as `refs/heads/main`: `~ALL` is a
different policy surface, while an explicit branch may or may not be the default
across the repositories selected by the rule's other conditions. Any such rule
that applies to a releasable repository still needs the release bypass, but its
applicability requires separate author review. Ruleset authors expressing the
canonical default-branch contract use `~DEFAULT_BRANCH` so this check can enforce
it without guessing repository semantics.

`scripts/org-ruleset-conformance.py` enforces the invariant without holding a
mutation path. It paginates the organization ruleset listing, reads every
ruleset detail with explicit GET requests, validates the bypass, rule, and
condition response shapes, and only then evaluates the actor requirement. Any
pagination, API, JSON, schema, duplicate-ID, or detail mismatch fails closed.
Diagnostics name only public ruleset identities and never print response bodies,
credentials, secrets, or variable values.

The schedule is default-branch event-SHA-bound, grants its Actions token only
Contents read, and runs on the trusted organization lane. Its no-argument command
resolves the policy beside the event-SHA script; inherited runner environment
cannot redirect it. The explicit `--test-policy` path exists only for isolated
tests, and the workflow contract rejects both that argument and a policy-path
environment binding.

The audit invokes only one exact wrapper shape: `gh api --hostname github.com
--method GET --paginate --slurp`, against the organization ruleset listing and
the IDs returned by that listing. Tests replace `gh` entirely and reject method,
hostname, pagination, argument, or endpoint drift.

### Residual credential (2026-08-15)

A metadata-only inventory of organization Actions secret names found no dedicated
credential with organization Administration read. No secret or variable value was
read. `ORG_ADMIN_TOKEN` therefore remains the scheduled audit's explicit residual:
it is broader than this GET-only task, but it is the only available credential
whose name and existing policy establish the required organization access.

Replace that binding once a credential exists with only organization
Administration read (plus unavoidable metadata), is available only to
`Verjson/.github`, and successfully proves both paginated ruleset listing and
per-ruleset detail GETs. The migration must update the scheduled-workflow contract
and organization secret-scope policy together; until all criteria hold, an
unproven narrower token would turn the audit into a fail-closed outage rather than
reduce authority safely.

This repository check is durable authoring-time evidence, not authorization to
repair live state. A live correction remains a separately reviewed sensitive
operation: capture and compare the full preimage, add only the required actor,
and verify every preserved actor, rule, condition, target, and enforcement field
afterward.

The operator-facing interpretation and local verification boundary are recorded
in [`docs/ruleset-conformance.md`](../../ruleset-conformance.md).

## Amendment (2026-08-22) — the release-artifact build job's isolation is now contract-tested (#975)

The `release-artifact` caller mode (#975) added a `build` job that runs
adopter-owned, potentially third-party `scripts/release-build.sh` on
caller-chosen runners. The generator's own template already emitted that job
with least privilege — `permissions: contents: read` and only `RELEASE_VERSION`
as step `env:`, never a secret — matching this ADR's boundary that only the
minted App installation token, scoped inside `snapshot`'s delegation to
`changelog-release.yml`, may carry Contents write or see
`RELEASE_APP_PRIVATE_KEY`.

An independent review after #1004 merged found the generated `contract-test`
never checked that shape: it verified the `build` job's `needs:`, `if:`, `ref:`,
hook executability, and artifact-upload pin, but not its `permissions:` block or
whether its steps referenced `secrets.*`. A hand-edited consumer caller that
escalated `build`'s permissions to `contents: write`, or injected
`RELEASE_APP_PRIVATE_KEY` into the build step's `env:`, passed the generated
contract-test with exit 0 — the exact class of generated/actual divergence this
suite exists to reject, and the same failure mode the #784 amendment above
already covers for the snapshot job's `GITHUB_TOKEN` grant.

`contract-test` mode now asserts the extracted `build_job`'s `permissions:`
block is exactly `contents: read`, and that no `secrets.*` context appears
anywhere in the job — a blanket ban rather than a per-secret denylist, since the
build hook has no legitimate need for any secret today. This restores the
invariant this ADR already establishes; it does not change the App's scope, the
minting path, or which job may hold the credential.

## Amendment (2026-10-19) — the credential guards no longer fail open on large job blocks

The snapshot guard (#784) and the build-job guard (#975) above each piped the
comment-stripped job block from `sed` into `grep -q` under `pipefail`, behind a
`!`. `grep -q` exits on its first match. With the forbidden reference early in a
block larger than a pipe, `sed` was still writing, died of SIGPIPE, and the
pipeline's status 141 was inverted into success. A caller that forwarded a
credential from `snapshot` therefore passed the generated contract test with
exit 0. A `build` job that referenced a secret passed this guard as well, and
was rejected only by the later `#1712` Python scan (ADR 0220).

Both guards now capture the stripped block into a variable and grep a
here-string. A failed capture aborts the suite under `set -e`, and no pipe
writer remains to be killed. `scripts/ci-gate/changelog-caller-contract.test.sh`
has one case for each guard. Each case places the reference before a MiB of
YAML and requires that guard's own message. Both cases failed against the
previous generator. This restores the invariant recorded above. It changes no
credential, scope, or job boundary. Adopters receive the fix when they
regenerate at a contract SHA that contains it.

That fix concerns only block size. Review then found that the snapshot guard
was incomplete in a way that has nothing to do with size. It matched a fixed
list of names after a comment strip that also cut text following a `#` inside a
quoted value. It therefore missed index-syntax context access, an expression
after a quoted `#`, and secrets read under a name the list did not include.
Successive reviews of this PR showed that the #1712 expression scan behind both
jobs had gaps of its own. A line-wise comment strip misread `#` inside block
scalars, inside multi-line quoted scalars, and after escaped quotes. The object
pattern missed an object filter over the `github` context. And because the scan
reads text without decoding YAML, a double-quoted escape could spell an
expression that YAML decodes but no raw-text scan sees. The controls are now as
follows.

**Pinned whole (generator-determined).** Neither job contains content the
adopter writes. The generator writes each from one definition, and the contract
test pins each against that same definition.

- **Snapshot job:** `release_snapshot_job`, which all three release caller
  modes call. Every field is pinned: `needs`, `if`, `uses`, `secrets: inherit`,
  `permissions`, and the full `with:` mapping.
- **Release-artifact `build` job:** `release_artifact_build_job_for`. Every
  field is pinned: name, gate, permissions, step sequence, `uses`, env values,
  and run bodies, with or without the approved private dependency restore. The
  one substituted part is the runner matrix. Each entry there must be a runner
  selector the generator's own grammar admits, followed by its
  `dependency-index`, in order. The dependency acquisition job was already
  pinned the same way.

Each comparison skips blank lines and full-line YAML comments, which existing
callers and `docs/changelog/README.md` put beside the generated wiring. A `#`
line inside a block scalar is content, not a comment, so it is compared. One
uniform indentation shift of the job body is tolerated, because it changes no
YAML value. Everything else must match the generator byte for byte. Callers are
byte-identical before and after this change in every release mode and option
set checked.

**Scanned and parse-checked (the rest of the caller).** The verify job carries
adopter-dependent inputs, such as package directories and runner lanes, and is
pinned step by step by the existing #1712 and #1717 checks rather than whole.
The expression scan covers the verify, snapshot, build, and acquisition jobs and
reads raw lines. It matches the token context, the `secrets` context, and the
`github` context object, whether read whole, by index, or through an object
filter. A parse rule covers the whole release caller file. It refuses any YAML
that would decode to something other than its raw text: a backslash anywhere outside block-scalar bodies and
full-line comments, or a quoted scalar left open at the end of its line. Block
scalars do not process escapes. With both refused, every flow scalar decodes to
its own raw text, so what the scan reads is what Actions evaluates. No generated
caller contains either form.

**Residual.** No residual is known for the escape and comment classes anywhere
in a release caller, because the parse rule covers the whole file. The rule is
deliberately narrower than YAML: a hand-written caller that uses escapes or
multi-line quoted scalars is refused rather than read. The `publish` job holds
the publication credentials by design, so it is neither pinned whole nor
covered by the expression scan. It keeps its existing per-field checks, and
this amendment does not change them.

## Rollback

Reverting the #803 implementation removes the scheduled conformance signal but
does not alter any live ruleset or revoke the App. That is a monitoring rollback,
not an authorization rollback.

Revert the implementing pull request and repin consumers. That restores the
temporary `ORG_ADMIN_TOKEN` contract and its broad authority; it is an emergency
compatibility rollback, not the preferred steady state. Removing the App from
the ruleset bypass list or rotating its key immediately stops future App-backed
releases without rewriting released history.
