# 0300 — Record the GitLab namespace source admission review

- **Date:** 2026-11-23
- **Status:** Conditional ARB recommendations. Archive and source admission each require ordinary protected review and CI. Production activation remains held.
- **Issue:** [VerJSON/.github#1264](https://github.com/verJSON/.github/issues/1264), [VerJSON/verjson-ci#30](https://github.com/verJSON/verjson-ci/issues/30)
- **Source:** [VerJSON/verjson-ci#370](https://github.com/verJSON/verjson-ci/pull/370)
- **Authority:** [ADR 0269](../0269-retire-owner-admin-merge-bypass/README.md), [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md)
- **Board:** [Chair record](./arb-record.md)

The board reviewed CI source `fd5f9c4fd1939d7a304554df5722e4e77d9327b2`
on base `68facb416c6751408061ff15701f895fd6cd79a6`. The source aligns
the exact GitLab namespace spelling in the registry gateway, promotion
broker, and source sync. Existing GitHub identity and workflow constraints
still apply. The recommendation covers ordinary protected source admission.

Production URL and service activation remain held. The staged package's
normal promotion broker command refuses under the active
[release hold](https://github.com/verJSON/verjson-ci/blob/fd5f9c4fd1939d7a304554df5722e4e77d9327b2/docs/decisions/0062-single-ci-library-package/README.md).
Qualify supported broker delivery separately under
[verJSON/verjson-cli#187](https://github.com/verJSON/verjson-cli/issues/187)
before any coordinated rollout.

Archive transport requires separate operator authorization. The source PR
can cite this exact chair record after ordinary archive admission and
independent verification of its immutable landed bytes.
