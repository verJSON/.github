# 0104 — Trusted automation attributes hosted Renovate changes

- **Date:** 2026-08-16
- **Issue:** [Verjson/.github#834](https://github.com/Verjson/.github/issues/834)
- **Related:** [Verjson/renovate-config#32](https://github.com/Verjson/renovate-config/issues/32), [ADR 0038](../0038-canonical-changelog-contract/README.md), [ADR 0099](../0099-release-app-replaces-admin-pat/README.md)
- **Category:** privileged write / authentication (sensitive class)

## Context

The canonical changelog contract intentionally has no actor exception: a
dependency manifest or lockfile change must add release context in `NEXT/`.
Hosted Renovate cannot create that fragment itself because its generated branch
has no repository credential, so every otherwise valid dependency update is
blocked by `check-pr`. Consumer-side exceptions or handwritten bot workflows
would weaken and fragment the organization contract.

An ordinary `pull_request` workflow cannot write the bot branch. A
`pull_request_target` workflow can use base-branch credentials, but executing or
checking out pull-request-head code in that event would cross the secret trust
boundary. `GITHUB_TOKEN` pushes also suppress the follow-up workflow events that
must validate the new commit.

## Decision

Add a generated, immutable `pull_request_target` caller and a trusted reusable
workflow in `Verjson/.github`. The caller is read-only and delegates at one
40-character contract SHA. The reusable workflow checks out only
`Verjson/.github` at its executing `job.workflow_sha`, and rejects a
`contract_ref` that differs from that SHA; it never checks out or executes
consumer PR-head code.

Admission is fail-closed and repeated against the live pull request. The PR must
be open, same-repository, authored by `app/renovate` or `renovate[bot]`, target
the triggering base, retain the exact triggering head, and use a normalized
`renovate/*` branch. Its body must contain exactly one structurally valid
Renovate update table with bounded, safe package and version fields. A valid
fragment newly added by the PR makes the workflow a no-op only when it declares
an explicit `major`, `minor`, or `patch` impact.

Only after planning requires a write does the workflow mint the dedicated
Release App token from ADR 0099. The request is limited to the current
repository and Contents write. PR metadata, changed files, and file contents
are read only with the job token's explicit `pull-requests: read` and
`contents: read` grants; the App token is never used for PR reads. A trusted
Python helper uses the App token only for Git Data API reads and mutations: it
creates one blob, tree, and commit whose sole parent is the admitted head, then
updates that exact head ref with `force: false`. It revalidates the live PR,
update table, added fragments, and ref immediately before mutation. A moved
head, malformed body, path collision, fork, different author, or different
branch fails without writing.

The fragment is
`NEXT/YYYY-MM-DD-issue-<pr>-renovate-dependencies.md`, records every parsed
package with its from/to version, and declares patch impact. The App-authored
push emits the normal `synchronize` event, so the canonical changelog and CI
checks evaluate the resulting head. The generated contract test verifies the
caller pin, event, credential mapping, and least privilege whenever adopters
move the contract SHA.

## Consequences

- Consumers adopt one generated caller through the same immutable-pin path as
  the workflow, renderer, and contract test; no local exception is supported.
- The workflow handles only Renovate's reviewed table shape. A new upstream body
  format fails closed until the canonical parser and tests deliberately change.
- The Release App private key remains organization-scoped as documented in ADR
  0099. The minted token is repository-scoped, but any trusted base workflow in
  a repository where that organization secret is available remains within the
  credential's existing trust boundary.
- First adoption requires the generated caller to land on the base branch. A
  subsequent representative Renovate PR is the live canary for fragment push
  and follow-up validation.

## Rollback

Regenerate adopters at the prior immutable contract SHA or remove only the
generated attribution caller. Revert the canonical workflow and helper. Existing
fragments and changelog history remain valid and are not deleted.

## Amendment (2026-08-25) — generated contracts enforce admission (#1014)

The generated adopter contract now binds the `renovate-changelog` job to the exact
same-repository, Renovate-actor, and `renovate/` branch admission expression emitted by
the canonical generator. Removing or weakening any conjunct fails locally before the
trusted reusable workflow can receive the Release App credential. This closes an
enforcement gap in the existing trust boundary; it does not change the admitted actors,
events, permissions, or token scope.

## Amendment (2026-09-10) — distinct Docker variant transitions (#1271)

Duplicate attribution rows are identified by the complete parsed package/from/to
transition, not the package name alone. Grouped Alpine and Bookworm updates for
one Docker image remain distinct and both appear in the generated fragment.
Exact duplicate transitions still fail, including equivalent linked and bare
package spellings. This restores grouped-update attribution without changing
author admission, table ambiguity checks or fragment publication authority.

## Amendment (2026-10-21) — identical transition rows collapse

Renovate writes one update-table row per file, manager, or build stage. When one
image is bumped in two Dockerfiles, the same package/from/to transition appears
in more than one row, and the rows differ only in columns such as `Type`.
Verjson/verjson-ci#290 (job 118966588750) was refused for that reason, although
it is a valid Renovate pull request. The 2026-09-10 rule refused exact duplicates
as a fail-closed default. It gave no threat that a repeated row could carry, and
none exists: a repeat adds no package, version, or text to the fragment.

The parser now drops a row whose complete parsed transition is identical to an
earlier one and keeps the first-seen order. Identity remains the exact parsed
`(package, from, to)`, so equivalent linked and bare package spellings collapse,
and so do rows that differ only in cell padding. Case is significant because
ecosystems such as Go modules and Maven treat case-variant names as different
packages. Rows for the same package with a different from or to version are
distinct transitions and all appear in the fragment, as decided on 2026-09-10.
Refusing them would also refuse #290, whose alpine and alpine3.23 tags both bump
`node`. The 50-row bound counts every table row before collapsing, so repeating
rows cannot raise the input bound. Every other row check still runs on each row.
Author admission, the single-table requirement, credential scope, and fragment
publication authority do not change. The trusted reusable workflow runs the
parser from its pinned contract checkout. The generated caller is unchanged, and
adopters receive the change when they move their contract SHA.
