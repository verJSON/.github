# 0237 — The gate review never loads agent configuration from the pull request

- **Date:** 2026-10-18
- **Status:** Accepted
- **Category:** merge-gate behavior and secrets handling — **sensitive class**
- **Related:** [ADR 0098](../0098-require-bounded-ai-review-for-code/README.md),
  which requires the bounded AI review this decision hardens;
  [ADR 0227](../0227-arm-bootstraps-stale-review-callers/README.md), whose
  `MINIMUM_SAFE_REVIEW` floor governs when adopters must regenerate;
  [ADR 0235](../0235-public-pull-request-code-uses-ephemeral-runners/README.md),
  which keeps public pull-request heads on ephemeral runners

## Context

The `gate` job in `ai-review-merge.yml` checks out the pull-request head into the
workspace root and runs `anthropics/claude-code-action` at the pinned commit
`c81e3bc69d1b18badbb63ba39581218f02421678` over it. That step holds
`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, and the job token, and the job has
already minted the review-reservation App token from the `ai-review-app`
environment.

Reading the action at that commit (untrusted source, read only):

- `base-action/src/parse-sdk-options.ts:338-344` sets the SDK `settingSources` to
  `["user", "project", "local"]` unless `claude_args` carries `--setting-sources`.
- `src/entrypoints/run.ts:254-276` restores project configuration from the base
  branch only when `isEntityContext(context) && context.isPR`. `src/github/context.ts:55-69`
  lists `workflow_dispatch` as an automation event, not an entity event. The gate
  runs under `workflow_dispatch` from the authorization arm, and an adopter's
  generated caller is itself `workflow_dispatch`, so the restore never runs here.
- `src/github/operations/restore-config.ts:232-242` states the reason the restore
  exists: in non-interactive mode the CLI reads `.mcp.json`, `.claude/settings.json`,
  and `.claude/settings.local.json` from the working directory and acts on them
  before any tool-permission gating — hooks, environment variables, credential
  helper commands, and MCP servers. `--allowedTools` does not restrict any of these.
- `base-action/src/setup-claude-code-settings.ts:62-63` always writes
  `enableAllProjectMcpServers: true` into the user settings it prepares.

So any pull-request head that reached this step could run commands inside the gate
job with its credentials. A scratch reproduction confirmed it with Claude Code 2.1.241
(the version `src/entrypoints/run.ts:80` installs) and 2.1.302, using no real
credentials and an unreachable API endpoint. Under the action's default source list a
fixture project hook, a fixture project MCP server, a runner-home hook, and a
runner-home MCP server all executed. Under `--setting-sources user` alone the
runner-home hook still executed, and without `--strict-mcp-config` so did the
runner-home MCP server. With both flags and a fresh `CLAUDE_CONFIG_DIR`, nothing did.

Three neighboring trust gaps in the same job came out of the review:

- The pull-request head checkout persisted the job token, and in agent mode the
  action's `configureGitAuth` (`src/github/operations/git-config.ts:22-48,128-135`)
  rewrites `origin` to a URL embedding its token. Both land in git configuration the
  review's `Read` tool can open. That rewrite runs inside a `try`/`catch` that
  continues on failure (`src/modes/agent/index.ts:57-65`).
- Review diffs read git attributes for the reviewed paths from the checkout, so a pull
  request could mark a changed text file `-diff`, directly or through a macro, and the
  model would see only `Binary files … differ`.
- `--setting-sources user` reads the runner's `~/.claude` and `~/.claude.json`, which
  an earlier job on a persistent runner could have written.

Reachability: `gate-rearm.yml` terminalizes the authorization check and dispatches
nothing when the head repository owner differs from the repository owner, so a fork
head does not reach the step. A same-repository head does, which means anyone able
to push a branch — a write collaborator, or an automation identity with branch-write
access — could reach the gate's environment secrets and model credentials, which
that write access alone does not grant. The exposure is pre-existing on `main`
`9cbb81cd` and in every adopter caller pinned to an earlier contract.

## Decision

1. **The Claude review loads only the runner's user setting source.** Its
   `claude_args` carry `--setting-sources user` exactly once, so no project or local
   configuration from the checkout is read, and `--strict-mcp-config`, so only MCP
   servers the action passes through `--mcp-config` can start.
2. **Pull-request agent configuration is removed before anything else runs.** A step
   immediately after `Checkout PR head`, under that checkout's condition, deletes the
   root-level paths the pinned action itself treats as sensitive
   (`restore-config.ts:26-35`): `.claude`, `.claude.json`, `.mcp.json`, `CLAUDE.md`,
   `CLAUDE.local.md`, `.gitmodules`, `.ripgreprc`, and `.husky`. A path that cannot
   be removed, or a missing workspace, fails the step. The step is not
   `continue-on-error` and the Claude step's condition calls no status function, so
   a failed removal fails the job before the review can start. A change that adds
   either must make the Claude step require `steps.pr-agent-config.outcome == 'success'`.
   Removal is defense in depth for decision 1: either control alone stops the
   reproduced execution, and a regression in one leaves the other.
3. **A symlink is unlinked, not refused.** Unlike the fixed-path checkout refusal in
   #1849, these paths are pull-request content, and a repository may legitimately
   link `CLAUDE.md` to `AGENTS.md`. Refusing would block every review in such a
   repository. `rm -rf -- <name>` removes the link itself and never follows it.
4. **The review evidence is unaffected.** The model still sees every changed line in
   `.ai-review/pr.diff`, and `review-verdict.py` resolves cited evidence from git
   objects at the reviewed commit, not the working tree.
5. **The OpenAI and DeepSeek passes need no change.** They run the trusted,
   standard-library-only `openai-review.py` and `deepseek-review.py` under
   `python3 -I` from `RUNNER_TEMP`. Neither starts a CLI or reads repository-local
   configuration. No other workflow or generator in this repository runs
   `claude-code-action` or a model CLI over pull-request content.

6. **The review checkout holds no credential.** `Checkout PR head` sets
   `persist-credentials: false`. The two later steps that fetch base history,
   `rereview` and `prep`, receive the job token through step-scoped `GIT_CONFIG_*`
   environment: the first entry resets any runner-configured credential helpers, and
   the second adds a helper for `github.server_url` that answers only `get` from the
   step's `GH_TOKEN` and stores nothing. A step immediately before the Claude review,
   under its exact condition, removes the `origin` remote, after the last fetch, so the
   action's token rewrite fails and is skipped.
7. **Review diffs ignore pull-request attributes.** A step right after the head
   removal writes `* !diff` to `$GIT_DIR/info/attributes`, which outranks every
   in-tree `.gitattributes`. It replaces a planted link there rather than writing
   through it. Both diff commands also pass `--no-ext-diff --no-textconv`, so runner
   configuration cannot substitute their output. Genuinely binary content still
   renders as a binary marker, which keeps the bounded diff valid UTF-8 for the
   tool-free providers. `--text` was rejected for that reason.
   The rereview patch-id needed no content-binding change: for a binary path git
   includes the blob ids, so a swapped binary already changes it. A pull request
   whose files were attribute-marked gets a new patch-id once, which forces one fresh
   review rather than a skipped one. `review-verdict.py` reads evidence with
   `git cat-file blob`, which applies no attributes.
8. **The review's user layer is fresh per run.** The Claude step sets
   `CLAUDE_CONFIG_DIR` to `$RUNNER_TEMP/claude-config-<run id>-<run attempt>`. The
   isolation step creates it with `mkdir -m 0700` and fails if anything, including a
   link, already exists there. The action's own `setupClaudeCodeSettings` still
   writes `~/.claude/settings.json`, which is now unread. The gate passes no
   `settings`, `plugins`, or `plugin_marketplaces` input, so the only lost setting
   is the project MCP pre-approval this decision does not want. A future change that
   needs one of those inputs must write it into the fresh directory instead.

`scripts/ci-gate/review-agent-config-isolation.test.py` enforces decisions 1–3 and
6–8, and every deliberate removal of a protection turns it red:

- It reads `claude_args` the way the pinned action's parser does.
- It executes the removal, isolation, attribute, `rereview`, and `prep` steps against
  fixture repositories with a stubbed `gh`, a planted storing credential helper, a
  substituting external diff, planted links, an unremovable path, and a pre-existing
  config directory.
- It evaluates every protection-to-consumer step pair under Actions' implicit
  `success()` across every lane, provider, and failure outcome.

## Rollout

- **This repository:** effective on merge. The arm dispatches the default-branch
  `ai-review-merge.yml`.
- **Adopters:** each generated caller runs the reusable at its pinned contract SHA,
  so the fix reaches a repository only when its caller is regenerated at or after the
  merge commit. Forcing that requires raising `MINIMUM_SAFE_REVIEW` in
  `gate-rearm.yml` to the merge commit (ADR 0227), which needs the merge SHA and is a
  follow-up change, and then rotating organization ruleset 20722935 to that
  follow-up's merge commit. The rotation is an owner action; this decision changes
  no live ruleset, setting, secret, or variable.

## Consequences

- A same-repository pull request can no longer turn its own agent configuration
  into code execution inside the gate job.
- The reviewer cannot open the pull request's `.claude/` files or root `CLAUDE.md`
  at head; it reviews them from the diff.
- The review model's `Read` tool no longer finds the job token in the checkout's git
  configuration, and attribute-marked text changes reach the reviewer as text hunks.
- On a persistent runner, a later job that reuses this workspace's `.git` keeps the
  `* !diff` override, because `actions/checkout` leaves `info/` alone. Its only effect
  is that `git diff` there renders attribute-marked files as text. A missing `origin`
  makes the next `actions/checkout` re-clone instead of reusing the directory.
- **Residual, inherent to the action:** the CLI process environment still carries the
  model credential and the action's GitHub token, because the CLI needs the first and
  the action exports the second before starting it. The verdict schema,
  `review-verdict.py`, and the short token lifetime are the remaining bounds on a
  prompt-injected attempt to echo either.
- **Residual, accepted:** managed policy settings under `/etc/claude-code` are always
  loaded. They are root-owned runner state, the same persistent-host class ADR 0235
  accepts for private repositories.
