# Organization changelog contract

This contract is the canonical changelog policy for Verjson repositories.

## Unreleased changes

Ordinary pull requests add one independently authored Markdown fragment,
`NEXT/YYYY-MM-DD-issue-<identity>-<slug>.md`. The `-issue-` segment is a fixed
literal for every fragment; only the identity after it varies with the metadata
key:

```
NEXT/2026-07-30-issue-249-adopt-immutable-snapshots.md    # issue: 249
NEXT/2026-07-30-issue-20260730T184500Z-tidy-fixtures.md   # id: 20260730T184500Z
```

The fragment starts with YAML-style metadata understood by
`scripts/changelog.py`:

```markdown
---
date: 2026-07-30
issue: 249
impact: minor
title: Adopt immutable changelog snapshots
---

Describe the user-visible change and link its issue, ADR, and pull request when
applicable.
```

### Metadata support is a property of the immutable pin

The README describes the current contract, but consumers execute the engine at
their chosen immutable `contract_ref`. Published tag `v2.2.0` accepts only the
four base keys; it predates `refs`, `summary`, and `component`. No newer release tag
exists as of 2026-08-07, so do not infer current metadata support from
`v2.2.0`.

The migration guide recommends immutable commit
`413bf03b179ff3028e6c7da5551aaa44562ddd8d`. Its engine accepts every key below and
its generator emits the standalone `pr-gate` caller required by the current rollout:
<!-- recommended-contract-pin: 413bf03b179ff3028e6c7da5551aaa44562ddd8d -->

<!-- contract-pin-metadata:start -->
| Metadata key | Required | Supported by `v2.2.0` | Supported by recommended pin |
| --- | --- | --- | --- |
| `date` | yes | yes | yes |
| `issue` | exactly one of `issue` / `id` | yes | yes |
| `id` | exactly one of `issue` / `id` | yes | yes |
| `title` | yes | yes | yes |
| `refs` | no | **no** | yes |
| `summary` | no | **no** | yes |
| `component` | no | **no** | yes |
| `impact` | yes for new fragments | **no** | yes |
<!-- contract-pin-metadata:end -->

`scripts/contract-pin.test.sh` executes the engine from that exact commit
against issue-form and id-form fragments carrying every advertised key. Adding
a key to this table without moving the recommended pin to an engine that
accepts it makes CI fail.

An entry may also carry `refs`, a comma-separated list of issue numbers it links
but does not own:

```markdown
---
date: 2026-07-30
id: 20260730T090000Z
refs: 13
title: Add authenticated public gateway transport
---
```

Identity must stay unique — that is what makes fragments conflict-free — so only
one entry per issue may carry `issue:`. Several entries can still be work on that
issue, and because only issue-form identities render a `#n` back-link, the rest
used to lose their release linkage silently. `refs` separates linkage from
ownership so every one of them links the issue. It renders after the identity:

```
_Date: 2026-07-30; id:20260730t090000z; refs #13_
```

`refs` is optional and additive: a fragment without it renders exactly as before.
`summary` optionally overrides the lead paragraph used in a released snapshot;
it does not change the full running-log entry.

### Independent component streams

A repository that releases independently versioned packages may add an optional
component identifier:

```markdown
---
date: 2026-08-07
issue: 390
component: python
title: Add Python worker support
---
```

Component names are lowercase 1–64 character identifiers using letters,
digits, dot, underscore, and hyphen; they must start and end with a letter or
digit. They are stream names, not paths.

The default renderer and release select only fragments with no `component`, so
single-package repositories are unchanged and scoped work cannot leak into
their release. Preview or release one explicit stream with
`render-next --component python`, inspect its derived tag with `next-version
--component python --prefix python-v`, or release it with `release --component
python`. An explicit fragment list can narrow that stream but cannot select
across component boundaries. Validation and pull-request consumption checks
still cover every stream. See
[ADR 0070](../decisions/0070-component-scoped-changelog-streams/README.md).

A generated component-specific release caller should make that stream its safe
manual-dispatch default instead of inheriting the root `v`/unscoped selection:

