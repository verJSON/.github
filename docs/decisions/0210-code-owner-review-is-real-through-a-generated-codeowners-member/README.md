# 0210 — Code-owner review is real: the generated CODEOWNERS is a required member of the adopter contract

- **Date:** 2026-09-27
- **Status:** Accepted
- **Issue:** [Verjson/.github#1272](https://github.com/Verjson/.github/issues/1272)
- **Related:** [ADR 0191](../0191-contract-releases-are-the-unit-of-adoption/README.md), [ADR 0202](../0202-bind-autonomous-merge-to-independent-review-receipt/README.md), verjson-ci#223
- **Category:** organization ruleset behavior (sensitive class)

## Context

Organization ruleset `main-protection` (id 18098028, `enforcement: active`, targets
`~ALL` repositories on the default branch) carries `require_code_owner_review: true`
in its pull-request rule. A code-owner requirement matches nothing in a repository
without a `CODEOWNERS` file. On 2026-09-10 the sweep for #1272 found a `CODEOWNERS`
in 3 of 101 repositories, so in ~98 the rule was enforced in the ruleset while asking
for nothing. Nothing about that is exploitable; the defect is that the rule's
recorded intent and its effect disagree, and the audit cannot tell an intentional
exemption from an oversight.

Two things changed between the finding and this decision:

- `scripts/gen-changelog-caller.sh codeowners <sha>` (rendering `scripts/codeowners.py`)
  already exists and emits one canonical file: `* @Verjson/devs`, with a header
  naming the generator. The `@Verjson/devs` team holds `push` on the managed
  repositories (granted 2026-09-23, recorded on #1272).
- `Verjson/verjson-ci` adopted that generated file in PR #223 (merged 2026-09-26
  13:19 UTC) after verjson-ci#212 cleared the `package.json` surface it had to
  wait for. The adoption path is proven on a real repository.

The alternatives were to make the rule real everywhere or to drop it from
`main-protection`.

## Decision

Keep `require_code_owner_review: true` in `main-protection` and make it real by
making `.github/CODEOWNERS` a **required member of the generated adopter set**,
verified by the generated contract test every adopter already runs on each pull
request:

- The canonical bytes live in `config/codeowners/CODEOWNERS` (the reviewed source)
  and are embedded verbatim in `scripts/gen-changelog-caller.sh`, which is itself the
  pinned artifact: the `codeowners` mode emits them and the `contract-test` mode
  embeds `EXPECTED_CODEOWNERS_SHA256`, their digest, without resolving anything at
  generation time. That matters because the required-checks audit materializes only
  the generator at a pin — no checkout, no network, and a deliberately hostile
  interpreter environment — and must still emit the same contract test; the audit's
  own byte comparison covers the five members it already regenerates and does not
  yet compare `.github/CODEOWNERS`, which is a follow-up rather than a dependency.
  `scripts/codeowners.py` reads the same source file, and `scripts/codeowners_test.py`
  fails if the generator's embedded copy drifts from it.
- The generated suite fails closed when `.github/CODEOWNERS` is absent, is reached
  through a symlink (either the directory or the file), does not match the pinned
  digest, or is shadowed by a competing `CODEOWNERS` or `docs/CODEOWNERS`, which
  GitHub would read in preference. The absent and drifted failures name the
  regeneration command; the shadowing failure names the file to remove.
- The meta-suite (`scripts/ci-gate/changelog-caller-contract.test.sh`) builds every
  adopter fixture with the generated file and asserts all four rejections with the
  existing no-op-mutation guard, so none of the cases can be vacuous.
- `docs/changelog/README.md` lists `codeowners` in both adoption command lists.
  `docs/changelog/migration.md` and the adoption-issue template
  (`scripts/render-changelog-adoption-issue.py`) are executable against the
  *recommended* contract pin, which predates this change and has no `codeowners`
  mode, so they gain the step in the same PR that advances the recommended pin.
  `scripts/changelog-migration.test.sh` runs the generated contract test at that pin
  and fails closed until the step is added; the adoption-issue test only asserts the
  rendered block's artifacts exist, so that PR must also add `.github/CODEOWNERS` to
  its `ARTIFACTS` list.

Fleet rollout follows [ADR 0191](../0191-contract-releases-are-the-unit-of-adoption/README.md):
an adopter that regenerates at a contract SHA containing this change must generate
the CODEOWNERS member in the same pass, exactly as it must regenerate any other
member. Repositories pinned before this SHA are unaffected until they advance,
which is the release train under #1374; the managed set (`verjson-ci`,
`verjson-compliance`, `verjson-compliance-schema`, `verjson-agents`) leads.

## Why not drop the rule

Dropping `require_code_owner_review` would have been honest about the present, but
it discards a control the organization had already started adopting on merit, and it
weakens the human-path merge on every repository to satisfy an audit. The rule costs
the autonomous path nothing: the merge Apps and organization admins are `always`
bypass actors on `main-protection`, so the independent-review receipt
(ADR 0202) remains the control for autonomous merges, while a human-initiated merge
now requires a reviewer from the team that actually owns the code. A rule that is
real in 3 repositories and a template nobody enforces would recreate #1272 on the
next sweep; the contract test is the enforcement that makes the template stick.

