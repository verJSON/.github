# 0220 — Confine release credentials to dependency acquisition

- **Date:** 2026-10-07
- **Status:** Accepted
- **Issue:** [verJSON/.github#1712](https://github.com/verJSON/.github/issues/1712)
- **Supersedes:** The verification-suite credential grant in the 2026-08-07 refinement [ADR 0062](../0062-release-verifies-before-it-tags/README.md).

## Context

Generated release-node, release-artifact, and release-snapshot workflows installed private packages with NODE_AUTH_TOKEN while npm lifecycle scripts were enabled. They then passed the same package credential to repository-controlled release-verification code. Their checkout also persisted GITHUB_TOKEN in .git/config for later tag lookup. A dependency lifecycle hook or verification hook could therefore read credentials needed only to download private packages.

ADR 0062's verification refinement gave the test hook package access because it might query the registry itself. The dependency tree is already installed before verification, so that access is not needed to run the installed test suite. Restart-safe tag lookup still needs read access to the repository, but it does not require persisting checkout credentials into the workspace.

## Decision

All generated Node release callers use the same short-lived credential boundary:

1. Check out the release tree with persist-credentials disabled.
2. Give GITHUB_TOKEN only to the restart-safe state-resolution step. That step uses process-scoped Git configuration for remote reads and does not write the token to Git configuration. Its complete shell body is pinned by the canonical generator's structural contract.
3. Give NODE_AUTH_TOKEN only to the verification-job step that runs npm ci --ignore-scripts. Then run npm rebuild with NODE_AUTH_TOKEN explicitly empty.
4. Run package preparation, version stamping, and release verification with NODE_AUTH_TOKEN empty.
5. Keep existing publication credential mappings unchanged. Publishing remains a separate job with the credential required by its existing workflow contract.

The credential-boundary test generates all three release modes and performs private registry acquisition against a local token-checking server. It proves install hooks did not run during acquisition and run only after the token is cleared. The structural contract rejects workflow mutations that expose credentials to lifecycle scripts, verification, inherited process environments, or persisted Git configuration.

## Implementation refinement (2026-10-07; issue #1717)

The clean verification process uses the trusted runner PATH and job-scoped changelog cache path captured as step outputs before dependency code runs; ambient GITHUB_ENV changes from lifecycle hooks cannot redirect verification.

The canonical generator contract also pins the verification step's selected-version condition, blocking behavior, command body, and approved environments across all three release modes. It validates the canonical selection-contract checkout's repository, path, immutable ref, and `persist-credentials: false` before normalizing the ref for the step digest. This keeps the guard stable as the contract advances without allowing a moving or altered checkout to pass. Regression cases reject skipped or nonblocking verification, changed checkout inputs, and credential-environment leakage.

This refinement makes the existing credential boundary fail closed in generated callers; it does not grant credentials to additional steps or change release authority.

## Implementation refinement (2026-10-09; issue #1724)

The canonical `node-release` publication workflow applies the same boundary to private dependency acquisition: one Actions step runs `npm ci --ignore-scripts` with `NODE_AUTH_TOKEN`, then exits; a separate step runs `npm ci --prefer-offline` without that secret so npm replays the locked install from its cache and runs the normal dependency and root lifecycle. Separate steps ensure lifecycle code cannot read the earlier step's token from an ancestor process environment. The offline contract test compares hook counts and per-package order with a normal install, inspects lifecycle ancestor environments, and covers empty dependency trees. Publication credentials and provenance behavior stay unchanged.

Package-directory provenance applies the generator's normalized relative-path and duplicate checks. For node-release callers, the forwarded package list must agree with the validated provenance and version-stamp command. The emitted contract validates the post-install version-stamp step's condition, environment, and full command separately from the pre-install digest, so a changed package set or an added command cannot hide behind the digest's installation boundary. The generated contract test pins expected package directories by workflow path. Additional release callers require an explicit path-to-package mapping in contract-test generation, and mixed additive/exact selection flags are rejected. Distinct release callers may select distinct valid package sets when each is pinned explicitly. Executable regression tests run the emitted `.npmrc` guard and verification command; they prove a workspace config blocks npm and the repository verification hook observes an empty package token.

The reusable `node-release` workflow now runs dependency acquisition, lifecycle scripts, package preparation, version stamping, builds, and packing in a preparation job with only repository and package read permissions. The private dependency token remains limited to the `npm ci --ignore-scripts` step; lifecycle scripts run in a later step without it. The legacy runner input remains accepted for caller compatibility but is ignored; ADR 0221 records the fresh-hosted boundary for all three jobs.

Package archives cross a one-day Actions artifact boundary to a fresh `ubuntu-24.04` publisher. The publisher reads expected package names and dispatched versions from the checked-out release tag, then runs the artifact validator from the immutable `contract-ref` checkout. Validation rejects unexpected files, symlinks, unsafe archive paths, identity/version mismatches, and SHA-512 digest mismatches before any package is published. The publisher runs no lifecycle, build, preparation, or pack scripts; `npm publish` receives only validated tarballs with scripts disabled and an explicit GitHub Packages registry. Every Node release job checks `runner.environment` as its first step and stops unless GitHub reports `github-hosted`; the scalar `ubuntu-24.04` selector alone is not proof of runner type because self-hosted runners may carry custom labels. The publisher and retention jobs still keep their existing job-scoped permissions and artifact validation. The uploaded artifact ID is carried as a prepare-job output and reused on release-only reruns, so those reruns do not derive a different artifact name from the new attempt number. The upload uses compression level zero because the package tarballs are already compressed, keeping the artifact API size close to the bytes the runner will extract. Before downloading, the publisher checks that size against a 2 GiB limit with actions-read permission. The validator also caps aggregate compressed and expanded archive bytes, each PAX and GNU metadata record, and cumulative PAX and GNU metadata to bound parser memory. GNU sparse archive formats are rejected before tarfile builds extent lists.

The build artifact is treated as untrusted transport data. Validation recomputes each archive's SHA-512 and requires it to match the digest in the transferred artifact manifest; it verifies package identity and version against the expected package list derived from the immutable tag. The digest check detects a mismatch between the manifest and archive, but is not an independent attestation of build provenance. The build process remains responsible for package contents as before. This refinement does not add a provenance attestation or change the existing publication/release-note behavior.

## Consequences

Installed dependencies remain available to release preparation and verification without a registry credential. A lifecycle hook that tries to fetch additional private packages must be redesigned; it cannot reuse the acquisition credential. Dependency lifecycle scripts run during the read-only preparation job under the package manager's existing script-approval policy. Every Node release job requires a fresh GitHub-hosted runner; callers that previously relied on their `runner` input must use hosted GitHub Actions capacity for the full Node release workflow.

## 2026-10-09 security review refinement — project npm config and caches (#1724)

The token-bearing `npm ci --ignore-scripts` now quarantines the release tag's root `.npmrc` for the duration of that command, restoring it on success and failure. A tag-controlled registry or auth directive therefore cannot redirect `NODE_AUTH_TOKEN`. The workflow also derives a cache-enabled output from the cache input, lockfile match, and only the boolean presence of the private dependency token; `setup-node` persistent caching and its cleanup are disabled when that token is present. Before setup-node runs, the reusable workflow writes a versioned namespace marker into the caller's checked-out workspace and removes it immediately after setup-node calculates the exact npm cache key. Tokenless runs therefore cannot restore legacy entries created before this policy. Contract coverage executes the actual marker creation and cleanup steps against a caller-workspace fixture, including conflicts and partial failures. Private dependency tarballs are not retained in cross-run Actions caches.

## 2026-10-09 refinement — separate npm configuration files (#1725)

The generated credentialless verification environment must assign npm's user and global configuration namespaces to distinct, controlled files. npm rejects loading the same `/dev/null` path for both namespaces before it runs the verification commands. Each generated release-node, release-artifact, and release-snapshot verification step now creates separate empty files under its private temporary home, applies a restrictive umask, and removes that home at step exit. A regression executes the generated verification command with real npm and a fixture build script in all three modes; it checks the two files are distinct, empty, private to the temporary home, and cleaned up afterward. These configuration files preserve the existing credential boundary and release authority.

## 2026-10-15 refinement — the verification hook receives the package retention window

**Context.** The post-publish `retention` jobs in `node-release.yml` and `container-release.yml` delete every stable version outside the newest `vars.CI_PACKAGE_RETENTION_KEEP || '3'` ([ADR 0108](../0108-bound-package-retention-to-three-stable-releases/README.md), [ADR 0115](../0115-configurable-package-retention-with-rename-deprecation/README.md)). A repository verification hook that must stop a release from pushing a still-consumed version out of that window (verjson-ci#298) could not see it: the generated verification step runs the hook under the `env -i` allowlist this ADR established. No new tracking issue was opened; the change is recorded here, in the PR, and in an issue-less `NEXT/` fragment.

**Decision.** Generated `release-node`, `release-artifact`, and `release-snapshot` callers admit exactly one new member to the verification step: `CI_PACKAGE_RETENTION_KEEP: ${{ vars.CI_PACKAGE_RETENTION_KEEP || '3' }}` in the step `env`, and the same name in the `run_clean` allowlist. The generator declares that expression once (`package_retention_keep_expr`). Within the verification step, before that step starts the hook or the default suite, it refuses a value that is not an integer from 1 to 999, through the same guard the retention jobs apply. Earlier verify-job steps (dependency installation, lifecycle scripts, package preparation) have already run repository-controlled code by then, so the guard bounds what the hook receives, not what ran before it. Because `verify` precedes the snapshot, an invalid organization window now stops a release before its irreversible tag. The value is not a credential, carries no repository-controlled input, and is identical in both halves of one release because a reusable workflow's `vars` context is the caller's. The member is admitted in all three modes, including `release-artifact` and `release-snapshot`, which run no package retention themselves, so the hook contract stays one shape across modes. That is a new failure mode, not a restatement of an existing one: an invalid window previously blocked no release at all. `node-release.yml` runs retention only after publication, and `container-release.yml`'s retention job is `continue-on-error`, so an invalid value surfaced as a failed or ignored post-publish cleanup. Failing `verify` instead is deliberate: the window has one valid range everywhere, and a hook cannot protect a version against a window it cannot read.

**One source.** The window cannot be shared at runtime between a generated caller and a reusable workflow, so identity is enforced instead of assumed: `scripts/ci-gate/release-verify-retention-keep.test.py` fails unless all five sites (the three generated verify steps and the two retention steps) read the same expression through the canonical guard, spelled literally. It also fails if the verify job or either retention job declares a job-level `environment:`, because an environment-scoped variable would rebind the window for that job alone. Passing the value from the caller into `node-release.yml` as an input was rejected: it widens the reusable workflow's interface and still leaves `container-release.yml` reading the variable itself.

**Guard correction.** The retention guard was `^[1-9][0-9]*$`. Bash bracket ranges follow locale collation, and under `en_US.UTF-8` the range admits U+0663 ARABIC-INDIC DIGIT THREE, which argparse's `int()` then reads as `3`. All guards now enumerate their digits and cap their length: `^[123456789][0123456789]{0,2}$`, which accepts 1 to 999 and only ASCII digits in every locale. The cap exists because an unbounded guard admits values that consumers read differently: 2**64 is 0 in bash arithmetic but a 20-digit integer to Python. 999 is far above any storage policy and inside every consumer's exact integer range. The tests run every invalid value, including Arabic-Indic and fullwidth digits, under both C.UTF-8 and en_US.UTF-8, and also assert the literal spelling, because a reverted range still passes under C.UTF-8.

**Contract.** The emitted contract test adds the member to the approved verification environment and re-pins the per-mode verification script digests; the verify-job step sequence and the pre-credential digest are unchanged, because no step is added and nothing before credentialed installation moves. Its executed #862 check now supplies the default the step env resolves. Three new rejection cases in `changelog-caller-contract.test.sh` prove that dropping the env member, re-defaulting it, or withholding it from `run_clean` each fail the emitted contract. Pin-aware: an adopter pinned to an earlier contract keeps validating its earlier caller with its earlier contract test, and the pinned-doc and fixture surfaces that execute older pins (`docs/changelog/migration.md`, `scripts/component-changelog-contract.test.sh`, the adoption-issue renderer) are left to the pin-bump that adopts this contract. Consumers receive the window by regenerating their adopter set at a contract SHA that includes this refinement.

**Same defect class elsewhere.** The defect is not specific to digits. `[a-z0-9]`-style letter ranges admit `é`, fullwidth `ａ`, and `٣` under en_US.UTF-8, and so do `case` labels and `grep` classes, not only `[[ =~ ]]`. Affected inputs include the node-release `prefix` and `scope`, the AI review App slug, the Renovate compatibility `package`, and many system-issued IDs. The rule is therefore general. Every shell guard (`=~`, `[[ == ]]`, `case`, `grep`) in every workflow, the composite action, the generator scripts, and the release generator's emitted callers and contract test lists its ASCII characters literally, for example `[abcdefghijklmnopqrstuvwxyz0123456789._-]`. Ranges are not allowed, and neither are POSIX classes other than `[:space:]`/`[:blank:]`. The whitespace classes only separate tokens in structural matches and never decide which characters a value may contain. A non-ASCII space can still widen such a separator; that is an accepted boundary.

The fix spells the characters out rather than setting `LC_ALL=C` in each step. Spelling out is the one approach applied everywhere, for three reasons:
- A literal class means the same thing wherever the guard runs or is copied.
- `LC_ALL=C` depends on ambient state that a later `GITHUB_ENV` write, a new step, or a moved guard silently loses.
- `LC_ALL=C` also changes sort order, `grep`, and tool output for every other command in the step.

It is also the form the digit guards already used. A spelled-out class admits exactly the ASCII characters its range did, and each of the 27 distinct rewrites was checked character by character in the C locale. Generated workflows are regenerated from their generators. The release generator's pre-credential step digests are re-pinned only for its two emitted dispatch guards.

`scripts/ci-gate/settable_value_guards.py` finds these guards subject-agnostically. It does not trace values through shell copies, `GITHUB_OUTPUT`, `GITHUB_ENV`, or job outputs, because once every guard must be canonical a guard on a copied, handed-off, or API-read value is covered without any tracing. A regex held whole or in part in a single-assignment single-quoted variable is resolved before it is judged. Any other unreadable shape fails unless reviewed. `scripts/ci-gate/settable-value-guards.test.py` does the following:
- pins the guard inventory (437 after #1826);
- probes every distinct class with non-ASCII letters, digits, and spaces, requiring the same decision under C.UTF-8 and en_US.UTF-8;
- runs the named letter-guard sites and the AI review budget `awk` check in both locales.

`.github/workflows/release.yml` is exempt as generated at an older pinned contract; the pin bump regenerates it. Accepting ID guards cap at 15 digits, inside jq's exact integer range. The AI review budget guards are recorded in [ADR 0082](../0082-receipt-bound-provider-budget-policy/README.md). Out of scope are `sed`/`awk` transforms, which normalize text rather than admit it, and non-generator shell scripts under `scripts/`. Those scripts receive values the workflow boundary has already guarded.

**Behavior change for an out-of-range window.** A `CI_PACKAGE_RETENTION_KEEP` above 999 now fails, whether it is set at organization or repository level, because `vars` resolves the repository value first. So do 0, a non-integer, and a non-ASCII value.
- It fails `verify`, before any snapshot, in every generated release mode.
- It fails the `node-release.yml` retention job after publication, which turns that run red.
- `container-release.yml`'s retention job is `continue-on-error`. Its failure leaves the run green: the release was promoted and older image versions were simply not deleted.

Both retention jobs now write the skip and its consequence to the step summary before failing, so the green container run still shows it. `continue-on-error` stays, because a cleanup failure after promotion must not mark the promoted release as failed.

## 2026-10-15 amendment — retention is an explicit policy value; `all` keeps every version

**Context.** Keeping every package version used to require an arbitrarily large `CI_PACKAGE_RETENTION_KEEP`. The 1-999 cap above turns that workaround into a hard failure. The owner decided that "keep everything" must be a stated policy, not a large number.

**Decision.** `CI_PACKAGE_RETENTION_KEEP` is a policy value with one language, accepted identically by every site that reads it:

| Value | Meaning |
| --- | --- |
| unset | the organization default, `3` (ADR 0108), through `vars.CI_PACKAGE_RETENTION_KEEP \|\| '3'` |
| `1`-`999`, ASCII digits, no leading zero | keep the newest N stable versions |
| `all` | retention disabled; nothing is deleted |
| anything else | fail closed with an error naming the accepted forms |

"Anything else" includes `0`, values above 999, non-ASCII digits, whitespace, and case variants such as `ALL`.

The sites that read the value:
- the generated release verify step, in all three modes, which forwards `all` to the hook;
- the `node-release.yml` and `container-release.yml` retention jobs. With `all` they pass the value to `package_retention.py`, which validates its targets, prints `package-retention: disabled by policy (keep=all)`, and returns before any API call. The jobs also write "Package retention disabled by policy" to the run summary.

`package_retention.py` parses `--keep` through the same language (`KEEP_POLICY`, `parse_keep`). Its integer path is unchanged, because its only callers already cap at 999.

**Why exact lowercase `all`.** Every consumer compares the value as a string: five shell guards, `package_retention.py`, and each adopter's release hook. A second accepted spelling would have to be normalized identically in all of them, and one consumer that forgets would read `ALL` as an invalid number or, worse, as a window. One spelling keeps the comparison a plain equality everywhere, and the error message names it.

**Enforcement.** `scripts/ci-gate/release-verify-retention-keep.test.py` runs a shared corpus through all five shell guards and `parse_keep` under C.UTF-8 and en_US.UTF-8, and requires one accept/refuse decision per value. It also runs each retention job with `all` against an unreachable API and requires no deletion plus the summary line. `scripts/package_retention_test.py` covers the parser and the no-op.

**Consequence for adopters.** A release hook that reads the window (verjson-ci#298) must treat `all` as "never evicts" and pass. Once its caller is regenerated, the variable is always present, so an absent value is an error the hook should fail closed on.

