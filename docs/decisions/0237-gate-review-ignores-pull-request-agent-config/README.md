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
job with its credentials. A scratch reproduction with Claude Code 2.1.302, no real
credentials, and an unreachable API endpoint confirmed it: a fixture project hook and
a fixture project MCP server both executed under the action's default source list,
and neither executed under `--setting-sources user`.

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

`scripts/ci-gate/review-agent-config-isolation.test.py` enforces decisions 1–3: it
reads `claude_args` the way the pinned action's parser does, executes the removal
step against fixture workspaces (including planted links and an unremovable path),
and evaluates both step conditions under Actions' implicit `success()` across every
lane, provider, and removal outcome.

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
- **Residual, accepted:** the user setting source still loads `~/.claude` from the
  runner. On an ephemeral runner that is the action's own output. On the persistent
  private-repository lane an earlier job on the same host could have written it,
  which is the cross-job persistence class ADR 0235 already accepts for private
  repositories.
