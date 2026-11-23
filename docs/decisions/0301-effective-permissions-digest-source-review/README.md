# 0301 — Archive the effective permissions digest source review

- **Date:** 2026-11-23
- **Status:** The board unanimously accepted the reviewed source subject to the conditions in the [chair record](./arb-record.md).
- **Issue:** [Identity Contracts #147](https://github.com/verJSON/verjson-identity-contracts/issues/147)
- **Source:** [Identity Contracts #161](https://github.com/verJSON/verjson-identity-contracts/pull/161)
- **Review policy:** [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md)

The board reviewed the Node export `digestEffectivePermissions`. It binds the
catalog version to a digest of a permission set whose role list is empty.
The decision covers commit `3124ea27e6cacf2c117446c7b5f0cad825011330`
against base `7fdad98e198503e21f151c9c6e41f4c32caa977a`, with reviewed
tree `660cc0e5389e19191aeede3603747f7ffc69bc86`.

The operator authorized publication of the [original chair record](https://github.com/verJSON/verjson-identity-contracts/pull/161#issuecomment-7114441288)
through ordinary protection. Before #161 can cite this archive, verify that
the native ruleset audit passed for the archive merge and that the landed file
matches the original bytes. The full record has SHA-256
`f9c141b3e6731d01594c5b7a376e93d7de8079da912f559618bb3f29bfdebf52`.
The canonical slate inside it has SHA-256
`046a4ffb8f509af0411b1da74f7cb387115a47a7c789fb914e25421d21a1d312`.

The source PR retains its hold and its own exact-commit review requirements.
Package release, consumer adoption and production agent credential issuance
are outside this decision.
