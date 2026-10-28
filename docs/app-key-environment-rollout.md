# Main-only App key rollout

This is the operational completion record for [#1285](https://github.com/Verjson/.github/issues/1285)
and [ADR 0166](decisions/0166-environment-only-app-private-keys/README.md), with its transport decision superseded by [ADR 0171](decisions/0171-inherited-reusable-environment-context/README.md).
Contract delivery and exposure removal are separate milestones. This source change
does not create environments, copy/delete keys, edit rulesets or dispatch consumers.

| Role | Required reusable input | Caller-owned environment | Environment secret |
| --- | --- | --- | --- |
| Release | `release_environment` | `release-app` | `RELEASE_APP_PRIVATE_KEY` |
| Merge | `merge_environment` | `merge-app` | `MERGE_APP_PRIVATE_KEY` |
| AI review | `ai_review_environment` | `ai-review-app` | `AI_REVIEW_APP_PRIVATE_KEY` |

Runner host evidence is a caller-owned role in the existing `production`
environment, using `RUNNER_HOST_EVIDENCE_APP_PRIVATE_KEY`. It is not an
organization secret; each consuming deployment repository must provision its
own reviewed read-only App key before host export can run. [ADR 0198](decisions/0198-confine-runner-host-evidence-credentials/README.md)
records that credential boundary.

These three roles are not the whole surface. `config/app-key-roles.json` declares
every App private key a canonical workflow binds, and
`scripts/ci-gate/app-key-environment.test.py` enumerates `.github/workflows` against
it in both directions, so a new binding must state its confinement. Two keys are
declared `unconfined` and are still readable from any non-fork ref:
`RENOVATE_COMPATIBILITY_APP_PRIVATE_KEY` and `DEPENDENCY_SUPERSESSION_APP_PRIVATE_KEY`.
[ADR 0179](decisions/0179-exhaustive-app-key-confinement/README.md) records their
provisioning prerequisite and why the environment binding waits for it.

`scripts/org-secret-scope-audit.py` declares an App key marked for withdrawal with
`target_visibility: "withdrawn"`, meaning the organization copy must not exist. Its
nightly run is the standing tracker for the remaining broad copies: it names each
surviving one rather than reporting a generic manifest mismatch, and turns green for
a key only when that copy is deleted.

Keep public App client IDs in their existing variables. Model API credentials and
`NODE_AUTH_TOKEN` retain their existing roles. App-key reusable calls now inherit the caller secret context, including these unrelated secrets; non-App workflows retain narrow grants.
`node-release.yml` does not consume the release App key. Snapshot publication and
container release do.

## Record every remaining broad copy as migration residue

`config/org-actions-secret-policy.json` is the only place a broad organization copy of
an App private key is recorded, and for the review, merge and release roles it may be
recorded only as `custody: environment-only-migration-residue` with a `withdrawal`
contract path, tracking issue and record date. There is no approved organization
visibility target for those roles: `scripts/org-secret-scope-audit.py` rejects one,
rejects an unrecognized `_APP_PRIVATE_KEY` name outright, and reports
`secret-scope-policy=withdrawal-pending` for as long as any residue entry exists.
Readable secret metadata proves a name and a visibility only; it never proves that a
key is confined to its main-only environment. See
[ADR 0180](decisions/0180-no-approved-organization-custody-for-environment-only-app-keys/README.md).

## Stage the entire shared-pin cohort

Inventory every App-key reader and every repository selected by the relevant
organization secrets and inherited required workflows. Include generated release,
privileged merge/retry, review/rearm and canary callers.
Use authenticated metadata queries; a forbidden or incomplete inventory is unknown,
never proof of absence. Do not print or hash key values.

Authenticated inspection initially found ruleset `20722935` following
`.github/workflows/gate-rearm.yml` from repository `1269388380` at
`refs/heads/main` without an immutable SHA. The operator installed and verified
its behavior-preserving protective freeze at `2026-09-10T02:10:43.462Z`, selecting
`c597d6908e3aa38d9a041c2150afadcbff32cf6d`. Only the workflow entry's SHA changed;
its path/ref/repository identity, enforcement, bypass actors and repository/ref
selectors remain intact. The retained preimage/candidate/postimage receipt belongs
to #1285. This freeze preserves the broad/forwarded review-key contract and #1275
body-edit admission guard while consumers are prepared. It does not activate the
environment-key contract or remove any broad key.

Do not move this protective pin to the new contract for one prepared repository,
or remove the broad AI key while any inherited required workflow still needs it.

For every affected repository, prepare the three role environments with:

```json
{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
```

Each environment's deployment-branch policy collection must contain exactly:

```json
{"name":"main","type":"branch"}
```

Remove obsolete wildcard, tag and other branch rules through the owner-reviewed
rollout. Reviewers/timers may add approval delay but cannot replace the exact branch
restriction. Confirm the repository default branch is `main`. Store each private
key directly in its environment with a secure local file/stdin operation, for example:

```sh
gh secret set RELEASE_APP_PRIVATE_KEY --repo OWNER/REPO --env release-app < /secure/path/release.pem
```

Use the corresponding environment/key for merge and review. Keys never belong in
issues, chat, artifacts, logs, PR bodies or workflow inputs. Provisioning alongside
existing broad copies is a temporary migration state and remains exposed.

## Activate and remove broad copies

After all repositories in the shared required-workflow cohort are provisioned,
regenerate each caller from the same reviewed immutable contract SHA. Keep complete
changelog caller/renderer/test/release artifacts together and exercise the pinned
release command in a disposable checkout. Confirm each generated caller selects
the fixed role name, inherits the secret context, and targets the exact reviewed immutable workflow SHA. Every nested App-key reusable edge must inherit as well; policy-only edges receive no secrets.

Coordinate advancement of the protective `gate-rearm.yml` pin to the new reviewed
contract with those consumer PRs, after the entire selected cohort is provisioned.
Verify exact-main execution and the native environment deployment records for every
key role. An uncredentialed policy prerequisite must fail if environment metadata
cannot be read with the job token. Do not add a broad App/token just to make that
check green. The workflow cannot prove that a populated key came from the
environment while broad copies remain.

Only after all old readers are retired, delete the organization and repository
copies of the three App private keys through the authorized operator rollout.
Record the affected cohort, policy and contract SHAs, successful canary runs and
metadata-only deletion receipts. Run:

```sh
python3 scripts/app-key-environment-audit.py --repo OWNER/REPO
```

The audit uses `gh` with operator permission to list organization, repository and
environment secret metadata. It is read-only and returns exit 1 for unreadable or
inconsistent inventory, missing environment keys, broad copies or policy drift.
An organization-wide broad copy fails the audit even if it is currently scoped
away from this repository; this prevents calling a partial migration complete.
The ordinary job token need not and should not gain that metadata authority.

Finally, repeat the non-main branch proof with a harmless workflow that omits
`environment` and reports only whether each key is populated. All three must be
absent. A separate non-main job requesting a role environment must be denied by
GitHub before it starts. Clean up proof branches after retaining the nonsecret
run URLs and results. Do not declare #1285 exposure removed until every affected
repository has both successful main consumption and these denial receipts.

Rollback pauses dispatch and restores a reviewed environment/pin configuration
within the same isolated storage model. It never restores an organization or
repository App private key to regain compatibility.

## Canonical repository self-adoption gate

The canonical repository runs native main entrypoints, so merging this contract activates its own environment-bound jobs without a consumer repin. Its three role environments and exact main-only policies are now provisioned and verified. The [2026-09-10 live receipt](https://github.com/Verjson/.github/issues/1291#issuecomment-5611815789) records sealing run `34429861784`, three successful environment-secret metadata readbacks and proof run `34429989792`; every expected App minted a token restricted to `.github`, and the local receipt verifier exited 0.

Self-adoption retires both temporary bootstrap workflows and all migration-only executable/configuration/dependency files, test and routing registrations. [ADR 0169](decisions/0169-sealed-environment-app-bootstrap/README.md), the retired runbook and externally retained ciphertext/metadata receipts preserve the history. No broad copy is withdrawn and the shared protective pin is not advanced by this change. Prepare and verify the entire shared-pin cohort before that cutover; #1285 remains open until exposure-removal and denial receipts cover the cohort.

The [native environment-denial receipt](https://github.com/Verjson/.github/issues/1285#issuecomment-5611832146) records run `34430152626` at keyless non-main proof commit `49779d3`. GitHub denied all three explicitly requested role environments before any runner or step started (`runner_id: 0`, empty steps). The proof branch was removed after checking its SHA. Together with main proof `34429989792`, this demonstrates the three native environment boundaries; it does not prove absence of broader secret copies, which remain pending cohort migration.

## Reusable transport correction

Do not adopt `1664d518c13f207916f793b3218bee545209b8ee`: its no-grant reusable secret transport failed after merge. The [controlled runtime receipt](https://github.com/Verjson/.github/issues/1285#issuecomment-5612184570) proves only inheritance populated the environment-only sentinel; all four same-source non-main calls were denied before execution. Follow ADR 0171 and regenerate the complete affected callers at the replacement immutable revision after its operational proof is recorded.

Inheritance exposes all accessible caller secrets to reviewed called code, including broader copies that still exist. Do not serialize or dynamically inspect that context, and do not interpret a successful real App mint as exclusive-storage proof. The metadata audit remains the storage check; whole-cohort migration and broad-copy withdrawal are still outstanding. The temporary probe is retired; its operator-owned noncredential sentinel is removed after operational verification.