```bash
scripts/gen-changelog-caller.sh release-node "$CONTRACT_REF" \
  --only-package-dir packages/python \
  --default-prefix python-v \
  --default-component python >.github/workflows/release-python.yml
```

The two default options are an atomic pair. The generator rejects missing,
repeated, or malformed values and records the pair in generated provenance so
the contract test can detect later workflow-default drift. Omitting both keeps
the existing root-stream defaults: prefix `v` and no component.

### Release impact

Every new fragment declares `impact: major`, `impact: minor`, or `impact: patch`.
The pull-request validation command compares the base and head revisions and
rejects an added fragment that omits it, naming all three permitted values.
Fragments already present on the base retain the historical patch fallback, so
adoption does not reinterpret old unreleased work. Released `CHANGELOG/`
snapshots remain immutable prose and are never parsed as fragments. Impact is
metadata only and never appears in rendered notes.

A rename within `NEXT/` retains that compatibility only when its canonical date
and identity are unchanged (for example, correcting the slug). Changing the
canonical identity or date creates a new fragment for impact validation; Git's
rename detection cannot turn a new release entry into a legacy one.

The reusable required checks pass a dated migration grace through 2026-08-29
UTC. During that window, branches authored against the implicit-patch contract
remain valid; from 2026-08-30 UTC, newly added fragments must declare impact.
The canonical engine exposes that bounded compatibility seam as
`validate --allow-missing-impact-through YYYY-MM-DD`; callers should not extend
it or implement a second impact policy.

At release time the central engine computes the highest impact among the
selected fragments and requires the exact next SemVer version on that axis.
Ordinary SemVer rules apply before 1.0: `v0.4.2` advances to `v1.0.0` for major,
`v0.5.0` for minor, or `v0.4.3` for patch. Higher, lower, and skipped bumps fail
before any snapshot or fragment is mutated. The first release in a prefix
history establishes its baseline.

Version prefixes define independent histories (`v1.2.3`, `python-v1.2.3`, and
so on). Explicit-fragment releases calculate impact only from their selected
fragments. Consumers pass versions and selectors to `scripts/changelog.py`;
they must not carry their own impact parser. See
[ADR 0071](../decisions/0071-changelog-impact-governs-version-bumps/README.md).

Inspect the exact tag `release` will accept without changing the repository:

```bash
python3 scripts/changelog.py next-version --repo-root .
python3 scripts/changelog.py next-version --repo-root . \
  --component python --prefix python-v
python3 scripts/changelog.py next-version --repo-root . \
  --fragment NEXT/2026-08-07-issue-390-python.md
```

`--fragment` may be repeated and has exactly the same path and component-stream
rules as `release`; naming the same fragment twice is rejected before either
command mutates release state. `--prefix` selects the existing snapshot
namespace and defaults to `v`; it is deliberately independent of `--component`,
preserving the caller-owned namespace decision in ADR 0070, including
conventions where a `python` component releases under a prefix other than
`python-v`. The command only reads snapshots and fragments: it does not require
a clean tree, invoke Git, write a snapshot, consume a fragment, commit, or tag.
A stream with no previous snapshot has no unique next version because its first
`release` establishes the baseline; `next-version` therefore exits non-zero
instead of inventing one.

Release versions follow SemVer 2 identifier rules: prerelease and build
identifiers are nonempty dot-separated ASCII alphanumeric/hyphen values, and a
numeric prerelease identifier cannot have a leading zero. Invalid snapshots do
not become release baselines.

The date and identity in the filename must match the metadata. Work that
legitimately has no issue uses `id` instead of `issue`; its identity must be a
UTC timestamp (`20260730T184500Z`) or a 6–12 character hexadecimal UUID prefix.
Only the metadata key changes — the filename keeps `-issue-`, so the inferred
`-id-` spelling is rejected as a non-canonical name. There is no sequential
fragment allocator. A second fragment with the same
issue or `id`, including one in a configured legacy directory, is rejected as
overlapping ownership and must be consolidated.

Rendering orders entries by metadata date, then stable identity and slug. File
allocation never determines precedence.

## Released changes

`CHANGELOG/<version>.md` is an immutable snapshot. A release:

