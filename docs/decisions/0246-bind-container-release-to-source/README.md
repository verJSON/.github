# 0246 — Propose source-bound container release admission

- **Date:** 2026-10-28
- **Status:** Proposed
- **Issue:** [verJSON/.github#1949](https://github.com/verJSON/.github/issues/1949)
- **Related:** [#1948: candidate versions and selected fragments](https://github.com/verJSON/.github/issues/1948), [ADR 0078](../0078-container-release-and-runner-deployment-contract/README.md), [ADR 0216](../0216-keyless-container-provenance-is-verified-per-registry/README.md)
- **Category:** release identity and provenance (sensitive class)

## Context

The current promotion planner authenticates a candidate against its original
candidate run, but accepts a different release dispatch source commit. The
release workflow records the dispatch commit separately. A valid signature on
an old candidate therefore does not establish that its image represents the
source and fragments at dispatch. The existing changelog selection digest
binds identities, component, and prefix; by itself it does not bind fragment
contents, source tree, prior history, candidate bytes, or image digests.

A successful release records a manifest and creates a snapshot commit and tag.
A later retry may run after `main` advances: the existing workflow loads the
original release context from that tag, then compares the reconstructed release
manifest. Comparing the candidate to the *new retry dispatch SHA* would reject
such a retry. Historic v2 and v3 release bytes and signatures are immutable.

## Proposed contract for a new release

This is a proposal, not an implemented gate or authority to publish. A new
release is explicitly dispatched. Building a version-neutral candidate on a
push does not select, tag, or publish a stable release.

1. Bind the authorized initial dispatch commit `D`, repository identity,
   protected ref, candidate source commit, immutable candidate manifest digest,
   publisher and contract identities, and every image index and platform digest.
   The default new-release policy requires candidate source commit to equal `D`.
   A later source is not accepted merely because it is a descendant or because
   a path filter says it is documentation-only. Any future tree-equivalence mode
   needs a separately reviewed proof of the complete build inputs and materials.
2. For a monolithic release, select the complete unscoped fragment set at `D`;
   reject subsets, mixed scopes, missing fragments, duplicate identities, and
   empty selection rather than claiming omitted changes were not shipped. A
   scoped stream may be supported only with an explicit independently reviewed
   artifact-boundary contract; otherwise, outstanding scoped fragments make a
   monolithic release fail closed. Run the pinned changelog engine against the
   immutable source and released history, validate the explicitly dispatched
   version, and bind its resolved version and prior release identity.
3. Record a canonical, versioned selection receipt containing `D`, its tree,
   the sorted fragment identities, paths and blob digests, component and
   prefix, existing selection digest, release-history identity and digest,
   exact engine/contract commit, resolved stable version, candidate artifact
   digest, and image digests. Bind its canonical digest into the signed release
   evidence. The identity-only selection digest is not a substitute for this
   receipt. Reject mismatches before registry or Git mutation.
4. Preserve the schema and verification semantics of v2 and v3 historical
   release manifests. Do not rewrite or reinterpret them. A new incompatible
   selection-bound record requires a distinct schema version and explicit
   validators; old versions cannot be implicitly upgraded by changing a parser.
   Full-set restrictions and neutral candidate identifiers belong in the
   coordinated #1948 contract, not a silent relaxation of v4 candidate rules.
5. Verify candidate signatures and immutable image identities before preparing
   promotion. New-release admission must not treat the mere existence of a Git
   tag, manifest filename, or caller-provided `resume` flag as a trusted
   exception. The full selection and source binding must be checked before
   stable aliases can be changed.

## Resume and historical compatibility

A resume is identified by the original signed release record and tagged
snapshot, not by the retry's current `GITHUB_SHA`. Verify the existing tag,
version-specific manifest bytes and signature/attestation, original release and
candidate source identities, candidate artifact digest, recorded operations,
registry state, and release assets before repeating any recorded operation.
An equal existing alias is idempotent; a divergent alias, replaced tag, missing
proof, malformed record, or different selected input fails closed. A retry may
complete only the already authorized recorded release and cannot add a new
candidate, selection, version, or operation.

For a new selection-bound release, require a signed, durable post-snapshot
receipt before any stable alias is published or repaired. It binds the
repository identity, stable version, fully qualified tag ref, tag object ID and
peeled release commit ID, release tree ID, original source commit, immutable
selection-receipt digest, candidate artifact digest, trusted signer/workflow
identity and contract revision. It also binds either the complete ordered
promotion operation list or a domain-separated, versioned canonical digest of
that list. Each operation identifies its registry/provider, destination
repository, stable alias, immutable artifact digest, and authorized prior-state
precondition. The encoding fixes field meaning and ordering and rejects missing,
duplicate, ambiguous, or unrecognized operations. The consumer verifies the
signature and authority, the tag object's current ref binding and peel, the
commit and tree, the version, the complete ordered operations, and the selection
and candidate evidence together before the first stable mutation. A hash or a
caller-supplied record alone is not an authenticated receipt. The receipt must
be stored in a durable append-only evidence system with its own verified
acceptance; neither the receipt nor the storage capability exists merely because
this decision proposes them.

If a snapshot or tag exists but that receipt is missing, invalid, unavailable,
or inconsistent, report a partial release and stop before stable alias mutation
or publication. Do not fabricate or infer a receipt from a tag. Recovery may
issue a replacement only under a separately reviewed authority contract after
the original authorized selection, exact tag object, commit/tree and provenance
are independently verified; otherwise hold and surface the missing evidence.
Once a valid receipt exists, a retry may reconcile only that exact ordered set
of recorded operations and artifact digests, subject to the recorded prior-state
preconditions and verified idempotency. The signed plan remains complete; a
retry may skip an operation only after verifying its already-completed exact
state, and executes the remaining operations in the recorded order. Substituting,
adding, omitting, duplicating, or reordering entries in the plan requires new
authorization and evidence; it is not a retry. A mismatched or replaced tag
remains a failure even if its contents appear similar.

For legacy v2/v3 records whose candidate and recorded dispatch sources differ,
retain their exact historical bytes and supported read/verification behavior.
Do not silently impose new fields or rewrite them. Whether a pending legacy
retry can be safely completed depends on verified original signature and
operation receipts, not on invented source equivalence. Establish that
version-specific proof with real historical fixtures; absent proof, hold the
retry and escalate the specific missing evidence. No proposed compatibility
exception is a general permission to start a new mismatched release.

## Ordering and unresolved external boundary

The current workflow promotes stable registry aliases before the Git
snapshot/tag push. A remote ref or prior-history change after preflight can
therefore leave external state even when the later push fails. This proposal
requires an independently reviewed transaction and recovery design binding the
release intent before stable publication. It must specify safe ordering,
immutable attempt state, exact-digest idempotent recovery, and visibility of an
incomplete release. It must not use a force push, bypass a ruleset, or report a
partially published release as complete.

An exact SHA comparison or lease cannot detect a ref that moved away and back
(ABA). No local mock proves GitHub's remote ref or ruleset behavior, and a Git
lease is not an atomic transaction with a registry. Server-enforced monotonic
ref evidence or a trusted complete ref-update audit is required where the
selected transaction depends on detecting that history. Until that proof and
the registry recovery contract are available, do not claim #1949's source
binding is implemented or enable promotion by weakening a check.

## Admission and recovery prerequisites

The release authorization must identify the initial dispatch source before the
run starts. The dispatch supplies that expected commit, and the trusted workflow
verifies the actual resolved source and authorization against it before mutation;
a moved ref or missing authorization fails closed. Identity, scope, expiry,
revocation, and separation of the dispatcher's authority from candidate code
must be specified and verified under the applicable identity policy. A candidate
for the exact authorized source must be available, including when push path
filters skipped its build; a trusted credentialless build for that source or an
explicit hold is required. Do not substitute a nearby candidate.

The present release App has an always-bypass role on default-branch rulesets.
This proposal does not grant that role to autonomous admission or resolve the
snapshot write path. Before rollout, define and verify an authorized non-bypass
snapshot transaction and its recovery, or hold the release; do not infer safety
from dropping an admin flag. Any change to existing bypass policy requires its
own reviewed decision and applicable protection checks.

Require an annotated signed tag with a verifiable tag object for new
selection-bound records. A retry must execute the originally recorded trusted
contract revision, or a separately verified compatible successor, with an
independently authorized signer and evidence-store trust root. Record key
custody, revocation, accepted identities, store durability, and acceptance
receipts before enabling this path. A missing store or signer holds publication.
For outstanding scoped fragments or legacy retries lacking proof, name an owner
and a separately reviewed resolution; never fabricate source equivalence or
silently omit changes. #1948 tracks the selection contract.

The transaction design must specify states and recovery at each Git and registry
boundary, including concurrent writers. A prior-state comparison alone is not
an atomic registry compare-and-swap. Prove an enforceable serialization or
conditional update mechanism for every destination; otherwise hold that
promotion path. Preserve the Git ref ABA limitation until a server-enforced or
auditable mechanism is proven.

## Verification before delivery

- Unit and mutation tests cover matching and stale sources, malformed commits,
  changed fragment bytes under the same identity, omitted or added fragments,
  conflicting release history, wrong candidate/image digest, wrong contract,
  empty selection, and explicit version disagreement before mutation.
- Exercise first release, pre-alias failure, each partial-alias boundary,
  post-snapshot/tag retries, already-existing GitHub Release, main advancement,
  tag replacement, and v2/v3 historical fixtures. Verify exact byte preservation
  and negative signature/provenance cases; no fixture stands in for a live
  provider receipt.
- Verify that missing, forged, stale, revoked, or unavailable post-snapshot
  receipts, changed tag refs or objects, substituted commits/trees, and mismatched
  selection digests all stop before stable alias mutation. Exercise recovery only
  against separately authenticated original evidence and exact recorded operations.
- Mutation tests substitute each registry, repository, stable alias, artifact
  digest, and prior-state precondition; add, omit, duplicate, and reorder
  operations; and verify every mismatch stops before the first stable mutation.
- In a disposable clean checkout, exercise the exact pinned changelog release
  command and verify the commit, tag and tree. In an authorized non-production
  registry, separately test immutable artifact promotion and restart behavior.
  Preserve explicit dispatch, scoped identity, independent evaluation, and all
  existing no-bypass controls. No live ruleset, runner, or credential change is
  authorized by this proposal.