## Why the contract test, not a separate conformance job

The generated contract test is already the single conformance surface every adopter
runs before merge, it is already digest-pinned to one contract SHA, and its
generator already owns the file. A second job would add a workflow to ~95
repositories to check one file that the existing job can check with fifteen lines.
The fleet sweep (`changelog-contract-fleet-report.yml`) surfaces the same failure
across repositories without further code.

## Consequences

- Every adopter regenerating at or after this SHA gains `.github/CODEOWNERS`
  (`* @Verjson/devs`); a repository that needs a different ownership map must change
  the reviewed source `config/codeowners/CODEOWNERS` and the generator's embedded copy
  upstream rather than hand-edit the file, since the digest refuses a local edit.
- `@Verjson/devs` must hold write access wherever the file lands; a repository where
  the team lacks it makes the rule impossible to satisfy on the human path and must
  be granted before its regeneration, as was done for the managed set on 2026-09-23.
- The coarse `#1272 blocked by verjson-ci#143` edge was dropped on 2026-09-26: the
  epic was never the prerequisite, verjson-ci#212 was, and it has merged.
- If `main-protection` ever drops `require_code_owner_review`, this member becomes
  advisory and a successor ADR should demote it to `optional`.

## 2026-10-19 amendment — Per-repository sensitive-path approval is a native required-reviewer rule

