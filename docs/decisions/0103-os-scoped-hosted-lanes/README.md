# 0103 — OS-scoped hosted lanes are repository-scoped, bounded, and fail closed

- **Date:** 2026-08-15
- **Issues:** [#810](https://github.com/Verjson/.github/issues/810),
  [#814](https://github.com/Verjson/.github/issues/814),
  [#815](https://github.com/Verjson/.github/issues/815),
  [#816](https://github.com/Verjson/.github/issues/816),
  [#820](https://github.com/Verjson/.github/issues/820),
  [Verjson/AiB#229](https://github.com/Verjson/AiB/issues/229)
- **Extends:** [ADR 0040](../0040-runner-lanes-and-admission-axes/README.md) (lanes name the work),
  [ADR 0041](../0041-shared-admission-hosted-and-self-hosted/README.md) (capacity moves are variable edits)
- **Category:** CI routing and organization spend — **sensitive class**
- **Status:** Accepted

## Context

The organization is raising its GitHub Actions spending limit so `Verjson/AiB` can build
Electron installers on hosted macOS and Windows. That raise removes the thing that was
actually doing the containment. Until now, hosted spend was bounded because the limit
refused the jobs; afterwards nothing structural stops any of ~89 private repositories from
consuming hosted minutes, and the only remaining bound is whatever contract this
organization builds.

The guarantee this decision has to hold is narrow and absolute at the surface it actually
controls: **once the limit is raised, no *merged* workflow anywhere in the organization may
route GitHub-hosted minutes outside AiB's macOS and Windows installer legs of a dispatched
release.** Everything else — all pull-request CI, all Linux work, every other repository —
stays on the self-hosted `general` lane, plus the two already-sanctioned `ubuntu-24.04`
security-boundary jobs of [ADR 0089](../0089-caller-supplied-privileged-routing/README.md),
whose minutes are free because `.github` is public. See the 2026-08-19 amendment below for
the residual this merge-time framing leaves open and why it is accepted rather than closed.

ADR 0040 already records why a convention will not hold this. The hardcoded-selector
defect regrew four times under a documented convention (#175, #182, #192, #203). The
check that is supposed to be the control, `scripts/ci-gate/runner-routing-policy.test.sh`,
keyed every literal-selector assertion on `ubuntu-(24\.04|latest)` — so `runs-on:
macos-latest` passed the entire file. The two metered SKUs, at 10x and 2x multipliers, were
precisely the two it could not see.

## Decision

### The lanes, and their scope

| Variable | Value | Scope |
| --- | --- | --- |
| `CI_LANE_TRUSTED_MACOS` | `["macos-15"]` | **repository variable on `Verjson/AiB` only** |
| `CI_LANE_TRUSTED_WINDOWS` | `["windows-2025"]` | **repository variable on `Verjson/AiB` only** |

JSON arrays, so `fromJSON()` matches every existing lane usage. The names keep the
`VERJSON_LANE_*` prefix so the routing conformance check and the actionlint policy see a
lane rather than a new concept.

**They are not organization variables, and that is the containment primitive.** An
organization variable is readable by every repository in its visibility set, so defining
these at org scope would hand ~89 private repositories a working hosted selector and invite
exactly the copy-paste reuse ADR 0040 watched regrow four times. GitHub's runner *groups*
enforce admission for self-hosted capacity, but they do **not** scope standard hosted
labels — there is no group-shaped way to say "only this repository may ask for
`macos-15`". A repository-scoped variable is the strongest containment GitHub's model
actually offers here, and everything else in this decision is defence in depth behind it.

**The image is pinned; `-latest` is never used.** `macos-latest` and `windows-latest` roll
over on GitHub's schedule, which silently changes the toolchain, SDK, and signing
environment that produce a shipped installer. A release artifact whose build environment
can change without a commit is not reproducible.

### Fail closed, with no fallback tail

The ordinary chain — `VERJSON_LANE_TRUSTED || VERJSON_LANE_FALLBACK || '["ubuntu-24.04"]'`
— is correct for Linux work and actively wrong here. An unset macOS lane that degrades to
Ubuntu produces a non-installable artifact behind a green check. The OS lanes therefore
carry **no fallback tail**, which is a deliberate exception to ADR 0040's rule that every
lane selector falls through to `VERJSON_LANE_FALLBACK`. The conformance check encodes the
exception; the workflow does not evade the rule.

An unset OS lane cannot be turned into an explicit in-job error, because `runs-on` is
evaluated before any step exists: `fromJSON(vars.UNSET)` produces a workflow-level startup
failure naming neither the variable nor the fix, and an *empty* array is accepted by GitHub
and queues forever with no check-run diagnostic — the #401 failure mode one level up. The
legible shape is a preflight job on `VERJSON_LANE_TRUSTED` that asserts the OS lane
variables are well-formed, non-empty JSON, which is the resolver tier already documented in
`docs/runner-routing.md`. A repository that copies the workflow without the variables gets
a hard, readable failure on self-hosted Linux rather than free hosted minutes.

### Dispatch-only, bounded, and minimal

The hosted legs run only on `workflow_dispatch` under the canonical release contract
(ADR 0038, ADR 0060). Never `pull_request`, never `push`, never a tag push. That bounds
spend to release cadence — a handful of runs a month — rather than to pull-request volume.

`timeout-minutes` is **mandatory and bounded**: the lanes are configured at 45 minutes, and
the conformance ceiling is 60. A presence-only check is not sufficient, and this is the
part most likely to be softened by someone in a hurry: `timeout-minutes: 360` satisfies
"has a timeout" while being exactly the runaway the requirement exists to prevent — six
hours at macOS's 10x multiplier is up to 3,600 billable minutes from one hung step, from a
single dispatch.

A hosted job does one thing: install dependencies, build its own OS's installer, upload the
artifact. Version resolution, the changelog snapshot, tagging, verification, release
creation, and asset attachment all stay on self-hosted Linux. Minimizing hosted job
*content* bounds spend far more than minimizing job count. A hosted job also receives no
`secrets: inherit`, and at most the single named registry credential its
dependency-acquisition step requires, scrubbed before any lifecycle-script step — artifact
*upload* itself needs no organization secret — and a hosted runner sits outside the
admission boundary that runner groups enforce for self-hosted capacity.

### `.github` does not grow a cross-platform release workflow

ADR 0060 retired `node-release.yml`, and the canonical release path is a dispatched
`changelog-release.yml` that **builds nothing**. AiB owns its own desktop release workflow.
So in this repository the sanctioned set for the OS lane variables is **empty**, and the
conformance rule is simply that no workflow here may reference them at all. The set is an
explicit constant in `scripts/ci-gate/hosted-selector-policy.py` rather than an implicit
absence, so #815 extends it deliberately rather than by accident.

The check does **not** hardcode `github.repository == 'Verjson/AiB'`. A repository
allowlist inside a shared workflow would look stronger while making a second desktop
repository a pull request in `.github` instead of a variable edit — precisely the property
ADR 0041 exists to preserve. The repository-scoped variables already are the allowlist; the
reference rule only makes the inability visible instead of implicit, and gives one grep
that answers "who can spend hosted minutes".

### The metered-SKU ban has zero exceptions — and one honest boundary

Any `runs-on` naming `macos-*` or `windows-*` fails. No allowlist, no parameter, no
environment override. No security-boundary argument has ever required either family, unlike
the closed `ubuntu-24.04` inventory of ADR 0089 — which stands **unchanged**, still limited
to the credentialless invalid-route guard and the privileged conformance audit, and still
pinned by exact-site equality so it cannot grow silently.

**The scope of that "any" is what the checker resolves, and saying otherwise would be a
defect in this record.** `scripts/ci-gate/hosted-selector-policy.py` parses each workflow
with `yaml.safe_load` and refuses — as *undetermined*, never as clean — any file it cannot
resolve into jobs: unparseable YAML, a non-mapping `jobs`, a non-mapping job, more than one
document, an anchor, or a file past the size ceiling. So the claim that holds is "no
selector this parser reads may name a metered family, and nothing it cannot read is passed",
which is materially stronger than the pattern-matching predecessor and materially weaker
than "no `runs-on` anywhere".

Expressions are accepted only when their complete normalized routing shape is in the
reviewed grammar. Blessing a reference name is insufficient: canonical callers may contain
guarded `inputs.runner` or `needs.preflight` clauses, but `${{ inputs.runner }}` and
`${{ fromJSON(inputs.runner) }}` alone remain undetermined. The same rule refuses arbitrary
repository variables and admits `matrix.os` / `matrix.lane` only while inspecting their
static strategy sources. Dynamic construction such as `format(...)`, `join(...)`,
arithmetic, or a mixed literal/expression selector is also undetermined because it can
assemble `macos-*` or `windows-*` without either complete word appearing in source. Both
GitHub dereference forms — `vars.NAME` and `vars['NAME']` — are normalized before every
rule, so bracket syntax cannot detach an OS lane from its timeout, fallback, and trigger
bounds.

The first implementation of this check matched `runs-on:` with line-oriented shell patterns,
and two review passes found five parser-level false negatives in shapes GitHub accepts: a
TAB/IFS field collapse, a matrix indirection, a flow-style job body
(`mac: {runs-on: macos-latest, …}`), a flow-mapping `jobs:`, and `runs-on : macos-15` with a
space before the colon. Each returned exit 0 on a genuinely metered job. That rate — five in
two passes — is why the parser was replaced rather than patched: patching known evasions
leaves the unknown ones, and for the single control standing between this organization and
metered spend, a false negative is silent and costs money.

### What no static scan can catch: larger runners

GitHub-hosted **larger runners** are configured at the organization level with
administrator-chosen labels. In `runs-on` they are textually indistinguishable from a
self-hosted fleet label — `runs-on: [self-hosted, big-linux]` and a hosted larger runner
named `big-linux` read identically — and they bill metered minutes. No amount of workflow
parsing can tell them apart, because the distinguishing fact is not in the workflow.

The containment for that class is **inventory**, not file reading. Measured at the time of
this decision:

```console
$ gh api /orgs/Verjson/actions/hosted-runners
{"total_count":0,"runners":[]}
```

Zero exist today, so the gap is currently theoretical — but it is one organization-settings
change away from real. [#820](https://github.com/Verjson/.github/issues/820) therefore
extends `scripts/ci-gate/runner-admission-reconcile.sh` to assert that inventory daily,
which is the right tier for it: the reconciler already holds the org-admin token and checks
fleet-level facts that the hot path cannot (`docs/runner-routing.md`, "Where each check
belongs"). Its reviewed identity allowlist is the repository file
`scripts/ci-gate/hosted-larger-runner-allowlist.json`, empty by default. A non-empty live
inventory is drift unless every exact `{id,name}` object is present there; an unreadable, malformed,
incomplete, or pagination-inconsistent response is undetermined and fails the scheduled
run rather than being mistaken for zero. The inventory and allowlist must match exactly,
so a rename or stale approval is drift too. Reports update only the comment owned by
GitHub Actions' immutable bot actor ID; a public commenter copying the marker is ignored,
and multiple trusted report comments fail closed. Organization-variable contents are
redacted before the public Actions log or durable issue report. Runner-group values are
rendered as lane identities plus safe numeric IDs; missing-group errors, admission drift,
default-group remediation, and clean output never echo the configured or live group names.
Re-run the command above rather than trusting this snapshot; runner inventories change
without touching this file.

### Two tiers, and why they differ

This is the part most likely to be re-litigated later as "the check is too noisy", so the
argument is settled here rather than in a future pull-request thread.

**Tier A — visibility-independent, zero exceptions.** The metered families are refused
regardless of what the repository is today. Separately, every standard hosted rolling image
— `ubuntu-latest`, `macos-latest`, and `windows-latest` — is refused under R2 regardless of
visibility; a mutable build image violates the pinned-image acceptance criterion even when
its minutes happen to be free. Repository visibility is a *mutable organization-settings fact*.
Encoding it in a workflow file is exactly the "encodes in workflow YAML a fact that lives in
org settings, so it is stale by construction" defect ADR 0033 diagnosed and ADR 0040 saw
repeat. A public repository flipped private turns a free `macos-latest` job into a 10x
metered one with no commit, no review, and no signal — so a refusal that depended on
visibility would be silently wrong at exactly the moment it mattered.

**Tier B — visibility-keyed.** Pinned literal Linux hosted selectors (`ubuntu-*`, such as
`ubuntu-24.04`) fail only when the target repository is private. `ubuntu-latest` already
fails visibility-independent R2. Hosted
minutes are free for public repositories (measured 2026-08-01; ADR 0047 corrected ADR 0033's
"unfunded" premise), so the same line spends nothing in a public repository and rides the
spending limit in a private one.

A public repository still should not hardcode a hosted selector, and the remedy is not a
literal. The organization already has a sanctioned path for public work to reach hosted
capacity: `VERJSON_RUNNER_FASTLANE`, a **variable** (ADR 0047/0048), already carved out in
the routing check. Because it is a variable, a capacity or provider move stays an
org-variable edit rather than a pull request in ~89 repositories, which is the ADR 0041
property. A hardcoded `ubuntu-24.04` reaches the same runner while giving that property
up. Enforcing that half for public repositories is #816; recording it here makes the
deferral a decision rather than an oversight. `.github` itself is held to the stricter
standard regardless, by its own ADR 0089 inventory, which this change does not touch.

### Amendment (2026-08-15) — the metered-family invariant reaches consumers (#815)

The reusable `actionlint.yml` now applies R1 to every Verjson caller's checked-out workflow
tree. The checker is sparse-checked from `job.workflow_repository` at `job.workflow_sha`, so
the policy under review cannot replace the policy reviewing it. Foreign callers skip both
the dependency bootstrap and the enforcement step, preserving ADR 0040's portability tail.

This export is deliberately **R1 only**. The full checker also contains `.github`'s rolling
Linux image, visibility-keyed literal Linux, lane-tail, OS-lane timeout, sanctioned-path, and
dispatch rules. Activating those across consumers would silently bundle #816 and repository-
local release policy into a spend-containment change. Consumer mode still parses every YAML
document and job with the fail-closed loader and folds referenced matrix strategy sources
into R1. It retains the reviewed expression grammar so `format`, `join`, or an arbitrary
input, variable, or needs output cannot assemble a metered selector invisibly; it simply
returns after the metered-family verdict for a selector whose shape can be resolved.

R1 covers both direct `runs-on` and the two canonical job-level reusable-workflow inputs that
delegate placement: `with.runner` and `with.runner_labels`. Only those names are inspected.
Other job inputs and every step-level `with` remain ordinary data, so prose mentioning an OS
does not become a placement false positive. A malformed reusable-job `with` mapping or an
unreviewed expression in either routing input is undetermined rather than clean.

PyYAML is not assumed to exist on a selected runner. The workflow downloads the pinned 6.0.2
source archive, verifies its SHA-256 digest before extraction, exposes only its pure-Python
`yaml` package through an isolated `PYTHONPATH`, and starts Python with site loading disabled.
The archive and package are extracted into a new mode-0700 directory allocated by `mktemp`
under `RUNNER_TEMP`, outside the caller checkout, and the exact bounded path is validated
before use and cleanup. This makes the parser dependency part of the immutable reusable-
workflow contract instead of an ambient or caller-prepopulated property of whichever runner
a lane selects.

### Amendment (2026-08-19) — correct two overclaims found during AiB's adoption (#810)

`Verjson/AiB#229`'s adoption (merged `Verjson/AiB#234` at `97e4528`) surfaced two places
where this record claimed more than the code delivers. Both are documentation corrections;
neither changes the decision, the containment primitive, or any control. Evidence:
[first finding](https://github.com/Verjson/.github/issues/810#issuecomment-5333643215),
[second finding](https://github.com/Verjson/.github/issues/810#issuecomment-5333760738).

1. **The guarantee is merge-time, not runtime.** Every control this ADR specifies — the
   fail-closed preflight, `timeout-minutes`, the dispatch-only trigger, the no-fallback-tail
   rule, `hosted-selector-policy.py` itself — is enforced on the *merged* workflow file.
   `workflow_dispatch` executes the workflow **as it exists on the dispatched ref**, not on
   the default branch. Once the spending limit is raised, anyone with write access to
   `Verjson/AiB` can push a branch whose workflow ignores the lane variables entirely — an
   uncapped `runs-on: macos-latest-xlarge` with no `timeout-minutes` — and dispatch it
   without opening a pull request, so no conformance check ever runs. The repository-scoped
   variables still hold their part of the guarantee: they confine this residual to `AiB`
   and keep the other ~88 private repositories out, because standard hosted labels are not
   variable-gated and a branch that ignores the lane variables is unaffected by them either
   way. The corrected, defensible claim is "no *merged* workflow may route metered minutes
   outside AiB's release legs" — not "the only jobs that may consume hosted minutes are
   AiB's dispatched installer legs." The residual is accepted, not closed: it is bounded by
   the spend backstop in #817 (the org- and repository-scoped Actions budgets with
   `prevent_further_usage=true`), not by this contract. Closing it is a runtime-side lever
   — required dispatch reviewers, restricted dispatch actors, or a ruleset on dispatchable
   refs — each with its own cost, and is left to a future decision if the residual proves
   material rather than theoretical.
2. **A private-dependency consumer's hosted job does receive one organization secret.**
   The original text said artifact upload needs no organization secret and left the
   impression the hosted job needs none at all. `Verjson/AiB`'s installer legs must run
   `npm ci` against `npm.pkg.github.com` to acquire `@verjson/ai`, so the hosted job needs
   `secrets.VERJSON_PACKAGES_TOKEN`. The corrected shape — the one AiB's adoption
   implements and this ADR now sanctions — is: no `secrets: inherit`, at most one named
   registry credential scoped to the acquisition step, an `.npmrc` written to `RUNNER_TEMP`
   holding the literal `${NODE_AUTH_TOKEN}` reference rather than the value, and a test
   asserting the token cannot survive into any lifecycle-script step. Artifact *upload*
   itself still needs no secret; dependency *acquisition* does.

### Amendment (2026-08-28) — generate the credential boundary for artifact releases (#1100)

The canonical `release-artifact` generator now implements the already-sanctioned private
dependency and OS-lane shape instead of forcing AiB to hand-edit generated output. Each
approved `@verjson/*` package is named at generation time. A per-runner acquisition matrix
validates the tagged lockfile's exact GitHub Packages URLs and integrity, installs with
lifecycle scripts disabled under only `contents: read` and `packages: read`, and saves the
result under a run-, attempt-, and canonical OS-matrix-index-bound cache key. The matching build matrix restores that tree
with package, GitHub, cloud, and OIDC credential variables blank before any adopter-owned
hook runs. This applies ADRs 0086 and 0095 to dispatched release builds; it does not grant
repository code a registry credential.

The generated contract independently records the approved package set, rejects additional
`@verjson/*` lock entries, and requires identical acquisition/build selectors, dependency
indices, and cache keys. Metered build legs keep this decision's 45-minute timeout.

`--build-runner` also accepts the exact no-fallback ADR 0103 expressions
`fromJSON(vars.CI_LANE_TRUSTED_MACOS)` and
`fromJSON(vars.CI_LANE_TRUSTED_WINDOWS)`. Other expressions remain generation errors,
and context-looking literals such as `vars.CI_LANE_TRUSTED_MACOS` are rejected rather
than silently becoming nonexistent runner labels. The generated contract independently
checks both matrices, their equality, the credential boundary, and hostile lifecycle and
selector mutations. Evidence is the registered
`scripts/ci-gate/changelog-caller-contract.test.sh` suite.

### Amendment (2026-10-09) — validate generated OS lane selectors before acquisition (#1723)

The generated `release-artifact` caller now validates its OS lane before snapshots,
artifact transfer, or metered build work. The credentialless preflight accepts only an
exact, versioned label from the requested OS family and rejects missing, malformed,
whitespace-padded, rolling, unversioned, and wrong-family selectors. It preserves the
validated selector unchanged so the selected lane remains paired with its matrix entry;
no credentials are introduced into this preflight. The generated contract test exercises
valid and rejected selectors against the emitted caller.

## Consequences

- The metered SKUs are refused by a check rather than by a spending limit, which is what
  makes the limit raise safe.
- An OS lane that is unset fails loudly on self-hosted Linux instead of quietly shipping a
  Linux binary as a macOS installer.
- A hung hosted step costs at most 45 minutes of wall time rather than six hours of billing.
- A sanctioned OS-lane workflow is accepted only when its trigger set is exactly
  `workflow_dispatch`; `pull_request`, `push`, and `schedule` cannot reach metered legs.
- One grep — for `CI_LANE_TRUSTED_MACOS` or `CI_LANE_TRUSTED_WINDOWS` — answers
  which workflows can spend hosted minutes.
- **The guarantee is split across the only two authoritative surfaces.** The parser refuses
  rather than guesses, so an unreadable workflow is undetermined and not clean; larger
  runners remain outside every static scan and are covered by the scheduled organization
  inventory reconciliation from #820.
  Stating the boundary is deliberate: an ADR that claims more than the code delivers is a
  defect in the durable record, and it outlives the code bug.
- **Accepted cost:** a future second desktop repository needs its own repository variables
  rather than inheriting them. That gives up one property ADR 0041 exists to preserve, and
  it is deliberate friction: a second hosted consumer should be an explicit act, not an
  inherited default.
- The rules live in a parameterized script with fixture-driven tests, because this
  repository has no macOS or Windows selector and no OS-scoped lane — an assertion against
  the live tree could never fail and would prove nothing. #815 points the same script at
  consumer checkouts without duplicating it.
- **Tier B is scoped to a *literal* selector — a `runs-on` value that is not an Actions
  expression.** Inside a `${{ … }}` chain, `'["ubuntu-24.04"]'` is ADR 0040's portability
  tail, which appears in the generated caller of every consumer; firing on it would reject
  ~89 private repositories for conforming to the contract, and a check nobody can keep
  switched on enforces nothing. This mirrors the strip that `runner-routing-policy.test.sh`
  already performs for the same reason.
- **A matrix indirection is judged against the whole `strategy` block by *every* rule, not
  only the referenced key.** The lane variable of a real desktop release lives in
  `strategy.matrix` rather than in `runs-on:`, so a rule that reads the selector line alone
  is absent on exactly the workflow it was written for. That is not hypothetical: the first
  implementation bounded and fail-closed only the direct form, and the matrix form — the
  shape #810 proposes — passed with no timeout and with a fallback tail. Consequently a
  metered word in an unreferenced cross-compile key is refused too.
  That imprecision is chosen: resolving the reference exactly means re-implementing YAML
  scoping inside the scan, and a bug there would fail *open*. A false positive costs an
  argument and is fixed by renaming a key; a false negative costs money.

## Rejected alternatives

- **Organization-scoped OS lane variables.** Readable by every repository in the visibility
  set, which hands ~89 repositories a working hosted selector. This is the copy-paste vector
  ADR 0040 documents regrowing four times.
- **A repository allowlist inside the shared workflow.** Looks stronger, but moves a second
  desktop repository from a variable edit to a `.github` pull request, reversing ADR 0041.
- **A presence-only `timeout-minutes` check.** Passes `timeout-minutes: 360`, which is the
  runaway itself.
- **Reusing the ordinary lane chain for the OS lanes.** Degrades a macOS leg to Linux and
  ships a non-installable artifact behind a green check.
- **Extending the ADR 0089 allowlist to cover macOS or Windows.** No security-boundary
  argument has ever required a metered SKU; the two rules protect different things and the
  metered ban is unconditional.
- **Keying Tier A on repository visibility.** Encodes a mutable org-settings fact in
  workflow YAML, so it is stale by construction and fails open the moment a repository flips
  private.
