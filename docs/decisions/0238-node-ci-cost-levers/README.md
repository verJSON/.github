# 0238 — node-ci cost levers: larger hosted sizes, diff-classified plans, and what stays caller-side

- **Date:** 2026-10-22
- **Status:** Accepted
- **Issue:** [Verjson/.github#1869](https://github.com/Verjson/.github/issues/1869)
- **Category:** runner topology and required-check scope (sensitive class)
- **Related:** [ADR 0222](../0222-documentation-diffs-skip-heavy-actions-ci/README.md),
  [ADR 0234](../0234-route-secretless-trusted-ref-builds-to-the-hosted-lane/README.md),
  [ADR 0147](../0147-route-untrusted-ci-to-ephemeral-hosted-runners/README.md)

## Context

A private adopter spent 3,406 runner-minutes on 58 pull-request runs in one day.
Most of it was repeated work: three lanes each build the app, an optional heavy lane
runs beside the lane that gates it, and a documentation-only diff pays for every
lane. #1869 proposed five canonical levers. Each was checked against what the reusable
workflow can actually own: `node-ci.yml` is one job per call. It cannot see its
sibling lanes, it runs only the caller's `package.json` scripts, and a matrix inside it
would rename the required `<lane> / build-test` context.

## Decision

1. **Larger hosted sizes are admitted for callers outside Verjson (implemented).** The
   ADR 0234 hosted-lane guard accepts `ubuntu-<latest|YY.MM>` optionally followed by
   `-4-`, `-8-`, `-16-`, `-32-` or `-64-` plus `cores`. The list is closed: arm, gpu,
   and arbitrary suffixes remain refused, as does any label mixed with a self-hosted
   label. A label cannot tell a hosted larger runner from a self-hosted one, and the
   credentialed acquisition job also runs on this lane. The organization's own repositories
   therefore keep exactly the reconciled standard image (`runner-admission-reconcile`,
   ADR 0147, which pins `CI_LANE_UNTRUSTED` to `["ubuntu-24.04"]` and allowlists larger
   runners by identity); a Verjson repository needs a reviewed allowlist identity and a
   reconciler change before it may use a larger size. An external adopter selects the size
   through its own `CI_LANE_UNTRUSTED` variable; the owner comparison folds case because the
   canonical login is `verJSON`, and owns the trust of its runner groups.
   The runtime `RUNNER_ENVIRONMENT=github-hosted` checks in the lifecycle rebuild and
   candidate sandbox remain the backstop. Larger machines raise the resource ceiling of
   the sandboxed scripts but not their reach.
2. **A diff classifier selects a lighter plan (implemented).** Two new optional inputs,
   `secretless-ci-non-app-script-plan` and `secretless-ci-non-app-paths`, let a
   `secretless-pr` call run a reduced non-empty plan, still as the same job and so under
   the same `<lane> / build-test` context. The classifier reads the changed-file list
   from the GitHub compare API at the PR's admitted base and head, never from the
   checkout, and fails closed to the full plan on any API error, truncation, empty list,
   or path outside the allowlist; a base or head that is not a 40-hex object name is a
   malformed event and fails the step. Non-application means a path under `docs/`, `NEXT/` or
   `CHANGELOG/`, a top-level Markdown file, or a caller-declared directory prefix; nested
   Markdown is application content until declared. Only `secretless-pr` on a
   `pull_request` event is eligible, never a trusted-ref push. Workflow files and Node package, lockfile,
   registry-config, git and toolchain-pin files can never classify as non-app, whatever
   the caller declares. A declared prefix asserts that nothing under it feeds the Node
   build, so `python/requirements.txt` under a declared `python/` is non-app. The classifier
   judges paths only: it does not see tree modes, so a changed symlink or gitlink under a
   non-app prefix is non-app too. That is a coverage choice, not a security boundary: both
   plans run in the same credentialless sandbox with the same token handling.
3. **Optional-lane ordering stays caller-side (no change).** A reusable workflow cannot
   `needs:` a sibling caller job. An adopter orders an optional heavy lane by writing
   `needs: verify` and `if: needs.verify.result == 'success'` on that caller job. This is
   safe only for lanes that are not required contexts, because a skipped caller job posts
   the wrong check name (see #1869's run 39548139618). This ADR records the pattern.
4. **Worker and shard counts stay in the consumer's scripts (no change).** node-ci
   runs `package.json` scripts and never invokes vitest or Playwright, so it has
   nothing to pass `--maxWorkers` or `--shard` to. A `shards` input would need a matrix
   in the reusable, which renames the required context to `build-test (n)`. Adopters
   shard in their own scripts, or fan out at the caller and aggregate in a required job
   they own.
5. **Build-once artifacts are deferred (open decision).** Lanes build in separate
   sandboxes, and sharing a `.next` build means a new egress path out of the credentialless
   sandbox plus a consume path that treats the artifact as untrusted. That widens the
   secretless guarantee and needs its own decision from the owner before any code.
6. **Sandbox system packages are deferred (open decision).** The sandbox has no sudo
   and no `/var`. Playwright system libraries are part of the contract of the hosted
   `ubuntu-24.04` and `ubuntu-latest` images, and an adopter must not use
   `--with-deps` inside the sandbox. A matching `pg_dump` major (for example a `pg-major`
   input) means installing an apt package before the sandbox starts, which belongs in an
   audited, pinned provisioning step like the existing sandbox provisioning. It is
   not designed here.

## Consequences

An external adopter can trade per-minute rate for wall time on a vetted size without
holding an old pin. The security posture of the secretless lane is unchanged: no input in this
change touches credentials, network policy, or the sandbox. Item 5's direction is recorded
in the 2026-10-24 amendment below; implementation remains subject to its security gates.
Item 6 remains open and is reported on the issue.

*Annotation 2026-10-23 (#1875).* Items 5 and 6 here are this ADR's own numbering
(build-once artifacts; sandbox system packages). They are not changes 5 and 6 of the
[#1869](https://github.com/Verjson/.github/issues/1869) cost list (larger hosted runners;
the unbounded container cache), which landed as #1871 and #1870, and #1872 added the diff
classifier. None of those three delivered either deferred decision, so both remain open
and need the owner's decision before any code.

## Amendment 2026-10-22 — concurrency stays caller-side; test levels not yet designed in the engine

ARB change 3 of #1869 proposed `concurrency` groups with `cancel-in-progress` inside the
reusable CI workflows. A disposable private probe repository (two calls of one reusable
workflow in one run, `workflow_dispatch`) established four facts:

- A group that evaluates to the empty string is a **startup failure with no jobs**, at job
  level, at workflow level, and through a `fromJSON(...) || null` object form. A reusable
  workflow therefore cannot make its group conditional on an input.
- `github.job` is `j` in both calls and `github.run_id` is shared, so nothing available to a
  called workflow distinguishes two calls in one run.
- Two calls that resolve the same group in one run cancel each other (`Canceling since a
  higher priority waiting request for <group> exists`); without `cancel-in-progress` the
  pending one is still replaced.
- Callers with several lanes (the case #1869 measured) would hit exactly that.

So a group declared directly in the reusable is either shared by sibling lanes, which
serializes or cancels them, or needs a caller-supplied lane key that still cannot be left
empty when the feature is off. A job-level variant is conceivable (a preceding job exports a
unique per-call value such as `job.check_run_id` through `needs` for the disabled path, and
a caller-supplied lane key plus the pull-request number names the enabled path), but it adds
a dependency to every call, cancels per job rather than per run, and its failure paths are
undesigned; it is **not adopted here**, not proven impossible. **Decision: cancelling
superseded pull-request runs stays at the caller's workflow level**, where one group covers
all lanes. `docs/node-workflows.md` carries the snippet (cancel only `pull_request`; every
other event, including `pull_request_target`, gets a disjoint `run-` group unique to the
run), and `scripts/ci-gate/reusable-ci-no-concurrency.test.py` pins that the reusable CI
workflows and the protected twin declare none and that the snippet is exact.

Probe receipts: repository `pyousefi/ci-concurrency-probe-1869` (private, archived), commits
`0089e5c`, `1cd28ad`, `dfba982`; runs 39749615522 (two same-group calls cancelled each
other), 39750020363, 39750024475, 39750029020 (empty group at job level, workflow level and
`fromJSON || null`: each completed `failure` with zero jobs), 39749867750 (`github.job` and
`github.run_id` identical in both calls). The startup-failure message itself is not
exposed through the API; the evidence is the zero-job failed run.

A typed `test-levels` input is **not** part of this amendment. The script-plan engine is
parsed in three separate Python blocks of `node-ci.yml` and rewritten by string replacement
into the protected twin; continue-after-failure semantics for a non-fail-fast level need a
design of their own and a decision on what `cancel` means for a level once concurrency is
caller-side. Until then the existing `secretless-ci-script-plan` is an ordered, fail-fast
list.

## Amendment 2026-10-22 — typed test levels, generated not interpreted

The ARB (four models) converged on ARB change 3's open question. A reusable workflow has
a static job graph, so a dynamic DAG needs generation. **Decision:** a caller declares
ordered levels in `ci-levels.json` and `scripts/gen-node-ci-caller.py` emits one
`node-ci.yml` call per level; `scripts/ci_levels.py` is the single validator and renderer.
`node-ci.yml`, the protected twin, and every digest are untouched, and with no levels file
the generator emits the single `ci` job, so `ci / build-test` is unchanged.

- **`failFast` is level-local**, with matrix semantics: it governs siblings inside a
  level and never aborts an independent level. Slice 1 implements only `true`; `false`
  needs the in-lane work of a later slice and is rejected with `fail_fast_unsupported`
  rather than accepted as a silent no-op.
- **Continue-after-failure belongs to the edge.** `needs` entries carry `condition`
  `success` (default) or `completed`. A `completed` edge renders
  `!cancelled() && (needs.x.result == 'success' || needs.x.result == 'failure')`, because
  a bare `!cancelled()` would not enforce prerequisite success. Every level with `needs`
  renders explicit direct-result predicates, so a success edge is not skipped because a
  grandparent failed behind a `completed` edge.
- **`cancel` is dropped.** Unknown fields are typed errors. In-run cancellation through
  the Actions API needs `actions: write` and a privileged controller, which the
  credentialless untrusted lane (above) forbids; job conditions cannot stop running
  siblings. Superseded-run cancellation stays the caller workflow-level `concurrency`
  block from the amendment above.
- **No silent green.** The generated `test-levels` aggregate (fixed name, `always()`,
  `permissions: {}`) fails unless each expected level succeeded and each other level was
  skipped, so failure, cancellation, and unexpected skips all fail it. `continue-on-error`
  is never used, because GitHub treats skipped required checks as passing.
- **Validation is one pure function** producing an immutable plan or `{code, path,
  message}` errors: duplicate keys and ids, unknown fields, malformed booleans, empty
  work, unknown or non-preceding `needs` (cycles are unrepresentable), needs that skip
  where the dependent runs, unknown `node-ci.yml` inputs, and bounded plan size.

Not decided here: unifying the three embedded script-plan parsers, in-lane
`failFast: false`, and the build-once artifact set, which still needs the owner's
sandbox-egress decision above. Required-check enrollment of `test-levels` must be
validated live against a ruleset before any adopter relies on it; this slice proves the
rendered text and its truth tables, not a live Actions run.


## Amendment 2026-10-23 — fail fast on pull requests; the cancel job is not adopted

Issue [#1817](https://github.com/Verjson/.github/issues/1817) asked for a stop-on-first-red
build. The ARB (two Codex models plus the author) decided each of its four items.

- **Cancel-on-failure job: not adopted.** A reusable's `github.run_id` is the caller's
  run, so "cancel its own run" cancels every sibling lane and unrelated job in the caller
  workflow, including required contexts that then read `cancelled` (assert-mergeable-head
  Gate C) and jobs that spend a per-head AI review pass. The job also cannot save anything
  inside a call: `needs: [every expensive job]` runs only after they all finish, and
  `node-ci`, `helm-ci` and `ui-ci` have one expensive job each. Reusable job permissions
  can only narrow the caller's, so the caller job would have to grant `actions: write` to
  every adopter, and the cancelling job itself bills a hosted runner. This follows the
  2026-10-22 decision above that in-run cancellation needs a privileged controller the
  credentialless lane forbids. Ordering and levels prevent downstream work but do not stop
  independent running lanes; that remains an accepted cost.
- **Matrix `fail-fast` on pull requests only (implemented).** `pulumi-ci.yml` `preview` and
  `actions-ci.yml` `shell-test-groups` set `fail-fast: ${{ github.event_name ==
  'pull_request' }}` (the Pulumi job additionally requires `pulumi-command == 'preview'`,
  because the same job also runs `up` and `refresh`), so trunk, `schedule`, `workflow_dispatch` and `merge_group` keep the
  full failure inventory and `pull_request_target` is never fail-fast. The container
  candidate publish matrices are release paths and are unchanged.
- **Cheap gates first (implemented where a gate exists).** On a pull request,
  `shell-test-groups` and `hosted-compatibility-tests` need `docs-contracts` and
  `runner-isolation` (about 20 seconds each in recent runs) to succeed. Every other event
  still runs them after a failed gate; `!cancelled()` plus explicit `success` results
  express that, because `!cancelled()` alone would also admit a failed prerequisite. The
  `shell-tests` aggregate is unchanged and still fails when a gate fails or a heavy job is
  skipped without a documentation-only classification. Cost: roughly the gate duration is
  added to wall time before the heavy jobs start, on every event. `adr-number-collision`
  was left out because it is about 5 seconds and rarely the first failure.
- **Same-ref supersession (verified, no generator change).** The default generated caller
  already carries the documented `concurrency` block exactly once; a test pins it.

**Accounting.** `scripts/ci_runner_minutes.py` estimates rounded runner-minutes per
workflow over a date window from the jobs API (`filter=all`), counting reruns and
started-then-cancelled jobs and flagging bad timestamps. It is an estimate, not the invoice.
Compare equal windows and event mix before and after the first adopter regenerates its
caller, and report wall time and retry frequency beside the minutes. No saving is claimed
here because none has been measured.

## Amendment 2026-10-24 — constrained same-run build-once artifacts

Issue [#1692](https://github.com/Verjson/.github/issues/1692) tracks the broader
ArtifactSet/TestReceipt/PromotionReceipt chain. This amendment decides only whether
ordinary CI may build once and share that build among checks in the same workflow run.

- **Scope and fallback.** Sharing is opt-in through a typed `buildArtifact` request
  and a trusted policy row. A shared ArtifactSet is limited to one repository, run,
  and attempt. Before selection, any unsupported or unverified condition chooses the
  original per-lane plans. After selection, producer, upload, artifact-integrity, or
  required-receipt failures make the aggregate fail; they never turn a failed shared
  run into a passing per-lane run.
- **Trusted selection and caller changes.** A reusable workflow pinned by immutable
  `job.workflow_sha` loads the authoritative policy and helper from its own revision.
  Repository and organization variables, caller JSON, and caller-controlled artifacts
  cannot authorize sharing. For a pull request, preflight compares the candidate
  checkout's `.github` tree with the event's base commit. Sharing is eligible only
  when those trees match. Preflight and each direct producer or consumer authorization
  repeat this check. A pull request that changes workflows, local actions, policy, or
  other `.github` content uses per-lane builds until the change is merged. The caller
  workflow revision remains bound into artifact identity and the build profile, but
  is not a digest allowlist that callers must regenerate against.
- **Export boundary.** The producer runs in an isolated, writable workspace. After its
  process tree has stopped and been reaped, trusted host-side code copies only declared,
  allowlisted output roots into host-owned staging using descriptor-relative,
  no-follow traversal. Reject traversal, symlinks, hard links, special files, duplicate
  paths, unexpected entries, and count, depth, file-size, total-size, or archive-size
  excesses. Hash and upload the exact staged bytes. Candidate code cannot write the
  exporter, receipt store, or artifact-service token. Only top-level export roots are
  admitted; nested roots use per-lane builds because mounting a nested output over its
  ancestor could hide sibling sources, manifests, or tests.
- **Artifact identity and consumption.** A trusted supervisor derives the manifest and
  receipt from GitHub context and observed execution, not candidate-supplied JSON.
  Bind repository, ref, checked-out commit, caller and reusable-workflow revisions,
  run and attempt, producer job and lane, build command and inputs, lockfiles,
  toolchain, export-policy revision, and every path, size, mode, and digest. The
  producer uploads with `retention-days: 1`. Each consumer selects the exact artifact
  ID and digest for the same run and attempt, downloads it, verifies the bounded
  archive with no-follow extraction, and executes from read-only verified bytes in an
  isolated, credentialless sandbox. Limit each ArtifactSet to 512 MiB compressed and
  1 GiB aggregate expanded files across consumers. Export size is measured after
  sharing is selected, so an export that exceeds either limit fails closed.
- **Plan compatibility.** A canonical build-profile digest covers every modeled
  build-affecting input and environment value, including lockfiles, runner and OS
  identity, Node and package-manager names and versions, rebuild settings, build
  configuration, lifecycle hooks, and build script. Each consumer independently
  derives the profile from effective inputs. Different or unmodeled build-affecting
  inputs, service requirements, broader egress, or an incompatible plan choose
  per-lane builds before selection. Direct build tools, task runners, local executable
  paths, unsupported wrappers, and commands outside the explicit check-tool allowlist
  remain per-lane. Package-manager calls must resolve recursively to tracked package
  scripts. Nested pnpm and Yarn invocations remain per-lane because project
  configuration can select a different script shell. Workspace-selection flags,
  alternate npm user or global config paths, and tracked `.npmrc` workspace selections
  also remain per-lane. For admitted credentialless npm checks, force
  `script-shell=/bin/sh` after caller-requested environment removals. Treat newlines
  as shell command boundaries. Reject shell grouping, redirection, carriage returns, pathname expansion, and non-ASCII or unsupported control whitespace in admitted commands. Only explicitly supported read-only command forms and options are admitted; unknown flags, output and cache destinations, snapshot updates, formatter writes, linter fixes, and positional mutating subcommands use per-lane fallback. Vitest admission is limited to a bare CI-mode invocation or explicit `run`, with no extra options. Whitespace-only script plans use a static per-lane fallback. Direct TypeScript checks remain per-lane because configurable incremental build metadata may write under exported roots; positional mutators such as `mocha init` and `tap plugin add` are not admitted.
- **Typed request and policy.** The optional `buildArtifact` request names a declared
  producer level, one or more declared consumer levels, and repository-relative output
  paths. The generator reserves synthetic job IDs `artifact-mode-gate`,
  `artifact-preflight`, and `artifact-producer`, rejecting collisions. The trusted
  policy row controls repository visibility, audience approval, producer and consumer
  levels, build script, allowed and required output roots, and exact profile-file paths.
  Profile files must be tracked regular files at the caller commit; reject symlinks and
  other file types, and hash Git blob bytes rather than the candidate working tree.
  Emit the aggregate profile digest, not individual file hashes. Caller paths must be
  nonempty, normalized, slash-separated repository-relative file or directory roots
  without globs, absolute paths, empty components, or `.`/`..` components. Compare
  roots by whole path components. Invalid requests or absent policy rows choose
  per-lane builds. The adopter policy table is currently empty, so no repository is
  authorized to share artifacts.
- **Credentialless mode gate.** A generated `artifact-mode-gate` independently
  evaluates each level's original event and branch filters from GitHub context and
  requires the event-appropriate `secretless-pr` or `secretless-trusted-ref` mode,
  even when a later `needs` condition skips that level. Missing or incorrect mode is
  a red configuration error: candidate code does not run in per-lane fallback. Only
  after this mandatory gate passes may a sharing-specific eligibility miss select
  the original per-lane plans. When `buildArtifact` is declared, `Plan.secrets` must
  be exactly `NODE_AUTH_TOKEN`, used only by the dependency-acquisition job. No other
  secret mapping is accepted, and the token never reaches producer or consumer
  candidate steps. Verify credentials are absent before producer execution, before
  artifact use, and again before consumer scripts. Any credential found after sharing
  selection fails closed without retrying per-lane.
- **Eligibility DAG and results.** Trusted preflight checks out the exact caller commit
  without executing candidate code, resolves effective full and non-app script plans
  through the same classifier and normalizer as `node-ci`, and evaluates the original
  event, branch, and prerequisite predicates. It selects sharing only when the
  producer and every consumer are scheduled, their effective profiles match, and the
  unique policy build script is first in each participant plan. Removing that entry
  must leave every consumer plan nonempty; remaining lifecycle hooks must not invoke
  the shared build.

  The generated DAG has a mode gate, selection preflight, synthetic producer, original
  level jobs, and the existing aggregate. The mode gate has no level-job dependency.
  Nonparticipant jobs gain only its dependency. In a share-eligible graph, preflight
  depends on the mode gate and the external prerequisite closure needed to evaluate
  participant predicates. The producer depends on its original prerequisite closure
  and runs only after successful selection and its original predicate. In a statically
  ineligible graph, the producer has no participant-derived prerequisite edges and is
  skipped. The generator validates the complete rendered DAG for cycles. In per-lane
  fallback, original levels run their original plans and the skipped producer is
  ignored. In selected sharing, every scheduled participant consumes the same
  ArtifactSet and emits a supervisor-generated TestReceipt through caller-owned
  `needs` outputs. The aggregate verifies each receipt's own bytes and its binding to
  the exact ArtifactSet ID and digest. Missing, skipped, cancelled, or mismatched
  required evidence fails the aggregate.
- **Sandbox and network boundary.** Candidate producer and consumer steps run in the
  credentialless Bubblewrap/AppArmor profile with capabilities dropped. Verify the
  Bubblewrap binary and enforcing child profile, set `no_new_privs`, and fail closed
  if host-isolation checks fail. Service-required plans remain per-lane; artifact mode
  does not add `actions: write` or give candidate code artifact-service credentials.
  Before enabling any policy row, an outside-Verjson adopter canary must verify
  loopback and external egress behavior and that every artifact reader is authorized
  to read the build inputs. Hashing or secret-scanning outputs does not establish
  confidentiality: valid build outputs can encode private source or dependencies.
- **Validation and rollout.** Before first upload or consumption, adversarial tests must
  reject exporter races, archive traversal, artifact substitution or replay,
  incompatible profiles, missing receipts, skips, cancellation, and credential access.
  Keep sharing disabled until an outside-Verjson adopter canary is green and equal
  before-and-after runner-minute windows have been measured. No savings are claimed
  until measured.
- **Promotion remains separate.** Same-run CI artifacts and test receipts do not
  satisfy deployment or promotion evidence. Issue #1692 covers a separate protected
  candidate ArtifactSet and fail-closed TestReceipt and PromotionReceipt chain bound
  to source repository, ref, commit, immutable caller and contract revisions, run and
  attempt, final image digests, provenance, and SBOMs. Promotion uses
  environment-specific credentials and explicit approvals only after verification.
  Scoped read-only credentials used to acquire dependencies stay outside the candidate
  sandbox and never reach candidate code. For Next.js, `NEXT_PUBLIC_*` values are
  compiled at build time, so a cross-environment image also requires runtime
  configuration or separate environment-specific artifacts
  ([official Next.js guidance](https://nextjs.org/docs/pages/guides/environment-variables#bundling-environment-variables-for-the-browser)).

## 2026-10-25 amendment — Level-local fail-fast and trusted entry receipts

- **Issue:** [Verjson/.github#1869](https://github.com/Verjson/.github/issues/1869); this implementation is part of the issue.

### Decision

A typed `test-levels` entry may declare strict boolean `failFast`; omission defaults to `true`. The immutable parsed level carries this value through generated callers, authorization, and both `node-ci.yml` executors. It controls the ordered scripts within a level and does not change GitHub matrix `fail-fast`.

Entries run sequentially in the existing shared workspace. With `failFast: true`, an ordinary candidate nonzero exit prevents later launches and records the remaining entries as `not-run-fail-fast`. With `false`, an ordinary nonzero exit is recorded and later entries run for diagnostics, while the failure remains latched and the level ultimately fails. Later outputs do not attest that earlier scripts passed because entries can change shared workspace state. The workflow does not use `continue-on-error`.

A trusted host supervisor writes versioned JSON receipts for every planned entry and for the plan beneath a fresh private directory in host `RUNNER_TEMP`, outside candidate mounts. Receipts bind repository, event, ref, admitted candidate commit, workflow SHA, run and attempt, job and level, complete ordered plan digest, ordinal, and entry identity. Candidate outcome, host cancellation, supervisor signal, and cleanup are recorded separately. Exclusive no-follow creation and atomic finalization prevent candidate processes from spoofing receipts. Hard kill or runner loss may prevent receipt completion; missing evidence fails the level.

### Trusted execution and cancellation

Both executors run a trusted helper as Bubblewrap PID 1 using `--as-pid-1`. The wrapper verifies PID 1 and successfully sets and reads back `PR_SET_DUMPABLE=0` before starting a candidate. It launches the candidate with `Popen(close_fds=True)` and exclusively waits for that direct child. Candidate output is kept off the wrapper's trusted result stream. The non-dumpable PID 1 boundary prevents the candidate from accessing its file descriptors through `/proc/1/fd` or tracing it.

Bubblewrap's separate `--json-status-fd` stream must contain one ordered `child-pid` event and one matching `exit-code` event. Strict JSON parsing rejects duplicate keys, non-finite values, malformed records, and contradictory or duplicate known events; valid unknown event objects and extra fields are ignored. PID 1 sends bounded TERM and KILL to remaining namespace processes, reaps adopted children, and reports cleanup complete only when no non-init process remains. Cleanup failure is terminal.

The host and PID 1 install SIGINT and SIGTERM handlers while those signals are blocked, then explicitly unblock them; candidates inherit an unblocked mask. Handlers enqueue signals, and every dequeued event passes through one latch helper. The first dequeued signal stays latched even after the queue empties. Candidate self-signals remain candidate outcomes and cannot set host-cancellation or supervisor-signal fields.

Before readiness, PID 1 verifies its PID namespace and enforces the AppArmor profile, zero process capabilities, and `no_new_privs`; it also prepares the candidate environment. The host pins the reported supervisor with a pidfd, sends one launch or abort frame, closes the control pipe to establish EOF, and requires a matching acknowledgement. A final host-signal poll immediately before `Popen` is the launch commit: cancellation during security validation or after ACK but before that poll produces `candidate-not-started`; a signal after the commit is forwarded to the live candidate. If pidfd signaling reports `ESRCH`, the supervisor has exited; the host does not claim forwarding and continues draining status while preserving observed outcome and cleanup evidence.

### Receipt and acceptance rules

The host stages the plan receipt privately, validates one-to-one entry coverage, takes a final host-signal queue cutoff, and publishes acceptance atomically. If cancellation wins before that boundary, the plan is interrupted even when completed entry receipts remain factual, so receipts cannot imply job success. Host interruption, including cancellation before the first entry, marks the remaining suffix `not-run-aborted`, regardless of `failFast`. At most one `interrupted` entry is allowed; it requires no prior terminal receipt and begins the terminal suffix, which may contain only `not-run-aborted` entries. An interrupted plan rejects candidate-terminal and infrastructure-failure entries, preserving infrastructure-failure precedence. `not-run-fail-fast` is reserved for entries skipped after an ordinary nonzero candidate exit with `failFast: true`; if cancellation wins final acceptance, that skipped suffix is reclassified before the interrupted plan receipt is published.

If candidate cleanup fails after a candidate ran, the infrastructure receipt retains `candidateStarted`, its factual return code or signal, the supervisor signal, and failed cleanup. Prelaunch cancellation followed by cleanup failure retains `candidateStarted: false`, no candidate outcome, and host/supervisor provenance. Cleanup-verification exceptions record `cleanup=unverified` and preserve the known candidate outcome and exception cause. An infrastructure plan carries `hostCancellationSignal` only when a matching infrastructure-failure entry with cleanup `failed` or `unverified` carries the same signal; a signal first observed at final cutoff cannot add plan provenance. Candidate `signal` records only candidate-originated signals, and `supervisorSignal` must match host provenance.

Cancellation, timeout, launch, sandbox or boundary failure, cleanup failure, receipt failure, and unexpected supervisor errors are terminal and prevent later launches. Ordinary candidate failures alone may continue with `failFast: false`. Existing credentials, permissions, egress, service, cache, workspace, and matrix behavior remains unchanged. The `false` path stays fail-closed until protected-twin parity, registered tests, independent receipt validation, and GitHub-hosted Bubblewrap acceptance pass. Local source and protected tests do not establish the hosted acceptance gate.

## Amendment 2026-10-26 — constrained Postgres 17.11 sandbox profile

- **Issue:** [Verjson/.github#1869](https://github.com/Verjson/.github/issues/1869), D2 sandbox profiles.
- **ARB:** Conditional GO for the first PostgreSQL profile; this amendment records that narrow choice without changing the separate build-once decision in item 5.

The `postgres-17.11` profile is explicit and default-off. It resolves only the reviewed amd64 manifest `docker.io/library/postgres@sha256:3518071376d0b6156b02e1db2747a78511b5b9dc3763cc79c506ebe2dc2ee71c`. It rejects other profile IDs, changed digests, non-amd64 runners, legacy service images, `producer`/`consumer` ArtifactSet roles, and non-secretless execution. The eligibility guard rejects compatibility requests and producer/consumer roles before credentialed dependency acquisition; the ordinary `level` build-artifact role remains permitted and does not enable ArtifactSet sharing. Trusted setup starts one database container per run with Docker network mode `none`, no published TCP port, temporary data storage, and a per-run Unix socket. A dedicated non-superuser can connect to `app_test` and create objects in `public`, but cannot create databases or roles, use server files or programs, or administer the server.

The provisioner verifies that `psql` and `pg_dump` are 17.11 from the same pinned image, stages only the clients and required libraries, and records their hash manifest before candidate execution. Bubblewrap receives the socket and bundle through read-only mounts. The Postgres profile always retains `--unshare-net`, including entries that request legacy services; it does not mount the Docker socket or expose a privileged command broker. Runtime checks bind the container image/network/port boundary, socket inode/mode, and client manifest before and after each candidate. Candidate-launch failures, including timeout or cancellation, boundary failures, and candidate-process cleanup failures, cannot produce a successful entry receipt. The later profile teardown runs after candidate receipts may already have been written; a teardown failure must fail the workflow job, but may leave those factual candidate entry receipts in place. The profile remains incompatible with ArtifactSet producer/consumer roles, and `config/ci-build-artifact-policy.json` remains empty. No sandbox egress, service, or artifact-sharing exception is authorized for the Postgres profile. A selected Postgres profile also rejects any non-empty direct or protected-baseline-resolved `secretless-compatibility-ranges` request before candidate launch.

The GitHub-hosted acceptance fixture checks the real Bubblewrap/AppArmor path, Drizzle Kit schema push, migration routines, normalized `pg_dump --schema-only` comparison between two fresh databases, network/metadata/Docker denial, database-mediated egress denial, least privilege, boundary failure receipts, and timeout/cancellation cleanup. Local Docker and Bubblewrap runs are useful development evidence only; they do not establish hosted AppArmor acceptance. The remaining general system-package and Playwright profiles stay deferred pending their own constrained design and hosted evidence.

The external canary's `scripts/generate-schema-sql.mjs` still uses the legacy `postgres:17.6` tag. That is adopter drift to update at the canary; it does not change the canonical profile's reviewed 17.11 digest.

## Amendment 2026-10-26 — isolated schema-validation PostgreSQL profile

- **Issue:** [Verjson/.github#1817](https://github.com/Verjson/.github/issues/1817), canonical CI fail-fast and sandbox profiles.
- **Decision:** Keep generic `postgres-17.11` unchanged and add the default-off `postgres-schema-validation-17.11` profile for the adopter's schema-validation contract. It uses the same reviewed amd64 image digest, container network isolation, Unix socket, and same-image client bundle.

The schema-validation profile creates only `schema_snapshot`, `schema_migrations`, and `schema_reference`. It exposes only the fixed `DATABASE_URL_SCHEMA_SNAPSHOT`, `DATABASE_URL_SCHEMA_MIGRATIONS`, and `DATABASE_URL_SCHEMA_REFERENCE` socket URLs, alongside `PGHOST=/run/verjson-postgres`, `PGPORT=5432`, and `PGUSER=verjson_ci`. The database owner remains `postgres`; the candidate role has no superuser, database-creation, or role-creation capability. Trusted setup revokes the default `PUBLIC` database privileges, grants the candidate `CONNECT` to all three databases and `USAGE, CREATE` on each `public` schema, and grants `TEMP` only on `schema_migrations`. That database alone receives the fixed verifier prelude (`authenticated`, `auth`, and `storage` objects); the migration role owns those fixed objects so it can apply the existing policies without a database-level `CREATE` grant. No compatibility, producer/consumer artifact-sharing, service, TCP, egress, Docker-socket, or privileged-broker exception is introduced.

Schema generation and reference push use `schema_snapshot` and `schema_reference`; migration replay uses `schema_migrations`. The hosted acceptance compares normalized outputs across these three independent clean databases, checks their privilege and prelude boundaries, and exercises the Bubblewrap/AppArmor runner. Candidate scripts must call `/opt/verjson/postgres-client/bin/psql` and `/opt/verjson/postgres-client/bin/pg_dump` by absolute path: npm may put a workspace `.bin` directory ahead of inherited `PATH`. The hosted acceptance places failing workspace shims first on `PATH` and verifies that the pinned absolute clients still run. Local Docker/Bubblewrap tests do not establish GitHub-hosted AppArmor acceptance.

## Amendment 2026-10-26 — harden same-run ArtifactSet parsing and selection

- **Issue:** [Verjson/.github#1869](https://github.com/Verjson/.github/issues/1869), D1 hardening.
- **ARB:** Conditional GO from Astra, Sol, and `code-reviewer` for hardening only. Fable and Opus Agent review tools were unavailable. Enrollment remains NO-GO.

### Decision

The canonical ArtifactSet reader now rejects direct GNU sparse members and rejects every `GNU.sparse.*` key and PAX `size` override in local, global, and Solaris extended headers before delegating to Python's tar parser. It inspects the full padded extension block, requires valid record framing and zero padding, and replays the bounded bytes to the standard parser exactly once. It also rejects a parsed member with sparse metadata before either manifest or payload extraction. Ordinary bounded PAX path headers remain supported.

Extension accounting is owned by the archive and initialized before its first header. Each extension payload is limited to 16 KiB; at most 513 extension headers, 8,667,648 cumulative padded header and data bytes, and 16 consecutive extension headers are accepted. The count and byte totals persist across ordinary members. Zero-length extensions count, and chain depth resets after a normal member. Quota checks happen before extension payload reads or parser delegation; the parser does not depend on catching recursion failure.

Caller routing uses the raw preflight selection as a case-exact JSON boolean. Only `false` from a successful preflight permits the original per-lane plan. A consumer plan requires exact `true`, attempt 1, a successful producer, and exact `true` share availability. Missing or malformed values authorize neither route. A retry uses per-lane execution only when successful preflight explicitly returns `false`; the attempt number alone does not select fallback. The always-run aggregate verifies its raw selection booleans and producer, upload, artifact, and receipt evidence even when an earlier level fails. Consumer TestReceipts are emitted only after artifact verification and candidate script-plan execution both succeed, in standard and protected workflows.

These controls do not enroll an adopter. `config/ci-build-artifact-policy.json` remains integer version `1` with an empty `repositories` object. Enrollment first requires runtime enforcement over the actual executing caller graph, including immutable pins, roles, prerequisites, outputs, and aggregate requirements. Only after that enforcement lands may a candidate immutable canonical revision carry one unmerged canary-only policy row and a generated external caller pinned to that revision. The outside-Verjson canary must pass before that row is merged. No broader sandbox egress or artifact-consumption exception is authorized here.