- **Issue:** [#1816](https://github.com/Verjson/.github/issues/1816)
- **Category:** organization ruleset behavior and review routing (sensitive class)

### Context

The canonical CODEOWNERS is the single rule `* @verJSON/devs`, and the generated
contract test refuses a local edit, so regeneration drops any hand-written
path-scoped route. `verjson-cli` lost `/.github/` and `/.gitmodules` →
`@Verjson/admins` in its #302 regeneration (`00800b0f` → `3275e8fc`), and on
2026-10-19 `verjson-cli-cloud`, `verjson-cli-projects` and `verjson-object-storage`
still carry admin routes on `main` that their next regeneration removes. A read-only
sweep that day of the current and historical CODEOWNERS of all 106 non-archived
organization repositories found admin routes in no others; `verjson-ci`, `verjson-compliance` and `verjson-compliance-schema`
adopted the canonical file without ever having one. Even where they existed, those
routes never constrained the merge Apps or organization owners, which bypass
`main-protection` (18098028) and with it its code-owner requirement.

The owner decided on 2026-10-19 (recorded by the PM on #1816) that canonical
CODEOWNERS stays default ownership only, and that additional admin approval is a
native organization `required_reviewers` ruleset managed as reviewed config here.
The 2026-10-18 rehearsal on #1816 (ruleset 25327030, since deleted) established
that such a ruleset refuses an unapproved sensitive-path merge even by an
organization owner using `--admin`, that `src/steps/**` matches nested directories
while `src/pulumi/**` does not match `src/pulumifoo/`, that ordinary paths are
unaffected, and that an organization-owner token reads
`current_user_can_bypass: never` on it.

### Decision

- **CODEOWNERS is unchanged.** The decision above and ADR 0174 stand: the canonical
  file states default ownership, and no path-specific override is generated.
- **Additional approval is one native ruleset per repository.** Each holds a single
  `pull_request` rule whose `required_reviewers` names `@verJSON/admins` (team
  17813010, exactly one approval) for the repository's sensitive `file_patterns`, targets
  `~DEFAULT_BRANCH` of exactly one rename-protected repository, and is named
  `<repository>-sensitive-path-admin-review`. Every other pull-request parameter is
  neutral (no approving-review count, code-owner, last-push, thread-resolution or
  unattributed-change requirement), so the ruleset adds the admin approval and
  changes no review behavior `main-protection` already sets;
  `dismiss_stale_reviews_on_push: true` makes a later push discard the approval.
  One approval is a cap, not a default: `@admins` has three members and an author
  cannot approve their own pull request, so two would stall an admin-authored
  change whenever one other member is unavailable.
- **Desired state lives in `config/required-reviewer-rulesets.json`.**
  `scripts/required-reviewer-ruleset.py render` prints the exact payloads, `check`
  compares the live organization rulesets by name and fails on absence, drift in any
  mutable field (compared without JSON type coercion), a duplicated name, or a live
  `*-sensitive-path-admin-review` ruleset the config no longer declares, and exits 2
  when live state cannot be read or a target is not a live, unarchived repository
  whose default branch is `main` (a misspelled name would otherwise enforce nothing
  while `check` stayed green). `apply --ack APPLY-REQUIRED-REVIEWERS-1816` makes the
  same target check and resolves every action before writing. It brings an absent
  ruleset, and any existing ruleset that is not active (such as a failed create's
  disabled leftover), to the reviewed image by writing it disabled, verifying it,
  then activating and re-verifying it, restoring the verified disabled image if
  activation reads back differently. An already-active drifted ruleset is rewritten
  in place without rollback: its prior image is drifted, so it is not a state worth
  restoring, and a failed read-back exits 2 for `check` to report. If activation reads
  back wrong and the disabling restore also fails, the ruleset may be left active with
  an unverified image; `apply` says so explicitly, names `check`, and a re-run takes
  the active-drifted path above. The rulesets API has no conditional write, so `apply`
  relies on full-image `PUT`s each followed by a read-back; two concurrent applies can
  each create the same ruleset, and the identical, stricter-not-weaker duplicate makes
  `check` and `apply` refuse the shared name until one is deleted. The config loader
  admits only the shape above, compared without JSON type coercion and with one
  literal repository name (no glob or `~ALL`), so weakening the bypass, neutrality,
  reviewer or target is a reviewed code change, not a data edit.
- **Scope is every dropped admin route**, with the routes it carried:

  | Repository | `file_patterns` | CODEOWNERS evidence |
  | --- | --- | --- |
  | `verjson-cli-cloud` | `.github/**`, `src/pulumi/**`, `src/steps/**`, `src/dns/**` | `main`, 2026-10-19 |
  | `verjson-cli-projects` | `.github/**`, `templates/**` | `main`, 2026-10-19 |
  | `verjson-cli` | `.github/**`, `.gitmodules` | `00800b0f`, before #302 |
  | `verjson-object-storage` | `.github/**`, `scripts/**`, `docs/decisions/**` | `main`, 2026-10-19 |

  `verjson-cli` has no `.gitmodules` on `main` today (2026-10-19); the pattern keeps
  the route it had, so reintroducing submodules needs an admin approval again.
- **Only the release-authorization App (Integration 4583107) bypasses.** The
  `pull_request` rule would otherwise block its direct release push. The merge App
  (4693283), Renovate (2740) and `OrganizationAdmin` are deliberately absent.
- **No organization-owner bypass.** This is the PM's technical decision on #1816's
  open question, flagged for the owner. Agent sessions run with an organization-owner
  token, so an owner bypass would let any such session merge a sensitive-path change
  with no second human, which is the gap this amendment closes. The cost is that an
  owner cannot merge a sensitive-path change alone in an emergency: another
  `@admins` member must approve, or an owner must visibly edit the ruleset, which
  `check` reports as drift until `apply` restores it. Since rollout step 4 (2026-10-20,
  amendment below) the daily audit runs `check`, so that edit is reported the next day
  without anyone running it.
- **Conformance keys these contracts by name.** `config/org-ruleset-conformance-policy.json`
  carries a `bypass_actor_contracts` entry with `"ruleset_id": null` for each ruleset,
  and `scripts/org-ruleset-conformance.py` matches a null-id contract by name: an
  absent ruleset is not a finding there (`check` owns presence and the full image), a
  present one must carry exactly the reviewed bypass actors, and two live rulesets
  under the name fail. The id does not exist until `apply`, and recording it would
  need a follow-up change while the daily audit reported the applied ruleset as
  uncovered; `check` already verifies the whole image by name.
  `scripts/required_reviewer_ruleset_test.py` keeps the two files in agreement.

### `reviewDecision` is not a review verdict here

On the rehearsal's refused sensitive-path pull requests GitHub reported
`reviewDecision` as null, while ordinary ones reported `REVIEW_REQUIRED` from
`main-protection`. Null means GitHub's summary does not describe the required-reviewer
rule, not that no review is required. Enforcement does not depend on that field —
GitHub refuses the merge natively for every actor without a bypass — but merge
automation must treat a null `reviewDecision` as unknown and blocked, never as
satisfied. `scripts/assert-mergeable-head.sh` reads no review state at all (its Gates
A–C are check-run assertions), so it has no fail-open here, and its `true` never
asserted that review requirements are met.

### Rollout

1. Merging this amendment changes nothing live.
2. An organization owner, from a clean checkout of merged `main`, runs
   `python3 -I scripts/required-reviewer-ruleset.py render`, then
   `apply --ack APPLY-REQUIRED-REVIEWERS-1816`, then `check`, which must exit 0.
3. At that rollout, verify what the rehearsal did not establish: an eligible
   `@admins` approval satisfies the rule (rehearsal step 2); a push after approval
   dismisses it (step 3); the merge-authorization App's installation token reads
   `current_user_can_bypass: never` and the release-authorization App's reads
   `always` (steps 6 and 7); that the bare-file pattern `.gitmodules` matches the root
   file; and that `.github/**` matches dotfile paths such as `.github/workflows/x.yml`
   under the ruleset's actual pattern semantics (plausible from rehearsal pull request
   #3, which was refused for that path, but not isolated as a dotfile question). Also
   confirm GitHub echoes `repository_name.protected: true` on read-back; that echo is
   unverified, and if GitHub omits it the create path fails safe, leaving the ruleset
   disabled with a read-back error rather than enforcing an unverified image.
4. Then add `check` to the scheduled `org-ruleset-conformance.yml` audit, so it
   starts green rather than reporting the pre-apply absence daily.
   Done 2026-10-20; see the amendment below.
5. Only then regenerate the remaining adopters' CODEOWNERS (`verjson-cli-cloud#575`,
   `verjson-cli-projects`, `verjson-object-storage`), so no repository is without
   admin review in between. #1816 closes after steps 2–5.
   *Annotation 2026-10-23: step 5 is held while the rulesets stay suspended; see the
   2026-10-23 amendment's rollout order.*

### Consequences

- Sensitive-path pull requests in these four repositories wait for a human `@admins`
  approval even when the AI gate has approved them, including Renovate pull requests
  that bump an action pin under `.github/` (and, in `verjson-object-storage`, anything
  under `scripts/`). That is the requirement the routes expressed, now made binding
  on the autonomous path as well.
- `@admins` has three members (pyousefi, manishb27, AceVentura7). A member cannot
  approve their own pull request, so each sensitive change needs another member's
  review; that is an operational dependency on a small team.
- A new repository that needs sensitive-path approval adds an entry to the config and
  is applied the same way; a fleet-wide `/.github/` requirement remains a separate
  policy decision that the same config can express later.

## 2026-10-20 amendment — Rollout step 4: the daily audit runs `check`

The four rulesets went live on 2026-10-20 (rollout steps 2 and 3, recorded on #1816):
ids 25513484, 25513492, 25513495 and 25513503, and `check` exited 0 against them.
Step 4 adds `python3 -I scripts/required-reviewer-ruleset.py check` to the `audit` job
of `.github/workflows/org-ruleset-conformance.yml`, so a deleted, edited, duplicated
or undeclared `*-sensitive-path-admin-review` ruleset now fails the daily run instead
of waiting for someone to run `check` by hand.

- **Same credential, read-only mode.** The step reuses the `ORG_ADMIN_TOKEN` binding
  the sibling audits already hold (`docs/ruleset-conformance.md` records it as the
  reviewed residual). `check` only issues GETs. The acknowledgement token is not a
  secret: it is a committed literal (`config/required-reviewer-rulesets.json`,
  `scripts/required-reviewer-ruleset.py`), so it does not keep the step from writing.
  What does is the hard-coded `check` mode, which
  `scripts/ci-gate/privileged-scheduled-workflows.test.py` pins as the step's exact
  command. That test rejects `apply`, an appended `|| true`, and any change to the
  step's keys or token binding.
- **Not masked by a sibling.** The step runs under `if: ${{ !cancelled() }}`, so a
  release-authorization or package-surface finding earlier in the job cannot hide a
  deleted reviewer ruleset. The package type-surface step gets the same guard: before
  this amendment, a release-authorization failure skipped it. Nothing on either step
  may turn its failure into success: `continue-on-error`, an `|| true`, or, for the
  required-reviewer step, a different mode fail the contract.
- **Failure states.** Exit 1 means drift: an absent, drifted, duplicated or undeclared
  ruleset. Exit 2 means the audit could not read trustworthy state, such as an API
  error, a target repository that is not live, or an unreadable config. Both fail the
  job. `scripts/required_reviewer_ruleset_test.py` runs the workflow's own step, read
  from the YAML, against a stubbed `gh` and asserts that it never writes, for these
  states: conformant, every ruleset absent, one absent, drifted, undeclared, duplicated
  name, and an API error on the ruleset listing. Its `CheckTest` class covers a
  detail-read error and a target that is not live by running `check` directly, and its
  `read_config` tests cover an unreadable config.
  `scripts/ci-gate/privileged-scheduled-workflows.test.py` rejects every weakening
  above.

#1816 stays open until rehearsal steps 2/3 (an eligible `@admins` approval satisfies
the rule, and a later push dismisses it), steps 6/7 (the App bypass reads) and the
dotfile-match check are evidenced, and step 5 lands.

## 2026-10-22 amendment — A declared suspension of the four reviewer rulesets

**What.** On 2026-10-22 the owner set all four sensitive-path rulesets to
`enforcement: disabled`: 25513484 `verjson-cli-cloud-`, 25513492 `verjson-cli-projects-`,
25513495 `verjson-cli-` and 25513503 `verjson-object-storage-sensitive-path-admin-review`.
The reason is that the admin approval cannot yet be attributed to an agent-plus-human
identity pair. The daily `check` required `enforcement == "active"`, so it would have
gone red until someone edited the config to match, which would also erase the fact that
the controls are off. `config/required-reviewer-rulesets.json` now carries an explicit
`suspended` object (`reason`, `since`, `reenable_when`) on each of the four.

**How the audit reads it.** The declared `enforcement` stays `active`: it is the state
to return to. A `suspended` object makes `scripts/required-reviewer-ruleset.py` expect
`disabled` for that ruleset and still compare name, target, conditions, bypass actors
and rules byte-for-byte. A live-active ruleset declared suspended fails, and a
disabled ruleset not declared suspended fails. `read_config` rejects a malformed
declaration (blank or multi-line text, an impossible or future `since`) and any
declared `enforcement` other than `active`, so disabling cannot be done by editing the
enforcement field. Every `check` prints one `SUSPENDED:` line per suspension with the
reason, date and re-enable condition, even when another ruleset drifts. `render` and
`apply` use the same expected image, and no declaration key is ever sent to GitHub.
`check` remains GET-only. The audit does not expire a suspension: visibility on every
run is the control, and a calendar ceiling would turn a deliberate owner decision red
without any change in the system.

**Re-enable condition.** When agent and human identity attribution exists
(verjson-agent-identity work), delete the `suspended` object from each ruleset, run
`apply` with the acknowledgement, and confirm `check` passes with the rulesets active.
A suspension is a declaration to remove, not a state to leave in place.

**Rollback JSON.** The pre-change image of each ruleset was saved outside the repository
at `/tmp/claude-1000/-home-dev-development--github/537ed082-3522-437a-9434-bf885c03497c/scratchpad/ruleset-<id>-before.json`
for each id above (temporary paths on the owner's machine; the committed declaration is
the durable record, and `render` with the `suspended` objects removed reproduces the
active images).

**Unchanged.** Bypass actors and every other reviewed property of the four rulesets are
unchanged, so the name-keyed bypass contract in
`config/org-ruleset-conformance-policy.json` needs no edit. No generated adopter artifact
or digest depends on this config; it feeds only this script and its test.

**Re-enable condition refined (2026-10-22).** The condition above is superseded by the
more specific trigger in the
[ADR 0202 2026-10-22 amendment](../0202-bind-autonomous-merge-to-independent-review-receipt/README.md):
direct-human approval events with head-SHA and diff binding are verifiable. The
`reenable_when` text in `config/required-reviewer-rulesets.json` is unchanged and is
updated by the change that removes a suspension.

## Amendment 2026-10-22 — bypass window ledger and restore checklist

On 2026-10-22 the owner gave a standing authorization, recorded verbatim: "You have my
ack and approval to bypass all rules until CI is optimized and working before
restoring them." It permits merging past gates (Gate C, ruleset review requirements,
admin-approval requirements) for work in service of optimizing and restoring the
canonical CI. It does not permit irreversible destructive acts (deleting published
package versions, force-push, data loss, secret exposure), and it never permits
weakening a test or check; bypass means merging past a gate with evidence. This
amendment is the ledger that makes every bypass restorable. It adds no new decision
beyond the dated authorization; it extends the suspension recorded above.

