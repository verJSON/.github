# Organization `v1.0.0` readiness contract

This contract is the canonical readiness bar a `@verjson/*` package must clear before
its version is cut to `v1.0.0`. It is applied to one candidate package at a time; clearing
the bar makes a package eligible to graduate but does not require a release.

Link it **at an immutable commit**, never at `main`, so the bar a repository was audited
against stays readable after the bar moves:

```
https://github.com/Verjson/.github/blob/<40-hex-sha>/docs/v1-readiness/README.md
```

The package-scoped graduation policy is recorded in
[ADR 0159](../decisions/0159-graduate-packages-to-v1-by-evidence/README.md), which
supersedes the organization-wide release wave in ADR 0137.

## Why `1.0.0` at all

Below `1.0.0`, SemVer gives the **minor** position the breaking-change role, and the caret
range follows: `^0.1.3` admits `0.1.4` but **not** `0.2.0`. A guardrail published as a `0.x`
minor therefore reaches nobody — every consumer on a caret range silently stays behind.
`1.0.0` is the point at which `^1.0.0` starts accepting the whole major, so a fix actually
propagates and a break actually announces itself.

That cuts both ways, and it is the reason for this contract: after `1.0.0`, a `patch` that
changes a published type is a defect that reaches consumers *without a signal*. Every item
below exists because that already happened somewhere in this organization.

## How to use this document

Each item is **PASS / FAIL / N/A**, with the evidence that decides it named in the item
itself. An auditor records the literal command output or URL that produced the verdict in
the repository's prep issue. Two auditors reading the same repository must reach the same
verdict; where an item leaves room for judgement, the item says which way to fail.

- A **blocker** must PASS before the `1.0.0` release is dispatched. Every item below is a
  blocker except item 10, which must be recorded but does not gate the cut.
- **N/A** requires a stated reason, not a blank. "The package has no runtime dependents"
  is a reason; silence is a FAIL.
- An item that cannot be evaluated is a FAIL, not a pass. A gate that cannot resolve its
  own inputs is the failure mode item 1 exists to prevent.

---

## 1. Published `.d.ts` surface audit, and a conformance check in CI — **blocker**

**Why.** `1.0.0` is where SemVer starts binding, so the check that detects a type break
must exist *before* the cut, not after the first one escapes.

