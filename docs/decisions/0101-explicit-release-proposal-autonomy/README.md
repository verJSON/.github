# 0101 — Make release-proposal autonomy explicit and non-releasing

- **Date:** 2026-08-15
- **Status:** Accepted
- **Issue:** [#799](https://github.com/Verjson/.github/issues/799)
- **Category:** release automation and GitHub write authority (sensitive class)
- **Superseded in part by:** [ADR 0244](../0244-manually-dispatched-fragment-derived-release/README.md) (scheduled release dispatch)
- **Extends:** [ADR 0038](../0038-canonical-changelog-contract/README.md), [ADR 0060](../0060-node-release-retired/README.md)

## Context

The canonical release workflow requires an operator to state a version even
though the changelog engine accepts only the one version derived from the
selected `NEXT/` fragments. `next-version` made that value safely readable, but
adopters still had no canonical way to surface it or dispatch it. Fragments
could therefore accumulate until a human noticed them and transcribed a value
the engine already knew.

Scheduling a release directly would erase the intentional distinction between
deciding *when* to release and deriving *what version* is valid. Giving every
adopter both issue and workflow-dispatch authority would also make a repository's
chosen autonomy invisible in its caller permissions.

## Decision

The canonical contract provides a reusable `release-propose` workflow and a
generated adopter caller. Generation requires exactly one source-controlled
autonomy:

- `propose` grants only `issues: write` and maintains one marker-owned open issue
  containing the derived tag and the released-form preview;
- `dispatch` grants only `actions: write` and invokes the existing generated
  `Release` workflow with the derived tag and identical fragment/component
  selection.

Both callers retain `contents: read`, run on a daily schedule or explicit
operator dispatch, and serialize per repository without cancelling an in-flight
decision. The reusable workflow fails outside the default branch, derives the
version through the pinned `next-version`, and renders the exact selected
fragments through the same pinned engine. The generated `Release` run name binds
its derived version and a canonical digest of the component, prefix, and selected
fragment identities into the Actions run record. Dispatch mode searches all run
pages for that exact version, head, and selector digest before dispatching, and
waits for the same receipt afterwards, making retries idempotent across the
workflow boundary without conflating two fragment subsets that derive the same
version.

The dispatch also carries the derived default-branch head and selector digest as
inputs. `Release` rejects a partial or malformed receipt, rejects a head other
than the dispatch commit before checkout or verification, and recomputes the
selection digest with the pinned contract before any snapshot or publication.
Manual dispatch remains supported only when both receipt fields are absent. A
scheduled run whose selection is empty succeeds without applying either GitHub
effect; an explicit invalid selector still fails.

The proposer never calls `release`, writes the checkout, consumes fragments,
commits, tags, or pushes. Its only dispatch target is the generated `Release`
workflow. `Release` alone retains the `verify → snapshot → publish` ordering and
the dedicated release-App authorization for its atomic snapshot push.

## Consequences

- Adopters choose proposal visibility or direct dispatch in reviewed source;
  an event input cannot upgrade that authority.
- Proposal mode creates an issue only during future adopter workflow operation,
  updates it in place, and fails closed if multiple surfaces claim ownership.
- Dispatch retries do not create another exact-version, exact-head release run;
  failed releases remain an operator-visible recovery decision rather than a
  schedule-driven retry loop.
- A component or explicit fragment subset produces a preview matching the set
  passed to `Release`; deferred fragments stay out of that preview, and a
  different subset has a different receipt even when it derives the same tag.
- The version namespace is carried through proposal, snapshot, and Node
  publication. Stream tags such as `python-v1.2.3` publish package version
  `1.2.3`; prefix and component remain independent as established by ADR 0070.
- An empty scheduled stream is a green no-op rather than an operational failure.
- First releases still require an explicit baseline because `next-version`
  refuses to invent a version without prior release history.

## Verification

The registered changelog-release suite executes generated caller shape,
mode-specific permissions, serialization, pagination, API failure, proposal
deduplication, empty-selection behavior, exact-head binding, selector mutation,
stream namespaces, exact dispatch inputs and receipt acknowledgement. All GitHub
mutations use mocked clients in tests. The disposable contract exercise derives
and previews selected fragments without changing the tree, then passes the same
derived version to the exact pinned `release` path.
