# 0153 — Bind cli-projects package-surface enforcement to a required workflow

- **Date:** 2026-08-29
- **Status:** Accepted
- **Issue:** [#1177](https://github.com/Verjson/.github/issues/1177)
- **Partially superseded by:** [ADR 0204](../0204-retire-duplicate-cli-projects-repository-rule/README.md)

## Context

`Verjson/verjson-cli-projects` ruleset `21567958` identifies its package-surface
evidence only by the `package-surface-contract` context and the GitHub Actions App.
The ruleset is in `evaluate` because no producer exists on `main`, but a future
repository-owned producer would still be replaceable by the pull request whose package
surface it attests. Matching a display name and App identity does not prove protected
verification code ran.

The verifier must inspect and exercise package bytes supplied by an untrusted pull
request. It also evaluates workflow templates shipped to new repositories. Running that
work on persistent runners or alongside credentials would let candidate code retain or
exfiltrate organization capability.

## Decision

Create `.github/workflows/cli-projects-package-surface-required.yml` as an
organization-owned required workflow. A dedicated active organization branch ruleset
selects repository ID `1277452690`, `~DEFAULT_BRANCH`, canonical workflow repository ID
`1269388380`, the exact workflow path, `refs/heads/main`, and its reviewed immutable
merged SHA. It has no bypass actors and cannot apply to a similarly named repository or
property assignment.

The workflow runs only for `pull_request` and refuses repositories other than
`Verjson/verjson-cli-projects`. It uses `ubuntu-24.04`, grants only contents read, passes
no secrets, and disables persisted checkout credentials. The candidate tree and policy
tree are checked out separately. The policy checkout uses `github.workflow_sha`, binding
the verifier bytes to the same immutable required-workflow revision selected by the
organization rule. Candidate code therefore remains untrusted data until the protected
verifier chooses a bounded operation; `npm pack` runs only on the disposable hosted VM.

The protected verifier fixes the public package name, binary map, published file
allowlist, registry, build entrypoint, and Node floor. It semantically parses the release
workflow and shipped workflow templates with duplicate-key rejection. Release remains
dispatch-only; action references require 40-hex commits, containers require sha256
digests, and verification steps cannot opt into `continue-on-error`, unconditional
skips, or `|| true`. It then creates an ignore-scripts package archive and rejects
missing public entrypoints or included repository/credential configuration. Both npm 11
array receipts and npm 12 keyed receipts are accepted only when they describe exactly
one `@verjson/cli-projects` package.

The rollout tool is read-only by default and verifies its own reviewed files are byte
identical at a SHA reachable from protected `main`. Apply requires the exact human
acknowledgement and a fresh, unique provider inventory. It creates the organization rule
disabled, reads and compares the complete postimage, activates it, and compares again.
An unreadable, mismatched, or client-failed activation always reads the live postimage,
then attempts and verifies rollback to disabled when the state is not already the exact
disabled image. A retry adopts only one exact disabled staged rule by numeric identity
and complete postimage; it resumes activation instead of creating a duplicate. One exact
active rule is an idempotent success, while ambiguity or any other image fails closed.

Repository ruleset `21567958` is a separate second transaction. It remains in
`evaluate` until a fresh exact-head run proves the organization-required workflow URL,
path, SHA, event, conclusion, and activation-time ordering. Only then may
`activate-repository` issue a PUT from an immediate exact preimage whose sole changed
field is `enforcement`, followed by a complete GET comparison. Drift or partial API
failure first reconciles live state and restores/verifies the exact `evaluate` preimage;
the old rule stays non-blocking until evidence binding is live.

The general organization ruleset audit keeps its release-App bypass invariant. Its
policy contains one narrow bypassless exception for this exact ruleset name, consumer
ID, workflow repository/path/ref, active enforcement, immutable SHA, and empty bypass
set. Any widened variant remains a conformance failure.

### 2026-08-30 — Credentialed acquisition also belongs to the required workflow

Issue [#1187](https://github.com/Verjson/.github/issues/1187) found that the
repository-local Node CI caller still granted `packages: read` and mapped its
`GITHUB_TOKEN` from pull-request-controlled YAML. Protecting only the final
package-surface verifier did not protect the earlier credential-bearing call.

Generate this required workflow from a strict repository configuration and move both
secretless Node lanes behind it. A no-checkout admission job validates the exact
pull-request event, repository, numeric pull-request identity, same-repository head,
head SHA, synthetic merge SHA, and merge ref against GitHub's live numeric record. Only
after admission may the workflow call the immutable canonical `node-ci` revision. Its
acquisition job receives package-read authority but never executes candidate code; its
build job restores only the bounded package cache and runs candidate scripts after
credential removal. The second lane proves the Node floor. The original protected
package verifier remains credentialless and now also requires both Node lanes.

The organization required-workflow rule, rather than a repository-owned caller name,
is the pull-request entry-point provenance boundary. The generated repository caller is
push-only, and admission hashes the candidate caller against that exact image before
package acquisition. The rollout tool also requires protected `main` to contain those
byte-exact push-only caller bytes before rotating the organization rule or activating
the repository rule. The transaction binds that read to the exact protected-main commit
and re-projects the branch immediately before and after each write. A moved branch or
ambiguous write response restores and verifies the prior organization workflow or the
repository rule's `evaluate` image. Updating either surface requires a fresh exact-head
canary.

## Consequences

- Pull-request-authored workflows, verifier files, and spoofed check names cannot satisfy
  the organization required-workflow rule.
- The repository-owned caller grants package authority only for pushes to protected
  `main`; it has no pull-request trigger. Candidate caller mutation fails required-
  workflow admission before credentialed acquisition.
- Untrusted package and scaffold behavior has no persistent host, inherited secret, or
  retained checkout credential.
- The package policy affects only `Verjson/verjson-cli-projects`.
- Rollout remains held until this change is merged, independently reviewed, and a fresh
  required-workflow canary succeeds; no live ruleset mutation is part of this change.
- Rollback preserves the repository rule in `evaluate` and restores a newly created
  organization rule to disabled rather than accepting ambiguous enforcement.

### 2026-08-30 — Bind empty-context delivery to authenticated run identity

Issue [#1195](https://github.com/Verjson/.github/issues/1195) established that an
organization-required workflow delivery can omit pull-request event fields. Admission
therefore reads only the current `github.run_id` through the authenticated Actions API,
requires exactly one pull-request binding, and matches its head SHA to one live, open,
same-repository numeric pull request. The admitted event, repository, and immutable head
SHA are outputs consumed by both protected Node lanes and candidate checkout.

Both Node lanes call `node-ci-protected.yml` at immutable organization contract commit
`29e28aa5d4606678dbee93d46dd0663fa55c749b`. Their callers explicitly grant
`actions: read` and `pull-requests: read`; the ordinary push-only consumer remains on the
legacy `node-ci.yml` contract. Missing, ambiguous, stale, closed, malformed, or partially
unavailable identity evidence fails closed before candidate bytes or package authority
are consumed.

### 2026-08-30 — Remove credential variables before candidate execution

Issue [#1198](https://github.com/Verjson/.github/issues/1198) found that protected
candidate processes inherited credential variable names with empty values. The protected
contract now removes those variables from the process environment before candidate code
runs and rejects schema-directory execution that would bypass the installed-package
boundary. Both required Node lanes are repinned to immutable organization contract commit
`e4d911fd4197e0a4a0d500cd43c49a2170234b7a`; the admission and fail-closed identity
contract otherwise remains unchanged.

### 2026-09-23 — Follow the consumer's supported release-contract command

Issue [#1423](https://github.com/Verjson/.github/issues/1423) found during the activation
rehearsal that the required workflow still planned `test:release-v1`, an obsolete alias
the consumer deliberately removed. The protected current-Node lane now invokes the
supported `test:release` command. Restoring the retired alias would hide canonical-plan
drift in the consumer instead of correcting the organization-owned contract. Activation
still requires a fresh exact-head canary after this corrected workflow is merged.

### 2026-09-26 — Repin both protected lanes to the hosted tool-cache tree fix

Issue [#1599](https://github.com/Verjson/.github/issues/1599) found `node-ci-protected.yml`
failing every GitHub-hosted protected run because only the `/opt/hostedtoolcache` root, not
its descendants, was admitted; [ADR 0209](../0209-hosted-tool-cache-trust-is-a-tree-property/README.md)
records the fix (#1624). Both required Node lanes of this workflow are repinned to the
immutable organization contract commit `3f8fb4bae1bdf0ced0b821e1b4277523f72a93b9`, through
`config/cli-projects-required-node-ci.json`, so activating ruleset 21830367 under
[#1423](https://github.com/Verjson/.github/issues/1423) cannot land on that check. The
consumer's `ci.yml` must be regenerated to the same commit before activation, because the
admission step compares the consumer's default-branch bytes with `CONSUMER_WORKFLOW_SHA256`
and fails closed on a mismatch. The admission and fail-closed identity contract are
otherwise unchanged.

### 2026-09-27 — Repin both protected lanes to the hosted sandbox-provisioning fix

The first exact-head control after activation (verjson-cli-projects#142, run 36283749779)
failed closed with `verified bubblewrap namespace boundary is unavailable`: the protected
plan requires the sandbox but hosted provisioning was gated on inputs these lanes do not
pass ([ADR 0209](../0209-hosted-tool-cache-trust-is-a-tree-property/README.md) amendment,
#1636). Both lanes are repinned to `4a08c756547ccaf8977eda692e14eff56fddaf15`; the
rollout contract's `previous_workflow_sha` moves to `ebe574b…` so the active ruleset can be
rotated from its current image, and the consumer `ci.yml` is regenerated to the same commit
before rotation.

## 2026-10-15 amendment: repository identity is compared case-insensitively

The runtime repository identity check is now an exact match after ASCII
lowercasing, not a byte comparison. Since the organization's 2026-10-02
display-case rename, GitHub reports the same repository as `Verjson/...` or
`verJSON/...` depending on how the workflow was called. The rationale,
evidence and tests are in
[ADR 0213's 2026-10-15 amendment](../0213-package-type-surface-release-authorization/README.md)
([#1794](https://github.com/Verjson/.github/issues/1794)).

## 2026-10-15 amendment: repin past #1750 and read the rotation predecessor from the live ruleset

**Repin.** Both protected Node lanes now call `node-ci-protected.yml` at
`576707fe0be0dabe43ea57c504575f8cda88b0d2`. That is the v4.0.0 release commit and the
`origin/main` HEAD when this change was cut, and it contains #1750 and #1759, which start
the protected lane's inline interpreters isolated. Releases here are dispatched rather
than derived from merges, so a released commit is the narrowest immutable revision that
contains both fixes. It is also the one other required workflows are expected to adopt.

**Separate pins for the protected lanes and the consumer caller.** The 2026-09-26
amendment drove the protected lanes and the push-only consumer caller from one
`node_ci_sha`. Every protected-lane repin therefore changed the consumer `ci.yml` digest
that admission requires. That forced a `verjson-cli-projects` pull request into each
rollout and failed admission for every open consumer pull request in between.
`config/cli-projects-required-node-ci.json` now carries `protected_node_ci_sha` and
`consumer_node_ci_sha` separately, restoring the 2026-08-30 separation in which the push
caller stayed on its own `node-ci.yml` pin. Admission is unchanged and still requires the
byte-exact generated caller (ADR 0212); this repin leaves that digest at `9e4ca97c…`.
The trade-off is that the push lane and the protected pull-request lane can run different
`node-ci` revisions. The push lane runs only already-merged code, so a protected-lane
hardening fix no longer waits on a consumer change.

**No committed predecessor record.** The rollout contract recorded the revision being
replaced (`previous_workflow_sha`, `previous_disabled_workflow_sha`), and the script
duplicated it as a hard-coded check. A rotation's target is always the merge commit of a
later change, so that record went stale after every rotation. #1824 repaired it once, and
it was stale again the same day (live `9998460f`, record `ae70e4ef`). Both keys and their
constants are removed. `dry-run` and `apply` now read the bound revision from the live
ruleset and accept it only when all of the following hold:

- The live image equals one of that revision's reviewed images: active or disabled, with
  or without the strict `admission` check.
- The revision is an ancestor of, or equal to, the rotation target, which is itself
  verified on protected `main`. A rotation therefore only moves forward and can never
  return to a revision below the floor described next.
- The required workflow at that revision is byte-for-byte the output of that revision's
  own generator and configuration. The generator runs from its fetched bytes in a temporary
  directory with no inherited environment.

A failed write restores the exact observed image and verifies it.

**Security floor.** Before any mutation, `dry-run` and `apply` require every
`node-ci-protected.yml` call in the target workflow to be canonical, immutable, and a
descendant of `21eb48e7` (#1759). The floor is a constant that moves only on purpose.

**Drift is detected, not avoided.** A new read-only `audit` mode runs daily in the
`required-workflow-audit` job of `org-ruleset-conformance.yml`. It fails when the live
ruleset is not the reviewed active image of its own revision, when that revision is not on
protected `main`, when any byte that revision executes differs from protected `main`
(the required workflow, plus the verifier, its config module, and its config, all checked
out at `github.workflow_sha`), or when its protected-lane pin is below the floor. The
committed generated workflow is the record, so the audit fails exactly while a rotation is
pending. Behavioral tests cover predecessor images, admission refusal before mutation,
rollback, the floor, the audit, and the job contract. The operator rotation of ruleset
21830367 to this change's merge commit remains a separate human-gated step.

### 2026-10-15 — Rotation applied; live receipt pending

Ruleset 21830367 was rotated from `9998460f` to
`c78fcd4ef9ab83b6775ae2ede94ebce475b3943c` (#1828), the first rotation to read its
predecessor from the live ruleset. The read-only `audit` exits 0. Live `updated_at` is
`2026-10-15T20:37:21.391-05:00`. The pre-trigger snapshot is
`{"pre_trigger_max_run_id": 38432170170}`.

The exact-head receipt is still outstanding: no consumer pull request has run since the
rotation. When the first `pull_request` run of
`.github/workflows/cli-projects-package-surface-required.yml` on
`Verjson/verjson-cli-projects` has a run ID above 38432170170, record its result here and
on #1423. Run it from a clean worktree at the rotated revision. `verify_canonical_bytes` requires the
local tool bytes to equal those at `--workflow-sha`:

```bash
M=c78fcd4ef9ab83b6775ae2ede94ebce475b3943c
CANONICAL=<canonical-checkout>   # any clone of the organization's .github repository
RECEIPT=$(mktemp -d)
git -C "$CANONICAL" fetch --prune origin
git -C "$CANONICAL" worktree add --detach "$RECEIPT" "$M"
cd "$RECEIPT"
RUN=<required-workflow run id above 38432170170>
HEAD_SHA=$(gh api "repos/Verjson/verjson-cli-projects/actions/runs/$RUN" --jq .head_sha)
python3 -I scripts/cli-projects-package-surface-ruleset.py verify-run --workflow-sha "$M" \
  --run-id "$RUN" --head-sha "$HEAD_SHA" --pre-trigger-max-run-id 38432170170
```

`verify-run` also requires the strict `admission` check from that same run on the exact
head. A rerun of an older run does not qualify.