1. acquires the repository release lock;
2. validates and selects canonical `NEXT/` fragments;
3. refuses an existing snapshot;
4. validates the requested version against the selected fragments' maximum impact;
5. writes exactly one snapshot and removes the selected fragments in the same
   commit;
6. tags that exact commit; and
7. regenerates `CHANGELOG.md` only as a display artifact.

Feature pull requests must not edit `CHANGELOG.md`, released snapshots, or
remove fragments. Release automation is the only fragment consumer.

`check-pr` also requires a new valid fragment whenever the final base-to-head
tree changes release-relevant content: behavior, configuration, code,
documentation, or pins. The rule fails closed for new repository paths. Only
the changelog stores (`NEXT/`, `CHANGELOG/`, and generated `CHANGELOG.md`) and
explicitly classified test-only paths are exempt. Dependency manifests and
production source keep their narrower diagnostics, but satisfy the same
fragment requirement. Executable GitHub workflow definitions and any local
action metadata named `action.yml` or `action.yaml` take precedence over
test-like filenames or directory names. Adding a fragment and deleting it in a
later commit does not satisfy the final-tree check.

Repositories should call the reusable
`.github/workflows/changelog-validate.yml` workflow and use
`.github/workflows/changelog-release.yml` for releases. Pin reusable workflows
to an immutable commit whose documented capabilities they need and pass that
same commit as the required `contract_ref` input. A release tag is convenient,
but it is not newer merely because `main` documents a feature; check the
capability table above.

### The release authorization App

Step 5 pushes the snapshot commit and its tag **directly to the default
branch**. Every Verjson repository carries an identical `main-protection`
ruleset whose `pull_request` and `workflows` rules forbid exactly that, and
whose bypass actors include the dedicated `verjson-release-authorization` App.
`GITHUB_TOKEN` is not a bypass actor, so a release using it is rejected at the
last step:

```
remote: error: GH013: Repository rule violations found for refs/heads/main.
remote: - Changes must be made through a pull request.
 ! [remote rejected] v0.4.0 -> v0.4.0 (atomic transaction failed)
```

The generated caller passes only the App identity material:

```yaml
    with:
      release_app_client_id: ${{ vars.RELEASE_APP_CLIENT_ID }}
      release_environment: release-app
```

Store `RELEASE_APP_PRIVATE_KEY` only in the caller-owned `release-app` environment,
with exact main-branch-only admission. Do not forward it through `secrets` or
retain organization/repository copies. Follow the staged
[App-key rollout](../app-key-environment-rollout.md) before activating this contract.

The reusable workflow rejects an empty or all-numeric legacy
`RELEASE_APP_CLIENT_ID`, then delegates full client-ID validation to the
immutable action while minting a short-lived token with the
`actions/create-github-app-token` pin. The
mint is explicitly constrained to `github.repository_owner`, the current
repository name, and `permission-contents: write`; the App has no Pull requests,
Actions, Administration, or organization permission. The App's explicit
`main-protection` bypass is still required because Contents write and ruleset
authorization are separate checks.

The App installation and both organization credential values are intentionally
available across the Verjson repository fleet to avoid per-repository admission
work. This is a convenience-first exception, not least-privilege secret
distribution: a compromised trusted workflow with the organization-wide private
key could ask GitHub to mint a token for another installed repository. The
canonical workflow's constraints prevent accidental cross-repository minting,
but they cannot constrain a different trusted workflow. Selected-repository App
installation and secret visibility, or a central broker that refuses arbitrary
repository requests, are the stricter alternatives (ADR 0099).

The release caller must live at `.github/workflows/release.yml`. That path is
what `scripts/changelog-contract.test.sh` looks for, so a caller named anything
else silently loses both the pin check and the release-App check — it is
treated as an adopter that has nothing to release. Adopters with genuinely
nothing to publish have no such file, which is a supported shape.

The push is `--atomic`, so a rejected release leaves no tag, no snapshot, no
package and `NEXT/` untouched. It fails safely; it simply cannot succeed. The
release job remains explicit-dispatch-only and default-branch-bound. Its
installation token expires automatically and is narrower than the temporary
admin PAT retired by ADR 0099.

