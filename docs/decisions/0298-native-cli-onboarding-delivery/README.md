# 0298 — Record the native CLI onboarding delivery review

- **Date:** 2026-11-22
- **Status:** Conditional ARB recommendations. Archive and CLI source admission remain held pending exact-head verification and protected approvals.
- **Issue:** [VerJSON/verjson-cli#187](https://github.com/verJSON/verjson-cli/issues/187)
- **Source:** [VerJSON/verjson-cli#407](https://github.com/verJSON/verjson-cli/pull/407)
- **Authority:** [ADR 0269](../0269-retire-owner-admin-merge-bypass/README.md), [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md)
- **Board:** [Chair record](./arb-record.md)

The board reviewed the read-only schema command at CLI source
`99c156c40e5f77248e204680eb4faddc193071ff`. The CLI reads both published JSON
schemas from a single installed `@verjson/ci` 3.0.0 package. CI owns the schemas
and portable contract. CLI owns command routing, help, and the canonical
response. The same dependency supplies the existing custody API. Plan,
canary verification, apply, and issuance remain outside this delivery.

The operator authorized separate protected paths for the archive and for the
exact CLI source. The archive PR may cite its own immutable chair record solely
for archive publication. The CLI PR may cite it after ordinary archive landing
and independent byte verification on policy main.
[Archive authorization](https://github.com/verJSON/verjson-cli/issues/187#issuecomment-7087798028)

The #407 window covers only the two named optional promotion workflows,
their verified restoration, and the ordinary protected merge of the reviewed
CLI source. Required CI and rules remain active, and #384 stays held. This
window applies only to #407. A CLI package release, credentials, retention
settings, policy changes, and production operations require separate authority.
[CLI source window](https://github.com/verJSON/verjson-cli/pull/407#issuecomment-7087661894)
