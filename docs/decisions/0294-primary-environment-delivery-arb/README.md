# 0294 — Archive the primary environment delivery board

- **Date:** 2026-11-22
- **Status:** Accepted with conditions for five source heads and this archive
- **Issue:** [#1264](https://github.com/verJSON/.github/issues/1264)
- **Authority:** [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md)
- **Board:** [Chair record](./arb-record.md)

## Context

Moving internal Git, CI and artifacts toward GitLab CE and Nexus requires changes in the CI library, the operator CLI, fleet documentation and the mirror rehearsal. Each repository has separate checks and release authority. The delivery board reviewed the exact code or documentation head for each change and recorded the limits of its approval.

## Decision

Publish the [six board decisions](./arb-record.md). Five identify reviewed source heads. The sixth authorizes this archive after independent verification of the final files, canonical digest and applicable platform checks. A publisher may cite the archived run for a listed head after its independent review, current repository rules, required CI and applicable approvals pass. A new head requires fresh adjudication.

## Operational scope

The git-only rehearsal still requires its attended window and entry checks. The storage conversion, release publication, customer access and transfer of Git authority keep their separate approval and recovery gates. Production agent credential issuance stays disabled under the single-operator constraint.
