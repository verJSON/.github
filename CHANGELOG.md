# v4.1.1

## Give generated release callers' package token only to exact private downloads

Security fix: the release callers that `scripts/gen-changelog-caller.sh` generates no longer give `NODE_AUTH_TOKEN` to `npm ci --ignore-scripts`. This covers the `verify` job of release-node, release-snapshot, and release-artifact, and release-artifact's `acquire-private-dependencies` job. Under npm 10.9.9 and 11.19.1, a git dependency's own `.npmrc` received the token in every one of them, and under npm 10 linked and git dependencies also ran `prepare` with it. Every credentialed install now runs `scripts/npm-private-acquisition.sh`, the acquisition `node-release.yml` already used and now runs from the same file. It is checked out at the immutable contract commit and only `npm cache add`s sha512-verified locked `npm.pkg.github.com` downloads. A tokenless `npm ci --ignore-scripts --prefer-offline` then installs the tree from that cache. The generated contract test pins the new steps, and `scripts/release-caller-credential-isolation.test.py` runs them under both npm majors against a hostile git dependency. The contract test now pins release-artifact's whole acquisition job: settings, step sequence, actions, environments, and run bodies. Both jobs also refuse a release tree that carries `.verjson-npm-acquisition-contract`, and `verify` refuses one that carries `.changelog-contract` before checking out the selection contract. In release-artifact, the shared acquisition script itself now enforces the approved-package list through `APPROVED_PRIVATE_PACKAGES`. It refuses any locked GitHub Packages download whose package name is not approved before npm runs, so every credentialed fetch is approved by construction. The contract test compares both allowlist values exactly as parsed, so a comment can no longer stand in for a widened list, and the script runs only from `$GITHUB_WORKSPACE`. node-release and the verify jobs set no list and trust the caller's lockfile. As defense in depth, the release-artifact lock validation also refuses `npm-shrinkwrap.json`, a resolved root lock entry, a private download outside `node_modules/`, non-ASCII or trailing-dot GitHub Packages hosts, and a private `resolved` URL that WHATWG parsing would rewrite, and it treats npm aliases like their target. The acquisition job's `name`, `needs`, `if`, `runs-on`, and `timeout-minutes` values are pinned, and the verify job may no longer add settings such as `container:`. Adopters, including this repository's own `.github/workflows/release.yml`, get the fix only when their callers are regenerated at a contract commit that contains it. See ADR 0220's second 2026-10-18 amendment.

_Date: 2026-10-18; id:8cabee01_

# v4.1.0

## Give the node-release package token only to exact private package downloads

Security fix: `node-release` no longer gives `NODE_AUTH_TOKEN` to `npm ci --ignore-scripts`. Under npm 10, which current Node 18, 20, and 22 releases bundle, that command still ran `prepare` for linked and directory dependencies with the token. Under every npm, a git dependency's preparation passed the token to a child `npm install` that read the dependency's own `.npmrc`. The credentialed step now only runs `npm cache add` for each locked `npm.pkg.github.com` download URL and checks the cached content against the lockfile's sha512 integrity. Then `npm ci --prefer-offline` installs and runs every lifecycle script without the token. When private downloads exist, acquisition refuses npm majors other than 10 and 11, and both steps share one explicit npm cache under `RUNNER_TEMP`. Private lock entries need lockfile version 2 or 3, an exact download URL, and sha512 integrity; a version 1 lockfile with no private packages still installs. A git dependency whose preparation needs private packages now fails instead of receiving the token. See ADR 0220's 2026-10-18 amendment.

_Date: 2026-10-18; id:fde0ede7_

## Accept the owner's scope spelling in container private-dependency download URLs

Fix: the container candidate's private-dependency acquisition
(`scripts/container_private_dependencies.py`) now accepts a GitHub Packages download URL
that records the organization's own spelling of the scope, such as
`https://npm.pkg.github.com/download/@verJSON/pg/1.2.3/<hash>`, for the lowercase package
`@verjson/pg`. Before this change, the scope was compared case-sensitively, so every lock
entry resolved with that URL was rejected as an unapproved private package. Only the scope
is folded, and only when it is ASCII: Python's `str.lower()` maps the Kelvin sign (U+212A)
to `k`, so a non-ASCII scope is rejected rather than folded. The package name, version,
approved-list membership, and the exact URL shape are still matched exactly, as the
generated release caller's private lock validator already does (#1713). Adopters pick up
the fix when their container-candidate caller is regenerated at a contract SHA that
includes it, because the caller pins this script by digest.

_Date: 2026-10-18; id:9c4e17a2_

## Re-record the authorization-arm finding after verjson-editor regenerated

Maintenance: the scheduled `arm-audit` job adjudicates the reduced finding again.
verjson-editor landed its four generated callers, including the `ai-review-merge.yml`
admission caller. The live finding therefore shrank to 20 missing generated callers across
18 armed repositories (17 `ai-review-label-rearm.yml`, 3 `ai-promotion-retry.yml`). Any
change to the finding counts as drift, so the job failed on 2026-10-16 and 2026-10-17
until `config/audit-expected-findings.json` recorded it. The remaining callers are
consumer regeneration tracked by #1810.

_Date: 2026-10-17; issue #1810_

## Bind the contract canary's same-repository Cosign publisher to its source commit

Fix: the container-candidate contract canary can sign and verify its own provenance
again. It calls the publisher with `uses: ./`, so its GitHub OIDC `job_workflow_ref`
ends in `@refs/heads/main`. Since #1690, the signer had rejected every such ref as "not
immutably pinned" (run 39060824097). Adopters, which call `Verjson/.github/...@<sha>`,
see no behavior change.

_Date: 2026-10-17; id:e15d144f_

## Make actions-ci row lines unforgeable and add structured runtime records

The actions-ci group runner now replays each row's output inside a random-token `::stop-commands::` block with every line, and every bare-carriage-return segment, prefixed by `| `, so command output can no longer hide, mask, or forge the `actions-ci command group=…` row line that the verjson-observability#271 collector parses; the row line's format is unchanged. Each `shell-test-groups` cell also uploads a seven-day `actions-ci-runtime-records-<run>-<attempt>-<group>` artifact holding one JSON record per row (status, elapsed, budget, timestamps, timeout, background processes, runner identity), written by the runner after each row exits to a path no row is told.

_Date: 2026-10-17; id:c4b5a059_

## Reclaim hosted disk and fail fast before container candidate image builds

On GitHub-hosted runners, the container candidate publisher now removes the large preinstalled hosted-image toolchains its image builds never use (Android SDK, .NET, GHC, CodeQL, Swift, PowerShell) on every row a publish job may build, with or without private Node packages. It also refuses to start an image build unless the Docker data filesystem holds twice the measured node_modules context plus a 4 GiB margin. If it doesn't, the build fails with an explicit required-versus-available error instead of an opaque `ENOSPC` mid-build.

_Date: 2026-10-17; id:83b2ec0e_

## Accept the strict admission check in the bypassless cli-projects ruleset exception

Fix: the scheduled `Organization ruleset conformance` audit accepts ruleset 21830367
(`cli-projects-package-surface-required-workflow`) again. Since the 2026-10-15 rotation
added ADR 0212's strict `admission` status check, the audit had reported `required release
authorization bypass is absent`. No bypass actor is added.

_Date: 2026-10-17; id:20261018T045749Z; refs #1423_

## Record the live receipts for both #1828 required-workflow rotations

ADRs 0151 and 0153 now record the exact-head `verify-run` receipts that complete the
rotations of rulesets 21750617 (authn) and 21830367 (CLI projects) to `c78fcd4e` (Part of #1423). Both
exited 0 against fresh organization-rule runs. ADR 0153 also records why the first CLI
projects run was refused: its head still carried the pre-generator caller.

_Date: 2026-10-16; id:20261016T145143Z_

## Pin the generator sandbox to the interpreter's runtime library directory and record pending rotation receipts

A test now gives the sandboxed historical generator a decoy build-time `LIBDIR`. It
asserts that the runtime prefix (`sys.base_prefix/lib`) is the only library directory
used. Without it, a switch to the build-time path could pass review while it breaks
self-hosted runners.

_Date: 2026-10-16; id:20261016T014014Z_

## Repin the CLI projects required workflow to node-ci-protected v4.0.0

Hardening: both protected Node lanes of `cli-projects-package-surface-required.yml`
now call `node-ci-protected.yml` at `576707fe` (v4.0.0), which contains #1750 and
#1759, instead of `1c7659b7`. `config/cli-projects-required-node-ci.json` now pins the
protected lanes (`protected_node_ci_sha`) separately from the push-only consumer caller
(`consumer_node_ci_sha`), so this repin leaves the consumer `ci.yml` digest that admission
requires unchanged and needs no `verjson-cli-projects` change. Organization ruleset
21830367 keeps running the old revision until an operator rotates it to this change's
merge commit ([ADR 0153](../docs/decisions/0153-cli-projects-package-surface-required-workflow/README.md)).

_Date: 2026-10-15; issue #1423_

## Route secretless trusted-ref builds to the hosted untrusted lane

Canonical `node-ci.yml` (and the generated `node-ci-protected.yml`) now routes the `build-test` job of a `secretless-trusted-ref` call to `CI_LANE_UNTRUSTED`, the same ephemeral GitHub-hosted lane secretless pull-request builds already use, ahead of a caller's `runner` input. Every other job, including `eligibility`, keeps the caller's route, so push validation pays for hosted capacity only in the one job whose lifecycle rebuild and candidate sandbox refuse to run elsewhere.

_Date: 2026-10-15; id:f5f026ef_

## Admit heads whose cancelled run was displaced by another re-arm workflow or left an aggregator failure behind

`scripts/assert-mergeable-head.sh` now sets aside a cancelled run displaced from a declared concurrency family by a later successful run of that family, and an aggregator failure that completed after the latest cancelled check of its run when the newest later run of the same workflow re-did that check to SUCCESS (ADR 0226 amendments, 2026-10-15).

_Date: 2026-10-15; id:f26743133a_

## Forward the package retention window to the release verification hook

Generated `release-node`, `release-artifact`, and `release-snapshot` callers now pass `CI_PACKAGE_RETENTION_KEEP` into the clean `env -i` environment of the release verification step, so a repository `scripts/release-verify.sh` sees the same retention window the post-publish retention job deletes against. Consumers pick it up by regenerating their adopter set at the new contract SHA; verjson-ci#298's retention-aware release hold is the first consumer.

_Date: 2026-10-15; id:20261015T232302Z_

## Keep public pull-request code off persistent self-hosted runners

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

_Date: 2026-10-15; id:20261015T230522Z_

## Repin the authn required workflow to node-ci-protected v4.0.0

Hardening: the generated `authn-type-surface-required.yml` now calls
`node-ci-protected.yml` at `576707fe` (v4.0.0), which contains #1750 and #1759,
instead of `3f8fb4ba`. Organization ruleset 21750617 keeps running the old revision
until an operator rotates it to this change's merge commit
([ADR 0151](../docs/decisions/0151-authn-type-surface-required-workflow/README.md)).

_Date: 2026-10-15; id:20261015T230400Z_

# v4.0.0

## Renovate maintains generator action pins so adopters stop hand-editing generated callers

`renovate.json` gains a regex custom manager over `scripts/gen-*.sh|py` that reads every `release_*='owner/repo@<sha> # <tag>'` pin declaration in the changelog caller generator, with the `github-actions` manager's datasource and versioning. Renovate now proposes those digest bumps here, in the same grouped `github actions digests` PR as this repository's workflows, instead of in adopters' generated callers. The generator declares each pin once: the pr-gate caller expands the declaration (its bytes are unchanged), and the generated contract test passes `EXPECTED_RELEASE_CHECKOUT` and `EXPECTED_RELEASE_SETUP_NODE` to its release-shape check, whose approved pre-credential step digest now records which declared pin a step uses rather than the pin's value, so an automated bump stays green.

_Date: 2026-10-15; issue #1812_

## Enroll verjson-editor in the reviewed authorization-arm cohort

The reviewed authorization-arm cohort adds `Verjson/verjson-editor`, which gained `verjson-core-checks=enforced` on 2026-10-15 and so is already armed by the legacy selector. The scheduled authorization-arm audit had stopped at the cohort mismatch; it now reaches the adopter checks again (#1803, part of #1401, ADR 0206).

_Date: 2026-10-15; issue #1803_

## Accept the withdrawn organization review key in the authorization-arm audit

The authorization-arm audit accepts a withdrawn organization copy of `AI_REVIEW_APP_PRIVATE_KEY` as the conforming custody state and then requires every armed repository to hold the key in its `ai-review-app` environment, reporting a missing key separately from an unreadable one. An organization 404 the same token cannot corroborate from a complete secret listing, and every other read failure, still fails closed (#1799, ADR 0166).

_Date: 2026-10-15; issue #1799_

## Terminal promotion and CLI projects admission compare repository identities in any ASCII case

`ai-privileged-merge.yml`'s "Authorize terminal merge from trusted metadata" step compared `job.workflow_repository` to the literal `Verjson/.github` byte for byte. Since the 2026-10-02 organization rename, a promotion that reaches the workflow through `ai-promotion-retry.yml`'s local `./` call reports `verJSON/.github`, so every retry failed with `promotion is not executing from the canonical workflow repository` (Verjson/AiB run 38790031475, PR #341 head `51e5add9`) and a pull request whose CI outlasted the direct privileged merge never promoted. The step now compares the repository as an ASCII-case-insensitive GitHub identity, as ADR 0213 records: `jq`'s `ascii_downcase` leaves non-ASCII untouched, so another owner, another repository, and a Unicode lookalike are still refused.

_Date: 2026-10-15; issue #1794_

## Accept GitHub App client IDs in the changelog fleet report

The changelog contract fleet report now validates `app_client_id` as a GitHub App client ID (`^Iv[A-Za-z0-9.]{8,126}$`, the merge gate's pattern) and refuses a numeric legacy App ID with a message naming the client-ID format. It previously required a numeric value, which is the App ID, so every real client ID passed by `verjson-agents` was refused and the scheduled report has produced no inventory since 2026-09-28 (verjson-agents#408).

_Date: 2026-10-15; issue #1792_

## Compare organization and repository identities case-insensitively in ruleset and secret audits

The package type-surface, Authn, and CLI projects ruleset audits and the organization secret-scope audit now compare GitHub organization and `owner/name` identities case-insensitively through one shared helper, `scripts/github_identity.py`. A different owner or repository, or a non-ASCII spelling that would case-fold onto a reviewed name, still fails closed. The nightly package type-surface audit passes against the live organization again.

_Date: 2026-10-15; issue #1779_

## Mint the Renovate compatibility token for the renamed runner repository

The Renovate compatibility reconciliation requested its App token for `verjson-github-runner`, which was renamed to `verjson-git-runners`. GitHub refuses a token request that names any repository the installation cannot see, so every scheduled run failed before reconciling anything. The token now names the current repository; its scope and read-only permissions are unchanged (#1778).

_Date: 2026-10-15; issue #1778_

## Record a provably absent generated caller instead of aborting capability-floor observation

Capability-floor discovery now lists each consumer's `.github/workflows` directory at the resolved source commit. An allowlisted caller missing from that successful listing becomes an `allowlisted-caller-absent` finding that names the repository as non-conforming. The finding appears in a new **Absent allowlisted callers** section of the run's step summary, and discovery continues. It also matches the canonical `Verjson` owner case-insensitively, so callers generated under the `verJSON` display case are accepted. A 404 is never read as absence, and every API, access, or response failure still fails closed.

_Date: 2026-10-15; issue #1777_

## CLI projects ruleset rollout records the live previous workflow revision

Org ruleset 21830367 (`cli-projects-package-surface-required-workflow`) was rotated on 2026-09-30 to workflow revision `ae70e4ef` (#1666). The rollout record in `config/cli-projects-package-surface-ruleset.json` and the matching check in `scripts/cli-projects-package-surface-ruleset.py` still named `ebe574b6`. As a result, the rotation tool refused every dry run with "canonical organization ruleset differs from reviewed active and disabled images". Both now record `ae70e4ef` as the previous active revision, so the tool can rotate the ruleset to a canonical revision that includes the #1823 fix (`64be2682`). That revision is this change's merge commit or later, because the tool only accepts a revision whose bytes match the local contract. That fix restores admission for CLI projects pull requests after the organization's display-case rename. The human gate and acknowledgement token are unchanged.

_Date: 2026-10-15; issue #1423_

## Secret policy keeps the AI review provider keys at visibility all and targets JEV_API_KEY at the quality-review canary

`config/org-actions-secret-policy.json` now records `DEEPSEEK_API_KEY` and `OPENAI_API_KEY` at visibility `all`, matching the live secrets. `ai-review-merge.yml` reads both in each caller repository's context, and the owner decided on 2026-10-15 that any repository may select either provider through `AI_REVIEW_PRIMARY_PROVIDER` without a per-repository grant. The earlier `selected: [Verjson/.github]` target would have left both empty in every other repository's review. `JEV_API_KEY` is now manifested with target `selected: [Verjson/.github]`, the #1820 quality-review canary. It has no Actions consumer today, so the audit reports the live `all` copy until the owner narrows it.

_Date: 2026-10-15; issue #1385_

## The generated-caller Renovate fence covers every release caller name

`renovate/generated-callers.json` now also fences `.github/workflows/release-*.yml`, so Renovate no longer edits an adopter's extra generated release caller. verjson-cli's `release-cli-schema.yml` was the case that slipped through. Renovate cannot match a file by its generated header: `matchJsonata` sees only the dependency, and `matchFileNames` sees only the path. The fence therefore stays a glob, kept inside the subset that Verjson/verjson-agents' resolution model understands. This repository's hand-written `release-app-canary.yml`, `release-dispatch.yml`, and `release-propose.yml` share the prefix. `renovate.json` reopens only their step actions, in a first rule, so the grouped digest PR keeps moving them. `scripts/ci-gate/renovate-generated-callers.test.py` now reads the preset as globs and places a release caller at each recorded adopter name. It also requires that every fenced hand-written workflow here that Renovate reads is reopened, and that every reopen is narrow, first, live, and free of bare-SHA pins. ADR 0232 records the amendment, the organization survey, and the remaining `publish.yml`-style gap. Part of [Verjson/.github#1812](https://github.com/Verjson/.github/issues/1812).

_Date: 2026-10-15; id:ccc5c6d2_

## Restore native rebuilds and offline reinstall rehearsals in Node CI sandboxes

Two adopter paths broke once #1729 moved Node CI's credentialless steps into Bubblewrap sandboxes. Native addons such as `better-sqlite3-multiple-ciphers` could not build in the network-denied lifecycle rebuild because node-gyp tried to download Node headers. The rebuild now points node-gyp at the selected toolchain's own `include/node` headers, and network access stays denied. Consumer scripts that rehearse `npm ci --offline` against the cache promised by `secretless-runtime-public-cache` found that cache empty. The canonical consumer plan now gives each script a disposable copy of the verified runtime cache, as the protected workflow already did. Network policy and credential isolation are unchanged. See ADR 0086.

_Date: 2026-10-14; issue #1798_

## Refuse inventory pages whose collection is not an array

`scripts/assert-mergeable-head.sh` now checks that every page from the check-run, commit-status, and workflow-run endpoints is an object whose collection is an array. Any other page fails closed with exit `1` before the page counts are reconciled. Previously the walk counted a collection with `length`, which also accepts a string, an object, or a number. A single page whose wrongly typed collection matched `total_count`, or a null collection on any page, was therefore read as an empty or forged inventory, and the admission gate printed `true`.

_Date: 2026-10-14; issue #1786_

## Keep admission-gate payloads out of jq's argument list

`scripts/assert-mergeable-head.sh` no longer passes API payloads to `jq` as arguments. Responses and the values derived from them now go through a scratch directory that the script's single EXIT trap removes, and `jq` reads them with `--slurpfile` or on stdin. Run objects are kept whole: once every payload travels by file, a field allowlist would buy nothing and would read any later-used field as `null`. On verJSON/AiB#347, eleven runs exceeded Linux's 128 KiB per-argument limit, so the gate exited `1` with `Argument list too long`. With this change it evaluates the head and prints `true`.

_Date: 2026-10-14; issue #1784_

## Let Node CI sandboxes run alternatives-managed system tools

The credentialless Node CI sandboxes start from an empty root and bind `/usr`, but on Debian and Ubuntu `/usr/bin/awk` (also `cc`, `c++`, `which`, and other names) is a symlink into `/etc/alternatives`. Since #1729 moved consumer scripts, lifecycle rebuilds, and compatibility lanes into those sandboxes, these names dangled and scripts calling `awk` failed with `command not found`. The canonical and protected Node CI workflows now bind `/etc/alternatives` read-only in every such sandbox. That directory holds only symlinks, so the sandboxes expose no new credentials or writable paths.

_Date: 2026-10-14; issue #1782_

## Enroll AiB in the deterministic CI and authorization-arm rulesets

The reviewed authorization-arm cohort adds AiB, so its repository properties can move to `verjson-stack=node`, `verjson-core-checks=enforced` and `changelog-contract=adopted`. AiB's `main` previously declared no required status checks, which ADR 0091 recorded as a coverage gap and which made the admin-merge assertion refuse every AiB pull request. ADR 0091 is amended with the enrollment.

_Date: 2026-10-14; issue #1781_

## Accept the withdrawn organization merge key in privileged merge conformance

The scheduled privileged merge fleet conformance audit accepts a withdrawn organization copy of `MERGE_APP_PRIVATE_KEY` as the conforming custody state and then requires each privileged merge consumer to hold the key in its own `merge-app` environment; that environment's main-only admission stays the job of `app-key-environment.yml`. Any other read failure, including an organization inventory that cannot corroborate the absence, still fails closed (#1776, ADR 0166).

_Date: 2026-10-14; issue #1776_

## Restore the reviewed authorization-arm cohort after the organization rename

The authorization-arm audit now compares repository identities case-insensitively and names the unreviewed and absent repositories when the cohort drifts. The reviewed migration set adds verjson-document-contracts, and the accepted verjson-agents finding is re-recorded under the renamed `verJSON` display case.

_Date: 2026-10-14; issue #1775_

## Allow versioned GitHub-hosted runner labels

Permit periods in explicit runner labels so generated changelog PR gates can select versioned Ubuntu runners.

_Date: 2026-10-14; issue #1770_

## Restore default SIGINT and SIGQUIT for actions-ci rows

The actions-ci group runner now execs every manifest row through `env --default-signal=INT,QUIT`, so a row can trap SIGINT and SIGQUIT even though the runner starts it asynchronously and a non-interactive bash hands asynchronous commands those signals ignored. Budgeted pull-request runs got the defaults back from `timeout`; unbudgeted push runs on `main` did not, which failed `runner-canary-contract.test.py` after #1746 merged. The runner refuses to start without `env --default-signal` (GNU coreutils 8.31 or later). See #1767 and ADR 0225.

_Date: 2026-10-14; issue #1767_

## Bound the authorization arm's wait for a runner

The authorization arm (`gate-rearm.yml`) now runs a `runner-acquisition-watchdog` job with no environment or repository secret. The job runs, and its write-scoped token is issued, only when the untrusted lane resolves to the ephemeral GitHub-hosted runner. When any arm job stays queued without a runner for 15 minutes, the watchdog names the job in a failed check and cancels the run, which releases the pull request's arm concurrency group. It force-cancels only if that job survives the cancellation. Before this change, a job GitHub accepted but never dispatched held the required arm check, and every later arm for the pull request, pending indefinitely (verjson-payments run 37352915856).

_Date: 2026-10-14; issue #1763_

## Refuse admission while a required context is only skipped or a workflow run is unfinished

`scripts/assert-mergeable-head.sh` no longer admits a head in the window after a draft pull request is marked ready: Gate A now requires a SUCCESS for every required context (a SKIPPED or NEUTRAL report alone is not evidence the work ran), and treats any workflow run on the head whose status is not `completed` as pending (ADR 0229). The passing conclusion set is unchanged; a draft-time SKIPPED plus a ready-time SUCCESS (#1753's documentation-only shape) still passes.

_Date: 2026-10-14; issue #1755_

## Let a review caller bootstrap move canonical reusable CI pins

The authorization arm's stale review caller bootstrap now also admits a consumer's canonical `node-ci.yml` and `actionlint.yml` caller pins moving to the bootstrap's contract revision, together with their contract-test mirrors, when nothing else in those workflow files changes ([ADR 0231](docs/decisions/0231-bootstrap-moves-reusable-ci-pins/README.md), extending ADR 0227).

_Date: 2026-10-14; id:20261014T185454Z; refs #1696_

## Sync the active issues index after the 2026-10-14 PM run

Remove the closed #1754 entry from the active issues index and add #1763, which stays open until the authorization ruleset is rotated and a live cancel receipt exists.

_Date: 2026-10-14; id:20261014T090030Z_

## Repin this repository's release caller and repair its verification hook

This repository's own release caller is regenerated at contract `21eb48e713bd8304951fb7b26a900d08359af900`, so its inline Python interpreters now start isolated (#1752), its publish job grants `actions: read` (#1748), and its publish job now runs on GitHub-hosted `ubuntu-24.04` instead of the self-hosted pool, because `node-release.yml` has hard-coded that runner since #1730. `scripts/release-verify.sh` again finds the generated header; it had kept the retired `Verjson/.github` spelling, so every self-release since #1702 would have failed at `verify`. It now matches the owner without regard to case, so a future casing change cannot break it the same way, while the byte-for-byte comparison with the pinned generator still rejects any header the generator did not emit.

_Date: 2026-10-13; issue #1758_

## Admit heads whose cancelled checks a later run of the same workflow superseded

`scripts/assert-mergeable-head.sh` no longer refuses a drafted-then-readied green pull request because the draft-time run was cancelled by concurrency: Gates A and C set aside a cancelled check run when its own workflow run was cancelled and the newest later run of the same workflow, under the same event and on the same head, succeeded and reports that check as SUCCESS or not at all (ADR 0226). The passing conclusion set is unchanged, and every other cancellation, failure, or pending check still blocks.

_Date: 2026-10-13; issue #1754_

## Sync the active issues index with tracker state

Remove six closed issues (#1663, #1665, #1669, #1675, #1682, #1689) from the active issues index and add #1752, which generalizes the isolated inline Python interpreter contract to every workflow.

_Date: 2026-10-13; issue #1752_

## Start every workflow's inline Python interpreters isolated

Every workflow, and every workflow the generators emit for adopters, now starts its inline Python interpreters with `-I`, so helpers resolve only the modules they name explicitly. Scripts that import a sibling add their own directory, the hosted-selector policy takes its pinned parser directory as an argument, and the release version guard requests UTF-8 on the command line.

_Date: 2026-10-13; id:311176181d_

## Bootstrap stale default-branch AI review callers through the authorization arm

The organization authorization arm now completes the exact-head `AI review authorization` check itself for the one pull request a stale review caller cannot authorize: the byte-exact generated `ai-review-merge.yml` update from a revision that predates the `verJSON` casing repair (`65529002`) to a canonical `main` commit at or after the minimum safe review revision (`12098a0e`, the #1650 exact-head finalizer fix). Every other change, and every failed or unavailable proof, keeps the ordinary dispatch.

_Date: 2026-10-13; id:20261013T160000Z; refs #1696_

## ci(node): separate npm config files and isolate inline Python in secretless CI

The secretless lifecycle rebuild gives npm distinct, empty user and global config files, so npm 11 no longer rejects one `/dev/null` path loaded for both roles. The sandbox bootstrap closes the host descriptors Bubblewrap leaves open before it creates `/tmp/npm-config/{user,global}.npmrc` (directory 0700, files 0600) on the sandbox's fresh `/tmp`, sets both `NPM_CONFIG_*` and `npm_config_*` spellings, and leaves the umask lifecycle scripts inherit unchanged. Every inline Python interpreter in `node-ci.yml` and the generated `node-ci-protected.yml` now runs isolated from the checkout with `-I`, enforced by a registered workflow contract with an empty exception list. (#1749; ADR 0086)

_Date: 2026-10-10; issue #1749_

## Grant actions read to the generated Node release publish job

The `release-node` caller now grants its `publish` job `actions: read`, which `node-release.yml`'s `release` job has declared since #1730. Without it the dispatch ended as a zero-job `startup_failure`, because a called workflow cannot exceed its caller's grant. The release-caller contract test now compares the grants of every generated mode that calls a reusable workflow with the permissions that workflow's jobs declare, recursing into nested reusable workflows with the calling job's grant as the cap and reporting cycles. It fails a calling job that declares no permissions instead of assuming write-all, reports a call it cannot resolve to this repository's workflows, classifies every mode the generator dispatches so a new one cannot skip the comparison, and proves by mutation that it rejects a caller that withholds `actions: read` and a nested callee that over-asks. Adopters regenerate their release caller at a contract SHA containing this fix.

_Date: 2026-10-10; issue #1747_

## test: pin the caller-contract shard case count

The caller-contract shard test now fails when its parsed assertion count changes unexpectedly.

_Date: 2026-10-09; issue #1744_

## Defer heavy pull request CI until review is ready

Draft pull requests continue to receive fast lint, type-surface, and admission
feedback, while long test suites and container candidate builds wait until the
author marks the pull request ready for review. Ready and converted-to-draft
events preserve the heavy checks' lifecycle and cancellation behavior.

_Date: 2026-10-09; issue #1739_

## Bound actions-ci command runtimes

Actions CI runs independent manifest commands concurrently, up to the runner's CPU quota rather than its raw `nproc`, with a 60-second default pull-request budget on every group-runner invocation, including documentation contracts. Rows measured above 30 seconds on the general runners declare a pinned `@budget` of twice their slowest CI time, capped at 600 seconds, and each row's log line names its budget. Parsed workflows, generated callers, and per-file scans are reused so release-contract work can finish without dropping assertions. The required-checks audit, conformance suite, actions-ci manifest contract, and privileged-merge conformance rows run alone to avoid CPU contention at the command cap. The audit contract assertions run in two isolated partitions concurrently within their row; logs report effective parallelism and each command's elapsed time.

_Date: 2026-10-09; issue #1736_

## ci: keep passing contract fixtures out of the check annotations

actions-ci masks workflow commands printed by a passing contract, so a green shell-test group no longer shows errors and warnings from fixtures that are supposed to fail inside the test. `add-mask` and `stop-commands` keep the command name and drop the value, so the rewritten line cannot publish a mask token.

_Date: 2026-10-09; issue #1735_

## ci: skip the heavy actions-ci matrix for documentation diffs

Documentation-only diffs run the documentation contracts and skip the release-caller, merge-gate, and hosted compatibility jobs. Any other diff, including an empty file list, still runs the heavy matrix, and the required `shell-tests` check fails if those two disagree.

_Date: 2026-10-09; issue #1734_

## ci: shard the changelog-release actions-ci group

The changelog-release actions-ci group is four matrix cells. The caller-contract suite is partitioned across them by case, and each case still runs the whole generated contract. That group was the check that stayed in progress for about 25 minutes after a package release had already finished.

_Date: 2026-10-09; issue #1733_

## Bind candidate receipts to observed provenance evidence

Candidate manifest v4 binds GAR index provenance and per-platform SBOM referrer records to registry readback, and validates each destination timestamp independently. Historical v2 and v3 candidates remain readable; v3 rejects v4-only fields, so candidates must be rebuilt before promotion. Promotion preserves the candidate manifest digest and evidence records, retains the exact signed candidate archive on the GitHub Release, and verifies that archive on resume.

_Date: 2026-10-09; issue #1726_

## Separate npm config files in generated release verification

Generated Node release callers now give npm's user and global configuration namespaces distinct, empty files under a private temporary home. The verification contract test runs real npm through the emitted verification command in release-node, release-artifact, and release-snapshot modes, proving a package build runs without loading `/dev/null` twice. (#1725; ADR 0220)

_Date: 2026-10-09; issue #1725_

## Isolate Node release registry credentials from lifecycle scripts

The canonical Node release workflow now confines credentialed acquisition, lifecycle scripts, metadata preparation, version stamping, build, and packing to a read-only preparation job. A fresh GitHub-hosted publisher validates the transferred archives against package identities and versions read from the immutable tag, checks archive digests and paths, then publishes with scripts disabled. The publisher and retention jobs cannot reuse a self-hosted preparation runner. Contract tests cover credential boundaries, artifact tampering, real npm pack output, and npm package-map keys matching packed metadata. The publisher checks the exact prepared artifact ID and its 2 GiB size before download, including failed-job reruns; upload compression is disabled for already-compressed tarballs so that limit bounds extracted data. The npm cache path is configured before setup-node so the bounded cache check and cleanup apply to the cache npm actually populates. Persistent setup-node cache restore/save is disabled when private dependency credentials are supplied, preventing private tarballs from entering a cross-run cache; a temporary versioned marker in the caller workspace invalidates old cache keys and is removed immediately after setup-node. The token-bearing npm install temporarily quarantines the tag-controlled root .npmrc and restores it on success or failure, so registry and auth directives from a release tag cannot redirect NODE_AUTH_TOKEN. Archive validation caps aggregate bytes and rejects oversized PAX and GNU metadata before parsing, including a 4 MiB aggregate limit for accumulated metadata. GNU sparse archives are rejected before tarfile can accumulate extent lists, with regression coverage for GNU and all three PAX sparse formats. Tar member names are normalized by entry type before duplicate detection, and file/directory ancestor conflicts are rejected regardless of archive order.

_Date: 2026-10-09; issue #1724_

## Validate pinned OS runner lanes before release snapshots

Generated release callers now reject trusted macOS and Windows lane values that
use the wrong OS family, rolling `latest` segments, or padded labels before the
snapshot job can run. Contract tests execute the exact generated preflight for
valid, invalid, empty, and malformed selectors while preserving the supported
array shape and acquisition/build matrix pairing.

_Date: 2026-10-09; issue #1723_

## Harden secretless rebuild environment controls

The reusable Node CI workflow allows only `ONNXRUNTIME_NODE_INSTALL=skip` when
`onnxruntime-node` is explicitly approved for credentialless lifecycle rebuild.
On GitHub-hosted runners, the workflow provisions and verifies its
bubblewrap/AppArmor boundary before the rebuild; other runner types fail closed.
It starts from a temporary filesystem root, isolates network access, and mounts
only system tools, the selected Node toolchain, required configuration, the
checkout, and (for pnpm) its Corepack cache. Directory descriptors hold the
checkout and tool sources through Bubblewrap setup, then a trusted bootstrap closes
all inherited descriptors before any package-manager lifecycle code runs. The checkout stays
read-only and only `node_modules` is writable. The package manager gets no
credentials, raw caller input, or GitHub command-file paths. Secretless pnpm
installs ignore repository pnpmfile hooks, and the rebuild mounts only the
runner's canonical Corepack cache. Protected identity checks run before
untrusted scripts. Consumer scripts cannot access runner command-file paths, and
the compatibility validator uses `/usr/bin/python3` so caller path additions
cannot shadow its checks. GitHub-hosted non-Linux runners fail with an explicit platform error.
Tests cover npm and pnpm, host process scans, and writes to known host
command-file paths. The explicit hosted Actions CI lane runs the inherited-descriptor write probe and consumer sandbox harness against the verified Bubblewrap binary; persistent fastlane groups keep the deterministic command stubs.

_Date: 2026-10-09; id:20261009T011500Z_

## Reduce OCI candidate publisher peak disk use

OCI candidate builds can opt into safe cleanup of unused preloaded images and retain disk, Docker, and BuildKit usage diagnostics. Private Node dependency archives now restore through authenticated streaming extraction, reducing peak disk use while preserving publication and provenance behavior.

_Date: 2026-10-08; issue #1721_

## Track OCI candidate disk work

Track `.github#1721` in the Active Issues index and record TTP #1352 as its downstream candidate-publisher consumer.

_Date: 2026-10-08; id:a1c071ebc257_

## Prune closed candidate-disk issue from active index

Remove the closed OCI candidate disk issue from the active issues index.

_Date: 2026-10-08; id:6d7b48a_

## Update the generated artifact download contract

The canonical runner deployment review producer pins all three artifact downloads to `actions/download-artifact` v8.0.2. Its generated consumer contract continues to pin the complete producer workflow digest, so consumers must regenerate from a reviewed immutable contract commit.

_Date: 2026-10-07; issue #1719_

## Harden generated release guards

The changelog-release CI job has a 30-minute bound to cover the expanded release-caller security contract and its remaining grouped checks.

_Date: 2026-10-07; issue #1717_

## Explain the isolated release tag lookup environment

Generated release callers now explain why the restart-safe tag lookup clears runner-controlled shell and Git settings and passes its authentication header only to an isolated Git child.

_Date: 2026-10-07; issue #1715_

## Confine release credentials to dependency acquisition

Generated Node release workflows now authenticate only when acquiring private dependencies. npm install runs with lifecycle scripts disabled while NODE_AUTH_TOKEN is present; scripts run later after the token is cleared. Package preparation, version stamping, and release verification also run without it. Restart-safe tag lookup uses process-scoped Git authentication, and checkouts do not persist credentials.

_Date: 2026-10-07; issue #1712_

## Correct generated artifact release caller validation

Generated artifact release callers now use the reviewed matrix runner selector,
accept the canonical organization spelling in authentic GitHub Packages download
URLs while still requiring the exact package name, and emit valid JavaScript for
private lock validation. The generator contract exercises the emitted selector,
Node syntax, matching URL, and wrong-package rejection.

_Date: 2026-10-06; issue #1711_

## Emit the canonical organization spelling in generated callers

The changelog caller generator now emits `verJSON` for organization-owned repositories, workflow references, renderer URLs, release runner selection, and CODEOWNERS. The npm scope and registry URLs remain unchanged. Contract tests cover the renamed output and the generated CODEOWNERS source.

_Date: 2026-10-06; issue #1698_

## Accept registry tarball scope casing

Accept GitHub Packages tarball URLs whose ASCII scope or package casing differs from the exact approved lowercase lock identity. Keep the registry-issued URL and integrity intact while preserving approval, URL structure, npm installation-path identity, and digest checks.

_Date: 2026-10-06; issue #1694_

## Require an explicit version in generated release dispatches

Generated package, artifact, and snapshot release callers now require an exact version at dispatch and reject blank or Unicode-whitespace-only input before release work, including on C-locale runners. The verified release plan continues to validate that version against the selected changelog fragments. ADR 0218 records the reversal of optional direct-dispatch versions.

_Date: 2026-10-06; issue #1688_

## Align terminal merge test indentation

Align the terminal merge assertion fixture with its surrounding entries so the exact-head and draft checks remain easy to review.

_Date: 2026-10-06; issue #1687_

## Explain npm CLI fixture path

Explain why the node launcher fixture accepts only the nested npm CLI path created by the test, so future tool-layout updates keep both sides aligned.

_Date: 2026-10-06; issue #1672_

## Use a set for npm CLI candidates

Use a set to deduplicate resolved npm CLI paths in the generated protected node CI workflow. Existing tests continue to require alias deduplication and reject distinct ambiguous candidates.

_Date: 2026-10-06; issue #1671_

## Restore container deployment dry-run plans

Collect run-bound host evidence and create a non-deployable preview plan during container deployment dry runs. Mutating deployments still require reviewed authorization tied to the exact workflow run.

_Date: 2026-10-06; issue #1654_

## Bind authorization finalizer to the expected pull request head

Reject an authorization check whose identity names a different pull request head before the finalizer can update it. The finalizer now uses the same exact-head condition as the other authorization paths.

_Date: 2026-10-06; issue #1650_

## Ignore source templates with variable contract references

Contract version verification now ignores shell variable placeholders in source files that assert or generate `uses:` references. It still rejects variable references in Actions YAML and still compares literal source-file pins with the declared release commit.

_Date: 2026-10-06; issue #1645_

## Retire moving major tags

Remove the failing `tag-major` release workflow. Published contract releases use immutable version tags and commit SHA pins; existing major aliases remain static for legacy callers until they migrate. Keep byte-pinned workflow sources unchanged while updating caller guidance and the release-documentation contract test.

_Date: 2026-10-06; issue #1644_

## Clarify privileged lane status capture

Document why the privileged-merge lane check saves the jq assignment status immediately, before a subsequent command could overwrite it.

_Date: 2026-10-06; issue #1622_

## Regenerate the release caller at the explicit-version contract

The organization's own generated release caller now requires an explicit version and rejects Unicode-whitespace-only input before release work. It pins the immutable contract from #1688 and uses the canonical `verJSON` organization spelling from #1698. The runner routing policy recognizes the generated canonical selector and still rejects unreviewed hosted fallbacks; the contract tools package test checks the regenerated header.

_Date: 2026-10-06; id:d1bf04c7f7_

## AI review caller bootstrap blocker

Index the fail-closed migration gap for generated AI review callers pinned behind a changed organization identity. The existing default-branch caller cannot verify the current arm receipt and therefore cannot authorize its own pin update; the migration and exact-head proof remain tracked in #1696.

_Date: 2026-10-05; issue #1696_

## One main environment contract

Index the proposed canonical deployment contract and the current generator gap. The proposed dev path would deploy the current `main` HEAD through a validated, lightweight path targeting p95 under five minutes; its image may have a different digest from the later release candidate. Nonprod and prod would promote the same fully verified pinned digest set with separate environment controls. CLI adoption depends on this canonical contract.

_Date: 2026-10-05; issue #1692_

## Add keyless Cosign provenance for container candidates

Replaces plan-incompatible GitHub Artifact Attestations in the canonical container publisher with keyless Cosign evidence, independently verified source/caller/publisher/contract claims, and a fail-closed GAR evidence read-back gate. Derived image read-back checks the pinned base digest against BuildKit dependencies; the mirror verifies index provenance and each platform's SPDX referrer at their actual OCI subjects, with the registry regressions enforced in CI. Pinned publisher and release helpers carry their required imports, and the reusable canary pins their current digests. Candidate eligibility remains disabled until genuine private reusable-workflow identity and GAR preservation evidence pass.

_Date: 2026-10-05; issue #1689_

## Validate REST draft metadata at the terminal merge boundary

Read the REST pull request response's `draft` field instead of the CLI and
GraphQL `isDraft` field, restoring autonomous merges of verified non-draft PRs.
Use the REST shape in the promotion fixture and verify that draft, missing,
null, and malformed draft values still fail closed before any merge attempt.
The correction restores ADR 0120's terminal authorization boundary.

_Date: 2026-10-04; issue #1685_

## Reject mixed container manifest version fields

Candidate schema v2 records now reject v3 publication timestamps and destination receipts.
V2 records remain readable when they match their original shape; v3 remains required for
promotion.

_Date: 2026-10-03; id:20261003T013336Z; refs #1667_

## Preserve versioned container manifest schema validation

Published candidate and release schemas now validate historical v2 records and current
v3 manifests. V3 requires publication timestamps and verified registry destination
receipts; release promotion continues to reject v2 candidates that lack this evidence.

_Date: 2026-10-03; id:20261003T012543Z; refs #1667_

## Encrypt private dependency cache transfers in protected builds

The generated protected Node CI workflow now transfers private dependency cache contents between jobs as authenticated ciphertext. It passes the decryption key only through the current workflow run and removes the plaintext archive before saving the run-scoped cache. A compatibility contract checks the generated workflow against the canonical secretless workflow and verifies that the encrypted archive is the cached payload.

_Date: 2026-10-03; id:20261003T003813Z; refs #1679_

## Normalize repository identity casing in canonical CI gates

Normalize GitHub repository identity casing in generated Node CI boundaries, hosted-selector policy selection, required-workflow receipts, API-backed run and receipt provenance checks in gate re-arm, post-merge authorization, dependency supersession, and zero-provider recovery, private GitHub Packages tarball provenance, plus the reusable container deployment-review producer. These checks retain ASCII-only repository syntax and their repository ID, run identity, workflow path, immutable SHA, protected ref, and publisher-policy bindings.

_Date: 2026-10-02; issue #1682_

## Keep private package contents out of pull-request builds

Container-candidate pull requests no longer acquire or mount private Node packages; when `privateNodePackages` is configured, the workflow clearly skips PR-controlled Docker builds and leaves private-package acquisition to trusted default-branch publication.

_Date: 2026-10-02; issue #1679_

## Accept integrity-pinned npm bundled dependencies

The container-candidate package planner now skips separately downloading npm lock entries marked `inBundle: true`, relying on the integrity-pinned containing tarball while continuing to reject ordinary entries without exact registry URLs and integrity.

_Date: 2026-10-02; issue #1678_

## Guard direct review dispatch provenance

Resolve the direct-dispatch verifier from GitHub's immutable `github.workflow_sha`; admit direct runs only from protected default `main` with the `ai-review-app` environment, before verifier checkout or execution. Reject alternate environments at preflight, App-key, and completion boundaries for every dispatch, including reusable calls carrying `contract_ref`; the exact guard test rejects disjunctive bypasses. Keep the trusted dispatcher on the repository default branch. The observed admission failure was an arm-run provenance and receipt identity mismatch, so the empty SHA has not been established as its cause.

_Date: 2026-10-02; id:a14c8e29_

## Guard candidate runner routing

For #1679, disable caller-selected runners on pull requests and allow that exact guarded selector only on the three jobs in the canonical candidate workflow. Add a negative policy fixture for the same expression in an unrelated workflow. Align the contract fixture with GitHub's canonical repository identity so PR validation exercises the same identity comparison as Actions.

_Date: 2026-10-02; id:20261002T190012Z_

## Surface GitLab migration status

The organization profile now links to the shared Nuremberg infrastructure guide and CI migration tracker. The guide records a read-only Kubernetes deployment inventory at 2026-10-02 23:13 UTC and limits that evidence to workload readiness; runner job/DNS acceptance and multi-platform OCI publication remain unverified under #30. It separates the generated Nexus bootstrap configuration from live validation still tracked in #1264.

_Date: 2026-10-02; id:20261002T175445Z_

## Verify OCI candidate registry destinations

Trusted container publication can mirror complete multi-platform OCI candidates
from canonical GHCR to a reviewed GAR destination. Candidate manifests record
read-back digests and per-destination expiry; promotion fails closed after the
canonical candidate expires. Retention values must be integers; booleans are
rejected. Mirror receipts carry the validated variant for workflow selection;
it is removed before the strict release-manifest schema is applied.

_Date: 2026-10-02; id:20261002T143132Z; refs #1667_

## Route private candidate skips through approved runner lanes

The private-package pull-request skip notice now follows the repository's configured untrusted runner lane. Its contract test also asserts that the read-only workflow has no private dependency acquisition job.

_Date: 2026-10-02; id:202610021317_

## Clarify fixed AI review environment contract

Clarify that ADR 0166 assigns generated gate callers to the `ai-review-app` role environment. The reusable input is part of the workflow-call contract; the generated-caller contract test pins this caller's value, while runtime policy validates the supplied environment's `main` branch policy. See [ADR 0166](../docs/decisions/0166-environment-only-app-private-keys/README.md) and [Verjson/.github#1675](https://github.com/Verjson/.github/issues/1675).

_Date: 2026-10-01; issue #1675_

## Standardize package type-surface release authorization

Package type-surface rulesets now have one organization-owned release-App policy with exact preimage/postimage auditing and scheduled conformance coverage.

_Date: 2026-10-01; issue #1673_

## Support the Node 26 npm CLI layout

Resolve npm's CLI from the validated Node toolcache and invoke it with the validated Node executable. Keep the tool prefix read-only and preserve the credentialless protected sandbox when Node 26 places npm's package under `lib/node_modules/npm` but its launcher still looks under `bin`.

_Date: 2026-10-01; issue #1669_

## Index configurable OCI candidate destinations

Adds the registry-neutral OCI candidate publisher proposal to the active issue index, linking the GHCR, GAR, and Sonatype Nexus backlog to the related Nexus engine work in [verjson-ci#30](https://github.com/Verjson/verjson-ci/issues/30). No publishing behavior changes in this documentation update.

_Date: 2026-10-01; issue #1667_

## Make the generated gate re-arm caller reusable

The generated gate re-arm caller now supports local workflow reuse and runs directly only for opened, reopened, and synchronize pull request events. Lifecycle-only events stay in the lifecycle caller, which invokes the local gate caller once; label-only re-arming remains in the label caller.

_Date: 2026-10-01; issue #1665_

## Pin the enrolled authz type-surface ruleset

Pin the live `authz-type-surface-required` ruleset ID in the organization-owned
release-authorization policy after its staged creation and postimage verification.
See [Verjson/.github#1673](https://github.com/Verjson/.github/issues/1673).

_Date: 2026-10-01; id:21b7a70f_

## Separate AI review re-arm events

The generated AI review label caller now runs only for `labeled`; lifecycle events remain on the gate re-arm caller, avoiding a duplicate privileged re-arm job. See [Verjson/.github#1663](https://github.com/Verjson/.github/issues/1663).

_Date: 2026-09-30; issue #1663_

## Normalize gen-adr-index exit status

The `gen-adr-index` package command now returns 1 for unexpected child exits while preserving exit 2 for invalid arguments.

_Date: 2026-09-30; issue #1660_

## Validate AI review lifecycle caller receipts

Lifecycle re-arm receipts now bind to the exact protected lifecycle workflow, permitted action, source run, repository, and actor; receipt verification and orphan recovery enforce the same provenance. See [Verjson/.github#1663](https://github.com/Verjson/.github/issues/1663).

_Date: 2026-09-30; id:9c31a7e2_

## Require the generated CLI Projects CI caller

Pin the CLI Projects required workflow to the current generated caller. PR admission accepts only byte-exact `ci.yml` output from the generator bound by the immutable required-workflow SHA. The prior-main digest remains in `rollout_legacy_consumer_workflow_sha256` only so the ruleset rotation verifier can validate the existing default-branch caller; it is not embedded in or accepted by the PR workflow. Keep the strict `admission` status check and bind rollout verification to the exact head and required-workflow run. Strict freshness refreshes ordinary stale heads; safety no longer depends on a base advance invalidating a cached legacy result. After `Verjson/verjson-cli-projects#142` lands and `main` matches the generator, remove the rollout-only digest. Resolves #1646; see ADR 0212.

_Date: 2026-09-29; issue #1646_

# v3.0.2

## Index the signed-provenance rollout follow-up

Track the separate signed merge-gate provenance rollout in the project issue index after #1339 closed its feasibility evidence work. ADR 0211 still requires a real `pull_request_target` capture before rollout.

_Date: 2026-09-28; issue #1655_

## Warm the pinned changelog engine for protected CI scripts

Protected Node CI now verifies and mounts the pinned changelog engine read-only before running its exact offline script plan, so release-contract checks can run without network access.

_Date: 2026-09-28; issue #1646_

## Publish contract developer tools

The next dispatched contract release will publish private `@verjson/contract` at the exact same version as its workflow contract, with `render-next` and `gen-adr-index` package commands. Existing releases are unchanged; publication begins with the next release after this change lands.

_Date: 2026-09-28; issue #1374_

## Recover an orphaned authorization check whose source run has a null completed_at

`gate-rearm.yml`'s `recover_orphaned_authorization` required the source arm run's
`completed_at` to be a valid timestamp string before it would treat that run as terminal.
GitHub can report `status: "completed"` with `completed_at: null` (an observed API
anomaly), which permanently blocked recovery of the affected check — discovered
re-arming `verjson-compliance-schema#23`'s canary, whose check-run `108560905178` had sat
`in_progress` since this morning's outage. The live-state check and the five-minute
eventual-consistency floor now fall back to `updated_at` when `completed_at` is exactly
`null`; every other binding is unchanged. ADR 0139 amended.

_Date: 2026-09-27; id:20260927T160037Z_

# v3.0.1

## Bind the authorization check by its stored page, not the arm-run details_url

Since ADR 0203 the arm creates `AI review authorization` with the Actions token, and GitHub replaces the `details_url` of an Actions-owned check with the check's own `/runs/<id>` page. Every predicate that bound the check to the arm run through that field failed, so every review dispatched after #1569 merged (2026-09-27 02:02 UTC; first failure 02:07 UTC) stopped at preflight with `authorization check is not receipt-bound`. `verify-arm-receipt.sh`, both re-verification steps in `ai-review-merge.yml`, orphan recovery and re-promotion in `gate-rearm.yml`, and `ai-promotion-retry.yml` now bind the check to its stored page and take the arm run from `external_id`; the receipt keeps the arm-run URL. Live only after ruleset 20722935 rotates to the merge commit and adopters repin through the next contract release (ADR 0203 amendment).

_Date: 2026-09-27; issue #1648_

## Repository notes record the tracked CHANGELOG snapshots and prune closed Active Issues

`CLAUDE.md` states that since v3.0.0 the repository tracks `CHANGELOG/<version>.md` and the release-written aggregate `CHANGELOG.md`, which no pull request edits; the Active Issues list drops the closed #1540, #1612, and #1617 and refreshes #1363 and #1451 with their 2026-09-27 verified state.

_Date: 2026-09-27; id:20260927T060000Z_

## Repin the cli-projects required workflow to the hosted sandbox-provisioning fix (#1423)

`cli-projects-package-surface-required.yml` calls `node-ci-protected.yml@4a08c756547ccaf8977eda692e14eff56fddaf15` (#1636) on both lanes, and `config/cli-projects-package-surface-ruleset.json` records the currently active image (`ebe574b`) as the previous workflow so ruleset 21830367 can be rotated to this commit. The consumer `ci.yml` is regenerated to the same commit before rotation because admission compares its bytes.

_Date: 2026-09-27; id:20260927T020500Z_

# v3.0.0

## Ruleset verify-run accepts the offset-form updated_at GitHub actually returns

`scripts/authn-type-surface-ruleset.py` and `scripts/cli-projects-package-surface-ruleset.py` parse any timezone-aware ISO 8601 timestamp and compare in UTC, so `verify-run` no longer exits with `ruleset updated_at is invalid` when the rulesets API returns `2026-09-26T20:16:20.896-04:00` instead of a `Z`-suffixed value. Naive, empty, and malformed values are still rejected; both suites gain the four-shape fixture.

_Date: 2026-09-27; issue #1630_

## Temporary required-workflow probe records the OIDC identity shape a private consumer presents

`oidc-claims-probe-1339.yml` is a temporary workflow injected into the private scratch repository `Verjson/demo-repository` by a temporary organization required-workflow ruleset. It requests the job's OIDC token and prints only the decoded claims (token, header segments, and signature are masked; `jti`, `exp`, `iat`, `nbf` are removed) plus the run metadata GitHub reports, so the exact identity shape a required workflow presents inside a private consumer can be recorded for ADR 0077's residual risk. It is deleted in the follow-up once the capture is recorded.

_Date: 2026-09-27; issue #1339_

## Verjson/.github cuts its own releases through a generated release-snapshot caller (#1374)

`.github/workflows/release.yml` is generated by `scripts/gen-changelog-caller.sh release-snapshot 89af2e69f7256bc0cd1af90a1a31f26027abd1ff` (the commit that taught snapshot callers to serve non-Node adopters) and `scripts/release-verify.sh` is the executable verification hook it runs in place of `npm test`, so a contract release is a `workflow_dispatch` that consumes `NEXT/` into an immutable `CHANGELOG/<version>.md` and tags it (ADR 0191). The generated header's regeneration note names adopter members this repository does not carry (`changelog.yml`, a generated `render-next.sh`, a generated contract test): the hub is the contract's source, not an adopter, and only the release caller and the verify hook are generated here.

_Date: 2026-09-27; id:20260927T031000Z_

## gate-rearm pins a ref-reachable validator and grants it the AI-review key

`gate-rearm.yml` calls `app-key-environment.yml@ebe23fa…` (a `main` commit; the previous `f56af66…` pin was a squashed branch head on no ref, and organization ruleset 20722935 rotated to it produced required-workflow runs with zero jobs) and passes the narrow named grant `AI_REVIEW_APP_PRIVATE_KEY` on that edge, because the validator's environment-bound job is key-bearing since e7db320 and the organization copy that masked the missing grant is withdrawn (#1385). ADR 0171 carries the dated amendment; rotate ruleset 20722935 to this commit.

_Date: 2026-09-27; id:20260927T025500Z_

## Verifier predicates for signed merge-gate provenance, derived from a captured private-consumer required-workflow token (#1339)

`scripts/merge-gate-provenance-claims.py` implements the exact predicates a signing rollout must enforce to bind a gate run to the canonical required workflow rather than the consumer's execution: issuer, organization id, `job_workflow_ref` on the canonical file at `refs/heads/main` **and** `job_workflow_sha` equal to the ruleset's stored commit, `workflow_ref`/`workflow_sha` identical to that pair (an injected required workflow, not a consumer-authored reusable call), `pull_request` event and `sub`, and a reviewed runner lane. `config/merge-gate-provenance-fixtures.json` carries the redacted genuine claim set captured from a real required-workflow run in the private scratch repository plus nine tampered variants; the registered test holds the genuine capture green and each variant red on its own predicate. The temporary probe workflow is deleted. ADR 0211 supersedes ADR 0077's residual risk as a specification; no signing is rolled out.

_Date: 2026-09-27; id:20260927T023000Z_

## release-snapshot callers no longer assume a Node project (#1374)

`gen-changelog-caller.sh release-snapshot` callers run `setup-node`, `npm ci`, and version stamping only when a root `package.json` exists, and a repository without one must verify through an executable `scripts/release-verify.sh` or the release fails closed before the snapshot (#1206). Root `package.json` is the contract for "this is a Node project" in snapshot mode; a snapshot adopter with packages only in subdirectories should regenerate as `release-node` or commit a verify hook. Existing Node snapshot adopters are unaffected; regenerate at this commit to pick up the guards. This is the first half of giving `Verjson/.github` its own generated release caller (ADR 0191): the caller itself is regenerated at this commit's merge SHA in the follow-up so its provenance header names a commit whose generator produced it.

_Date: 2026-09-27; id:20260927T020000Z_

## ADR 0206 records the authorization and privileged-merge path review and untangles the dependency chain (#1363)

ADR 0206 gains a dated amendment describing the five hops of the authorization path (property enrollment, `gate-rearm`, `ai-review-merge`, `ai-privileged-merge`, App custody) and adjudicating the tracker chain `#1363 ← #1401 ← #1500 ← verjson-cli#281`: only `#1401 → #1363` was real coupling, now bound to the specific prerequisite verjson-agents#417; the `#1500 → #1401` and `verjson-cli#281` edges were over-linking and are removed. The migration transaction is unchanged; its prerequisites are reordered so the privileged-merge canaries (#1500) proceed in parallel.

_Date: 2026-09-27; id:20260927T012500Z_

## The conditional Node floor lane is live as organization ruleset 24056850 (#1274)

Organization ruleset 24056850 `core-checks-node-floor` requires `ci-node22 / build-test` and `ci-node22 / eligibility` on the default branch of every repository carrying `verjson-stack=node`, `verjson-core-checks=enforced`, and `verjson-node-floor=node22`, exactly as ADR 0176 declared and `scripts/node-floor-ruleset.py render` emits; its bypass actors are mirrored from `core-checks-node` and reviewed through `config/org-ruleset-conformance-policy.json`. `verjson-object-storage` is enrolled and its hand-built repository ruleset 22590648 is retired. ADR 0176 gains the dated rollout amendment with the failing and non-opted-in control receipts.

_Date: 2026-09-27; id:20260927T012000Z_

## Remove the one-off orphaned-check completion workflow after its single run (#1540)

`oneoff-1540-complete-orphaned-checks.yml` ran once on `main` (run recorded on #1540) and terminalized the three `AI review authorization` check-runs App 4528902 left `in_progress` since 2026-09-22; all three now read back `completed/cancelled`, and a paginated org-wide query over every open pull request finds no in-progress or queued check-run from that App. The workflow is deleted so it cannot become a standing check-run writer.

_Date: 2026-09-27; id:20260927T011500Z_

## node-ci-protected provisions the hosted sandbox for every lane that runs the protected plan (#1423)

On GitHub-hosted runners `node-ci-protected.yml` now provisions the trusted compatibility sandbox whenever a lane will run the protected script plan or the compatibility lanes, not only when the caller declares a type surface or compatibility ranges. The protected script plan requires the bubblewrap boundary unconditionally, so callers without those inputs (verjson-cli-projects' package-surface lanes) failed closed with `verified bubblewrap namespace boundary is unavailable` the moment their required workflow was activated. Regenerated from `scripts/gen-node-ci-protected.py`; ADR 0209 carries a dated amendment.

_Date: 2026-09-27; id:20260927T010000Z_

## The generated .github/CODEOWNERS is a required member of the adopter contract (#1272)

`scripts/gen-changelog-caller.sh contract-test` now pins the digest of the canonical CODEOWNERS bytes (source: `config/codeowners/CODEOWNERS`, embedded in the generator so a pinned generator alone reproduces them), and the generated suite fails closed when `.github/CODEOWNERS` is absent, hand-edited, symlinked, or shadowed by a root or `docs/` `CODEOWNERS`. Adopters regenerating at this SHA must also run `scripts/gen-changelog-caller.sh codeowners "$PIN" > .github/CODEOWNERS`; the adoption command lists say so.

_Date: 2026-09-27; id:20260927T002500Z_

## Close the container-deployment generator's empty-config fail-open under jq 1.6

`scripts/gen-container-deployment.sh`'s `contract-test` mode generates a
deployment-config validator that relies on `jq -re` to reject a config
producing no violations output, with a comment explaining the intent:
"`-e` keeps an empty or input-less config a rejection: jq yields no result
at all for one." Under jq 1.6, that doesn't hold — `jq -re` against a truly
empty or whitespace-only file exits `0` regardless of `-e`, because the
filter never runs when there is no JSON value to run it against (`-e`
reports on the filter's *result*, not on whether it ran at all). That let an
empty or whitespace-only `container-deployment.json` silently pass, the
inversion the comment says `-e` was there to prevent.

_Date: 2026-09-26; issue #1617_

## Fix the release-snapshot publish job's VERSION reference to the unreachable verify-job step

`scripts/gen-changelog-caller.sh`'s `release-snapshot` mode generates a
`publish` job that runs in its own job context, separate from the `verify`
job that resolves the release version via its `release-version` step. The
publish job's "Verify the checked-out tag and immutable release note" step
read `VERSION` from `steps.release-version.outputs.version` — a step ID that
only exists inside `verify` — instead of `needs.verify.outputs.version`,
which every other reference in that same job (the checkout `ref`, and the
following publish step) already used correctly. `steps.release-version`
resolves to empty outside its own job, so the exact-tag check
(`test "$(git describe --tags --exact-match HEAD)" = "$VERSION"`) always
failed against an empty string, hard-failing every release cut with this
generator mode.

_Date: 2026-09-26; issue #1616_

## Stop gating independent-review eligibility on author_association, an org-membership signal the automation token cannot reliably resolve

`ai-privileged-merge.yml`'s independent-review revalidation required a candidate
review's `author_association` to be `OWNER`, `MEMBER`, or `COLLABORATOR`.
`author_association` reflects the *calling token's* visibility into organization
membership, not the reviewer's actual standing. The step authenticates with the
job's default `GITHUB_TOKEN`, which lacks `read:org` visibility into private
organization membership, so a real `Verjson` member with private membership
visibility resolved as something other than `MEMBER` from that token's
perspective — silently rejecting a correctly marked, exact-head, live-`maintain`
review with "an exact-head independent-review verdict is required before
autonomous merge" (`Verjson/verjson-ai` PR #695).

_Date: 2026-09-26; issue #1615_

## Close the privileged-lane admission gate's empty/whitespace-input fail-open under jq 1.6

`ai-privileged-merge.yml`'s `validate_privileged_lane` job — the
credentialless admission gate a later job's scheduling-time `if:`
independently rechecks before scheduling on self-hosted capacity (ADR 0117 /
ADR 0118) — fed `PRIVILEGED_LANE` to `jq -e` through a bash here-string
(`<<<"${PRIVILEGED_LANE:-}"`). A here-string always appends a trailing
newline, so an unset, empty, or whitespace-only input reached jq as a
stream with no JSON values. jq 1.6 treats that as "the filter never ran"
and exits `0` regardless of `-e`, unlike a real parse error or a
`null`/`false` result — so the step printed "privileged_lane validated:
exact match" and returned success for exactly the input shape this step
exists to catch first.

_Date: 2026-09-26; issue #1612_

## node-ci-protected admits the whole hosted tool cache tree, not only its root

`node-ci-protected.yml` no longer fails every GitHub-hosted protected type-surface run with `setup-node lexical PATH ancestry has unsafe ownership mode`. When the trusted tool root is the admitted `/opt/hostedtoolcache` convention (uid 0 or 1001, gid 0, mode 0777), the write-bit requirement is relaxed for every resolved path under it: PATH ancestry, the npm/node executables, and the mounted tool prefix tree. Owner allowlisting, symlink containment, special-file rejection, and the exact-root admission stay fail-closed, and every other tool root remains strict.

_Date: 2026-09-26; issue #1599_

## Spend the base ref as one percent-encoded path segment at every rules/branches/ reader

`scripts/assert-mergeable-head.sh` (Gate A) and `scripts/ci-gate/verify-arm-receipt.sh` now encode the base ref with `@uri` before spending it in `repos/<repo>/rules/branches/<ref>`, matching the four readers that already used the path form. A live probe in the sanctioned scratch repository showed `rules/branches/` resolves the same ruleset for `release%2F2026.09` and `release/2026.09`, so the path form is the strictly safer spelling; Gate A's verdict is unchanged for every current base ref because no Verjson ruleset targets a slash-bearing ref. The URI-encoding enumeration test drops its "deliberate and unresolved" allowlist entries, so the scan now pins the form at both sites.

_Date: 2026-09-26; issue #1470_

## renovate-changelog encodes the head branch as one path segment under git/ref/heads/

`scripts/renovate-changelog.py` now builds `repos/<repo>/git/ref/heads/<branch>` (and the matching `git/refs/heads/` update) with the branch percent-encoded in full (`renovate%2Fx`), the spelling `scripts/release-propose.py` already uses for the same endpoint and the shape ADR 0192's path rule requires. The renovate test fixtures pin the encoded form for both the read and the update.

_Date: 2026-09-26; issue #1458_

## One-off workflow terminalizes three orphaned authorization check-runs (#1540)

`oneoff-1540-complete-orphaned-checks.yml` is a dispatch-only operator workflow, bound to the main-only `ai-review-app` environment, that completes the three `AI review authorization` check-runs App 4528902 left `in_progress` on 2026-09-22 after their pull requests reached a terminal state (verjson-git-runners 106611362849, verjson-authn 106657731199, verjson-agents 106661948598). It refuses any check-run that is not exactly one of those three, still in progress, owned by that App, and at the recorded head; it writes `completed/cancelled` with an explanatory summary and reads the result back. The workflow is deleted in the follow-up once all three read back terminal.

_Date: 2026-09-26; id:20260927T001800Z_

## Repin the cli-projects required workflow to the hosted tool-cache tree fix (#1599)

`cli-projects-package-surface-required.yml` now calls `node-ci-protected.yml@3f8fb4bae1bdf0ced0b821e1b4277523f72a93b9` (merge of #1624) on both of its protected lanes, so the hosted package-surface check for `verjson-cli-projects` no longer inherits the descendant ownership failure #1599 reopened. The ruleset script's reviewed workflow image carries the same pin. Activation under #1423 still requires the consumer's `ci.yml` to be regenerated to this commit first, because admission compares the consumer's default-branch bytes with `CONSUMER_WORKFLOW_SHA256` and fails closed on a mismatch.

_Date: 2026-09-26; id:20260926T235900Z_

## Repin the authn required workflow to the hosted tool-cache tree fix (#1599)

`authn-type-surface-required.yml` now calls `node-ci-protected.yml@3f8fb4bae1bdf0ced0b821e1b4277523f72a93b9`, the merge of #1624, so the hosted protected type-surface lane for `verjson-authn` stops failing on `setup-node lexical PATH ancestry has unsafe ownership mode`. The ruleset script and its test carry the same pin.

_Date: 2026-09-26; id:20260926T234500Z_

## Generated container-deployment callers inherit the protected environment secret context (#1451)

`scripts/gen-container-deployment.sh` now emits `secrets: inherit` on each reusable edge it generates, and the generated contract test requires exactly that line while still rejecting any named secret, `secrets.` reference, `RUNNER_HOST_EVIDENCE_` name, or `environment:` binding in a caller. Adopters must regenerate the full caller set at this contract SHA; a caller without the inheritance now fails its own contract test.

_Date: 2026-09-26; id:20260926T233900Z_

## Bound package_retention.py's GitHub API calls with a socket timeout

`GitHubPackages._request` called `urllib.request.urlopen` with no timeout, so a
stalled connection to the GitHub API blocked the retention job indefinitely —
observed directly on a real `container-release.yml` run (`verjson-git-runners`
run 36209652761): the "Retain three numbered image releases" step produced
zero output for 30 minutes before the job's own external timeout cancelled
it, rather than the script itself failing fast with a diagnosis. No package
version was deleted before the cancellation (confirmed against live package
state), but the retention step never completed.

_Date: 2026-09-26; id:20260926T022956Z_

## Retry instead of hard-failing when a required check's job finishes before its multi-job run

`ai-privileged-merge.yml`'s required-check verification combined the trusted
workflow run's *identity* (`workflow_id`, `path`, `event`, `head_sha`,
`head_repository`) and its *liveness/conclusion* (`status == "completed" and
conclusion == "success"`) into one jq predicate. A required check's own job
routinely finishes (and publishes its check-run) before a slower sibling job
in the same run does — a fast `eligibility` job alongside a slower
`build-test` job is a normal CI shape, not an edge case. That made the whole
predicate fail while the run was still `in_progress`, which hard-failed
(`exit 1`) instead of retrying, so `ai-promotion-retry.yml` never got a
"try again later" signal and promotion needed a manual admin merge.

_Date: 2026-09-25; issue #1610_

## Fail closed when a moved workflow step's boundary can't be found

`move_step_before_guard`'s end-of-step scan accepted any line indented exactly
six spaces and starting with `- `, and silently fell back to end-of-document
when none matched. Addressing the non-blocking AI-review follow-up from
#1596: the scan now requires an actual step marker (`- name: ` or
`- uses: `), and raises `SystemExit` naming the drift instead of defaulting
to end-of-document, matching this generator's existing fail-closed pattern
for every other marker in the same function. No change to the generated
`node-ci-protected.yml` at the current tree.

_Date: 2026-09-25; issue #1597_

## Repin Authn required workflow after canonical tool-cache merge

Repin the generated Authn required workflow and its ruleset verifier to the
canonical protected Node workflow at merge commit
`fff8891797f316a46417e6689719b79bfeafe01f`. The consumer declaration remains
the only baseline selection surface; the organization workflow retains the
credential, authorization, fail-closed validation, registry-integrity, and
evidence-receipt boundaries.

_Date: 2026-09-25; id:20260925T000001Z_

## Document why hosted tool-cache root admits uid 1001

`gen-node-ci-protected.py`'s `hosted_tool_cache_uids = (0, 1001)` now carries a
comment naming uid 1001 as the GitHub-hosted "runner" account, addressing the
non-blocking AI-review follow-up from #1603. No behavior change; regenerated
`node-ci-protected.yml` accordingly.

_Date: 2026-09-24; issue #1606_

## Cover Renovate comparator ranges unioned with carets

Add regression coverage proving the Renovate changelog parser accepts a spaced
comparator range unioned with caret ranges, such as
`>=0.2.2 <0.4.0 || ^1.0.0 || ^0.4.0`, on either or both sides of a change.

_Date: 2026-09-24; issue #1602_

## Repin the deployment CLI contract to cli-cloud 1.1.1

`contracts/container-deployment-cli` pinned `@verjson/cli-cloud@1.1.0`, which shipped
`runnerHostEvidenceInput()` with two shell bugs that made every real invocation fail
before this evidence path was ever exercised against a live host (`df -Pi --output=`
rejected by current coreutils; a `pipefail`/`set -e` interaction that silently aborted
the script whenever `docker`, `jq`, or `pwsh` was absent). Both are fixed in
`verjson-cli-cloud@1.1.1` (verjson-cli-cloud#536, verjson-cli-cloud#537,
verjson-cli-cloud#538), verified live against `Verjson/verjson-git-runners`'s
`gha-deployment-canary` host. Repinned `package.json`/`package-lock.json` to `1.1.1` and
regenerated the lockfile; `scripts/validate-container-deployment-cli-lock.py` passes.

_Date: 2026-09-24; id:20260924T190951Z; refs #1451_

## Repin authn required CI to hosted tool cache fix

Repin the required authn type-surface workflow to the canonical CI release that
recognizes the exact GitHub-hosted setup-node tool-cache root convention while
keeping tool prefixes and executables fail-closed.

_Date: 2026-09-24; id:20260924T000008Z_

## Accept the hosted setup-node cache root convention

Allow only the exact `0777` `/opt/hostedtoolcache` directory with the known
GitHub-hosted runner ownership (`uid 0 or 1001`, `gid 0`). Retain strict
ownership and mode checks for every tool prefix, executable, and descendant
mounted into the protected sandbox.

_Date: 2026-09-24; id:20260924T000006Z_

## Repin authn required CI to sandbox-order fix

Repin the required authn type-surface workflow to the canonical CI release that
provisions the verified compatibility sandbox before executing the credentialless
candidate script plan.

_Date: 2026-09-24; id:20260924T000005Z_

## Provision protected sandbox before candidate scripts

Provision the verified hosted bubblewrap compatibility sandbox before the
protected credentialless candidate script plan. This preserves the fail-closed
namespace boundary while making the required tool available before execution.

_Date: 2026-09-24; id:20260924T000003Z_

## Repin authn required workflow after protected cache fix

Repin the generated authn required workflow and its ruleset renderer to the
merged canonical protected-CI commit that permits a missing optional runtime
cache while retaining strict validation when a cache is present.

_Date: 2026-09-24; id:20260924T000001Z_

## Test prerelease protected baseline resolution

The protected baseline regression harness now exercises the explicit prerelease opt-in through compatibility request generation, registry resolution, exact range satisfaction, and provenance binding. This prevents a malformed prerelease authorization branch from passing on indentation checks alone.

_Date: 2026-09-23; issue #1590_

## Retire the obsolete cli-projects repository rule

Align the package-surface rollout with the durable cli-projects resolution by requiring
the superseded repository status-check rule to remain disabled and removing its obsolete
activation transaction from the organization required-workflow rollout.

_Date: 2026-09-23; issue #1576_

## Resolve consumer type-surface baselines from protected declarations

The protected Node CI contract now reads an exact consumer baseline declaration from the authenticated pull-request base commit, verifies its package and script against protected policy, resolves the exact released artifact, and carries the declaration and artifact binding in the credentialless evidence receipt.

_Date: 2026-09-23; issue #1575_

## Parameterize component release caller defaults

Allow canonical release callers to declare a strictly validated default
version prefix and component as one atomic pair. Generated component workflows
now open manual dispatches on their intended stream, while callers that omit the
new options retain the existing `v` and unscoped defaults. Contract tests bind
the emitted defaults to generator provenance and reject later byte drift.

_Date: 2026-09-23; issue #1565_

## Harden AI review output instructions and diagnostics

- Require review providers to return only the canonical JSON verdict keys and fold follow-up metadata into the supported `note` field.
- Add incident fixtures proving malformed follow-up objects and non-JSON fallback responses remain fail-closed with actionable, content-free diagnostics.

_Date: 2026-09-23; issue #1564_

## Bind AI review validators to the canonical source

Generated AI-review callers now pass the immutable Verjson/.github contract revision used by their reusable-workflow edge, so consumer reviews check out policy and authorization validators from that canonical commit rather than pairing the hub repository with the consumer head.

_Date: 2026-09-23; issue #1554_

## Scan executable authorization steps for runner-held waits

The event-driven authorization contract now parses workflow `run:` blocks and ignores shell comments before rejecting runner-held waiting constructs. Paired mutation controls prove a real executable `sleep` remains rejected while comments may name `sleep`, `MERGE_PROBE`, and `ci-wait` without making the governing contract itself fail.

_Date: 2026-09-23; issue #1501_

## Validate every discovered release workflow trigger

Remove the last-caller-only trigger assertion from the emitted changelog contract and
cover a multi-release adopter whose non-final workflow is incorrectly push-triggered.
Raise the bounded actions-CI worker ceiling after the expanded changelog-release group
completed all 62 commands but was canceled during finalization at the former limit.

_Date: 2026-09-23; issue #1488_

## Pin required-check audit test fixtures to the audited contract

Build required-check audit fixtures from the committed contract pin so uncommitted generator edits cannot create false byte-drift failures.

_Date: 2026-09-23; issue #1486_

## Verify non-gate module declarations against imports and command use

Require every declared CI-gate library module to be imported by its exact tracked path
from another CI-gate script and never invoked as a command on a tracked Actions execution
path, regardless of relative, workspace-variable, trusted-checkout, or punctuation form.
Ambiguous imports, relative-package and module-attribute filename collisions, shell
continuations, and parse failures that mention a gate path fail closed.

_Date: 2026-09-23; issue #1477_

## Require changelog fragments for release-relevant changes

Require a new valid `NEXT/` fragment when a pull request changes behavior, configuration, code, documentation, or pins without touching a dependency manifest.

_Date: 2026-09-23; issue #1455_

## Prove standalone shell lint coverage

The platform Actions group now invokes its warning-level tracked-shell lint through a focused runner with executable conformance coverage. Pull-request and push triggers cover tracked shell scripts outside the conventional script directories.

_Date: 2026-09-23; issue #1436_

## Report closing issues left open by App merges

The fallback now binds the target and every visible issue repository to an immutable database ID plus its current name, and rejects accessible cross-repository or identity-mismatched nodes. The retry resolver is explicitly downscoped without issue access; only the promotion job receives the `issues:read` authority needed for capture and reporting.

_Date: 2026-09-23; issue #1424_

## Align the cli-projects protected release plan

Regenerate the cli-projects organization required workflow to invoke the consumer's
supported `test:release` contract instead of its retired `test:release-v1` alias, keeping
the activation rehearsal and fresh-canary gate aligned with executable consumer behavior.

_Date: 2026-09-23; issue #1423_

## Bind the deferred-CI regression fixture to its exact delta

The pre-ADR-0178 conformance fixture generator now validates and removes exactly
`jobs.deferred-ci` from a copied contract document. Independent structural-difference
assertions and paired wrong-field controls prevent a renamed, moved, or malformed job from
silently turning the historical counterexample into a passing but irrelevant fixture.

_Date: 2026-09-23; issue #1420_

## Cover every organization ruleset bypass with a reviewed contract

Make the organization-wide ruleset audit reject bypass actors on any uncontracted
ruleset, independent of branch targeting, and require every contracted ruleset to
match its exact reviewed actor image without treating actor ordering as policy.

_Date: 2026-09-23; issue #1408_

## Harden public-cache file and mount validation

The Node CI compatibility sandbox now carries a malicious-cache regression proving that
a top-level `content-v2` file symlink cannot copy host content into the sandbox. Its
public-cache mount allowlist is also documented and checked against the installed
Bubblewrap option synopsis, including negative controls for arity drift, unsupported
flags, truncated arguments, and replacing `lstat` with a symlink-following check. This
also resolves #1419.

_Date: 2026-09-23; issue #1397_

## Bind autonomous merge to independent review receipt

Autonomous terminal promotion now requires the latest exact-head verdict from an independently authorized non-App maintain/admin account, terminally revalidated before the merge App token is minted. The final merge boundary also rechecks draft, normalized hold labels, the `DO NOT MERGE` title marker, open state, exact head, and base immediately before the admin merge. Edited, dismissed, withdrawn, superseded, malformed, App-authored, permission-downgraded, and late-held attempts fail closed while the protected human merge path remains unchanged.

_Date: 2026-09-23; issue #1383_

## Restore deferred CI to the Authn required workflow

Regenerate the organization-owned Authn type-surface caller at a current immutable Node CI contract so stability-delay deferrals remain visible instead of appearing as successful no-op runs.

_Date: 2026-09-23; issue #1368_

## Separate AI authorization enrollment from core checks

Give the AI authorization arm its own organization-controlled repository property and
add audited, cohort-preserving migration and rollback payloads so adopting deterministic
core checks no longer silently changes AI review enforcement.

_Date: 2026-09-23; issue #1363_

## Retire review-App authorization-check ownership

Require GitHub Actions App `15368` to own every authorization receipt, check,
terminalizer, recovery, retry, promotion, and post-merge evidence path. Remove
`checks:write` from the dedicated review App while retaining its identity for
approval-author verification. The change remains blocked until all live legacy
checks are terminal and the zero-active-receipt prerequisite in
[ADR 0203](../docs/decisions/0203-retire-review-app-authorization-check-ownership/README.md)
is proven.

_Date: 2026-09-23; id:8d07a5c_

## Prune closed issues from the Active Issues index

Removed the #1502 and #1565 entries from `CLAUDE.md`'s Active Issues index; both
issues are closed. Stale entries there cost context in every session and
misreport work as open, per the index's own pruning rule.

_Date: 2026-09-23; id:20260923T234231Z_

## Resolve secretless compatibility ranges from released versions

The credentialless compatibility lane now resolves bounded ranges from the
authenticated package version list, selects the highest matching stable
release, and records the exact artifact provenance used by the lane.

_Date: 2026-09-23; id:20260923T191700Z_

## Retire the fulfilled merge-gate guidance proposal

Remove the temporary merge-gate guidance proposal after the authoritative change lands as
[Verjson/verjson-agents#412](https://github.com/Verjson/verjson-agents/pull/412). The proposal was tracked by
[Verjson/.github#1421](https://github.com/Verjson/.github/issues/1421); the adopted
guidance now directs privileged merges through the canonical fail-closed assertion, binds
the validated head SHA, and names all reads the helper performs. Correct the helper's own
permission message to match those reads.

_Date: 2026-09-23; id:20260923T104554Z_

## Allow protected scripts without an optional runtime cache

The protected candidate-script sandbox now treats an absent npm runtime cache
as an intentional empty baseline when public cache population is disabled.
Existing cache paths remain strictly validated, snapshotted, and isolated;
the missing-path regression is covered so the protected lane reaches the
credentialless consumer script plan instead of failing during baseline
inventory.

_Date: 2026-09-23; id:20260923T000011Z_

## Repin authn required workflow after stable baseline fix

Repin the canonical authn required-workflow generator and rendered caller to
the reviewed protected CI commit that accepts released stable baseline
versions. This keeps the organization ruleset and its consumer-owned
declaration aligned after the protected range-validation correction.

_Date: 2026-09-23; id:20260923T000009Z_

## Accept stable versions in protected baseline acquisition

The protected workflow generator now keeps stable semantic-version validation
at the same level as its explicit prerelease branch. Generated consumers can
therefore acquire an authorized released baseline such as `3.0.0` or `0.11.1`;
the new regression test prevents stable declarations from being rejected as if
they required prerelease authorization.

_Date: 2026-09-23; id:20260923T000008Z_

## Record the retired authn ruleset preimage

The canonical authn ruleset contract now records the already-disabled retired
repository ruleset preimage. This keeps the protected workflow rollout
fail-closed against the live control-plane state instead of treating a known
retirement as an apply-time surprise.

_Date: 2026-09-23; id:20260923T000007Z_

## Harden declaration-only protected baseline execution

Protected consumers now execute the compatibility lane when the declaration is
the only baseline input, and explicit prerelease authorization is honored by
the bounded-range resolver. The generated adoption instructions document the
split ownership for subsequent `@verjson/*` packages.

_Date: 2026-09-23; id:20260923T000006Z_

## Advance the authn required baseline to v3.0.0

The existing authn required caller and its policy fixture now authorize the
released `@verjson/authn` 3.0.0 baseline, allowing the declaration migration to
land before the protected caller is activated.

_Date: 2026-09-23; id:20260923T000004Z_

## Repin the authn required caller to the hardened protected workflow

The generated authn required caller now references the immutable commit that
contains the declaration-only lane hardening and prerelease authorization fix.

_Date: 2026-09-23; id:20260923T000002Z_

## Review fastlane selectors for reusable CI callers

Allow the exact `CI_RUNNER_FASTLANE` lane expression only for same-contract `node-ci.yml` calls that bind pull requests to secretless execution and expose exactly pull requests plus pushes to `main`. Regressions reject unguarded forks, merge queues, dispatches, non-default pushes, mismatched pins, and unreviewed variants so private compatibility checks retain their hosted sandbox without routing PR-derived code there.

_Date: 2026-09-22; issue #1548_

## Remove redundant branch reference guard

Removed the empty branch reference guard: the earlier empty-branch return and `set -e` already stop classification if URI encoding fails.

_Date: 2026-09-22; issue #1541_

## Gate re-arm on pinned App-key validation

Use the pinned App-key environment policy result for re-arm validation. This avoids a checkout at the caller's workflow SHA that may not exist in `Verjson/.github` and prevents review dispatch when the policy check fails.

_Date: 2026-09-22; issue #1540_

## Report canonical changelog contract drift across adopters

Secret-bearing inventory runs use fixed ephemeral hosted capacity, accept only scheduled or default-branch push events, and serialize fleet-controlled paths without active Markdown.
A weekly read-only fleet report now names adopter pins that differ from the current canonical contract in a private caller run, with an immutable inventory artifact for follow-up. Incomplete scans are shown in the report and fail the workflow so missing access cannot appear as a clean fleet.

_Date: 2026-09-22; issue #1506_

## Lint tracked shell scripts in Actions

The platform Actions group now runs warning-level ShellCheck over every tracked shell script; existing warning-level findings are fixed or narrowly annotated at their source.

_Date: 2026-09-22; issue #1502_

## Pin gate re-arm to the inline App-key validator

Update the nested gate-rearm policy call to immutable validator revision `f56af66cc14f3bdc7697e527df4c8c4d04ab5935`. Part #1540.

_Date: 2026-09-22; id:20260922T043019Z_

## Avoid nested reusable-workflow SHA mismatch in AI key validation

Run the tested AI review App-key validator inline in the pinned environment-admission workflow, with contract coverage that keeps the workflow block identical to the helper script.

_Date: 2026-09-22; id:20260922T042759Z_

## Protect deployment admission authority

The reusable workflow now rejects non-default-branch calls before production jobs
start. Controller admission validates the complete review authority and rebuilds the
submitted plan from reviewed configuration and evidence before persisting a receipt.
This closes the direct-admission gap found while delivering #1527 and continues
#1451's protected runner deployment work. See ADR 0198.

_Date: 2026-09-21; issue #1527_

## Confine host observation temp files

Host observation credentials now require job-scoped temporary storage, and requests must
bind to the admitted deployment plan.

_Date: 2026-09-21; issue #1526_

## Stabilize generated ADR-index formatter checks

Select only the pinned Prettier version and fail when formatter results are mismatched or incomplete, so an invocation failure cannot hide an earlier finding.

_Date: 2026-09-21; issue #1521_

## Validate relative ADR links

Corrected seven stale ADR references and added a platform CI check that resolves every tracked relative link in `docs/decisions` against a real file.

_Date: 2026-09-21; issue #1509_

## Reject ref-encoding clobbers in the merge gate

The default-branch URI encoding gate now rejects repeated assignments to the
merge workflow's encoded compare ref, and the workflow makes that encoded value
readonly before use. Regression coverage checks direct and guarded clobbers,
rejects duplicate compare functions including Bash's function-keyword and
line-continuation forms, requires the unique production call, and executes the
extracted function against a stubbed API to verify that hostile and slash-bearing
refs remain encoded.

_Date: 2026-09-21; issue #1508_

## Hold AI review freshness when mergeability cannot be verified

The freshness gate now treats failed or permanently unknown mergeability reads through its retry helper as indeterminate and holds the PR instead of allowing an unverified state to proceed. Regression coverage exercises both unreadable and persistently unknown responses.

_Date: 2026-09-21; issue #1505_

## Fail AI review authorization visibly when its key is invalid

The AI review gate now completes authorization checks with a clear failure when its private key is empty or malformed, while preserving receipt-bound compatibility for in-flight checks as ownership moves to GitHub Actions.

_Date: 2026-09-21; issue #1498_

## Explain issue-less changelog identity diagnostics

When an issue-less timestamp or hexadecimal identity is mistakenly placed in `issue:`, validation now directs the author to the `id:` key while preserving the filename contract.

_Date: 2026-09-21; issue #1497_

## Defer AI review when the Renovate release-age status cannot be read

Treat an unreadable `renovate/stability-days` status as unverified and defer the review lane instead of silently treating the gate as clear. The extracted workflow test covers the transient API failure path.

_Date: 2026-09-21; issue #1495_

## Fail closed when repository workflow classification reads fail

The classifier now distinguishes repositories with no workflows from unreadable or truncated GitHub tree responses, so uncertain repository state is not silently classified as having no CI. Regression coverage includes empty inventories, metadata and tree read failures, truncated trees, and workflow content failures.

_Date: 2026-09-21; issue #1493_

## Wire controller to read-only runner host evidence export

The controller and canonical transport now use the pinned `cli-cloud` host
evidence export for baseline, capacity, and post-update observations. Requests
bind reviewed App authority, exact release bytes, fleet, action, and plan; SSH
key material is routed only through a private temporary file. Host observation
fails closed when authority is incomplete and never uses fleet or runner-control
write tokens. Protected environment credential provenance is recorded in
[ADR 0198](../docs/decisions/0198-confine-runner-host-evidence-credentials/README.md).

_Date: 2026-09-21; issue #1451_

## Keep queued AI review runs eligible for receipt validation

Receipt-bound AI review recovery now accepts a live workflow-dispatch run that is still queued while an environment-gated sibling waits for admission, while continuing to require the exact run identity and reject terminal or unrelated runs ([#1393](https://github.com/Verjson/.github/issues/1393)). Completion selects the Checks API token from the verified check owner so legacy App-owned checks can reach a terminal state; new checks remain Actions-owned. The temporary permission and its exact-receipt limits are recorded in [ADR 0200](../docs/decisions/0200-temporary-legacy-authorization-check-write/README.md), with removal tracked by [#1540](https://github.com/Verjson/.github/issues/1540).

_Date: 2026-09-21; issue #1393_

## Validate runner fleet capacity evidence

The deployment controller now validates the host-export lane, complete runner roster, and per-runner availability against the reported capacity, rejects boolean capacity values, and uses `@verjson/cli-cloud@1.1.0` for [#1451](https://github.com/Verjson/.github/issues/1451).

_Date: 2026-09-21; id:20260921T100254Z_

## Prune a closed issue from the active list

Remove the ADR-index formatter issue from the active-issues list when its implementing pull request closes it, so session guidance does not carry stale work. This follow-up was needed because the tracking issue had already closed before the list cleanup merged.

_Date: 2026-09-21; id:20260921T015252Z_

## Match title hold markers as whole phrases

AI review workflows now recognize `DO NOT MERGE` as a whole phrase in PR titles. Permissionless event classification, receipt re-arming, and both merge gates use the same word boundaries, so longer unrelated words no longer trigger title holds.

_Date: 2026-09-20; issue #1518_

## Choose Pulumi for the GitLab mirror kit

Accepted ADR 0197 supersedes the Terraform implementation choice for the GitLab mirror kit. It preserves published v1/v2 manifest compatibility, requires a versioned Pulumi manifest artifact, and keeps live migration separately authorized.

_Date: 2026-09-20; issue #1514_

## Enforce title hold state transitions

The AI review arm now requires a maintain or admin actor, based on GitHub's `role_name`, before clearing draft, title, or recognized label holds, and before adding `ai-review` or `re-review` labels can reach App-token minting. Title edits that add a `DO NOT MERGE` marker reach the trusted arm without hold-clear authorization so it can disable native auto-merge; edits that remove the marker require maintainer or admin authorization. Body/base-only edits and title edits that leave held state unchanged skip runner placement and App-token minting. Unrelated label additions and removals are filtered on a permissionless untrusted runner before protected jobs start. Recognized hold-label additions still reach the protected arm to disable native auto-merge. All re-arm paths retain exact-head receipt checks.

_Date: 2026-09-20; issue #1504_

## Keep hosted runner baseline updates out of Renovate

Renovate no longer updates `github-runner` dependencies or groups the `ubuntu` runner selector with action digest updates. The reviewed organization runner baseline stays under coordinated policy control; individual runner image migrations require a complete rollout instead of a partial workflow edit.

_Date: 2026-09-20; issue #1485_

## Reject incompatible secretless compatibility runner routing

Reject secretless trusted-ref compatibility runs when the selected runner is self-hosted, before dependency acquisition begins. Secretless pull-request runs remain on their untrusted runner and ignore the caller's runner input. Refresh the generated pre-ADR-0178 counterexample so its only difference remains the deferred job and the conformance test covers the new shared eligibility guard.

_Date: 2026-09-20; issue #1416_

## Remove unused generated-set checker argument

The generated changelog contract test no longer carries a dead generator-flags argument. The canonical generator contract test asserts the emitted checker omits it.

_Date: 2026-09-20; id:20260920T234636Z_

## Route terminal-hold labels through the merge gate

Adding a terminal-hold label now disables native auto-merge before the gate exits. The event allowlist accepts normalized `hold` and `do-not-merge` labels and tests the lowercase `hold` case without dispatching a paid review.

_Date: 2026-09-19; issue #1507_

## One shared definition of what a contract reference is

`scripts/contract-version.py` and `scripts/fleet-contract-inventory.py` each carried their
own `uses:` pattern, and the two drifted. #1472 taught the first to read a pathless
root-action pin and did not change the second, so at `aa9d386` the line
`    uses: Verjson/.github@<40-hex>` was a reference to one tool and invisible to the
other — `contract-version` returned `(path=None, ref=<sha>)` and the inventory returned
nothing at all. An inventory that reports fewer references than exist is wrong in the
safe-looking direction: a repository carrying a root-action pin looks clean.

_Date: 2026-09-18; issue #1482_

## Hold the merge gate when freshness cannot be established, instead of proceeding

The merge gate no longer merges on a possibly stale base when it cannot establish that
the base is current. Two separate reads in the freshness step could previously leave it
unable to establish that and proceed anyway; both now hold.

_Date: 2026-09-18; issue #1476_

## Require the conformance reset to name every variable the loop assigns

The structural assertion added with #1448 pinned the per-repository reset's *position*
but not its *membership*. Deleting `metadata default_branch visibility_type has_secret`
from the reset list in `scripts/privileged-merge-conformance.sh` passed the entire suite
green — exit 0, zero failures — and that is precisely #1448's own bug class: a variable
assigned in the fleet loop and forgotten from the reset. The check could not have caught
the defect it was added for. Re-confirmed against the merged code at `89f11f6` before
this change, not only against the branch the finding was raised on.

_Date: 2026-09-18; issue #1473_

## Read both value-side `uses:` shapes the contract scan misdescribed

`scripts/contract-version.py` extracts a hub reference from the value side of a
`uses:` key, and two readable shapes were misdescribed there.

_Date: 2026-09-18; issue #1472_

## Report a mutable privileged-merge caller pin as a non-SHA pin, not a pin-count problem

`scripts/privileged-merge-conformance.sh` extracted a consumer's canonical `uses:` pin
with a capture group of `([0-9a-f]{40})`. A repository pinned to a mutable ref — `@main`,
a tag, a short SHA — therefore produced *zero* captures rather than one malformed capture,
fell into the pin-count arm, and was told it had a missing or duplicated `uses:` line when
the line was present and simply mutable. The 40-hex guard one branch away, which says
`pin is not a 40-hex commit SHA`, could not fire.

_Date: 2026-09-18; issue #1471_

## Widen the ref-interpolation scan past its two URL shapes

The `default-branch-uri-encoding` gate recognized ref interpolations in only two URL
shapes, so a positional encoding error in any other ref-bearing path passed green. The
scan now anchors on every ref-bearing GitHub REST path segment — `git/ref[s]/heads/`,
`commits/`, `git/commits/`, `git/trees/`, `git/tags/`, `branches/` including
`rules/branches/`, and both operands of a `compare/A...B` range — and treats `${VAR}` as
the same shell interpolation as `$VAR`. Recognized sites went from 35 to 77.

_Date: 2026-09-18; issue #1464_

## Measure the contract-version guards instead of describing them

`scripts/contract-version.py` carried three claims its suite did not measure, and
this adds the tests that measure them. A fourth claim, about the `node_modules`
symlink guard, turned out to be measured already — the correction there is to the
record, not to the code.

_Date: 2026-09-18; issue #1463_

## See a multi-line awk consumer on the read end of a pipe

The `pipefail-sigpipe` guard now buffers a quoted argument written across real
newlines, so an `awk` program spanning several lines is scanned as the one
pipeline it is, and its `exit` is read from inside the quoted program rather
than from "anything after `awk` with no `|` in between". Two live sites the
guard could not see are converted to the here-string form it already prescribes.

_Date: 2026-09-18; issue #1461_

## The container deployment runbook stops describing the export API as unreleased

`docs/container-deployment-runbook.md` carried two readings of the same fact. Its
opening sentence already recorded that verjson-cli-cloud#504 is closed and that
`@verjson/cli-cloud@1.0.0` ships the `runner-host-evidence` export API, while its
closing sentence still instructed a reader to "adopt its exact dependency" once the
export is released. PR #1362 pinned that dependency — `@verjson/cli-cloud` is
`1.0.0` in `contracts/container-deployment-cli/package.json` — so the closing
sentence named a step that had already happened and left the outstanding work
ambiguous.

_Date: 2026-09-18; issue #1452_

## Cover every ci-gate script by a stated rule instead of the .test. suffix

The #1320 orphan detector in `scripts/actions-ci-groups.test.sh` exists so a gate cannot
pass locally while running nowhere in Actions. It found its candidates by the `*.test.sh`
suffix, so `scripts/ci-gate/hub-changelog-validate.sh` — a gate that is not a test — was
invisible to it: deregistering it from `scripts/actions-ci-groups.tsv` left the entire
suite green with the gate running nowhere. PR #1446 pinned that one registration from
inside the gate's own test, which protects one file and signals nothing about the next.

_Date: 2026-09-18; issue #1450_

## The ref-interpolation invariant reaches Python callers too

The enumeration in `scripts/ci-gate/default-branch-uri-encoding.test.sh` held every
`?ref=$…` and `git/ref[s]/heads/$…` interpolation in workflows and shell scripts to a
percent-encoding or a 40-hex constraint, but it never looked at `*.py`. Hub-privileged
Python built the same URLs outside it, so a new unencoded read there would have landed
silently — the hole ADR 0192's invariant exists to close, left open in another language.

_Date: 2026-09-18; issue #1449_

## Reset privileged-merge conformance state before any branch leaves the iteration

`scripts/privileged-merge-conformance.sh` evaluated each repository inside one loop
whose per-repository `unset` sat at the **bottom** of the body, after seven branches
that can leave the iteration early. The 40-hex caller-pin guard was the one branch
that reached that exit while the repository was already inventoried as a consumer, so
it both skipped the reset and skipped the repository's own organization-secret check:
a consumer with an unusable pin was never asked whether it can read
`MERGE_APP_PRIVATE_KEY`, and the next repository began its verdict on the previous
one's values.

_Date: 2026-09-18; issue #1448_

## Guard every early-exiting pipe consumer, not just `grep -q`

The `pipefail-sigpipe` guard now rejects any tracked shell script that pipes into a
consumer which stops reading early — `head` in every spelling, `grep -m`/`--max-count`,
and an `awk` program that calls `exit` — instead of `grep -q` alone, and twenty live
sites have been rewritten to read their first line without truncating a pipe.

_Date: 2026-09-18; issue #1445_

## Stop attributing an empty ruleset response to an ungoverned base ref

`assert-mergeable-head.sh` reported every zero-length required set with one sentence —
`declares no required status checks, so a green rollup proves nothing about it` — and on a
merge gate a wrong attribution is a wrong remedy. The documented response to an ungoverned
ref is *add a ruleset*, so an operator reading that sentence would widen a ruleset that
already exists, or decide the gate does not apply to the repository and merge by hand.

_Date: 2026-09-18; issue #1437_

## Measure which `uses:` shapes the contract-version scan misses, and name the rest

`contract-version verify` now recognizes a quoted or space-padded `uses:` key, and
reports a `uses:` reference it cannot resolve as an `UNRESOLVED_REFERENCE` gap
instead of silence. Both changes are measured rather than speculative: across
1208 tracked YAML files and 7361 Markdown files in 94 organization repositories
plus this one, they leave all 432 hub references and every verdict on them
unchanged, and produce zero new findings.

_Date: 2026-09-18; issue #1433_

## Stop pipefail from reporting grep -q's early exit as a contract violation

`grep -q` exits on its first match. Reading from a pipe, that closes the pipe while the
producer is still writing, so the producer dies on `SIGPIPE` and `set -o pipefail` reports
`141` as the pipeline's status. Thirty-nine assertions across eighteen scripts were built on
that shape, so any of them could redden a required check on a pull request whose diff could
not have caused it — `ci-eligibility.test.sh` did exactly that on #1417, reporting that
`node-ci.yml` lacks `statuses: read` when it grants it.

_Date: 2026-09-18; issue #1430_

## Name the generator mode that refused instead of blaming its empty capture

`changelog-caller-contract.test.sh` captured each generator mode with
`var="$(bash "$gen" <mode> "$sha")"`, which discards the generator's exit status. A mode
that refused therefore left an empty capture, and the assertions downstream read that
emptiness as a defect in the thing they test: a single
`FAIL - adr-index-test did not rewrite the repository root for the adopter layout` at a run
whose rewrite logic was correct, while six reruns of the identical tree were clean. That
verdict sends the next reader to the rewrite, which works, rather than to the resolution
failure, which does not.

_Date: 2026-09-18; issue #1427_

## Every gh api URL percent-encodes the adopter ref name it interpolates

Hub-privileged automation read adopter repositories through URLs built by string
interpolation of an adopter-controlled ref name — the adopter's default branch, and
in one case a contributor's PR head branch. Every such site now percent-encodes the
name before it reaches the URL:

_Date: 2026-09-18; issue #1426_

## Run the published changelog contract on the hub's own pull requests

`Verjson/.github` now validates its own `NEXT/` fragments against the contract it
publishes, from `scripts/ci-gate/hub-changelog-validate.sh` in the `platform`
actions-ci group. A fragment the pull request adds without `impact:` now reddens
the hub's own checks. Only `validate --base` evaluates that field, and nothing on
the hub was passing it a base; a duplicate fragment identity was already caught,
by the base-less `validate` the `changelog-release` group has always run.

_Date: 2026-09-18; issue #1425_

## Name the token permission Gate A needs, and carry the guidance diff that adopts it

`assert-mergeable-head.sh` reads the base ref's ruleset to establish its required set, and
`gh api` exits 1 for an authorization refusal and a transient 5xx alike — so an adopter
whose token could not read that endpoint got the same opaque fault as a retryable outage
and no way to tell which. The ruleset read now inspects the HTTP status and refuses
401/403/404 with a message naming the token as the cause and stating the access required:
ordinary repository read (fine-grained Metadata read, classic `repo` for a private
repository, `contents: read` in Actions), never `administration`. That requirement is now
stated in the script's own header, together with the fact that GitHub masks an
unauthorized read of a private repository as 404. Gate A remains unevaluable — never
degraded — when the read is refused.

_Date: 2026-09-18; issue #1421_

## Bound the generated-set remedy pin's claim to the name it matches

The pin added for #1369 over `generated_set_check`'s remedy handling carried a claim that
touching `$remedy` anywhere else in the generator "reddens that pin". Measured against the
pin itself, that is false in one direction, and the claim is the artifact a later reader
trusts instead of re-deriving the property.

_Date: 2026-09-18; id:20260918T212612Z_

## Upgrade the protected deployment CLI to cli-cloud v1

Upgrade the immutable container-deployment contract to `@verjson/cli-cloud@1.0.0`,
including its verified lock graph and the exact behavioral contract assertions.

_Date: 2026-09-18; id:20260918T024500Z_

## Say that ruleset drift reports compare asserted fields, not whole images

The authorization-arm audit's drift reports now describe the comparison they
actually run: `main-protection matches neither reviewed image on the fields the
contract asserts`, and the arm and deterministic reports name "the fields its
reviewed image asserts".

_Date: 2026-09-17; issue #1411_

## Report the live ruleset fields no reviewed image pins

The authorization-arm audit's report now carries `unpinned_live_fields`: every
live field of a contracted ruleset that no reviewed image asserts, named by its
dotted path. The scheduled run therefore hands the periodic review ADR 0188
calls for its exact candidate set.

_Date: 2026-09-17; issue #1410_

## Adjudicate the scheduled arm audit's findings against a reviewed expectation

The scheduled authorization-arm audit reported only into its own run log. It
ships deliberately red — `Verjson/verjson-agents` is armed with no deterministic
required CI behind it (#1401, #1407) — so a genuinely new finding and the known
one produced the same signal, and telling them apart meant opening the run.

_Date: 2026-09-17; issue #1409_

## Make the authorization arm audit runnable, and run it on a schedule

`scripts/ai-review-required-workflow-audit.py` now compares a live organization
ruleset against the fields its reviewed image actually asserts — tolerating keys
GitHub adds to its own schema, while still reporting an unexpected value, an
absent asserted key, or an added list member such as a bypass actor — and runs
daily in its own job alongside the organization ruleset conformance audit.

_Date: 2026-09-17; issue #1404_

## Refuse a blank --base/--head instead of comparing HEAD against itself

`scripts/changelog.py check-pr --base '' --head HEAD` exited **0 silently**.
`git diff "...HEAD"` resolves a blank side to `HEAD`, so an empty `--base` made
the gate compare `HEAD` against itself: an empty diff satisfies every rule the
subcommand exists to enforce — no fragment requirement, no aggregate-edit
refusal, no fragment-consumption refusal. The ~95 adopter repositories that run
this gate would have been told their pull request was clean.

_Date: 2026-09-17; issue #1403_

## record that the review environment is provisioned before the caller that names it

Adds ADR 0187. The generated AI review caller passes a required `ai_review_environment`
input that resolves in the adopter's repository, and GitHub creates a referenced
environment on first use with no protection rules. Installing the caller without creating
`ai-review-app` first therefore yields a working review lane and an unprotected
environment, with every signal reporting success — trading a loud failure for a silent one.

_Date: 2026-09-17; issue #1401_

## An omitted optional input takes the value the runtime would supply

`bind_inputs` gave an optional `workflow_call` input with no declared default
the empty string for every type but boolean. That is right for a string and
wrong for a number, which GitHub supplies as `0`: bound as `''`, an unsupplied
numeric input compares unequal to every number a guard could test it against,
and the guard is modelled false for a reason the contract never expressed.

_Date: 2026-09-17; issue #1387_

## The generated ADR index no longer fights prettier

`scripts/gen-adr-index.sh` now emits a `<!-- prettier-ignore -->` guard and a
trailing blank line inside the region it already owns, so `prettier --check` and
`gen-adr-index.sh --check` agree by construction instead of being mutually
unsatisfiable over `docs/decisions/README.md`.

_Date: 2026-09-17; issue #1382_

## Fail the organization ruleset audit when a required status check pins no producer App

The organization ruleset conformance audit now reports every required status
check whose ruleset entry declares no producer `integration_id`. An unbound
context can be satisfied by a status posted from any App that happens to use the
same name, which is the ADR 0024 class the ADR 0184 gate's provenance half exists
to close.

_Date: 2026-09-17; issue #1381_

## The ADR index generator now ships with the suite that covers it

`scripts/gen-changelog-caller.sh` gained an `adr-index-test` mode, so an adopter
that opts into ADR-index checking acquires `scripts/gen-adr-index.test.sh` from
the pin instead of hand-keeping a copy that rots. The mode re-emits the
canonical hub suite, rewriting only its repository-root computation for the
adopter's flat `scripts/` layout and failing closed if that line ever moves.

_Date: 2026-09-17; issue #1380_

## Give the organization contract a release identity an adopter can declare and a check can read back

ADR 0191 decides that a contract version is a published `Verjson/.github`
release, that an adopter declares exactly one of them in
`.github/verjson-contract.json`, and that the declaration is verified by
resolving it to that release's commit and requiring every `Verjson/.github`
reference on disk to name that commit. `scripts/contract-version.py` is the
readback: a declared version nothing verifies fails exactly like a pin nobody
advances.

_Date: 2026-09-17; issue #1374_

## Hand the verified public npm cache to the compatibility sandbox

`secretless-runtime-public-cache` content reaches the runtime-resolved
compatibility lane again: the sandbox is given a disposable copy of the run's
own verified `_cacache/content-v2` at the npm cache path the consumer reads,
so offline installs from the verified public blobs work inside the sandbox.

_Date: 2026-09-17; issue #1372_

## Name every colliding ADR number in one gen-adr-index run

`validate_unique_numbers()` aborted on the first duplicate ADR number it found.
With a three-way collision that reports two of the three directories, so whoever
renames the pair the error named hits the same error again on a directory
nothing had mentioned. Independent collisions behaved the same way: the second
pair stayed invisible until the first was resolved.

_Date: 2026-09-17; issue #1371_

## Name the fleet sweep's capped workflow listings instead of under-reporting them

The AI-review follow-up on `scripts/fleet-contract-inventory.py` read the
workflow listing as paginated and silently truncated past 30 entries. It is
neither. `repos/{repo}/contents/.github/workflows` returns the whole directory
in one unpaginated response — 49 entries for this repository's own workflows,
with no `Link` header — so `--paginate` would change nothing and there is no
page to follow.

_Date: 2026-09-17; issue #1370_

## The contract is exercised against synthetic adopters before it is published

A reusable workflow is only ever exercised by real callers, after publication,
so a contract defect reaches every adopter before anyone is positioned to see
it. `Verjson/verjson-ci#184` is the worked example: `node-ci.yml` shipped in a
state where a deferred head reported the required `ci / build-test` context
SUCCESS having executed no test, lint, type check, or repository-local contract
guard. It was green the whole time.

_Date: 2026-09-17; issue #1369_

## Record the org contract distribution decision and add read-only fleet drift detection

ADR 0185 records the decision to give the org automation contract a **release
identity instead of a commit identity**. A `uses:` SHA is a content address: it
cannot state which contract an adopter is on, whether that contract is behind, or
whether it is still supported. Every drift-detection and regeneration mechanism
previously proposed is a compensating control for that one missing property.

_Date: 2026-09-17; issue #1366_

## Assert that verification executed, not merely that nothing is red

The organization's pre-merge assertion collapsed four materially different head
states into one bare `false`: a genuinely failing required check, an ADR 0178
deferral where nothing ran, a required check that never reported at all, and a
red advisory check. The cheapest way to clear an opaque `false` is to widen the
accepted-conclusion list, and the first entry anyone widens it to accept is the
`deferred-ci` `FAILURE` that makes an otherwise-green Renovate pull request look
broken — which restores exactly the `Verjson/verjson-cli#251` condition ADR 0178
was written to end.

_Date: 2026-09-17; issue #1364_

## The authorization arm audit reaches adopter conformance

`scripts/ai-review-required-workflow-audit.py` now audits the adopters the arm
governs, not only the organization objects that arm them. Making the audit
runnable and scheduling it (#1406, #1413) fixed the first three items of #1404;
this is the fourth, and the one `Verjson/.github#1401` needs.

_Date: 2026-09-17; id:20260917T224500Z_

## The compatibility public cache fails closed, and its assertions hold again

An independent review of the verified public npm cache handed to the
compatibility sandbox (#1372, landed in #1396) returned after the repository's
automation had already merged it. Six findings are resolved here.

_Date: 2026-09-17; id:20260917T190000Z_

## A digest pin now fails closed instead of pinning the empty-string hash

`scripts/gen-changelog-caller.sh` computed `ADR_INDEX_SHA256` and
`ADR_INDEX_TEST_SHA256` by piping a resolver into `digest_of`, so a resolver
that refused produced an empty stream and pinned `e3b0c442…`, the SHA-256 of
nothing. No adopter file can ever match that digest, which made the emitted
contract test unsatisfiable while the generator's own `[ -n … ]` guards looked
satisfied. Both pins now go through `digest_of_resolved`, which requires the
resolver to exit zero, write a non-empty file, and yield a non-empty digest,
and reports the resolver's stderr when it does not.

_Date: 2026-09-17; id:20260917T174500Z_

## Record that required status checks bind through repository properties (ADR 0186)

`Verjson/verjson-ci#186`, `Verjson/verjson-compliance#23`, and
`Verjson/verjson-compliance-schema#12` each reported a repository whose `main`
ruleset declares no required status checks, so a green rollup gated nothing.

_Date: 2026-09-17; id:20260917T161500Z_

## The conformance model no longer manufactures the evidence it demands

An independent review of the conformance matrix (#1369, landed in #1386)
found the harness reproducing,
in itself, the defect class it exists to catch. Two modelling errors:

_Date: 2026-09-17; id:20260917T154000Z_

## The fleet inventory no longer reports an absent workflows directory as a gap

`scripts/fleet-contract-inventory.py` counted every repository whose
`.github/workflows` listing 404s as `unreachable`. Fourteen fleet repositories
answer that way — eleven have no workflows directory and three have no commits
at all — so the sweep reported fourteen gaps it did not have and exited nonzero
for a benign reason.

_Date: 2026-09-17; id:20260917T061500Z_

## Reconcile both merge-gate inventories and require the endpoint's attestation

The independent review of the previous merge-gate hardening found the page-walk
reconciliation had been applied to only one of the two inventory endpoints.
Commit statuses are the half Gate C reads a stranded `failure` from, so a short
walk there hides precisely what that gate exists to see. Both fetches now share
one `reconcile_pages` helper.

_Date: 2026-09-17; id:20260917T041500Z_

## Report a capped directory listing as its own gap kind

Review follow-up to #1373. The capped-listing gap was appended to `unreadable`,
whose members are paths whose blob could not be read and whose length is printed
as `unreadable_files=`. A capped directory is neither a path nor a file — the
file names themselves are what went missing — so one counter was reporting two
different conditions. It now has its own `incomplete_listings` list, its own
counter, and its own line, still folded into the non-zero exit. The entry is
printed one per line because it is the only gap that contains spaces, which
would otherwise break the space-joined "one token per gap" convention.

_Date: 2026-09-17; id:20260917T034500Z_

## Close the fail-open gaps the merge gate's hardening claim outran

Re-reviewing `scripts/assert-mergeable-head.sh` found four ways it could still
answer a question it had not actually asked.

_Date: 2026-09-17; id:20260917T033000Z_

## Release actions can derive a blank version

Generated Release workflows now accept a blank version and resolve the selected
stream through the canonical read-only release plan. The workflow summary shows
the previous release, bump rationale, selected fragments, assembled notes, and
source commit before the existing verify, snapshot, and publish stages run.

_Date: 2026-09-15; issue #1360_

## Fix nested App-key policy workflow startup

Remove unsupported job-level `permissions` mappings from jobs that call the nested
App-key environment workflow. The workflow-level `actions: read` and `contents: read`
boundary remains authoritative, while GitHub can now plan the reusable workflow and
create its credentialless policy job.

_Date: 2026-09-15; issue #1356_

## Restore explicit read-only permissions on nested policy calls

Restore the explicit `actions: read` and `contents: read` permissions on
reusable-workflow caller jobs. GitHub supports this caller keyword, and
retaining it makes the intended token boundary explicit while #1356 remains
under investigation.

_Date: 2026-09-15; id:20260915T083104Z_

## Check ADR coverage claims against the tests that exist, and restore two properties #1329 dropped

#1329 deleted eight `scripts/ci-gate` harnesses and amended three ADRs. Five more kept
making present-tense claims about the same deleted files, and two of the three amended
ones kept a live claim in their own body. Nothing caught it, because the sweep was
manual and a manual sweep over 175 ADRs misses.

_Date: 2026-09-14; issue #1331_

## Define owner-approved delegation for agent-operated provisioning

An owner can now delegate a reviewed provisioning plan to a named executor through a grant document. `config/provisioning-delegation-contract.json` defines the issuer/executor boundary, the effect vocabulary and the owner-gated subset it derives (`ownerConsentRequired: "all"`, so a new effect cannot be added to the vocabulary and silently escape its gate), the immutable `Verjson/verjson-ci` contract pin, evidence, expiry and revocation requirements; `scripts/provisioning-delegation-validate.py` turns a grant into an authorization receipt offline, with no network call and no credential read. One reviewed grant authorizes every action it lists without per-call reconfirmation. A contract or inventory that cannot be read as a policy document exits 2, which is distinct from the exit 1 that reports a withheld authorization; every declared effect list is rejected unless it is a list of names, because `in` and `set()` both accept a bare string and degrade to substring and character tests that answer permissively rather than raising. Model output, unattended mode, an executor-authored approval field and any unrecognized field are rejected, and every owner-gated effect needs its own consent record with a GitHub-hosted reference and approvers excluding the executor.

_Date: 2026-09-14; issue #1325_

## Require a NEXT fragment for production source changes in check-pr

`changelog.py check-pr` required a NEXT fragment only when the diff touched a
dependency manifest or lockfile, so a change to production source code — the
reproduction in #1324 is `tools/subscriber-gateway/gateway.mjs` in
`Verjson/verjson-ci` — landed on `main` with nothing in the running log and
exit 0.

_Date: 2026-09-14; issue #1324_

## Package-backed portable CI adapter

`Verjson/.github` now contains the reviewed thin facade for the
`Verjson/verjson-ci` portable CI package. The facade pins package source to an
immutable commit, requires a complete-release OCI image digest, and documents
separate GitHub and GitLab identity/credential boundaries. Existing GitHub
`node-ci.yml` remains the shadow/canary fallback; this does not claim a live
production cutover before signed release identities and authenticated
deployment evidence are available.

_Date: 2026-09-14; id:2c8d8f1_

## Audit the declared App key environment and report why a verification failed

Part of [#1285](https://github.com/Verjson/.github/issues/1285).

_Date: 2026-09-14; id:20260914T144500Z_

## Widen the App key workflow scan to every spelling a binding can take

Part of [#1285](https://github.com/Verjson/.github/issues/1285).

_Date: 2026-09-14; id:20260914T143000Z_

## Declare broad App key copies withdrawn

Part [#1285](https://github.com/Verjson/.github/issues/1285).

_Date: 2026-09-14; id:20260914T091501Z_

## Publish a deferred org CI run as its own failing check instead of a silent green

`node-ci.yml` and the generated `node-ci-protected.yml` now publish a separate
`deferred-ci` check that runs only when `eligibility` defers the lane on a pending
`renovate/stability-days` status, and that check fails. The required `ci / build-test`
context is unchanged and still reports success on a defer, so no held Renovate pull
request wedges — but a deferred head is no longer indistinguishable from an executed,
passing one in `statusCheckRollup`.

_Date: 2026-09-14; id:20260914T091500Z_

## Enumerate every App private key binding so an unconfined one fails CI

Part of [#1285](https://github.com/Verjson/.github/issues/1285).

_Date: 2026-09-14; id:20260914T090000Z_

## Report which container-deployment contract assertion failed, and what it expected

`scripts/gen-container-deployment.sh contract-test` now generates a contract test that
names the failing assertion and prints its expected and observed values, instead of
exiting 1 with no output. A delivery leg on `Verjson/verjson-git-runners#207` had to
bisect the generated script by hand to discover which precondition was unmet, because
every assertion was a bare `test`, `jq -e`, or `grep -q` under `set -e`.

_Date: 2026-09-14; id:20260914T083000Z_

## Correct the Node-floor preparation digest, rule guard and contract pin

`scripts/node-floor-ruleset.py` now derives `baselineDigest` from the validated reviewed
baseline instead of the caller's observation. Because `render` tolerates extra top-level
keys the live API returns, the same live ruleset previously produced one digest through
`dry-run` and a different one through `render` while `ruleset` and `baselineProducerAppId`
were identical, so a field named for the reviewed baseline reported provenance of the read
that `baselineSource` already carries. Two proposals of the same policy now compare equal
by digest, and tests hold the two paths to one value.

_Date: 2026-09-14; id:20260914T075211Z; refs #1274_

## Derive the container-deployment contract verdict from the diagnostics it reports

`scripts/gen-container-deployment.sh contract-test` stated the nine
`container-deployment.json` preconditions twice: once as a `jq -e` predicate that decided
the verdict, and once as a `jq` program that reported which field failed. A condition
added to the predicate but not to the report would have made `join("; ")` return the empty
string, and the adopter would have seen `container-deployment contract FAILED: ` with
nothing after it — the zero-diagnostics failure the reporting was added to remove.

_Date: 2026-09-14; id:20260914T074500Z_

## Refresh the reviewed Node-floor baseline to the activated producer bindings

The reviewed `core-checks-node` baseline snapshot behind the opt-in Node-floor
preparation still recorded the pre-activation projection of organization ruleset
`20515817`, whose three required contexts carried no producer App binding. ADR 0173's
authorized rollout bound `ci / build-test`, `ci / eligibility` and `changelog-contract`
to GitHub Actions App `15368` at 2026-09-10T15:24:16.455Z, so every `render` and
`dry-run` against live policy failed closed on baseline drift. The snapshot now records
the verified postimage; the delta is exactly those three `integration_id` additions, with
identity, selectors, enforcement, branch conditions and bypass actors byte-for-byte
unchanged. Preparation stays read-only and still emits a disabled candidate.

_Date: 2026-09-14; id:20260914T070454Z; refs #1274_

## Declare the conditional Node-floor required-check lane

`.github/required-check-contract.json` now declares `core-checks-node-floor` — selected
by `verjson-stack=node`, `verjson-core-checks=enforced` and the new
`verjson-node-floor=node22` property, requiring only `ci-node22 / build-test` and
`ci-node22 / eligibility` — so an organization lane that only some repositories carry is
readable where every other lane is declared, instead of living inside one preparation
script. `stacks.node.contexts` is untouched and the property defaults to `disabled`, so
no Node repository acquires a floor requirement and no repository-level validation
changes; the declared entry is inert for the audit and rollout paths, which consume the
plan by the single declared rollout name.

_Date: 2026-09-14; id:20260914T070453Z; refs #1274_

## Refuse approved organization custody for environment-only App keys

The organization secret-scope policy now declares custody for every App private key. `AI_REVIEW_APP_PRIVATE_KEY`, `MERGE_APP_PRIVATE_KEY` and `RELEASE_APP_PRIVATE_KEY` may appear only as `environment-only-migration-residue` with a recorded withdrawal contract, tracking issue and date; `RENOVATE_COMPATIBILITY_APP_PRIVATE_KEY` and `DEPENDENCY_SUPERSESSION_APP_PRIVATE_KEY` keep their current organization contract by declaring it explicitly; any other `_APP_PRIVATE_KEY` name fails closed as an unrecognized role before any GitHub read.

_Date: 2026-09-14; id:20260914T070000Z; refs #1326_

## Group pydantic and pydantic-core so a concurrent update to both bumps them together

`pydantic-core` is `pydantic`'s compiled Rust backend; the two are version-coupled
upstream, and bumping only one risks pairing an unsupported combination.
`verjson-editor#123` bumps `pydantic-core` alone in
`benchmarks/unstructured/requirements.txt` (pinned at `pydantic==2.13.5` /
`pydantic-core==2.46.5`). A new `packageRules` entry groups `pydantic` and
`pydantic-core` across `pip_requirements`, `pep621`, and `poetry`, matching the
existing "github actions digests" group's rationale for coupled dependencies.

_Date: 2026-09-13; id:20260913T173500Z_

## Bound deployment transport replay inventory requests

Bound the GitHub workflow-run replay inventory to 40 list requests per probe, shared by the complete baseline and post-dispatch reconciliation. A trusted history that cannot fit the remaining budget fails closed before dispatch, preventing paginated polling from amplifying provider rate-limit load.

_Date: 2026-09-12; issue #1333_

## Refuse legacy bootstrap copies of environment-only App keys

The canonical adopter bootstrap now uses an exact credential map: `NODE_AUTH_TOKEN` is the supported non-App organization credential, while App private-key entries require the declared canonical role and exact `<role>_PRIVATE_KEY` name. Aliases such as `MERGE_TOKEN`, renamed private-key-like names, and unrecognized keys fail before any GitHub or generated-output mutation; review, merge and release roles retain an explicit `provisioning_required` result for their canonical credentials. Roles without an environment-only custody decision retain their exact typed organization-secret path; existing broad copies remain untouched.

_Date: 2026-09-12; issue #1326_

## Confirm the terminal-merge credential shape needs no change

Design pass on the terminal merge credential (#1323): the canonical
`ai-privileged-merge.yml` already binds the terminal merge step exclusively to a
`merge-app` GitHub App installation token (ADR 0120, hardened by ADR 0166) with no
`ORG_ADMIN_TOKEN`/PAT fallback. A PAT-attributed automation merge reported by an
adopter traced to that repository's caller being pinned to a pre-ADR-0120 revision — a
consumer-side staleness gap, not a canonical-contract defect. See ADR 0120's 2026-09-12
addendum; the adopter's specific remediation is tracked in that (private) repository's
own issue tracker, not named here.

_Date: 2026-09-12; issue #1323_

## Triage the unregistered ci-gate tests and guard against new orphans

Nine `scripts/ci-gate/*.test.sh` files were registered in no `actions-ci` group and
so never ran in Actions. `dispatch-permission.test.sh` is rewritten against the
current five-job merge-gate shape and registered; the other eight assert a workflow
topology ADR 0079 removed and are deleted. `scripts/actions-ci-groups.test.sh` now
fails CI when a ci-gate test is reachable from neither the manifest nor the hosted
compatibility job, so the drift cannot recur silently.

_Date: 2026-09-12; issue #1320_

## stamp selected packages before release builds

node-release now stamps every selected package with the dispatched version before any selected package build, so generated component callers cannot publish stale dist output.

_Date: 2026-09-12; id:20260912T230000Z_

## validate package selections for every release caller

The generated release contract now derives package selections for Node, artifact, and snapshot callers independently, so sibling release workflows are validated against their own version stamping behavior.

_Date: 2026-09-12; id:20260912T200000Z_

## build every selected Node package before script-disabled publication

The reusable node-release contract now builds each selected nested package after stamping and before npm pack, preserving root release callers while allowing independently generated component callers to publish their own dist output.

_Date: 2026-09-12; id:20260912T180000Z_

## Revalidate refreshed container deployment release evidence

The canonical container deployment workflow now validates refreshed manifest identity and deployed digest evidence against the immutable plan before advancing to the next host. Dishonest or stale second-host responses fail closed before mutation, while bounded replay and credential separation remain intact.

_Date: 2026-09-12; id:20260912T055348Z_

## Harden deployment transport and pre-mutation runner admission

The canonical deployment controller now validates online, idle, admitted runners against the reviewed group, labels, and tools before retaining admission or mutating any host. Workload evidence and probe children receive a minimal allowlist without `HOME`, `SSH_AUTH_SOCK`, deployment CLI locators, or deployment credentials; the mutating CLI retains separate explicit provider and runner-control environment. The GitHub canary broker now requires a bounded paginated run inventory whose trusted `total_count` matches every record before replay detection or dispatch polling. Regenerate and repin the complete generated deployment artifact set, including controller, transport, and contract test, to a single merge commit. Implements existing ADR 0078/0144 boundaries for #1281 and #629; no new architectural decision is introduced.

_Date: 2026-09-12; id:20260912T033225Z_

## Record operational alerting expectations for inherited secret context

Amend ADR 0171 with the operational control the AI-review follow-up asked for:
inherited secret delivery is monitored at runtime for access patterns the reviewed
workflows cannot legitimately produce, and any such observation is an incident that
pauses dispatch. No transport, policy or pin decision changes.

_Date: 2026-09-11; issue #1297_

## Pin the gate-rearm policy call for required-workflow execution

An isolated canary proved a ruleset required workflow cannot resolve a relative
reusable call: revision b050 startup-failed on both selectors while the identical
content ran locally, so the shared rule stays frozen on c597. Replace gate-rearm's
relative app-key-environment call with an absolutely pinned reference and record
the corrected invocation semantics, including main-only environment admission for
develop-targeted pull_request_target runs, in ADR 0171.

_Date: 2026-09-11; id:20260911T223000Z; refs #1285_

## Promote the unregistered ci-gate test triage to a tracked issue

The Active Issues entry for unregistered `scripts/ci-gate/*.test.sh` files is now
tracked as #1320; the session-loaded list keeps a one-line pointer instead of the
full drift inventory.

_Date: 2026-09-11; id:20260911T220000Z; refs #1320_

## Ignore remaining Claude Code tooling residue files

Two residue files the scoped Claude Code ignore block missed —
`.claude/.headroom_wrap_owners.json` and `.claude/scheduled_tasks.lock` — kept
`git status` reporting an untracked `.claude/`, a standing false signal competing
with real uncommitted work. Add them to the existing scoped block.

_Date: 2026-09-11; id:20260911T210500Z_

## Record verified App-binding activation and fresh PR-head controls

Record in ADR 0173 the 2026-09-10 activation of the reviewed producer App bindings on
both organization rulesets, with exact preimage/postimage verification and unchanged
non-selected policy, and the 2026-09-11 post-activation controls: every fresh check
run for all four contexts produced by the bound App, fresh passes on merged heads for
all four contexts, and fresh failures blocking merge for both rule objects. The
residual — no natural failure yet for two contexts sharing the proven rule, and no
staged wrong-app control — is recorded explicitly instead of manufactured.

_Date: 2026-09-11; id:20260911T204500Z; refs #1303_

## Define the zero-additional-cost Nexus CE subscriber boundary

Supersede the Pro-dependent distribution decision with a separate server-enforced subscriber read gateway on the existing paid host. Define expiration, current entitlement and revocation checks, origin isolation, attribution and restore acceptance. Route gateway and deployment implementation to the existing CI and CLI PMs; no live credentials, infrastructure or customer migration change.

_Date: 2026-09-11; id:20260911T120000Z; refs #1186_

## Require canonical CODEOWNERS entry spelling

Check the exact .github directory and CODEOWNERS filename spelling so case-insensitive lookups cannot falsely pass canonical ownership conformance. Preserve existing fallback checks without scanning unsupported locations.

_Date: 2026-09-10; issue #1312_

## Carry verified zero-provider recovery into gate admission

Reverify the immutable zero-provider proof at gate admission before accepting its exact retained failed authorization. Preserve pending-only ordinary admission and refusal after any provider boundary. Skip replay cleanup when trusted helper setup never succeeded, without masking missing helpers after setup.

_Date: 2026-09-10; issue #1309_

## Bind Node-floor policy reads to GitHub.com

Explicitly select GitHub.com for fixed read-only policy requests so ambient GH_HOST cannot redirect the target. Preserve normal environment handling and GET-only behavior; regress conflicting host settings.

_Date: 2026-09-10; issue #1305_

## Prepare exact producer App bindings for organization checks

Prepare before/after/rollback artifacts for four missing producer bindings across two organization rulesets, preserving their targeting, contexts and bypasses. A deterministic renderer and fixed-host GET-only dry-run validate exact deltas against privately retained cohort evidence; public metadata contains only aggregate counts and evidence digests. Activation and fresh operational acceptance remain separately authorized.

_Date: 2026-09-10; issue #1303_

## Sealed environment App bootstrap

Add a temporary protected-main bootstrap that encrypts existing App keys with reviewed environment public keys. A local authenticated importer checks workflow/artifact provenance and fresh main-only destinations before writing ciphertext; separate environment-bound App scope proofs and metadata receipts gate completion. Broader secret copies remain available until a separately reviewed cohort migration.

_Date: 2026-09-10; issue #1291_

## Select exact nested Node release packages

Add repeatable `--only-package-dir` selection to generated release callers and their contract tests. Nested-only releases stamp and publish only the selected packages; existing root defaults and additive `--package-dir` callers retain their behavior. Reject mixed selection modes, empty or duplicate paths, and paths outside the normalized repository-relative contract.

_Date: 2026-09-10; issue #1286_

## Require main-only environment storage for privileged App keys

Move release, merge and AI review key consumption inside caller-owned main-only
environments. Generated callers select role environments instead of forwarding
App private keys; model/npm credentials retain explicit grants. Validate live
environment policy before admitting key jobs, preserve rearm event filtering and
provide a metadata-only operator audit plus staged shared-ruleset rollout.
Consumers must provision environments and regenerate before broad copies can be
removed; merging this contract alone does not complete exposure removal.

_Date: 2026-09-10; issue #1285_

## Reject invalid ADR generator arguments and clean verification temporaries

The canonical ADR index generator rejects unknown modes and extra arguments with exit 2 before changing files. Verification and regeneration now clean their rendered temporary files on success or failure, while preserving the existing index on rendering errors and retaining locale-independent sorting.

_Date: 2026-09-10; issue #1284_

## Add a credential-separated deployment GitHub broker

Generate a parent-owned manifest and canary broker with dedicated single-repository App authority, complete transaction request binding, exact attested manifest bytes, durable dispatch intent and authenticated runner/run/job/artifact receipts. Mocked adversarial tests cover credential isolation, stale or mismatched evidence, ambiguous dispatch and artifact tampering. This is partial capability: CLI-cloud #504 owns the missing read-only host export, and controller integration, protected issuer provisioning and live deployment/rollback acceptance remain required under ADR 0170.

_Date: 2026-09-10; issue #1281_

## Prepare an opt-in Node22 required-check lane

Add deterministic offline rendering and read-only dry-run preparation for a disabled Node22 floor ruleset selected by an organization-controlled disabled/node22 property. Validate the existing Node baseline and preserve its bypass actors and selectors. Live activation, consumer proof and repository-rule retirement remain separately gated; consumer required-check lists remain mandatory.

_Date: 2026-09-10; issue #1274_

## Identify generated ADR index copies and remove reported shell diagnostics

Add the generated-file warning and regeneration command to the canonical ADR index source copied into adopters. Brace the emitted contract-ref regex variable to avoid ShellCheck SC1087, and express missing-marker rejection as an explicit conditional without SC2015. Both single-marker failures remain non-mutating; generated artifacts stay pinned to exact canonical bytes.

_Date: 2026-09-10; issue #1273_

## Generate the declared organization code owner

Add canonical generation and exact-byte conformance for all-path ownership by @Verjson/devs, and adopt it in the organization repository. Reject missing, overridden or drifted ownership. Record team-access eligibility and consumer rollout as separate verification gates; no permissions or review requirements are changed.

_Date: 2026-09-10; issue #1272_

## Attribute grouped Docker image variants independently

Renovate attribution now distinguishes complete package/version transitions, so grouped Alpine and Bookworm updates for the same image produce one fragment containing both changes. Exact duplicate rows still fail, including equivalent linked and bare package cells. Regression coverage uses the real infra #229 table; ADR 0104 records the preserved trust boundary.

_Date: 2026-09-10; issue #1271_

## Require release context for conventional image dependency files

The dependency guard now covers Dockerfile and Containerfile variants and conventional Compose YAML manifests. Actual image-digest changes fail without a new NEXT fragment and pass with one; documentation and backup lookalikes remain outside the boundary. ADR 0038 documents the filename-based scope and canonical consumer adoption requirement.

_Date: 2026-09-10; issue #1270_

## Bind the release caller shape check to inherited environment context

Update the independent release caller shape validator to require the ADR 0171 inherited context together with release-app, and reject a mutation removing inheritance. Preserve publication ordering, immutable pins and the separate explicit node publication token grant.

_Date: 2026-09-10; id:20260910T042000Z_

## Restore reusable App-key delivery through proven inherited context

Require inheritance on pinned generated and canonical nested App-key reusable edges after the environment-only sentinel resolved only with that transport. Preserve fixed role environments, main-only admission and event-before-mint guards, document broader inherited-context risk in ADR 0171, and retire the completed diagnostic probe. Broad App copies/shared workflow pins remain unchanged pending #1285 cohort rollout.

_Date: 2026-09-10; id:20260910T040000Z_

## Retire the verified App-key bootstrap during self-adoption

Retire both temporary bootstrap workflows and their migration-only executable, dependency, configuration and test surfaces after all three environment App proofs succeeded. Preserve ADR 0169 and immutable receipt/source pointers. Record fulfilled canonical provisioning and the protective shared-workflow freeze; broader secret copies and the shared pin remain unchanged pending the complete #1285 cohort rollout.

_Date: 2026-09-10; id:20260910T033000Z_

## Probe reusable environment secret context without App credentials

Add a temporary four-case noncredential probe for the empty reusable App-key context observed after #1285 self-adoption. Compare declarations and explicit-empty/inherited grants at one reviewed main revision, retaining native environment denial controls and boolean-only output. No production caller, key or environment policy is changed; runtime observations must precede selecting a fix.

_Date: 2026-09-10; id:20260910T030800Z_

## Preserve renamed repository provenance and strict receipt types

Separate the deployment broker's current API repository name and stable ID from historical signed-source identity. The exact runner v0.2.1 manifest proves renamed repositories retain strict source and signer verification. Reject numeric health flags, Boolean attempts and floating-point IDs at receipt and API boundaries; adversarial tests and ADR 0170 record both review corrections.

_Date: 2026-09-10; id:20260910T025044Z_

## Register the four reviewed bootstrap hosted jobs

Register the exact sealing and three environment proof job identities from ADR 0169 in the runner-routing policy's fixed hosted inventory. This restores CI conformance for #1291 without allowing other jobs in either workflow to inherit a hosted exception.

_Date: 2026-09-10; id:20260910T023000Z_

## Require a protective pin before shared App-key contract adoption

Correct the shared arm rollout record: ruleset 20722935 follows main without an
immutable SHA. Require a reviewed one-field freeze to the current protected
workflow before this contract merges, preserving all existing selectors and
bypasses. Keep the live freeze receipt separate from the later cohort-wide
environment-key cutover and broad-secret removal.

_Date: 2026-09-10; id:20260910T020817Z_

## Distinguish environment policy records from workflow inputs

Rename the policy response variable so ShellCheck no longer mistakes the
uppercase workflow environment selector for a misspelled local variable.
Keep the exact main-only policy checks and credential boundary unchanged.

_Date: 2026-09-10; id:20260910T015420Z_

## Align the Authn required workflow with its supported compatibility baseline

The canonical Authn type-surface workflow now requests the published 2.0.0 baseline permitted by the consumer's protected package policy, replacing the stale 1.0.3 request that failed the controlled activation trial. Strict validation and policy regression coverage retain the exact allowed baseline; ADR 0168 preserves the activation, rollback, and live-receipt gates for the corrected pin.

_Date: 2026-09-10; id:20260910T014122Z_

## Record controlled Authn required-workflow activation

Record the explicit owner-authorized activation order for organization ruleset
21750617, with exact pre/postimage verification, an unchanged workflow pin and
release-only bypass, retained consumer enforcement, and a reviewed rollback.
Live required-workflow and follow-up mergeability receipts remain separate gates.

_Date: 2026-09-10; id:20260910T011537Z_

## Bind runner deployments to the exact attested release asset bytes

Runner deployments accept the original UTF-8 release manifest bytes and verify their digest against the attestation and selected release identity. This fixes admission of published manifests whose whitespace differs from compact JSON, while preserving exact identities during rollback, resume, and reconciliation and rejecting tampered or ambiguous evidence.

_Date: 2026-09-09; issue #1279_

## Stop cancelled DeepSeek reviews before fallback spending

Cancelled reviews no longer reserve or invoke a DeepSeek fallback. Both provider
passes verify the current PR head immediately before invocation and stop after five
minutes, preserving the existing exact-head pass cap and verdict authorization.

_Date: 2026-09-09; issue #1278_

## Admit verified checkout-local npm links in secretless manifests

Restore nested examples that link an internal package from the same checkout,
including file references to the workspace root, and allow empty private-package
approval sets when no private registry download exists. Verify local target
containment and package identity before excluding source records from acquisition;
retain exact registry URL, integrity and per-manifest authorization checks.
Regenerate the protected Node workflow and cover link escapes, substitutions and
empty-approval denial in the registered behavioral suite.

_Date: 2026-09-09; issue #1276_

## Skip body-only review re-arm events before allocating credentials

The canonical AI review arm skips body/base-only PR edits before scheduling its trusted runner or minting an App token. Title edits and other lifecycle events retain the existing hold cancellation, source binding, and review authorization checks.

_Date: 2026-09-09; issue #1275_

## Clarify how to pause before a worker deployment

The deployment runbook now instructs operators to preserve the dry-run plan and withhold the mutating dispatch when pausing before deployment, matching ADR 0144's production environment without a required environment reviewer.

_Date: 2026-09-09; id:20260909T162419Z_

## Document shared development service endpoints

Add a public infrastructure directory with GitLab, Nexus, npm, Docker, PyPI,
and S3 endpoints, client examples, and dated verification boundaries. Keep
private operational inventory and credentials outside this public repository.

_Date: 2026-09-07; issue #1266_

## Record verified development registry provisioning

Document the provisioned npm, Docker, and PyPI endpoints with authenticated
publish/download evidence, access and overwrite boundaries, and synthetic test
cleanup. Record the bounded development proof under the existing Nexus decision
without claiming production identity, subscriber, or recovery readiness.

_Date: 2026-09-07; issue #1264_

## Align consumer runner policy with canonical generated callers

Accept the exact generated changelog PR gate on disposable hosted capacity and the
existing release routing expression without changing generator output or moving
PR-authored code onto persistent runners. Reject modified execution fields and
unreviewed selectors; exercise generated callers in consumer-policy regression tests.

_Date: 2026-09-05; issue #1261_

## Preserve release authorization during authn workflow migration

Keep the sole release-authorization App bypass in the authn ruleset migration so dispatched releases remain possible. Add a guarded staging path that updates the existing organization rule in evaluate mode without activating it, with exact preimage/postimage verification and rejection of widened bypasses.

_Date: 2026-09-05; issue #1258_

## Bridge draft and hold review lifecycle events

Extend the separate generated review caller to deliver ready-for-review, draft,
hold-removal and title-edit events that required workflows cannot dispatch. Preserve
exact-head source authentication and existing authorization policy. Explain the
installation requirement when an arm stops on draft or hold.

_Date: 2026-09-05; issue #1257_

## Refresh active work in repository guidance

Remove completed rollout pointers from repository guidance and update the protected runner deployment blocker from current GitHub evidence.

_Date: 2026-09-04; issue #1255_

## Add bounded secretless dependency and browser caches

Allow failed-job reruns to reuse an immutable earlier-attempt dependency transfer within the same workflow run, add opt-in lockfile-keyed persistence for verified public npm blobs, and add a default-off Playwright browser cache for credentialless secretless jobs.

_Date: 2026-09-04; issue #1246_

## Make compatibility sandbox failures actionable

Give compatibility consumers an isolated writable npm cache, report failed commands with
their exit status and a bounded output tail, and document the top-level bind-mount inode
constraint. Mask ambient npm cache and config paths and reject top-level checkout symlinks
that cannot remain workspace-confined, without weakening the credentialless read-only
sandbox boundary.

_Date: 2026-09-04; issue #1244_

## Isolate lowercase npm configuration paths

Mask lowercase npm user and global config paths and redirect every config-variable case variant to sandbox-owned empty npmrc mounts so compatibility code cannot recover ambient registry credentials through npm's lowercase environment aliases.

_Date: 2026-09-04; id:20260904T125200Z_

## Unify the portable CI engine and forge adapters

Adopt one versioned `verjson-ci` repository for the portable engine, CLI, OCI image,
GitHub and GitLab adapters, local and remote parity, OIDC-secured coordination, ShadScan,
signed releases, and external GitLab mirroring.

_Date: 2026-09-03; issue #1249_

## Expose whether reusable Node CI executed or deferred

Expose the reusable Node CI lane's `should-run` decision so callers can distinguish an executed suite from a successful deferred no-op.

_Date: 2026-09-01; issue #1235_

## node-ci secretless acquisition covers nested manifests with per-manifest package and script authorization

`node-ci.yml`'s secretless mode hardcoded the root lockfile in every
acquisition, packaging, and install path, so a repository whose private
dependencies are not all reachable from `./package-lock.json` had no
credentialless way to obtain them at all. `Verjson/verjson-authn#252` is the
live case: `examples/catalog-google-login/` has its own manifest and lockfile
and depends on a private `@verjson/*` package, so its deliberately
credentialless job only passed on a warm self-hosted runner cache and failed
`E403 read_package` on a cold one. Giving that job a token would have put a
package-read credential in a job that runs untrusted PR-authored lifecycle
scripts — the exact regression this workflow exists to prevent.

_Date: 2026-09-01; issue #1229_

## audit test fixture rejects an unrecognized properties-endpoint shape instead of silently misparsing it

`scripts/required-checks-audit.test.sh`'s `gh` stub derived the repository name
for `GET /repos/{org}/{repo}/properties/values` from `${args[1]}` without
checking the endpoint had that shape. Anything else landing there would still
parse to some string and silently answer with the wrong repository's (or the
shared) properties file instead of failing — masking a fixture/production
drift behind a passing suite for the wrong reason.

_Date: 2026-09-01; issue #1225_

## ADR 0058's live org-audit evidence is now dated and marked as a snapshot

The `#1221` amendment to ADR 0058 quoted a live org-wide audit result
(`conformant=25 nonconformant=3 unclassified=6 unaudited=0 skipped=63`) with no
date attached, which reads as a current fact rather than a one-time measurement
that will drift as repositories are added, reclassified, or brought into
conformance.

_Date: 2026-09-01; issue #1224_

## Renovate-deferred CI is now legible to the pre-merge assertion

`node-ci.yml`'s `build-test` job always reports a conclusion so a held
Renovate PR never wedges the required `ci / build-test` context (#191), but
that meant a stability-window deferral — every step skipped, only the
`Report deferred CI` notice ran — was byte-for-byte indistinguishable from a
real pass at the merge gate. `Verjson/verjson-graphql-conventions#66` showed
the resulting hazard: a lockfile bump with a real, locally-reproducible test
failure carried a fully green `statusCheckRollup`.

_Date: 2026-09-01; issue #1219_

## node-ci secretless dependency acquisition requires exact scope match against trusted repository policy

`.github/workflows/node-ci.yml`'s policy-checked branch of the secretless
dependency validator accepted a caller `approved-internal-scopes` set that was
merely a **subset** of the protected `CI_SECRETLESS_PACKAGE_POLICY` scopes.
Internal-vs-public routing (`path_is_internal` in the lock-scan loop, and the
matching `found`-set filter) is computed from that same caller-supplied
`scopes` value, not from the validated policy scopes. A caller that omitted a
protected scope from its own declaration therefore stopped routing packages
under that scope through the internal-package checks entirely — such a
package would resolve from the public registry unrouted and unchecked, a
dependency-confusion shape. It was inert today because every consumer policy
declares exactly one scope, so subset and equality coincide; it becomes
reachable the moment a policy protects more than one scope.

_Date: 2026-09-01; issue #1217_

## the audit byte-compares the generated changelog-contract.yml (pr-gate) artifact, not just its shape

`scripts/gen-changelog-caller.sh` has produced a fifth generated artifact since
#935 — the `pr-gate` mode that writes `.github/workflows/changelog-contract.yml`.
`generated_contract_identity_for_repo` byte-compared the other four generated
artifacts against fresh generator output, but never this one: the audit only
checked its *shape*, via `required-checks-workflow.py` (does some unconditional
job run the contract test on `pull_request`). A hand-written job satisfying that
shape passed — even though the generated file is what carries the pinned
runner selection and the `persist-credentials: false` hardening from #959. Four
repositories (`verjson-authn`, `verjson-graphql-conventions`, `verjson-infra`,
`verjson-leads`) carried exactly this kind of hand-written duplicate,
undetected, for months.

_Date: 2026-09-01; issue #1212_

## Define the measured GitLab CI migration contract

Define a phased, reversible migration of economically justified CI workloads from GitHub Actions to GitLab, preserving exact-head checks, trust boundaries, parity evidence, and a measured total-cost objective.

_Date: 2026-09-01; issue #1209_

## Add a release-snapshot caller mode for adopters that publish nothing

`scripts/gen-changelog-caller.sh` gains `release-snapshot`, a snapshot-only
release caller for an adopter that publishes nothing from its release workflow
but still cuts versioned releases — concretely, a repository whose container
images ship from a separate, independently triggered workflow. It keeps
`release-node`'s immutable `verify` → `snapshot` boundary unchanged and reduces
`publish` to creating or updating the tag's GitHub Release from the immutable
`CHANGELOG/<version>.md`, with no build matrix, no package publication and no
attached artifacts.

_Date: 2026-09-01; issue #1206_

## Reconcile derived release inputs before minting the release credential

Container releases can now reconcile derived build inputs — a Dockerfile base pin, a
deployment values file — to the just-published immutable digest inside the same release
commit and tag, through an opt-in hook that runs after the provenance-verified manifest
exists and before the release App token is minted.

_Date: 2026-09-01; issue #1203_

## Generate the canonical npm pack receipt parser

Own the type-surface contract's `npm pack --json` parser centrally and generate both its
consumer helper and byte-identity test from an immutable contract revision. The parser
accepts npm 11 array receipts and npm 12 package-keyed object receipts, while rejecting
missing or unsafe filenames, non-tarballs, multiple entries, and wrong-package tarballs
before a consumer joins or installs the reported path. npm 12 object receipts must also
carry the expected package as their sole own key and agree with the entry's package name.

_Date: 2026-09-01; issue #1188_

## Replace the Cloudsmith decision with a gated Nexus migration architecture

Supersede the unprovisioned Cloudsmith rollout decision with a self-hosted Sonatype Nexus Repository architecture that defines trust boundaries, operational ownership, non-production proof, byte-identical dual publication, recovery, rollback, and explicit human gates.

_Date: 2026-09-01; issue #1186_

## Ignore the live Headroom settings lock

Keep Headroom's per-session settings lock from leaving every otherwise clean checkout dirty during repository preflight and terminal workspace sweeps.

_Date: 2026-09-01; id:20260901T181100Z_

## require-verified is a positional flag, not an inheritable environment variable

`scripts/required-checks-audit.sh` read `RCA_REQUIRE_VERIFIED` as a plain
environment variable. A value exported by an unrelated parent shell, or
inherited across a CI step boundary, would silently flip the read-only
org-wide report to fail-closed with no caller ever having asked for that —
and the reverse is just as real: an ambient `RCA_REQUIRE_VERIFIED=true` could
mask `required-checks-rollout.sh`'s own explicit intent to require positive
verification before applying a rule.

_Date: 2026-08-31; issue #1223_

## A skipped repository is not a pass for a caller about to apply a rule

`scripts/required-checks-audit.sh` skips a repository whose stack declares no
required contexts and still exits 0. That is right for the org-wide report,
whose question is "which repositories would be wedged?" — a stack that requires
nothing cannot be.

_Date: 2026-08-31; issue #1221_

## De-escalate the authn type-surface required workflow to evaluate

- Move organization ruleset `21750617` (`authn-type-surface-required-workflow`) from
  `active` to `evaluate`. The pinned workflow reads
  `.github/ci/type-surface-base.json` and a `test:type-surface-compatibility` script that
  exist only on the held draft `Verjson/verjson-authn#251`, so every run failed at
  `Resolve immutable auxiliary source`. A required-workflow rule contributes required
  checks and this ruleset has no bypass actors, so that failure blocked every pull request
  in `verjson-authn` — including the ones that would have supplied the missing artifacts.
- Nothing else about the ruleset changed. The workflow pin `f2f425e9`, the repository and
  ref conditions and the empty bypass list were captured before the change and verified
  byte-identical after it. The workflow still runs and still reports on every pull request;
  it no longer gates merges.
- Re-activation is tracked in #1215 and gated on the prerequisites being present on
  `verjson-authn` `main`, one green run on a real pull request head, and a confirmation
  that a later pull request is still mergeable. ADR 0155 records the decision and the
  general rule: an organization-owned required workflow starts in `evaluate` and is
  promoted on evidence, never armed ahead of the consumer artifacts it reads.

_Date: 2026-08-31; issue #1215_

## Pin the ADR index sort to the C collation

`scripts/gen-adr-index.sh` sorted the ADR directory list twice under the ambient
locale, so the ordering of the generated table depended on `LC_ALL`/`LC_COLLATE`
in the environment that happened to run it. Both sorts are now pinned to
`LC_ALL=C`, and `scripts/ci-gate/gen-adr-index.test.sh` regenerates the index
under a UTF-8 collation and asserts `--check` still passes.

_Date: 2026-08-31; issue #1214_

## Audit skips a stack that declares no required contexts

`scripts/required-checks-audit.sh` resolved a stack's core contract with a single
`jq -er '.stacks[$stack].contexts[]'`, which cannot distinguish a stack the
declaration never mentions from one it declares with an empty context list: both
produce no output and a non-zero exit. The `none` stack is the second kind, so an
unscoped run aborted at whichever `none` repository sorted first and reported no
per-repository results at all — the workaround being to compute the repository
selection out of band and pass it back through `RCA_REPOS`.

_Date: 2026-08-31; issue #1213_

## Stop reading a second reusable-workflow caller as a job-name violation

- Scope the core-checks audit's stack-caller check to the job that actually publishes the required contexts, so a repository that calls the same reusable workflow again under another job name is no longer reported nonconformant.

_Date: 2026-08-31; issue #1210_

## Parse Renovate package link lists

Accept Renovate's comma-separated source and changelog links in package cells while continuing to reject malformed decoration and non-HTTPS targets.

_Date: 2026-08-30; issue #1204_

## Isolate each protected candidate script runtime cache

Run every candidate script in a verified Bubblewrap mount and PID namespace with a
minimal read-only system runtime and a fresh bounded, digest-verified cache copy, then clean it
on completion, failure, or interruption so one script cannot mutate another script's
dependency evidence or leave detached descendants.

_Date: 2026-08-30; issue #1201_

## Remove credential keys from protected candidate execution

- Remove GitHub, npm, cloud-provider, and Actions OIDC credential keys from every protected Node candidate execution route instead of exposing empty values.
- Preserve immediate authenticated live-PR revalidation and the byte-identical legacy Node workflow.
- Reject non-empty protected `schema-dir` inputs and omit the unsupported credentialed schema install route.

_Date: 2026-08-30; issue #1198_

## Authenticate required-workflow identity overrides

- Generate a separately permissioned protected Node CI variant while preserving the legacy reusable workflow byte-for-byte.
- Bind required-workflow identity to the current authenticated Actions run and live numeric pull request before any auxiliary/package credential use and before every candidate execution route.
- Bind candidate checkouts to the immutable admitted head and fail closed on ambiguous, stale, malformed, partial, or ambient-auth identity evidence.

_Date: 2026-08-30; issue #1195_

## Align the cli-projects verifier Node floor

Derive the protected cli-projects package-surface engine requirement from the
strict required-workflow Node configuration. The verifier now accepts the v1
`>=24.19.0` floor used by its Node floor lane and rejects the retired
`>=20.20.2` contract, with a synchronization regression covering both surfaces.

_Date: 2026-08-30; issue #1193_

## Accept canonical AI-review follow-up aliases

Normalize the bounded `suggestion` and `recommendation` provider aliases into the
canonical follow-up `note` field. Preserve fail-closed rejection of conflicting aliases,
unknown response fields, and injected nested payloads, with replay coverage for both
captured DeepSeek response shapes from run 33293783983.

_Date: 2026-08-30; issue #1191_

## Bind secretless Node acquisition to trusted PR workflow code

Generate the cli-projects organization required workflow from a strict canonical
configuration. Validate the live pull-request repository, number, head, merge commit,
merge ref, and exact generated push-only consumer caller before the immutable Node CI
contract receives package-read authority. Fail rollout closed until that caller is
byte-identical at one protected `main` commit, and bind every ruleset write to that
unchanged commit with verified rollback on drift. Candidate dependency acquisition remains
non-executing, both Node lanes execute without credentials, and the protected
package-surface verifier runs only after both succeed.

_Date: 2026-08-30; issue #1187_

## Make the generated-artifacts fixture independent of the impact grace window

Declare the canonical release impact in the generated-artifacts pull-request fixture so the platform test remains deterministic after the bounded migration grace window.

_Date: 2026-08-30; issue #1181_

## Exercise runner canary signal cleanup behavior

Exercise the runner canary admission script's real signal and cleanup functions with
isolated mocks, covering INT and TERM status preservation, cleanup-query failures, and
the absence of surviving owned disposable resources. Exact Docker argument and target
checks also prove that sibling, network, or image cleanup cannot silently target a
different resource.

_Date: 2026-08-30; issue #1163_

## Resolve hosted-selector policy from its checkout

Derive canonical workflow authority from the hosted-selector policy script's Git-verified, tracked checkout layout instead of the caller's working directory, while failing closed for fake repository markers, moved or symlinked policy copies, and preserving consumer enforcement.

_Date: 2026-08-30; issue #1159_

## Upgrade the protected deployment CLI

Upgrade the immutable container-deployment contract to `@verjson/cli-cloud@0.29.0`, including its verified lock graph and exact behavioral contract assertions.

_Date: 2026-08-30; id:20260830T194645Z_

## Repin CLI projects to the credential-safe protected Node contract

- Pin the CLI projects package-surface required workflow and generated consumer to the immutable protected Node contract that removes credential variables before candidate code runs.

_Date: 2026-08-30; id:20260830T133100Z_

## Adopt authenticated protected Node required workflow

- Resolve empty-context required-workflow deliveries through the authenticated current Actions run and exactly one live pull request.
- Call the immutable protected Node contract with explicit admitted identity outputs and least-privilege API scopes.
- Bind every candidate checkout to the admitted immutable head SHA and fail closed on stale, ambiguous, malformed, or partial evidence.

_Date: 2026-08-30; id:20260830T120000Z_

## Upgrade runner canary artifact upload

Upgrade the runner canary receipt upload to the immutable actions/upload-artifact v7.0.1 commit and bind the emitted dependency evidence and reviewed workflow checksum to that exact action.

_Date: 2026-08-30; id:20260830T000923Z_

## Parse Renovate union version ranges safely

Accept Renovate update-table version ranges joined by escaped `||` operators while
continuing to reject unescaped cell delimiters, malformed escapes, and unsafe version
text.

_Date: 2026-08-29; issue #1178_

## Protect the cli-projects package-surface gate

Add the organization-owned, exact-repository cli-projects package-surface required
workflow and a fail-closed transactional rollout plan. The protected verifier parses
shipped workflow YAML semantically, requires immutable action and container references,
checks the public package surface, and exercises the candidate archive without secrets
or persisted credentials on an ephemeral hosted runner. Adversarial controls reject
pull-request check-name spoofing, mutable references, duplicate triggers, weakened
verification steps, ambiguous provider identity, preimage drift, and partial activation.
Applied-remotely/client-failed mutations reconcile and roll back to the reviewed
non-enforcing image; retries resume only an exact uniquely identified staged rule.

_Date: 2026-08-29; issue #1177_

## Select subscriber-aware package distribution

Select Cloudsmith entitlement tokens for paid package distribution, designate an audit-gated public adoption package set, and retain GitHub Packages only as a verified bounded migration bridge.

_Date: 2026-08-28; issue #718_

## Align private acquisition timeout with OS lane policy

Generate private dependency acquisition legs with ADR 0103's 45-minute timeout and reject consumer attempts to widen that bound.

_Date: 2026-08-28; issue #1172_

## Publish container-safe runner capacity admission

Publish the byte-identical reviewed runner canary contract that measures Docker backing capacity through a hardened mount-free probe, preserves foreign resources, and proves bounded cleanup across failures and signals.

_Date: 2026-08-28; issue #1161_

## Bind authn type-surface checks to a protected required workflow

Add an organization-owned authn type-surface required workflow that acquires approved
private inputs in protected canonical code and runs pull-request-authored build and
contract code credentiallessly on ephemeral hosted capacity. A reviewed, SHA-bound,
repository-ID-scoped organization ruleset plan has no bypass actors and includes
fail-closed dry-run, apply, and exact-head required-run verification tooling. Rollout
also binds the retiring repository rule to its exact reviewed preimage and verifies a
disabled rollback whenever the activated organization rule cannot be read or validated.

_Date: 2026-08-28; issue #1154_

## Bound compatibility race synchronization by deadlines

Replace iteration-count waits in the swap/load/restore race fixture with monotonic
ten-second deadlines so loaded CI hosts retain the full synchronization budget.

_Date: 2026-08-28; issue #1147_

## Defend the empty compatibility backup invariant

Detect an unexpected entry in an initially-absent target's empty backup container,
restore the owned target absence, preserve the unexpected state for diagnosis, and fail
with a fixed message rather than a generic filesystem exception.

_Date: 2026-08-28; issue #1146_

## Prove sealed descriptors close before consumer execution

Exercise the namespace-bound npm consumer through `/proc/self/fd` and require that none
of the sealed compatibility-input descriptors survive bubblewrap setup into consumer
execution.

_Date: 2026-08-28; issue #1145_

## Fail sealed compatibility inputs with a fixed diagnostic

Report an unavailable or denied sealed-memory primitive through one fixed fail-closed
diagnostic without exposing platform exception details.

_Date: 2026-08-28; issue #1144_

## Bind candidate manifest checks to direct producers

Bind the candidate-manifest contract test to its exact direct-producer graph and prove that skipped, cancelled, or failed producers remain fail closed.

_Date: 2026-08-28; issue #1127_

## Clarify failed provider-gate recovery status

Report failed or cancelled AI review gates as having reached or ambiguously approached the provider boundary, making clear that their retained receipt cannot use zero-provider recovery.

_Date: 2026-08-28; issue #1122_

## Make pinned generator mode checks deterministic

Read the pinned changelog generator once before checking its advertised modes, eliminating pipefail races while preserving distinct unreadable-source and missing-mode failures.

_Date: 2026-08-28; issue #1118_

## Diagnose interrupted secretless dependency acquisition

Fail the required build job before cache restore when secretless dependency acquisition
did not complete, naming the likely interrupted eligibility job and the safe rerun remedy.
Intentional Renovate stability deferrals remain green and continue to report their
required context.

_Date: 2026-08-28; issue #1117_

## Accept NEXT documentation during changelog adoption

Allow a repository to add `NEXT/README.md`, a dependency manifest, and a valid changelog fragment in the same pull request.

_Date: 2026-08-28; issue #1116_

## Generate secretless private artifact builds

Let generated artifact releases acquire explicitly approved private packages outside repository build hooks and route multi-OS builds through exact ADR 0103 lane expressions.

_Date: 2026-08-28; issue #1100_

## Scope runner canary literal-probe lint

Publish the command-local ShellCheck annotation for the literal capacity probe without relaxing repository-wide workflow linting.

_Date: 2026-08-28; id:20260828T035900Z_

## Publish the immutable exact-runner canary

Publish the canonical database-backed runner canary at a versioned immutable ref, binding promotion to one runner transaction and the reviewed CLI workflow bytes.

_Date: 2026-08-28; id:20260828T005122Z_

## Bound GHCR readiness before provenance attestation

Require a newly pushed image digest to become visible through GHCR's OCI API
within a bounded retry window before attaching provenance, failing closed if
the registry never converges.

_Date: 2026-08-27; issue #1151_

## Authorize exact secretless call subsets

Treat the protected secretless package policy as a repository-wide authorization superset while every reusable call retains an exact, lock-bound package and scope subset.

_Date: 2026-08-27; issue #1149_

## Build deployable runner provenance on hosted infrastructure

Bind container publication, image attestations, and release-manifest promotion to
independently trusted GitHub-hosted runners so a managed fleet never acts as its
own provenance root.

_Date: 2026-08-27; issue #1142_

## Bound multi-package retention at 30 minutes

Raise the explicit container-retention job ceiling from 10 to 30 minutes so digest-verified traversal across multi-platform packages and attestations can complete while remaining time-bounded.

_Date: 2026-08-27; issue #1140_

## Preserve retained OCI artifact leaves

Treat digest-verified OCI artifacts referenced by retained image indexes as protected graph leaves rather than deletion candidates or traversal failures.

_Date: 2026-08-27; issue #1138_

## Preserve OCI artifacts during package retention

Preserve untagged OCI attestations and referrer artifacts during container retention, and verify inspected bytes match each requested package digest before cleanup eligibility is granted.

_Date: 2026-08-27; issue #1135_

## Normalize GHCR retention namespaces

Normalize mixed-case GitHub organization logins to lowercase OCI namespaces before inspecting container releases during canonical package retention.

_Date: 2026-08-27; issue #1132_

## Route untrusted CI aliases to ephemeral hosted runners

Strengthen runner admission so the six canonical and historical untrusted
selectors reconcile as one configuration and accept hosted routing only as the
exact `ubuntu-24.04` label. This prepares the human-gated organization-variable
cut without changing trusted, fallback, privileged, or default runner routing;
the live cut and exact-head consumer canary remain separate rollout actions.

_Date: 2026-08-27; issue #1115_

## Enforce the hosted-provenance routing contract

Recognize the exact hosted selectors introduced for #1142 container provenance while preserving the fixed-hosted inventory boundary and its mutation controls.

_Date: 2026-08-27; id:20260827T102953Z_

## Renumber the hosted-provenance decision to avoid an ADR collision

Move the hosted-provenance decision from ADR 0146 to ADR 0148 so it does not
collide with the older open compatibility-sandbox decision, while preserving
the generated reverse-chronological decision index.

_Date: 2026-08-27; id:20260827T101526Z_

## Correct the #1115 attended untrusted-runner cutover order

Correct #1115 so the five historical untrusted runner aliases move before
canonical `CI_LANE_UNTRUSTED`, and contract-test that the documented sequence
contains all six aliases exactly once with the canonical selector last. This
changes only the future human-gated rollout procedure; no organization variable
changed.

_Date: 2026-08-27; id:20260827T052017Z_

## Repair canonical container release preflight

Capture the artifact ID and digest together before immutable release preflight reads the
digest group, require Python 3 for all release validators, and enforce both bindings in
the canonical release contract.

_Date: 2026-08-27; id:20260827T034000Z_

## Bind generated AI review run names to authorization receipts

Generate the AI review caller with the exact authorization-check and arm-run identity used by
zero-provider recovery. Reject missing, generic, substituted, and duplicated run-name contracts so
recovery cannot lose or ambiguously correlate the trusted dispatch.

_Date: 2026-08-27; id:20260827T030000Z_

## Require Python 3 for container release extraction

Invoke the immutable candidate artifact extractor with `python3` and reject interpreter
regressions in the canonical container release contract.

_Date: 2026-08-27; id:20260827T025300Z_

## Run candidate manifest after optional dependency skips

Evaluate the candidate-manifest job after skipped optional dependencies while requiring
every direct candidate publisher and attestation job to finish successfully.

_Date: 2026-08-27; id:20260827T003233Z_

## Restore absent secretless compatibility targets

Allow verified secretless compatibility artifacts to occupy an initially absent self-package target, then restore that absence after success, failure, or signal without adding a self-dependency pin.

_Date: 2026-08-26; issue #1114_

## Recover receipt-bound reviews that stop before provider execution

Preserve exact dispatch identity and causal preflight state when the paid review gate never
runs. Permit only same-run, same-receipt recovery after all prior attempts prove skipped
provider gates and no reservation, submission, review, stale head, or identity drift.

_Date: 2026-08-26; issue #1112_

## Reserve DeepSeek review completions for JSON verdicts

Disable thinking for tool-free DeepSeek JSON reviews so the bounded completion is
reserved for the required verdict, and fail closed if a provider emits unexpected
reasoning. See ADR 0142.

_Date: 2026-08-26; issue #1110_

## Populate secretless public runtime caches from verified lock blobs

Add an opt-in canonical Node CI input that copies only complete, SHA-512-verified
public npm registry blobs from the credentialless install cache into the exact
job-scoped runtime cache. Lock-derived count and byte bounds, exact destination
binding, and missing, corrupt, and internal-package controls keep private
content, cache indexes, request metadata, symlinks, and credentials outside the
handoff.

_Date: 2026-08-26; issue #1109_

## Preserve the installed graph in secretless compatibility lanes

Replace compatibility-lane package-manager graph re-resolution with a bounded,
provenance-bound package swap so cold offline caches do not require missing
public registry packuments. The lane now rejects archive traversal, links,
special entries, duplicate or surplus roots, size and member-count overflows,
and wrong package identities before any consumer script runs, while restoring
the original installed package on rejection. ADR 0141 records the extraction,
time-of-check/time-of-use, and atomic-swap security boundary.

_Date: 2026-08-26; issue #1105_

## Resolve compatibility ranges through secretless Node CI

Let canonical Node CI resolve approved GitHub Packages ranges at run time, transfer exact verified tarballs with bound provenance, and run each compatibility lane without exposing acquisition credentials to consumer code.

_Date: 2026-08-26; issue #1103_

## Recover orphaned authorization checks across runs

Recover exact-head authorization checks only after their source arm is terminal, no active receipt-bound review owns them, and every repository, App, run, receipt, and check identity is revalidated.

_Date: 2026-08-26; issue #1094_

## Align the published fragment schema with the changelog engine

Accept the documented `impact`, `summary`, and `component` metadata in the published
fragment schema, and continuously compare its complete key set and load-bearing
constraints with the canonical changelog engine. Reject ambiguous quoted release text,
keep both schema and v1-readiness guards wired fail-closed, and install their complete
hash-verified Python dependency closure on fresh CI interpreters.

_Date: 2026-08-26; issue #1091_

## Derive changelog adoption guidance from the correct capability floors

Move the recommended changelog contract pin above every registered changelog-generator
floor, require the generated `pr-gate` mode, and render future #731 adoption briefs from
the machine-readable recommendation with the live `changelog / validate` context.

_Date: 2026-08-26; issue #1087_

## Bind zero-provider recovery to the dispatched head

Pass the immutable dispatched head into zero-provider recovery so the verifier can
authenticate its receipt before any provider boundary is crossed.

_Date: 2026-08-26; id:20260826T224500Z_

## Bind initial direct review dispatches to the trusted arm

Require a unique trusted-arm-owned first workflow attempt before accepting a direct
review receipt, and evaluate recovery against the bare job names emitted by direct runs.

_Date: 2026-08-26; id:20260826T212000Z; refs #1112_

## Bind runner deployment to verified review evidence

Runner deployment now binds independent code, security, and AI review artifacts to GitHub-owned exact-head authority, separates uncredentialed review analysis from protected App publishing, limits each repository-bound publisher token to checks writes, and separates DigitalOcean from runner-registration credentials.

_Date: 2026-08-26; id:20260826T170000Z_

## Provision Python dependencies for actions-ci groups

Reject whitespace-only immutable release titles in the published fragment schema,
install the complete hash-verified Python dependency closure for every actions-ci
group, and give the bounded matrix enough time to finish the full contract suite.

_Date: 2026-08-26; id:20260826T032728Z; refs #1091_

## Clarify v1 readiness enforcement

Clarify the organization v1 readiness contract so dependency matrices pass vacuously only when there is no in-wave sibling range to exercise, generated changelog pins are capability floors with live ruleset enforcement, published packages cannot pass without immutable snapshots, and npm publication remains explicit-dispatch only.

_Date: 2026-08-26; id:20260826T010957Z; refs #1088_

## Match required-check commands by strict shell semantics

Accept harmless quoting and horizontal-spacing changes in the canonical changelog-contract job while continuing to reject changed variables, redirects, arguments, scripts, command boundaries, appended commands, and job-level permission overrides.

_Date: 2026-08-25; issue #999_

## Graduate packages to v1 only when consumer evidence justifies it

Add the canonical `v1.0.0` readiness contract and replace the mandatory organization-wide release wave with package-scoped graduation based on consumer value, readiness, bounded migration, and durable ownership.

_Date: 2026-08-25; issue #1088_

## Fail AI authorization slug drift closed without stranded checks

Record the canonical CI Apps' organization-neutral live identities and make the AI
authorization arm reject a missing, malformed, or mismatched token-action App slug
before creating an authorization check. Remove an invalid installation-token
`GET /installation` probe after a production 404 proved that endpoint is unavailable to
the legitimate credential. Export each created check ID before validating its returned
App attribution so the no-dispatch terminalizer can complete any mismatch as failure.

_Date: 2026-08-25; issue #1086_

## Separate dependency supersession observation from terminal mutation

Define an organization-neutral, least-privilege GitHub App contract for documenting and closing dependency-update pull requests that are completely superseded by newer bot updates.

_Date: 2026-08-25; issue #1084_

## Fail closed on inherited changelog check permissions

The required-check classifier now requires the exact workflow-level `contents: read`
permission on a workflow that publishes the changelog contract context, preventing the
generated job from silently inheriting broader authority. Ambiguous duplicate top-level
keys, including mixed `on`/YAML-1.1 `true` trigger aliases, now fail closed before
contract extraction. Regression coverage binds the classifier to the canonical
generator output and also records the accepted trailing-comment and block-scalar
formatting raised in #1078 and #1079.

_Date: 2026-08-25; issue #1080_

## Reject malformed short pnpm integrity pins

Treat every pnpm version containing a Corepack `+sha512.` qualifier as an integrity pin and require the exact 128-character lowercase hexadecimal digest, including malformed values short enough to match the general Renovate version grammar.

_Date: 2026-08-25; issue #1074_

## fix(changelog): reject ambiguous release trigger mappings

Make generated changelog contracts reject duplicate YAML-equivalent release trigger keys, aliases, merge keys, flow ambiguity, malformed mappings, and any drift from the exact generated `workflow_dispatch` input schema instead of accepting the first readable `on` key.

_Date: 2026-08-25; issue #1070_

## Bind terminal merges to the live default-branch commit

Terminal promotion now rejects stale generated publications by binding authorization and the repository-scoped merge App's final pre-merge checks to the same trusted default-branch commit.

_Date: 2026-08-25; issue #1069_

## Authorize exact pre-contract snapshot reclassification from the base branch

Allow a reviewed base-branch permit to authorize one digest-bound, byte-identical move of an untagged pre-contract snapshot outside the active changelog namespaces without weakening ordinary immutable release history.

_Date: 2026-08-25; issue #1067_

## Bind privileged callers to exact terminal verification reads

Generate primary and retry terminal-merge callers with the exact non-writing Actions,
checks, contents, and pull-request read ceiling required by the App-backed reusable
workflow, preventing zero-job startup failures without restoring PAT authority.

_Date: 2026-08-25; issue #1061_

## Reject retired universal required-check declarations

Fail the required-check audit at contract validation when a declaration
reintroduces the retired `universal_contexts` key, instead of silently ignoring
an authorization context excluded by ADR 0128.

_Date: 2026-08-25; issue #1060_

## Add a fail-closed container release workflow identity canary

Add a fixed manual reusable-call probe that proves GitHub supplies the exact immutable
`job.workflow_ref` before deliberately rejecting malformed candidate data, without
artifact access, App-token mint, or package or release mutation.

_Date: 2026-08-25; issue #1059_

## Add bounded immutable assets to generated Node releases

Generated Node release callers can opt into attaching committed repository files, with traversal, symlink, type, collision, count, and size checks binding every uploaded byte to the immutable release tag.

_Date: 2026-08-25; issue #1042_

## Accept integrity-qualified pnpm updates from Renovate

Accept Renovate's package-manager update rows when pnpm's previous version is an exact SHA-512-qualified Corepack pin, while rejecting malformed pins and equivalent long values for other packages.

_Date: 2026-08-25; issue #1025_

## Attribute bare-name Renovate image digest updates

Accept Renovate's bounded bare package-name form so Docker image digest updates receive their canonical changelog fragment.

_Date: 2026-08-25; issue #1015_

## Enforce generated Renovate attribution admission

Generated changelog contract tests now reject removal or mutation of the
same-repository, Renovate-actor, and `renovate/` branch admission gate before the
Release App credential can reach the trusted attribution workflow.

_Date: 2026-08-25; issue #1014_

## Make container candidate retries idempotent

Reuse an existing exact-commit container image only after its complete platform index and provenance are cryptographically bound to the reviewed repository, source commit, canonical workflow, and immutable contract.

_Date: 2026-08-24; issue #1055_

## Bind explicit AI-review labels through a separate protected caller

Route explicit review labels through a separate protected `pull_request_target:labeled`
caller, reject persistent-label and rerun authority, and bind the caller revision,
delivery source, actor, repository, pull request, and exact head into the verified arm
receipt.

_Date: 2026-08-24; issue #1054_

## Compact complete container SBOM predicates

Serialize complete platform SPDX documents as compact JSON so large container candidates remain within GitHub's 16 MiB attestation boundary without dropping package evidence.

_Date: 2026-08-24; issue #1047_

## Keep the component contract fixture free of SIGPIPE races

Pin the component release integration fixture to the canonical contract that removed captured-value producer pipelines, so its repeated `pipefail` checks report the real contract violation instead of a misleading Node-version failure.

_Date: 2026-08-24; issue #1046_

## Split container release Git and package authority

Container releases now mint the repository-bound Contents-only Release App token solely
for protected Git and GitHub Release writes, while ephemeral job tokens independently
authorize GHCR promotion and retention. Generated callers no longer require a release PAT.

_Date: 2026-08-24; issue #1043_

## Split container candidate validation and publication authority

Container candidate pull-request validation and trusted publication now use separate source-fixed reusable workflows, allowing GitHub to admit the read-only graph without granting package, attestation, or OIDC write authority.

_Date: 2026-08-24; issue #1038_

## Bootstrap canonical CI adopters idempotently

Add a fail-closed manifest-driven bootstrap for neutral organization variables,
write-only secrets, exact GitHub App installations, and immutable generated callers,
with redacted receipts and check, dry-run, and apply modes.

_Date: 2026-08-24; issue #1037_

## Use organization-neutral canonical CI configuration names

Canonical workflows, generators, tests, and documentation now use role-based `CI_*` variables and neutral deployment/release secret names, so adopters no longer configure CI using the authoring organization's name.

_Date: 2026-08-24; issue #1035_

## Accept npm 12 pack metadata

Node release packaging now accepts both npm 11's one-element array and npm 12's
package-name-keyed object while retaining exact single-artifact validation.

_Date: 2026-08-24; issue #1031_

## Restore review-policy bootstrap for the inconsistent historical caller

The trusted authorization arm now recognizes the byte-exact generated caller at the
historically inconsistent contract revision and projects only safe, human-authority
Anthropic policy into its legacy envelope. All other callers retain the current strict
schema, and malformed or widened recovery attempts fail before review dispatch.

_Date: 2026-08-24; issue #1030_

## Admit least-privilege release proposal callers

Generated release proposal callers now select separate reusable entrypoints for issue proposals and Release dispatches, so GitHub can admit each call graph without granting the other mode's write authority.

_Date: 2026-08-24; issue #1028_

## Support reviewed pnpm dependency acquisition for container candidates

Allow container-candidate adopters to select an integrity-pinned pnpm 9 lockfile while preserving exact private-package scope, lifecycle isolation, credential-free Docker delivery, and recursive rejection of executable dependency configuration in committed lockfiles.

_Date: 2026-08-24; issue #1019_

## Keep the audited Claude action pin synchronized

Renovate now updates the Claude review action and its independently audited SHA in one dependency group, preventing otherwise-safe digest updates from leaving merge-gate CI red.

_Date: 2026-08-24; issue #1017_

## Pin the container release changelog engine

Container releases now acquire and execute the changelog engine from the caller's exact
immutable canonical contract SHA, so a clean generated adopter no longer needs a local
copy of `scripts/changelog.py`. The release path uses the runner's portable `python3`
interpreter contract.

_Date: 2026-08-24; issue #1013_

## Consume synchronized AI-review authorization

Treat a current maintainer-authorized `ai-review` label as explicit authorization for a synchronized exact head, while consuming and verifying the label before provider spend so it cannot be replayed across later heads. See ADR 0121 and issue #991.

_Date: 2026-08-24; id:20260824T000000Z_

## Bind terminal merge to a repository-scoped App token

Replace the terminal merge PAT with a short-lived GitHub App token restricted to the exact authorized repository and only contents-write and pull-requests-write. Generated callers now pass only the merge App client ID and private key. See ADR 0120.

_Date: 2026-08-23; issue #991_

## Prune resolved CI backlog pointers

Removed the resolved spending-limit and package-retention entries from the repository's active-issue guidance after closing #819 and #987 with live evidence and the owner's policy decision.

_Date: 2026-08-23; id:20260823T015300Z_

## Make package retention count configurable and require rename-deprecation stubs

Package retention's `keep` count is now a parameter (default 3, unchanged) instead of a hardcoded literal — `scripts/package_retention.py` exposes `--keep` on its CLI, and `node-release.yml`/`container-release.yml` source it from the org variable `VERJSON_PACKAGE_RETENTION_KEEP`, falling back to 3. No org variable is set by this change, so retention still keeps exactly the newest three stable versions everywhere.

_Date: 2026-08-22; issue #987_

## node-ci accepts a caller-supplied health check and container port for db-image/cache-image

Canonical Node CI's `db-image`/`cache-image` service slots are no longer
hardwired to Postgres/Redis. New optional inputs `db-health-cmd`, `db-port`, and
`cache-health-cmd` let a caller run any engine's own readiness check (and, for
`db-image`, its own container port) instead of the fixed `pg_isready` /
`redis-cli ping` checks.

_Date: 2026-08-22; issue #986_

## Fail a PR whose ADR number already exists on main or another open PR

`docs/decisions/NNNN-<slug>/` numbers are picked by each PR author as "the
next free number as seen from main" -- two PRs open at once cannot see each
other's allocation and can pick the same number silently, as three
concurrent PRs did live for `0004` in `Verjson/verjson-ai-gguf`.
`scripts/gen-adr-index.sh` only ever sees one PR's own tree, so it faithfully
regenerates an index containing the duplicate, and git merges the two
differently-named directories without a conflict.

_Date: 2026-08-22; issue #983_

## Add a release-artifact caller mode for non-npm publications

`scripts/gen-changelog-caller.sh release-node` was the only generated release
caller, and it refuses a repository with no scope-owned npm package —
concretely, an Electron desktop app that ships OS installers as GitHub
Release assets instead of publishing to a registry (`Verjson/AiB#211`).

_Date: 2026-08-22; issue #975_

## docs: refresh CLAUDE.md Active Issues with live-verified status and a verification-date convention

Refreshed `CLAUDE.md`'s Active Issues section against live GitHub state (issue
comments, `gh api orgs/Verjson/actions/secrets`, the #731 rollout audit) rather than
carrying forward prose written days earlier. Removed #931/#701/#728/#157, all closed.
Added #731/#975/#983/#985/#986/#987/#994, discovered or dispatched to delivery agents
during this pass.

_Date: 2026-08-22; issue #956_

## Detect live generated-caller pins that predate an assumed capability

Adds a two-stage generated-caller capability-floor observer (ADRs 0114 and
0135). A scheduled and manually dispatched, report-only workflow reads an
explicit reviewed consumer allowlist using an exact-repository,
`contents:read` App token, strictly extracts immutable canonical pins, and
feeds them to the local Git-ancestry classifier. Malformed or inaccessible
callers fail closed; stale findings remain non-mutating summary and artifact
evidence.

_Date: 2026-08-22; issue #933_

## test(node-ci): regression test for backslash-containing health commands

Non-blocking AI review follow-up from PR #1008 (the `read -r` fix for
`db-health-cmd`/`cache-health-cmd`): no regression test proved a backslash
survives the tokenization step unaltered. Added one to
`scripts/ci-gate/node-ci-db-service.test.sh`.

_Date: 2026-08-22; issue #1010_

## fix(node-ci): use read -r for caller-supplied health commands

`node-ci.yml`'s `db-health-cmd`/`cache-health-cmd` tokenization used `read -ra`
without `-r`, so a backslash in a caller-supplied health command would be
interpreted by `read` rather than reaching `docker exec` as a literal
character, contradicting the "reaches docker exec as literal argv" comment
and tests. Added `-r` to both `read` invocations.

_Date: 2026-08-22; issue #1007_

## docs(adr): correct ADR 0114's staleness direction to match the implementation

ADR 0114 defined staleness as "pinned SHA is not an ancestor of the introducing
commit," but the implemented check is `is_ancestor(introduced_at, pinned_sha)`
— the reverse direction. Corrected the prose to "the introducing commit is not
an ancestor of the pinned SHA" so a future implementation can't invert the
check by trusting the ADR's wording.

_Date: 2026-08-22; issue #1003_

## docs: prune Active Issues entries for the 2026-08-22 backlog pass

Removed #975/#983/#985/#986 (closed — delivered via #1004/#1006, #1000, #1002)
and updated #933/#987/#731 with their current merged status. Added #991
(design posted, needs org-admin App provisioning) and #999 (real but
low-priority, deliberately deferred). No code change.

_Date: 2026-08-22; id:20260822T193000Z_

## Fail closed on a missing or malformed privileged_lane in ai-privileged-merge.yml

`ai-privileged-merge.yml` declared the `privileged_lane` workflow input ADR
0089 requires generated callers to supply, but never read or validated it —
the terminal job's private-Verjson `runs-on:` branch hardcoded its self-hosted
selector regardless of what the caller sent. A new first step in the
`privileged_merge` job now exact-matches the caller-supplied value against
`["self-hosted","general"]`, failing closed with a diagnostic on anything
missing, malformed, hosted, widened, or shadowed by a repository-level
variable, per ADR 0089 (amended with the gap and fix rationale). The step
runs on whatever capacity `privileged_merge` already resolved to for the
caller, so it adds no new capacity requirement for external self-hosted-only
consumers on the `runner_labels` escape hatch.

_Date: 2026-08-21; issue #988_

## Refresh the rework-telemetry repo list to activity-ranked repos (ADR 0113)

`.telemetry/rework-thresholds.json`'s `repos` list had gone stale — several
of its 8 repos had little to no recent activity. Replaced it with the top 10
org repos by a fresh 6-week bot-filtered commit-activity ranking, excluding
`verjson-agents` (tooling, not a shipped product) per explicit user
direction. Full methodology and the ranked list are in ADR 0113. This is a
human-owned dial under ADR 0006; changed here at the user's explicit
request, not unilaterally.

_Date: 2026-08-21; issue #980_

## Require the checkout-credential exemption's consuming step to run after the checkout

`audit_checkout_credentials` (`scripts/ci-gate/changelog-caller-contract.test.sh`)
verified a named consuming step exists and still performs the justifying remote
git operation, but not that it runs after the checkout it excuses. A reordered
workflow could keep the exemption valid by name while the persisted credential
was actually consumed by an earlier, unrelated step. The audit now also checks
step index ordering, with a new tamper test confirming a reordered consumer
re-flags the checkout.

_Date: 2026-08-21; issue #974_

## Bump the audited claude-code-action pin alongside its Renovate digest bump

Renovate's `renovate/github-actions-digests` PR bumped
`anthropics/claude-code-action` to `3f854a8fb5146b39d5cbf8b57f70d80810e1366f`
in `ai-review-merge.yml` without updating the mirrored `MODEL_ACTION_SHA`
constant in `scripts/ci-gate/event-driven-authorization.test.py`, per the
ADR 0083 contract that requires both to match. Audited the upstream diff
(`e2a4b761c...3f854a8`): a Claude Code CLI version bump and a
credential-leak hardening fix in `git-config.ts` (clears the checkout auth
header from `actions/checkout` v6+'s `include.path`-based config layout, not
just the legacy repo-local config) — no change to actor/login normalization,
so the ADR 0083 authorization contract is unaffected. Updated the test
constant to match.

_Date: 2026-08-21; id:20260821T020821Z_

## Fail closed instead of crashing on a Renovate body ending in a header row

`table_rows` indexed `lines[index + 1]` to read a candidate table's separator
row without checking that line existed, so a pull-request body whose final
line was itself a header row (`| Update | Change |` with nothing after)
raised an unhandled `IndexError` instead of the intended `AutomationError`.
Added the missing bounds check.

_Date: 2026-08-20; issue #972_

## Verify the release-node credential exemption structurally, not by name alone

The #959 checkout-credential audit exempted `release-node`'s "Check out the
tree that will be released" step by name only, trusting that a step named
"Resolve restart-safe release state" still consumed the credential. Renaming
or defanging that consuming step would have silently kept the exemption
alive. The audit now looks up the named consumer step and requires its `run`
body to still contain the git remote operations (`git ls-remote`, `git
fetch`) the exemption is justified by; either drifting re-flags the checkout.

_Date: 2026-08-20; issue #971_

## Align the ADR 0111 inline comments with its actual blast-radius tradeoff

The inline comments on `RENOVATE_COMPATIBILITY_PAT`'s usage claimed it carries
"only the read access this job needs, not a broader organization credential" —
understating the blast radius ADR 0111 itself already documents accurately: the
PAT is the owner's own credential with full org-wide read access, not a
least-privilege token. Corrected the comments to match.

_Date: 2026-08-20; issue #967_

## Complete the authorization check when the arm never dispatches a review

`gate-rearm.yml` creates the required `AI review authorization` check-run before it
uploads the immutable receipt, but nothing completed that check-run when the upload
failed — so an artifact-quota rejection left the check `in_progress` forever and its PR
blocked with no visible failure. The arm now gives that check-run a terminal `failure`
conclusion with recovery guidance whenever it ends without a dispatched review.

_Date: 2026-08-20; issue #964_

## Stop the generated pr-gate caller persisting its job credential into PR-authored code

`scripts/gen-changelog-caller.sh pr-gate` emitted `actions/checkout` with no `persist-credentials: false`, so the job's `GITHUB_TOKEN` was written into `.git/config` and was still there when the very next step ran PR-authored `scripts/changelog-contract.test.sh` out of the same workspace. Nothing in that job fetches or pushes afterwards, so the generated checkout now sets `persist-credentials: false`, and every step it emits carries a `name:`.

_Date: 2026-08-20; issue #959_

## Attribute Renovate lock file maintenance pull requests

`scripts/renovate-changelog.py` now recognizes Renovate's `lockFileMaintenance` table
shape and writes a fixed `NEXT/` fragment for it, so the weekly lock-file-maintenance pull
request no longer needs a hand-written entry.

_Date: 2026-08-20; issue #958_

## Skip the #950 write-denial test under a root runner

The #950 regression test denies write access via `chmod 555` on a directory,
but a root runner ignores directory permission bits, so `mktemp`/`mv` could
still succeed there and falsely fail the test. Skip that one case (with a
`pass` noting why) when `id -u` is `0`; every other case is unaffected.

_Date: 2026-08-20; issue #953_

## Test the arm-receipt deletion step's stderr redaction

No automated test exercised the `sed` redaction guarding
`ai-privileged-merge.yml`'s receipt-deletion step against a leaked
Authorization header on a failed `gh api --method DELETE` call. A new test
extracts that step's terminal fragment from the shipped workflow and exercises
it against a stub that leaks a header on stderr, asserting the header is
redacted and the deletion failure stays non-fatal.

_Date: 2026-08-20; issue #951_

## Write the deferred arm-receipt artifact ID atomically

`verify-arm-receipt.sh` recorded the artifact ID for deferred deletion with a
direct `printf` to the target path; a partial write (e.g. ENOSPC/EIO) could
leave a truncated ID for the later deletion step to read. The write now goes
through a same-directory temp file and an atomic rename, so a failure can
never leave a truncated ID at the target path.

_Date: 2026-08-20; issue #950_

## Prune closed issues and refresh backlog status from live findings

`CLAUDE.md`'s Active Issues list carried #858 and #856 as open; both were
actually closed on 2026-08-16, days before this refresh, and had gone
unnoticed. Removed them and updated #931, #676, #699, #157, and #819/#810
with live findings from a PM sync: #931's quota cleared but a dangling
check-run is now the real blocker; #676's lane concept is done but `.github`'s
own caller still bypasses it; #699 has an interim-PAT PR open; #157's new
token doesn't cover 7 specific private repos; #819/#810 narrowed to two
residual decisions that are `.github`'s to make.

_Date: 2026-08-20; id:20260820T023000Z_

## Refresh the Active Issues backlog status after a PM sync

A PM sync landed #952 (arm-receipt hardening) and #920 (Renovate digest bump,
paired with the ADR 0083 audited model-action pin), closed #947 as already
fixed by #949, and diagnosed current status on #931, #933, #701, #728, #676,
#629, #718, #699, #157, #819, and #810. Refreshed `CLAUDE.md`'s Active Issues
list so it reflects what's actually blocking (mostly external: org billing
quota recalculation, GitHub App provisioning, unmanaged-repo PM work, and
user-owned product decisions) rather than a day-old snapshot.

_Date: 2026-08-20; id:20260820T004500Z_

## Repair the audited Claude review action pin after a Renovate digest bump

Renovate proposed bumping `anthropics/claude-code-action` to
`e2a4b761cd77a1138a5b41410eda9b28581f9bcd`, but ADR 0083 requires the merge
gate's separately audited `MODEL_ACTION_SHA` constant to be updated in the same
PR after reviewing the upstream diff. The upstream range (5 commits) does not
touch `src/github/validation/actor.ts` or `src/github/validation/trigger.ts` —
the authorization normalization ADR 0083 pins — so the source audit passes and
`event-driven-authorization.test.py` is updated to match.

_Date: 2026-08-20; id:20260820T000000Z_

## Harden the deferred arm-receipt deletion against credential leaks and write failures

Two non-blocking AI-review follow-ups on #945:

_Date: 2026-08-19; issue #946_

## Fix a merge-gate CI regression introduced by #945's own merge

#945 merged with a red `merge-gate` CI group: multi-line env-var-prefixed invocations
of `verify-arm-receipt.sh` (`FOO="..." \` on one line, `bash .../verify-arm-receipt.sh`
on the next) broke `event-driven-authorization.test.py`'s single-line invocation
pattern check, and `native-automerge.test.sh`'s extracted `promote.sh` fixture hit an
unbound `$RUNNER_TEMP` under `set -u` since the harness never exported it (never
needed before this change).

_Date: 2026-08-19; issue #945_

## Delete the arm receipt at the caller's true terminal success, not at verification

#942 deleted the arm receipt artifact inside `verify-arm-receipt.sh`, immediately on
successful verification -- before the rest of the calling step's own work (completing
the check run, or the terminal merge) had actually run. If that later work failed for
an unrelated transient reason and the step were retried, the retry's own
re-verification would find the receipt already gone, with no way to recover short of
restarting the whole arm cycle.

_Date: 2026-08-19; issue #943_

## Scope secretless-rebuild-packages to packages actually installed on this platform

#932 made `secretless-rebuild-packages` require an exact match against the lock's
full declared install-script surface (npm's `hasInstallScript`, pnpm's
`requiresBuild`). A pnpm or npm lockfile records every platform variant of an
optional native dependency (e.g. `@esbuild/darwin-arm64`, `@esbuild/linux-x64`),
but `--frozen-lockfile`/`--ignore-scripts` only installs the ones this runner's
`os`/`cpu` actually match. The exact-match check would have forced the allowlist
to name a platform variant that was never installed here, and rebuilding it would
then fail.

_Date: 2026-08-19; issue #941_

## Default the generated pr-gate caller to a hosted, ephemeral runner

`scripts/gen-changelog-caller.sh pr-gate` emitted a `pull_request`-triggered job pinned to `[self-hosted, general]`. On that trigger `actions/checkout` resolves the pull request's own ref, and the job then executes PR-authored `scripts/changelog-contract.test.sh` from that checkout — on a persistent self-hosted runner, that is arbitrary code execution from any PR author, discovered when it blocked a real adopter's trust policy from accepting the caller at all. The default is now GitHub-hosted `ubuntu-24.04` (ephemeral, isolated per job), matching this repository's own convention for PR-triggered security-boundary jobs. Adopters with a genuinely isolated, ephemeral, per-job untrusted-PR lane can opt back into a self-hosted selector explicitly with the new `--untrusted-runner` flag.

_Date: 2026-08-19; issue #935_

## secretless-rebuild-packages now asserts the lock's full install-script surface

`node-ci.yml`'s `secretless-rebuild-packages` input let a consumer name packages to rebuild in the credentialless job, but the workflow only verified those names appear in the lockfile — not that they are the complete set of packages the lock actually marks as needing install-time code execution (npm's `hasInstallScript`, pnpm's `requiresBuild`). A dependency update that silently added a lifecycle script to some other, unnamed package passed review unnoticed, and a stale name left in the allowlist looked like continued intent rather than drift. The step now fails closed on either direction of mismatch: a lock-declared install-script package missing from the allowlist, or an allowlisted name the lock no longer marks as needing one — turning the input into the reviewed assertion consumers already assumed it was.

_Date: 2026-08-19; issue #932_

## Delete the arm receipt artifact once its last required read succeeds

`gate-rearm.yml`'s immutable arm receipt uploaded with a 90-day retention and no
delete-on-consumption, so it grew unbounded with organization PR volume and
exhausted the org-wide Actions artifact storage quota, blocking every
authorization arm org-wide (observed failing `verjson-browser-agent#46` and
`verjson-cloud-storage#92` at the upload step itself).

_Date: 2026-08-19; issue #931_

## Assert no partial npmrc override is written on unapproved-scope rejection

The secretless pnpm install test only asserted the lockfile was left
unchanged when an unapproved scope is rejected. It now also asserts the mock
registry npmrc override file was never created, proving the fail-closed path
writes nothing before rejecting.

_Date: 2026-08-19; issue #930_

## Clarify that a post-publish-failure 404 is not proof a package version is unpublished

`node-release.yml`'s publish-failure remedy treated an `npm view` 404 as proof
the version was never published. GitHub Packages also returns 404 for a
private package the job's token cannot read, so a 404 could equally reflect
an authorization gap. The error message now says so explicitly instead of
asserting a confirmed-missing version.

_Date: 2026-08-19; issue #929_

## Add regression test for mixed digit/non-digit prerelease dist-tags.latest selection

`node-ci.yml`'s secretless pnpm install rewrite picks `dist-tags.latest` from a
locked-version dict without a real semver comparison (safe under
`--frozen-lockfile`, which never reads the tag). Add a test fixture with a
packument spanning `1.2.3` and `1.0.0-beta.1` (a mixed digit/non-digit
prerelease) to prove the selection never raises a `TypeError`, closing the
#917 follow-up gap.

_Date: 2026-08-19; issue #928_

## Accept Renovate's default source-link suffix in changelog attribution

`scripts/renovate-changelog.py`'s `PACKAGE` regex anchored immediately after the Package cell's first `[name](url)` link, but Renovate appends a ` ([source](…))` suffix by default whenever a package's `sourceUrl` differs from its homepage — not an opt-in presentation. Every such row (`vitest`, `@types/node`, and any package sharing that shape) failed `renovate-changelog: Renovate update table contains an unsupported package or change cell`, which meant no `NEXT/` fragment was ever written and the canonical contract's own required check then blocked the PR permanently. `PACKAGE` now accepts zero or more trailing ` ([label](url))` groups after the primary link.

_Date: 2026-08-19; issue #925_

## Name the safe remedy when node-release.yml's publish step fails

`node-release.yml`'s restart-safe publish step could not distinguish a genuine `npm publish` failure (the version never reached the registry) from the registry-mismatch/spoof cases it also guards against, so both surfaced the same "cannot read the allegedly existing registry version" error. That message reads as a wedged release when the actual state is expected-recoverable — the changelog contract's verify-then-snapshot-then-publish ordering (ADR 0069) means the git tag and immutable `CHANGELOG/<version>.md` already exist by the time this step runs, and re-dispatching the same version safely resumes publication alone. Two adopters hit this in one day from unrelated causes (an organization billing limit, a transient `429` fetching `actions/checkout`), and the ambiguous message caused a real downstream misdiagnosis. The error now states the remedy directly: the tag and snapshot are safe, this is not a wedged release, and re-dispatching the same version resumes publication automatically.

_Date: 2026-08-19; issue #921_

## Address AI-review follow-ups on the pnpm mock registry and publish-failure message

Three non-blocking AI-review findings from PR #916 and PR #923, fixed rather than left open. The pnpm loopback mock registry no longer computes a fragile semver-like sort for a package's `dist-tags.latest` (a mixed digit/non-digit prerelease segment such as `1.0.0-beta.1` could raise `TypeError`); any locked version is picked instead, since `--frozen-lockfile` never consults it. The per-scope npmrc override that routes a scope to the mock registry is now bounded to `APPROVED_INTERNAL_SCOPES` — the same allowlist already used to approve individual packages — instead of any scope discovered in the lockfile, so it can never reroute a scope the workflow hasn't already declared fully internal. And `node-release.yml`'s publish-failure message now distinguishes a registry-confirmed-missing version (`npm error code E404`) from a registry check that simply couldn't be confirmed (auth/network/other error) — the safe re-dispatch remedy is unchanged either way, but the message no longer overclaims what the registry actually said.

_Date: 2026-08-19; issue #917; refs #918, #924_

## Confirm the AI-review App's permission envelope was never the defect

The authorization App's `403 Resource not accessible by integration` errors were traced through several layers — missing Contents access, a GraphQL head lookup under the wrong token, an ambient `GH_TOKEN` alias shared between the workflow and App tokens — and fixed incrementally (App-level Contents permission, REST head lookup, explicit per-call App-token binding, phase-specific diagnostics). The installation's own permission envelope (`checks: write`, `contents: read`, `pull_requests: write`, `repository_selection: all`) was correct throughout; ADR 0079 is amended with the dated correction rather than superseded, since no decision changed.

_Date: 2026-08-19; issue #702_

## Update container deployment Node runtime

Update the container deployment workflow to Node 24.19.0 and keep its
generated-contract verification synchronized with the runtime.

_Date: 2026-08-18; issue #914_

## Prove release commits exclude workspace cache residue

Cover the canonical changelog release path with an untracked workspace cache and verify
that its explicit release pathspecs leave the cache outside the release commit and tag.

_Date: 2026-08-18; issue #910_

## Exercise the secretless pnpm lock rewrite with a real frozen install

The canonical Node workflow now bounds PyYAML parsing to YAML 1.2 boolean semantics, reports surplus transfer digests precisely, and exercises successful, failure, surplus, and lock-restoration paths with a real credentialless frozen pnpm install fixture. This also resolves follow-up issues #904 and #905.

_Date: 2026-08-18; issue #903; refs #904, #905_

## Make changelog contract caches runner-portable

Allocate Node CI and release changelog tool caches in uniquely owned workspace
directories so cold contract resolution remains writable even when a shared
runner exposes a protected temporary root.

_Date: 2026-08-18; issue #902_

## Serve verified private pnpm packages from a loopback mock registry

Rewriting a pnpm lock's private tarball URLs to local `file:` paths kept the resolution registry-shaped in name only: pnpm's own supply-chain policy rejects a `name@version` dependency path backed by a non-registry resolution (`ERR_PNPM_RESOLUTION_SHAPE_MISMATCH`), which a live consumer reproduced after the first fix landed. The credentialless build now starts a loopback-only HTTP mock registry per run, publishes each verified private tarball and a matching packument under it, rewrites only the tarball URL to that local `http://127.0.0.1` address, and scopes a run-local npmrc override to the affected package scopes so `pnpm install --frozen-lockfile --prefer-offline` resolves and fetches entirely from the loopback mirror — never presenting as a non-registry resolution and never contacting the private registry — while public dependencies remain installable from the public registry.

_Date: 2026-08-18; issue #896_

## Surface package retention authorization failures

Make the Node release workflow fail visibly when its least-privileged package-retention job cannot delete stale GitHub Package versions, while retaining restart-safe publication behavior.

_Date: 2026-08-18; issue #893_

## Retain aliased numbered container releases

Allow a numbered container release to share its digest with non-version aliases while
continuing to reject multiple numbered tags on one digest. Retained aliased indexes now
participate in recursive OCI graph protection, so their untagged child manifests remain
protected from cleanup.

_Date: 2026-08-18; issue #892_

## Retain the three newest numbered package releases

Canonical Node and container releases now delete stable numbered package versions older than the newest three and remove only aged, unreachable untagged container versions after publication succeeds.

_Date: 2026-08-18; issue #889_

## Update the Claude review action pin

Update the Claude review action to its audited immutable revision and keep the merge-gate contract synchronized with that pin.

_Date: 2026-08-18; issue #888_

## Document AI review adoption prerequisites

Clarify that adopters must receive the complete AI review variable and secret families before enabling the required authorization arm.

_Date: 2026-08-18; issue #836_

## Generate the changelog contract PR gate

Generate the required changelog contract pull-request job so adopters inherit its
job-scoped writable tool cache instead of copying a runner-dependent job by hand.

_Date: 2026-08-18; issue #822_

## Enforce Linux runner selector policy in consumers

The required actionlint workflow now rejects literal Linux hosted-runner selectors in Verjson consumer workflows, including matrix and reusable-workflow inputs, while preserving reviewed lane expressions.

_Date: 2026-08-18; issue #816_

## Add the observe-first Renovate compatibility control plane

Add a least-privilege compatibility reconciler, stack-scoped failure fingerprinting,
secretless candidate canaries, and an immutable generated consumer caller. The initial
rollout reports evidence without automatically creating or mutating compatibility
holds.

_Date: 2026-08-18; issue #699_

## Pin protected deployment CLI acquisition

Acquire the protected runner deployment CLI from the immutable contract checkout with
a fully validated SHA-512 npm lock, exact Node runtime, isolated job cache, and
guaranteed cleanup before or after any deployment operation. This corrects the
dispatchability gap found while completing #629 without creating a duplicate issue.

_Date: 2026-08-18; id:20260818T143500Z_

## Authenticate compatibility policy reads with the dedicated App

Use the compatibility App client ID for observer authentication and mint its
least-privilege token before the grouping planner checks out the private policy
repository on the trusted lane. Both observe-first workflows now fail closed on one documented
credential boundary instead of relying on a repository-scoped workflow token. Each
mint explicitly requests only the read permissions its job needs.

_Date: 2026-08-18; id:20260818T082700Z; refs #699_

## Import verified pnpm cache blobs as tarballs

Secretless pnpm CI now copies verified npm cache blobs to temporary `.tgz` package specs before importing them into the pnpm store, preventing runtime rejection of extensionless content-addressed cache paths.

_Date: 2026-08-17; issue #886_

## Support pnpm in secretless Node CI

Canonical secretless Node validation now supports pnpm lockfile version 9 through
an explicit package-manager input. It preserves exact private-package authorization,
integrity-bound cache transfer, credentialless frozen installation, and cleanup while
leaving existing npm callers unchanged. Scoped and unscoped peer-context keys share
one fail-closed identity grammar across acquisition authorization and lifecycle rebuilds.

_Date: 2026-08-17; issue #879_

## Report actionable npm acquisition failures

Preserve the npm exit status and bounded, token-redacted stderr when private-package acquisition fails, and identify authorization, permissions, network, registry, and integrity as distinct possible causes.

_Date: 2026-08-17; issue #878_

## Clean failed secretless Node cache attempts

Allocate every secretless private-package acquisition in a unique temporary cache and remove partial state immediately when acquisition fails, so persistent runners remain retryable without weakening fresh-cache validation.

_Date: 2026-08-17; issue #877_

## Exercise authorization wildcard with distinct outcomes

Complete-authorization tests now exercise several distinct unknown AI-review outcomes, preventing an exact-token special case from masking a regression in the wildcard neutral human fallback.

_Date: 2026-08-17; issue #867_

## Fail early or select a reachable database endpoint on isolated runners

Canonical Node CI now verifies its database endpoint before consumer commands and
automatically uses the exact container bridge endpoint when Docker host loopback
is isolated from the runner.

_Date: 2026-08-17; issue #842_

## Supply the container dependency context for non-Node adopters

Create an empty, credential-free `verjson_node_modules` named build context when
private Node packages are absent, allowing non-Node repositories to complete first
adoption without adding a package graph.

_Date: 2026-08-17; issue #837_

## Document the required-check workflow YAML subset

The required-check workflow audit now documents its intentionally narrow canonical YAML subset and locks the fail-closed behavior for anchors, aliases and merge keys, and explicit tags.

_Date: 2026-08-16; issue #871_

## Restrict container candidate credentials to required trust paths

Generate secretless, read-only pull-request validation for container candidates that
declare no private Node packages, and reserve write and OIDC permissions for trusted
publication events.

_Date: 2026-08-16; issue #869_

## Test extraction diagnostic short-write rejection

Lock in rejection of partially written DeepSeek extraction diagnostics with a file-object test double.

_Date: 2026-08-16; issue #865_

## Lock authorization fallback outcomes

Complete-authorization behavioral tests now lock the GitHub-visible conclusion, title, and summary for superseded and unknown AI-review outcomes. Both paths must remain neutral human fallbacks that cannot request App approval, emit AI authority, or report success or failure while exact-head trust checks pass.

_Date: 2026-08-16; issue #863_

## Diagnose release tests that hardcode package versions

Generated Node release callers now warn that verification runs after the dispatched package version is stamped and report that version when the suite fails, pointing adopters to version assertions that must read `package.json` dynamically.

_Date: 2026-08-16; issue #862_

## Bind extraction diagnostics to retained artifacts

Expose DeepSeek extraction diagnostics to workflow consumers only after their bounded, redacted artifact has been written successfully.

_Date: 2026-08-16; issue #859_

## Distinguish advisory AI checks from approval

Blocking, inconclusive, skipped, superseded, and human-only AI review outcomes now complete the authorization check as visibly neutral, while green success is reserved for a persisted exact-head App approval.

_Date: 2026-08-16; issue #858_

## Retain typed DeepSeek extraction diagnostics

Classify completed DeepSeek response-extraction failures and retain bounded, pass-specific, non-content diagnostic artifacts so deterministic advisories name each exact cause without granting review authority.

_Date: 2026-08-16; issue #856_

## Bind container SBOMs through the parent OCI index

Make generated container candidate callers executable and bind each signed SPDX document to one exact reviewed platform through BuildKit's parent-index evidence topology.

_Date: 2026-08-16; issue #851_

## Preserve AI review across synchronize cancellation

AI review reservations are now capped per exact head, so a canceled stale-head run cannot exhaust review eligibility for a replacement revision.

_Date: 2026-08-16; issue #850_

## Attest and serialize canonical container promotion

Canonical container publication now signs every platform SPDX document and the release manifest, verifies exact producer identities, serializes releases, and reconciles the complete alias set before durable publication.

_Date: 2026-08-16; issue #847_

## Reject existing release tags before mutation

Fail an exact changelog release before creating its commit or consuming fragments when the requested local tag already exists.

_Date: 2026-08-16; issue #844_

## Make container config-step contract extraction fail closed

Make the container candidate contract test tolerate optional step keys before the config script while rejecting missing, malformed, or empty run blocks.

_Date: 2026-08-16; issue #841_

## Keep container Node acquisition optional

Allow non-Node container adopters to build without a lockfile, npm installation, transfer cache, pre-existing base configuration, or `node_modules` build context.

_Date: 2026-08-16; issue #839_

## Make generated changelog callers publish the live required check

Generated changelog callers use the `changelog` job prefix while invoking the consolidated `generated-artifacts.yml` workflow, so every changelog-enabled mode publishes the active ruleset's exact `changelog / validate` context.

_Date: 2026-08-16; issue #835_

## Add trusted changelog attribution for Renovate updates

Generate a guarded Renovate caller that adds dependency release context without executing pull-request code.

_Date: 2026-08-16; issue #834_

## Restore private package acquisition in secretless Node CI

The secretless Node acquisition job now requests package-read authority, restores
fresh private-package downloads, and rejects pre-existing cache state that could mask
missing authority, while keeping the token out of PR-controlled execution.

_Date: 2026-08-16; issue #833_

## Move private container dependency handoff off artifact storage

Transfer credential-free private container dependencies through an exact repository cache key so organization artifact quota exhaustion cannot block candidate builds.

_Date: 2026-08-15; issue #830_

## Lock reserved transfer path guards

Assert that the Node CI contract rejects occupied transfer paths before cache save and restore.

_Date: 2026-08-15; issue #829_

## Exercise empty secretless package handoffs

Cover the zero-private-package cache packaging and credential-free install path end to end.

_Date: 2026-08-15; issue #827_

## Move secretless Node handoff off artifact storage

The canonical Node workflow now saves and restores its bounded private-package
handoff through an exact run-attempt Actions cache key, eliminating transfer
artifacts and the caller-level `actions: write` permission. Empty private-package
sets produce a validated empty handoff without a tokened npm request, while
non-empty locks retain exact allowlist, URL, integrity, and payload validation.

_Date: 2026-08-15; issue #824_

## Reconcile GitHub-hosted larger-runner inventory

The scheduled organization runner reconciler now rejects GitHub-hosted larger runners
unless their exact ID/name identities appear in an explicit reviewed allowlist, which is
empty by default, closing the arbitrary-label route around static hosted-selector policy.

_Date: 2026-08-15; issue #820_

## Enforce metered runner routing in reusable actionlint

Reusable actionlint now rejects literal macOS and Windows hosted selectors in every Verjson caller, including canonical reusable-workflow runner inputs, while leaving foreign callers and the separately deferred Linux policy unchanged.

_Date: 2026-08-15; issue #815_

## Reject metered hosted selectors and unbounded hosted jobs

Runner-routing conformance now refuses any `runs-on` naming the metered
`macos-*` or `windows-*` runner families, requires a bounded `timeout-minutes`
on every job that resolves to an OS-scoped lane, forbids those lanes from
degrading to Linux through `VERJSON_LANE_FALLBACK`, and fails any workflow that
references the OS lane variables off the sanctioned desktop-release path or
from any trigger other than `workflow_dispatch`. Rolling `ubuntu-latest` is
also refused independently of repository visibility.

_Date: 2026-08-15; issue #814_

## Require explicit release impact on new changelog fragments

Require every newly added changelog fragment to declare `major`, `minor`, or
`patch`, while preserving the historical patch fallback for existing fragments
and immutable released snapshots.

_Date: 2026-08-15; issue #800_

## Generate explicit, idempotent release proposals

The canonical changelog generator now emits an adopter release proposer that either maintains one released-preview issue or dispatches the existing guarded `Release` workflow with the exact derived version, with autonomy fixed in source and least-privileged permissions.

_Date: 2026-08-15; issue #799_

## Stabilize the secretless cache path across runners

Secretless Node handoffs now use the same stable relative cache path on acquisition
and build runners, preventing runner-specific work roots from changing the cache
version and causing an exact-key restore miss.

_Date: 2026-08-15; id:20260815T225900Z; refs #824_

## Audit the Claude review action update

Update the merge gate to the audited immutable Claude review action revision and keep its behavioral pin contract synchronized.

_Date: 2026-08-15; id:20260815T011500Z_

## Require release authorization on every ~DEFAULT_BRANCH ruleset

Add a fail-closed scheduled and local conformance audit that requires the release
authorization App on every organization ruleset targeting `~DEFAULT_BRANCH`.

_Date: 2026-08-14; issue #803_

## Require reviewed repository-specific privileged-merge check policies

Generate each adopter's exact required-check, GitHub App, workflow ID, and workflow path policy into its reviewed privileged-merge callers instead of reading a non-portable Actions variable; fail adoption and terminal promotion closed on stale identities while retaining event-driven deferral for checks that are genuinely pending.

_Date: 2026-08-14; issue #801_

## Expose the derived next changelog version

`changelog.py next-version` now prints the exact tag accepted by `release` for the same version-prefix history, component, and repeated-fragment selection without writing, invoking Git, consuming fragments, committing, or tagging.

_Date: 2026-08-14; issue #798_

## Add the protected sequential runner deployment contract

Add an immutable generated deployment contract that retains pre-mutation authority,
proves an ordinary canary, and rolls an existing runner fleet sequentially behind the
consumer's protected production environment.

_Date: 2026-08-14; issue #629_

## Stage repository-bound hosted privileged routing

Route terminal merge authority for the canonical public repository to exact GitHub-hosted capacity and prepare the public runner consumer for immutable caller regeneration, while private consumers remain fail-closed on the admitted persistent selector.

_Date: 2026-08-14; id:20260815T015900Z; refs #676_

## Harden AI review adoption scope and retry

Repository-local generated AI review callers now require the complete nine-variable primary identity and policy family to be scoped atomically, while terminal promotion retry succeeds quietly before adoption only when both App identity variables are absent and fails closed with an actionable annotation for partial or malformed identity.

_Date: 2026-08-13; issue #789_

## Keep the release job token read-only

The canonical release workflow and generated snapshot caller now keep `GITHUB_TOKEN` at Contents read while the repository-scoped release App token alone performs the protected atomic push.

_Date: 2026-08-13; issue #784_

## Bind blocking AI findings to exact-head source

Require every blocking AI review finding to cite exactly one source line and carry evidence that deterministically matches that line at the receipt-bound reviewed head before the verdict can be published or authorize promotion.

_Date: 2026-08-13; issue #773_

## Fail privileged merge pin tests closed when their workflow step changes

The privileged merge pin test now structurally locates the current terminal merge step, rejects missing or empty scripts, and runs in the merge-gate CI group.

_Date: 2026-08-13; issue #772_

## Align the AI review prompt with the canonical verdict schema

The provider-neutral AI review prompt now requests canonical `review_first[].why`, with contract coverage proving provider variants normalize to that shape before rendered review comments consume it.

_Date: 2026-08-13; issue #771_

## Replace the release admin PAT with a repository-scoped App token

Canonical Node release callers now pass the dedicated release App identity
instead of `ORG_ADMIN_TOKEN`. The reusable workflow rejects empty and numeric
legacy IDs, delegates the supported client-ID grammar to the pinned action, and
mints a short-lived installation token constrained to the current repository and
Contents write before atomically pushing the immutable snapshot and tag.

_Date: 2026-08-13; issue #329_

## Retain redacted DeepSeek diagnostic replays

Retain a one-day, exact-head-bound replay when a completed DeepSeek response later fails canonical validation or publication. Bind the replay to the trusted validator revision and retain documented verdict fields while collapsing unknown provider field names and values to fixed sentinels.

_Date: 2026-08-13; id:20260813T214500Z_

## Report redacted DeepSeek review progress

Emit rate-limited, content-free progress for long DeepSeek thinking streams while keeping the paid call, token and cost bounds unchanged, and let repository Anthropic or OpenAI overrides clear inherited DeepSeek fallback settings before receipt creation.

_Date: 2026-08-13; id:20260813T210000Z_

## Scale the DeepSeek SSE wire bound with output tokens

DeepSeek review streams now allow bounded SSE and JSON framing overhead in
proportion to the existing maximum output-token envelope, so long reasoning
streams can reach their final verdict without weakening token, cost, model,
tool-call, or structured-output validation.

_Date: 2026-08-13; id:20260813T205400Z_

## Stream long-running DeepSeek code reviews

DeepSeek code reviews now consume bounded server-sent events and retain exact
usage, budget, model, JSON, and tool-free validation, preventing long reviews
from losing their entire verdict to an idle HTTP connection reset.

_Date: 2026-08-13; id:20260813T202800Z_

## Require bounded AI review for code-changing pull requests

Require code, executable dependency, workflow, policy, prompt, and agent-instruction changes to receive one or two cumulative AI review passes while retaining no-model validation for generated lockfile-only and non-agent documentation changes.

_Date: 2026-08-12; issue #767_

## Skip terminal promotion for human-path authorization

Terminal promotion retries and post-merge reconciliation now exit successfully without
privileged processing unless the newest exact-head authorization check carries the
dedicated App's persisted `ai-merge` marker.

_Date: 2026-08-12; issue #766_

## Validate trusted Node refs without consumer credentials

Add an opt-in trusted-ref lane to canonical Node CI that reuses the exact private-package cache, immutable auxiliary tree, credential scrub, rebuild allowlist, and ordered audit/smoke plan from secretless PR validation while admitting only pushes and explicit dispatches.

_Date: 2026-08-12; issue #761_

## Complete the authorization check run when receipt verification fails

A failed arm-receipt verification now completes the `AI review authorization` check run with
`conclusion=failure` instead of leaving it `in_progress` forever, so a PR shows a red check it can
act on rather than blocking indefinitely with no signal.

_Date: 2026-08-12; issue #759_

## Bind ruleset-created arm receipts to organization provenance

Accept authorization-arm receipts from organization-required workflow runs even
when a consumer has no repository-local caller, while binding those runs to the
canonical Verjson source repository and `refs/heads/main` organization rule.
Matching-path repository rules, mixed trusted and impostor rules, and wrong-ref
sources remain rejected.

_Date: 2026-08-12; issue #757_

## Transfer only verified private npm cache blobs

Populate exact locked private package URLs with npm's supported cache command, remove signed redirect indexes, and transfer only verified integrity-addressed blobs plus optional auxiliary content. The current Tequity payload falls from 157,265,920 bytes to 1,249,280 bytes while preserving the 80 MiB cap and credentialless build boundary.

_Date: 2026-08-12; issue #754_

## Validate consumer npm config before auxiliary checkout

Reject consumer-controlled `.npmrc` files before materializing the policy-bound auxiliary checkout, so a trusted auxiliary repository's root `.npmrc` cannot trip the consumer guard or affect authenticated npm acquisition.

_Date: 2026-08-12; issue #752_

## Bound secretless Node CI transfer storage

Transfer only a verified npm offline cache plus one optional immutable sparse auxiliary tree under an 81 MiB artifact-envelope budget, bind it to the exact run attempt, lockfile, and auxiliary identity, and delete it through a bounded no-checkout cleanup job without exposing credentials to consumer execution.

_Date: 2026-08-12; issue #750_

## Isolate scheduled workflow checkouts

Isolate scheduled workflow checkouts by run, attempt, and job, and contain sparse checkouts so reused runner workspaces cannot corrupt later jobs.

_Date: 2026-08-12; issue #743_

## Update the audited Claude action pin

Update the Claude review action and its load-bearing authorization contract to
the immutable `239e3a7` revision.

_Date: 2026-08-12; id:20260813T011405Z_

## Ignore the .worktrees/ agent scratch directory

`.gitignore` now covers `.worktrees/` alongside the `.claude/worktrees/` entry it
has carried since the tooling-residue block was added, so a clean checkout
reports a clean `git status` again.

_Date: 2026-08-12; id:20260812T131211Z_

## Refresh the CLAUDE.md active-issue pointers

Replace four stale pointers — three of which (#682, #677, #265) had already
closed — with the current open set, led by #701, which is the single item
standing between the organization and a working merge gate. This file loads into
every session, so a closed entry costs context in each one and misreports the
state of the work.

_Date: 2026-08-12; id:20260812T053000Z_

## Require the authorization arm from its own property-scoped ruleset

Split the `workflows` rule out of `main-protection` into
`ai-authorization-arm-required`, selecting `gate-rearm.yml@main` and scoped by the
`verjson-core-checks: enforced` repository property. `main-protection` keeps
`deletion`, `non_fast_forward`, `required_linear_history`, and `pull_request` at
`~ALL`, unchanged.

_Date: 2026-08-11; issue #748_

## Fail the arm rollout audit while the selected required workflow cannot run

Verify that the workflow `main-protection` currently selects declares at least one
trigger a ruleset can fire — `pull_request`, `pull_request_target`, or `merge_group` —
and check it before any rollout precondition. `#642` removed the `pull_request` trigger
from the selected `ai-review-merge.yml` on 2026-08-08, which left the required-workflow
record unschedulable: every governed pull request showed "Workflow configuration
invalid" and produced no run, no check, and no receipt for four days. The audit
previously validated only the *replacement* workflow, and reported the unrelated
fleet-readiness gap first, so it never named the live outage.

_Date: 2026-08-11; issue #745_

## Route the authorization arm through organization runner lanes

Route the trusted `pull_request_target` authorization arm through `VERJSON_LANE_TRUSTED` and `VERJSON_LANE_FALLBACK` instead of bypassing configured organization capacity with a hardcoded hosted runner.

_Date: 2026-08-11; issue #742_

## Confirm AI review verdicts through one provider-neutral boundary

AI review providers now hand one extracted JSON object to a shared canonical
validator. The validator accepts documented structured aliases, normalizes review
locations, enforces the merge-safety invariants, and returns bounded diagnostics for
invalid output without exposing model responses or pull-request diffs. Cross-provider
tests confirm equivalent Claude, OpenAI, and DeepSeek responses produce the same
canonical verdict without another model call.

_Date: 2026-08-11; issue #739_

## Retire stale gate extractors and preserve their live coverage

Remove the unregistered hold and re-arm tests that targeted retired workflow steps, and move their surviving terminal-hold and event-rearm cases onto the registered current arm and promotion harnesses.

_Date: 2026-08-11; issue #733_

## Audit the Claude Code action 2.1.227 pin

The AI review gate now uses the audited Claude Code action revision that bundles
Claude Code and Agent SDK 2.1.227, while retaining its immutable-SHA admission
contract and existing trusted inputs and structured outputs.

_Date: 2026-08-11; id:20260811T113822Z_

## Keep human approval available beside opt-in AI review

Keep deterministic CI and branch protection authoritative when AI is skipped or unavailable, and make AI approval or merge capability an exact receipt-bound variable.

_Date: 2026-08-10; issue #732_

## Stage the generated changelog contract as a required Node check

Declare `changelog-contract` as a required Node context and gate its organization rollout on exact property-selected heads, canonical generated-artifact byte identity, merged rollout code, full preimage stability, explicit human acknowledgement, and exact post-write verification.

_Date: 2026-08-10; issue #731_

## Keep AI review classification executable

Remove a stale model-variable reference that stopped ordinary code pull requests before AI review, and make the extracted classifier contract fail on any non-zero execution.

_Date: 2026-08-10; issue #729_

## Require the trusted authorization arm before dispatching AI review

Make the trusted `pull_request_target` authorization arm the deployable replacement for the ruleset's dispatch-only AI workflow, with a fail-closed fleet credential audit and a non-vetoing human path.

_Date: 2026-08-10; issue #728_

## Standardize the first AI review pass on Haiku 4.5

The single automatic paid AI-review pass now defaults to Claude Haiku 4.5 with a $1.00 cap for every full-review diff while preserving receipt-bound explicit overrides and fail-closed budget exhaustion.

_Date: 2026-08-10; issue #725_

## Treat stale promotion retries as no-ops

Terminal promotion now validates trusted workflow provenance and exits successfully before receipt verification when an authorized PR head has already been superseded, while preserving every fail-closed check for the current open head.

_Date: 2026-08-10; issue #723_

## Remove the privileged route's organization-variable PAT dependency

Pass the privileged runner lane through the trusted generated caller and validate the exact self-hosted general selector before scheduling terminal merge, eliminating an unnecessary organization-variable API credential.

_Date: 2026-08-10; issue #721_

## Diagnose and align arm receipt reads

Arm receipt verification now reports phase-specific, credential-redacted GitHub API failures, uses the least-privilege REST pull-request read instead of an implicit GraphQL query, and declares every reusable-job permission required to verify immutable provenance.

_Date: 2026-08-10; issue #719_

## Generate the universal AI review caller

Generate the repository-local AI review dispatcher alongside the re-arm and
privileged-merge callers so universal gate adoption cannot fail with a workflow
dispatch 404 after publishing an authorization receipt.

_Date: 2026-08-10; issue #716_

## Preserve App mutation phase diagnostics

Write dedicated-App failure annotations to stderr so command substitution cannot swallow the rejected operation, while retaining sanitized GitHub request diagnostics and request IDs without exposing the installation token.

_Date: 2026-08-10; issue #713_

## Identify authorization App mutation failures precisely

Bind the dedicated GitHub App token explicitly on every approval and check mutation, validate the minted App and installation identity before mutation, and preserve phase-specific GitHub response diagnostics so approval and check failures can no longer collapse into one ambiguous 403.

_Date: 2026-08-10; issue #710_

## Support a secondary Redis-compatible CI service

Let Node CI callers opt into a Redis-compatible cache container alongside the existing Postgres service, with an isolated dynamic loopback port, environment placeholder substitution, readiness checking, and always-run teardown.

_Date: 2026-08-10; issue #706_

## Keep generated release callers Renovate-inert

Render generated Node-version fields as literal GitHub expressions so Renovate cannot desynchronize release callers from their generated contract tests while Actions still receives the selected Node version.

_Date: 2026-08-10; issue #700_

## Preserve dedicated App API diagnostics

Write dedicated App API responses to explicit files so command substitution
cannot swallow operation names, request diagnostics, or fail-closed status
(#713).

_Date: 2026-08-10; id:20260810T170805Z_

## Require immutable pins for every active GitHub Action

Pin every active remote Action reference to a reviewed commit and enforce the invariant repository-wide with semantic YAML and mutation tests.

_Date: 2026-08-09; issue #696_

## Acquire private container dependencies outside BuildKit

Acquire the exact approved private `@verjson/*` dependency graph from the reviewed lockfile without lifecycle execution, bind credential use to the immutable called-workflow revision and an allowlist already reviewed on the base branch, then expose only a lock-bound, credential-free `node_modules` tree as a named BuildKit context.

_Date: 2026-08-09; issue #690_

## Make generated container contracts coexist

Generate the candidate and stable-release manifest validator as one byte-identical pinned artifact so consumers can adopt the complete container lifecycle and pass both generated contract suites.

_Date: 2026-08-09; issue #687_

## Serialize promotion-retry workflow names safely

Allow generated terminal-promotion retry callers to name punctuation-rich GitHub workflows such as `Integration (event-hub e2e)`. The generator now emits compact JSON as a YAML flow sequence, rejects control characters and GitHub expressions at the boundary, and shell-quotes the canonical JSON in its regeneration command.

_Date: 2026-08-09; issue #685_

## Ignore scoped root projects during secretless dependency validation

Allow scoped `@verjson/*` root projects to use secretless Node validation by exempting only the package lock's exact root entry while preserving allowlist and canonical URL checks for every installed internal dependency.

_Date: 2026-08-09; issue #682_

## Add secretless Node pull-request validation for approved private dependencies

Add an opt-in canonical Node CI route that acquires allowlisted internal dependencies without lifecycle scripts, then executes pull-request code in a separate job without package, Git, cloud, or OIDC credentials ([ADR 0086](../docs/decisions/0086-secretless-node-pr-validation/README.md)).

_Date: 2026-08-09; issue #680_

## Restore the privileged merge secrets contract

Wire the split review and privileged merge workflows into a mutation-tested CI contract that keeps the broad merge token away from PR validation and keeps the terminal merge path restricted to immutable trusted verifier material.

_Date: 2026-08-09; issue #677_

## Stage organization-authoritative privileged route resolution

Adds a checkout-free resolver that reads the Verjson privileged lane with a dedicated
read-only token and gives that selector first precedence for regenerated callers. The
existing route remains available only during caller migration; the live privileged
variable remains on the DigitalOcean general fleet.

_Date: 2026-08-09; issue #676_

## Isolate Actions CI source checkouts

Actions CI now checks out each matrix group and the actionlint job into
run-unique source directories before execution or job-temporary copying. This
prevents a co-located runner's checkout cleanup from removing another job's
workflows or group runner during setup.

_Date: 2026-08-09; issue #675_

## Pin the Claude review action and its trusted bot contract

Pin every Claude review invocation to one audited immutable revision, record exact trusted bot source logins without wildcard admission, and gate organization-managed Renovate updates with focused authorization tests.

_Date: 2026-08-09; issue #649_

## Restore trusted post-merge follow-ups

Restore exact-head, evidence-bound follow-up issue filing and same-repository branch cleanup after terminal AI promotion without granting the reconciler merge or dispatch authority.

_Date: 2026-08-09; issue #640_

## Prune closed active-issue pointers

Remove closed issue pointers from the repository instructions so future agent
sessions load only work that remains active.

_Date: 2026-08-09; issue #636_

## Add immutable container candidate publication contract

Add the canonical reusable workflow, generator, semantic validator, and generated contract test for complete, attested, digest-addressed container candidates on successful default-branch builds.

_Date: 2026-08-09; issue #628_

## Add immutable stable container promotion

Adds a dispatch-only, fail-closed stable container release contract that promotes verified candidate digests without rebuilding or deploying.

_Date: 2026-08-09; issue #627_

## Audit organization Actions secret scope before mutation

Record every organization Actions secret's justified consumer boundary and run a trusted read-only schedule that fails closed when live visibility or selected-repository grants drift from that reviewed policy (ADR 0088).

_Date: 2026-08-09; issue #265_

## Make runner-free AI gate waiting a structural contract

Prohibit both AI review and privileged merge jobs from polling or sleeping while external CI is pending, and generate the consumer completion bridge that re-enters exact-head terminal promotion for #341.

_Date: 2026-08-09; id:20260809T221500Z_

## Pin generated privileged callers to immutable contract revisions

Requires an immutable canonical contract SHA when generating privileged merge callers
and records the same SHA in each caller's reproducible regeneration command
(Verjson/.github#676; ADR 0085).

_Date: 2026-08-09; id:20260809T172200Z_

## Eliminate captured-value pipeline races in generated changelog checks

Generated changelog contract checks now redirect captured workflow values into validators, preventing early-exit consumers from turning intended negative-test diagnostics into `printf: Broken pipe` failures under `pipefail`.

_Date: 2026-08-09; id:20260809T170500Z; refs #659_

## Update container workflow action majors

Update the container candidate and stable-promotion workflows to audited,
immutable pins for the current Docker, artifact-download, and provenance action
majors. Existing runner and workflow contracts remain green with the Node 24
runtime floor.

_Date: 2026-08-09; id:20260809T145845Z_

## Bind review policy into terminal promotion

Carry the exact opaque review-policy envelope through immediate terminal
promotion, deterministic-CI retry, hold-removal recovery, and generated reusable
callers. The terminal verifier binds it to the immutable arm receipt and exact
head before any merge-readiness decision, without dispatching another paid review.

_Date: 2026-08-08; issue #664_

## Accept documented Responses reasoning items

Allow documented completed Responses API `reasoning` output items before or
after exactly one completed assistant message. Unknown output types, tool calls,
multiple messages, malformed or incomplete reasoning, refusal/error evidence,
wrong models, and usage beyond the hard preflight envelope still fail closed.

_Date: 2026-08-08; issue #660_

## Make generated changelog validation deterministic under pipefail

Feed captured generated contract tests to syntax validation through a file so policy diagnostics cannot be replaced by producer SIGPIPE failures.

_Date: 2026-08-08; issue #659_

## Preserve canonical review policy across dispatch

Carry the exact six-field AI review policy across Actions outputs, receipts,
and `workflow_dispatch` as a strictly validated base64url envelope. Trusted
consumers reject malformed or non-canonical encodings before provider, model,
budget, actor permission, or pricing policy can be used.

_Date: 2026-08-08; issue #657_

## Receipt-bound AI review provider and budget policies

- Allow organization variables to select a primary Anthropic policy and an explicit re-review policy.
- Add one-call, tool-free OpenAI `gpt-5.6-luna` review with a conservative hard dollar ceiling and receipt-bound long-context pricing evidence.
- Embed the bounded PR metadata/diff in tool-free requests, strictly validate completed response evidence, and require a currently authorized maintainer for explicit paid re-review.
- Keep trusted developer instructions role-separated from JSON-encoded, PR-controlled review data so diff content cannot redefine the review policy.

_Date: 2026-08-08; issue #655_

## Replace native auto-merge with terminal event-driven promotion

Complete authorized merges with exact-head admin promotion after declared CI, without polling or paid retries.

_Date: 2026-08-08; issue #653_

## Require dedicated App approval before native auto-merge

Make the receipt-bound authorization App publish and verify an exact-head approving review before its check can succeed, and reject promotion while GitHub still reports `REVIEW_REQUIRED`.

_Date: 2026-08-08; issue #650_

## Admit the trusted Actions review dispatcher

Allow the exact `github-actions` dispatcher identity through every AI-review model pass only after immutable arm-receipt and pending dedicated-App check validation.

_Date: 2026-08-08; issue #647_

## Mint AI authorization App tokens by client ID

Migrate pinned GitHub App token actions to the supported client-id input while retaining numeric App identity verification.

_Date: 2026-08-08; issue #646_

## Invoke the arm-receipt verifier portably

Run the immutable arm-receipt verifier explicitly through Bash after sparse checkout, preserving fail-closed authorization when executable file modes are unavailable.

_Date: 2026-08-08; issue #644_

## Limit each head to one automatic paid AI review

Limit an eligible PR head to one automatic paid AI review at its selected budget; inconclusive outcomes now fail closed and require an explicit maintainer `re-review` before another paid attempt.

_Date: 2026-08-08; issue #637_

## Delegate AI-authorized merges to GitHub native auto-merge

Replace runner-held CI polling with an exact-head AI authorization check and GitHub native auto-merge, deduplicating automatic model review to once per PR head.

_Date: 2026-08-08; issue #632_

## Normalize AI review-first locations

Preserve otherwise usable AI-review verdicts by reducing ranged or comma-separated human review pinpoints to one canonical `file:line`, while leaving blocking-finding validation fail-closed.

_Date: 2026-08-08; issue #631_

## Give generated Node releases a job-writable changelog cache

Generated Node release verification and publication now override persistent runner cache paths with a cold, job-scoped changelog tool cache beneath `runner.temp`.

_Date: 2026-08-08; issue #630_

## Define immutable container release and protected runner deployment

Accept ADR 0078: main publishes immutable candidates, explicit release promotes the exact attested digest set, and an independently approved environment deploys it through a canary and sequential rollout.

_Date: 2026-08-08; issue #626_

## Isolate Actions CI contracts from runner bootstrap labels

Clears the routed runner's ambient `RUNNER_LABELS` value at the shell-contract
boundary, so extracted reusable-workflow fixtures observe only inputs they set
explicitly and behave the same on developer and self-hosted environments.

_Date: 2026-08-08; issue #624_

## Isolate Actions CI matrix groups

Runs each Actions CI matrix group from its own job-temporary worktree so
parallel contract tests cannot observe or overwrite another group's fixtures on
a shared self-hosted runner.

_Date: 2026-08-08; issue #622_

## Remove undeclared make dependency from actions-ci

The actions-ci manifest now invokes the canonical renderer and ADR-index scripts
directly, while the Makefile remains a developer convenience surface. Its portability
contract validates recipes statically, so routed minimal runners no longer need a
system `make` binary.

_Date: 2026-08-08; issue #615_

## Surface reusable gate failure diagnostics

- Link failed trusted gate runs directly and report their failing jobs and steps from the privileged merge check.

_Date: 2026-08-08; issue #613_

## Require actionable merge-gate blocking verdicts

Reject blocking AI-review verdicts without structured actionable findings, validate sensitive review locations, and retry semantically unusable model output within the existing bounded attempt chain.

_Date: 2026-08-08; issue #611_

## Validate split generated-artifact callers

Generated changelog contract tests now validate both callers in the supported
split topology, including their shared immutable pin, ADR opt-in, and pinned
ADR-index generator.

_Date: 2026-08-08; issue #610_

## Give Node CI a job-writable changelog cache

Scopes the changelog tool cache used by Node CI to the runner's job-temporary
directory and clears the persistent override for the contract's isolated test
fixtures, so cold-cache tests do not depend on writable runner state.

_Date: 2026-08-08; issue #609_

## Make merge-gate review pagination portable

Collect paginated prior-review responses with jq so public adopter runners whose GitHub CLI predates `gh api --slurp` can publish a non-blocking gate verdict while API and payload failures remain fail-closed.

_Date: 2026-08-08; issue #608_

## Pin the authorization App token action to v3

Update both trusted authorization workflows to the immutable v3 release of `actions/create-github-app-token`. Strengthen the security contract to require the official action, a full lowercase 40-hex commit SHA, and the same pin at both token-minting sites.

_Date: 2026-08-08; id:20260808T184150Z_

## Keep actions-ci grouping contract stable as setup steps evolve

The grouping contract now locates the named group runner step instead of assuming its
ordinal position, so adding bounded setup steps does not invalidate an unrelated CI
topology assertion.

_Date: 2026-08-08; id:20260808T130000Z_

## Restore private-repository merge gates

- Remove the unavailable artifact-attestation requirement from private consumer gates while preserving required-workflow entry provenance and keeping repository-authored reusable callers non-authoritative.

_Date: 2026-08-07; issue #602_

## Allow already-stamped first package publication

- Let the reusable Node publisher stamp a package whose scaffold version already matches the first dispatched release.

_Date: 2026-08-07; issue #593_

## Verify the tagged tree when resuming publication

- Make resumed generated Node releases run their verification suite against the existing immutable tag that publication consumes.

_Date: 2026-08-07; issue #591_

## Resume publication from an existing release snapshot

- Let generated Node release callers verify and reuse an existing immutable snapshot after a mid-release failure, while rejecting conflicting tags and snapshots.

_Date: 2026-08-07; issue #588_

## Keep the Node CI database alive through consumer tests

The reusable Node CI workflow now tears down its optional database container
after every install, build, test, lint, and cache step. Cleanup remains
failure-safe, container-ID-scoped, and suppressed on the deferred Renovate
no-op path.

_Date: 2026-08-07; issue #585_

## Bootstrap Node before release input validation

- Run Node-dependent package-directory validation only after `actions/setup-node`, allowing publication on bootstrap-clean self-hosted runners.

_Date: 2026-08-07; issue #583_

## Allow the scaffold version on a package's first release

Generated Node release callers now accept a dispatch whose version already matches the scaffold manifest.

_Date: 2026-08-07; issue #579_

## Align required checks with generated artifacts

- Make the plan-only required-check contract and read-only audit recognize the canonical `generated-artifacts / validate` context emitted by scaffolded package repositories.

_Date: 2026-08-07; issue #577_

## Bound generated GitHub Release notes

Generated Node releases now publish exact changelog snapshots up to 125,000 bytes and conservatively truncate oversized bodies on a line boundary with an immutable link to the full tagged snapshot.

_Date: 2026-08-07; issue #571_

## Authenticate generated release verification hooks

Generated Node release callers now expose the private-package read credential to the verification-hook/default-suite step, allowing supported hooks to query private package metadata before a release mutates history.

_Date: 2026-08-07; issue #569_

## Force local paths for generated package publication

Generated multi-package Node releases now pass explicit local paths to npm, preventing a configured directory name from resolving and attesting an unrelated registry package.

_Date: 2026-08-07; issue #561_

## Verify every published package at the dispatched version

Stamp every configured release package before running the verification suite.

_Date: 2026-08-07; issue #557_

## Reject duplicate ADR numbers

The ADR index generator now fails before rendering or checking when two
decision directories reuse the same four-digit number, and its error names
both conflicting paths. Fixture coverage preserves distinct supersession while
preventing ambiguous durable references.

_Date: 2026-08-07; issue #555_

## Generate restart-safe multi-package Node releases

Generated Node release callers can now publish validated repository-relative compatibility packages alongside the implicit root package.

_Date: 2026-08-07; issue #550_

## Exercise malformed npm pack metadata rejections

Restart-safe Node release tests now prove that ambiguous multi-package pack output and a packed version differing from the dispatched release both fail before registry publication or reconciliation.

_Date: 2026-08-07; issue #549_

## Assert observable registry proof for publication reruns

Generated release contracts now enforce the authenticated package identity and integrity evidence consumed by restart reconciliation without prescribing a redundant registry identity command.

_Date: 2026-08-07; issue #548_

## Expose portable CI command entry points

Add standard Make targets for workflow linting, the documented behavioral smoke test, NEXT rendering, and ADR-index validation while keeping GitHub merge control-plane workflows and runner availability outside the portability boundary.

_Date: 2026-08-07; issue #537_

## Make generated Node publication restart-safe

Generated Node release callers can now recover when npm accepts a package but GitHub Release creation fails, without overwriting immutable packages or tags.

_Date: 2026-08-07; issue #535_

## Require dependency updates to add a changelog fragment

Canonical pull-request validation now requires a new valid `NEXT/` fragment when supported dependency manifests or lockfiles change, including bot-authored updates.

_Date: 2026-08-07; issue #524_

## Parameterize generated Node release scope and runtime

The canonical Node release generator now accepts validated npm-scope and Node-version options while preserving `@verjson` and Node 24 as backward-compatible defaults.

_Date: 2026-08-07; issue #520_

## Stamp the dispatched npm version before release builds

Generated Node release callers now apply the dispatched package version before both verification and publish-time builds, preventing artifacts from embedding the stale branch version.

_Date: 2026-08-07; issue #519_

## Detect queued jobs with unsatisfiable runner selectors

Add a report-only pass to the scheduled fleet watchdog that detects queued
self-hosted jobs whose full label set matches no online organization runner
admitted to the requesting repository. Reports identify the repository, run and
job, labels, queue age, and whether the cause is labels/offline capacity or a
runner-group admission boundary.

_Date: 2026-08-07; issue #511_

## Delete the retired semantic-release tooling and audit surface

Remove the semantic-release package and lockfile, Node floor guard, programmatic runner,
audit wrapper and allowlist, and the scheduled persistent-cache probe. The publish-only
`node-release.yml` contract remains covered, as do the live workflow and composite-action
pinning and cache controls.

_Date: 2026-08-07; issue #509_

## Ignore retired workflow runs during gate discovery

Exclude skipped trusted runs from newest-verdict selection, allowing an older active gate at the same head to remain authoritative after required-workflow migration.

_Date: 2026-08-07; issue #506_

## Re-arm the gate after every normalized hold spelling

Re-arm the merge gate when any hold label recognized by its separator normalizer is removed, while denying the gate's own `re-review` cleanup event.

_Date: 2026-08-07; issue #497_

## Record that one transient 500 on the PR file list reddens the privileged merge

`privileged_merge` reads the PR file list once, and a `500` there is fatal:

_Date: 2026-08-07; issue #490_

## Bump js-yaml past the omap DoS advisory that had reddened every PR

`GHSA-5p4m-2wfm-xmqj` was published against `js-yaml` (quadratic CPU consumption resolving
`!!omap`, CVE-2026-59870 unbackported, CVSS 7.5, range `>=4.0.0 <4.3.1`). Nothing in this
repository changed; the advisory appeared, `scripts/release-tooling-audit.sh` failed closed
as designed, and `shell-tests` went red on `main` and on every open pull request at once.

_Date: 2026-08-07; issue #488_

## Let the privileged merge poller reach the overflow lane its dispatcher already had

`ai-privileged-merge.yml`'s `runs-on` omitted `VERJSON_RUNNER_OVERFLOW`, so the escape hatch
ADR 0053 built for "jobs that poll the pool they wait on" was wired to the cheap dispatcher
and withheld from the ~40-minute poller — the job 0053's own table measures at 29% of lane
runtime, and the one whose file says of itself, two lines above its poll loop, that it "polls
the SAME pool as the CI it waits for" (#363).

_Date: 2026-08-07; issue #487_

## Bridge the re-review label lane, for that label only

`ai-review-merge.yml` documents a re-review lane — apply `re-review` and the gate re-runs — and
guards it at `:165`. That lane had never fired: the gate is installed as a required workflow by
the `main-protection` ruleset, which schedules it for `opened`, `synchronize` and `reopened`
only, so its own `labeled` type produces no run. Measured dead on throwaway PR #471 (a probe
workflow's `labeled` run fired; the gate produced none).

_Date: 2026-08-07; issue #481_

## Make the terminal hold and the sensitive-path classifier fail closed

Three `if jq -e '<predicate>'` sites in `ai-review-merge.yml` read a **jq error** as a
`false` predicate, because `jq -e` exits non-zero for both and `if` cannot tell them apart.

_Date: 2026-08-07; issue #480_

## Generate immutable gate re-arm callers for fleet rollout

Generate thin, immutable-SHA-pinned gate re-arm callers so repositories can bridge activity types that the organization required workflow never receives, with the canonical security guards bound to reviewed code.

_Date: 2026-08-07; issue #477_

## Retry transient trusted merge dispatch failures

Retry bounded GitHub 5xx and transport failures while dispatching the trusted merge continuation, while failing immediately on structural 4xx responses.

_Date: 2026-08-07; issue #475_

## Add an ownership-bounded gate coverage audit

Add a dry-run-first fleet audit that reports every exact open pull request missing the current `gate` context and proposes a supported PR-associated retrigger.

_Date: 2026-08-07; issue #474_

## Correct the stated root cause of the folded-scalar front-matter failure

The `#454` entry in `CLAUDE.md` said `parse_frontmatter` "rejects a key with an empty value".
Traced against `scripts/changelog.py:90-97`, that is not what happens, and the difference sends
a reader to the wrong branch:

_Date: 2026-08-07; issue #457_

## Accept block scalar release summaries

Changelog summaries now accept folded and literal block scalars, so release notes can wrap naturally in fragment metadata.

_Date: 2026-08-07; issue #454_

## Make turn exhaustion visible to the gate's retry-outcome probe

The `retry_outcome` probe matched only `error_max_budget_usd`, so a review pass that died on
the 15-turn cap was invisible: `turns_exhausted` did not exist and `budget_exhausted` stayed
`false`. The run surfaced as an unexplained gate failure — the same illegible shape #293 fixed
for the budget, still present for the sibling limit.

_Date: 2026-08-07; issue #442_

## Reject verdicts produced by terminal-error review passes

Treat schema-valid output from a terminal-error review pass as no verdict, so the merge gate fails closed with an inconclusive review instead of publishing fabricated findings.

_Date: 2026-08-07; issue #441_

## Detect matrixed reusable callers before requiring canonical checks

The required-check classifier now rejects matrixed reusable caller jobs because GitHub never emits their canonical unmatrixed check names.

_Date: 2026-08-07; issue #431_

## Make fleet-watchdog scheduler gaps explicit

The scheduled fleet watchdog now records the interval from its preceding run
in the durable job summary and emits a warning when GitHub delays the nominal
15-minute schedule beyond 30 minutes. ADR 0072 keeps the privileged workflow
schedule-only and confines cadence inspection to a read-only job token.

_Date: 2026-08-07; issue #414_

## Enforce an immutable contract_ref before changelog-validate checks anything out

`changelog-validate.yml` described `contract_ref` as an "Immutable Verjson/.github commit"
and then handed it straight to `actions/checkout`'s `ref:`, which accepts **any** ref of this
repository. The job executes `python3 .changelog-contract/scripts/changelog.py` from the
result, so that input decided what code ran. `required: true` guaranteed only that it was
present. Around 90 repositories call this workflow.

_Date: 2026-08-07; issue #412_

## Honor explicit consumer runner labels on merge dispatch

Apply a reusable caller’s explicit `runner_labels` fleet selection consistently to `preflight`, `gate`, and the metadata-only `dispatch-merge` job, while preserving every absent-input visibility and lane fallback.

_Date: 2026-08-07; issue #411_

## Let the generated render guard tolerate only an emptied NEXT/, not every failure

The contract test emitted by `scripts/gen-changelog-caller.sh contract-test` guarded its
render block on the renderer's exit status alone:

_Date: 2026-08-07; issue #399_

## Retry transient review-diff fetch failures

Retry bounded GitHub 5xx and transport failures while fetching both the AI
review diff and the privileged merge's complete pull-request file list, and
fail immediately on structural 4xx responses.

_Date: 2026-08-07; issue #394_

## Add component-scoped changelog releases

Allow multi-package repositories to scope fragments to explicit component streams while keeping unscoped rendering and release as the safe backward-compatible default. Disposable verification fetches and executes the generated artifacts at the documented immutable component-contract commit.

_Date: 2026-08-07; issue #390_

## Bind documented changelog metadata to the recommended immutable pin

Changelog adoption docs now distinguish v2.2.0 capabilities from the recommended immutable commit, and CI executes that pin against every advertised metadata key.

_Date: 2026-08-07; issue #388_

## Verify that a dispatched merge actually occurred

Fail `dispatch-merge` when its trusted continuation request succeeds but the pull request does not reach a merged state.

_Date: 2026-08-07; issue #384_

## Add a verified changelog tooling cache contract

Let generated changelog renderers and contract tests use a runner-preloaded,
commit-keyed tool cache after verifying the exact pinned digest, with immutable
raw GitHub download as a repair fallback.

_Date: 2026-08-07; issue #379_

## Enforce changelog release impact centrally

Add canonical `major`, `minor`, and `patch` impact metadata and require the exact corresponding SemVer bump before a selected release can mutate snapshots or fragments.

_Date: 2026-08-07; issue #378_

## Make trigger-scope enforce the path invariants it claims

`scripts/trigger-scope.test.sh` left two path-trigger regressions green.

_Date: 2026-08-07; issue #360_

## Deduplicate AI-review follow-ups before parallel filing

Deduplicate identical AI-review follow-ups by their canonical marker hash before starting parallel issue workers, while retaining cross-run lookup and distinct findings.

_Date: 2026-08-07; issue #359_

## Make the workflow-files hold test actually depend on pagination

`require-secrets.test.sh` labelled a case "paginated workflow changes beyond 100 files", but
its `gh` stub returned the whole already-filtered fixture whatever flags it was given. So the
case never depended on `--paginate`, and **removing pagination from the production call left
the test green** — the guard would have missed a workflow file after file 100 and allowed a
privileged auto-merge where policy requires a human hold, with a passing test on top.

_Date: 2026-08-07; issue #358_

## Bind merge-gate routing tests to each job and target

Semantically evaluate the exact `preflight`, `gate`, and `dispatch-merge` runner routes for public, private, unresolved, and external targets, and reject visibility lookups that query anything but the target repository’s `.private` field.

_Date: 2026-08-07; issue #357_

## Make the changelog migration procedure executable

Run changelog migrations from an explicit consumer root using a disposable canonical checkout at one immutable contract SHA, generate executable artifacts, and normalize before enabling validation.

_Date: 2026-08-07; issue #354; refs #388_

## Make reusable-workflow release instructions immutable

Pin the copyable Node release example to an exact release and target GitHub Releases at the full commit SHA that was actually verified.

_Date: 2026-08-07; issue #353_

## Make generated changelog contract tests fail closed

Verify the canonical generated release caller binds both its reusable-workflow ref and `contract_ref` to one pin, accepts `refs` metadata across a real release, and distinguishes an empty `NEXT/` from renderer failures.

_Date: 2026-08-07; issue #349; refs #437, #418_

## Accept a NEXT/-relative selected fragment in the canonical release

`changelog-release.yml` forwards each selected entry to the engine unchanged, and release
callers naturally supply the repository-relative path they were given —
`NEXT/2026-08-02-issue-1-initialize-customer-lifecycle.md`. `release` indexed selection by
basename only, so that value was rejected with:

_Date: 2026-08-07; issue #328_

## Audit canonical privileged merge caller content

Make privileged-merge fleet conformance compare each consumer caller with the canonical generator output instead of accepting any file at the expected path.

_Date: 2026-08-07; issue #327_

## Fail when the privileged merge continuation is missing

The merge dispatcher now fails with actionable generator and secret-access
remediation when a repository lacks its trusted privileged continuation.
A scheduled read-only conformance audit reports missing callers or
`ORG_ADMIN_TOKEN` access across active Verjson repositories before merge
delivery is needed.

_Date: 2026-08-07; issue #326_

## Review publication pull requests over 300 files

Build the AI gate's review diff locally from the immutable checked-out PR head
and a freshly fetched base ref, removing GitHub's 300-file whole-diff endpoint
limit. Base-history fetches retain bounded retry and masked diagnostics; missing
merge history or local diff failure remains typed and fail-closed.

_Date: 2026-08-07; issue #323_

## Report a draft or held PR as a terminal no-op instead of a red privileged merge

`ai-privileged-merge.yml` fired on drafts and died with `::error::PR is draft` and exit 1, so
every pull request that followed this repository's own guidance — open non-trivial work as a
draft while an out-of-band review runs — carried a red `privileged_merge` check for as long as
it stayed a draft. A `hold` / `DO NOT MERGE` label did the same. Both are now terminal no-ops
with a notice naming the remedy, the shape ADR 0037 already established for the
workflow-files hold.

_Date: 2026-08-07; issue #263_

## Bound actions CI into cohesive parallel shell-test groups

`actions-ci` now runs platform, merge-gate, and changelog/release shell
contracts in three bounded parallel legs with fail-fast disabled. Every command
within a failed leg still runs, while an unmatrixed `shell-tests` aggregate
preserves the repository's existing required-check context.

_Date: 2026-08-07; issue #233_

## Reconcile privileged runner-lane admission and capacity

Validate `VERJSON_LANE_PRIVILEGED` alongside the trusted and untrusted lanes so an isolation cutover cannot silently target an inaccessible runner group or a selector with no online capacity.

_Date: 2026-08-07; issue #204_

## Keep the required Node CI context satisfied during Renovate deferrals

The reusable Node workflow now reports a successful `ci / build-test` no-op
when Renovate's release-age gate intentionally defers the suite. All checkout,
install, build, test, database, and cache steps remain suppressed until the
hold clears, while eligibility errors continue to fail open by running CI.

_Date: 2026-08-07; issue #191_

## Refuse unsigned reusable-caller merge provenance

Privileged merge no longer accepts a reusable workflow reference as proof the
gate produced an attestation. Consumer-authored callers remain supported for
review but require human merge until signed workflow provenance exists.

_Date: 2026-08-07; id:20260807T170000Z_

## Node publication consumes the contract-selected release

Reintroduce node-release.yml as a publish-only consumer of the exact version and tag created by the dispatched changelog contract.

_Date: 2026-08-07; id:20260807T140000Z; refs #455_

## Prune the closed re-review-lane entry from Active Issues

`#481` closed with #504, which bridged the `re-review` lane. `CLAUDE.md` loads into every
session, so the entry now costs context in each one while misreporting the state of the work.

_Date: 2026-08-07; id:20260807T044500Z_

## Prune closed issues from the CLAUDE.md Active Issues list

`CLAUDE.md` loads into every session, so a closed entry in `Active Issues` costs context in
each one *and* misreports the state of the work — which is what that list's own closing
instruction exists to prevent. Five entries closed today are removed: #263, #312, #399, #412
and #482.

_Date: 2026-08-07; id:20260807T040000Z_

## Move the documented contract pin to the commit that carries release-node

`docs/changelog/migration.md` documented four generator commands under one `$PIN`, and the
pin could only run three of them. `release-node` landed with the fix for #463/#464/#465;
the pin still named `3d5f2896`, which predates it. An adopter following the guide verbatim
got three files and a loud failure on the fourth.

_Date: 2026-08-06; issue #484_

## Declare the core required checks per stack, so GitHub waits instead of the gate

ADR 0058 decided the organization would declare required status checks and let GitHub wait
for them. Nothing had been declared: `main-protection` (18098028) carried no
`required_status_checks` rule at all, so every check in the org was advisory. Steps 1–5 of
that ADR are now executed, and the
[ADR is amended](docs/decisions/0058-github-waits-for-checks-not-the-gate/README.md) with
what the execution corrected.

_Date: 2026-08-06; issue #472_

## Marking a draft ready re-enters the merge gate again

Marking a draft pull request ready — or removing a `hold` — now re-enters the merge
gate on its own, instead of leaving the pull request wedged until someone pushed a
commit or dispatched the gate by hand.

_Date: 2026-08-06; issue #468_

## fix(changelog): a release dispatched off the default branch fails instead of reporting success

`changelog-release.yml` guarded the default branch with a job-level `if:`. GitHub
reports a skipped job as successful to its caller, so dispatching a release from
any other ref produced a green run that released nothing — no snapshot, no tag, no
publish — and a caller's `publish` job then ran against a tag that was never
created.

_Date: 2026-08-06; issue #466_

## feat(changelog): generate the release caller so it verifies before it tags

`scripts/gen-changelog-caller.sh release-node <sha>` now emits an adopter's `.github/workflows/release.yml`, so the release caller stops being hand-copied from a sibling. The generated shape runs a `verify` job — default-branch guard, exact `v`-prefixed SemVer guard, tag-absence guard, and the repository's full suite — that `snapshot` declares in `needs:`, installs with `NODE_AUTH_TOKEN` while publishing with `GITHUB_TOKEN`, passes an explicit `runner:` so both halves of one release share a pool, and is `workflow_dispatch` only at the pinned contract commit.

_Date: 2026-08-06; issue #463_

## fix(changelog): the generated contract test accepts a fragment carrying refs

The contract test emitted by `scripts/gen-changelog-caller.sh contract-test`
rejected an issue-form fragment that also carried `refs:`, reporting `issue
back-link missing from the rendered log` for an entry the engine renders
correctly. Its back-link assertion anchored `_$` immediately after the issue
number, while the renderer appends the `; refs #a, #b` suffix that `refs` exists
to produce. The assertion now matches the shape the engine emits.

_Date: 2026-08-06; issue #461_

## chore(actions): retire node-release.yml in favour of a dispatched release

`node-release.yml` refuses to run. Its first step, before any checkout and with
no condition, names `changelog-release.yml` as its replacement and exits 1.

_Date: 2026-08-06; issue #460_

## fix(actions): gate exit status reflects the merge, not branch cleanup

The privileged merge step no longer passes `--delete-branch` to `gh pr merge`.
The head ref is deleted afterwards, as a separate call that cannot fail the
step.

_Date: 2026-08-06; issue #458_

## docs(claude): record three defects the fleet pin bump exposed

Working notes now carry the three org-level defects that surfaced while moving
the fleet to contract pin `f12dca7`.

_Date: 2026-08-06; issue #455_

## Dismiss stale gate reviews after a finding is fixed

Dismiss an old-head `github-actions[bot]` changes-requested review before a
new-head non-blocking verdict turns green, allowing corrected pull requests to
recover without manual intervention.

_Date: 2026-08-06; issue #452_

## fix(changelog): preview the released form where adopters actually see it

The release-note preview now runs in `changelog-validate.yml`, so every
repository on the changelog contract sees what a release would freeze into
`CHANGELOG/<version>.md` while its fragments can still be edited.

_Date: 2026-08-06; issue #449_

## fix(changelog): the generated renderer accepts --as-released

`scripts/render-next.sh --as-released` now works in an adopter repository
instead of exiting 2.

_Date: 2026-08-06; issue #443_

## The changelog check is now a required status check for contract adopters

The `changelog / validate` context is required on the default branch of the 23
repositories that carry the generated `changelog-validate.yml` caller, through a new
organization ruleset scoped by the `changelog-contract` repository property. The contract
was previously advisory: a branch that predated the caller could merge with no changelog run
recorded at all. Requiring the check does not require a PR to carry a fragment — that is a
separate decision about `check-pr`. Recorded in ADR 0061.

_Date: 2026-08-06; issue #31_

## chore(renovate): group action digest bumps into one pull request

Renovate now opens one pull request for all GitHub Actions digest bumps instead
of one per action, rebases only on conflict, and keeps at most three pull
requests open at a time.

_Date: 2026-08-06; id:20260806T193000Z_

## chore(repo): ignore the Serena symbol index

`.serena/` no longer shows up as untracked in `git status`.

_Date: 2026-08-06; id:20260806T014007Z_

## Generate shared artifact callers and ADR index tooling

`gen-changelog-caller.sh` can now emit a `generated-artifacts.yml` caller while
preserving the existing changelog-only caller. Repositories opting into ADR
index validation can acquire `gen-adr-index.sh` from the same immutable pin and
generate a caller that enables both checks; the generated contract test verifies
that pinned script before accepting `adr-index: true`.

_Date: 2026-08-05; issue #437_

## fix(changelog): the generated contract test reads quoted titles

The adopter contract test now unquotes front-matter scalars the way the engine
does, so a fragment titled `'fix(x): the thing'` stops being reported as a
missing title.

_Date: 2026-08-05; issue #436_

## fix(changelog): render an identity as written, not as normalised

A timestamp identity now renders `id:20260805T000000Z` instead of
`id:20260805t000000z`.

_Date: 2026-08-05; issue #434_

## feat(changelog): released snapshots carry the release note, not the diary

A released snapshot now keeps each entry's title and lead paragraph; the running
log keeps the whole body. `render-next --as-released` shows the released shape
while the fragments can still be changed.

_Date: 2026-08-05; issue #426_

## fix(changelog): reject a title whose quoting is ambiguous

`unquote_scalar` (#420) resolves a genuine quoted scalar and deliberately
leaves alone a title that merely opens and closes with a quote — `"a" and "b"`
is not one scalar, and stripping by position would corrupt it rather than tidy
it. That was the right parser behaviour and the wrong end state: the residue
reaches `CHANGELOG/<version>.md` verbatim, and a released snapshot is immutable,
so nobody can fix it afterwards.

_Date: 2026-08-05; issue #425_

## Tolerate unknown changelog metadata instead of rejecting it

`validate_metadata` raised on any key outside `{date, issue, id, title, refs}`.
Because every repository pins its own contract SHA, that made each future
metadata addition breaking: a fragment carrying a new key failed validation in
every repository that had not bumped yet, and failed again if a repository ever
pinned backward with such fragments still in `NEXT/`.

_Date: 2026-08-05; issue #424_

## Recognise generated-artifacts.yml as a changelog-contract caller

`classify-repo-stacks.sh` matched only `changelog-validate.yml` when deciding
whether a repository is on the changelog contract. ADR 0055 landed
`generated-artifacts.yml`, whose inner job is also `validate`, so a caller job
named `changelog` emits the `changelog / validate` context the core check
contract requires (ADR 0058).

_Date: 2026-08-05; issue #422_

## Resolve YAML quoting in changelog front matter

Front matter is parsed line-wise with `str.partition(":")`, and the right-hand
side was stored verbatim. YAML *requires* a quoted scalar wherever a value
contains `: `, which is the shape of every conventional-commit title, so
`title: 'Fix: thing'` rendered as `## 'Fix: thing'`. Reported against
`Verjson/verjson-ai` `CHANGELOG/v0.11.0.md`, where every heading is quoted.

_Date: 2026-08-05; issue #420_

## Declare a core check contract so GitHub can do the waiting the gate does in bash

Records the decision to stop managing the merge-gate poll deadlock and remove it, in
[ADR 0058](docs/decisions/0058-github-waits-for-checks-not-the-gate/README.md), plus the
first read-only step towards it.

_Date: 2026-08-05; issue #416_

## Stop baking a fleet label into every generated privileged-merge caller

`scripts/gen-privileged-merge-caller.sh` required a runner-labels argument and
emitted `runner_labels: '["self-hosted","general"]'` into every caller it
produced, because `ai-privileged-merge.yml` and `ai-review-merge.yml` declared
the input `required: true`. #401 cleared every `runs-on:` in this repository onto
lane variables, so the generator was the last place the org handed a fleet label
to consumers — one that only a pull request in each of ~90 repositories could
change. `Verjson/verjson-identity-lifecycle` carries the literal today.
`actionlint` cannot catch the class: its undeclared-label check inspects
`runs-on` arrays, and a label inside a string input is invisible to it.

_Date: 2026-08-05; issue #405_

## Centralize generated-artifact CI checks in a reusable workflow

New reusable workflow `.github/workflows/generated-artifacts.yml` (`on: workflow_call`)
owns checkout, lane-variable runner selection, `contents: read`, the timeout, and one
uniform failure report for generated-artifact validation. Consumers stop hand-writing a
`generated-docs` job — the copied plumbing that left `verjson-identity-lifecycle` queued
forever on the retired `[self-hosted, GCP]` label.

_Date: 2026-08-05; issue #404_

## Run the admission monitor off the pool it watches, and follow a group rename by variable

Every `[self-hosted, general]` job in this repository queued indefinitely while
five `general` runners sat idle. The pool had moved into runner group
`DigitalOcean` with `allows_public_repositories: false`, and this repository is
public, so no runner in that group was an eligible target. GitHub queues such a
job instead of failing it — no check-run diagnostic, no error. The merge gate
wedged behind it: #400 sat with `gate` and `dispatch-merge` green and
`privileged_merge` unable to start.

_Date: 2026-08-05; issue #401_

## Pass the rendered changelog to the contract test as a file, not an argument

The generated contract test handed the rendered `NEXT/` to `python3` through an
environment string. A single argv or environment string is capped at
`MAX_ARG_STRLEN` — a fixed 128 KiB, not the 2 MiB `ARG_MAX` that a size check
reads — so once an adopter's unreleased log crossed it, the suite died at the
render assertion:

_Date: 2026-08-05; issue #398_

## Assert the semantic-release pin as a property, not as a literal version

`node-workflow-pins.test.sh` hardcoded `25.0.8` in three assertions, so every
Renovate bump of `semantic-release` was red by construction. #396 carried a
correct, exact-pin bump — `25.0.8` → `25.0.9`, with `resolved` and a sha512
`integrity` — and failed for saying so:

_Date: 2026-08-05; issue #396_

## Keep the fleet watchdog, retarget it at the poll job the overflow lane cannot reach

#341 proposed deleting the fleet watchdog; #342, #343 and #355 are defects inside it. The
disposition is **keep and fix**, recorded in
[ADR 0056](docs/decisions/0056-fleet-watchdog-retained-and-retargeted/README.md). The merge
gate now also stops polling for CI in any repository that has declared required status
checks on the PR's base branch, because GitHub is already blocking the merge on those
contexts — the poll retires per repository as each one conforms, not org-wide on a date.

_Date: 2026-08-05; issue #341_

## Keep the hygiene suite inside its sandbox, and its fence parser inside the fence

`scripts/repo-hygiene.test.sh` was destructive to run in a live checkout. Its
`fixture()` helper returned the fixture path by printing it, so every caller was
a `$(...)` capture of whatever the helper's own commands printed. A reused
fixture name (`nested`, used at two cases) re-entered an already-committed
fixture, `git commit` found nothing to stage and reported "nothing to commit" on
**stdout**, and that report came back prepended to the path. The caller then ran
`mkdir -p "$path/docs"` — creating a newline-named directory in the repository
root — and `git -C "$path" add -A && git commit`, which walked up into the HOST
repository and swept the runner's in-progress work into a stray commit titled
`document under docs/`. Observed three times in one session (#340), and it also
meant the `docs/` edge case never exercised the behaviour it claimed to cover:
`git ls-tree` is prefix-relative, so checking the junk path reported "no root
README" for the wrong reason (#393).

_Date: 2026-08-05; issue #340_

## Add a reversible overflow lane for gate jobs that poll the shared pool

`ai-review-merge.yml`, `changelog-validate.yml`, and `changelog-release.yml` now consult
`VERJSON_RUNNER_OVERFLOW` ahead of `VERJSON_RUNNER_UNTRUSTED` and `VERJSON_RUNNER_DEFAULT`.
Setting it to `["ubuntu-24.04"]` moves those jobs to hosted runners; deleting the variable
restores the previous routing exactly, with no code change.

_Date: 2026-08-05; issue #118_

## ci(changelog): print the released shape in the check job summary

The `changelog` check in `generated-artifacts.yml` now renders
`render-next --as-released` into the job summary, so reviewers see what a
release would publish while the fragments can still be edited. A released
snapshot is immutable, so without this the released form is first seen when it
can no longer be changed.

_Date: 2026-08-05; id:20260805T213000Z; refs #426_

## docs(adr): record that the released-snapshot fix is forward-only

ADR 0059 records the two-audience rendering split and, explicitly, that no
re-render of already-released snapshots will be done.

_Date: 2026-08-05; id:20260805T210000Z; refs #426_

## Prune the Active Issues list to what is still open

`CLAUDE.md`'s Active Issues list loads into every session, so a closed entry
costs context in each one and misreports the state of the work. Four were stale:
#340 and #393 closed by #409, #350 and #377 closed earlier.

_Date: 2026-08-05; id:20260805T190000Z_

## Give the changelog release a push token the branch ruleset accepts

`changelog-release.yml` ends with an `--atomic` push of the snapshot commit and
its tag straight to the default branch. Every Verjson repository carries an
identical `main-protection` ruleset that forbids exactly that, and whose only
bypass actors are `OrganizationAdmin` and Renovate. `GITHUB_TOKEN` is neither, so
the wiring every adopter copied could not complete a release:

_Date: 2026-08-04; issue #389_

## Remove branch-selectable runner admission dispatch

Remove `workflow_dispatch` from runner admission reconciliation so a selected
branch cannot control code that receives `ORG_ADMIN_TOKEN`. Scheduled runs bind
checkout to their immutable default-branch `github.sha`; shared semantic contract
coverage rejects alternate triggers, checkout sources, execution surfaces, bare
sequence steps, and quoted executable keys. ADR 0033 records the boundary and the
residual #261/#265 organization-secret work.

_Date: 2026-08-04; issue #387_

## Require ShellCheck on every actionlint route

Enable ShellCheck explicitly for Verjson-owned and external workflow linting now
that the governed self-hosted image supplies it. Adjudicate the current findings,
pin the required invocation and missing-tool failure in the reusable actionlint
contract tests, and record the policy amendment in ADR 0026.

_Date: 2026-08-04; issue #362_

## Remove branch-selectable privileged watchdog dispatch

Remove `workflow_dispatch` from the privileged fleet watchdog because a selected
branch controls the workflow definition as well as checkout defaults. Scheduled
runs now bind checkout to their immutable default-branch `github.sha`; a wired
semantic contract locks the trigger, checkout, named-step, command, and
token-ordering boundaries across alternate valid YAML spellings. ADR 0049 records
the fix and the residual #261/#265 secret-scope work.

_Date: 2026-08-04; issue #350_

## Bind AI review evidence to the PR-head context

The merge-gate prompt now states that its workspace and `HEAD` are the pull
request head, never evidence of base-branch content. Lifecycle state such as
an inferred duplicate submission, stale head, closed PR, or already-merged PR
remains owned by deterministic API checks, preventing a byte-identical PR
checkout from becoming a false blocker without suppressing idempotency review.
ADR 0051 records the evidence boundary.

_Date: 2026-08-03; issue #377_

## Time-box npm-bundled ip-address advisory exceptions

Accept the three newly published `ip-address` advisories through 2026-08-10.
The affected copy is bundled in npm 11.19.0 behind SOCKS proxy support, while
trusted release jobs use organization-controlled endpoints without a SOCKS
proxy. npm 11 has no fixed release yet, bundled dependencies cannot be
overridden, and the release plugin does not support npm 12.

_Date: 2026-08-03; issue #374_

## Update release tooling past the new undici advisories

Refresh the release-tooling lockfile to `undici` 6.28.0/7.29.0 and npm 11.19.0.
This removes the newly published high-severity response-cache advisory without
adding an audit exception. npm's bundled `undici` remains on 6.27.0 with only
the moderate advisory, below the high/critical release gate.

_Date: 2026-08-03; issue #372_

## Review the new npm-bundled brace-expansion advisory

Add a seven-day, advisory-specific exception for the new brace-expansion DoS
bypass in npm's bundled release CLI dependency. The release path is trusted-only,
and the short review window keeps the exception blocking until npm 11 carries
the fixed brace-expansion 5.0.9.

_Date: 2026-08-03; issue #370_

## Release merge-control runners when stability-days defers a PR

Give gate preflight the status permission its defer classifier requires, and
make privileged merge runs terminate when Renovate's release-age
status is pending. A late-arriving hold also stops the gate immediately instead
of consuming its polling window, restoring ADR 0023's no-long-running-runner
invariant without weakening failed-check handling.

_Date: 2026-08-03; issue #368_

## A runner without gh fails the gate in a second instead of holding it for thirty minutes

`ci_wait` treated every non-zero return from `gh` as "the API is unavailable"
and retried. Exit **127** is not a transient API error — it is
`command not found`. On a runner without `gh` every call failed instantly and
identically, the loop spent all 60 attempts on it, and the job failed closed
after thirty minutes:

_Date: 2026-08-03; issue #363_

## Preserve documentation paths while validating fragment examples

The documented-fragment scanner no longer joins source paths to candidate names
with a space-delimited record it later reparses. Valid examples in tracked
Markdown paths containing spaces now reach the canonical changelog matcher
unchanged, with a committed-path regression fixture covering the case.

_Date: 2026-08-03; issue #356_

## Require immutable repository-hygiene policy refs

The reusable repository-hygiene workflow now rejects branches, tags, abbreviated
hashes, and malformed refs before checkout. A full commit SHA must also remain
reachable from `main`, preserving the existing reviewed-and-merged boundary;
focused behavioral coverage exercises mutable, unmerged, and merged refs.

_Date: 2026-08-03; issue #351_

## actionlint takes the fast lane on public targets, so the gate stops waiting on a starved job

ADRs 0047 and 0048 moved the merge gate onto elastic hosted capacity but left
the jobs it *waits on* behind. The gate got cheaper at waiting; nothing it waits
for got faster.

_Date: 2026-08-03; issue #346_

## A watchdog breaks merge-gate poll deadlocks on the self-hosted fleet

ADR 0048 removed the poll deadlock for public targets by routing merge-gate jobs
to elastic hosted capacity. Measuring after it landed showed how little that
covers: **88 of the 90 repositories in this organization are private**, so the
fix reaches two of them. Everything else still contends for six self-hosted
runners on which `gate` and `privileged_merge` sleep for 10-40 minutes while
polling for checks they cannot influence.

_Date: 2026-08-03; issue #336_

## Merge-gate jobs take the fast lane when the target repository is public

ADR 0047 gave short jobs a fast lane and deliberately left `gate` out of it, on
the grounds that a poll loop wastes a hosted minute exactly as it wastes a
self-hosted one. That is true about waste and wrong about starvation, and
starvation was the actual failure: sleepers only block anyone on a **fixed-size**
pool. Hosted capacity is elastic, so a sleeping gate blocks nobody.

_Date: 2026-08-03; issue #334_

## Short CI jobs get a fast lane, selected by variable rather than a hardcoded label

All six self-hosted runners carry the same labels, so the three
`VERJSON_RUNNER_*` pools name one queue three times. That queue mixes jobs with
opposite shapes: `gate` and `privileged_merge` hold a runner for 10-40 minutes
while *polling*, while `shell-tests` is minutes of actual CPU. On one queue the
sleepers evict the workers, and this repository's own validation ended up queuing
behind the consumer repositories it ships fixes to.

_Date: 2026-08-03; issue #332_

## Privileged merge stops re-paginating the PR file list on every poll

The privileged merge step holds a self-hosted runner for its whole polling
window, so work inside that loop is charged to the fleet, not just to the clock.
Three costs came out of it.

_Date: 2026-08-03; issue #330_

## The documented exact pin advances to v2.2.0, the release that carries the eligibility fix

`v2.1.0` predates the `ci-eligibility` inlining, so every consumer following the
documented exact-pin posture was on the pre-fix behaviour. #287 corrected the
examples *downward* — from a `v2.1.1` that was never cut to the `v2.1.0` that
existed — which made the pins resolve without making them current.

_Date: 2026-08-03; issue #300_

## Sync active issues after the backlog audit

The repository working notes now remove closed issues #281, #303, and #317 and
record the still-open secret-boundary and AI-review context defects in #350 and
#377. This keeps the session-loaded triage list aligned with the issue tracker.

_Date: 2026-08-03; id:20260803T222839Z_

## The fleet watchdog gets the decision record its class of change requires

The watchdog changes runner topology and can cancel merge-gate runs. Both are
sensitive classes that must leave an ADR, and #336 shipped without one — ADRs
0047 and 0048 covered the two routing changes either side of it, and the
numbering stopped at 0048.

_Date: 2026-08-03; id:20260803T140000Z; refs #336_

## Active Issues sync after the merge-gate deadlock audit

`CLAUDE.md`'s Active Issues list loads into every session, so a stale entry
costs context in each one and misreports the state of the work.

_Date: 2026-08-03; id:20260803T134500Z; refs #300, #340, #341, #342, #343_

## Close the migration-checklist gaps that let adopters keep a second publisher

`docs/changelog/migration.md` now says to delete `.releaserc.json` when adopting
the reusable release workflow, and to generate all three consumer files rather
than hand-writing them. Both omissions produced real defects: two repositories
finished migration with a semantic-release config still tracked, and six
hand-wrote a contract test that asserted a pre-release tree.

_Date: 2026-08-02; issue #318_

## Let a fragment link an issue it does not own, and define snapshot repair

Fragments may now carry an optional `refs:` list of issue numbers they link but
do not own. Identity stays unique — that is what makes fragments conflict-free —
so only one entry per issue carries `issue:`; before this, every other entry for
that issue silently lost its `#n` release back-link, with no validation error.
`refs` is additive: a fragment without it renders byte-identically to before, so
no consumer is forced to re-pin.

_Date: 2026-08-02; issue #316; refs #317_

## Generate the changelog contract test so adopters survive their first release

`scripts/gen-changelog-caller.sh` gained a third mode, `contract-test`, so the
adopter suite is generated alongside the workflow and the renderer instead of
being hand-copied.

_Date: 2026-08-02; issue #309_

## Recommend a contract pin that actually contains the generator

Consumers were told to generate their changelog callers against `1486d44…`, a commit that
predates `scripts/gen-changelog-caller.sh`. Fetching the generator at `contract_ref`
therefore 404s, and `verjson-authn` had to carry a second `generator_ref` pointing
somewhere else to keep its contract test hermetic — two pins for one contract, which is the
drift #304 reported wearing a different hat.

_Date: 2026-08-02; issue #308_

## Document the filename form for issue-less changelog fragments

The changelog contract showed one fragment filename and described the issue-less
case in prose only, so readers inferred that the `-issue-` segment tracks the
metadata key and wrote `-id-` names the engine rejects. The contract, the
migration guide, and `NEXT/README.md` now show both filenames side by side and
state that `-issue-` is literal. `scripts/doc-fragment-names.sh` keeps them
honest: it validates every fragment filename example in tracked Markdown against
`CANONICAL_NAME` in `scripts/changelog.py` itself, and fails closed when the
engine cannot be consulted or fails its probes. See #305.

_Date: 2026-08-02; issue #305_

## Pin the engine digest, verify every path to it, and restore the contract override

The generated changelog scripts resolved the engine by path and executed whatever was
there. The cache path is keyed by a commit SHA, which reads as content-addressed but is
not: anything able to write it — another tool, a restored CI cache, an interrupted
download — became the contract for every later run. Demonstrated against the generator as
it stood, with a poisoned cache entry and no network: the renderer executed it and exited
0. With the digest pinned it exits 1 and names the pin it failed against.

_Date: 2026-08-02; issue #304_

## Snapshot each review pass's transcript so a first-pass budget failure is still reported

The merge gate's budget probe looped over `EXEC_FILE_1/2/3`, but all three resolved to the
one fixed path `claude-code-action` writes under `$RUNNER_TEMP`. Each pass overwrote the
last, so the loop read the final pass three times and an earlier `error_max_budget_usd` was
gone before it was read. On PR #288 (run 30724025229) pass 1 died at the $0.50 cap and the
step still logged `budget_exhausted=false`, so the maintainer got a generic "review could
not complete" instead of the budget-exceeded message that names the cap, the diff size and
the advice to split the PR. Since the first pass carries the smallest cap, that ordering is
the common one, which left the recovered/blocked branch effectively dormant.

_Date: 2026-08-02; issue #293_

## Track the two merge-gate findings from the PR 288 re-review post-mortem

`CLAUDE.md`'s Active Issues list loads into every session, so a finding missing from it is
one the next session starts blind to. Two findings from dissecting the failed re-review on
#288 are now listed:

_Date: 2026-08-02; issue #292_

## Documented tag pins are corrected to v2.1.0 and checked against real tags

Every reusable-workflow example advertised `@v2.1.1`, a tag this repository never
published — releases stop at `v2.1.0` — so a consumer copying the documented exact
pin got an unresolved workflow reference. Six examples across `README.md`,
`docs/reusable-workflow-versioning.md`, `docs/node-workflows.md` and the
`node-ci.yml` caller header now name `v2.1.0`, the latest existing immutable
release. This supersedes the "exact consumer examples advance to the planned
`v2.1.1` patch release" note in the 2026-07-28 `node-ci` fragment: that release was
never cut, and cutting one is a separate, human-triggered decision.

_Date: 2026-08-02; issue #287_

## Generate the consumer changelog caller instead of hand-writing it

Consumers need two files that must pin the same contract commit — a
`changelog-validate.yml` caller and a `render-next.sh` that fetches
`changelog.py` at that commit — and the contract described neither. The first
three adopters each hand-wrote them and each produced a slightly different
shape, which is the drift the contract exists to remove, reappearing in the act
of adopting it.

_Date: 2026-08-02; issue #286_

## Bind privileged-merge gate provenance to the run's entry workflow

`ai-privileged-merge.yml` trusted a gate run whose `referenced_workflows[]` named
`ai-review-merge.yml` at `main`'s SHA. That array describes the workflow **file**,
not what executed — a job under `if: false` still lists its callee — so one
crafted workflow in a consumer repository could mint every signal the merge asks
for (reference the gate it never runs, publish a job literally named `gate` that
succeeds, upload a hand-written `merge-attestation-<run_id>` artifact) and obtain
an `--admin` merge. Pre-existing; found by the adversarial review in #277.

_Date: 2026-08-02; issue #279_

## The merge gate derives its own job names at runtime instead of waiting on itself

A reusable `workflow_call` publishes the callee's checks as `<caller job> /
<callee job>`, so a consumer installing the gate with `uses:
Verjson/.github/.github/workflows/ai-review-merge.yml` — a shape ADR 0039
supports — saw the gate's own jobs as `ci / preflight`, `ci / gate`,
`ci / dispatch-merge`. Both CI snapshots (`ci_wait` and the authoritative merge
recheck) filtered required checks by exact equality against a static list of
*bare* names, so the gate enumerated its own jobs as required checks and waited
for itself until the poll window expired, reporting `trusted gate/checks did not
become green` — an error pointing nowhere near the cause.

_Date: 2026-08-02; issue #276_

## Bound the actions-ci checkout to depth 1 and fetch pinned commits on demand

`actions-ci` checked out full history (`fetch-depth: 0`) because
`node-workflow-pins.test.sh` resolves co-located action dependencies at their
pinned commits. Full history was only ever a proxy for "the pinned objects are
present", and #287 made the proxy more expensive by widening the workflow's
`paths:` triggers to `.github/workflows/**`, `docs/**` and `README.md` — so
docs-only PRs paid for the whole graph too.

_Date: 2026-08-02; issue #234_

## Baseline repository-hygiene check for a root README, audit-only by default

Added one central repository-hygiene check instead of a job copied into every
repository: `scripts/repo-hygiene.sh` plus the reusable workflow
`.github/workflows/repo-hygiene.yml`. It requires the repository's root
`README.md` to answer purpose, ownership/contact, and local validation, with the
exact rule and its known limits recorded in
[ADR 0046](docs/decisions/0046-baseline-repository-hygiene/README.md).

_Date: 2026-08-02; issue #232_

## Sync Active Issues after the changelog contract batch

`#316` closed with the `refs` metadata key, so its pointer is pruned. `#317` is
rewritten to reflect what actually remains: the repair path and the acceptance
decision both shipped, and the only outstanding work is the `verjson-agents`
snapshot, tracked in that repository.

_Date: 2026-08-02; id:20260802T190000Z; refs #316, #317_

## Sync the Active Issues list after the changelog-contract audit

The Active Issues list loads into every session, so a stale entry costs context in each one
and misreports the state of the work. #251, #276 and #293 closed earlier in the batch, and
#304, #305 and #308 closed in this audit — six entries describing finished work.

_Date: 2026-08-02; id:20260802T161500Z_

## Sync the Active Issues pointer list with what the 2026-08-02 batch left open

The Active Issues list loads into every session, so a stale entry costs context in each one
and misreports the state of the work. After #251, #276 and #293 closed in this batch, and
#289 earlier, four entries described work that is done.

_Date: 2026-08-02; id:20260802T031500Z_

## Migrate the pre-contract fragments and drop the legacy switch

All 88 pre-#249 fragments now carry canonical identities and metadata, so
`validate` and `render-next.sh` run strict — `--allow-legacy-next` is gone from
both call sites and this repository finally meets the contract it defines (#289).

_Date: 2026-08-02; id:20260802T001745Z_

## changelog-release grants its pushing job write authority

`changelog-release.yml` declared a workflow-level `contents: read` and no job override,
so the token reaching its final atomic push was read-only. A called workflow's
`permissions` block is the cap rather than the caller's, so a consumer granting
`contents: write` and passing `push_token: ${{ secrets.GITHUB_TOKEN }}` still got a
read-only token: every release generated its snapshot, verified its exact tag, then died
on the push with `remote: Write access to repository not granted` / HTTP 403, leaving no
tag, release or package. Evidence: verjson-identity-contracts run 30724326491, tracked
downstream as Verjson/verjson-identity-contracts#24.

_Date: 2026-08-01; issue #295_

## Track the unenforced changelog contract in the Active Issues pointer list

`CLAUDE.md`'s Active Issues list is the triage pointer loaded into every session, so a
finding that is missing from it is a finding the next session will not see. #289 — this
repository owns the canonical changelog contract but never runs
`python3 scripts/changelog.py validate` over its own `NEXT/`, and one fragment from
e18650b already fails it — was filed while adding the #244 fragment and is now listed.

_Date: 2026-08-01; issue #289_

## Sync the Active Issues pointer list with what the 2026-08-01 batch left open

`CLAUDE.md`'s Active Issues list loads into every session, so a stale entry costs context in
each one and misreports the state of the work. After #270, #271, #273, #275 and #278 closed,
three open findings from the same batch were missing from it and one entry understated its
defect:

_Date: 2026-08-01; issue #281_

## Privileged merge verifies which revision of itself is executing

ADR 0042 required consumers to pin the privileged merge at `@main` and recorded the
SHA-pinning exposure as closed. Nothing enforced it. The generator and the pin test both
run in `Verjson/.github`; the caller file lives in the **consumer's** repository, so
neither binds anything downstream, and the runtime anchor pins the *gate*'s revision rather
than this workflow's own. `ai-privileged-merge.yml` could have been executing from a fork
with every other guard passing.

_Date: 2026-08-01; issue #278_

## Detect runners registered into the default runner group

The admission reconciler asked two questions — are repositories admitted to the lane their
visibility selects, and does each lane resolve to at least one online runner. A runner
registered **without** `--runnergroup` answers neither. It lands in GitHub's default group,
which is `visibility: all` with `allows_public_repositories: true` and has no label
discipline, so it is capacity that no lane selects and no policy governs.
[ADR 0003](../docs/decisions/0003-runner-groups-gcp-github-manish/README.md) already records
this as a hand-verified operational caveat — a custom group cannot be made default, so a new
runner auto-lands in the public-accessible one and must be moved afterwards. Nothing checked
that it had been.

_Date: 2026-08-01; issue #275_

## Create the VERJSON_LANE_* organization variables

Leg 1 of the lane rollout. The four lane variables defined by
[ADR 0040](../docs/decisions/0040-runner-lanes-and-admission-axes/README.md) now exist at
organization scope, all pointing at the current pool:

_Date: 2026-08-01; issue #273_

## Correct runner guidance that pointed at lanes and runners which no longer exist

`helm-ci.yml` told callers to pin their kind smoke tests to a `docker` label, described
as "the only runner with a Docker socket". No runner in the fleet carries that label — the
Docker lane was retired (ADR 0040) — so a caller who followed the advice got a job that
**queued forever with no check run**, #182's silent-failure mode. The guidance now routes
that job through the org variables like every other job, and says plainly that the
"every runner has Docker Compose" claim is an owner assertion rather than an API-visible
fact, because runner-local software cannot be verified through the API.

_Date: 2026-08-01; issue #271_

## Accept shared runner admission by decision, and narrow the public repository surface

## The admission discrepancy is resolved, in favour of the live state

_Date: 2026-08-01; issue #270_

## Resolve reconciler runner groups by name, and only when a lane selects them

The daily runner-admission reconciler pinned `UNTRUSTED_GROUP_ID=6`. Group 6
(`isolated`) and group 7 (`docker-builders`) were deleted on 2026-07-31, leaving the
organization with groups 1 (`GitHub`), 3 (`manish`), and 4 (`GCP`), so the fetch 404ed
and the job exited 2 on every run. It stayed fail-closed and never reported a dirty
organization as clean, but it reported nothing at all — the monitor went dark at the
moment its subject changed.

_Date: 2026-08-01; issue #266_

## Define the lane model and rewrite the runner-routing guide against measured state

`docs/runner-routing.md` described a topology that no longer exists — `gha-docker-1` and a
required `[self-hosted, docker]` lane, `gha-gate-*`/`gha-meta-*` runners, an `isolated`
group, and eight GCE VMs — while ADR 0033 asserted a funding premise that is measurably
false. Four routing defects (#175, #182, #192, `verjson-infra` PR #77) share one root
cause, and the documents as written would have produced a fifth.

_Date: 2026-08-01; issue #256_

## node-release reports whether semantic-release actually published

`node-release.yml` exposed no signal for "did this run publish a version", so a consumer
could only gate follow-up work on job success — which is green on every push that
semantic-release decides needs no release. Verjson/verjson-browser-agent#17 needs to run a
one-time package deprecation exactly once, after a real publication.

_Date: 2026-08-01; issue #244_

## Validate this repository's own changelog fragments in CI

This repository defines the canonical changelog contract but never ran the
validator over its own `NEXT/` (#289). `actions-ci.yml` exercised the tooling —
the renderer, its edge cases, and `changelog.test.py` — while the fragments those
tools exist to govern went unchecked, so the contract's own repository was the one
place it was not enforced.

_Date: 2026-08-01; id:20260801T235744Z_

## Split privileged merge into a reusable workflow with a tested two-sided name contract

`ai-privileged-merge.yml` existed only as an event-triggered workflow that consumers
copied. The one fat copy in the organization, `verjson-cloud-storage`, never received the
ADR 0039 provenance fix precisely because it was a copy.

_Date: 2026-08-01; id:20260801T120000Z_

## Trust organization required-workflow runs as gate provenance

The privileged merge only recognised the gate when a repository ran
`ai-review-merge.yml` itself or called it as a reusable workflow. Verjson consumers
receive it through the organization ruleset `main-protection`, whose runs carry a
repository-scoped `workflow_id` and no `referenced_workflows`, so provenance never
became trusted and every privileged run exhausted its bounded wait with
`trusted gate/checks did not become green`. The dispatched continuation additionally
required its own `source_run_id` to be a `workflow_dispatch` run, which the
`pull_request`-triggered gate never is. Together these left consumer pull requests
OPEN/BLOCKED with a cancelled `privileged_merge` check and nothing running.

_Date: 2026-07-31; issue #259_

## Ignore PM orchestrator run state and track the draft-PR gate defect

The PM orchestrator writes a `.pm-execution-plan-*.md` and `.pm-run-brief-*.json` per
session and is meant to delete both at terminal completion. When a session ends without
that step the files linger as untracked residue, so `.gitignore` now covers `.pm-*`
alongside the existing Claude Code tooling entries.

_Date: 2026-07-31; id:20260731T155511Z_

## Standardize changelog fragments and immutable release snapshots

Added the organization contract, schema, reusable validation and release
workflows, migration guide, and reference tooling for collision-resistant
unreleased fragments and immutable released snapshots
([#249](https://github.com/Verjson/.github/issues/249), ADR 0038).
Reusable validation and release jobs follow the organization-aware runner
routing contract, with caller overrides and an external hosted fallback.
Temporary in-place compatibility gives historical prose stable file identities;
only explicit metadata or configured migration directories infer issue ownership.
Workflow entrypoints consistently use the available `python3` runtime.
Release publication targets the explicit caller default branch from any checkout
state, and pull-request policy distinguishes in-place fragment renames from
actual fragment consumption.

_Date: 2026-07-30; issue #249_

## Make CI status pagination runner-compatible

# Make CI status pagination runner-compatible — 2026-07-30

_Date: 2026-07-30; issue #248_

## Read CI status through repository-scoped REST APIs

The shared merge gate now combines paginated check runs and commit statuses from
the immutable PR head instead of expanding the permission-sensitive GraphQL
`statusCheckRollup`. Green, red, pending, absent, startup-failed, and unreadable
states retain their fail-closed handling
([#248](https://github.com/Verjson/.github/issues/248), ADR 0037).

_Date: 2026-07-30; id:20260731T004326Z_

## Keep validation green without a privileged continuation

Treat an absent repository-local privileged merge workflow as a successful
manual-merge fallback while keeping workflow-enumeration failures fail-closed.
This restores the consumer boundary recorded by ADR 0036 without expanding
`actions: write` or exposing `ORG_ADMIN_TOKEN` (#247).

_Date: 2026-07-30; id:20260731T002204Z_

## Allow read-only private check rollups

Grant the unprivileged, `ORG_ADMIN_TOKEN`-free merge gate `checks: read` so GitHub permits `statusCheckRollup` queries in private consumer repositories. Write access remains absent, `actions: write` stays isolated in the metadata-only dispatcher, and `ORG_ADMIN_TOKEN` never enters PR-controlled execution. See #240 and ADR 0037.

_Date: 2026-07-30; id:20260730T235355Z_

## Recognize the complete approval policy denial

Accept the complete, observed `gh` CLI error for an Actions-approval-disabled repository and preserve the approved verdict as an audit comment. Exact whole-error matching keeps mixed or unknown publication failures terminal. See #242 and ADR 0036.

_Date: 2026-07-30; id:20260730T235354Z_

## Attribute red checks and preserve approval audit

Emit a compact, safely encoded snapshot of checks the merge gate classifies as failed so the payments reproduction can be diagnosed without guessing. When repository policy disables Actions-authored approvals, retain a non-blocking verdict as a head- and patch-id-bound audit comment while unexpected publication errors remain fail-closed. See #240, #242, ADR 0024, and ADR 0036.

_Date: 2026-07-30; id:20260730T233002Z_

## Wait for completed check conclusions

Keep the merge gate fail-closed without rejecting healthy CI when GitHub briefly reports a completed CheckRun before populating its conclusion. The polling gate waits for the conclusion, while the immutable merge recheck refuses the inconclusive snapshot. See #240 and ADR 0024.

_Date: 2026-07-30; id:20260730T230255Z_

## Report workflow-change auto-merge holds as successful

Workflow-file pull requests now leave the privileged merge path successfully with a
clear human-review notice instead of reporting the intentional no-merge policy as a
failed CI check. The review gate excludes its trusted dispatch and privileged merge
continuations from repository CI evaluation, preventing circular authorization while
ordinary repository check failures remain fail-closed.

_Date: 2026-07-30; id:20260730T215122Z_

## Isolate Actions write permission in the trusted dispatcher

The PR checkout/review gate is back to `actions: read`. A minimal metadata-only job now
holds `actions: write`, validates repository/PR/head/run shapes plus the fixed trusted
workflow identity, and dispatches the existing exact-run privileged continuation
([#235](https://github.com/Verjson/.github/issues/235), ADR 0037).

_Date: 2026-07-30; id:20260730T215121Z_

## Separate PR review from privileged merge authority

The organization gate now keeps `ORG_ADMIN_TOKEN` exclusively in a trusted
base-branch execution path that never consumes PR code, artifacts, caches, environment
files, or outputs. Dependabot, Renovate, forks, and ordinary PRs share the credential-free
validation path; the trusted merger revalidates repository identity, immutable head SHA,
required checks, draft/hold state, and current PR state before a matched-head merge
([#230](https://github.com/Verjson/.github/issues/230), ADR 0036).

_Date: 2026-07-30; id:20260730T213214Z_

## Align ADR 0035's merge-gate routing description

The primary decision text now matches its amendment: preflight stays untrusted until
the target visibility lookup selects the downstream gate lane.

_Date: 2026-07-30; id:20260730T180014Z_

## Harden runner-lane migration seams

Merge-gate preflight now stays on the untrusted lane until target visibility is
resolved, and admission reconciliation accepts both compatibility and namespaced lane
labels.

_Date: 2026-07-30; id:20260730T175623Z_

## Make runner lanes variable-driven for new repositories

Reusable workflows now select independent organization variables for private/default
and public-or-unresolved workloads. Both variables intentionally target the shared
`general` fleet during the temporary permissive exception, while runner admission and
capacity reconciliation validates the live organization policy.

_Date: 2026-07-30; id:20260730T174545Z_

## Correct the runner-routing test label

The Actions CI step name now describes the general-lane policy it tests.

_Date: 2026-07-30; id:20260730T171213Z_

## Skip the central actionlint checkout for caller overrides

Reusable callers that provide `config-file` no longer pay for a redundant checkout of
the default central policy.

_Date: 2026-07-30; id:20260730T170748Z_

## Correct stale runner and regression-test descriptions

Reusable workflow documentation now reflects ADR 0034's general-runner default,
and regression fixtures accurately describe the code paths they exercise.

_Date: 2026-07-30; id:20260730T170701Z_

## Stabilize Pulumi workflow assertions

Make the Pulumi credential-boundary test search extracted jobs without a
`pipefail`-sensitive producer pipeline, and cover large job blocks that
previously turned successful early matches into intermittent failures (#199).

_Date: 2026-07-30; id:20260730T164608Z_

## Enforce declared Node typechecks in reusable CI

The reusable Node CI workflow now runs a caller's `typecheck` script when
present, closing the org-wide type-test gap without breaking repositories that
do not define the script. Fixes #190.

_Date: 2026-07-30; id:20260730T164440Z_

## Route Verjson workflows through online general runners

All Verjson-owned workflow defaults and repository-local jobs now target the
provider-neutral `general` lane so required checks do not queue behind offline
provider or isolation labels. External reusable callers keep their hosted
default, and #204 tracks restoration of the paused isolation hardening. Fixes
#212.

_Date: 2026-07-30; id:20260730T163539Z_

## Apply the central actionlint runner policy to reusable callers

The reusable actionlint workflow now loads its config from the immutable
workflow revision by default, while allowing callers to select their own config
path when necessary. Its behavioral contract proves that an undeclared custom
runner label fails linting while the governed `GCP` label passes. Closes #208.

_Date: 2026-07-30; id:20260730T162531Z_

## Temporarily route Verjson merge gates through general runners

The AI merge gate and this repository’s shell validation now use the Verjson
organization’s provider-neutral `general` DigitalOcean pool. This speed-first
exception keeps DigitalOcean contexts and GitHub runner registrations
organization-isolated, but temporarily removes disposable-host isolation for
public PR processing. Public Verjson repositories are temporarily admitted to
the general runner group, and the public-visible `VERJSON_RUNNER_ISOLATED`
variable points to `general`.

_Date: 2026-07-29; id:20260730T040312Z_

## Runner admission is reconciled against routing daily

ADR 0033 routes by repository visibility, but only org settings decide whether a
repository can actually be *assigned* the lane routing picks. Nothing kept the
two honest. That is how #182 and #192 happened, and it still bit the one case
routing cannot fix: a newly created repository is in neither runner group, so
every job it emits queues with **no check run at all**.

_Date: 2026-07-29; id:20260729T204105Z_

## runs-on expression evaluator drops ambient Node authority

`runner-routing-policy.test.sh` evaluated the awk-extracted `runs-on` expression
through `new Function`, which runs with the full Node global scope. The input is
workflow text from this repository, so this was never a live privilege gain —
anyone able to edit those files can already edit the `.test.sh` that bash
executes outright. The concern is the pattern: evaluating extracted expressions
is a useful trick, and the next place it gets pointed may be a workflow from a
fork or another org.

_Date: 2026-07-29; id:20260729T185430Z_

## Reusable workflows route by visibility onto configurable self-hosted pools

`ubuntu-24.04` was never a fallback. GitHub-hosted minutes are unfunded for this
org (#189), so every route that "degraded" to hosted was a guaranteed failure —
including the one added hours earlier by #192 to stop releases queueing. The
queue stopped; the release still could not run.

_Date: 2026-07-29; id:20260729T172556Z_

## node-release routes on isolated-pool admission, not owner

`node-release.yml` kept the owner-wide `github.repository_owner == 'Verjson'`
isolated route that #184 removed from `node-ci.yml`, so every Verjson repo
outside runner group 6's four-repo allowlist emitted a release job GitHub can
never assign. Verjson/verjson-authn run 30450460243 sat `queued` from 12:10:01Z
with `runner: ""` — the release for merged PR #86, whose OTP code-store fix
therefore sat on `main` while the published version stayed at 0.21.0.

_Date: 2026-07-29; id:20260729T161620Z_

## Ignore Claude Code tooling residue

`.gitignore` carried only `.tokensave/`, so every agent scratch worktree under
`.claude/worktrees/`, plus `.claude/settings.local.json` and
`.claude/.headroom_wrap_marker.json`, showed up as untracked noise in `git status`
and could be swept into a `git add -A` by accident.

_Date: 2026-07-29; id:20260729T143257Z_

## Size the gate review budget to the diff, and close the empty-verdict fail-open

The merge gate gave every PR the same first-pass review budget ($0.15, or $0.50
for sensitive paths) regardless of diff size, so a large PR exhausted the cheap
tier deterministically. `classify` now emits `changed_lines` and raises the
first-pass cap to $0.60 / $0.90 for diffs of 800+ changed lines — still strictly
under the $1.00 escalation cap, so escalation remains a step up.

_Date: 2026-07-29; id:20260729T135618Z_

## node-ci routes on isolated-pool admission, not owner

`node-ci.yml` targeted `[self-hosted, isolated, linux, x64]` for every
`github.repository_owner == 'Verjson'` caller after #175, but org runner group
`isolated` (id 6) is `visibility: selected` and admits only `.github`,
`verjson-cli`, `verjson-cli-cloud`, and `verjson-cli-project-init`. Every other
consumer's `ci / eligibility` job queued forever — no check run ever reported, so
the merge gate waited out its timeout and failed (live on Verjson/verjson-authn#87
and #88). Both `runs-on` expressions now test `github.repository` against the
isolated-pool allowlist and fall back to `ubuntu-24.04` for anyone else; the
`runner` input remains the per-caller override. The org runner-group allowlist is
unchanged — pool admission stays the org admin's call (#182, ADR 0031).

_Date: 2026-07-29; id:20260729T134417Z_

## Document how to provision REWORK_RECONCILE_TOKEN

The weekly rework reconciler has been reporting seven of its eight configured
repos as degraded (`commit data unavailable`) because the Actions secret
`REWORK_RECONCILE_TOKEN` is unset and the job falls back to this repo's
`GITHUB_TOKEN`. The workflow wiring was already correct
(`.github/workflows/rework-reconcile.yml:40`); what was missing was a runbook, so
the org admin had to re-derive the token's scope from the script every time.

_Date: 2026-07-29; id:20260729T133923Z_

## Target visibility stub gh coverage

- `scripts/ci-gate/target-visibility.test.sh` (new): extracts the
  `target_visibility` step's `run:` block from `ai-review-merge.yml` and drives
  it with a stub `gh` — a public target publishes `target_private=false`, a
  private one `true`, and a failed lookup exits 0 while publishing an EMPTY
  value plus a `::warning::`, so the gate job stays on the self-hosted `gate`
  pool. Also pins the consuming contract: `runs-on` routes the isolated pool
  only on the exact string `'false'`, so an unresolved visibility can never fail
  open. Wired into `actions-ci.yml`. (#170)
- `scripts/ci-gate/dispatch-target-guard.test.sh`: cover the tightened
  `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$` charset — legal dot/dash/underscore repo
  names still pass, while backticks, whitespace and newlines in an otherwise
  same-owner target now fail closed, closing the annotation-injection vector the
  previous `^[^/]+/[^/]+$` shape accepted. (#119, #176)

_Date: 2026-07-29; id:20260729T044424Z_

## Gate lane unzip shim for the isolated pool

The first live AI-gate run on the #173 isolated pool fail-closed with no
verdict: `claude-code-action`'s Bun install needs `unzip`, absent from runner
image `base-d1c4376`, so all three model invocations exited 127 (#179, run
30420746289). `ai-review-merge.yml` now installs `unzip` before the model
steps inside the ephemeral child container — a labelled temporary shim until
Verjson/verjson-github-runner#72 ships an image that carries it and the pool
template rolls forward.

_Date: 2026-07-29; id:20260729T042528Z_

## Permanent isolated runner pool live

The standing `isolated` pool required by #173 (and assumed by the ADR 0030
routing merged in #175) is deployed: org runner group `isolated` (id 6,
`visibility: selected`, public repos allowed) granted to `.github`,
`verjson-cli`, `verjson-cli-cloud`, and `verjson-cli-project-init`; Spot MIG
`isolated` (us-east4, 3 × n1-standard-1, template `isolated-tmpl-cd0b6286`)
in `verjson-ci-502400` via released `@verjson/cli-cloud` 0.25.1
(`--runner-mode isolated-pr`), image pinned to the canary-proven digest
`ghcr.io/verjson/gha-runner@sha256:d1ff3e93d7cd694229842fb958c61d789dde34b0b34cc6baf058bdbff89fa125`
(`base-d1c4376`), not the pre-contract `b2cb8c3e` digest
(Verjson/verjson-cli-cloud#161). Lane SA `runner-isolated@…` holds
`secretAccessor` on `gha-runner-reg-key` only. NAT adoption still needs the
local matcher shim for Verjson/verjson-cli-cloud#157. Live admission receipts
confirm `metadata_denied=true socket_absent=true shared_writes=false` and
per-job child teardown; deployment evidence on #173.

_Date: 2026-07-29; id:20260729T040805Z_

## Gate routes on target visibility

- `ai-review-merge.yml`: the gate job now routes on the resolved
  `TARGET_REPO`'s visibility (preflight output `target_private`), not the
  dispatching repo's — an operator re-gating a private repo from public
  `Verjson/.github` no longer lands the review on the public/isolated path;
  unresolved visibility fails closed to the self-hosted `gate` pool. The
  org-aware non-Verjson → `ubuntu-24.04` branch from #175/ADR 0030 is
  preserved, and both `runs-on` comments drop the retired ADR-0016
  meta-lane self-gate description. (#168, #170)

_Date: 2026-07-28; id:20260729T044423Z_

## Composite cache guard warning

- `setup-verjson-node` README: documented that the composite has no
  `cache-max-mb` end-of-job cache-size guard (unlike `node-ci.yml` /
  `node-release.yml`) — bespoke callers enabling `cache: 'true'` own their
  own guard. Warning added directly in the usage block. (#169)

_Date: 2026-07-28; id:20260729T040917Z_

## Repurpose retired meta runners as private gate capacity

ADR 0029 reuses `gha-meta-1` and `gha-meta-2` for the private merge-gate lane
after ADR 0028 moved public `.github` execution to hosted capacity. Adding the
`gate` label raises private review capacity from four to six without changing
machines, selected/private-only GCP group access, workflow permissions, or
public routing (#171; observed queue: `Verjson/toquorum#283`).

_Date: 2026-07-28; id:20260728T163411Z_

## node-ci eliminates its ci-eligibility self-pin

`node-ci` now inlines the co-located `ci-eligibility` action's shell block,
eliminating a manually-maintained remote self-pin from its transitive dependency
graph. The extraction suite requires exact parity between the reusable workflow
and composite-action copies and exercises defer, clean, fail-open, and dispatch
behavior. This avoids the self-update loop that Renovate maintenance would
create while preserving the `statuses: read` boundary and stability-days
semantics. Exact consumer examples advance to the planned `v2.1.1` patch release,
which is published only after this change merges. Fixes #164; see ADRs 0014 and
0023.

_Date: 2026-07-28; id:20260728T160903Z_

## Bound Node caches and codify CI security tiers

Reusable Node CI/release/setup now default Actions npm caching off on persistent
self-hosted runners. Opt-in caches are job-scoped and reusable workflows clear
them above a 1024 MB default upload bound. ADR 0028 separates isolated, trusted,
and fast CI routes, moves public `.github` and public-target gate execution to
fixed hosted capacity, and supersedes GCP's former all-repository/public access
posture (#166; consumer evidence: `Verjson/toquorum` run `30363686973`).

_Date: 2026-07-28; id:20260728T151509Z_

## Make node-ci dependencies transitively immutable

Pin the co-located CI-eligibility action to the reviewed commit that introduced
it, remove its mutable-ref Renovate exception, and make policy tests walk the
live dependency graph. Document `v2.1.0` as the exact repo-wide SemVer release
for shared actions and reusable workflows, with `v2` retained as the explicitly
moving major option. See #162 and ADRs 0014/0023.

_Date: 2026-07-28; id:20260728T124526Z_

## Close Pulumi validation runner and admission leaks

Pinned credential-free validation to the GitHub-hosted `ubuntu-24.04` image and
reduced preview admission inputs to boolean secret-presence flags. Callers can
no longer route validation commands onto a runner with ambient credentials, and
the admission job never receives raw cloud secret values (#151; ADR 0027).

_Date: 2026-07-28; id:20260728T021149Z_

## Pin the removed statuses-permission claim in CI

Strengthened the reusable Node permission-contract test to reject the exact
former ADR wording that incorrectly described an absent caller grant as
fail-open, while positively requiring the workflow-startup boundary in both
`node-ci.yml` and ADR 0023 (#148).

_Date: 2026-07-28; id:20260728T013809Z_

## Mark node-ci's statuses permission as startup-required

Correct `node-ci.yml` and ADR 0023 to say that callers must grant the reusable
eligibility job's explicit `statuses: read` request. Omitting it fails the called
workflow at startup before the runtime fail-open logic can execute. Extend the
eligibility regression test to keep that caller contract accurate. Closes #148.

_Date: 2026-07-28; id:20260728T013808Z_

## Disable setup-node automatic cache discovery

Disabled setup-node's package-manager auto-cache path in the reusable Node
workflows and composite action so caching is controlled only by the explicit
cache input and matching lockfile contract (#152).

_Date: 2026-07-28; id:20260728T012114Z_

## Separate Pulumi validation from trusted live-preview credentials

`pulumi-ci.yml` now runs caller-supplied install and validation commands in a
GitHub-hosted `contents: read` job with no package, Git, cloud, OIDC, or
repository-write credentials. A separate live-preview job receives its required
permissions and secrets only after successful validation and explicit
fail-closed admission for pushes or same-repository PRs; fork PRs, unknown
events, and missing cloud credentials stay on validation only. Both checkouts
disable credential persistence and all third-party actions are digest-pinned
(Verjson/.github#151; ADR 0027).

_Date: 2026-07-27; id:20260728T021148Z_

## Keep public Node installs on npmjs

Reusable Node CI and release workflows now leave `actions/setup-node`'s
`registry-url` empty when callers set `scope: ''`, preventing public-only
installs from being redirected to GitHub Packages. The default `@verjson`
private-registry authentication and explicit npm cache controls remain intact
(#155).

_Date: 2026-07-27; id:20260728T013734Z_

## Pin Node cache workflow dependencies

Pinned `actions/checkout` in the cache-restore probe and `actions/setup-node` in
the setup composite to their audited full SHAs, with regression and Renovate
coverage for every Node workflow/setup surface (#152).

_Date: 2026-07-27; id:20260728T012113Z_

## Pin actions-ci checkout

Pinned `actions/checkout` in `actions-ci` to the audited full SHA and extended
the Node surface regression/Renovate coverage to keep that persistent-runner
dependency immutable (#152).

_Date: 2026-07-27; id:20260728T012112Z_

## Cache and bound reusable Node workflows

Added configurable 30-minute job bounds and lockfile-keyed npm download caching
to `node-ci`, `node-release`, and `setup-verjson-node`, with a cold-cache restore
probe and caller concurrency guidance. Private registry auth, schema/DB setup,
eligibility checks, and locked release tooling remain regression-covered
(#152).

_Date: 2026-07-27; id:20260728T012111Z_

## Expose actionlint as a governed reusable workflow

Add `workflow_call` to the existing actionlint workflow while preserving its
local triggers, GCP default, pinned actionlint version, and checksum verification.
Consumers can choose only the governed GCP or `ubuntu-24.04` route, pin the call
by full commit SHA, and retain ownership of path filters and required-check
policy. Full-SHA nested Actions plus structural and real invalid-workflow tests
close #153; runner and required-check topology is recorded in ADR 0026.

_Date: 2026-07-27; id:20260728T005444Z_

## Harden reusable actionlint after independent review

Inline actionlint's invalid-workflow fixtures so a reusable call never executes a
caller-controlled test script on the default self-hosted runner. Hosted calls now
require ShellCheck explicitly, with four existing intentional SC2016 literals
documented by line-scoped suppressions. The structural suite extracts and
mutation-tests the inline block and pins the hosted ShellCheck requirement.

_Date: 2026-07-27; id:20260728T005443Z_

## Release-tooling audit gains a dated, fail-closed advisory allowlist

Unwedges org-wide CI (#146). A newly-published advisory —
[GHSA-mh99-v99m-4gvg](https://github.com/advisories/GHSA-mh99-v99m-4gvg),
`brace-expansion <=5.0.7`, high — turned every PR in the repo red on
`actions-ci`'s `npm audit --audit-level=high` step, with no code change: the
vulnerable copy is **bundled** inside npm's own CLI
(`semantic-release` → `@semantic-release/npm` → `npm`), so `npm audit fix`,
a `semantic-release` bump (already latest) and `overrides` all fail to move it,
and npm has shipped no release carrying the fixed `brace-expansion@5.0.8`.

_Date: 2026-07-25; issue #146_

## Merge gate treats absent CI checks as not-green

Closes the second, deeper defect in #143: the gate concluded "CI green" whenever
nothing in the filtered `statusCheckRollup` was pending — which is also true when
the rollup is **empty**. A workflow that dies with `startup_failure` produces no
check run at all, so a PR that was never built, tested or linted looked identical
to a fully green one and was auto-merged.

_Date: 2026-07-25; issue #143_

## node-ci's optional DB service is safe to run twice on one host

`node-ci.yml`'s default-off Postgres service bound a fixed host port `5432` and
named its container from `run_id`/`run_attempt` alone, so the second DB-backed job
to land on a self-hosted host could not start: the port was taken and the name was
already in use (#116, an AI-review follow-up from #113). The container now
publishes on an OS-assigned loopback port (`-p 127.0.0.1::5432`) and is named
`ci-postgres-<run_id>-<run_attempt>-<hash of job + runner identity>`. The name is
only for humans reading `docker ps`: the start step publishes the **container ID**
`docker run` returns and drives `docker port`/`exec`/`logs` and the `if: always()`
teardown off that, so a job can never remove a concurrent job's live container
even if two names ever did collide.

_Date: 2026-07-25; id:20260726T124511Z_

## Rework-telemetry reconciler (MVP)

Implements the MVP slice of #33 (ADR 0006 observe-and-report; ADR 0007 blast-radius
verification). A weekly, retrospective reconciler measures rework on merged PRs —
reverts, fix-follows-merge, pre-merge friction — split by `change_category` ×
`ai_authored`, and opens a **summary issue that only proposes** where human
verification should be heavier. It never touches a merge/verification gate: the
workflow's sole write is `gh issue create`, and its permissions are
`contents: read` + `issues: write` (asserted by a governance-guard test).

_Date: 2026-07-24; issue #33_

## Regression guard for the ci-eligibility @main self-pin

Closes the coverage gap flagged in #139: the #138 fix (reference the co-located
`ci-eligibility` action `@main`, exclude it from Renovate digest pinning) had no
test asserting it, so a future Renovate re-pin could silently reintroduce the #135
"action not found" break. `node-workflow-pins.test.sh` now asserts three things:
node-ci's `ci-eligibility` ref is exactly `@main`; it is NOT digest-pinned (no
40-hex ref); and `renovate.json` carries the `pinDigests:false` exclusion for
`Verjson/.github` in node-ci.yml. Test-only; no behaviour change. Refs #135, #138, #139.

_Date: 2026-07-24; id:20260724T164720Z_

## node-ci's ci-eligibility self-reference is @main, not a v1 digest

Fixes a live break from #134/#135. node-ci referenced its co-located
`ci-eligibility` action via the `v1` tag, and Renovate (`pinDigests: true` on
node-ci.yml) pinned it to `9f36163 # v1` (#135) — a commit predating the action,
so `uses: …/ci-eligibility@<that-sha>` was "action not found" on `main` for every
`@main` node-ci consumer (verjson-agents/authn/email/eslint-config + templates).
Root cause: the moving `v1` tag lags `main` until a release is manually cut, so
pinning a first-party self-reference from `v1` resolves to a pre-action commit.
Fix: reference `@main` (a branch tip always resolves to a commit with the action)
and add a `renovate.json` rule excluding this self-reference from digest pinning
(first-party, no supply-chain reason to pin). ADR 0023 updated. Refs #133, #134, #135.

_Date: 2026-07-24; id:20260724T150013Z_

## Org CI defers while renovate/stability-days is pending

New composite action `.github/actions/ci-eligibility` checks a PR head for a
pending `renovate/stability-days` status (the signal the merge gate already
`defer`s on) and outputs `should-run`. `node-ci.yml` gained an `eligibility` job
and gates `build-test` on it (`needs:` + `if: … == 'true'`), so every node-ci
consumer stops burning the CI suite on a Renovate PR that can't merge and
re-burning it on the inevitable rebase (casualty: toquorum#161 ran ~20 CI-min
while held). Fails OPEN on any uncertainty (API error / missing status → run) and
a `workflow_dispatch` always runs. Deferred jobs report `skipped` → the PR stays
BLOCKED until Renovate rebases on age-clear and `synchronize` re-fires CI for real
(self-healing, approved on #133). Pinned by `scripts/ci-gate/ci-eligibility.test.sh`,
wired into `actions-ci.yml`. ADR 0023. Hand-rolled CI adoption (toquorum,
catalog-*, viager-app) is default-pm's, tracked from #133. Refs #133, toquorum#161.

_Date: 2026-07-24; id:20260724T143028Z_

## Merge gate is now a pinnable cross-org reusable workflow

`ai-review-merge.yml` gained a `workflow_call` trigger alongside its existing
`pull_request` (org ruleset) and `workflow_dispatch` (operator) paths, so
consumers in other orgs pin `uses: Verjson/.github/.github/workflows/ai-review-merge.yml@v1`
with `secrets: inherit` instead of hand-copying and drifting (the #38 casualty).
Both jobs' `runs-on` now prefer a `runner_labels` input, falling back to the
unchanged self-gate `meta`/`gate` split (ADR 0016) on the org direct paths. The
`target_guard` (#119, ADR 0020) is unchanged and auto-bounds each consumer to its
own org via `GITHUB_REPOSITORY_OWNER` under `workflow_call`. New
`scripts/ci-gate/reusable-workflow.test.sh` pins these seams; wired into
`actions-ci.yml`. No manual prerequisite — Verjson/.github is public, so its
reusable is callable cross-org as-is (the Actions access-policy setting only
exists for internal/private repos). Refs #128, ADR 0022; follow-up migration of
Tequity's copies tracked from `tequityapp/tequity-platform#38`.

_Date: 2026-07-23; issue #128_

## Reusable merge gate fails fast on cross-org consumer misconfig

Two follow-ups from the #129 gate review. `runner_labels` is now **required**
under `workflow_call`: a consumer org has no runner for Verjson's `self-hosted,gate`
pool, so omitting it silently queued the job forever (#130) — an in-job check
can't catch that (no runner ever starts the step), so the call is now rejected
outright with a clear message. New `require_secrets` preflight step fails closed
early with an actionable message when `ORG_ADMIN_TOKEN` is empty (consumer forgot
`secrets: inherit`, #131) instead of dying later in an opaque `gh` auth error; it
never echoes the token and is a no-op on the org direct paths. Pinned by new
`scripts/ci-gate/require-secrets.test.sh` and an extended `reusable-workflow.test.sh`,
wired into `actions-ci.yml`. ADR 0022 amended. Refs #130, #131.

_Date: 2026-07-23; id:20260723T173709Z_

## ci-gate extraction tests run under `bash -eo pipefail`

The ci-gate extraction tests executed the extracted workflow block with plain
`bash` (no `-e`), so a step that aborts under GitHub's real `run:` shell
(`bash --noprofile --norc -eo pipefail`) could pass its test yet fail in
production — exactly how #124 (the org-wide merge outage) shipped. Swept the
remaining seven harnesses (dispatch-target-guard, followup-issues, freshness,
hold, node-ci-db-service, preflight-timing, review-comment) to invoke the block
under `bash -eo pipefail`, matching GitHub. All still pass — no other latent
fail-closed-abort surfaced; the class check is now in place. Ref #126, #124.

_Date: 2026-07-22; issue #126_

## Merge gate - re-review skip must fail open under `bash -e`

The re-review-skip step (ADR 0019, #120) aborted the required `gate` job on
first-review PRs, blocking all merges org-wide (#124): GitHub runs `run:` under
`bash -eo pipefail`, and the step's `set -uo pipefail` never cleared that `-e`,
so the `grep` for a (nonexistent) prior marker returned 1 and errexit killed the
step. Fixed with an explicit `set +e` so the decision degrades to "review" on any
non-zero. `scripts/ci-gate/rereview-skip.test.sh` now runs the extracted block
under `bash -eo pipefail` (matching GitHub) — mutation-verified it catches the
abort. Amends [ADR 0019](docs/decisions/0019-gate-skips-rereview-on-unchanged-diff/README.md).

_Date: 2026-07-22; issue #124_

## Merge gate skips the paid re-review on a base-merge-only re-fire

Under active `main`, `preflight` keeps base-merging a green PR, which re-fires
`synchronize` and re-pays a full AI review even though the PR's own net diff is
unchanged — burning $ and widening the window that thrashes the required check
red (observed on #114). The `gate` job now computes the PR's net patch-id
(`git patch-id --stable` over `git diff <merge-base> HEAD`) and embeds it in the
approval marker: `<!-- ai-review-head:SHA patchid:PID model:M -->`. On a re-fire
it compares the current patch-id against the most recent **approval** marker's
patchid and, on a non-empty exact match, skips the model and reuses a
synthesized approved verdict — the unchanged authoritative merge-recheck still
re-verifies head/CI/hold/draft and does the squash, so correctness is unchanged.
Every ambiguous case (no merge-base, empty diff, missing/old/blocking-only
marker, different patch-id) falls through to the full review. Pinned by
`scripts/ci-gate/rereview-skip.test.sh` + `review-comment.test.sh` (wired into
`actions-ci`). See
[ADR 0019](docs/decisions/0019-gate-skips-rereview-on-unchanged-diff/README.md),
issue #120.

_Date: 2026-07-22; issue #120_

## Merge gate constrains its dispatch target to this org

The required org merge gate drives `gh pr view/merge --admin` against
`TARGET_REPO` under `ORG_ADMIN_TOKEN`, and `TARGET_REPO` came from a free-form
`repository` `workflow_dispatch` input — so a dispatcher could aim the
admin-merge machinery at an **arbitrary** org (a cross-repo admin-merge
escalation surface). The `preflight` job now runs an early `target_guard` step
that fails closed unless `TARGET_REPO` is an exact `<owner>/<repo>` whose owner
equals `github.repository_owner`: the default path and sibling-Verjson re-gating
pass, while a foreign owner or a malformed target (`x`, `a/b/c`, `Verjson/`,
empty) rejects the dispatch. The `repository` input is kept (operators can still
re-gate a sibling org repo), just bounded to the org. Downstream single-repo
copies should instead drop the input and pin `TARGET_REPO: ${{ github.repository }}`
(the Tequity ADR-0027 form), as documented in the workflow header. Pinned by
`scripts/ci-gate/dispatch-target-guard.test.sh` (wired into `actions-ci`). See
[ADR 0020](docs/decisions/0020-gate-constrains-dispatch-target-to-org/README.md),
issue #119.

_Date: 2026-07-22; issue #119_

## node-ci DB service - harden teardown test + record the security posture

Closes out the AI-review follow-ups from the #113 node-ci DB-service merge.
`scripts/ci-gate/node-ci-db-service.test.sh` now asserts the teardown step
combines `always()` with the `inputs.db-image` guard on the **same** `if:` line
(#115) — a regression dropping the guard (making teardown run unconditionally)
now fails the suite. And [ADR 0021](docs/decisions/0021-node-ci-caller-supplied-db-image/README.md)
records the decision to run a caller-supplied `db-image` on the shared
self-hosted pool and its trust boundary (first-party callers only; #117). The
single-host concurrency limit stays tracked in #116.

_Date: 2026-07-22; issue #115_

## Merge gate elides generated lockfiles from the AI review payload

The AI review lane fed the complete PR diff to the model, so a PR that touched
source **and** refreshed a lockfile spent its review budget reading the
generated lock (on `Verjson/verjson-ai#1`, `package-lock.json` was 8,007 of
10,559 added lines) and produced a no-verdict, fail-closed run. The
"Prepare bounded review context" step now writes the full diff to
`pr.full.diff` and hands the model a `pr.diff` with lockfile sections filtered
out, plus a prompt note naming the omitted locks so the manifest counterpart is
still reviewed. Lockfiles only are elided; manifests (`package.json`,
`Cargo.toml`, `Dockerfile`, …) are kept. Pinned by
`scripts/ci-gate/elide-lockfiles.test.sh` (wired into `actions-ci`). See
[ADR 0018](docs/decisions/0018-gate-elides-lockfiles-from-review/README.md),
issue #110.

_Date: 2026-07-22; issue #110_

## Reusable node-ci gains an optional, default-off Postgres service

`node-ci.yml` now accepts optional `db-image` and `db-env` inputs so a
Postgres-backed Node repo (e.g. tequityapp/tequity-api, tequity-worker running
Jest against `pgvector/pgvector:pg16`) can adopt the reusable without re-inlining
checkout/setup-node/test. When `db-image` is empty (the default) nothing changes
for current callers; when set, a conditional `docker run` step starts the image
on port 5432, health-waits with `pg_isready`, passes each `POSTGRES_*` pair into
the container, and exports every `db-env` pair — including `DATABASE_URL` — to
`$GITHUB_ENV` for `npm test`. A `services:` block was avoided because it has no
`if:` toggle and rejects an empty image, so it couldn't be defaulted off without
forcing a DB on every caller. Covered by
`scripts/ci-gate/node-ci-db-service.test.sh` (wired into `actions-ci.yml`). Refs
#108.

_Date: 2026-07-22; issue #108_

## Harden merge-gate runner-timing diagnostics against bad timestamps

The two diagnostic steps in `ai-review-merge.yml` ("Record preflight runner
timing" and "Record gate runner timing") computed a queue delta with bash
arithmetic over `date -d` output. A non-empty but unparseable timestamp made
`date -d` emit nothing, turning the expression into `$(( N -  ))` — a fatal
arithmetic syntax error under `set -uo pipefail` that aborted the required
preflight/gate job, contradicting ADR 0017's promise that these diagnostics
never change enforcement. Each `date -d` is now captured with `|| true` and the
delta is computed only when both epochs match `^[0-9]+$`, degrading a bad or
empty timestamp to the existing "unknown" notice instead of aborting. Covered by
`scripts/ci-gate/preflight-timing.test.sh` (extraction-based, wired into
`actions-ci.yml`). Ref issue #106; amends ADR 0017.

_Date: 2026-07-22; id:20260722T200814Z_

## ai-review gate — drop the unused `id-token - write` permission

The `ai-review-merge.yml` gate job declared `id-token: write`, but nothing in the
workflow mints an OIDC token — there is no `google-github-actions/auth`,
`aws-actions/configure-aws-credentials`, `azure/login`, or `getIDToken()` consumer
(the only `OIDC` string is inside the reviewer prompt's sensitive-class list). The
permission was dead grant. Removed it so the gate job's token is least-privilege
(`contents: read` + `pull-requests`/`issues: write` + `actions: read`). No behaviour
change. Surfaced by tequity's autonomous review (tequityapp/tequity-api#33,
tequityapp/tequity-ui#17); the tequityapp copies get the same drop on re-sync.

_Date: 2026-07-22; id:20260722T190802Z_

## pulumi-ci reusable failed to start — `secrets` in a job-level `env -`

The `pulumi-ci.yml` reusable workflow computed `HAS_CLOUD_CREDS` in a **job-level
`env:`** from `secrets.gcp-wip` / `secrets.pulumi-access-token`. `secrets` is not an
allowed context in a job `env:` (only `github`/`needs`/`strategy`/`matrix`/`vars`/
`inputs` are), so the workflow failed to parse and every caller got a
`startup_failure` with **zero jobs** — no compile gate, no preview. Surfaced as
`tequityapp/tequity-infra` `main` red on push (it is the only pulumi consumer, so it
was the only repo affected); the reusable-CI migration's other callers use
`node-ci.yml` and were unaffected. Fix: compute the live-vs-credential-free decision
in a first `Detect cloud credentials` step (secrets ARE valid in a step `env:`) and
expose it as `steps.creds.outputs.has_cloud`; the auth / `pulumi` / skip-note steps
now gate on that output. Behaviour is unchanged — live preview still runs only when
both GCP WIP and the Pulumi token are present, credential-free validation runs alone
otherwise. Reported from tequity's platform survey.

_Date: 2026-07-22; id:20260722T185050Z_

## Re-fire the merge gate when a terminal hold is removed

Subscribe the org merge gate to `unlabeled` events and narrowly re-run it when
`hold` or `DO NOT MERGE` is removed. This restores the documented hold/review
flow without running reviews—or cancelling an active review—for unrelated label
changes or the gate's own `re-review` cleanup. Closes #88; amends ADR 0012.

_Date: 2026-07-21; issue #88_

## Verify the pinned actionlint archive before extraction

Pin the upstream SHA-256 digest for the actionlint v1.7.7 Linux amd64 archive
and verify it before extraction or execution. A corrupted or replaced download
now fails closed, with the installer behavior covered by an extracted-workflow
test. Closes #83.

_Date: 2026-07-21; issue #83_

## Run actionlint when its runner-label config changes

Include `.github/actionlint.yaml` in actionlint's pull-request and main-push path
filters, with an extraction-based regression test for both events. Runner-label
allowlist edits can no longer bypass lint and drift from workflow `runs-on`
usage. Closes #82.

_Date: 2026-07-21; issue #82_

## Validate ADR index dates and both title separators

Reject malformed or impossible ADR `Date` metadata before generating the index,
including invalid leap days, and cover the supported plain-hyphen H1 separator
alongside the em dash. This keeps bad dates and unstripped numeric prefixes out
of the derived decision table. Closes #78 and #79.

_Date: 2026-07-21; issue #78_

## Clarify ADR coverage for sensitive bug fixes

Document that sensitive-class fixes restoring an existing invariant must amend
the controlling ADR, while genuinely new or superseding decisions receive a new
ADR number. This resolves the merge-gate policy ambiguity without forcing a new
record for every corrective patch. Closes #76.

_Date: 2026-07-21; issue #76_

## Keep the NEXT fast lane limited to flat fragments

Tighten the merge gate's documentation allowlist so only flat `NEXT/*.md` files
qualify for no-model review. Nested paths under `NEXT/` now follow the normal AI
review lane, matching the fragment layout documented by the repository. Closes
#75; amends ADR 0007.

_Date: 2026-07-21; issue #75_

## Point moving major tags directly at release commits

Peel each annotated `vX.Y.Z` release tag to its commit before creating the
annotated moving `vX` tag. This removes the unnecessary tag-of-a-tag chain while
preserving the published release and moving-major semantics from ADR 0014.
Closes #73.

_Date: 2026-07-21; issue #73_

## Parse Node version components as decimal

Normalize numeric Node version components to base 10 before evaluating the
semantic-release engine floor, so zero-padded input receives the intended clear
diagnostic instead of a Bash octal-parsing error. Fixes #101.

_Date: 2026-07-21; issue #101_

## Consolidate the AI merge gate into two runner jobs

Combine freshness with classification and AI review with merge so the required
gate uses at most two runner assignments and only one long CI wait. An immediate
head, hold, and CI recheck still fails closed before merge, with phase timing
diagnostics, aggregate phase-aware timeouts, an atomic expected-head merge guard,
and explicit accepted CI conclusions under extracted-shell regression coverage.
See #104 and ADR 0017.

_Date: 2026-07-21; id:20260721T222852Z_

## Record the reusable release-tool trust boundary

Amend ADR 0014 to capture how nested action SHAs, the exact semantic-release
lock, called-workflow commit identity, temporary-directory isolation, CI audits,
and reviewed Renovate updates form the trusted Node publish path. Addresses #99.

_Date: 2026-07-21; id:20260721T104332Z_

## Enforce the Node release runtime floor

Fail reusable Node releases with a clear diagnostic before installing the locked
semantic-release toolchain when a caller selects an unsupported runtime. CI now
tests the exact `^22.14.0 or >=24.10.0` boundary tracked in #98.

_Date: 2026-07-21; id:20260721T104331Z_

## Pin nested Node workflow actions and release tooling

Pin checkout and setup-node in both reusable Node workflows to audited v7 commit
SHAs, and replace runtime `npx` resolution with semantic-release 25.0.8 installed
from a lockfile checked out at the reusable job's own workflow commit. Renovate
now maintains the action digests and npm lock, with CI guarding the immutable
and vulnerability-free production graph. Addresses #89; initial `v1` release
bootstrap remains tracked separately in #85.

_Date: 2026-07-21; id:20260721T103146Z_

## actionlint deterministically lints the workflows

Phase 1 of #43 (issue #80). Adds `.github/workflows/actionlint.yml`, which runs
`actionlint` over `.github/workflows/**` on every workflow-touching PR — catching
the nit classes the merge-gate AI reviewer used to flag by hand (deprecated/
inconsistent action pins like #60, workflow-expression errors) deterministically
and for free, pre-review. actionlint is installed as a **pinned release binary**
(v1.7.7) rather than a container action, because the GCP self-hosted pool has no
Docker socket.

_Date: 2026-07-20; issue #80_

## Self-gate runner lane must be redundant (ADR 0016)

The `Verjson/.github` self-gate lane (`runs-on: [self-hosted, meta]`, reserved in
#14 so pipeline-fix PRs never queue behind bulk/other-repo CI) runs on a **single**
`meta`-only runner, `gha-meta-1` — a SPOF: if it drops, `.github` PRs sit un-gated,
and a run's four gate jobs serialize on the one runner (observed congestion).
ADR 0016 decides the lane must be **redundant** (≥2 `meta`-only runners) and
`docs/runner-routing.md` is updated to match. Fix: provision a second dedicated
runner `gha-meta-2` as a **GCE** VM (also resolves private composite actions,
unlike `gha-meta-1`), registered `meta`-only and moved into the `GCP` group after
registration. **No workflow change** — `runs-on: [self-hosted, meta]` picks it up
automatically. Provisioning the VM is the runner-topology owner's on-box step,
tracked by #70. Sensitive-class (runner topology).

_Date: 2026-07-20; issue #70_

## Fixture-based edge-case tests for the generator scripts

Closes #67. `scripts/gen-adr-index.sh` and `scripts/render-next.sh` were only
exercised by their live `--check`/smoke-run in CI, which catches drift but not
edge cases with a clear pass/fail. Added `scripts/ci-gate/gen-adr-index.test.sh`
and `scripts/ci-gate/render-next.test.sh` — each copies the **real** script into
a stubbed fixture tree (single source of truth, can't drift) and asserts the
fail-fast paths (ADR dir with no README, malformed `**Date:**`, missing index
markers, missing/empty `NEXT/`) plus the happy paths (reverse-sorted index,
`--check` staleness detection, newest-first render with README excluded and
`0000-archive` last). Wired into `actions-ci`, matching the per-script
`scripts/ci-gate/*.test.sh` convention.

_Date: 2026-07-20; issue #67_

## Docs fast-lane recognizes NEXT/ fragments, not just NEXT.md

Fixes #66. The classify job's docs/community-health fast lane (which skips paid
AI review for documentation-only PRs) matched the literal `NEXT.md` but not
`NEXT/<fragment>.md`. Since the changelog moved to `NEXT/` fragments (#65), an
"ADR + NEXT/ fragment" PR failed the all-files allowlist and paid for a full
model review — a regression against the cost-reduction goal (this session's own
docs-only PRs hit it). The allowlist now matches `NEXT(\.md|/.*\.md)`, so
fragment PRs take the free deterministic lane again. Pinned by
`scripts/ci-gate/classify-fast-lane.test.sh` (extracts the real jq predicate from
the workflow; mutation-tested; wired into `actions-ci`).

_Date: 2026-07-20; issue #66_

## Gate retries a third time on a transient structured-output flake

Fixes #64 (sensitive-class: org merge-gate behavior). The `ai-review` job could
fail with `error_max_structured_output_retries` — the model failing to emit its
structured verdict — on **both** the cheap first pass and the strong-tier
escalation in the same run, leaving the deterministic submit with no verdict and
failing closed (~50% of runs flaked on 2026-07-20). Added a **second bounded
escalation** (`claude_retry2`, sonnet-5, guarded on both prior passes being empty)
before the fail-closed submit; the verdict now falls back `claude_retry2 →
claude_retry → claude`. Because the flakes are roughly independent, a third attempt
clears the run ≈⅞ of the time. Fail-closed is preserved (`continue-on-error`; still
labels `ai-review-inconclusive` and refuses to merge if all three come back empty).
A mitigation, not a root-cause fix — #64 stays open to track distinguishing the
action's failure subtype if flakes persist. Pinned by
`scripts/ci-gate/ai-review-retry.test.sh` (mutation-tested, wired into
`actions-ci`). ADR 0015 (extends ADR 0002), carries the effective diff.

_Date: 2026-07-20; issue #64_

## Moving major tag is now annotated, matching the documented bootstrap

Fixes #61. `tag-major.yml` re-pointed the moving `vX` tag with `git tag -f`
(lightweight), while the documented bootstrap in
`docs/reusable-workflow-versioning.md` creates **annotated** tags (`git tag -a`)
like `v1.0.0`. The re-point now uses `git tag -f -a … -m …` so the moving major
tag carries a tagger/message and matches the release tags it tracks — no more
lightweight/annotated split across the `vX` and `vX.Y.Z` refs.

_Date: 2026-07-20; issue #61_

## node-ci.yml gains schema-dir + submodules support

Closes #84; unblocks the schema/api/worker legs of #49. `node-ci.yml` did a
plain `checkout` + single root `npm ci`, so the node-flavored templates — which
consume their schema package as a **git submodule + `file:` dep** — couldn't
adopt it without red CI (the worker PM correctly blocked rather than land a
regression, verjson-worker-template#8).

_Date: 2026-07-20; issue #49_

## node-ci.yml caller-doc - document the required packages -read permission

Follow-up to #86. A `node-ci.yml` caller must set `permissions: {contents:
read, packages: read}` on its job — a reusable workflow's `GITHUB_TOKEN` is
capped by the caller, so the reusable's own `packages: read` doesn't take effect
unless the caller grants it, and `npm ci` 401s on private `@scope/*` deps
otherwise. The verjson-ui-template migration hit this concretely (its gate
flagged the 401); documenting it in the caller example stops every future
node-ci.yml consumer from rediscovering it. Docs-only.

_Date: 2026-07-20; id:20260720T201316Z_

## Correct ADR 0015's PR cross-reference

ADR 0015 (gate retries a third time on the structured-output flake) landed via
PR #68, but its body cited PR #66 in two places (`- **PR:**` and the closing
"Full change:" link). Accurate ADR↔PR cross-linking is a stated convention (the
PR is the audit trail for the ADR's decision), so both references now point to
#68. Docs-only; no behaviour change.

_Date: 2026-07-20; id:20260720T152606Z_

## Changelog fragments + generated ADR index (kill the merge-conflict class)

Concurrent PRs used to conflict on every merge because each one prepended to a
shared `NEXT.md` and inserted a row in the shared `docs/decisions/README.md` ADR
table — during one fan-out round this forced three rebases of a single PR. This
removes that entire conflict class:

_Date: 2026-07-20; id:20260720T142237Z_
