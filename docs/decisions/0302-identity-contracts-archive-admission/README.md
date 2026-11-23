# 0302 — Admit the Identity Contracts decision archive through ordinary protection

- **Date:** 2026-11-23
- **Scope:** Publication of the documentation in [policy PR #2277](https://github.com/verJSON/.github/pull/2277).
- **Issue:** [Identity Contracts #147](https://github.com/verJSON/verjson-identity-contracts/issues/147).
- **Authority:** [Owner ratification](https://github.com/verJSON/.github/pull/2277#issuecomment-7116859854) and the [archive admission chair record](./arb-record.md), subject to [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md).

[ADR 0301](../0301-effective-permissions-digest-source-review/README.md)
preserves the source review for Identity Contracts #161. Its chair record
covers the source commit. The owner ratified a separate decision for the
one-time publication of that record in #2277.

The board reviewed the four original files at commit
`57a471fc03bd833c1548b18bd795e4ccfbc11d4c`, based on
`e899bb4eed8063679fb2dc6d14bd3f60f646350f`, with tree
`c1d0bcb00b64269ae682c0edabd7b8155dcbd3fb`. The original chair file has
SHA-256 `f9c141b3e6731d01594c5b7a376e93d7de8079da912f559618bb3f29bfdebf52`.
The revision adds this ADR and its chair record, regenerates the decision
index, and updates the existing release note.

For #2277 alone, the final commit may cite this companion chair record after
an independent reviewer attests to the exact base, head, tree, changed paths
and record digests. Publish the citation and review receipt in order under the
existing admission policy. Use an ordinary protected merge with server-side
bypass disabled. Verify the native ruleset result and the landed bytes before
treating the archive as published.

[Identity Contracts #161](https://github.com/verJSON/verjson-identity-contracts/pull/161)
remains held. Its delivery, package publication and credential issuance require
separate authority.
