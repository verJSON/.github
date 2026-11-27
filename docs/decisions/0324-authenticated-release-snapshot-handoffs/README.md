# 0324 — Propose authenticated release snapshot handoffs

- **Date:** 2026-11-27
- **Status:** Proposed
- **Issue:** [#1954](https://github.com/verJSON/.github/issues/1954)
- **Extends:** ADR 0248 (downstream completion hold) and ADR 0250 (snapshot receipt inspection)

## Context

A successful dispatch run and the release tag currently named by it are two
independent observations. A downstream `workflow_run` delivery needs evidence
from the issuer that actually observed the committed snapshot. A release event,
run title, successful checkout, or retried historical hook cannot supply that
evidence. The [architecture review disposition][review] remains changes
requested. This proposal advances the offline verifier while the publication
hold remains in force.

## Evidence and trust

Use a DSSE envelope with payload type
`application/vnd.verjson.snapshot-handoff.v1+json`. The bounded UTF-8 JSON
payload carries `issuer`, `consumer`, `policyRevision`, `repositoryAuthority`,
`destinationId`, integer Unix seconds `issuedAt` and `expiresAt`, `subjectId`,
`subjectSha256`, and `snapshot`. The nested object uses ADR 0250's schema to bind
repository ID, immutable producer workflow, dispatch run and attempt, source commit, release
version, full tag ref and annotated tag object, snapshot commit and tree, and
changelog digest. SHA-256 digests use the `sha256:` prefix and lowercase hex.
The issuer signs the DSSE pre-authentication encoding of the exact payload
bytes. The envelope carries one signature in standard padded Base64. A `keyid`
hint has no effect on verification. The envelope, decoded payload, and decoded
signature are limited to 48 KiB, 20 KiB, and 8 KiB respectively.

The consumer's protected policy origin supplies the current policy revision,
expected issuer, verifier and signature algorithm, consumer, maximum lifetime,
and the authorized source-to-destination route. The route pins the repository
authority, immutable repository ID, immutable producer workflow, and canonical
destination ID. The signed source must match both the route and the independent
delivery. The signed destination must match protected policy and the publisher
must write to that destination. The policy resolver must authenticate explicit
policy for ordinary consumers and fail closed when authoritative policy cannot be established. A
missing repository flag supplies no classification. Evaluate policy before
checking out a historical tag, running a historical hook, or returning
completion. Revocation and a new policy revision must take effect at the
publication boundary as well as at admission.

The provider adapter independently authenticates a successful explicit
dispatch attempt, immutable producer workflow identity, repository authority
and ID, and the associated subject and envelope artifacts. It must bind the
fully fetched and hashed bytes to those artifacts and the exact attempt. The
subject and the envelope are separate objects. The envelope's transport digest
is verified from the authenticated observation outside the signed payload. A
trusted clock must be inside the signed interval `[issuedAt, expiresAt)`.
`scripts/snapshot_handoff.py` freezes the dispatch observation and enforces these
bindings before Git inspection. The inspector checks the exact tag ref and its
unpeeled object ID in a trusted local repository. The verifier yields
point-in-time evidence with both signed time bounds for the publisher's
acceptance transaction. The tests use an ephemeral Ed25519 issuer.
The wire profile leaves production issuer provisioning and verification to a
separately qualified adapter.

## Transaction order and recovery

1. The pinned producer resolves current policy and authenticates the explicit
   dispatch inputs. It produces the snapshot with the pinned release engine and
   durably records the intent before any remote mutation. The intent binds the
   dispatch and operation identity to the exact source branch and tag preimage
   and the intended commit and tag object. The producer atomically updates both
   refs with compare-and-swap preconditions and confirms the exact postimage at
   the authoritative remote before issuing the receipt for that attempt and subject.
   The snapshot-ready run can then succeed and deliver its receipt while the
   GitHub Release remains absent or draft.
2. The publisher verifies the handoff and atomically claims the publication
   scope `(repository authority, repository ID, tag ref, destination ID)`.
   Resolve authority and destination aliases to the same durable IDs before
   issuing or accepting evidence. The scope stays stable across consumer renames,
   policy changes, retries, and ledger recovery. If several source scopes can
   write the same provider resource, enforce conflicts at the destination too.
   Enforce current policy and receipt freshness inside the claim transaction.
   Persist the payload digest, immutable tag object and subject digest with the
   claim. An exact duplicate can retrieve the recorded outcome or join
   its reconciliation. Another payload for that scope cannot start a second
   effect. Any authorized replacement of a pending claim must be fenced and
   reconciled before changing ownership.
3. The publisher must enforce receipt freshness, current policy generation,
   current claim fence, and the same immutable tag at the provider's write
   commit. Enforcement occurs at the provider or through an authoritative
   broker that serializes these checks and effect commitment. An expired pending
   claim requires separately authorized reconciliation before any further write. The tag
   check requires continuously enforced ref immutability from
   creation, or an effect conditioned on an authoritative ref revision that
   detects changes away and back. A read followed by an unconditional write
   cannot supply that guarantee. The downstream effect needs a stable operation
   identity and an independently recoverable outcome across process failure,
   concurrent deliveries, and ledger restore. Confirm the effect at the provider
   before recording completion. Only authenticated completion for the original
   scope and immutable subject can authorize publication of the GitHub Release.
4. If the initial push response is lost, reconcile against the recorded intent,
   exact remote postimage, current policy, and authenticated provider evidence
   linking the update to the original operation. Hold if the provider cannot
   prove which operation performed the update. If the downstream write response
   is lost, query the authority that actually controls the effect. Reissue the
   same operation only under provider-enforced idempotency, or when the provider
   proves the effect is absent and the earlier request can no longer commit.
   Apply current authorization and claim fencing before any retry. An
   ambiguous result stays pending for reconciliation. A restored ledger or a
   repeated local receipt number cannot prove continuity. A historical release
   that lacks authenticated evidence stays held for separately authorized
   recovery. Preserve its original tag and changelog snapshot.

## Qualification

The offline verifier is tested with a real local changelog release, real
signatures, substituted dispatch and artifacts, expiry, and a tag replaced by a
different annotation pointing at the same commit. Previous [restart][restart]
and [concurrency][concurrency] experiments model lost responses, concurrent
claims, and ref changes using disposable SQLite stores. Qualification still
requires an independently enforced policy and issuer, a durable publisher and
reconciliation mechanism, and a separately authorized nonproduction receipt
for dispatch delivery, ref stability at the effect, duplicate suppression, and
recovery of an ambiguous write. The generated caller's hold remains in place.

[review]: https://github.com/verJSON/.github/issues/1954#issuecomment-7120662635
[restart]: https://github.com/verJSON/.github/issues/1954#issuecomment-7134496147
[concurrency]: https://github.com/verJSON/.github/issues/1954#issuecomment-7137729220
