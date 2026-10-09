# 0158 — Container releases reconcile derived inputs before minting the release credential

- **Date:** 2026-09-01
- **Issue:** [#1203](https://github.com/Verjson/.github/issues/1203)
- **Blocks:** [Verjson/verjson-github-runner#195](https://github.com/Verjson/verjson-github-runner/issues/195)
- **Category:** release authority, production credentials, and pre-credential code execution — **sensitive class**
- **Status:** Accepted
- **Extends:** [ADR 0078](../0078-container-release-and-runner-deployment-contract/README.md)

## Context

The canonical reusable `container-release.yml` copies the provenance-verified immutable
release manifest into `RELEASES/containers/` and commits only that manifest, the generated
changelog snapshot, and the tag. A repository whose *derived* build inputs must be
reconciled to the just-published immutable base digest has nowhere to do it inside that
atomic release commit.

`Verjson/verjson-github-runner#195` is the concrete failure: v0.2.1 was released while every
derived Dockerfile default remained pinned to v0.2.0, leaving `main` red immediately after a
green release. The obvious remedies are both wrong. Reconciling in a follow-up commit means
the tag names a tree that never built. Letting a generated consumer hand-edit the pinned
workflow destroys the byte-identity contract that makes the release path reviewable at all.

So the hook has to exist, and it has to run *inside* the release job. That is precisely what
makes it dangerous: the only useful position for it is after the manifest is proven and
before the release commit, which is also one step before the job mints a GitHub App token
carrying `contents: write` on the release repository — a credential that can push to
`main` past branch protection.

### Threat model

An under-constrained hook in that position could:

1. **Exfiltrate or misuse the release credential** — by reading it out of the ambient
   environment, or by leaving a background process alive to observe the token minted in the
   following step.
2. **Corrupt the release commit** — by writing files the reviewers of the release never saw,
   which the subsequent `git commit` would then tag and publish under a verified manifest.
3. **Escalate into the pinned engine** — the release step executes
   `.container-release-contract/scripts/changelog.py` *with* the App token. That checkout is
   on disk, untracked from the consumer repository's point of view, when the hook runs.
4. **Bypass the file allowlist** — through symlinks, deletion-and-recreate, file-mode or
   type changes, untracked output, or index manipulation, all of which a naive
   `git diff --name-only` check accepts.
5. **Be widened at release time** — if the allowlist or command were a `workflow_dispatch`
   input, anyone who can dispatch a release could choose what code runs pre-credential.
6. **Write to `.git/` instead of the worktree.** `git status` never reports `.git/`, so a
   worktree-diff validator is blind to it — and `.git/` is precisely where the answer to
   "what code does the next `git` command run" lives: `hooks/pre-commit`,
   `core.hooksPath`, `core.fsmonitor`, `credential.helper`, content filters and aliases in
   `config`, and `info/exclude` to hide the hook's own output. The equivalent write to
   `$HOME/.gitconfig` reaches the same switches from outside the repository. The adversarial
   review found this class live: a hook that dropped an executable `.git/hooks/pre-commit`
   passed validation with exit 0, and the `git commit` two steps later would have executed
   it with the App token in its environment.

Ignored paths are the same blind spot one level down: `git status` omits them, so a
consumer `.gitignore` entry would let a hook write output the validator never saw —
including under `NEXT/`, which the pinned engine consumes after the token is minted.

## Decision

Add one **opt-in, generator-declared, fail-closed** reconciliation step to the reusable
workflow, enforced entirely by immutable contract code.

**Generation time, not run time.** `scripts/gen-container-release.sh` gains
`--reconcile-allow <path>` (repeatable, `workflow` and `contract-test` kinds only). It bakes
a literal `reconcile-allowlist: '["…"]'` into the generated caller's `with:` block. The
caller's `workflow_dispatch` inputs remain exactly `version` and `candidate-manifest`, and
the generated caller is byte-pinned by the generated contract test — so the allowlist is
reviewed in a PR and cannot be widened by a dispatcher. This mirrors how `--build-runner`
and `--release-asset` are threaded through `gen-changelog-caller.sh`.

**Exact allowlisted command.** The hook is always `scripts/release-reconcile.sh` in the
consumer repository — a constant of the contract, not a configurable path. It must be
tracked, a regular file (not a symlink), and executable. There is no shell fragment,
command string, or interpreter selection anywhere in the input surface.

**Position.** The step runs after `Fail-closed preflight and immutable plan` (so
`release-manifest.json` exists and is provenance-verified), after the pinned engine
checkout, and before `Require the release App client ID` / `Mint exact-repository release
App token`. `scripts/container-release-reconcile-hook.test.py` asserts all four orderings
against the real workflow and rejects mutants that move the step.

**Enforcement lives in the contract, not the consumer.** The validator is
`scripts/container_release_reconcile.py`, executed from `.container-release-contract/` at
the pinned contract SHA. It is deliberately *not* a generated consumer-side artifact: a
consumer-side copy of the thing that constrains the consumer is not a constraint. The
existing pinned checkout is widened by exactly one file to carry it.

**Bounded inputs.** The step's `env:` is exactly `VERSION`, `CONTRACT_REF` and
`RECONCILE_ALLOWLIST`. The validator then builds the hook's environment *from scratch* —
`PATH`, `HOME`, `LANG`, `RELEASE_VERSION`, `RELEASE_MANIFEST` — rather than filtering a copy
of the runner environment. An allowlist is used instead of the denylist pattern used by
`release-build.sh` because a denylist cannot keep up with newly introduced credential
variables, and the blast radius here is the release App token. `HOME` is a throwaway
directory, not the runner's: a writable home is a `~/.gitconfig` away from `core.hooksPath`,
and git reads `$HOME/.config/git/config` too.

**The Git control surface is compared directly, not through `git`.** Before the hook, the
validator fingerprints `config`, `config.worktree`, `info/exclude`, every file under
`hooks/` (content *and* executable bit), and `HEAD` — for both the release checkout and the
pinned contract checkout, resolving each `--absolute-git-dir` from the trusted pre-hook
state. After each hook run that fingerprint must be unchanged, and it is checked *first*,
before any other validation, because `.git/config` is exactly what would make later `git`
output lie. Defence in depth in the release step itself: every `git` invocation that runs
after the hook — the workflow-authored `commit` and `push`, and `scripts/changelog.py`'s own
internal `commit`/`tag` during `release()` — passes `-c core.hooksPath=/dev/null`, enforced
once in `changelog.py`'s shared `git()` helper rather than per call site, since `.git/hooks`
is untracked and therefore was never reviewed by anyone. An initial version of this defence
covered only the two workflow-authored commands and missed `changelog.py`'s own commit,
which still ran with the release token in its environment and no hooks guard — closed before
merge; `scripts/changelog.test.py`'s
`test_release_commit_never_executes_a_repository_pre_commit_hook` plants a real
`.git/hooks/pre-commit` and asserts it never runs during `release()`.

**Fail-closed validation.** Before the hook: the allowlist is structurally validated
(normalized repository-relative paths, no `..`/`.`/`.git*` segments, no `RELEASES/`,
`CHANGELOG/`, `NEXT/` or release-engine script, unique, ≤ 32 entries, each already a tracked
regular file — compared against the name `git ls-files` actually lists, so a directory
cannot pass as a file); the manifest path and the staged-list path are bounded, and the
staged-list path must not be tracked; the pinned checkout is confirmed to be at the contract
ref and unmodified; the tracked tree is confirmed clean. After the hook: every
`git status --porcelain=v1 -z -uall` record must be either untracked output that already
existed before the hook, or an unstaged content modification (` M`) of an allowlisted path,
and `--ignored=matching` must yield no new ignored path either.
Deletions, renames, type changes, index staging, and any new untracked path are rejected.
`git diff --raw` then requires the source and destination modes to be equal and to be a
regular blob, which rejects mode flips and symlink substitution, and the filesystem is
re-checked for symlinks. On any rejection — non-zero exit, signal, timeout, or failed
validation — the index is reset, tracked files are restored, hook-created untracked files
are removed, and the job exits non-zero without ever reaching the mint step.

**Idempotence is enforced, not assumed.** Releases are retried. The hook is run a second
time against its own output and the reconciled blob hashes must be unchanged. (Blob hashes,
not `git diff --raw`: for unstaged worktree changes git reports an all-zero destination
hash, so `--raw` cannot distinguish two different reconciliations of the same path — a
defect found by the idempotence test itself.)

**Containment after the hook.** The hook runs in its own process group with `stdin` closed
and a 300-second timeout; the group is `SIGKILL`ed on completion, which reaches anything it
backgrounds in the ordinary case. This is not airtight: a child that calls `setsid()` a
second time detaches from the group entirely and survives the `killpg`, giving it a window
before the token is minted. The control-surface fingerprint (above) and the universal
`core.hooksPath=/dev/null` are the actual backstops against what such a survivor could do —
plant a hook or rewrite `.git/config` — not the process-group kill by itself; this is a
residual risk to keep in mind rather than a closed one. The validator re-verifies the pinned
checkout afterwards, and the release step re-binds it (`rev-parse HEAD` equals
`contract-ref`, `scripts/changelog.py` unmodified) immediately before executing it with the
token.

**Exactly one commit.** The validator stages only the validated paths. The release step then
asserts that the staged set is exactly the release manifest plus the reconciled paths it
recorded, so reconciled files ride in the same commit and tag as the manifest and changelog,
and nothing else can.

**Opt-in.** With no `--reconcile-allow`, the generated caller is byte-identical to today and
the step is skipped by `if: ${{ inputs.reconcile-allowlist != '' }}`. The generated contract
test additionally asserts the negative — no `reconcile-allowlist` in the caller and no
undeclared `scripts/release-reconcile.sh` — so a hook cannot appear without review.

## Consequences

- Consumers such as `verjson-github-runner` can pin derived build inputs to the released
  digest inside the release commit, closing the class of failure in #195.
- The reusable workflow now executes consumer-authored code before credential minting. It
  already ran consumer-side `container_release_*.py` at that point, so this is a widening of
  an existing surface rather than a new one — but it is a deliberate widening, constrained
  by an org-owned enforcer and covered by adversarial tests
  (`scripts/container_release_reconcile.test.py`) that attempt each bypass in the threat
  model above and require rejection.
- Reconciliation doubles the hook's execution time by design (the idempotence fixed-point
  check). Hooks are expected to be small deterministic rewrites; a hook that cannot tolerate
  running twice is not safe to run in a retryable release.
- The pinned engine checkout is widened from one file to two. It remains sparse,
  cone-mode-off, and credential-free, and `container_release_changelog_engine.test.py` still
  asserts it exactly.
- Adopters must regenerate the whole artifact set at one contract SHA to pick this up; the
  contract test's negative assertions are new bytes even for unconfigured adopters.
- The adversarial review that preceded merge found the `.git/`-write class (threat 6) live
  and exploitable in the first implementation, which is the strongest argument for the
  position taken here: a worktree diff is not a sufficient model of "what did the hook
  change" when the next step mints a credential. Eight regression tests hold that line
  (`scripts/container_release_reconcile.test.py`, classes `GitControlSurfaceTest` and
  `AllowlistShapeTest`).
- **Residual risk, stated plainly:** a malicious hook can still consume runner CPU and read
  the repository tree. Validation covers the worktree, the index, ignored paths and the Git
  control surface, but it is still filesystem-state-based: it does not model a hook that
  attacks the runner itself (`/etc/gitconfig` needs root, so it is out of reach, but the
  general class is not disproven). The mitigation is that the hook, its allowlist, and the
  pinned contract SHA are all reviewed in the consumer's own PR before any release can use
  them.

## Amendment — 2026-10-09 (#1726): preserve pre-existing release inputs

The reconciler fingerprints the contents and modes of every pre-existing untracked or ignored path before running the hook, then rejects deletion or any change to those paths. This includes the generated release manifest, candidate artifact ZIP, and verification receipts that are not tracked in the consumer checkout. The pinned contract checkout is separately checked for its exact commit, clean worktree, and Git control surfaces; its `.git` metadata is excluded from the filesystem snapshot.