### Bypass ledger

1. **Four `sensitive-path-admin-review` rulesets suspended** (`enforcement: disabled`,
   declared in `config/required-reviewer-rulesets.json`): `25513484` verjson-cli-cloud,
   `25513492` verjson-cli-projects, `25513495` verjson-cli, `25513503`
   verjson-object-storage. Pre-change images (ruleset configs only; checked for
   credentials, none present) are committed at
   [`rollback/ruleset-<id>-before.json`](rollback/ruleset-25513484-before.json), so restoration does not depend on
   `/tmp`. Each file records `enforcement: active`.
2. **Gate C exception.** verjson-cli-projects#159 was admin-merged at head `9978df1e`
   (merge commit `35fe7a9b`) while the `AI review authorization` check was orphaned: the
   stale caller `63fc49c6` fails `arm run provenance mismatch`. The root cause is source
   casing, fixed upstream in `65529002`. #159 is too large for the
   [ADR 0227](../0227-arm-bootstraps-stale-review-callers/README.md) bootstrap (38 files
   against a cap of 32), so the bootstrap could not carry it.
3. **Docs PRs merged with `assert-mergeable-head` exit 3** (no required checks declared,
   so Gate A has nothing to assert): verjson-docs#18 (`c6349dd5`),
   verjson-agent-identity#3 (`3314afaa`), verjson-agent-identity-infra#3 (`445051bd`),
   verjson-cli-identity#3 (`af1ecf5e`), all merged 2026-10-22 ~05:03Z.
