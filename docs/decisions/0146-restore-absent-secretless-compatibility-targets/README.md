# 0146 — Restore absent secretless compatibility targets

- **Status:** Accepted
- **Date:** 2026-08-26
- **Amends:** [ADR 0141](../0141-swap-verified-compatibility-packages-without-graph-resolution/README.md)
- **Category:** Security-sensitive (credentialless package-swap boundary)
- **Issue:** [#1114](https://github.com/Verjson/.github/issues/1114)

## Context

The canonical secretless compatibility lane swaps a provenance-verified published
package into an installed consumer graph without registry access. ADR 0141 required the
target package directory to exist first. A package testing its own published baseline
correctly has no self-dependency, so its scoped `node_modules` parent can exist while the
exact self-package target does not. Requiring a self-dependency would manufacture a
retention-sensitive package pin and change the graph being tested.

Letting an absent target through without tracking its initial state would leave the
published package behind after the lane. Following a symlinked or replaced parent could
write the verified artifact outside the installed graph, while treating a target that
appears during staging as the expected absence would overwrite unverified state.

## Decision

Permit only the final compatibility package target to be initially absent. Open and hold
every existing parent component from `node_modules` to the package parent as a real
directory with no symlink following, reject path components that can escape that chain,
and revalidate the held parent identity before staging, swapping, and cleanup.

Record whether the exact target was absent before any staging. The first lane may occupy
that absence only if the target is still absent; later lanes may replace only the exact
directory identity installed by the preceding verified lane. A symlink, non-directory,
missing or replaced parent, changed staged target, or independently appearing target
fails closed before consumer execution.

Keep the verified staging directory descriptor open through placement and use Linux
`renameat2(RENAME_NOREPLACE)` for the transition into the held package parent. The placed
name must resolve to the staged inode before consumer execution, and the same open inode
must remain at that name after execution. Competing target, staging-name, parent, or
post-execution identity changes fail without deleting the competing entry.

Do not let `npm run` resolve the mutable installed pathname. Require the trusted,
root-owned `/usr/bin/bwrap`; an absent or writable executable fails closed. For
each lane, derive a private package projection directly from the verified archive
bytes: every regular file is copied through a sealed anonymous `memfd`, the
package is assembled on private tmpfs inside fresh user, PID, IPC, UTS, and
cgroup namespaces, and the package mount is remounted read-only. The sandbox
drops all capabilities and disables nested user namespaces before it runs the
consumer script. The archive and provenance paths exposed to that script are
separate sealed anonymous files in the same private mount. Its workspace and
`node_modules` path topology are private, so
an earlier PR-authored background process can rename, load, and restore the host
target without changing which package bytes the consumer resolves. The external
installed target remains only the state whose placement and restoration the lane
audits; it is not the consumer's resolution source.

Run the two executable compatibility contract suites in a dedicated
`ubuntu-24.04` actions-ci job. The persistent platform-test fleet does not carry
the required bubblewrap executable, so it must not register either suite. Keep
the hosted job as a required input to the unmatrixed `shell-tests` aggregate;
missing, skipped, or failed hosted execution therefore fails the existing
required context instead of silently reducing coverage.

The hosted Ubuntu image also does not preinstall bubblewrap. Only when a
secretless PR or trusted-ref compatibility lane will execute on a GitHub-hosted
runner, acquire `bubblewrap` from the image's signed Ubuntu apt repositories.
Use no credentials and perform no broad upgrade. Require the Ubuntu package
version and executable version to meet the 0.9.0 floor used by the namespace
flags, bind `/usr/bin/bwrap` to the installed package, and require a regular,
executable, root-owned binary that is not group- or world-writable. The
actions-ci hosted contract uses the byte-identical provisioner before executing
the suites. Self-hosted runners are never installed or mutated by the workflow;
the runtime boundary continues to reject a missing or unsafe preprovisioned
binary there.

The first hosted receipt failed with the fixed `bubblewrap-unavailable`
category. Signed bubblewrap acquisition reached the next boundary, where the
unprivileged production-shaped probe failed with
`bubblewrap-namespace-denied`: Ubuntu Noble's AppArmor policy restricts
unprivileged user namespaces unless the invoking application has an explicit
profile. The hosted-only acquisition therefore also installs `apparmor` and
`apparmor-profiles`, with both packages at or above Noble's first SRU that
contains `bwrap-userns-restrict`,
`4.0.1really4.0.1-0ubuntu0.24.04.3`.

The next hosted receipt failed at `ancestor-directories`. The exact
`ubuntu-24.04` image build intentionally makes `/usr/share` recursively
world-writable ([runner-images `ubuntu24/20260823.283`, lines 12–13](https://github.com/actions/runner-images/blob/ubuntu24/20260823.283/images/ubuntu/scripts/build/configure-system.sh#L12-L13)),
so neither the installed profile nor the image's `/usr/share/keyrings` archive
key can cross the privileged load boundary. Do not relax ancestor validation,
copy from that installed pathname, or use the image's global apt source, list,
cache, or key configuration.

Instead, validate the package-owned Ubuntu archive key rooted under
`/etc/apt/trusted.gpg.d`, then create an exclusive root-owned acquisition
session. Configure fixed Noble archive and security HTTPS sources, fresh
source/list/cache state, the verified system dpkg status, and the safe key only.
Disable the global apt netrc and preferences files and route their directory
forms to validated empty root-owned directories alongside the already isolated
source, trusted-key, and configuration parts.
Keep apt authentication, weak-repository, downgrade, date, validity, and TLS
checks enabled; simulate and bound the no-remove install plan before installing
`bubblewrap`, `apparmor`, and `apparmor-profiles`. The `_apt` account owns only
the isolated partial directories. Raw apt, package, archive, and helper output
never crosses the fixed diagnostic boundary.

Bind the exact `apparmor-profiles` version, `Architecture: all`, normalized
repository filename, size, and SHA-256 to the signed Packages metadata, then
download exactly that archive into a second empty
isolated cache. Independently require its signed size and SHA-256, control
fields, exact normalized basename, and one canonical regular root-owned,
non-executable profile member. Reject absolute, traversing, encoded, duplicated,
or otherwise ambiguous signed filenames and any downloaded-name mismatch.
Stream that member from the archive, validate its ABI 4.0, absolute bwrap
attachment, transition from bwrap to the restricted child profile, recursive
child transition, audited capability denial, and absence of any unconfined
flag. Stage those verified bytes atomically under the root-owned, non-writable
`/run/verjson-compatibility-sandbox` hierarchy. The installed `/usr/share`
pathname remains package-database evidence only and never authenticates or
supplies the staged bytes.

Bind `/sbin/apparmor_parser` and `/usr/bin/bwrap` to their installed packages.
Require every executable and the safely staged profile to be regular,
root-owned and root-grouped, hardlink-free, capability-free, and not group- or
world-writable; executables must be executable and the profile must not be.
Reject either optional local bwrap profile override, including a broken
symlink, so the parser cannot compose unverified host policy into the signed
package profile.

The filesystem verifier additionally rejects set-id or file-capability-bearing
executables. It verifies the lexical usrmerge `/sbin` link, resolved parser and
safe staged-profile ancestry, `/etc/apparmor.d[/local]`, and the complete existing ABI and
tunables include trees as root-owned, root-grouped, and not group- or
world-writable;
symlinks and special include entries fail closed. Executables, the profile, and
include files are read through no-follow descriptors and bound by metadata and
content to a deterministically ordered receipt. Both the verifier and the
privileged loader run isolated Python under a fixed empty environment, so
working-directory modules and ambient credentials cannot influence the check.
The loader recreates the expected receipt, retains the no-follow parser and
profile descriptors, and directly executes the verified parser descriptor with
the verified profile descriptor; it never returns to a pathname-based shell
load after verification.
Profile validation is block-structural: the parent transition must occur in the
bwrap block, the recursive transition and audited capability denial must occur
in the restricted child, and that exact denial is the only child rule that may
mention capabilities. This
closes the reachable non-root background-process race without claiming
protection from a pre-existing privileged process outside the workflow threat
boundary.

When this filesystem boundary fails, report only a fixed phase from a closed
allowlist: ancestor directories, the usrmerge parser link, local overrides,
the ABI or tunables tree, the bwrap or parser binary, the package profile,
profile semantics, receipt recomputation, profile load, acquisition cleanup, or unknown. The
privileged loader communicates the same phases through fixed exit codes while
its stdout and stderr remain suppressed. The unprivileged verifier's shell
boundary captures all producer and interpreter output, accepts only an exact
receipt on success, and replays only an exact allowlisted diagnostic on
failure. Exception text, paths, environment values, parser output, and any
non-allowlisted value never cross the diagnostic boundary; an unexpected
failure becomes the fixed `unknown` phase.

No verifier output crosses a runner-owned pathname. A static isolated root
supervisor receives the hash-bound trusted verifier source on stdin and starts
the verifier child from those immutable bytes in an isolated `-c` argument,
with stdin closed, a fixed empty environment, and in-memory byte pipes. The
trusted verifier is read-only; it accepts only the fixed package-profile path,
and the loader remains the sole privileged state-changing path. The supervisor
emits only an exact receipt or
allowlisted diagnostic whose bytes and exit status agree. Trailing records,
embedded or trailing NULs, status/content mismatches, child or supervisor
failures, exception text, and every other value become fixed `unknown`; outer
stderr is suppressed. Concurrent same-UID directory, raw-file, receipt-file,
symlink, hardlink, and rewrite attempts have no artifact to substitute. The
separate privileged loader retains its descriptor-bound recomputation and
parser execution.

First attempt the production namespace and capability-drop probe as the
unprivileged runner under an empty credential environment. Only if it fails,
load that verified package-owned restrictive profile with the absolute parser
under credential-scrubbed `sudo`, then require the same unprivileged probe to
pass. Never disable the AppArmor sysctl, make bubblewrap setuid, invoke
bubblewrap as root, download a profile, or fall back to weaker isolation. Those
alternatives either weaken the host policy globally or move PR-influenced path
and mount processing into a privileged process. The exact hosted actions-ci
mirror runs this same prerequisite before its compatibility contracts;
self-hosted execution remains excluded from every install and policy mutation.

The acquisition supervisor is digest-bound trusted static code executed as
root with a fixed empty environment, closed stdin, in-memory output, and a
bounded runtime. It records the exclusive session and staging directory
identities, removes the session on every terminal path, removes an uncommitted
stage after failure or handled signal, and never reuses residue. Cleanup refuses
identity drift and emits only the fixed acquisition-cleanup phase. A successful
run preserves only the immutable staged profile needed by the descriptor-bound
verifier and loader.

Keep the exact embedded acquirer behind a conventional `main()` guard so its
production functions can be imported without running privileged acquisition.
The registered behavioral contract extracts the byte-identical node-ci and
actions-ci source and executes its real orchestration and guards with
deterministic OS and subprocess fixtures. Compilation treats `SyntaxWarning` as
fatal for production and every mutation.

Acquisition failures use a closed, secretless taxonomy. The child resets its
phase before each run and advances before key validation, apt update, bounded
plan, install, installed status, signed metadata, download, archive, member,
and staging. It emits only that fixed `package-acquisition-*` diagnostic. The
digest-bound supervisor accepts only child status 1 plus one exact diagnostic
and maps the ten phases uniquely to statuses 83 through 92; cleanup remains 82
and unknown remains 81. The outer shell accepts only exact status-and-diagnostic
pairs, so cross-paired, trailing, NUL, raw, timeout, and interpreter results
become `unknown`. Staged/archive distinctness compares `(st_dev, st_ino)`:
equal inode numbers on different filesystems are valid, while an equal pair
fails closed.

Enumerate signed Packages metadata with the exact `Created-By: Packages` and
`Component: main` selectors. `apparmor-profiles` is published in Main, while
unrelated Universe records can contain lines larger than the deliberately
bounded parser accepts. Keep Universe in the fixed apt source components for
package resolution, but do not broaden the metadata parser or its 65,536-byte
per-line bound or its existing total-input bound. Bind the selected metadata
paths by uniqueness, safe list-root prefix, regular-file validation, exact
package tuple, normalized signed filename, and `_all.deb` suffix.

When the target began absent, retain its installed directory identity and remove that
exact directory after success, script failure, `SIGINT`, or `SIGTERM`. Cleanup first
quarantines the identity under the held package parent and refuses to delete a different
entry. Existing-target swap and post-success replacement semantics remain unchanged.
No consumer self-dependency or package pin is introduced.

On 2026-08-28, follow-up hardening made three existing fail-closed boundaries explicit.
An unavailable sealed-memory primitive now emits one fixed diagnostic instead of raw
platform detail. Execution-level coverage proves the descriptors used to populate the
private mounts are closed before the npm consumer starts. A supposedly empty backup
container that gains an unexpected entry restores the owned initially-absent target,
preserves the unexpected entry for diagnosis, and exits with a fixed diagnostic rather
than deleting unowned state.

## Consequences

- Self-packages can exercise multiple published compatibility lanes without changing
  their dependency graph.
- Consumer code sees one fully verified staged package per lane; the initially absent
  target is absent again at job teardown.
- Consumer resolution is bound to sealed archive bytes in a read-only private
  mount, rather than inferred from equal path metadata before and after `npm`.
- Actions CI exercises that namespace boundary on an explicit hosted image and
  aggregates its result into `shell-tests`; registration mutations reject suite
  removal or reassignment to the incompatible persistent platform lane.
- Hosted package acquisition is narrowly gated, signed, credentialless, and
  fail-closed. Parity and ordering mutations prevent the contract job from
  exercising a different prerequisite than canonical node-ci consumers.
- The privileged parser never trusts the hosted image's world-writable
  `/usr/share` tree; its profile bytes come from an independently authenticated
  package archive and an identity-bound root-only staging hierarchy.
- Parent and target races fail the job and preserve unexpected state for diagnosis rather
  than deleting or overwriting it.
- Real-shaped tests cover success, script failure, signals, multiple lanes, parent and
  target symlinks, missing parents, path escape, and target appearance during
  staging. A deterministic swap/load/restore control proves pathname execution
  loads attacker bytes while the bound lane continues to load the verified
  version.


## 2026-09-20 correction — reject unsupported runner combinations early

[Verjson/.github#1416](https://github.com/Verjson/.github/issues/1416) found that a `secretless-trusted-ref` call could combine `secretless-compatibility-ranges` with a self-hosted runner. Compatibility sandbox provisioning remains intentionally hosted-only, but the compatibility lane was not, so the call failed only after dependency acquisition when `/usr/bin/bwrap` was unavailable. Keep hosted-only provisioning and reject this unsupported combination in the `eligibility` job before acquisition or build work. The check uses the selected runner's `runner.environment`, so it also catches the default self-hosted lane when the caller leaves `runner` unset. It applies only to trusted-ref mode: secretless pull-request builds deliberately ignore the caller's runner and use the untrusted lane.

The regression assertion lives in `scripts/ci-gate/node-ci-secretless-compatibility.test.sh`: the eligibility guard must be limited to trusted-ref compatibility calls on non-hosted runners and must fail with the stable diagnostic. This preserves the existing fail-closed sandbox boundary rather than silently skipping compatibility verification.

## 2026-10-15 — guard superseded by ADR 0234

The 2026-09-20 correction's eligibility guard read the eligibility job's own `runner.environment` as a proxy for the build job's runner. [ADR 0234](../0234-route-secretless-trusted-ref-builds-to-the-hosted-lane/README.md) routes every secretless `build-test` to the hosted `CI_LANE_UNTRUSTED` lane, so that proxy no longer predicts where compatibility lanes run and is replaced by a guard on the lane itself. Hosted-only sandbox provisioning is unchanged.
