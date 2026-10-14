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
Pull-request workload tiers and their current classifications are documented in
[`docs/ci-workflow-tiers.md`](docs/ci-workflow-tiers.md).

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

- [#1754](https://github.com/verJSON/.github/issues/1754) — Draft-then-ready PRs keep a concurrency-cancelled run on the same head, so `assert-mergeable-head.sh` exited 3/5 on green heads (#1750 `d762771c`, #1753 `c10425e6`; reproduced 2026-10-13 against live check runs with `filter=all`); ADR 0226 sets aside only cancellations superseded by a later successful run of the same workflow and event.
- [#1752](https://github.com/verJSON/.github/issues/1752) — Generalize the isolated inline Python interpreter contract from the Node CI pair to every workflow; its blocker #1749 closed on 2026-10-13 via PR #1750, which added the Node CI test (verified live with `gh issue view 1749 --json state`). Opened 2026-10-13 after a per-workflow count during #1750 review.

- [#1667](https://github.com/Verjson/.github/issues/1667) — Track configurable generated OCI candidate destinations for GHCR, GAR, and Sonatype Nexus. Opened 2026-10-01 after live issue searches and checking related [.github#1264](https://github.com/Verjson/.github/issues/1264), [.github#1186](https://github.com/Verjson/.github/issues/1186), and [verjson-ci#30](https://github.com/Verjson/verjson-ci/issues/30); those cover adopter rollout and GitLab CE Nexus engine work, not registry-neutral canonical publisher destinations.

- [#1692](https://github.com/Verjson/.github/issues/1692) — Target current `main` HEAD in dev via a fast, validated image path and promote one fully verified pinned digest set from nonprod to prod with separate environment controls; opened 2026-10-05 after confirming the candidate and release generators lack a general app deployment caller.

- [#1696](https://github.com/Verjson/.github/issues/1696) — The authorization arm bootstraps a stale default-branch AI review caller ([ADR 0227](docs/decisions/0227-arm-bootstraps-stale-review-callers/README.md)). Closing the issue still requires rotating ruleset 20722935 to the merge SHA and proving a focused verjson-cli bootstrap pull request. Verified 2026-10-13 with `python3 scripts/ci-gate/review-caller-bootstrap.test.py` and a read-only live classifier run against verjson-cli#302.

- [#1655](https://github.com/Verjson/.github/issues/1655) — Signed merge-gate provenance rollout, opened 2026-09-28 after #1339 met its evidence gate; ADR 0211 still requires a real `pull_request_target` capture before rollout.

- [#1423](https://github.com/Verjson/.github/issues/1423) — CLI Projects PR admission accepts only the byte-exact generated caller; the previous-main digest is rollout-only. Strict admission freshness is defense in depth. Remove the rollout digest after [Verjson/verjson-cli-projects PR #142](https://github.com/Verjson/verjson-cli-projects/pull/142) updates main. See [ADR 0212](docs/decisions/0212-cli-projects-admission-accepts-only-generated-caller/README.md).

- [#1363](https://github.com/Verjson/.github/issues/1363) — Separate AI authorization-arm enrollment from deterministic core checks. Live audit on 2026-09-27 reports one armed repository without canonical deterministic CI (`verjson-agents`, verjson-agents#417 filed with the proposed diff); the ADR 0206 transaction applies after verjson-agents#417 (via #1401) closes. Since the organization App keys were withdrawn on 2026-09-27, ruleset 20722935 runs `gate-rearm@b7e7899` and only the 12 repositories holding the key in `ai-review-app` can arm (#1385).

- [#629](https://github.com/Verjson/.github/issues/629) — Protected runner canary rollout. GitHub API verification on 2026-09-04: `verjson-github-runner` has no generated deployment caller; its protected `production` environment has the registration App key but lacks `DIGITALOCEAN_RUNNER_FLEET_TOKEN`. Its environment review policy also needs alignment with ADR 0144. Complete adopter installation and a non-production fleet canary/stop/rollback receipt in the owning repository before closure.

- [#1451](https://github.com/Verjson/.github/issues/1451) — Complete consumer capacity validation and live host acceptance. The generated caller now inherits the `production` environment secret context (#1625, ADR 0198 amendment 2026-09-26); the verjson-git-runners PM regenerates its caller at that SHA and re-dispatches the dry-run for the first CI-driven host-export receipt.

Prune an entry when its issue closes. This list loads into every session, so a
closed entry costs context in each one and misreports the state of the work.