4. **Earlier admin merges in the same session window** (2026-10-22): Verjson/.github
   #1870, #1871, #1872, #1873, #1876. Listed by merge window; the per-PR gate
   outcome for each is in its own PR thread, and this entry does not re-derive it.
5. **Admin merge past the review requirement.** Verjson/.github#1884 (ARB change 2,
   slice 1) was admin-merged at head `6f80be71` (merge commit `df3fd29f`) after
   `assert-mergeable-head.sh` exit 0 and every check `SUCCESS`/`SKIPPED`; no gate was
   weakened. Two Codex models and a `code-reviewer` pass were adjudicated before the push.
6. **Admin merge of a ledger-only PR.** Verjson/.github#1885 (records item 5) was
   admin-merged as `ee02ffd6`. It is a documentation-only change; it was not recorded
   when merged, and this entry corrects that.
7. **Admin merge past the review requirement.** Verjson/.github#1886 (ARB change 3a,
   concurrency decision) was admin-merged at head `64db2a8f` (merge commit `d31b9729`)
   after `assert-mergeable-head.sh` exit 0; superseded cancelled runs were set aside by
   the gate, every current check passed. Two Codex models and a `code-reviewer` pass were
   adjudicated before the push; the Codex pair was not re-run after the fixes.
   The merge of the PR that adds this entry is recorded by the next ledger update.
