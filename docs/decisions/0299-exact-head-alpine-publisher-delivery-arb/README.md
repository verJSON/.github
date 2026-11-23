# 0299 — Record the Alpine candidate publisher delivery review

- **Date:** 2026-11-23
- **Status:** Conditional ARB recommendations. Archive and source admission each require ordinary protected review and CI.
- **Issue:** [VerJSON/.github#1264](https://github.com/verJSON/.github/issues/1264), [VerJSON/verjson-ci#30](https://github.com/verJSON/verjson-ci/issues/30)
- **Source:** [VerJSON/.github#2271](https://github.com/verJSON/.github/pull/2271)
- **Authority:** [ADR 0269](../0269-retire-owner-admin-merge-bypass/README.md), [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md)
- **Board:** [Chair record](./arb-record.md)

The board reviewed publisher source `d02ee2bc7d145d92e973cbc66a64aa9021ab421e`
against policy base `7c6cdb908255c47960654dc6e05d54cadbd59030`. The opt-in
`alpine-bubblewrap-amd64` profile stages two size- and SHA-256-verified APKs
from Alpine's public upstream before the Linux amd64 CLI image build. The
Docker build steps for this profile run with `network: none`. Existing adopters
retain their configuration.

The operator authorized a documentation-only archive first and separate
protected admission of the exact publisher source. The archive PR may cite
its own immutable chair record solely for archive publication. The source PR
may cite the separately landed record after independent byte verification on
policy main. The reviewed window permits one nonoverlapping, documentation-only
target advance for the archive, followed by exact combined-tree verification.
[Operator authorization](https://github.com/verJSON/.github/pull/2271#issuecomment-7090822094)

The CI adopter PR #368 remains held. VerJSON-owned packages and images are
intended for Sonatype Nexus. Package or image publication, Nexus and runner
changes, credentials, ruleset changes, and admin bypass remain outside this
window. [Held CI adopter](https://github.com/verJSON/verjson-ci/pull/368)