Administrators can verify the live authorization path with the manual
`release-app-canary.yml` workflow after its commit is on the default branch. It
accepts no target inputs, routes through the trusted organization lane with
`CI_LANE_FALLBACK`, and atomically pushes an isolated canonical snapshot to
the otherwise-absent protected `develop` ref plus a run-unique prerelease tag,
verifies the annotated tag and branch resolve to the exact snapshot, writes a
retained receipt, and atomically deletes only refs still owned by that run. The
canary exercises organization ruleset `main-protection` and its App bypass; it
does not claim to exercise the default branch ref itself.

### The release caller is generated, not copied

`--atomic` protects the *push*, not the release. Once that push lands, the
version is spent: re-dispatching it is refused because the tag exists, and
re-dispatching a higher one is refused with `release selected no fragments`
because `NEXT/` was already consumed. So anything that can fail must fail before
the push, and until #463/#464/#465 it did not — the caller was hand-copied from a
sibling, and its build, lint and test ran in a job that was `needs: snapshot`.

`scripts/gen-changelog-caller.sh release-node` emits the corrected shape:

```yaml
verify:   # default branch, version format, tag absence, and the full suite
snapshot: { needs: verify }   # the irreversible push
publish:  { needs: snapshot } # build and publish only
```

If your suite is not `npm test`, commit an executable `scripts/release-verify.sh`
and `verify` runs it instead. Do **not** edit the generated caller — the emitted
contract test asserts its provenance and rejects a hand-written one.

The hook runs under `env -i` with a fixed allowlist, not the job environment.
Callers generated at or after this contract also pass
`CI_PACKAGE_RETENTION_KEEP`: the package retention window, read from the
organization variable through the same expression and positive-integer guard
as the post-publish retention jobs in `node-release.yml` and
`container-release.yml`, defaulting to `3`. An invalid value fails `verify`
before the hook runs. A hook that must keep a still-consumed version inside
that window reads it from there ([ADR 0220](../decisions/0220-confine-release-credentials-to-dependency-acquisition/README.md)).

The defaults are npm scope `@verjson` and Node 24. A scaffolder targeting
another lowercase npm scope or numeric Node version passes the same validated
options to both coupled outputs:

```bash
scripts/gen-changelog-caller.sh release-node "$PIN" --scope @acme --node-version 22 > .github/workflows/release.yml
scripts/gen-changelog-caller.sh contract-test "$PIN" --scope @acme --node-version 22 > scripts/changelog-contract.test.sh
scripts/gen-changelog-caller.sh pr-gate "$PIN" > .github/workflows/changelog-contract.yml
scripts/gen-changelog-caller.sh codeowners "$PIN" > .github/CODEOWNERS   # required member (ADR 0210)
```

The generated contract test then rejects drift in either release job. Do not
generate only one side or edit the workflow after generation.

### Leave the release version blank when it is derived

The generated `Release` action keeps the publication decision manual but makes
the `version` input optional. Leave it blank to have the pinned canonical
`release-plan` select the requested component/fragments and derive the next
valid version. Enter a version only when bootstrapping a namespace or when an
explicit version is required; it is checked by the same impact and namespace
rules before any snapshot or publication occurs.

The verify job's summary is the review surface: it records the previous
release, resolved version, highest selected impact and rationale, component,
selection digest, exact source commit, selected fragment names, and assembled
release notes. An empty stream is a successful no-op and does not consume
fragments or create a tag. Retries stay bound to the original dispatch commit
and resolved version.

By default, the root package is the first artifact. Repeat `--package-dir` for
explicit repository-relative secondary packages, passing the same ordered list
to both outputs:

```bash
scripts/gen-changelog-caller.sh release-node "$PIN" --package-dir compat > .github/workflows/release.yml
scripts/gen-changelog-caller.sh contract-test "$PIN" --package-dir compat > scripts/changelog-contract.test.sh
```

For an exact selection that excludes the root package, repeat `--only-package-dir`
instead. It replaces the implicit root on its first occurrence; it cannot be mixed
with `--package-dir`. Empty, duplicate, non-normalized and escaping paths are rejected.
Use the same ordered selection for the caller and contract test:

