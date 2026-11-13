# 0250 — Propose offline snapshot receipt inspection

- **Date:** 2026-10-29
- **Status:** Proposed
- **Issue:** [#1954](https://github.com/verJSON/.github/issues/1954)
- **Category:** release provenance and authorization

## Context

Downstream publishers need evidence that a release snapshot corresponds to the
intended producer run, source and immutable Git objects. Existing snapshot
creation does not issue authenticated run-bound evidence. Historical-tag
retries must not derive current policy from historical repository contents.

## Proposal

Prototype strict receipt decoding and offline consistency checks against
independently supplied expected context and a local Git object database. Check
the observed annotated tag and its direct commit, the commit's sole parent and
tree, and the regular changelog blob's digest. This is a local observation: the
prototype returns no permission, signature verdict, or publication decision.
Its receipt format is experimental and is not a deployed producer contract.

## Trust and unresolved boundaries

The caller must independently obtain and authenticate the expected repository,
workflow and run context and Git observations. Local repository configuration and the Git executable on
PATH are trusted inputs. The inspector disables inherited global and system
Git configuration, replacement objects, lazy fetching, and terminal prompts.
It rejects effective repository, included, and worktree partial-clone or
promisor configuration before reading objects,
including when a Git version ignores the lazy-fetch environment flag. It cannot turn a hostile repository or executable into trusted evidence. A
receipt's claims and a matching
hash do not authenticate an issuer. A future design needs a reviewed signing
format, issuer and verifier trust policy, signed exact bytes, key custody and
revocation, and a nonproduction integration receipt. No signer or live GitHub
attestation is asserted here.

Neither issuance time nor expiry is validated. A trusted clock, replay scope,
durable atomic acceptance and recovery after uncertain writes remain required.
Current release policy must come from a separately protected authority or broker;
this prototype does not implement or depend on one. A local tag check does not
prevent remote retargeting, including ABA, between observation and publication.
Prove immutable ref enforcement or an atomic conditional operation before a
consumer relies on tag stability. Historical releases without authenticated
receipts require separately authorized recovery, not invented evidence. Existing
callers and release holds are unaffected.