8. **Admin merge past the review requirement.** Verjson/.github#1890 (the
   `secretless-skip-install-scripts` input, finishing #1888) was admin-merged at head
   `ab7665b7` (merge commit `6781138a`) after `assert-mergeable-head.sh` exit 0 and every
   check `SUCCESS`/`SKIPPED`; no gate was weakened. `gpt-6-astra` and `gpt-6.1-sol` ran six
   review rounds to completion plus three `code-reviewer` rounds before the first push; the
   last round's findings were adjudicated, and one test-pin commit (`ab7665b7`) followed it.
   The merge of the PR that adds this entry is recorded by the next ledger update.
9. **Ledger-only admin merges.** Verjson/.github#1887 (records item 7) was admin-merged as
   `43808054` and #1892 (records item 8) as `3a425c25`; both are documentation-only changes,
   recorded here because they were not recorded when merged.
10. **Admin merge past the review requirement.** Verjson/verjson-cli#347 ("docs: add
    customer identity deployment to CLI family") was admin-squash-merged on 2026-10-22 as
    `8e963a03` at head `0c73bbb`. Fable approved the final head `0c73bbb`; Codex
    (gpt-6-astra and gpt-6.1-sol) approved the earlier head `9627289`, so the final head
    was not reviewed by the Codex pair.
11. **Admin merge past the review requirement.** Verjson/.github#1893 (typed `test-levels`
    generator, slice 1 of ARB change 3, `Part of #1869`) was admin-merged at head
    `ba88dd9f` (merge commit `a451a763`) after `assert-mergeable-head.sh` exit 0 and every
    check `SUCCESS`/`SKIPPED`; no gate was weakened. Four Codex rounds (`gpt-6-astra`,
    `gpt-6.1-sol`) and three `code-reviewer` rounds ran to completion before the push; the
    final one-line change rejecting `HEAD` as a level branch name was reviewed by the PM
    only, with no Codex pass after it.
    The merge of the PR that adds this entry is recorded by the next ledger update.
12. **Docs-only admin merges.** Verjson/.github#1894 (records item 11) was admin-merged as
    `d90caf0a` and #1895 (documents the nested-only install-script limit for adopters,
    #1891) as `100397e5`. Both are documentation-only changes and were not recorded when
    merged; this entry corrects that. #1895 received no model review.
13. **Admin merges past the review requirement.** Verjson/.github#1896 (the ADR 0210
    CODEOWNERS routing decision, `Part of #1816`) as `2d067ee6` at head `aa9027f1`, #1897
    (#1889, ADR-index title fix) as `65c71314`, #1898 (#1845, baseline `.npmrc` assertion)
    as `07538d82` and #1899 (ADR 0238 annotation, `Part of #1875`) as `1eb0e477`. Each
    followed `assert-mergeable-head.sh` exit 0 and green checks; no gate was weakened.
    #1896 had `gpt-6-astra` and `gpt-6.1-sol` design and review rounds plus a
    `code-reviewer` pass, adjudicated before it was marked ready. #1897 and #1898 had a
    `code-reviewer` pass only, and #1899 (docs-only) had no model review. #1897 needed a
    merge of `main` into its branch (no force-push) to clear a stale `AI review
    authorization` failure ("Arm receipt verification failed") that a reopen did not,
    and a re-key of the `release.yml` `verify` row in
    `scripts/ci-gate/public-pr-runner-isolation.pending.tsv`, whose digest binds
    `scripts/gen-adr-index.sh`. The merge of the PR that adds this entry is recorded by
    the next ledger update.
14. **Admin merge past the review requirement.** Verjson/.github#1900 (matrix fail-fast on
    pull requests and cheap gates first, `Part of #1817`; the cancel job is recorded as not
    adopted in ADR 0238) was admin-merged as `e4cce4d6` at head `9e1568e0` after
    `assert-mergeable-head.sh` exit 0 and every current check `SUCCESS`/`SKIPPED`, except the
    advisory `AI review authorization` check, which concluded `neutral` (no verdict);
    superseded cancelled runs were set aside by the gate. No gate was weakened. `gpt-6-astra` and
    `gpt-6.1-sol` ran a design round and a review round, plus a `code-reviewer` pass, before
    the first push; their estimator findings were fixed afterwards and that fix was not
    re-reviewed. The merge of the PR that adds this entry is recorded by the next ledger
    update.
15. **Docs-only ledger PR.** Verjson/.github#1901 ("docs(adr): record the
    #1896 through #1899 admin merges in the ADR 0210 ledger") merged
    2026-10-23 with head `530319e0cf8df851ae6e8e45f564434da2b00dd7` and
    merge commit `6469b7a16419a4f0e48b73c983d4c9362cf0943a`. GitHub reports
    it was merged by `pyousefi`; its only changed paths were its NEXT fragment
    and this ADR. The PR body says it had no model review; its check rollup
    had SUCCESS and SKIPPED conclusions except the advisory AI review
    authorization check, which was NEUTRAL. GitHub's PR metadata does not show
    whether `--admin` was used or retain the local assertion output, so this
    entry records neither as verified.
    The merge of the PR adding this entry is recorded by the next ledger update.