```bash
scripts/gen-changelog-caller.sh release-node "$PIN" \
  --only-package-dir packages/cli-schema \
  --default-prefix schema-v \
  --default-component cli-schema > .github/workflows/release-cli-schema.yml
scripts/gen-changelog-caller.sh contract-test "$PIN" --only-package-dir packages/cli-schema > scripts/changelog-contract.test.sh
```

Only selected manifests are stamped and passed to Node publication. Verification
still runs the repository suite; an adopter-owned preparation hook remains
responsible for its own changes. Those release-caller defaults open manual
dispatches on the independent `cli-schema`/`schema-v` changelog and version
stream; they do not select publication directories. Exact selection also
applies to the package
stamping performed by `release-artifact` and `release-snapshot`.

Before stamping and packing the configured directories, the caller runs an
executable `scripts/release-prepare-packages.sh <version>` when present. Use
that adopter-owned hook to update compatibility dependencies or generate
secondary manifests; the caller itself applies the dispatched version to every
package and gives each artifact the same restart-safe integrity proof.

### Adopters with nothing to publish to a registry (#975)

`release-node` refuses a repository with no scope-owned package — an Electron
desktop app shipping OS installers as GitHub Release assets, for example.
`scripts/gen-changelog-caller.sh release-artifact` keeps the same immutable
`verify` → `snapshot` boundary (byte-identical to `release-node`'s: the same
default-branch, version, and restart-safety guarantees, the same npm-scoped
`verify` job so private `@verjson` devDependencies still install) and replaces
`publish`'s call to `node-release.yml` with a caller-declared `build` matrix
plus an inlined `publish` job:

```bash
scripts/gen-changelog-caller.sh release-artifact "$PIN" \
  --build-runner macos-14 --build-runner windows-2022 --build-runner ubuntu-24.04 \
  > .github/workflows/release.yml
scripts/gen-changelog-caller.sh contract-test "$PIN" > scripts/changelog-contract.test.sh
```

Repeat `--build-runner <label>` once per runner the release needs to build on
— each becomes one matrix leg of `build`, gated on `verify` and `snapshot` the
same way `publish` is. Every leg runs an adopter-owned, fail-closed
`scripts/release-build.sh <version> <output-dir>`; it must leave every
artifact for that runner inside the given directory or exit non-zero.
`publish` downloads every runner's artifacts and attaches them to the tagged
commit's GitHub Release, using the immutable `CHANGELOG/<version>.md` as the
release notes — the same restart-safe release-notes logic `node-release.yml`
uses, inlined here because there is no separate reusable publication workflow
for artifact releases. The generated contract test recognizes either shape as
a conformant release caller; a file that calls `changelog-release.yml` but
matches no generated provenance comment is rejected as a hand-rolled
substitute, exactly as before.

### Adopters that publish nothing from the release workflow (#1206)

A repository can publish nothing from its release run and still cut versioned
releases — container images shipped by a separate, independently triggered
workflow are the concrete case. `release-node` fails at `npm publish` on a
`private` or unscoped package, and `release-artifact` requires a
`--build-runner` matrix and an executable `scripts/release-build.sh` that
produces at least one file. Neither is satisfiable, and "no release caller at
all" means the repository's `NEXT/` fragments are never consumed into
`CHANGELOG/<version>.md`.

`scripts/gen-changelog-caller.sh release-snapshot` is that shape:

```bash
scripts/gen-changelog-caller.sh release-snapshot "$PIN" > .github/workflows/release.yml
scripts/gen-changelog-caller.sh contract-test "$PIN" > scripts/changelog-contract.test.sh
```

It keeps the same immutable `verify` → `snapshot` boundary as the other two
callers, byte-for-byte in intent, and stops there: `publish` creates or updates
the tag's GitHub Release from the immutable `CHANGELOG/<version>.md` with the
same restart-safe notes logic, and attaches nothing. There is no `build` job,
no `scripts/release-build.sh` requirement, and no `--build-runner`,
`--approved-internal-package` or `--release-asset` flag — a caller generated in
this mode that grows any of them has been hand-edited, and the generated
contract test rejects it. An adopter that later gains something to publish
regenerates as `release-node` or `release-artifact` rather than editing this
file.