**Evidence.** [`Verjson/verjson-authn#244`](https://github.com/Verjson/verjson-authn/issues/244):
between `1.0.1` and `1.0.3` the published `CodeStore` port replaced the **optional**
`incrementAttempts?(key)` with a **required** `verifyAttempt(key, attempt)` and shipped it
as a **patch**. `^1.0.1` and `~1.0.1` both admit `1.0.3`, so any consumer implementing the
port got a type error — or a runtime `TypeError` — from a routine `npm ci`. It was reported
by a downstream consumer, not caught by the repository.

**Bar.**

- [ ] The repository runs a published-type-surface conformance check on every pull request,
      comparing the surface this tree emits against the surface of the **published tarball**
      the consumer's range currently resolves to.
- [ ] The check **fails closed**: if the base surface cannot be resolved, the job fails.
      A gate that cannot compare must not report success.
- [ ] A deliberate surface change is accompanied by a `NEXT/` fragment whose `impact:` is
      `major` (or `minor` for a purely additive optional member) — see item 6.
- [ ] The audit has been run once against the *currently published* tarball, not only
      against the working tree, and any accidental surface already shipped is recorded.

**Canonical implementation.** Do not design a new one. Adopt the `type-surface-contract`
job in `Verjson/verjson-authn` `.github/workflows/ci.yml`, which already carries the three
traps that make a naive version of this check silently useless:

Generate the npm pack receipt parser and its byte-identity contract from the same immutable
`Verjson/.github` revision used for the adoption. The parser accepts both the npm 11 array
receipt and npm 12 package-keyed object receipt, then fails closed unless exactly one safe
tarball for the expected package was produced:

```sh
scripts/gen-type-surface-contract.sh pack-helper <contract-sha> > scripts/npm-pack-json.mjs
scripts/gen-type-surface-contract.sh contract-test <contract-sha> > scripts/type-surface-contract-contract.test.sh
```

The type-surface script must call `resolvePackedTarball(packed, packageName)` instead of
reading `[0].filename`. Run the generated contract test in CI alongside the type-surface
test; it binds the copied helper byte-for-byte to the pinned organization contract.

**Ordering — item 1 depends on item 5.** The canonical implementation resolves its baseline
from the highest released `CHANGELOG/v<x.y.z>.md` snapshot and hard-fails when there is none
(`scripts/type-surface-contract.test.sh:419-426`). A repository that has not yet landed the
changelog contract, or has landed it but never cut a release under it, **cannot pass this item
yet** — record it as FAIL blocked on item 5, never N/A, and re-audit after its first
conforming release.

**Status — read this before citing it.** The implementation is
[`Verjson/verjson-authn#246`](https://github.com/Verjson/verjson-authn/pull/246), which was
**open** when this contract was written (head `b160239a75d5ba2e325c9ef7e244f2d0ff948592`,
its required checks green). It is not on `verjson-authn` `main`. A candidate package must use a
landed canonical implementation before graduating, and should pin to its merge commit rather than to this
branch SHA.

Its two artefacts are the job `type-surface-contract` in `.github/workflows/ci.yml:39` and
the script `scripts/type-surface-contract.test.sh`. Four properties are the reason to adopt
it rather than re-derive it — the first is its baseline choice, the other three are the traps
named above:

1. **The baseline is the published tarball, not a git ref or a committed snapshot.** The
   script resolves the version from the highest `CHANGELOG/v<x.y.z>.md` snapshot and
   `npm pack`s it (`scripts/type-surface-contract.test.sh:419`, `:450`). A committed
   baseline file would be editable by the very pull request it constrains; the released
   artefact is what a consumer's range actually resolves to. Note that `package.json`
   `version` is **not** the source of truth here — releases are dispatched, so the manifest
   version is a placeholder.

2. **`skipLibCheck: true` makes a naive guard dead code — so the extractor does not reuse
   the repository's tsconfig.** `@verjson/tsconfig/tsconfig.base.json` sets
   `skipLibCheck: true`, and every entry point being audited is a `.d.ts`, so the option
   skips semantic checking of exactly the files the guard inspects. The extractor builds
   its own `ts.createProgram` with an explicit inline `skipLibCheck: false`
   (`scripts/type-surface-contract.test.sh:81`). This matters because an unresolved import
   silently degrades types to `any` — and since both roots resolve against the same
   `node_modules`, the degradation is *identical on both sides* and compares **equal**. The
   check would pass while measuring nothing.

3. **Truncated type printing blinds a textual comparison — so printing sets
   `TypeFormatFlags.NoTruncation`.** TypeScript elides large types as `... N more ...`; a
   change *inside* the elided region compares equal (false negative), while an added field
   flips `25 more` to `26 more` (false positive). 65 strings in `verjson-authn`'s own
   surface elide by default. Both print sites set the flag
   (`scripts/type-surface-contract.test.sh:175`, `:195`), and `:195` additionally sets
   `InTypeAlias`, because an aliased union otherwise prints as its alias *name* — so
   narrowing `'ok'|'invalid'|'expired'` to `'ok'|'invalid'` would compare equal to itself.

4. **A shallow CI checkout silently widens the gate — so the workflow, not the script, is
   what fails closed.** `actions/checkout` defaults to `fetch-depth: 1`, so `origin/<base>`
   never resolves. The script's own fallback **widens**: when the base ref cannot be
   resolved it scans *every* pending `NEXT/` fragment
   (`scripts/type-surface-contract.test.sh:513`), which lets an unrelated pending
   `impact: major` license this pull request's break. The workflow closes that hole with
   `fetch-depth: 0`, an explicit base-branch fetch, and a hard
   `git rev-parse --verify "origin/$BASE_REF"` (`.github/workflows/ci.yml:52-61`) whose
   failure fails the step.

   > **An adopter who copies the script without the workflow's checkout and `rev-parse`
   > steps gets a silently vacuous gate.** Copy both, or copy neither.

**Also inherit, or you will get it wrong:**

- **The waiver is `impact: major` on a fragment this pull request *adds*** — matched from
  the merge-base with `--diff-filter=A` (`scripts/type-surface-contract.test.sh:509`), so
  one release's major declaration cannot retroactively cover unrelated breaks. This is the
  direct tie to item 6: the changelog contract already refuses a mismatched dispatched
  version, but `impact:` defaults to `patch`, which is what made the `1.0.2` break
  invisible.
- **It must be its own job, not a step inside `npm test`** (`.github/workflows/ci.yml:34-38`).
  The reusable `node-ci.yml` sets `NODE_AUTH_TOKEN` only on its `npm ci` step, so a registry
  read from inside `npm test` returns 401.
- **Use `secrets.GITHUB_TOKEN`, not a publish-capable registry token**
  (`.github/workflows/ci.yml:67-75` — rationale at `:67-72`, assignment at `:75`). The
  final step runs a script the pull request itself
  can edit; a repo-scoped token can read the repository's own package, which is all the
  baseline needs.
- **A caller can only narrow a reusable workflow's permissions.** Omitting a scope the
  reusable requests kills the workflow at startup **with no check run emitted** — which
  reads as "not required yet", not as a failure.
- **The fixture self-test is load-bearing, not decoration**
  (`scripts/type-surface-contract.test.sh:305-409`). It re-derives the `#244` verdict on
  every run and asserts the fixture surface is not degraded, so the machinery cannot pass
  while measuring nothing. Do not drop it to save time.
- **The job is advisory until a ruleset requires it.** On `verjson-authn` the rulesets
  require `ci / build-test`, `ci / eligibility`, and `changelog / validate` — not
  `type-surface-contract`. Adding the job without adding the required context leaves the
  break-detection unenforced.

---

## 2. `exports` map present and complete — **blocker**

**Why.** `exports` is a **restricting** field. While it is absent, Node and TypeScript fall
back to plain path resolution against whatever the tarball ships, so every subpath a
consumer happens to reference resolves. The moment an `exports` map appears, every subpath
*not* listed becomes unreachable — and the failure is an opaque module-resolution error in
the consumer, at install or build time, with nothing in the version number to predict it.

**Evidence.** [`Verjson/verjson-tsconfig#34`](https://github.com/Verjson/verjson-tsconfig/issues/34):
`@verjson/tsconfig` was the organization's only package shipping **no** `exports` map, while
its dependents extend it by explicit subpath (`"extends": "@verjson/tsconfig/tsconfig.base.json"`).
#34 counted 19 repositories at filing; the manifest derivation on 2026-08-25 counts **22**,
all `devDependencies` (ADR 0137).
Any future "modernize the manifest" chore that added a map without an explicit
`"./tsconfig.base.json"` entry would have silently unreached all 19.

**Bar.**

- [ ] `package.json` declares an `exports` map.
- [ ] The map lists **every** subpath any consumer currently imports, including
      `"./package.json"` where tooling reads it. Enumerate by the same method item 3 uses for
      dependents: grep the organization's checkouts for `<pkg>/` subpath references and
      cross-check with GitHub code search. **If the consumer set cannot be enumerated — an
      external or unknown consumer — this item FAILs**, because an unlisted subpath cannot
      then be ruled out.
- [ ] The map is verified by **real resolution from a scratch consumer against a packed
      tarball** — `npm pack`, install the tarball into an empty project, then
      `require.resolve('<pkg>/<subpath>')` and/or `import.meta.resolve(...)` for each
      declared and each historically used subpath.
- [ ] That verification runs in CI, not once by hand.

**Explicitly not sufficient:** a test asserting the files exist on disk, or that `files:`
lists them. A file-existence test passes with a completely broken `exports` map, because
the file is in the tarball and the *resolver* is what refuses to reach it. If the check
does not call a resolver, it does not satisfy this item.

---

## 3. No `^0.x` internal peer or dependency ranges — **blocker**

**Why.** `^0.2.0` does not admit `0.3.0`, and it does not admit `1.0.0` either. A package
whose peer range points at a `0.x` sibling becomes uninstallable the moment that sibling
cuts `1.0.0`. This is the single item most likely to
break consumers, and it must be resolved **before** the depended-on package releases, not
after.

**Evidence.** [`Verjson/verjson-authz#125`](https://github.com/Verjson/verjson-authz/issues/125):
`@verjson/authz` declares `peerDependencies: { "@verjson/identity-contracts": "^0.2.0" }`
(`package.json:60`) and already `ERESOLVE`s against the published `0.3.0` — even though the
code is compatible and typechecks against it. The peer range, not the code, is the
incompatibility.

The same shape was present across the organization when the original wave was evaluated.
Derived from the manifests on 2026-08-25, these are historical examples that a candidate
package's fresh dependent inventory must replace:

| Consumer | Historical range on a candidate dependency | Kind | Location |
|---|---|---|---|
| `@verjson/ai-gguf` | `@verjson/ai@>=0.12.0 <1` | peer | `package.json:48` |
| `@verjson/authz` | `@verjson/identity-contracts@^0.2.0` | peer | `package.json:60` |
| `@verjson/oidc-claims-middleware` | `@verjson/identity-contracts@^0.2.2` | peer | `package.json:70` |
| `@verjson/identity-lifecycle` | `@verjson/identity-contracts@^0.3.0` | runtime | `package.json:50` |
| `@verjson/leads` | `@verjson/identity-contracts@^0.3.0` | runtime | `package.json:60` |
| **`@verjson/authn` 1.0.3** | `@verjson/identity-contracts@^0.2.2` | runtime | `package.json:67` |
| **`@verjson/observability` 1.1.0** | `@verjson/identity-contracts@^0.2.1` | runtime | `package.json:160` |
| **`@verjson/payments` 2.1.1** | `@verjson/identity-contracts@^0.3.0` | runtime | `package.json:72` |
| `@verjson/cli` | `@verjson/cli-cloud@>=0.29.1 <1 \|\| ^1.0.0`, `@verjson/cli-projects@^0.8.0` | runtime | `package.json` |
| `@verjson/cli-cloud` | `@verjson/infra@>=0.19.1 <0.20.0 \|\| ^1.0.0 \|\| ^0.20.0` | runtime | `package.json` |

Two things this table makes visible that the per-repository view does not:

- **`@verjson/ai-gguf` carries an explicit `<1` ceiling**, not just a caret. A range
  widening that only edits the lower bound leaves it excluding `1.0.0`. Read the whole
  range, not the caret.
- **Packages already past `1.0.0` are in scope for this item.** `authn` (`^0.2.2`),
  `observability` (`^0.2.1`), and `payments` (`^0.3.0`) each consume
  `@verjson/identity-contracts` at **runtime**, and all three ranges exclude `1.0.0`. They
  need not graduate themselves, but they will break if `identity-contracts` does — and
  `authn` and `observability`, being on `^0.2.x`, already fail to resolve the published
  `0.3.0` today. Audit the dependents of the package being cut, not only the package
  itself.

This repository is itself such a consumer: `contracts/container-deployment-cli/package.json:6`
pins `@verjson/cli-cloud@1.1.0` **exactly**.

**Bar.**

- [ ] No `dependencies`, `peerDependencies`, or `optionalDependencies` entry naming another
      `@verjson/*` package uses a range that excludes that package's planned `1.0.0`.
- [ ] Every such range has been widened (for example `^0.2.0 || ^0.3.0 || ^1.0.0`, or
      `>=0.3.0 <2` where the package genuinely supports both) **and released** before the
      depended-on package dispatches its own `1.0.0`.
- [ ] The widened range is *exercised*, not asserted: a compatibility matrix leg installs
      the new version alongside the packed tarball and runs the real tests. Widening a peer
      range without a matrix leg proves only that the string changed.
- [ ] The audit covers **dependents as well as dependencies** — including dependents
      already past `1.0.0`, which the candidate package's release does not otherwise touch.
- [ ] `devDependencies` on `@verjson/*` are recorded but do **not** block a consumer's
      install: a devDependency never reaches a consumer's install graph. They still break
      the *depending repository's own CI* the moment their target cuts `1.0.0`, so they are
      a scheduling constraint rather than a release blocker (ADR 0137).

---

## 4. CI compatibility matrices pin majors, not exact versions — **blocker**

**Why.** GitHub Packages retention keeps only the **3 most recent stable versions** of
each organization npm package
([ADR 0108](../decisions/0108-bound-package-retention-to-three-stable-releases/README.md)).
An exact old version named in a matrix is deleted by the third publish after it, and every
open pull request in the repository goes red for reasons unrelated to any of them.

**Evidence.** [`Verjson/verjson-authz#124`](https://github.com/Verjson/verjson-authz/issues/124):
publishing `@verjson/identity-contracts@0.3.0` on 2026-08-24 deleted `0.2.0`, which
`.github/workflows/identity-contracts-compatibility.yml:33` named as an exact matrix leg.
That line read `0.2.0` at the time of the incident and reads `0.2.1` today — the floor was
moved, but the shape was not fixed.

Two unrelated pull requests went red at once. Moving the floor to `0.2.1` unblocked CI but
reproduces the defect one publish later. The same repository's
`.github/workflows/next-compatibility.yml:30` shows the correct shape:
`next: ['14','15','16']` — **majors**, resolved at install time.

Graduation can accelerate this sharply when it requires a range-widening release before
`1.0.0`. Every exact-version matrix leg pointing at a `0.x` sibling has a bounded remaining
life that must be checked in the candidate's migration plan.

**Bar.**

- [ ] If the package has no relevant sibling dependency or range in the candidate migration to exercise, this
      item passes vacuously: record that reason and do not add an artificial exact-version
      leg.
- [ ] If a package declares any relevant sibling dependency or peer range in that migration, at least
      one compatibility leg must exercise it. This remains true whether the range already
      admits `1.0.0` or still FAILs item 3; zero legs is FAIL, not vacuous.
- [ ] Every applicable compatibility matrix leg avoids an exact `x.y.z` version of an
      `@verjson/*` package.
- [ ] Legs express **majors** (or a range resolved at install time), then are derived at run
      time from versions actually published
      (`gh api /orgs/Verjson/packages/npm/<name>/versions`).
- [ ] The matrix includes a leg for the sibling's `1.0.0` once that sibling has cut it.

---

## 5. Changelog contract conformance landed — **blocker**

**Why.** The cut must produce the correct immutable `CHANGELOG/v<x.y.z>.md` snapshot. A
repository that is not on the current changelog contract cannot dispatch a conforming
release, so it cannot make the candidate package's `1.0.0` stability promise.

**Bar.**

- [ ] The repository is on the canonical changelog contract
      ([ADR 0038](../decisions/0038-canonical-changelog-contract/README.md)):
      `NEXT/` fragments are the sole unreleased store, immutable `CHANGELOG/v<x.y.z>.md`
      snapshots, and no hand-authored combined `CHANGELOG.md` appears in a feature pull
      request.
- [ ] The workflow caller, renderer, contract test, pull-request gate, and release caller
      (when present) are all **generated** by the canonical `scripts/gen-changelog-caller.sh`
      at **one** immutable contract SHA — never hand-written or partially updated.
- [ ] The chosen SHA is a capability-bearing floor, not one magic pin copied from prose.
      Resolve every capability in `config/capability-floors.json` that names
      `scripts/gen-changelog-caller.sh`, then prove each `introduced_at` commit is an
      ancestor of the chosen SHA. `scripts/capability-floor-audit.py` implements that
      ancestry check; any newer immutable descendant that carries every applicable
      capability is conformant.
- [ ] The generated pull-request gate emits the canonical context (`changelog / validate`
      at the current floor), and an active ruleset that actually applies to the default
      branch requires that literal context. A green or merely present check is not
      enforcement; record the live ruleset evidence.

**Correction — do not follow the generated adoption issues literally.** The per-consumer
adoption issues generated under [`#731`](https://github.com/Verjson/.github/issues/731)
carry two wrong instructions, filed as
[`#1087`](https://github.com/Verjson/.github/issues/1087):

| adoption issue says | readiness requirement |
|---|---|
| regenerate at contract SHA `23f641822d1fdf4787a46f0b55f24a755b8a73ae` | select one immutable SHA that descends every applicable machine-readable capability floor, then generate every changelog artifact from it |
| preserve required context `generated-artifacts / validate` | require the context emitted by the generated gate (`changelog / validate` at the current floor) in an active, applicable ruleset |

The stale SHA is unadoptable, not merely old: it is the recorded `introduced_at` floor for a
*different* capability, predates the `pr-gate` generator mode the same issue also mandates
(so the required job cannot be generated from it), and is below an applicable changelog
capability floor. `Verjson/verjson-temporal-kit#110` and
`Verjson/verjson-video-forge#16` each rejected it independently. Audit the chosen SHA
against the registry instead of treating any README literal as the only conformant pin.

---

## 6. Explicit `impact:` on every unreleased fragment — **blocker**

**Why.** The changelog engine at `scripts/changelog.py` computes the release version from
the highest `impact:` among the selected fragments. When the field is **absent**, it falls
back to `patch` (`scripts/changelog.py:247`, `:653`). An omitted `impact:` therefore
does not produce an error — it produces a **patch release for a breaking change**. That is
the precise mechanism that shipped `verjson-authn` `1.0.2`.

A second fail-open path exists when a package has published versions but no immutable
`CHANGELOG/v<x.y.z>.md` snapshots: the engine has no release baseline, so next-version
cannot derive a version and the release-time bump check returns early. A non-zero
`next-version` result in that state is evidence of a disabled guard, not an acceptable
first-release outcome.

**Evidence.** [`Verjson/verjson-tsconfig#34`](https://github.com/Verjson/verjson-tsconfig/issues/34)
nearly cut a *restricting* `exports` addition as a patch because its fragment declared no
`impact:` and the contract defaulted it.

Note also that `check-pr` does **not** enforce the field on fragments already present on
the base — only `validate --base <ref>` rejects a *newly added* fragment that omits it.
The dated migration grace period **runs through 2026-08-29 UTC and is still active**
(`scripts/changelog.py:494`), so before that date a green `validate --base` proves nothing
about `impact:` — inspect the fragments directly.

**Bar.**

- [ ] Every fragment currently in `NEXT/` declares an explicit `impact:` of `major`,
      `minor`, or `patch`. **Absence is a defect to fix, not a default to rely on** —
      inspect the files directly during the grace period and add the field to all
      pre-existing fragments the release will consume.
- [ ] Each declared `impact:` has been checked against what the change actually did to the
      published surface, using item 1's audit — not against the commit-message type.
- [ ] A package with any published version has at least one immutable
      `CHANGELOG/v<x.y.z>.md` snapshot. Published versions with no snapshot are **FAIL**;
      repair the baseline through the canonical migration path before claiming readiness.
- [ ] The selected `v1.0.0` release set includes a fragment that explicitly declares
      `impact: major` and describes the v1 transition.
- [ ] `python3 scripts/changelog.py next-version --repo-root .` at the chosen contract SHA
      returns **`v1.0.0`**. The command emits a v-prefixed tag, not `1.0.0`
      (`--prefix` defaults to `v`; `scripts/changelog.py:691`, `:698`). Any other
      output or non-zero result is FAIL.
- [ ] In a disposable clean checkout, the exact pinned release command is control-tested
      against the selected fragments: a wrong requested version is rejected without a
      release commit, tag, or tree mutation, while exactly `v1.0.0` is accepted. Nothing
      from this rehearsal is pushed.

---

## 7. Release path rehearsed — **blocker**

**Why.** The npm/package release path is an explicit `workflow_dispatch`; its
verified plan derives the version from selected fragment impacts when omitted. Nothing is inferred from a merge or branch push. Discovering at dispatch time that the
path is broken is a failure that cannot be rolled back cheaply, because the version and tag
are immutable.

**Bar.**

- [ ] The `@verjson/*` npm publication path starts only from `workflow_dispatch` with a
      version resolved from the selected fragments or explicitly supplied for validation. No merge, branch-push, or other `push` event can publish
      the npm package or derive its version from commit subjects.
- [ ] An independent non-npm language lane does not fail this item solely because it
      consumes a tag-push event. Its tag must use an explicit, disjoint namespace that
      states the exact version; the lane must be unable to publish the `@verjson/*` npm
      package and must have its own rehearsed guard that rejects malformed, wrong-version,
      or conflicting tags.
- [ ] `semantic-release` is retired: no `.releaserc.json`, no `semantic-release`
      dependency, and no commit-subject-derived npm version. A survivor is a defect to
      remediate, not a local convention.
- [ ] The release caller is generated by `scripts/gen-changelog-caller.sh` at the
      capability-bearing SHA proven in item 5; workflow, renderer, contract test, and
      release caller all pin the same immutable SHA.
- [ ] The generated contract test has been run and passes.
- [ ] **A rehearsal has been performed in a disposable clean checkout**: run the exact
      pinned `scripts/changelog.py release` command with a throwaway version and the
      fragments the real release will select, then verify that the resulting **commit, tag,
      and changed tree** are what the real cut will produce. Discard the checkout without
      pushing. A local render or green pull-request validation is *not* release-path
      evidence.

---

## 8. Open vulnerability and support sweep — **blocker**

**Why.** `1.0.0` is a stability claim. Cutting it over an unremediated high-severity
advisory publishes that advisory to every consumer whose caret range now accepts the
release. A green vulnerability audit also says nothing about a dependency whose published
metadata marks the installed line deprecated, end-of-support, or maintenance-only.

**Evidence.** [`Verjson/verjson-temporal-kit#79`](https://github.com/Verjson/verjson-temporal-kit/issues/79):
13 open Dependabot alerts on `main` — 11 high — from transitive `brace-expansion` and
`fast-uri`, plus `protobufjs` and `esbuild`. The high-severity ones were reachable only
through the lockfile and cleared with a lockfile refresh, which is exactly why nobody
noticed them until an explicit sweep.

**Bar.**

- [ ] `npm audit` reports no **high or critical** advisory, or each remaining one has a
      documented, evidence-backed dismissal. High and critical findings must be
      *remediated or dismissed with evidence*; moderate and low findings must be
      *adjudicated* — a recorded decision — but need not be remediated to pass.
- [ ] Every GitHub Dependabot alert is adjudicated under the same rule. An empty list passes
      trivially; an open **moderate** alert with a recorded decision also passes, while one
      with no recorded decision FAILs.
- [ ] Every known deprecation, end-of-support, or maintenance-only marker in a published
      dependency's registry metadata is either addressed before the cut or carries an
      explicit, evidence-backed compatibility and risk justification. A green `npm audit`
      or empty Dependabot list does not satisfy this check.
- [ ] The sweep is **deliberate**, not incidental: record the command output. An advisory
      that disappeared because an unrelated lockfile refresh happened to move a transitive
      pin was never actually assessed.
- [ ] The lockfile committed at the cut is the one the sweep ran against.

---

## 9. Public API and README accuracy — **blocker**

**Why.** `1.0.0` freezes the surface being documented. A README that describes an API the
package does not have becomes a support burden that a major version makes expensive to
correct.

**Bar.**

- [ ] Every exported symbol in the audited `.d.ts` surface (item 1) is either documented or
      deliberately internal. **"Documented" means a TSDoc comment on the declaration** —
      README prose is not a substitute, because it is not carried in the published types.
      Anything internal must be genuinely unreachable through the `exports` map (item 2),
      not merely undocumented. A symbol that is neither TSDoc'd nor unreachable FAILs.
- [ ] Every example in the README executes against the packed tarball. An example that has
      drifted from the API is a FAIL.
- [ ] The stated peer/engine requirements match `package.json` — including the ranges
      widened under item 3.
- [ ] The README states the support commitment `1.0.0` implies: what is covered by SemVer,
      and what is explicitly not (internal paths, unlisted subpaths).

---

## 10. Retention consequence acknowledged — **not a blocker, but must be recorded**

GitHub Packages retention keeps the **3 most recent stable versions**
([ADR 0108](../decisions/0108-bound-package-retention-to-three-stable-releases/README.md)).
A candidate may need both a range-widening release and its `1.0.0` release, so relevant `0.x`
versions can age out of the registry quickly. Any consumer left on a range whose satisfiable
versions are removed will begin hitting `E404` on fresh installs.

**That is an intended forcing function, not a defect.** The correct response is to move the
consumer to the planned supported range. Do **not** re-pin a vanished version, ask for it to be restored, or
propose widening retention for a stale consumer.

Diagnose before acting: a 404 is equally consistent with a stale lockfile pin, an
authentication failure, and a registry outage. Treat it as retention only after
authenticated verification over the same credential path shows the requested version absent
while current versions are readable.

- [ ] The repository's own `@verjson/*` pins have been audited in one pass — every lockfile
      `resolved` URL under `npm.pkg.github.com` checked against
      `gh api /orgs/Verjson/packages/npm/<name>/versions` — rather than one 404 at a time.

---

## Publishing `1.0.0` does not migrate consumers

`^0.x` ranges do not accept `1.0.0`, so no consumer receives a candidate package's release
until its dependency range is deliberately migrated. Publishing without completing the
bounded consumer plan has not delivered the intended stable propagation.

Each affected consumer moves to the supported range recorded by the candidate package's
migration plan, which may be `^1.0.0` or a deliberately tested dual-major range. Track those
migrations per consumer and account for the retention consequence above. See ADR 0159.

---

## Audit record

A prep issue records, per item: the verdict, the command or URL that produced it, and the
commit the audit was run against. Link this document **at the SHA the audit used**, so a
later reader can see the bar as it stood.
