# 0235 — Public pull-request code runs only on ephemeral runners

- **Date:** 2026-10-15
- **Status:** Accepted
- **Issue:** [verJSON/.github#1831](https://github.com/verJSON/.github/issues/1831)
  tracks the deferred work. The finding was verified against the live
  organization on 2026-10-15 and fixed for `actions-ci.yml` in
  [verJSON/.github#1827](https://github.com/verJSON/.github/pull/1827).
- **Extends:** [ADR 0147](../0147-route-untrusted-ci-to-ephemeral-hosted-runners/README.md),
  which made the untrusted lane ephemeral
- **Narrows:** [ADR 0054](../0054-public-repositories-admitted-to-the-general-pool/README.md),
  whose "Residual risk" section accepted public pull-request code on the
  persistent pool
- **Supersedes in part:** [ADR 0047](../0047-fast-lane-runner-variable/README.md),
  for `actions-ci.yml` only, and
  [ADR 0048](../0048-merge-gate-fast-lane-by-visibility/README.md), for a public
  target of the `ai-review-merge.yml` relay (decision 7)
- **Related:** [ADR 0234](../0234-route-secretless-trusted-ref-builds-to-the-hosted-lane/README.md)
  ([#1826](https://github.com/verJSON/.github/pull/1826)).
  Its trade-off paragraph cites ADR 0147's rollback of `CI_LANE_UNTRUSTED` to
  `["self-hosted","general"]`. Decision 2 below withdraws that rollback, so
  0234's fail-closed eligibility guard is the expected outcome of an attempted
  persistent rollback, not an available operating state.

## Context

`Verjson/.github` is public. On 2026-10-15 the organization was configured as
follows:

| Setting | Live value |
| --- | --- |
| `CI_RUNNER_FASTLANE`, `CI_LANE_TRUSTED`, `CI_LANE_FALLBACK` | `["self-hosted","general"]` |
| `CI_LANE_UNTRUSTED`, `CI_RUNNER_UNTRUSTED`, `CI_RUNNER_ISOLATED` | `["ubuntu-24.04"]` |
| Runner groups `DigitalOcean`, `GitHub`, `runner-fleet-recovery` | `visibility: all`, `allows_public_repositories: true` |
| Runner group `verjson-runner-canary` | `visibility: selected`, `allows_public_repositories: true` |
| Fork pull-request approval policy | `all_external_contributors` |

`actions-ci.yml` triggers on `pull_request`, and every job in it selected
`fromJSON(vars.CI_RUNNER_FASTLANE || vars.CI_LANE_TRUSTED || ...)`. By design,
those jobs run the pull request's own scripts and tests. Code from any pull
request, including a fork, therefore ran on the persistent DigitalOcean
`general` pool. That pool also runs the AI review gate, container deployment,
and other privileged jobs. The only remaining control was the click that
approves a fork's first workflow run.

GitHub's guidance is direct. The
[secure-use reference](https://docs.github.com/en/actions/reference/security/secure-use)
says self-hosted runners "should almost never be used for public repositories"
because "any user can open pull requests against the repository and compromise
the environment". The
[runner setup guide](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
recommends self-hosted runners only for private repositories.

ADR 0054 accepted this risk because public repositories had to be able to use
the general pool. This decision keeps that ability for trusted events. It
removes only pull-request execution from the pool.

### Cost (owner goal #1817: minimize hosted spend)

The
[GitHub Actions billing reference](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
states that usage is free "for public repositories that use standard
GitHub-hosted runners". The same page notes that larger runners are always
billed. `ubuntu-24.04` is a standard runner, so this change spends no metered
minutes. The real limit is concurrency. The
[Actions limits reference](https://docs.github.com/en/actions/reference/limits)
caps standard hosted runners at 60 concurrent jobs on the Team plan, shared
across the organization. A full `actions-ci` run uses up to 9 of those slots:
`change-scope`, six `shell-test-groups` cells (already `max-parallel: 6`),
`docs-contracts`, the existing hosted compatibility job, `adr-number-collision`,
and `shell-tests`, some of which run in sequence.

## Decision

**Every job that a `pull_request` event can reach in this public repository
selects an ephemeral runner.** Reachable jobs include those in local reusable
workflows. The job must use either the untrusted lane with a hosted tail and no
persistent fallback, `fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]')`,
or a literal GitHub-hosted image.

1. **`actions-ci.yml` uses the untrusted lane for every job and every event.**
   One selector is the simplest rule a reviewer can check. It is also the only
   event-aware option that `hosted-selector-policy.py`'s reviewed grammar
   permits today without a new allowlist entry. Push validation of `main` now
   runs on the same image its pull requests used, which removes a source of
   drift between the two. Trusted push jobs could have stayed on the pool. They
   do not, because hosted capacity costs nothing here and a split selector adds
   no safety.
2. **The ADR 0147 untrusted family is the only set of variables treated as
   ephemeral.** The six names are `CI_LANE_UNTRUSTED`, `CI_RUNNER_UNTRUSTED`,
   `CI_RUNNER_ISOLATED`, and their `VERJSON_` aliases. This decision
   **withdraws ADR 0147's rollback to `["self-hosted","general"]`**. A rollback
   target for the untrusted lane must be ephemeral: GitHub-hosted, or a
   dedicated ephemeral pool that the reconciler classifies as the `untrusted`
   group. Pointing the lane back at the persistent pool would undo this decision
   for every public repository at once.
3. **A contract test enforces the rule.**
   `scripts/ci-gate/public-pr-runner-isolation.py` evaluates every
   `pull_request` job's `runs-on` the way Actions does for a public repository.
   It follows local `uses:` calls and models job conditions, Actions' implicit
   `success()`, and skipped dependencies. It enumerates every organization
   variable a selector reads in two states: unset, and set to its class value.
   The untrusted family takes the hosted value; every other variable takes a
   persistent value. A chain such as `vars.CI_LANE_UNTRUSTED ||
   vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]'` is therefore a violation.
   A free reference compared to a literal (`vars.MODE == 'shared'`) is also
   tried as that literal. Any other comparison over a free reference (against
   another reference, a function result, or an ordering) evaluates both ways.
   Only standard GitHub-hosted image names count as ephemeral (`ubuntu-latest`,
   `ubuntu-NN.NN[-arm]`, `windows-latest|YYYY|11-arm`,
   `macos-latest|NN[-intel]`), because a self-hosted runner can carry any
   custom label.
   A reusable input that the caller sets from an expression is tried as an
   adversarial value, because a fork can edit the caller. A remote reusable
   call that a pull request can reach, or any selector the checker cannot
   resolve, is undetermined. It never passes as clean.
   `pull_request_review`, `pull_request_review_comment`, and `merge_group`
   also run the pull request's own code, so they are held to the same rule as
   `pull_request`. Every trigger a workflow names must be classified: one of
   those four, a privileged event or relay in decision 5, or a documented event
   that carries no fork-controlled ref or text (such as `push`, `schedule`,
   `release`, `create`, or `label`). `workflow_dispatch`, `workflow_call`, and
   `repository_dispatch` are relays, not safe events: their dispatcher or
   caller chooses the inputs and can hand the run a pull request head, as
   `gate-rearm.yml` does (decision 7). `fork`, `issues`, `discussion`, and
   `discussion_comment` carry a repository or text an outside account chooses,
   so they are privileged events. Any other
   trigger is undetermined, including `check_run`, `check_suite`, `status`,
   `deployment`, and `deployment_status`, which can name a fork head SHA, and
   any unknown name such as a future `pull_request_*` event. A duplicate
   path-and-job row in the pending or reviewed list is a format error (exit 2)
   rather than a silent override.
   The `platform` group runs the checker against the live tree, with its
   pending and reviewed lists, and
   `public-pr-runner-isolation.test.py` covers its behavior with fixtures. Both
   are registered in `scripts/actions-ci-groups.tsv`.
4. **Two canonical workflows are pending, ratcheted exactly by
   `scripts/ci-gate/public-pr-runner-isolation.pending.tsv`.**
   `actionlint.yml` sends public targets to `CI_RUNNER_FASTLANE`, which is
   persistent today. It is reached directly and through
   `actionlint-reusable-contract.yml`. `container-candidate.yml` falls through
   to `CI_LANE_FALLBACK` when the untrusted lane is unset, and it is reached
   through `container-candidate-reusable-contract.yml`.
   `hosted-selector-policy.py` pins both selectors byte for byte, either through
   `REVIEWED_SELECTOR_EXPRESSIONS` or through
   `CANONICAL_CONTAINER_CANDIDATE_EXPRESSION`. Concurrent
   [#1826](https://github.com/verJSON/.github/pull/1826) owns edits to that file,
   so both changes land after it. The required expressions are:

   - `actionlint.yml`: insert
     `github.event_name == 'pull_request' && fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') ||`
     after the private-repository branch.
   - `container-candidate.yml`: replace the final term with
     `github.event_name == 'pull_request' && fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') || fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]')`.

   Each pending row carries the SHA-256 of the job's `runs-on` value. A listed
   job whose selector changes is a violation again, so a row cannot mask a
   worse selector. The checker reports a fixed entry that is still listed as
   `STALE PENDING` and fails. It also fails on any unlisted job that violates
   the rule.
5. **Privileged events use a reviewed-job allowlist, not head detection.**
   `pull_request_target`, `workflow_run`, and `issue_comment` run the base
   workflow with base privileges, and fork activity can trigger them. `fork`,
   `issues`, `discussion`, and `discussion_comment` get the same treatment:
   their payload names a repository or carries a title and body an outside
   account writes, which a job can turn into a ref. Under
   any of these events, a reachable job that can resolve to a persistent or
   self-hosted runner fails, including a job reached through a local reusable
   call or a remote callee whose placement cannot be read. It passes only when
   `scripts/ci-gate/public-pr-runner-isolation.reviewed.tsv` lists it. Each
   entry is `path<TAB>job<TAB>review digest<TAB>justification`, with an
   optional fifth `dynamic:` field described below, keyed by
   workflow path (including the call chain and event), job id, and a SHA-256
   over:

   - the job's full normalized content;
   - the workflow-level trigger block (`on:`, including `types`, `branches`,
     and `paths`), `env`, `permissions`, and `defaults` it inherits;
   - every calling job on the path to it, together with that caller's
     inherited settings;
   - the scanned-tree bytes the job puts on disk. That means what each
     checkout materializes, not what commands name:
     - non-cone `sparse-checkout`: exactly the listed paths, glob-expanded;
     - cone mode: each listed directory recursively, the root-level files,
       and the files directly in every parent directory, as git materializes
       them;
     - every local action directory (`uses: ./…`), recursively, plus files
       its composite steps reach through `github.action_path`;
     - a full checkout of the scanned tree: every tracked entry, in path
       order, as one JSON array per entry, so no path or link target containing
       a newline can forge an entry boundary. In a git work tree the entries
       are git's index (`git ls-files -s -z`), so untracked residue such as
       `__pycache__` never counts. Each entry carries its index mode and:
       for a file, the SHA-256 of its bytes on disk, so an unstaged edit
       counts; for a symlink, its target, never followed; for a submodule
       gitlink, its commit ID, which is what `submodules: true` or a later
       `git submodule update` would check out; and for a path the index holds
       but the disk does not, the index object a checkout would put there. An
       edit, an added or deleted file, a mode change, a retargeted link, and a
       moved gitlink each change the digest. Outside a git work tree it is
       every file except `.git`, without modes. Reading the bytes of this
       repository's 867 tracked files (about 12 MB) costs 13–37 ms per run,
       once per run.

       The pending and reviewed lists are left out, because an entry cannot
       hold a digest of its own bytes. Nothing in the checker binds the lists
       themselves. A list edit reaches `main` only through a pull request
       under `main`'s ruleset: one approval from a code owner
       (`* @verJSON/devs`) on the last push, which the AI review App supplies
       where the organization grants it merge authority (ADR 0098). A list
       row gets exactly the scrutiny that pull request's review gives it;
   - the job's unresolved reasons, below, with their details.

   Any edit to any of these makes the entry `STALE REVIEWED`, which fails
   until someone re-reviews it.

   **Unresolved reasons and the `dynamic:` field.** Some on-disk content
   cannot be bounded by the scanned tree. The checker over-approximates it:
   each of the following leaves the job unresolved under a named reason.

   | Reason | What the checker matches |
   | --- | --- |
   | `fetch` | A fetch named anywhere in a step (its `run` without full-line comments, `shell`, `with`, and `env`), in a local composite action's steps, or in any scanned-tree file the job puts on disk. The vocabulary covers `curl`, `wget`, `rsync`, `scp`; git `clone`, `fetch`, `pull`, `submodule`, `ls-remote` after any global options; `gh api`, `gh release download`, `gh run download`, `gh pr checkout`, `gh pr diff`, `gh repo clone`; `npx` and package-manager installs (npm, pnpm, yarn, pip, uv, go, cargo, gem, apt, brew); `docker`/`podman` `run`, `pull`, and `build`; PowerShell `iwr`/`irm`; Python `urllib`, `urlopen`, `requests`, `httpx`, `http.client`, and Node `fetch(`, the `http`/`https` modules, `https.get`, `axios`; and `["gh", "api"]`/`["git", "fetch"]`-style argv lists. It also covers the `actions/download-artifact`, `actions/cache`, and `actions/github-script` actions, `docker://` steps, local Docker actions, and a job's `container:` and `services:` images, in both the string and `image:` forms. Those images must also be pinned by an `@sha256:` digest: a tag, an expression, or a missing image is refused, and no entry can accept the job. The action-pin conformance test reads only `uses:`, so this is the only rule that pins them. |
   | `head-materialization` | Head detection concludes that the job materializes the pull request head, or cannot decide. Only "no known head-materializing pattern" leaves this reason unset, and that result is not proof of safety. |
   | `tree-change` | A runtime command that changes the checked-out tree: git `sparse-checkout`, `checkout`, `switch`, `restore`, `reset`, `pull`, `merge`, `rebase`, `worktree`, `apply`, `am`, `cherry-pick`, `read-tree`, or `checkout-index` after any global options; any git command given `--git-dir` or `--work-tree`; `gh pr checkout`; or `patch`. |
   | `shell` | A `shell:` other than `bash` on any step, including a local composite action's step, or in a job or workflow `defaults.run`. |
   | `full-checkout` | A checkout of the scanned tree with no `sparse-checkout` list. The whole tracked tree is in the digest, as above. |
   | `runtime-checkout` | A checkout whose repository or ref is selected at runtime, not the scanned tree. Only this repository at the default ref or `github.sha` is the scanned tree. `pull_request.base.sha` is the base as of the pull request's last update and is not refreshed when the base branch advances, so a checkout at it can put older bytes on disk than the reviewed tree; it is a runtime checkout. It must carry a `sparse-checkout` list; without one, no review can bind what it puts on disk, so the job is refused and no entry can accept it. |
   | `submodules` | A checkout with `submodules: true` or `recursive`. |
   | `unscanned-path` | A sparse path, glob, or local-action file that the scanned tree does not contain, or that lies outside it. |
   | `local-action` | A local action that is missing, nested more than four deep, has no `runs` block, or whose manifest does not parse. |

   The entry for an unresolved job needs a fifth field,
   `dynamic: <reason>[, <reason>]: <justification>`, for example
   `dynamic: fetch, runtime-checkout: <what is fetched and checked out, and why
   a pull request cannot choose it>`. The field must name exactly the reasons
   the checker computes. A missing reason fails as unclassifiable and names the
   reason. A reason the job no longer has makes the entry stale. An unknown or
   repeated reason, or an empty justification, is a format error (exit 2). When
   a reviewed job gains a reason, the `STALE REVIEWED` line lists the newly
   unresolved reasons with their details.

   **Why over-approximate fetches.** An earlier revision exempted a job that
   fetched content but executed only paths it could bind to the scanned tree.
   The review at 62723cdd passed 27 of 35 adversarial jobs through that
   binding: `bash -c "$(curl …)"`, `. <(curl …)`, `sh < f`, interpreter `-c`,
   `-m`, and heredoc input, shell aliases, `xargs`, a `curl -o` that overwrites
   a scanned file, `download-artifact` into `.`, and a `working-directory`
   inside an extracted archive. Deciding which fetched bytes a job executes
   from command text is the same open-ended problem that the on-disk digest
   below abandoned. Any fetch now needs `dynamic: fetch`. The cost: every
   reviewed job that calls `gh api` carries that reason and a justification
   that the response is read only as data.

   **Why on-disk rather than command parsing.** A fourth review round found
   `source`, `.`, bare script paths, Python imports, Node `require`, `make`,
   `npm run`, and data files read by scripts. Each reaches a file that no
   command names. Every shell, interpreter, and build tool has its own way to
   reach a file. The set of bytes a checkout puts on disk does not depend on
   any of them, so digesting those bytes closes the class. Command parsing
   remains only for files a local action reaches through
   `github.action_path` and for head detection.

   **What the checker still does not enforce.** The `fetch` reason comes from a
   vocabulary, so it is incomplete by construction. A fetch spelled outside it
   is not tagged: for example a raw socket, an interpreter library the list
   does not name, or a program the job runs that is not in the scanned tree
   and that head detection does not read. The digest still binds the job's
   text and the scanned bytes it puts on disk, so adding such a fetch to a
   reviewed job turns its entry `STALE REVIEWED` until a person re-reads it,
   but nothing demands a `dynamic:` field for it. The digest does not bind what SHA-pinned remote
   actions or tools fetch at runtime. A pinned action can install a mutable
   npm dependency; only the actions in the table above are tagged. Those
   fetches are outside the scanned tree and are the reviewer's responsibility.
   Action pins are policed separately by the action-pin conformance test. A
   remote callee must be pinned to a full 40-hex commit SHA; no entry can
   accept a branch or tag, because either can change after review. The
   checker does not read a remote callee's jobs, so its entry carries no
   reasons. An entry that no longer matches a job needing review is also
   stale. Jobs that resolve only to standard hosted images pass without an
   entry.

   **Why this replaces pattern detection.** Three review rounds each found
   head-materializing shapes the pattern detector missed. Examples include
   reading `$GITHUB_EVENT_PATH` with `jq` into `GITHUB_ENV` or `GITHUB_OUTPUT`,
   `actions/github-script` calling `downloadTarballArchive`, `git --git-dir`
   fetching `refs/pull`, and `gh repo clone` of the head repository. The set of
   ways a job can obtain head bytes is open-ended. An allowlist of reviewed job
   content is closed: anything new or changed fails until a person reads it.
   Head detection prints a `DIAGNOSTIC` line explaining why a job looks
   dangerous. It cannot pass a job: an unlisted job fails whatever it
   concludes. When it finds a materializing pattern or cannot decide, the job
   is also unresolved under `head-materialization`.

   **Relays.** `workflow_dispatch`, `workflow_call`, and `repository_dispatch`
   are scanned like privileged events, with three differences:
   - Their own inputs take every value the dispatcher or caller can choose. A
     `choice` input takes each option, a `boolean` takes both values, and any
     other input takes the adversarial values decision 3 uses. In a standalone
     `workflow_call` scan, `github.event_name` is every event a caller can
     have, because a called workflow sees its caller's event.
   - Head detection runs on every relayed job that can reach a persistent
     runner. When it finds a materializing pattern or cannot decide, the job
     is refused: it must move to an ephemeral runner, and no reviewed entry
     can accept it. A relayed job carries a head only because something
     handed it one, so no justification can make running it on the pool safe.
   - Any other relayed job that can reach a persistent runner needs a reviewed
     entry, as `workflow_run` does.

   44 relayed jobs predate this rule. Moving them is #1831's work: most are
   the canonical reusable workflows that adopters call, and some are fleet
   canaries that must run on the pool. They are ratcheted in the pending list
   under a "relay backlog" header, keyed by a relay digest: SHA-256 over the
   job's selector and its unresolved reason names. A changed selector, or a
   new reason such as `head-materialization`, makes the row stop applying.
   A row that a reviewed entry also lists is undetermined, and a pending row
   never stands in for a privileged-event review. The two `gate-rearm.yml`
   jobs reached standalone through `workflow_call` are the same jobs already
   reviewed under `pull_request_target`; their digests are identical, so they
   carry the same justification as reviewed entries.

   `ai-post-merge.yml`'s `reconcile` job checked its reconciler out at
   `pull_request.base.sha`. It now checks out `github.sha`, the base-branch
   commit its workflow file is read from, so the script it runs is the one its
   review digest binds; its entry was re-reviewed with that ref.

   `issue_comment` has no workflow in this repository. The one `workflow_run`
   workflow, `ai-promotion-retry.yml`, is a reviewed entry: it checks out only
   a trusted `Verjson/.github` revision. Downloading a workflow artifact is a
   fetch, so a reviewed job that does so needs `dynamic: fetch`, and its
   justification must say how the artifact is used.

6. **Review procedure for adding or refreshing an entry.**
   1. Run `public-pr-runner-isolation.py --pending … --reviewed … .github/workflows`.
      The failing `VIOLATION` or `STALE REVIEWED` line names the path, job, and
      new review digest, and a `DIAGNOSTIC` line gives the head-detection
      result.
   2. Read the whole job and everything it inherits or calls, including the
      trigger block and every repository script and local action the digest
      covers. Record three things: what it checks out (repository, ref,
      paths); what it executes (inline code, repository scripts, actions, and
      any program it writes); and how every PR-derived value and downloaded
      artifact is used. If the checker reports unresolved reasons, name every
      one of them in the `dynamic:` field. For each, state what the job
      fetches, changes, or checks out at runtime, how it uses those bytes, and
      why a pull request cannot choose them or reach execution through them.
      Write it for this job; a generic sentence is not a review. Prefer
      removing a reason (a narrow `sparse-checkout` list, `bash`, no
      download) to justifying it.
   3. Prefer moving the job to a hosted runner. List it only when it needs the
      trusted pool and no PR-controlled byte can reach execution: anything
      PR-derived is used only as an API identifier or as validated data.
   4. Add or replace the row with that digest and a one-line justification
      covering checkout, execution, and why PR content cannot reach execution.
      Never relist a stale row without re-reading the change.

7. **The merge-gate relay runs a public target on the ephemeral lane.**
   A third review round found a live exposure the checker did not scan.
   `gate-rearm.yml` runs on `pull_request_target` and dispatches
   `ai-review-merge.yml` with the pull request number and head SHA. That run's
   `gate` job checks the head out at the workspace root and runs the review
   action on it. Under ADR 0048 a public target sent `gate` to
   `CI_RUNNER_FASTLANE`, which is `["self-hosted","general"]` today. The
   authorization label only decides who can start the run; it does not change
   which bytes reach the runner, so it does not meet decision 5's bar.
   Every job the dispatch reaches now selects `CI_LANE_UNTRUSTED` with a hosted
   tail unless the target is private: `preflight`, `gate`,
   `complete-authorization`, `dispatch-merge`, and the
   `app-key-environment.yml` job `validate-ai-review-key` that `app-key-policy`
   calls. The shape is the one `actions-ci.yml` uses, plus the private branch
   each job already had:
   - The persistent lane needs `github.event.repository.private == true`.
     Actions coerces a missing `private` to 0, so a payload without
     visibility also takes the ephemeral lane. `TARGET_REPO` is the event
     repository on both entry points since ADR 0036, so the payload describes
     the target.
   - `gate` and `dispatch-merge` additionally need preflight's API lookup not
     to say public (`target_private != 'false'`). An unreadable lookup (`''`)
     keeps a private target on its lane, so a lookup outage spends no hosted
     minutes on a private repository. A lookup that says public overrides a
     private payload.
   - A Verjson caller's `runner_labels` input is honored only for a private
     repository. Off Verjson it still names the caller's own fleet. A public
     Verjson caller that passed it would otherwise reopen the pool through a
     pull request that edits the caller.
   - A private target keeps its previous lane: `gate` on
     `CI_RUNNER_OVERFLOW || CI_LANE_TRUSTED`, `dispatch-merge` on
     `CI_LANE_PRIVILEGED`, `preflight` on `CI_RUNNER_OVERFLOW ||
     CI_LANE_UNTRUSTED`, `complete-authorization` on `CI_RUNNER_FASTLANE`, and
     `validate-ai-review-key` on `CI_LANE_TRUSTED`.

   Nothing these jobs consume exists only on the fleet. They read repository
   and organization secrets (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`,
   `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`) and the `ai-review-app` environment's
   `AI_REVIEW_APP_PRIVATE_KEY`, which GitHub delivers to hosted and
   self-hosted runners alike. They use `bash`, `git`, `gh`, `jq`, `python3`,
   `openssl`, and SHA-pinned actions, all on the `ubuntu-24.04` image, and
   reach only `api.github.com` and the model providers' public endpoints. No
   step reads a path, socket, or credential that only a pool host holds. The
   same jobs already run on `ubuntu-24.04` for every caller outside Verjson,
   and ADR 0048 chose the fast lane for public targets because it was hosted
   then; it now names the pool.
   The four selectors are pinned in `hosted-selector-policy.py`'s
   `REVIEWED_SELECTOR_EXPRESSIONS`, and `runner-routing-policy.test.sh`
   evaluates every visibility, lookup, and `runner_labels` polarity.

   **Rollout.** `ai-review-merge.yml` is not a required workflow; organization
   ruleset 20722935 requires only `gate-rearm.yml`, at a stored SHA. This
   repository's own gate dispatches its default-branch copy, so the change
   takes effect here at merge. An adopter's `ai-review-merge.yml` is a
   generated caller pinned to a canonical commit, so an adopter is covered
   only after its caller is regenerated at or after the merge commit
   (`scripts/gen-ai-review-caller.sh <merge-sha>`, ADR 0227's consumer
   recipe). This change does not edit `gate-rearm.yml`, so ruleset 20722935
   needs no rotation for it.

### What this decision cannot do

For a `pull_request` run, GitHub reads the workflow file from the pull
request's merge commit. A fork can therefore edit `runs-on:` itself and request
`[self-hosted, general]`. No routing expression in this repository can prevent
that. Routing removes every default path to the pool. The controls that stop a
hostile fork are runner-group admission and the fork-approval review.

The operator option, which this decision does not take, is to set the
`DigitalOcean`, `GitHub`, `runner-fleet-recovery`, and `verjson-runner-canary`
runner groups to `allows_public_repositories: false`. ADR 0054's rollback
section explains the prerequisite. Every trusted job that a public repository
still routes to the pool would then queue forever, with no diagnostic. Those
jobs must move to hosted capacity first: push and schedule jobs on
`CI_RUNNER_FASTLANE` and `CI_LANE_TRUSTED`, the privileged merge path on
`CI_LANE_PRIVILEGED`, and `runner-admission-reconcile`. The owner decides
whether to take this option.

## Consequences

- No default path sends pull-request code in this repository to a persistent
  runner, apart from the two ratcheted entries above. The contract test detects
  a regression to a fallback chain or to a reusable workflow that honors a
  caller runner.
- `actions-ci` push runs move from the persistent pool to hosted capacity at no
  metered cost. They now compete for the organization's 60 standard hosted
  slots.
- The untrusted lane can no longer be rolled back to the persistent pool.
  `runner-admission-reconcile.sh` still accepts a persistent untrusted selector,
  so live enforcement of that rule is a follow-up tracked in #1831.
- Public targets of the merge-gate relay run on hosted capacity: one more
  `ubuntu-24.04` slot per review, free for public repositories. The AI lane's
  `gate` job keeps its 80-minute ceiling, so a slow review now holds a hosted
  slot rather than a pool runner.
- Adopters keep the exposure until their generated `ai-review-merge.yml` caller
  is regenerated at or after the merge commit (decision 7).
- The same guarantee for public adopters' calls to the canonical reusable
  workflows is a follow-up tracked in #1831. Those include `node-ci`, `ui-ci`,
  `helm-ci`, `pulumi-ci`, `generated-artifacts`, `repo-hygiene`, and
  `changelog-validate`. It needs the same event-aware branch and
  `hosted-selector-policy.py` allowlist entries.

## Rollback

Revert the pull request. The previous selector puts public pull-request code
back on the persistent pool. Treat that as a security regression and record it
with a tracked cause.
