# Verjson/.github — repo working notes

Org-level `.github`: the merge gate (`ai-review-merge.yml`), reusable workflows
(`helm-ci`/`pulumi-ci`/`ui-ci`), composite actions, and decision records. These
conventions augment the workspace and global `~/.claude` rules; where they
conflict, the more local one wins.

## Running log — add a NEXT/ fragment, never edit a shared file

This repo does **not** keep a prepend-only `NEXT.md`. In the same commit as a
change that affects behaviour, pins, docs, or config, add a **new** file
`NEXT/YYYY-MM-DD-issue-<issue-number>-<slug>.md` (see `NEXT/README.md` for
metadata and the issue-less exception).
Because no two PRs touch the same file, the log can't produce merge conflicts —
which is the whole point. Read the log with `scripts/render-next.sh`. `NEXT.md` is
a static pointer; don't add entries to it. Since v3.0.0 (2026-09-27) the repository also
tracks `CHANGELOG/<version>.md` snapshots and the aggregate `CHANGELOG.md` that the
dispatched release writes (`.github/workflows/release.yml`); no PR edits either — a
correction is a later release.

## ADRs — add a directory, let the index generate

Decisions live at `docs/decisions/NNNN-<slug>/README.md` with a `# NNNN — Title`
H1 and a `- **Date:** YYYY-MM-DD` line. **Do not hand-edit the index table** in
`docs/decisions/README.md` — run `scripts/gen-adr-index.sh` to regenerate it from
the ADR directories, and commit that. `actions-ci` runs `gen-adr-index.sh --check`
and fails if the committed table is stale. On a rebase, re-run the generator
instead of hand-merging table rows. Sensitive-class changes (auth/RBAC, rulesets,
runner topology, IAM/OIDC, secrets, merge-gate behaviour) still require an ADR.
For a bug fix that restores an invariant already recorded in an ADR, amend that
controlling ADR with the dated rationale and evidence; reserve a new ADR number
for a new or superseding decision. “Restoring intended behaviour” is not an
exemption from decision-record coverage.

## CI-gate tests

Gate shell tests execute the current named workflow steps against stubbed
dependencies or exercise checked-in helpers directly. Add or extend a behavioral
test for every gate change and register it in `scripts/actions-ci-groups.tsv`; an
unregistered test does not run in Actions.

## Autonomous batches — review before AI merge authority is enabled

The org gate defaults to human approval, but code, executable dependency,
workflow, policy, prompt, and agent-instruction changes automatically receive
one or two cumulative AI review passes. Generated lockfile-only and non-agent
documentation changes may use the no-model lane.
An operator can set `AI_REVIEW_AUTHORITY=ai-merge`, which can merge a green PR
in ~1–3 minutes before an out-of-band `code-reviewer` pass finishes. When that
authority is enabled for non-trivial or fanned-out autonomous work:

- Run the independent `code-reviewer` **before pushing**, or open the PR as a
  **draft** (the gate skips drafts) / apply the **`hold`** label until the review
  passes, then mark ready / remove `hold`. `DO NOT MERGE`/`hold` are honored as
  terminal holds (ADR 0012).
- Worktree agents may be cut from a **stale** base — fetch real `origin/main` and
  branch from it before working, and keep local `main` synced after squash-merges
  (remote is the source of truth; local `main` goes stale).
- PRs that touch shared append surfaces are conflict-prone when run in parallel;
  the `NEXT/` fragments + generated ADR index above remove the common cases.

## Package PM release-control policy

PM work for Verjson package repositories retains the type-surface ruleset and
uses the organization-owned package ruleset policy. The release App is the sole
type-surface bypass actor; private release credentials stay confined to the
canonical snapshot job. Before any package release, run the exact ruleset
preflight/postimage audit, the release-App canary, and the pinned release
rehearsal. Do not hand-edit live bypass actors or treat an unaudited consumer
exception as a release fix.

## Active Issues / Areas for Improvement

Each entry states the concrete fact and how/when it was last verified — a live re-check,
not just inspection of prose (#956: an entry asserting external status should say how it
was confirmed, since inspection-only claims go stale silently).

