# 0244 — Derive manually dispatched release versions from fragments

- **Date:** 2026-10-28
- **Status:** Proposed
- **Issues:** [#1939](https://github.com/VerJSON/.github/issues/1939), [#1940](https://github.com/VerJSON/.github/issues/1940)
- **Category:** release automation and GitHub write authority
- **Supersedes:** [ADR 0218](../0218-require-explicit-release-dispatch-version/README.md), and the scheduled dispatch provision of [ADR 0101](../0101-explicit-release-proposal-autonomy/README.md)
- **Extends:** [ADR 0038](../0038-canonical-changelog-contract/README.md) and [ADR 0183](../0183-release-plan-resolves-optional-version/README.md)

## Context

The release manager decides when to release, but the selected `NEXT/` fragments already carry the information needed to compute the version. Requiring the manager to transcribe that result adds work and creates an opportunity for error. ADR 0218 requires a version at dispatch; the owner has directed that manual release initiation remain explicit while the version is calculated automatically.

## Decision

The generated node, artifact, and snapshot callers offer an optional `version` input. When it is empty, the existing pinned `release-plan` derives the next tag from the highest impact of the selected fragments and the latest immutable release snapshot for the selected namespace. If supplied, a version is validated against the same plan. Whitespace-only and invalid explicit values are errors. The selected source commit, namespace, selection digest, and resolved version remain bound before the snapshot and publication steps. Summaries expose the resolved plan. The authorized release manager explicitly initiates a dispatch; no tag-push or main-merge release trigger is added. Scheduled proposal-only runs may continue, but scheduled release dispatch is rejected in both the generated caller and reusable dispatch workflow. GitHub workflow_dispatch can also be invoked via API, so this event check does not by itself prove a direct human session; existing credential and review controls remain necessary.

The existing empty-selection behavior remains a successful no-op without a release. A new namespace with no prior release snapshot requires an explicit initial version; it is not inferred. Automatic calculation requires a plain stable snapshot; qualified-only history requires an explicit bootstrap version whose core is not below any existing qualified snapshot; another qualified version at the same core is rejected as ambiguous. If any prerelease or build-qualified snapshot has a core newer than the latest plain stable snapshot, release planning fails closed until the history is reconciled. Existing legacy fragments without impact retain the established patch default; newly introduced fragments must meet the canonical impact validation contract. Version arithmetic follows the canonical engine's major/minor/patch rules, including for `0.x`. Prerelease sequencing remains an explicitly selected release workflow rather than an inferred stable bump.

The existing exact-head, selection-receipt, provenance, release credential, concurrency, immutable pin, and verify → snapshot → publish controls remain in force. If selection or source no longer matches, fail instead of silently retargeting. Retrying after an existing snapshot uses the canonical restart-safe path; it must not rewrite an immutable release or duplicate a tag.

## Consequences

A manager can initiate a release without manually calculating its tag, while an explicit valid version remains possible for bootstrap and controlled cases. The plan is visible in the release run and only becomes authoritative for that run. Adopters must regenerate the complete canonical caller set from one reviewed immutable contract revision. This decision does not activate GitHub Actions, deploy artifacts, change release cadence, or grant additional approval authority.

## Verification

Generator and release caller tests cover all three caller modes, blank and explicit versions, empty selections, mixed impacts, bootstrap, invalid values, and selection binding. Exercise the exact pinned release command in a disposable clean checkout before delivering an adopter. Remote workflow evidence remains pending while organization Actions are paused.