Reaching for `release-node` with a `"private": true` package is now refused
before anything irreversible happens. node-release.yml only ever runs as
`publish`, so a failure there lands on top of a tag and an immutable
`CHANGELOG/<version>.md` that are already pushed and cannot be re-cut. A
generated `release-node` caller therefore refuses an unpublishable package in
`verify` — the last stage that still precedes the snapshot push — and names
`release-snapshot` as the fix. node-release.yml repeats the refusal in
`Validate release package directories` for adopters still pinned to an older
caller, so the failure at least states its cause.

### Release proposals are generated and explicitly autonomous

`scripts/gen-changelog-caller.sh release-propose` emits a daily and
operator-triggered caller. Adoption requires an explicit autonomy choice; there
is no default that can silently gain write authority. Here, `PIN` must be an
immutable contract commit containing #799; a previously advertised pin does not
gain a new generator mode retroactively:

```bash
# Maintain one open issue containing the derived tag and released preview.
scripts/gen-changelog-caller.sh release-propose "$PIN" --autonomy propose \
  > .github/workflows/release-propose.yml

# Or dispatch the existing generated Release workflow with the derived tag.
scripts/gen-changelog-caller.sh release-propose "$PIN" --autonomy dispatch \
  > .github/workflows/release-propose.yml
```

The generated `propose` caller receives `issues: write` but not `actions: write`.
The generated `dispatch` caller receives `actions: write` but not `issues: write`.
The reusable workflow validates that it is running from the default branch,
uses `selection-digest`, `next-version`, and `render-next --as-released` against
the same prefix, component, and fragments, and serializes decisions per
repository. A scheduled run with no selected fragments is a green no-op, while
an invalid explicit selector still fails. Proposal mode updates one marker-owned
open issue in place. Dispatch mode first looks for an exact-version, exact-head,
exact-selection `Release` run and waits for the dispatched run to become visible,
so retrying the same proposer selection does not create a second release run and
a different subset is never suppressed merely because it derives the same tag.

The dispatched `Release` checks the receipt head before checkout or verification
and recomputes its selector digest through the immutable contract before release
state, snapshot, or publication. It also carries the chosen tag namespace through
the generated snapshot and reusable Node publisher: `python-v1.2.3` is a tag in
the `python-v` namespace and publishes package version `1.2.3`. Prefix remains
independent from component; neither is inferred from the other.

Neither mode runs `changelog.py release`, consumes a fragment, creates a tag, or
pushes a commit. Dispatch mode can only invoke the generated `Release` workflow;
that workflow retains the `verify → snapshot → publish` boundary and remains the
only path that can mutate release history.

## Consumer adoption

A consumer needs three files, and they must pin the **same** commit — plus a
fourth if it publishes something and an optional fifth when it adopts release
proposals. Repositories using hosted Renovate may add the generated attribution
caller as a sixth file. Generate all of them rather than writing them; the
reasoning is in the generator's header.

```bash
PIN=413bf03b179ff3028e6c7da5551aaa44562ddd8d
# `workflow` remains an alias for automation using the original command name:
scripts/gen-changelog-caller.sh workflow "$PIN" > .github/workflows/changelog.yml
# New adopters use the explicit generated-artifacts mode:
scripts/gen-changelog-caller.sh generated-artifacts "$PIN" > .github/workflows/changelog.yml
scripts/gen-changelog-caller.sh renderer "$PIN" > scripts/render-next.sh
scripts/gen-changelog-caller.sh contract-test "$PIN" > scripts/changelog-contract.test.sh
scripts/gen-changelog-caller.sh pr-gate "$PIN" > .github/workflows/changelog-contract.yml
scripts/gen-changelog-caller.sh codeowners "$PIN" > .github/CODEOWNERS   # required member (ADR 0210)
# Hosted Renovate repositories add this trusted pull_request_target caller. It
# adds a fragment through the Git Data API when the bot did not provide one.
scripts/gen-changelog-caller.sh renovate-attribution "$PIN" > .github/workflows/renovate-changelog.yml
# Only one release caller at a time. Use release-node if the repository
# publishes a Node package to a registry, release-artifact if it ships GitHub
# Release assets instead (#975), or release-snapshot if it publishes nothing
# from the release workflow but still cuts versioned releases (#1206). Only a
# repository that cuts no releases at all keeps having no release caller.
scripts/gen-changelog-caller.sh release-node "$PIN" > .github/workflows/release.yml
# scripts/gen-changelog-caller.sh release-artifact "$PIN" --build-runner ubuntu-24.04 > .github/workflows/release.yml
# scripts/gen-changelog-caller.sh release-snapshot "$PIN" > .github/workflows/release.yml
chmod +x scripts/render-next.sh scripts/changelog-contract.test.sh
```

