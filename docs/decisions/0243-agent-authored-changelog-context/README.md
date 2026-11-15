# 0243 — Dependency updates require agent-authored changelog context

- **Date:** 2026-10-27
- **Status:** Accepted
- **Issue:** [verJSON/.github#1869](https://github.com/verJSON/.github/issues/1869)
- **Category:** release integrity and privileged automation — **sensitive class**
- **Related:** [ADR 0038](../0038-canonical-changelog-contract/README.md),
  [ADR 0104](../0104-trusted-renovate-changelog-attribution/README.md),
  [ADR 0232](../0232-fence-generated-callers-from-renovate/README.md)
- **Supersedes:** ADR 0104

## Context

ADR 0104 authorizes a `pull_request_target` workflow to parse a Renovate PR and
write a generated `NEXT/` fragment with a generic `patch` impact. The canonical
dependency-upgrade process now requires the agent to install the new version,
exercise the consuming repository, adapt affected code, and describe that work
in the fragment. A metadata parser cannot observe or describe those results.
Its generated fragment therefore cannot satisfy the release-context contract,
and the writer workflow grants a privileged credential to automate text whose
meaning depends on work performed outside the workflow.

The workflow also depends on a generated caller in every adopter and a
pre-write ref check followed by a force:false branch update. Those mechanisms do not resolve the mismatch
between a generic generated entry and the required exercised behavior. The
existing required `changelog / validate` and `changelog-contract` checks already
provide the correct merge boundary when an agent authors the entry.

## Decision

1. Renovate may open and update dependency pull requests, but no workflow or
   automation hook writes a `NEXT/` fragment for those changes.
2. The agent handling a dependency upgrade installs and exercises the bumped
   dependency, adapts the consumer as needed, then authors the fragment with
   accurate behavior and impact. The ordinary changelog validation remains
   required; no Renovate actor exception or weakened validation is introduced.
3. The canonical contract removes the Renovate attribution helper, reusable
   workflow, generated caller mode, and writer-only tests and capability
   declarations. Existing adopters delete
   `.github/workflows/renovate-changelog.yml`, then regenerate the full
   remaining caller set from one immutable contract SHA. The generated
   consumer contract applies a bounded, source-wide scan to every workflow file.
   It rejects the retired path case-insensitively in comments, run bodies,
   metadata, and anchor source, and after supported YAML escape decoding and
   whitespace compaction. It removes escaped line continuations for CRLF, CR,
   LF, NEL, line separator, and paragraph separator. Enumeration stops at the
   256-workflow limit before inspecting another candidate, with per-file and
   aggregate byte limits on content reads. It intentionally overmatches inert
   mentions; no YAML parser or dependency is added.
4. Historical fragments and released changelog snapshots remain unchanged.

## Consequences

- Dependency PRs remain blocked by the existing changelog gate until an agent
  records the work it actually exercised. The handoff for that queue is
  [verJSON/.github#2120](https://github.com/verJSON/.github/issues/2120),
  recorded in the 2026-11-15 amendment.
- Any textual mention of the retired path in workflow source must be removed,
  even when it appears only in a comment, run body, or unrelated metadata.
- The standard required changelog checks remain the source of enforcement; the
  retirement does not relax branch protection.

## Rollback

Do not reinstate the writer by restoring ADR 0104. Any future automation that
authors release context requires a new decision that satisfies the agent-owned
exercise and description requirements without weakening the required checks.

## Amendment 2026-11-15 — Renovate handoff is a signal, not a writer

The header still cites issue #1869 because that was the record named when this
decision was accepted. #1869 tracks node-ci performance and does not own the
handoff this decision left open. Renovate may open a dependency pull request,
and `changelog / validate` then fails closed until an agent authors the
fragment. That queue looked like ordinary CI breakage. The handoff is
[verJSON/.github#2120](https://github.com/verJSON/.github/issues/2120).

1. `check-pr` is unchanged. It still requires a valid new `NEXT/` fragment for
   dependency changes, for every author. There is no Renovate exemption, and
   no workflow writes a fragment.
2. Both changelog caller shapes run an informational step named
   `needs-changelog-context` before that gate: `changelog-validate.yml` and
   `generated-artifacts.yml` (the common adopter pin). The step fails, under
   that title, only when the pull request author is `renovate[bot]` or
   `renovate`, a dependency manifest or lockfile changed, and the pull request
   adds no valid new fragment. `continue-on-error` keeps the step from becoming
   a second merge gate. The required check remains `changelog / validate`.
   Adopters see the annotation when their changelog caller pin includes either
   workflow at a commit that carries the step.
3. The organization Renovate preset states the same contract in the pull
   request body: install, exercise, adapt, then author
   `NEXT/YYYY-MM-DD-issue-<id>-<slug>.md`. The slug matches
   `[a-z0-9]+(?:-[a-z0-9]+)*` and cannot contain dots. The body links this
   decision and the dependency-upgrade skill in `verjson-agents`. A static
   `needs-changelog-context` label is not applied. Renovate would reapply it
   after a valid fragment landed, so the annotation is the signal that clears.
   The preset body reaches the next Renovate run after `Verjson/renovate-config`
   merges that change.

## Amendment 2026-11-15 — generated-artifacts callers get the same handoff

The 2026-11-15 handoff landed first on `changelog-validate.yml`. Most adopters
pin `generated-artifacts.yml` (including verjson-cli). Without the same step on
that workflow, a pin bump to the earlier commit could not surface
`needs-changelog-context` on Renovate PRs. The informational step is therefore
duplicated on `generated-artifacts.yml` with the same body, author env contract,
and `continue-on-error` placement ahead of validation. Evidence:
verJSON/.github#2120 after verjson-cli#371.