16. **Admin merge past the review requirement.** Verjson/.github#1902 (the secretless
    transfer-restore diagnostic and troubleshooting docs, `Part of #1830`) was admin-merged
    as `dd614b2e` at head `3d4b8422` after `assert-mergeable-head.sh` exit 0 and every
    current check `SUCCESS`/`SKIPPED`; superseded cancelled runs were set aside by the gate.
    No gate was weakened; the restore keeps `fail-on-cache-miss: true`. `gpt-6-astra` and
    `gpt-6.1-sol` ran a design round and a review round, plus a `code-reviewer` pass, before
    the first push; their findings were fixed afterwards and those fixes were not re-reviewed.
    The merge of the PR that adds this entry is recorded by the next ledger update.
17. **Docs-only ledger PR.** Verjson/.github#1904 ("docs(adr): record the
    #1902 admin merge in the ADR 0210 ledger") merged 2026-10-23 with head
    `5c8f419dab6f5f32cd628ab155fa5f4a6c9138d4` and merge commit
    `6ef749effddaa83f45de4b2cba7869955c10c30b`. GitHub reports it was merged
    by `pyousefi`; its only changed paths were its NEXT fragment and this ADR.
    Its review list was empty; its check rollup had SUCCESS and SKIPPED
    conclusions except the advisory AI review authorization check, which was
    NEUTRAL. GitHub's PR metadata does not show whether `--admin` was used or
    retain the local assertion output, so this entry records neither as verified.
    The merge of the PR adding this entry is recorded by the next ledger update.
18. **Authorization:** the verbatim text and date above.

### Exit condition ("CI optimized and working"), measurable

The window closes when all hold, each evidenced in the closing PR:

