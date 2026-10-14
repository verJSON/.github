# 0166 — Confine App private keys to main-only environment secrets

- **Date:** 2026-09-10
- **Status:** Accepted
- **Issue:** [#1285](https://github.com/Verjson/.github/issues/1285)

## Context

The non-default-branch proof in #1285 read the presence of release, merge and
AI-review App keys without declaring an environment. Binding the legitimate job
to an environment does not confine an organization or repository secret: another
workflow on a writable branch can omit that binding. Trigger changes cannot repair
secret storage provenance. An App private key can mint tokens across its installation
scope, so repository-scoped installation tokens do not reduce the exposed key's scope.

## Decision

Store `RELEASE_APP_PRIVATE_KEY`, `MERGE_APP_PRIVATE_KEY` and
`AI_REVIEW_APP_PRIVATE_KEY` only in caller-owned environments `release-app`,
`merge-app` and `ai-review-app`, respectively. Each environment uses a custom
deployment-branch policy with exactly one branch rule named `main`, no tag rule
and no wildcard. Protected-branches-only is insufficient: another protected branch
would then be admitted. The repository default branch must also be `main`.

The reusable contracts require `release_environment`, `merge_environment` or
`ai_review_environment`. Generators emit the canonical name. The key-consuming
jobs bind that environment and read the uppercase environment secret directly.
Callers neither map the App key nor use `secrets: inherit`; unrelated model or npm
credentials retain explicit grants. `node-release.yml` publishes using its job
token and consumes no App private key, so it gains no invented key input.
Release attribution, container promotion, canaries, review completion and rearm
use the same storage boundary; leaving any reader dependent on broad copies
would make their removal impossible.

An uncredentialed prerequisite checks the exact ref and live environment policy
with its ordinary `actions: read` job token before the key-bearing job can start.
The prerequisite has no environment, App key or checkout. Native environment
admission controls initial secret release; an in-job check cannot retrospectively
protect a key already delivered. Key jobs with `always()` conditions explicitly
require policy success. Body/base-only label-rearm edits allocate neither the
policy prerequisite nor the key job, preserving #1275's admission boundary.

Rearm and retry also have native event entrypoints with no reusable inputs. Their
fixed-role default is `ai-review-app` or `merge-app`; an empty selector in these
two contracts resolves to that same canonical role environment. This is an
explicit native-compatible default, not an unguarded-environment fallback. All
generated callers supply a nonempty literal and all policy checks require the
fixed name. No code claims to identify a reusable call from `github.event_name`,
which carries the original event.

**Remove every organization/repository copy during rollout.** Runtime expressions
cannot distinguish a secret's provenance while broad copies remain. Environment
binding and a passing policy prerequisite are not evidence of exclusive storage.
The operator metadata audit fails on any broad copy, incomplete/unreadable secret
inventory, missing role secret or non-main policy. Its stronger organization-wide
check remains red during a partial migration, even if some consumers are isolated.
It does not read secret values or provision/delete anything.

## Rollout and consequences

Follow the [staged rollout](../../app-key-environment-rollout.md). Authenticated
inspection on 2026-09-10 found that shared ruleset `20722935` binds `gate-rearm.yml`
to `refs/heads/main` without an immutable `sha`; the earlier description of an
existing shared pin was incorrect. Merging this contract would change the entire
selected cohort immediately unless that binding is frozen first.

Before this contract merges, add only the workflow entry's `sha`, selecting merged
`c597d6908e3aa38d9a041c2150afadcbff32cf6d`. Verify that its workflow bytes still match
protected main and retain the #1275 admission guard. Preserve all other ruleset
fields, including enforcement, bypasses and the complete repository/ref selectors.
Retain exact preimage/candidate/postimage evidence and reject concurrent drift;
the live receipt, not this decision, proves the protective freeze is installed.

Consumers cannot retire broad AI keys while the frozen inherited workflow needs
them. Prepare all affected repositories before advancing that pin and regenerating
consumers, verify environment consumption, remove broad copies and perform a
non-main denial proof. Freezing the old behavior does not activate this contract;
merging this contract does not complete exposure removal. There is no compatibility
fallback to a broad App-key grant.

The validation and additional policy API call add a small job cost to privileged
operations. This buys visible admission failure before key delivery and avoids a
new privileged bootstrap credential. GitHub's documented environment and branch
policy reads require Actions read; secret-store auditing instead requires operator
metadata authority and remains outside the ordinary workflow token.

References: [reusable environment secrets](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows#using-inputs-and-secrets-in-a-reusable-workflow),
[environment API permissions](https://docs.github.com/en/rest/deployments/environments#get-an-environment),
[branch-policy API](https://docs.github.com/en/rest/deployments/branch-policies#list-deployment-branch-policies).

The credentialless admission runs on fixed `ubuntu-24.04` with a two-minute limit, without checkout, so policy validation cannot depend on mutable fleet routing. Canonical native entrypoints self-adopt on merge; provision this repository before merge as well as every consumer before the protective pin advances to the environment-key contract. Missing environments fail the arm workflow even though its subsequent operational arm job remains advisory.

The 2026-09-10 CI run caught ShellCheck SC2153 where the policy response variable
`environment` resembled the external `ENVIRONMENT` selector. Naming the response
`environment_record` preserves the checks without suppressing the diagnostic.
Validation includes actionlint with ShellCheck enabled; a workflow parse or the
mocked policy suite alone does not exercise that CI boundary.

## 2026-09-10 — Verified self-adoption prerequisites and bootstrap retirement

The protective ruleset `20722935` freeze was installed and read back at `2026-09-10T02:10:43.462Z`, pinning the existing contract at `c597d6908e3aa38d9a041c2150afadcbff32cf6d` without changing other ruleset fields. The preceding rollout requirements describe the sequence; this dated receipt records their execution.

The [#1291 provisioning/proof receipt](https://github.com/Verjson/.github/issues/1291#issuecomment-5611815789) confirms all three `.github` main-only environment keys are present, unchanged since their successful import readbacks, and usable by the exact expected Apps with `.github`-only token scope (proof run `34429989792`, local verifier exit 0). Key provisioning is fulfilled for canonical self-adoption. Retire the complete temporary bootstrap executable surface in the same self-adoption change so a broad-key sealing entrypoint cannot remain under this contract. Preserve ADR 0169 and ciphertext/metadata history at its immutable source commit.

This completes neither the wider cohort migration nor #1285 exposure removal. Broad copies remain for unprepared consumers and the inherited frozen workflow; advancement of that pin and broad-copy removal require the separately prepared cohort and receipts.

The [native environment-denial receipt](https://github.com/Verjson/.github/issues/1285#issuecomment-5611832146) records run `34430152626` at keyless non-main proof commit `49779d3`. GitHub denied all three explicitly requested role environments before any runner or step started (`runner_id: 0`, empty steps). The proof branch was removed after checking its SHA. Together with main proof `34429989792`, this demonstrates the three native environment boundaries; it does not prove absence of broader secret copies, which remain pending cohort migration.

## 2026-10-14 — Fleet conformance accepts the withdrawn organization copy

The organization copies of `AI_REVIEW_APP_PRIVATE_KEY`, `MERGE_APP_PRIVATE_KEY` and
`RELEASE_APP_PRIVATE_KEY` were withdrawn between 23:10 and 02:36 UTC on 2026-09-27
([#1385](https://github.com/Verjson/.github/issues/1385)); the owner confirmed on
2026-09-28 that they stay withdrawn. That is the custody this decision requires, but
`scripts/privileged-merge-conformance.sh` read the organization secret's 404 as
"cannot verify" and failed every scheduled run from 2026-09-27 onward
([#1776](https://github.com/Verjson/.github/issues/1776)). Its per-consumer remediation
also asked operators to grant access to the organization copy this decision removed.

The audit now treats that 404 as withdrawal only when the same token reads a complete,
well-formed organization secret inventory omitting the name. GitHub also returns 404 for
a resource the caller cannot see, so an uncorroborated 404 is unknown custody and fails
closed with every other read failure. With the copy withdrawn, each privileged merge
consumer must hold the key in its own `merge-app` environment; the audit reads that
secret's metadata, never its value, and distinguishes a missing key (404) from an
unreadable one. While an organization copy exists, the earlier visibility-based verdict
is unchanged; flagging such a copy as residue remains the job of
`scripts/org-secret-scope-audit.py`.

The same change records the review and merge organization copies as `withdrawn` in
`config/org-actions-secret-policy.json` and marks both roles' custody as achieved in
`config/app-role-custody-inventory.json`, matching the release role. A read-only fleet
scan on 2026-10-14 found no repository-level copy of any `*_APP_PRIVATE_KEY` across 99
active repositories, so no surviving broad copy remains for either role.

This audit checks key presence only. Main-only branch admission remains the job of
`app-key-environment.yml`, and a consumer pinned to a contract older than the
`merge_environment` input would still not bind the environment at run time.
