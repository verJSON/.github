# 0237 — The gate review never loads agent configuration from the pull request

- **Date:** 2026-10-18
- **Status:** Accepted
- **Amended:** 2026-10-19 — decision 10 refuses OpenAI and DeepSeek key shapes (#1854)
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

Reviews of this change found further trust gaps in the same job:

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
- The gate wrote its review inputs (`.ai-review/`) and telemetry (`.telemetry/`) into
  the pull-request working tree with plain redirections, which follow a link the head
  commits at those paths. A planted link could leave the provider input empty while
  `prep` still reported it available, or overwrite a file the gate relies on.
- The bare `Read`, `Grep`, and `Glob` allow rules let the review read any file the
  runner user can. That includes the review process's own environment, which holds the
  model credential: the CLI refuses a direct read of that file, but not one reached
  through a link the pull request commits. The validated verdict is posted to the pull
  request without redaction.

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
   `CLAUDE.local.md`, `.gitmodules`, `.ripgreprc`, and `.husky`, plus the gate's former
   in-tree review directory `.ai-review`, so it cannot hold a decoy. A path that cannot
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
4. **No gate step writes into the pull-request working tree.** `prep` creates the
   review directory with `mktemp -d` under `RUNNER_TEMP` (mode 0700) and publishes it
   as the `review_dir` output only after it succeeds. Every provider, the prompt, and
   the Claude review's `--add-dir` name it by absolute path. The telemetry payload is
   written the same way. After the head checkout, the gate writes inside the workspace
   only in three places, none of which a review input passes through: git's own
   metadata (base-history fetches and decision 7's override), and the telemetry
   helpers' sparse checkout into `.gate-tools`, which ADR 0236's refusal guards and
   which runs after the review. A link the head commits therefore cannot redirect,
   replace, or empty a review input.
   Generated lockfiles are elided with git pathspec excludes, never by parsing diff
   headers, so no path spelling (spaces, quotes, newlines) can move a file in or out
   of the review. The elided list comes from `--raw -z`, whose file modes are checked,
   and the reviewed list from `--name-status -z`, whose status letters give the
   expected section count. Only a regular file is treated as a lockfile: a link or
   submodule pointer on either side of a change at a lockfile path fails closed with
   `lockfile_not_regular_file` rather than being elided. The prep, rereview, and
   verdict steps set `GIT_NO_REPLACE_OBJECTS=1`, so a replace ref left in a reused
   `.git` cannot substitute content in a diff, a patch id, or an evidence lookup.
   Every diff in `prep` and `rereview` passes `--ignore-submodules=none`. Git otherwise
   honors a submodule `ignore` setting from `.gitmodules`, and it reads that file from
   the index and `HEAD` even after decision 2 removes the working-tree copy, as well as
   from repository or runner configuration. Such a setting would hide a pointer change
   from the review diff, from the path lists, and from the rereview patch id. The diffs
   whose content the model or the patch id consumes also pass `--submodule=short`, so
   configuration cannot switch a pointer change to a format without a diff section.
   These checks run unconditionally. `prep` fails closed with `review_input_empty`
   when git's own comparison sees a change that no path list does, or when the
   filtered diff is empty although it has reviewed paths. It fails with
   `review_input_incomplete` when the filtered diff's section count differs, in
   either direction, from what `--name-status -z` implies: one section per reviewed
   path, and two for a type change between a file and a link, which git renders as a
   deletion and a creation. The unused full diff is no longer rendered. The model
   still sees every changed text line, and
   `review-verdict.py` resolves cited evidence from git objects at the reviewed
   commit, not the working tree.
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
   in-tree `.gitattributes`. A link at that file or at its directory can only be runner
   residue, because git metadata is never pull-request content, so the step refuses it
   and creates the file exclusively. No later step writes into the working tree
   (decision 4), so nothing can redirect a write onto the override. Both diff commands
   also pass `--no-ext-diff --no-textconv`, so neither an external diff nor a
   conversion driver in runner configuration can substitute their output; each of
   those controls is tested on its own. `review-verdict.py` reads evidence with
   `git cat-file blob`, which applies no attributes.
   Content git itself detects as binary, such as a text file carrying one NUL byte,
   still renders only as a binary marker. `--text` was rejected because it would make
   the bounded diff invalid UTF-8 for the tool-free providers. Instead, `prep` lists
   added and modified binary paths with `--numstat -z` and fails closed with
   `binary_content_unreviewable`, leaving the decision to a human, unless every one is
   an inert media type: `png`, `jpg`, `jpeg`, `gif`, `webp`, `avif`, `bmp`, `ico`,
   `woff`, `woff2`, `ttf`, `otf`, or `eot`, matched case-insensitively. Archives are
   deliberately excluded: they are opaque containers of arbitrary content, including
   runnable content. Deleted binaries and elided lockfiles are not counted. The
   remaining limit is that the allowance is by name: a file carrying an allowed
   extension is accepted unseen, so anything that later loads it as something other
   than media is outside this check, as is content the model can see but
   misjudges.
   The rereview skip compares `git patch-id --verbatim` ids (git 2.39 or later; the
   hosted image ships 2.55). The previous `--stable` ids strip whitespace, so a push
   after approval that only changed whitespace inside a command reused the approval.
   A git without `--verbatim` yields no id, and an unknown id never skips. Existing
   approval markers carry the old ids, so each open approved pull request gets one
   fresh review. For a binary path git includes the blob ids, so a swapped binary also
   changes the id.
8. **The review's user layer is fresh per run.** The Claude step sets
   `CLAUDE_CONFIG_DIR` to `$RUNNER_TEMP/claude-config-<run id>-<run attempt>`. The
   isolation step creates it with `mkdir -m 0700` and fails if anything, including a
   link, already exists there. The action's own `setupClaudeCodeSettings` still
   writes `~/.claude/settings.json`, which is now unread. The gate passes no
   `settings`, `plugins`, or `plugin_marketplaces` input, so the only lost setting
   is the project MCP pre-approval this decision does not want. A future change that
   needs one of those inputs must write it into the fresh directory instead.
9. **The review reads only the checkout and its review directory.** `claude_args`
   pass `--tools "Read,Grep,Glob"`, so no other built-in tool exists, and no allow
   rule, so the CLI's default permission applies: reads within the working directory
   and the `--add-dir` review directory are allowed, and anything else asks for
   permission, which the non-interactive review denies. `--disallowedTools
   "Read(//proc/**)"` denies process state outright, so re-adding a broad allow rule
   would still not expose it. This was verified against CLI 2.1.241 through
   `@anthropic-ai/claude-agent-sdk` 0.3.241, the versions the pinned action uses. A
   local stand-in for the model API issued scripted tool calls with no real
   credentials. With the previous arguments, a link committed in the workspace exposed
   the placeholder key and `/etc` was readable. With these arguments both were denied,
   while the checkout and the review directory stayed readable through all three
   tools. The action's own `parseSdkOptions` maps the workflow's `claude_args` to
   exactly those SDK options. A second probe drove the SDK with that parser's output
   for the workflow's literal `claude_args`, including `--json-schema`. The CLI offered
   `Glob`, `Grep`, `Read`, and `StructuredOutput`, read the review directory's diff,
   and returned a `success` result whose `structured_output` was the schema-shaped
   verdict. `--tools` therefore does not withhold the tool the structured verdict
   depends on.
10. **A verdict that carries credential material is unusable.** The verdict steps pass
    the model credentials and the job token to `review-verdict.py`. It refuses a
    verdict when any text in it contains one of those exact values, or a GitHub
    (`ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`, `github_pat_`) or Anthropic (`sk-ant-`)
    prefix followed by a token-length body. Its diagnostic names only the field. A bare
    prefix, as in a security review that mentions token formats, stays usable. Only
    field values are scanned: canonicalization has already rejected every field name
    outside the fixed schema, so a name cannot carry text into the published verdict. The
    submit step posts only a validated verdict, so a refused verdict becomes the
    existing fail-closed no-usable-verdict outcome. The validator's own git lookups
    run without those values in their environment.

    **Amended 2026-10-19 ([#1854](https://github.com/Verjson/.github/issues/1854)).**
    The verdict steps also hold `OPENAI_API_KEY` and `DEEPSEEK_API_KEY`, but the shape
    check named only GitHub and Anthropic formats, so an OpenAI or DeepSeek key the
    model saw anywhere other than the configured secret was publishable. The shape
    check now also refuses an `sk-` token, when nothing in `[A-Za-z0-9_-]` precedes it,
    that has any of these bodies:
    - 32 or more alphanumerics, which covers DeepSeek's 32 lowercase hex characters and
      OpenAI's 48-character legacy user keys;
    - an OpenAI segment prefix (`proj`, `svcacct`, `admin`, `service`) followed by 80 or
      more `[A-Za-z0-9_-]` characters;
    - 20 or more `[A-Za-z0-9_-]` characters on each side of `T3BlbkFJ`, the base64 of
      "OpenAI" that OpenAI keys carry, which covers a prefix not named above.

    Neither provider publishes a key-format specification. The prefixes, lengths,
    alphabets, and marker come from the public scanner rules that recognize these keys:
    gitleaks' `openai-api-key` rule (`proj`, `svcacct`, `admin`; 58 or 74 characters on
    each side of the marker; legacy keys of 20 alphanumerics on each side) and
    TruffleHog's `openai` (`proj`, `svcacct`, `service`), `openaiadmin`, and `deepseek`
    (`sk-` plus 32 `[a-z0-9]`) detectors. The minimums sit below
    every observed key and above the longest unbroken run ordinary prose produces, so a
    bare prefix ("rotate any `sk-proj-` keys"), a commit SHA, an npm integrity digest, or
    an identifier such as `task-sk-…` stays usable. The leading boundary covers every
    body character, so a match can start only at the beginning of a token run and the
    scan stays linear in the verdict's size. None of these shapes overlaps `sk-ant-`,
    which keeps its own alternative.

`scripts/ci-gate/review-agent-config-isolation.test.py` enforces decisions 1–4 and
6–9, and `scripts/ci-gate/review-verdict.test.py` enforces decision 10. Every
deliberate removal of a protection turns them red:

- It reads `claude_args` the way the pinned action's parser does.
- It executes the removal, isolation, attribute, `rereview`, `prep`, and telemetry
  steps against fixture repositories. The fixtures include:
  - a stubbed `gh`;
  - a planted storing credential helper;
  - a substituting external diff and a configured conversion driver;
  - links planted at every former in-tree output path and at the attributes override;
  - an unremovable path and a pre-existing config directory;
  - an empty or sectionless rendered diff;
  - paths with spaces, a lockfile name inside a longer path, and a newline;
  - a NUL-bearing script, an archive, an image and a font, and a deleted binary;
  - an approval followed by a whitespace-only change, and a git without `--verbatim`;
  - a checkout whose `origin` cannot be removed.
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
- **Residual, inherent to the action:** the CLI process environment still holds the
  long-lived model credential and the action's GitHub token, because the CLI needs the
  first and the action exports the second before starting it. Decision 9 removes the
  review's read path to them, and decision 10 refuses their exact values and token
  shapes in published text. Neither stops a value the model obtained some other way
  from being published in a transformed form, such as encoded or split. The job
  token's lifetime bounds only the token: the model credential stays valid until it is
  rotated, which is the remedy if exposure is ever suspected.
  *Amended 2026-10-19 (#1854):* the shapes come from public scanner rules, not a
  provider-documented format. A key format a provider introduces without an `sk-`
  prefix, a named segment, or the OpenAI marker is refused only by its exact
  configured value until the shape list is extended.
- **Residual, persistent-runner class (ADR 0235):** on the persistent
  private-repository lane, the reused `.git` directory from an earlier job is not
  neutralized as a whole. Its configuration, hooks, object store, and refs persist.
  Configuration such as a filesystem monitor command, or a hook git runs during a
  fetch, executes during the checkout and the gate's own git commands. The gate
  closes only the specific paths it can name: external diff and conversion drivers,
  attributes, submodule ignore settings, the submodule display format, and replace
  refs. This is the cross-job persistence ADR 0235 accepts for private
  repositories, which already keeps public pull-request heads on ephemeral runners.
- **Residual, accepted:** managed policy settings under `/etc/claude-code` are always
  loaded. They are root-owned runner state, the same persistent-host class ADR 0235
  accepts for private repositories.