Choose exactly one workflow command. Every changelog-enabled mode publishes the
organization-required `changelog / validate` check. `generated-artifacts`
enables changelog validation but leaves ADR-index checking off. A repository with
`docs/decisions/` may opt into both checks only by acquiring the pinned
generator, the suite that covers it, and the matching caller together:

```bash
scripts/gen-changelog-caller.sh adr-index-generator "$PIN" > scripts/gen-adr-index.sh
scripts/gen-changelog-caller.sh adr-index-test "$PIN" > scripts/gen-adr-index.test.sh
scripts/gen-changelog-caller.sh generated-artifacts-with-adr-index "$PIN" > .github/workflows/changelog.yml
chmod +x scripts/gen-adr-index.sh
```

`scripts/gen-adr-index.test.sh` is part of the same pinned set: the generated
contract test asserts its digest, so acquiring the generator without it — or
keeping a hand-written copy beside it — fails validation rather than rotting
silently. Run it with `bash scripts/gen-adr-index.test.sh`.

The selected command is the only validation caller and its output path is
`.github/workflows/changelog.yml`. Do not retain a second caller under any
workflow filename: the generated contract and central audit scan every `.yml`
and `.yaml` workflow and reject renamed generated copies as well as callers of
the retired `changelog-validate.yml` workflow. The canonical job inputs are
exactly `changelog` and `contract_ref`, plus `adr-index` only in the generated
ADR-index mode.

`adr-index: true` without that generated script is deliberately a failure, not
a clean result. Do not copy the script from another repository or hand-edit the
caller: the generated contract test verifies that the script's digest matches
the same immutable pin as the caller.

Re-run all of them together whenever the pin moves, and commit the result — a
partial regeneration is the divergence the generator exists to prevent.

At a pin containing #524, `check-pr` requires a newly added, valid `NEXT/`
fragment whenever a supported dependency manifest or lockfile changes. This
includes bot-authored updates: actor identity is not an exemption. To acquire
the rule, fetch one immutable Verjson/.github commit and run the `workflow`,
`renderer`, `contract-test`, and (for hosted Renovate) `renovate-attribution`
commands above with that exact `PIN`; never patch an adopter's generated caller
or contract test locally. The attribution workflow accepts only same-repository
`renovate/*` pull requests authored by the Renovate App, parses its update table
strictly, and never checks out or executes pull-request-head code. It no-ops when
the pull request already adds a valid fragment.

At a pin containing #800, `validate --base <base> --head <head>` also requires
explicit `impact` metadata on each fragment the pull request adds. Acquire the
workflow, renderer, contract test, and release caller (when present) from one
immutable pin with `scripts/gen-changelog-caller.sh`; do not hand-edit only the
workflow to approximate the policy.

`scripts/render-next.sh` does not implement rendering. It fetches this
repository's `scripts/changelog.py` at the pinned commit, caches it by content
address under `$XDG_CACHE_HOME/verjson-changelog/<commit>`, and executes it. A
consumer therefore runs the same code locally that CI validates with, without
vendoring a copy that can drift — the failure `verjson-agents` demonstrated by
vendoring `changelog.py` and never receiving a later fix.

Consumers must not reimplement ordering, filtering, or filename validation. Four
independent bash renderers existed before this contract; one shipped a
locale-collated `sort -r` defect (`verjson-authn#93`).

`scripts/changelog-contract.test.sh` is the pin-agreement test — the renderer's
`CONTRACT_REF`, the workflow's `uses:` ref and its `contract_ref` input must be
one commit. Divergence there is silent: every file keeps working while local
output stops predicting CI.