- Changes 1-6 of the untrusted-PR isolation work on
  [Verjson/.github#1869](https://github.com/Verjson/.github/issues/1869) have landed or
  been explicitly dropped by recorded decision.
- An adopter canary outside the Verjson organization runs the canonical CI green.
- Runner minutes per run are measured for the canonical workflows and recorded, with
  the figure compared against the pre-optimization baseline.

### Restore checklist

- [ ] Prerequisite: verifiable direct-human approval events bound to head SHA and diff
  exist, per the
  [ADR 0202 2026-10-22 amendment](../0202-bind-autonomous-merge-to-independent-review-receipt/README.md).
  This amendment does not supersede that condition.
- [ ] Delete the four `suspended` objects in `config/required-reviewer-rulesets.json`
  and update the `reenable_when` text, in one change.
- [ ] Re-enable through the audited path: `scripts/required-reviewer-ruleset.py apply`
  with its acknowledgement, which verifies the full image while disabled and rolls back
  if activation verification fails; then `check`, which must pass with all four active
  and print no `SUSPENDED:` line.
- [ ] Only if `apply` cannot be used, fall back to `PUT /orgs/Verjson/rulesets/<id>`
  with the saved JSON from `rollback/` (strip read-only keys such as `id`, `source`,
  `_links` if rejected), then diff the read-back against the saved image in full, not
  only `enforcement`.
- [ ] Verify the verjson-cli-projects caller on `main` is replaced (no stale `63fc49c6`
  pin) and that `AI review authorization` succeeds on a fresh PR head; that check is
  published against the PR head, not `main`.
- [ ] Re-run `assert-mergeable-head` on each open PR that relied on exit-3 or Gate C
  handling.
- [ ] Add a dated line here recording the restoration and its evidence.

## 2026-10-23 amendment — Review routing: the canonical CODEOWNERS stays default-only

- **Issue:** [#1816](https://github.com/Verjson/.github/issues/1816) (the merit question it
  left open); this amendment is `Part of #1816`, which stays open for the restore checklist.
- **Category:** review routing (sensitive class)

### Context

#1816 asks whether the canonical generated `.github/CODEOWNERS` should carry a
sensitive-path rule such as `/.github/ @verJSON/admins` and `/.gitmodules` beyond
`* @verJSON/devs`, because any of nine `devs` can satisfy the code-owner review that
`main-protection` (18098028) requires for a workflow change (the issue counts nine). The
[2026-10-19 amendment](#2026-10-19-amendment--per-repository-sensitive-path-approval-is-a-native-required-reviewer-rule)
answered by moving additional approval to native per-repository rulesets; this
amendment records the merit decision on the CODEOWNERS option itself, because the
owner has since suspended those rulesets (the 2026-10-22 amendment "A declared
suspension of the four reviewer rulesets") and the question of
whether CODEOWNERS should carry the route in the meantime is a live one. The option set
below was put to two read-only reviewers (`gpt-6-astra`, `gpt-6.1-sol`) independently,
before this text was written; their conclusions matched and are summarized on the pull
request.

### Options considered

| Option | Verdict |
| --- | --- |
| Do nothing and say nothing | Rejected: leaves the exposure unrecorded. |
| Keep `* @verJSON/devs`, record the decision, the residual risk and the restoration prerequisites | **Chosen.** |
| Fleet-wide `/.github/` and `/.gitmodules` (or workflows only, or all paths) to `@verJSON/admins` | Rejected, see below. |
| List `devs` and `admins` on the sensitive paths | Rejected: either team satisfies the review, so admin approval is not required. |
| Opt-in generated admin variant (a second pinned digest) | Rejected for now: dormant machinery whose only purpose is enforcement the owner has suspended; it also needs downgrade protection. |
| Variants by repository class | Rejected: needs a reviewed classification and does not supply approval identity. |
| Local appended rules or overlays | Rejected: CODEOWNERS applies the last matching pattern, so a local line can override an inherited one; the digest test exists to refuse exactly this. |
| Drop `require_code_owner_review` from `main-protection` | Rejected: removes the `devs` review that is real today. |
| Native per-repository `required_reviewers` rulesets | Retained as the eventual mechanism (2026-10-19 amendment); suspended. |

### Decision

- **No generator change.** The canonical bytes remain the single rule
  `* @verJSON/devs`. `config/codeowners/CODEOWNERS`, the copy embedded in
  `scripts/gen-changelog-caller.sh`, `EXPECTED_CODEOWNERS_SHA256` in the generated
  contract test and this repository's own `.github/CODEOWNERS` are untouched, so no
  golden file or digest moves. `scripts/codeowners_test.py` already pins the rule set to
  exactly `['* @verJSON/devs']`; that assertion is the executable form of this decision.
- **Why a fleet-wide admin route is wrong now.** `main-protection` is active on every
  repository, so a generated `/.github/ @verJSON/admins` line would take effect in every
  adopter on regeneration. That re-enables, at fleet scale and without the per-repository
  scoping the rulesets have, the admin approval the owner suspended because it cannot yet
  be attributed to an agent-plus-human identity pair. Three `admins` would become the
  reviewers of every Renovate action-pin bump and every contract regeneration, all of
  which touch `.github/workflows/`. It also would not constrain the actors that carry the
  autonomous path: the merge and release Apps and organization owners bypass
  `main-protection`, and with them its code-owner requirement.
- **Why the rulesets, not CODEOWNERS, are the right mechanism.** Ownership (who is
  asked) and authorization (whose approval is required) have different lifecycles. A
  ruleset is additive, scoped to named paths in one repository, and has no
  organization-owner bypass; CODEOWNERS routes into a shared review requirement that
  other rules also satisfy.
- **Accepted residual risk.** While the suspension holds, a change to a workflow file
  that no generated contract guards (for example a hand-written `ci.yml` with no digest
  pin) can be approved by two `devs` in any adopter whose CODEOWNERS is default-only.
  A live read on 2026-10-23 found that to be `verjson-cli` (since #302),
  `verjson-cli-cloud` and `verjson-cli-projects` (since their regenerations on
  2026-10-22); `verjson-object-storage` alone still routes `/.github/`, `/scripts/` and
  `/docs/decisions/` to `@Verjson/admins` in CODEOWNERS, and keeps it only until it
  regenerates, which the rollout order below holds. This is the exposure #1816
  describes; it is accepted by the owner's standing direction, not resolved. Execution
  hardening, such as the release credential isolation, limits what such a change can
  reach and is the complementary control, but it is not mandatory admin approval.

### How it composes

- **With the suspension.** No ruleset is edited, enabled or re-declared, and
  `config/required-reviewer-rulesets.json` is unchanged. The decision to add a
  CODEOWNERS route is deferred behind the same condition as the rulesets (verifiable
  direct-human approval bound to head SHA and diff, ADR 0202 2026-10-22 amendment).
- **With the generated-contract test.** The test still refuses any local edit. A future
  change to the bytes is one reviewed change that moves the source file, the embedded
  copy, the digest, this repository's self-adoption and the drift test together
  (ADR 0191), and adopters then regenerate the whole pinned set at one contract SHA,
  never a subset.

### Rollout order

1. Merge this amendment; nothing live changes and no adopter regenerates for it.
2. Hold the 2026-10-19 rollout step 5 for the one repository still carrying admin routes:
   do not regenerate `verjson-object-storage`'s CODEOWNERS while its ruleset is
   suspended, because regeneration would drop the routes it still carries on `main` with
   nothing replacing them.
3. When the restore checklist's prerequisite is met, re-enable the four rulesets
   through `apply`, then satisfy the evidence the earlier amendments left open before
   any route is removed or #1816 closes: an eligible `@admins` approval satisfies the
   rule, a later push dismisses it, the merge and release Apps' bypass reads, and the
   `.github/**` dotfile match and the bare `.gitmodules` root-file match. `apply` verifies the ruleset image, not those behaviors.
4. Only then regenerate `verjson-object-storage`.
5. A fleet-wide CODEOWNERS sensitive path is reconsidered only as a separate policy
   decision after step 4, with measured `admins` review volume, and then as a new
   generator version under the full-set regeneration rule.
