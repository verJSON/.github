# 0191 — A contract version is a release, declared once and read back from disk

- **Date:** 2026-09-17
- **Status:** Accepted
- **Related:** [ADR 0014](../0014-reusable-workflow-versioning/README.md), [ADR 0108](../0108-bound-package-retention-to-three-stable-releases/README.md), [ADR 0185](../0185-org-contract-distribution/README.md), [ADR 0186](../0186-required-checks-bind-through-repository-properties/README.md)
- **Issues:** [#1374](https://github.com/Verjson/.github/issues/1374)

## Context

[ADR 0185](../0185-org-contract-distribution/README.md) found the root cause:
**the contract has a commit identity instead of a release identity.** A `uses:`
SHA is a content address, so there is no "version 3 of the contract", no way to
say which versions are supported, and no way to deprecate one. It decided the
direction — release the contract, one version per adopter, a supported window,
two channels — and left the mechanics open. [#1374](https://github.com/Verjson/.github/issues/1374)
is those mechanics. This ADR decides four things and nothing else: what a
contract version *is*, how an adopter declares it, what the supported window is,
and what deprecation and expiry look like.

### What already exists, and why it has not helped

The uncomfortable fact is that a release identity is not missing. [ADR 0014](../0014-reusable-workflow-versioning/README.md)
established semver releases of this repository as a whole, `docs/reusable-workflow-versioning.md`
documents how to pin them, `tag-major.yml` re-points the moving `vX` tag on every
published release, and `v1.0.0` through `v2.3.0` exist right now.

| Fact | Source |
| --- | --- |
| The newest release is `v2.3.0`, published 2026-08-08 | `gh release list --repo Verjson/.github` |
| `main` is **401 commits** past it | `git rev-list --count v2.3.0..origin/main` |
| Generated adopter artifacts pin a 40-hex SHA, never a tag | `scripts/gen-changelog-caller.sh <mode> <sha>` |
| 22 of the 29 `verjson-core-checks=enforced` repositories carry a review caller predating the `ai_review_environment` input, across **eleven** distinct contract SHAs | fleet sweep, #1374 |
| Intra-repository skew is real: `verjson-ci` ran `ci.yml` at one SHA and `changelog.yml` at another | [Verjson/verjson-ci#184](https://github.com/Verjson/verjson-ci/issues/184) |

So the release train exists and is bypassed. Every mechanism the organization
actually depends on — the generator, the contract test, the pinned
`scripts/changelog.py` — names a commit, and any of those 401 commits is an
equally legal thing to name. **A version nobody is required to use is not an
identity**, and eleven SHAs across one input's adopters is what that looks like
when measured. This ADR is therefore not about inventing a version. It is about
making the version the *only* legal thing to be on, and about being able to read
back which one a repository is actually on.

## Decision

### 1. A contract version is a published `Verjson/.github` release, and a legal pin is a release commit

A contract version is a `vX.Y.Z` GitHub Release of this repository, cut by
`workflow_dispatch` under the canonical contract — never derived from a merge —
with `scripts/changelog.py release` consuming `NEXT/` into an immutable
`CHANGELOG/<version>.md`.

The `uses:` pin **stays a 40-hex SHA**, and that is deliberate. A `uses:` must be
a git ref; a tag ref is mutable, and `tag-major.yml` force-updates `vX` on every
release, so a tag pin hands the hub the org-wide blast radius [ADR 0010](../0010-platform-templates-consume-reusable-workflows/README.md)
and [ADR 0014](../0014-reusable-workflow-versioning/README.md) exist to remove.
What changes is not the *form* of the pin but its **legal range**: a contract SHA
must be the commit of a published release. That single restriction is what
converts a commit identity into a release identity, because it makes the set of
legal values small, enumerable, and named.

Restricting the range is the whole move. Today the answer to "which contract is
this repository on" is a 40-hex string with no meaning outside a content diff.
Under this decision it is a lookup in a list of releases, and "is it behind" and
"is it still supported" are comparisons on that list.

**This restriction has a precondition, and it is the first entry under
[Consequences](#consequences): the release train must be running before the pin
constraint is enforced.** Enforcing it while `main` sits hundreds of commits past
the newest release converts "adopters rot on their own schedule" into "adopters
cannot advance at all". The decision is stated here; the sequencing is stated
there, and the two are not separable.

### 2. An adopter declares exactly one version, in one file

`.github/verjson-contract.json`, with exactly one adopter-controlled field:

```json
{ "contract_version": "v3.2.0" }
```

The declaration carries the **version only, never the commit**. A declaration
holding both would let the two disagree, reintroducing at the declaration the
skew the declaration exists to remove. The commit is resolved from this
repository's release metadata at check time.

One file and one field is what makes "a repository is partially on two
contracts" unrepresentable rather than merely discouraged.

### 3. The declaration is verified against the repository's real tree

This is the load-bearing half, and it is what distinguishes this decision from
adding a version field nobody reads. **A version an adopter declares but nothing
verifies fails in exactly the way a pin nobody advances does.** So:

> Resolve the declared version to its release commit, then require **every**
> `Verjson/.github` reference on disk — every `uses:` pin and every generated
> header claim — to name that commit.

Four properties follow, and each one is a thing the rejected alternatives could
not do:

- **Intra-repository skew becomes a check failure by construction.** It is not
  discovered by a sweep after the fact; a pull request that introduces a second
  contract SHA cannot pass.
- **It is a version lookup and one equality on a resolved object id**, not a
  per-file blob diff against the default branch. [ADR 0185](../0185-org-contract-distribution/README.md)
  rejected that diff for failing open, for being the wrong granularity (a file
  can be byte-identical while a transitively referenced script has drifted), and
  for reporting comment-only upstream edits as drift. None of the three survives
  once there is a version to compare.
- **The scan is every tracked file, not a list of known adopter files.** Scoping
  it to a list would reproduce the defect it exists to catch: a reference the
  list did not name is precisely the skew that was measured. The boundary is the
  git index — `git ls-files` — because the index is exactly what Actions checks
  out and executes, so it is the set within which a contract reference can
  actually reach a workflow run. That is wider than a directory walk over the
  content that ships (a tracked file under an ignored directory is in the index
  and is scanned, and `.git` needs no special case because git's own object
  storage is never indexed) and narrower only over things no run ever sees:
  untracked build output, tool caches, downloaded runner binaries. Scanning
  those was not more total, only louder — each oversize binary in them became an
  `UNSCANNED` finding no adopter could clear, which is a permanent exit 1 and
  therefore the muted check [ADR 0185](../0185-org-contract-distribution/README.md)
  warns about. A tracked symlink is its target *path*, never its target's
  content. Within that boundary totality is a claim with teeth only if the gaps
  are named, so the implementation makes each one visible rather than silent: a
  tracked file that is unreadable, past the scan limit, or text in an undecodable
  encoding is an `UNSCANNED` finding, not a `continue`. A file holding a NUL byte
  in its first 8000 is skipped on git's own binary heuristic, because it cannot
  carry a UTF-8 `uses:` line and reporting every image in a repository is
  precisely the noise ADR 0185 says gets a check muted — and that heuristic runs
  *before* the size limit, so a binary is quiet at any size rather than becoming
  an `UNSCANNED` finding once it passes 1 MiB. A declared UTF-16 BOM is decoded
  ahead of the heuristic: a UTF-16 file is full of NUL bytes and carries a
  perfectly readable `uses:` line in its own encoding, so treating it as binary
  dropped a real skew as neither finding nor gap. Comment lines are candidate header claims
  wherever they occur, not within a leading window: `gen-changelog-caller.sh`
  stamps `CONTRACT_REF` on line 13 of one generated file and emits the release
  caller's header below `concurrency:`.
- **The edges of that boundary are decided here, not left to whatever `open()`
  happens to raise.** Each one is the difference between a gap an adopter can act
  on and either a permanent exit 1 or a silent PASS.
  - *Submodule content is out of scope.* A submodule is an index entry of mode
    `160000` whose path is a directory, so reading it raises `IsADirectoryError`
    and it became an `UNSCANNED` finding no adopter could ever clear — the muted
    check this section already refuses. `actions/checkout` does not fetch
    submodules by default, so that content is not what the enforcing run
    executes, and the `160000` entry is skipped explicitly rather than failed on.
    A submodule that *is* checked out in a workflow is verified by running this
    check in the repository that owns it.
  - *A sparse checkout is refused, path by path.* Every skip-worktree path
    absent from the work tree stays an `UNSCANNED` gap. Skipping them instead
    would let a local sparse run report a PASS the enforcing run — which checks
    out the whole tree — cannot reproduce, and this check fails closed. What the
    gap now says is *why*: the file is exactly where git left it and the
    checkout is what is partial, rather than a missing-file error that reads as
    a broken tree.
  - *UTF-16 without a byte-order mark is a gap, not a decode and not a drop.* It
    is as full of NUL bytes as the declared form, so the binary heuristic
    dropped it with no finding and no gap — a clean PASS on a file whose `uses:`
    line is sitting there in its own encoding. A BOM is a claim and is decoded;
    an undeclared encoding is a guess, so the alternating-NUL shape of UTF-16
    text raises a gap to resolve by hand instead of a verdict computed from a
    guess. The shape, not a trial `decode("utf-16")`, is the discriminator:
    nearly every binary decodes as UTF-16, and a gap on every image is the noise
    that gets a check muted.
  - *A tree with no work tree is a refusal, not an empty scan.* `git ls-files`
    in a bare repository exits 0 with no output, so the sweep scanned zero
    files, found zero references and reported PASS — the "scanned nothing, found
    nothing" degradation that is indistinguishable from conformance in an exit
    code. A bare repository, and a `.git` directory handed in as the root, raise
    the same refusal as an absent git. An index that is genuinely empty is the
    opposite case and stays a legitimate empty scan: that tree really was read
    end to end.
  - *A repository with no declaration and no reference is silent only when the
    scan finished.* That silence is what keeps this check from firing on every
    repository in the organization, but it is a statement about a completed
    scan, so a tree that also carries gaps reports them rather than passing
    clean. It reports the gaps alone: whether the repository owes a declaration
    is precisely what the unread files might have answered.
- **A generated header is read as a claim, never as an instruction.** Headers
  remain human traceability per [ADR 0185](../0185-org-contract-distribution/README.md)'s
  sixth point; here a header disagreeing with the declaration is an *alarm*, and
  nothing privileged consumes adopter-controlled text.

It runs in two places, from one implementation. Adopter-side, in the caller lane,
for fast feedback on the pull request that would introduce the skew. Hub-side, in
the read-only fleet sweep, which needs no adopter cooperation and reaches
repositories we do not own. **The hub-side result is authoritative**; the
adopter-side check is convenience, because a repository can delete its own check
and the fleet sweep is what notices.

Every unresolvable input is `UNKNOWN`, never `SUPPORTED`. A release whose commit
does not resolve to a 40-hex object id fails closed — `target_commitish` is a
branch name for many releases, and comparing a pin against the string `main`
would report the entire fleet broken.

**The cost of failing closed here is a producer obligation, and it is real.**
`gh api repos/Verjson/.github/releases` returns a 40-hex SHA for `v2.3.0`,
`v2.2.0` and `v2.1.0` and the literal string `main` for `v2.0.0`, `v1.1.0` and
`v1.0.0`. A release-list producer built on `target_commitish` therefore refuses
legitimately published older versions — a false failure, not a detected defect —
so the producer must peel the tag instead, and `load_releases` validates that
rather than trusting it. Peeling means `refs/tags/<tag>^{}`: an **annotated** tag's
ref names the tag object, whose id is 40-hex and would pass every check here
while being the wrong commit. Every current tag on this repository is
lightweight, so the unpeeled form works today by luck.

### 4. The supported window is two minor lines, never narrower than the newest three releases

[ADR 0185](../0185-org-contract-distribution/README.md) said "current and
previous minor". Stated alone that is wrong, and #1374's canary is exactly where
it breaks:

> `@verjson/compliance` and `@verjson/compliance-schema` sit at `0.2.0/0.3.0/0.4.0`,
> exactly the three-version retention floor, and consumers pin `@verjson/*`
> exactly with no range.

[ADR 0108](../0108-bound-package-retention-to-three-stable-releases/README.md) keeps the **three most recent
stable versions**. Three releases on three minor lines — `v3.0.0`, `v3.1.0`,
`v3.2.0` — leave `v3.0.0` unsupported under a two-minor-line rule while retention
still keeps it. **A window narrower than retention strands a consumer the
organization's own policy calls current.** So the rule is a union, not an
intersection:

> **Supported = every release in the two most recent minor lines, ∪ the three
> most recent releases.**

The union makes the window a superset of retention by construction, so the
interaction cannot strand anyone regardless of the cadence minors are cut at.
The minor-line half is what gives a fast-moving quarter a wider window than three
patches would; the retention half is what gives a slow quarter a floor.

### 5. Deprecation is dated from the release that caused it; expiry refuses

A version outside the window is **deprecated**, not immediately refused, and the
deprecation names a date:

- The clock starts at the publication of **the release that pushed the version
  out of the window**, not at the version's own release. The obligation appears
  when the window moves, and that is the moment a maintainer could first have
  acted.
- **90 days** within a major. **180 days** when the release that pushed it out
  crossed a major, because a major by definition requires adopter work rather
  than a repin.
- Before the date: a deprecation finding carrying the date. On and after it:
  refusal.

The expiry date is *computed from release metadata*, not stored. A stored
deadline is one more thing that can drift from the releases it describes.

A warning state is a real hazard — [ADR 0185](../0185-org-contract-distribution/README.md)
notes that noisy checks get muted, which is how the previous invalid drift test
survived. This one is acceptable because it is rare by construction (it fires
only on a repository genuinely outside the window), it carries a hard date rather
than an open-ended complaint, and it converts to a refusal on that date whether
or not anyone read it.

### 6. Expiry is enforced by the required-check binding, not by the workflow refusing

A repository running an expired contract is executing hub code at an old,
immutable ref. The hub cannot reach back and make that ref refuse — immutability
is the point — so "refuse" cannot mean the reusable workflow declining to run.

Enforcement is the **required check**. Per [ADR 0186](../0186-required-checks-bind-through-repository-properties/README.md),
required checks bind through repository properties, so the contract-version check
binds to `verjson-core-checks=enforced` and an expired declaration blocks merges
in those repositories without their cooperation.

State the reach honestly: **29 repositories carry that property; roughly 95 carry
a contract reference.** For the remainder, expiry is detection and a tracked
issue, not a block. That asymmetry is not a flaw in the mechanism — it is [ADR 0185](../0185-org-contract-distribution/README.md)'s
"detection covers ~95, remediation covers 8" restated at the gate — but a
decision that quietly implied fleet-wide enforcement would be false.

## Rejected options

- **Declare the version in a comment header.** Free, and already half-present.
  Rejected: a header is per-file, so N files means N declarations and the skew is
  representable again; and it puts adopter-controlled prose where a parser wants
  a schema.
- **Pin the moving `@vX` tag instead of a commit.** Removes skew, removes the
  audit trail, and re-arms the org-wide blast radius [ADR 0014](../0014-reusable-workflow-versioning/README.md)
  removed. A pin that can mean something different tomorrow cannot answer "which
  contract did this repository run".
- **Derive the declared version from the pins rather than declaring it.** Tempting,
  since the pins are already there and this needs no adopter file. Rejected: it
  makes skew *unrepresentable in the report* rather than illegal — a repository on
  two SHAs derives two versions and there is nothing to compare them to. The
  declaration is what turns skew from an observation into a contradiction.
- **Keep content diffing, but fix its fail-open.** This is the cheapest option and
  it is the one #1374 forbids reproducing. Fixing the fail-open leaves the
  granularity and noise defects, and leaves "am I current" answerable only by
  comparison rather than by name.
- **A calendar-only window (support every version for N days).** Simple and
  predictable, but it decouples support from the release train: a quarter with no
  releases expires the only contract anyone is on.
- **Support exactly the three retained versions, dropping the minor-line rule.**
  One rule instead of two. Rejected: it ties the contract's compatibility promise
  to a *storage* policy, so raising `VERJSON_PACKAGE_RETENTION_KEEP` would silently
  widen a compatibility guarantee. Retention is a floor here, not the definition.

## Consequences

- **The release train must run before the pin constraint is enforced, and this is
  the real risk in this decision.** Restricting legal pins to release commits
  means a fix is not adoptable until a release is cut. With `main` 401 commits
  past `v2.3.0`, enforcing the constraint today would convert "adopters rot on
  their own schedule" into "adopters cannot advance at all". Cutting releases on a
  cadence is a precondition of enforcement, not a follow-up to it.
- Every generated adopter artifact must be regenerated at a *release* commit. The
  generator's argument is already a SHA, so its interface does not change; what
  changes is which SHAs are admissible.
- A new adopter-resident file, `.github/verjson-contract.json`, across ~95
  repositories — sequenced with the migration [ADR 0185](../0185-org-contract-distribution/README.md)
  already scopes, and canaried on the eight owned repositories.
- Expiry gives this repository the ability to drop compatibility code on a stated
  clock, which it has never had.
- The trade-off, recorded plainly: propagation latency for identity. A contract
  that can only be adopted at a release is slower to fix and possible to reason
  about. That is the correct side of the trade for a control plane ~95
  repositories execute.

## Verification

`scripts/contract-version.py` implements the window, the clock, and the readback,
and its regression suite is `scripts/ci-gate/contract-version.test.py`, registered
in `scripts/actions-ci-groups.tsv` under the `platform` group. The assertions that
matter are the fail-closed ones: an unreleased or malformed version is `UNKNOWN`
rather than a soft `SUPPORTED`, a release whose commit is not a 40-hex object id
is refused rather than compared against a branch name, an expired declaration is
refused at `verify` even when every pin in the tree agrees with it, and an
unreadable, ambiguous, or unvalidatable releases document exits 2 rather than
reporting a tree it never compared as conformant.

The totality claim in §3 carries its own assertions, because a missed reference
reads as a clean PASS and the "declares a version but governs nothing" net only
fires on a tree with *zero* references — never on a partial miss. Each case
builds a tree with one conformant pin plus one skewed reference and requires a
finding: a single- and a double-quoted `uses:` scalar, a header below the old
six-line window, a caller vendored under `node_modules`, and an unreadable,
oversize, or undecodable file. The quiet cases are pinned too, so the noise
budget is a test rather than a hope: a binary file reports nothing, a
`node_modules` symlink is skipped rather than followed into an `UNSCANNED` gap
no adopter could clear, a 64-hex digest on a line naming the hub is not
truncated into a 40-hex SHA, and a commented-out pin is one reference, not two.

Two limits on how far that reads. `.git` is pinned by a test — a 40-hex ref
written into `.git/COMMIT_EDITMSG` reports nothing — but no *mutation* backs
that test, because the exclusion is by construction: `_tracked_paths` enumerates
`git ls-files`, which never names `.git`, so there is no guard to delete. Read
the `.git` row as a boundary the suite observes, not one it defends. And
the digest case is a constructed line, not a sampled one — no file here puts a
64-hex run on a line that also names the hub — so the boundary defends a shape
the header pass admits rather than one it has already met. Each guard above is
checked by deleting it and requiring the suite to redden, because a green run
over a guard nothing exercises is the sentence-instead-of-a-measurement this
ADR's own §3 was found relying on (#1463).

§3's totality is a claim about the *file set*, and #1433 measured the other
half — which `uses:` **shapes** the recognizer sees within a file it read.
Across 1208 tracked YAML files and 7361 Markdown files in 94 organization
repositories plus this one, 1553 `uses:` keys and 432 hub references, the
line-based recognizer missed **zero** real references: every shape that occurs
is a single-line scalar, quoted or not, some with a trailing comment and five
with CRLF. The shapes it could not see are shapes nobody writes — a quoted or
space-padded key, a value on the following line, a block scalar, an alias to an
anchor, and a path assembled from an expression, each with **0** occurrences.
The recognizer was widened for the two it can read exactly (a quoted key, space
before the colon) with no change to any of the 432 references measured, and the
rest are now `UNRESOLVED_REFERENCE` gaps rather than silence, on the same
principle as `UNSCANNED`: a shape the scan cannot resolve is named, not read as
"this repository has no reference". That gap half is anchored to the start of a
line and requires the file to name the hub — without both, the measured corpus
produces 30 findings from prose that ends a sentence with `uses:`, from
`statuses:` containing `uses:`, and from this suite's own fixture strings; with
both, it produces none. It is also case-sensitive, alone among these patterns:
Actions requires a lowercase `uses` key, so a case-insensitive one can only invent
gaps — `- Uses:` in English prose inside a file that names the hub — and can never
catch a pin. **The stated ceiling:** the scan stays line-based and
will not join continuation lines or resolve anchors to read a reference, and a
hub path assembled at run time from an expression — where the owner and
repository never appear literally in the tree — leaves no text for any scan of
the tree to find. The first is reported as a gap; the second cannot be, and is
recorded here rather than implied away.

Not verified here, and deliberately so: no release has been cut, no adopter
declares a version yet, and the required-check binding in §6 is decided rather
than wired. The steps that implement them carry their own verification.

## 2026-10-20 amendment — Header claims the hub name does not mark

§3 requires every generated header claim to name the declared commit, and cites
`CONTRACT_REF` on line 13 of the ADR index test as one. The header pass only
recognized a comment that names `Verjson/.github` before the SHA on the same
line, so that claim was never read. Neither were the other shapes the
organization generators stamp without the hub name:

- `# CONTRACT_REF=<sha>` (`gen-changelog-caller.sh adr-index-test`);
- `# Contract: <sha>` (`gen-container-candidate.sh`, `gen-container-release.sh`,
  `gen-container-deployment.sh`);
- `# scripts/gen-<name>.sh [<mode>] <sha> …`, the regeneration line in the AI
  review, label re-arm, gate re-arm, privileged merge and container candidate
  callers.

A stale SHA in one of those alone therefore passed `verify`. The observed case
was line 3 of a generated `ai-review-label-rearm.yml`, which went unnoticed
because the file's `uses:` pin was current. This amendment restores the
invariant. `GENERATED_HEADER_RE` reads the three shapes. Each one is anchored to
the full comment a generator stamps, because nothing on the line names the hub
to key on. The regeneration shape keys on the hub's `scripts/gen-` convention
rather than a list of names, so a new generator is read without an edit.

`scripts/ci-gate/contract-version.test.py` gives each shape a stale-SHA finding
and a current-SHA pass. It also refuses lookalikes: prose after the SHA, a 64-hex
digest, an uncommented assignment, a mention of a generator, and a non-`scripts/`
path. The test runs every mode of every SHA-stamping `scripts/gen-*` generator at
a real commit and requires every comment line carrying that SHA to be read.
Generators that stamp no adopter SHA are exempted by name with a reason, so a new
generator has to be either exercised or exempted.

The hex in all three shapes reads case-insensitively, as the hub-named pass
already did. Both passes report a claim in lowercase. A commit id names the same
object in either case, so case alone neither hides a claim nor turns a current
one into a `PIN_MISMATCH`. The words around the hex stay exact, because the
generators stamp them exactly.

Two related gaps are outside this repair. The generated contract test's own
`CONTRACT_REF="<sha>"` assignment is not a comment, and the contract test already
asserts that every generated member shares it. The fleet inventory's separate
`HEADER_RE` reads only a six-line window of workflow files.