**Do not hand-write or hand-edit that test.** It is the only adopter file that
encodes assumptions about repository *state*, and every hand-copied version so
far asserted a pre-release tree — named fragment titles, hashed released
entries, "no `CHANGELOG.md` yet", "released history is empty". A release
consumes `NEXT/`, writes `CHANGELOG/<version>.md` and generates `CHANGELOG.md`,
so each of those is false the moment the contract works as intended. Consumers
wire the suite into `npm test`, which release workflows run before publishing,
so the first dispatched release pushed its tag and then died in the publish job:
orphaned tag, nothing published, `main` red thereafter (#309). The generated
form derives every content assertion from the tree and is exercised against a
fixture that performs a real release.

## Where the engine is read from

Both generated scripts pin two things: the contract commit, and the SHA-256 of
`scripts/changelog.py` at that commit. Every path to the engine is checked against
that digest before it is executed — a cache hit, a fresh download, and an explicit
override alike.

The cache path is keyed by commit, which reads as content-addressed but is not.
Nothing prevents another tool, a restored CI cache, or an interrupted write from
leaving different bytes at that path, and before the digest was pinned those bytes
were executed as the contract on every subsequent run.

`CHANGELOG_CONTRACT_PATH` selects **where** the engine is read from:

```bash
CHANGELOG_CONTRACT_PATH=/opt/vendor/changelog.py scripts/render-next.sh
```

It is for a vendored copy, an offline mirror, or a warmed CI cache. It cannot
select **what** runs: an override whose bytes differ from the pinned digest is
refused, naming the pin it failed against. So the guarantee the renderer is sold
on — that it runs the same code CI validates with — holds regardless of the
environment, which is what makes the override safe to offer at all (#304).

### Runner-preloaded cache

Generated renderers and contract tests also share one stable cache layout:

```text
VERJSON_CHANGELOG_TOOL_CACHE/<40-hex-contract-commit>/changelog.py
```

Runner bootstrap may preload that file to make validation and releases work
without egress. The path is only a discovery mechanism: generated artifacts
verify its bytes against the SHA-256 embedded for the exact pinned commit before
execution. Writable, restored, or partially populated caches receive no trust
from their location.

When the variable is unset, the same layout uses
`${XDG_CACHE_HOME:-$HOME/.cache}/verjson-changelog` as its root. A missing or
corrupt entry falls back to the immutable
`raw.githubusercontent.com/Verjson/.github/<commit>/scripts/changelog.py` URL,
verifies the temporary download, and atomically publishes it. If egress is
restricted and no valid preload exists, the error names the expected cache file,
digest, and cache-root setting. See
[ADR 0065](../decisions/0065-verified-changelog-tool-cache/README.md).

## Repairing a released snapshot

`CHANGELOG/<version>.md` is immutable, and `check-pr` rejects **modifying** an
existing snapshot as well as adding one — so a malformed snapshot cannot be fixed
through an ordinary pull request. That is deliberate for released history, but it
also means a migration mistake (an inverted heading, or a snapshot for a release
that was never cut) would otherwise be permanent.

`check-pr` is a **pull-request** guard, not a filesystem lock. Repair is therefore
possible, and is defined as a deliberate maintainer act rather than a normal
change:

1. confirm the snapshot does **not** describe a real published release — check for
   a matching git tag and GitHub Release first. If one exists, the snapshot is
   correct history and must not be touched, however unusual its shape;
2. make the correction in a commit direct to the default branch, by a maintainer;
3. record it in an ADR, naming the snapshot and why it was not a real release.

Never repair a snapshot to make it *prettier*. Pre-contract releases legitimately
look different from generated ones, and rewriting them fabricates history that did
not happen — see the migration guide's step 2.

## Temporary migration compatibility

During issue [#249](https://github.com/Verjson/.github/issues/249), validation
and rendering may be given `--legacy-dir` for a former directory of unreleased
Markdown fragments. Compatibility is read-only: releases never consume legacy
fragments. Duplicate canonical/legacy issue identities still fail. The option
must be removed after the tracked consumer migration; it is not a second
unreleased store.

See [migration.md](migration.md) for the one-time conversion procedure.
