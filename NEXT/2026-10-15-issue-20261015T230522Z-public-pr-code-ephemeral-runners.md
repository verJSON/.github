---
date: 2026-10-15
id: 20261015T230522Z
impact: patch
title: Keep public pull-request code off persistent self-hosted runners
---
`actions-ci` now runs every job on the ephemeral untrusted lane
(`CI_LANE_UNTRUSTED` with a hosted tail and no persistent fallback). Before
this change, code from any pull request to this public repository, including
forks, ran on the shared DigitalOcean `general` pool that also runs privileged
jobs. A new contract check, `scripts/ci-gate/public-pr-runner-isolation.py`,
fails if a job that a `pull_request` event can reach could land on a persistent
runner through a variable fallback or a caller-supplied input
([ADR 0235](../docs/decisions/0235-public-pull-request-code-uses-ephemeral-runners/README.md)).
`pull_request_review`, `pull_request_review_comment`, and `merge_group` are held
to the same rule. A trigger the checker does not classify, such as
`check_suite`, `status`, or an unknown `pull_request_*` name, is undetermined
rather than clean, and a duplicate list row is a format error.

Under `pull_request_target`, `workflow_run`, and `issue_comment`, a job that can
reach a persistent runner must appear in
`scripts/ci-gate/public-pr-runner-isolation.reviewed.tsv`, keyed by a digest of
its content and inherited settings, with a written justification. The checker
over-approximates what such a job puts on disk: any fetch it can name (`curl`,
`wget`, `gh api`, `gh run`/`release download`, git transport, package managers,
`docker`/`podman` pulls and builds, `docker://` steps, job `container:` and
`services:` images, in-language HTTP clients, and the download, cache, and
`github-script` actions), any runtime tree change (including `gh pr checkout`
and `git --git-dir`/`--work-tree`), any non-default `shell:`, a full or
runtime-selected checkout, checkout submodules, or head detection that finds or
cannot rule out head materialization leaves the job unresolved. Its entry then
needs `dynamic: <reason>[, <reason>]: <justification>` naming exactly those
reasons. A full checkout binds the whole tracked tree, so editing, adding, or
deleting any file, changing a file mode, retargeting a symlink, or moving a
submodule gitlink to another commit turns its entry stale. A runtime-selected checkout, which includes one at
`pull_request.base.sha` because that SHA is not refreshed when the base branch
advances, must carry a `sparse-checkout` list; without one it is refused, because no review
can bind what it puts on disk. A job `container:` or `services:` image, a
`docker://` step's image, a local Docker action's image, and every `FROM` of a
Dockerfile it builds must be pinned by an `@sha256:` digest; a tag or
expression is refused. The fetch
vocabulary is not exhaustive; a fetch outside it still turns a reviewed entry
stale but demands no `dynamic:` field.
`scripts/ci-gate/public-pr-runner-isolation.pending.tsv` ratchets
`actionlint.yml` and `container-candidate.yml` as pending.
Their selectors are pinned in `hosted-selector-policy.py` and change after #1826;
#1831 tracks them, the adopter reusable workflows, and live lane enforcement.

The merge gate's relay no longer puts a public pull request's head on the
persistent pool. `gate-rearm.yml` dispatches `ai-review-merge.yml`, whose `gate`
job checks out and reviews the head; ADR 0048 had sent a public target there on
`CI_RUNNER_FASTLANE`, which is the `general` pool today. Every job that dispatch
reaches (`preflight`, `gate`, `complete-authorization`, `dispatch-merge`, and
`app-key-environment.yml`'s `validate-ai-review-key`) now takes
`CI_LANE_UNTRUSTED` with a hosted tail unless the event repository is private,
and a Verjson caller's `runner_labels` is honored only for a private repository.
Private targets keep their lanes. The change applies to this repository at
merge; an adopter is covered once its generated `ai-review-merge.yml` caller is
regenerated at or after the merge commit. No ruleset rotation is needed.

The checker now scans relays. `workflow_dispatch`, `workflow_call`, and
`repository_dispatch` jobs that can reach a persistent runner are refused
outright when head detection finds, or cannot rule out, that they materialize a
pull request head, and otherwise need a reviewed entry. Dispatch and caller
inputs take every value their dispatcher can choose. `fork`, `issues`,
`discussion`, and `discussion_comment` are privileged events. The 44 relayed
jobs that predate the rule are ratcheted in the pending list as #1831's relay
backlog. Its keys are frozen in the checker and may only shrink; refusals are
decided before any list row is read, and a backlog job tolerates only the
refusal kinds it carried when the backlog froze; each row binds the job's full
content, callers, inherited settings, and local actions, so any edit to the job
stops the row applying.
`ai-post-merge.yml` now checks its reconciler out at `github.sha`, the commit
its own workflow file comes from, instead of `pull_request.base.sha`.
A new `runner-isolation` workflow runs the check on every pull request and push
to `main` with no path filter, because the review digests bind tracked files
outside `actions-ci`'s path filter.