- [#1663](https://github.com/Verjson/.github/issues/1663) — The generated label re-arm caller also subscribed to lifecycle events already handled by `gate-rearm.yml`; confirmed 2026-09-30 by comparing both generators and caller contracts.

- [#1669](https://github.com/Verjson/.github/issues/1669) — Node 26 places npm’s CLI under the validated tool prefix’s `lib` tree while the launcher looks below `bin`; the registered credentialless consumer script plan regression and protected identity harness pass locally on 2026-10-01, pending canonical CI.

- [#1682](https://github.com/verJSON/.github/issues/1682) — GitHub's `verJSON/.github` casing exposed case-sensitive repository checks in Node CI, hosted-selector policy selection, required-workflow receipts, API-backed run provenance in gate re-arm, post-merge authorization, dependency supersession, zero-provider recovery, private GitHub Packages tarball provenance, and the container deployment-review producer. Reproduced in verjson-ai-gguf#139 CI attempt 2 and the live branch-rules API on 2026-10-02; fixes are in [PR #1683](https://github.com/verJSON/.github/pull/1683).

- [#1665](https://github.com/Verjson/.github/issues/1665) — The generated gate caller requires a typed reusable environment input and directly handles only head transitions; lifecycle and label events remain on their dedicated callers. Verified 2026-10-01 with `bash scripts/ci-gate/gate-rearm-caller-contract.test.sh` and actionlint 1.7.7.
- [#1675](https://github.com/Verjson/.github/issues/1675) — The required reusable input does not select a different role environment: ADR 0166 fixes generated callers to `ai-review-app`, and the generated-caller contract test pins that value. Verified 2026-10-01 against the caller contract test and ADR 0166.

- [#1667](https://github.com/Verjson/.github/issues/1667) — Track configurable generated OCI candidate destinations for GHCR, GAR, and Sonatype Nexus. Opened 2026-10-01 after live issue searches and checking related [.github#1264](https://github.com/Verjson/.github/issues/1264), [.github#1186](https://github.com/Verjson/.github/issues/1186), and [verjson-ci#30](https://github.com/Verjson/verjson-ci/issues/30); those cover adopter rollout and GitLab CE Nexus engine work, not registry-neutral canonical publisher destinations.

- [#1692](https://github.com/Verjson/.github/issues/1692) — Generate dev, nonprod, and prod deployment from one immutable `main` candidate with separate environment controls; opened 2026-10-05 after confirming the candidate and release generators lack a general app deployment caller.

- [#1655](https://github.com/Verjson/.github/issues/1655) — Signed merge-gate provenance rollout, opened 2026-09-28 after #1339 met its evidence gate; ADR 0211 still requires a real `pull_request_target` capture before rollout.

- [#1423](https://github.com/Verjson/.github/issues/1423) — CLI Projects PR admission accepts only the byte-exact generated caller; the previous-main digest is rollout-only. Strict admission freshness is defense in depth. Remove the rollout digest after [Verjson/verjson-cli-projects PR #142](https://github.com/Verjson/verjson-cli-projects/pull/142) updates main. See [ADR 0212](docs/decisions/0212-cli-projects-admission-accepts-only-generated-caller/README.md).

- [#1363](https://github.com/Verjson/.github/issues/1363) — Separate AI authorization-arm enrollment from deterministic core checks. Live audit on 2026-09-27 reports one armed repository without canonical deterministic CI (`verjson-agents`, verjson-agents#417 filed with the proposed diff); the ADR 0206 transaction applies after verjson-agents#417 (via #1401) closes. Since the organization App keys were withdrawn on 2026-09-27, ruleset 20722935 runs `gate-rearm@b7e7899` and only the 12 repositories holding the key in `ai-review-app` can arm (#1385).
- [#1650](https://github.com/Verjson/.github/issues/1650) — `ai-review-merge.yml`'s finalizer step (`complete-authorization` job, "Fail authorization if completion did not run") validates the `external_id`'s embedded head SHA by shape only (`test("^[0-9a-f]{40}$")`), not by equality against `EXPECTED_HEAD_SHA`, unlike its `gate-rearm.yml` sibling. Flagged 2026-09-27 by code review on #1649; confirmed pre-existing and out of that PR's scope via diff against its merge-base.

- [#629](https://github.com/Verjson/.github/issues/629) — Protected runner canary rollout. GitHub API verification on 2026-09-04: `verjson-github-runner` has no generated deployment caller; its protected `production` environment has the registration App key but lacks `DIGITALOCEAN_RUNNER_FLEET_TOKEN`. Its environment review policy also needs alignment with ADR 0144. Complete adopter installation and a non-production fleet canary/stop/rollback receipt in the owning repository before closure.


- [#1451](https://github.com/Verjson/.github/issues/1451) — Complete consumer capacity validation and live host acceptance. The generated caller now inherits the `production` environment secret context (#1625, ADR 0198 amendment 2026-09-26); the verjson-git-runners PM regenerates its caller at that SHA and re-dispatches the dry-run for the first CI-driven host-export receipt.

Prune an entry when its issue closes. This list loads into every session, so a
closed entry costs context in each one and misreports the state of the work.
