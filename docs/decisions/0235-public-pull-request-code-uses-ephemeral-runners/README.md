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
   The `runner-isolation` workflow runs the checker against the live tree, with
   its pending and reviewed lists, on every pull request (drafts included) and
   every push to `main`, with no path filter. The review digests bind tracked
   files that `actions-ci`'s path filter omits (`config/`, `bin/`,
   `contracts/`, `package*.json`, `profile/`, `index.html`, `.gitignore`), and
   a documentation-only diff skips `actions-ci`'s heavy groups, so an edit
   there could otherwise stale a reviewed entry without any run noticing.
   `actions-ci` stays path-filtered (#233, #234); the check is one short job
   (about 0.6 s of checker time plus Python setup) on the untrusted lane.
   `pr-ci-tiers.test.py` pins its trigger as Tier 1 with no path filter. The
   `platform` group also runs the checker, and
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
   **Done (2026-10-17).** #1826 merged first, so both expressions landed in
   #1827 itself (round-4 finding 6, below); the five pending rows were removed,
   and the checker now refuses any pull_request row.
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
     - non-cone `sparse-checkout`: every tracked path a listed gitignore
       pattern can match, evaluated as git does (anchored only by a slash
       before its last character, otherwise at every depth; `*` matching a
       leading dot); a pattern it cannot evaluate exactly is refused
       (2026-10-17 amendment);
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
   | `fetch` | A fetch named anywhere in a step (its `run` without full-line comments, `shell`, `with`, and `env`), in a local composite action's steps, or in any scanned-tree file the job puts on disk. The vocabulary covers `curl`, `wget`, `rsync`, `scp`; git `clone`, `fetch`, `pull`, `submodule`, `ls-remote` after any global options; `gh api`, `gh release download`, `gh run download`, `gh pr checkout`, `gh pr diff`, `gh repo clone`; `npx` and package-manager installs (npm, pnpm, yarn, pip, uv, go, cargo, gem, apt, brew); `docker`/`podman` `run`, `pull`, and `build`; PowerShell `iwr`/`irm`; Python `urllib`, `urlopen`, `requests`, `httpx`, `http.client`, and Node `fetch(`, the `http`/`https` modules, `https.get`, `axios`; and `["gh", "api"]`/`["git", "fetch"]`-style argv lists. It also covers the `actions/download-artifact`, `actions/cache`, and `actions/github-script` actions, `docker://` steps, local Docker actions, and a job's `container:` and `services:` images, in both the string and `image:` forms. Every one of those images must also be pinned by an `@sha256:` digest: a `docker://` step's image, a local Docker action's `docker://` image, every `FROM`, `COPY --from`, and `RUN --mount=…,from=` source and any `# syntax=` frontend in the Dockerfile a local Docker action builds (an earlier stage name or index, or `scratch`, excepted; a non-default `escape` directive is refused outright), and a job's `container:` and `services:` images. A tag, an expression, or a missing image or `FROM` is refused, and no entry can accept the job. The action-pin conformance test reads only action `uses:` pins, so this is the only rule that pins images. |
   | `head-materialization` | Head detection concludes that the job materializes the pull request head, or cannot decide. Only "no known head-materializing pattern" leaves this reason unset, and that result is not proof of safety. |
   | `tree-change` | A runtime command that changes the checked-out tree: git `sparse-checkout`, `checkout`, `switch`, `restore`, `reset`, `pull`, `merge`, `rebase`, `worktree`, `apply`, `am`, `cherry-pick`, `read-tree`, or `checkout-index` after any global options; any git command given `--git-dir` or `--work-tree`; `gh pr checkout`; or `patch`. |
   | `shell` | A `shell:` other than `bash` on any step, including a local composite action's step, or in a job or workflow `defaults.run`. |
   | `full-checkout` | A checkout of the scanned tree with no `sparse-checkout` list. The whole tracked tree is in the digest, as above. |
   | `runtime-checkout` | A checkout whose repository or ref is selected at runtime, not the scanned tree. Only this repository at the default ref or `github.sha` is the scanned tree. `pull_request.base.sha` is the base as of the pull request's last update and is not refreshed when the base branch advances, so a checkout at it can put older bytes on disk than the reviewed tree; it is a runtime checkout. It must carry a `sparse-checkout` list; without one, no review can bind what it puts on disk, so the job is refused and no entry can accept it. |
   | `submodules` | A checkout with `submodules: true` or `recursive`. |
   | `unscanned-path` | A sparse path, glob, or local-action file that the scanned tree does not contain, or that lies outside it. No digest follows a symlink: a link binds as its target text, so retargeting it changes the digest, and a link whose target leaves the tree, or a path reached through a linked directory, is unscanned, because its bytes would be the runner's rather than the repository's. Head detection reads a program through a link only when the link and every parent resolve inside the scanned tree; otherwise the program is unreadable (2026-10-17 round-6 amendment). |
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
   any of them, so digesting those bytes closes the class for a sparse
   checkout, where the digest holds every listed file. It does not close it
   for a full checkout. A reviewed entry binds a full checkout by the
   whole-tree digest. A relay-backlog row cannot, because that digest changes
   on every commit, so since the round-6 amendment a backlog job may not
   check out this whole tree at all. The invariant is that every
   scanned-tree byte a reviewed or pending job can execute or read is either
   in its digest or refused. Command parsing remains only for files a local
   action reaches through `github.action_path` and for head detection.

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
   under a "relay backlog" header, keyed by a backlog digest over the job's
   full content (see the 2026-10-17 amendment). The keys are frozen in the
   checker, refusals are decided before any row is read, and a key tolerates
   only the refusal kinds it carried when the backlog froze.
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
   The five selectors are pinned in `hosted-selector-policy.py`'s
   `REVIEWED_SELECTOR_EXPRESSIONS`, and `runner-routing-policy.test.sh`
   evaluates every visibility, lookup, and `runner_labels` polarity.

   **Rollout.** `ai-review-merge.yml` is not a required workflow; organization
   ruleset 20722935 requires only `gate-rearm.yml`, at a stored SHA. This
   repository's own gate dispatches its default-branch copy, so the change
   takes effect here at merge. An adopter's `ai-review-merge.yml` is a
   generated caller pinned to a canonical commit, so an adopter is covered
   only after its caller is regenerated at or after the merge commit
   (`scripts/gen-ai-review-caller.sh <merge-sha>`, ADR 0227's consumer
   recipe). Regenerating every public adopter's caller is a **blocking**
   rollout step, not a follow-up: the rollout is incomplete, and #1831's
   decision-7 item stays open, until
   `python3 -I scripts/public-review-caller-rollout.py --minimum <merge-sha>`
   exits 0 (see the 2026-10-17 amendment). This change does not edit
   `gate-rearm.yml`, so ruleset 20722935 needs no rotation for it.

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
  runner; the two entries decision 4 ratcheted are fixed. The contract test detects
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
  is regenerated at or after the merge commit (decision 7). The rollout is not
  complete until `public-review-caller-rollout.py` exits 0 for that commit.
- The same guarantee for public adopters' calls to the canonical reusable
  workflows is a follow-up tracked in #1831. Those include `node-ci`, `ui-ci`,
  `helm-ci`, `pulumi-ci`, `generated-artifacts`, `repo-hygiene`, and
  `changelog-validate`. It needs the same event-aware branch and
  `hosted-selector-policy.py` allowlist entries.

## Rollback

Revert the pull request. The previous selector puts public pull-request code
back on the persistent pool. Treat that as a security regression and record it
with a tracked cause.

## Amendment (2026-10-17, #1827) — round-4 review

A fourth adversarial review of #1827 failed the change with six findings.
Each is recorded here with the decision taken.

### 1. The relay backlog no longer waives content or refusals

**Finding.** A relay-backlog row was keyed to a digest over the job's
selector and its unresolved reason *names*. A backlog job could change
anything else — a new command, an unpinned `container:` image, a different
caller — and keep its row. The pending list was also consulted before
refusals, so a matching row waived refusals that decision 5 says no entry can
accept.

**Decision.**

- **Frozen keys.** `public-pr-runner-isolation.py` holds the backlog as code:
  `PULL_REQUEST_BACKLOG` (the five pull_request jobs of decision 4) and
  `RELAY_BACKLOG` (the 44 relayed jobs). A pending row whose key is in
  neither is a format error (exit 2), so the list cannot grow without a code
  change (see the round-5 amendment for what that guarantees).
  `public-pr-runner-isolation.test.py` pins both as a snapshot that the code
  may only shrink against, and requires the live pending list to name exactly
  the frozen keys, so a job that leaves the list leaves the code too.
- **Refusals first.** Every refusal is tagged with a kind: `remote-callee`,
  `image`, `runtime-checkout`, or `relay-head`. They are decided before the
  pending or reviewed list is read. `RELAY_BACKLOG` names, per key, the kinds
  that job carried when the backlog froze (28 carry `relay-head`, 16 of those
  also `runtime-checkout`; none carries `image` or `remote-callee`). Only a
  frozen key *with its own pending row* tolerates those kinds; any other kind,
  or any kind once the row is gone, fails whatever the lists say, and no
  reviewed entry stands in for it. A kind the job no longer carries fails as
  `STALE BACKLOG` until it is removed from the code and the snapshot.
- **Content binding.** A backlog row now keys to the backlog digest: SHA-256
  over the job's full normalized content, every calling job, the inherited
  workflow settings (trigger block, `env`, `permissions`, `defaults`), the
  bytes of every local action the job runs, and its unresolved reason names.
  Any edit to the job stops the row applying.

**Trade-off.** Refusing the 28 grandfathered jobs outright would have made
this pull request #1831: they are canonical reusable workflows that private
adopters run on the pool by design. Tolerance is therefore granted by code,
per key and per kind, never by a list row, and can only shrink. As first
written, the backlog digest left out the repository bytes a checkout puts on
disk; the 2026-10-17 round-5 amendment below replaces that choice, which
let a row survive a change to what the job runs.

### 2. The runner-isolation check is part of the required `shell-tests` context

**Finding.** The checker ran only in the standalone `runner-isolation`
workflow. Repository ruleset `core-checks-actions` (20515822) requires only the
`shell-tests` context, so a red isolation run was advisory and a pull request
could merge past it.

**Decision.** `actions-ci.yml` gains a `runner-isolation` job (no draft guard,
untrusted lane, the same checkout-isolated steps as the standalone workflow),
and the `shell-tests` aggregate needs it and fails unless its result is
`success`; `skipped` and `cancelled` fail too, because the job has no `if:`.
`scripts/ci-gate/runner-isolation-required.test.py` executes the live
aggregate step against stubbed `needs` results. The standalone workflow stays,
because `actions-ci` is path-filtered (#233, #234) and the review digests bind
files outside that filter; on such a diff the required context does not report
at all, so the standalone run is the signal there. Making the standalone
context required would be a ruleset change, which is the owner's decision and
is not taken here.

### 3. Regenerating the public adopters' callers is a blocking rollout step

**Finding.** Decision 7's rollout said public adopters "come first" but left
their regeneration as an operator follow-up with no completion criterion, so
the change could be reported done while `verjson-git-runners` still pinned
`2d1ea177` and sent public pull-request heads to the pool.

**Decision.** The rollout has an executable exit criterion.
`scripts/public-review-caller-rollout.py --minimum <merge-sha>` lists
every public, non-archived repository in the organization (so a repository
made public later is covered without editing a list), reads its
default-branch `.github/workflows/ai-review-merge.yml`, requires every job to
call the canonical `ai-review-merge.yml` at one full commit SHA, and asks the
compare API whether that commit contains the merge commit. It exits 1 naming
each stale caller and 2 on any state it cannot establish (an API error other
than an absent caller, an unparseable caller, a branch or short-SHA pin, an
unknown compare status). A repository without a caller is reported, not
failed: nothing there dispatches a gate. The default branch is
percent-encoded before it reaches the contents query. The script lives in
`scripts/`, not `scripts/ci-gate/`, because ADR 0193 reserves that directory
for gates Actions runs. It reads only, needs no credentials
beyond read access, and is not wired into pull-request CI, because its answer
is organization state that a pull request cannot change.

Rollout order after #1827 merges:

1. Regenerate each stale public adopter's caller at or after the merge commit
   with ADR 0227's recipe, in that adopter's own repository and PR.
   On 2026-10-17 the check (run against this branch's head as a stand-in
   minimum) reported `verjson-git-runners` STALE at `2d1ea177`, and
   `gguf-demo` and `repo-dashboard` with no caller.
2. Run the check with the merge SHA; it must exit 0. Record the output on
   #1831. The rollout, and decision 7, are complete only then.
3. Only after that, the optional `MINIMUM_SAFE_REVIEW` hardening (raising it
   to the merge SHA in `gate-rearm.yml`, then rotating ruleset 20722935) can
   make the requirement continuous, so a later bootstrap or hand edit cannot
   reintroduce an older caller. That edit and rotation remain the owner's
   decision.

### 4. Non-cone sparse-checkout patterns are evaluated as gitignore patterns, or refused

**Finding.** A non-cone list is gitignore syntax, but the checker expanded each
line with a root-anchored Python glob that skips dotfiles. `tool.sh` or `lib/`
matches at every depth in git and only at the root in the glob, and
`scripts/*` puts `scripts/.env.sh` on disk without binding it, so a reviewed
job's bytes could change under its entry. Character classes and backslash
escapes also mean different things to the two matchers.

**Decision.** A pattern is anchored to the root only when it has a slash before
its last character; otherwise it is evaluated at every depth (`**/<pattern>`),
with hidden files included and `.git` excluded. A negation is skipped: it only
removes bytes, so the union of the positive patterns over-approximates the
checkout. Anything the checker does not evaluate exactly fails closed as a
`sparse-pattern` refusal that no entry can accept: a character class or
backslash escape in a non-cone pattern, any wildcard, class, escape, or
leading `!` in a cone-mode entry (which git reads as a directory), and an
empty or bare-root pattern. Literal paths and `*`/`?` wildcards remain
available. Every live sparse list in this repository is a literal path, so no
entry changed digest.

### 5. A Dockerfile's other image sources and frontends are vetted

**Finding.** Only `FROM` lines were checked. `COPY --from=<image>` and
`RUN --mount=type=bind,from=<image>` pull an image when the name is not an
earlier stage, and a `# syntax=` parser directive makes BuildKit pull and run
a frontend image, so a reviewed local Docker action could pull mutable code
that its review digest never bound.

**Decision.** `COPY --from`, every `from=` in a `RUN --mount=`, and a
`# syntax=` frontend must name an earlier stage (by name, or by an index lower
than the number of stages so far) or be pinned by an `@sha256:` digest; an
expression, a tag, a later stage, or an unknown name fails as an `image`
refusal no entry can accept. Only leading `--flag=value` tokens are read, so a
command argument that looks like a flag is not mistaken for one. A
non-default `# escape=` directive changes line continuation, so the checker
cannot parse the file and refuses it. Directives are matched on any line, not
only the top: that over-approximates, and a false refusal is fixed by moving
the comment. No Dockerfile in this repository is affected.

### 6. The rows #1826 left behind are gone, and the selector count is five

**Finding.** The five pull_request rows for `actionlint.yml` and
`container-candidate.yml` (each reached directly and through its reusable-call
contract) waited on #1826, which merged before this review, so they were
stale. Decision 7 also said "four" selectors where it lists, and
`hosted-selector-policy.py` pins, five.

**Decision.** Decision 4's expressions landed here: `actionlint.yml` takes
`github.event_name == 'pull_request' && fromJSON(vars.CI_LANE_UNTRUSTED ||
'["ubuntu-24.04"]')` right after its private-repository branch, and
`container-candidate.yml`'s three jobs take the same term ahead of their
fallback tail. Private repositories and non-pull-request events keep their
lanes. `hosted-selector-policy.py`, `actionlint-reusable.test.sh`,
`container-candidate-contract.test.sh`, the `candidate-selector-unbound`
fixture, and the ADR 0050 routing cases pin the new expressions. The five rows
are removed, and with them the pull_request half of the pending list:
`PULL_REQUEST_BACKLOG` and the selector-digest ratchet no longer exist, so the
checker refuses any pull_request row as outside the frozen backlog, and the
test requires the live tree to be clean with only relay rows listed. The four
relay-backlog rows of the same jobs (reached through `workflow_call`) keep
their keys; their backlog digests were refreshed because their `runs-on`
changed, which is the content binding of finding 1 working as intended. The
rollout gate of finding 3 is unaffected: these are reusable workflows that
adopters call by commit, so an adopter gains the change when it bumps that
pin.

Moving `actionlint` to the hosted image for a public pull request exposed a
lint verdict that depended on the image's ShellCheck (0.9.0 on `ubuntu-24.04`
reports SC2015 chains the pool's build does not). The durable fix is ADR 0026's
own principle, that lint policy must not be an accidental property of routing:
`actionlint.yml` now pins ShellCheck 0.11.0 by version and digest (ADR 0026,
2026-10-17 amendment).

## Amendment (2026-10-17, #1827) — round-5 review: a backlog row binds what the job runs

**Finding.** A pending row still applied after the job's runtime behavior
changed. The reviewer appended `gh pr checkout "$PR"` and a `refs/pull/$PR/head`
fetch-and-checkout to `scripts/ci-telemetry/rework-reconcile.sh`, which the
frozen backlog job `rework-reconcile.yml [workflow_dispatch]` / `reconcile`
runs from a full checkout; the checker printed `PENDING` and exited 0. Two
causes: head detection followed only `./x`, `../x`, and interpreter-invoked
programs, so the bare relative call `scripts/ci-telemetry/rework-reconcile.sh …`
was never read; and the backlog digest bound none of the bytes the job runs.
The earlier reason for leaving checkout bytes out (a checkout of the list's own
directory, and the whole-tree digest of a full checkout) held only for full
checkouts and the lists themselves.

**Decision.**

- Head detection and the program closure follow a bare relative command word
  (`scripts/x.sh`) in any command position (line start, after `;`, `&`, `|`,
  `(`, `$(`, a backtick, or a compound keyword, past `NAME=value` prefixes)
  when it names a file in the scanned tree. A word that names no tracked file,
  such as `node_modules/.bin/tool`, is not a program, so it cannot make a step
  undetermined.
- The pending and reviewed lists are left out of every on-disk binding at their
  real paths, as the tree digest already did, because a list edit is reviewed
  as the pull request that makes it.
- The backlog digest now binds the job, its callers, its inherited settings,
  its reason names, the bytes its sparse checkouts and local actions put on
  disk, and the bytes of every scanned-tree program its steps execute. For
  shell scripts that closure is followed through to the scripts they run, to
  the closure depth limit of 4. Another interpreter's source is bound by its
  bytes but not read as shell, because its string literals would otherwise pull
  most of the tree in. A full checkout's whole-tree digest stays out; the
  programs it executes stand in for it.
- `actionlint.yml`'s policy checkout is non-cone, so it puts exactly its two
  files on disk. In cone mode it also put every file directly in `.github/`,
  `scripts/`, and `scripts/ci-gate/` on disk, about 360 files, and binding those
  would have restaled the actionlint backlog row on nearly every pull request.

**Trade-off.** A backlog row now goes stale when any program its job runs
changes, not only when the job definition changes. That is the intended
content binding. Its cost is a mechanical digest refresh for the affected rows,
which changes only the digest column. A pull request that edits a script a
backlog job runs therefore shows that job in its diff, and that is where a
reviewer should look. The closure is still a vocabulary: a program reached
through an indirection the patterns do not model (an alias, `xargs`, a Python
`subprocess` call) is not followed. This is the same residual decision 5
records for fetches.


**Shrink-only, stated accurately.** The frozen backlog cannot grow without a
visible code change: a pending row naming a key outside `RELAY_BACKLOG` is a
format error, and `public-pr-runner-isolation.test.py` fails if `RELAY_BACKLOG`
holds a key or tolerated kind its round-4 snapshot lacks. Both the code and
the snapshot live in this repository, though, so a pull request can edit both
together. The guarantee is that growth is explicit and reviewable, not that it
is impossible.

## Amendment (2026-10-17, #1827) — round-6 review: a backlog row binds what is on disk

**Finding.** Four live backlog jobs ran tracked code that the round-5 program
closure never bound: `capability-floor-observe` (an import of
`github_identity`), `container-deployment` `dry-run` (an import of the
preflight module), `release.yml` `verify` (a script run through a shell
function after a full checkout), and `release-app-canary` (a CLI materialized
with `git show`). Recognizing more command shapes cannot close this class:
imports, shell functions, variables, and data files reach bytes that no
command names. Writing the round-6 tests found one more case:
`rework-reconcile.sh` runs `"$here/rework-classify.sh"` and two sibling
scripts through a variable.

**Decision: bind what is on disk, not what the parser recognizes.**

- A relay-backlog job may not check out this repository's whole tree. A full
  checkout of the scanned tree is refused for a pending row, whatever kinds
  the key tolerates, because no row can bind it without restaling on every
  commit. A job that runs this repository's code checks out an exact non-cone
  sparse list of the files it needs. The on-disk binding then covers imports,
  functions, siblings, and data byte for byte. The round-5 program closure
  is removed from the digest. Head detection still follows bare relative
  paths.
- `git show`, `git archive`, and `git cat-file` put tracked bytes on disk
  without a checkout. They are refused the same way, read in every step and
  every shell script the job checks out. The one exception is
  `git show "$GITHUB_SHA:<path>"` (or `${{ github.sha }}`) of a file the job's
  own sparse list holds: those are exactly the bytes the list binds. The
  release canary keeps its `git show`, so replacement refs and working-tree
  edits still cannot change the CLI it runs, and checks out exactly that file.
- A standalone `workflow_call` job's default checkout is its caller's
  repository at the caller's ref, not this tree. `container-deployment` runs
  the caller's generated copy of the controller (`gen-container-deployment.sh`),
  so this repository's controller and preflight bytes never run there, and
  binding them would bind the wrong tree. Such a checkout is not refused as
  this tree. Running caller code is the exposure #1831 already tracks for
  these jobs.
- Converted to exact sparse checkouts, each isolated under a per-run path with
  a cleanup step:
  - `capability-floor-observe` (two programs, `github_identity.py`, two
    configs, and the five generators the audit checks for);
  - `dependency-supersession-observe` and `-reconcile` (one program);
  - `release-app-canary` (`scripts/changelog.py`);
  - `rework-reconcile` (four scripts and its thresholds).
  `renovate-compatibility-reconcile` reads no repository file, so it creates
  its working directory instead of checking out.
  `scripts/ci-gate/backlog-sparse-closure.test.py` derives each converted job's
  programs, their Python import closure by `ast`, shell siblings reached
  through `$here/`, and `git show` paths. It requires each sparse list to hold
  exactly those plus the data files named there. That test caught
  `rework-report.sh` missing from the first list.
- **`release.yml` `verify` is the one exception (PM decision, 2026-10-17;
  Part of #1831).** It is generated by `gen-changelog-caller.sh`, and
  `release-verify.sh` refuses a copy that differs from the pinned generator.
  By design it runs the repository's whole release suite on the exact commit
  that is tagged, so it needs the full tree. `RELAY_BACKLOG` grants its key,
  and no other, the tolerated kind `full-checkout`. Its row binds the job text
  and the bytes of every scanned-tree program that `ProgramTrace` resolves it
  running, never the whole tree. The trace covers shell, Python, and
  JavaScript, follows each program from the shallowest depth it is reached
  at, and binds what `npm pack` selects when a program packs the package or
  runs its bins (round-7 amendment). Every program the job runs that the trace
  cannot resolve is an `unresolved-program` refusal that no key tolerates.
  Since round 8 the trace also binds every tracked program a traced text names
  literally, so the set is 36 files on this branch rather than the 13 the
  round-6 trace bound; the round-8 amendment lists them and states what the
  binding guarantees and what it does not:
  - `release-verify.sh` (run through the `run_clean` wrapper);
  - the programs it runs: `changelog.py`, `render-next.sh`,
    `gen-adr-index.sh`, `adr-relative-links.test.py`,
    `doc-fragment-names.test.sh` and its `doc-fragment-names.sh`, and
    `contract-tools-package.test.js`, which packs the package and runs its
    bins `bin/render-next.js` and `bin/gen-adr-index.js`;
  - two data files: `.github/workflows/release.yml`, whose header carries the
    generator pin the hook compares against, and `package.json`, whose
    `files` and `bin` the pack selects;
  - 24 programs reached only because a bound program names them (round 8).

  `backlog-sparse-closure.test.py` independently reads the scripts the hook
  runs, and the files the pack selects, and requires each to be bound. A test
  also fails if any other key is granted `full-checkout`.

  **Residual.** The round-8 amendment names every residual. In short: code
  resolved through PATH from outside the tree, git objects `changelog.py`
  reads as data, an absent guarded hook, `package-lock.json` dependency code,
  the frozen pin comparison, a resumed release's tag tree, and the inputs the
  Markdown and link checks read.

  Every other form the trace cannot resolve refuses. For what remains, the
  protection is code review of `main`, not this checker. The exception is
  removed when #1831 moves `verify` off the persistent pool through the
  generator and a regeneration.

**Trade-off.** Every converted job must now list what it reads, and adding a
file to a job means editing its sparse list. The closure test catches a
missing program, import, or sibling. A missing data file breaks the job's next
run instead, which is why `DATA` in that test names each one explicitly.

**Head detection's call forms (round-6 should-fix).** Head detection is
diagnostic for reviewed entries and decides the relay-head refusal for
relays, so the forms it follows matter. It follows `<interpreter> [flags]
<path>`, `./path`, `../path`, and a bare relative path to a tracked file in a
command position. A shell's `-e` is errexit, so `bash -e x` runs `x`; only
`-c` (alone or bundled, such as `-ec`) makes the next word inline code for a
shell, Python, or pwsh, and `-e` does for node, perl, and ruby. It does not
follow:

- `bash -o <option> <path>` and other options with values;
- `sh -c "<path> …"`, `bash -c`, and `eval`;
- `source <path>` and `. <path>`;
- `env [NAME=value…] <path>`, `nohup`, `sudo`, `timeout <n>`, `exec`, and
  `xargs`;
- compound commands such as `if`, `while`, and functions;
- quoted command words, variables (`"$here/x.sh"`), and output piped into a
  shell;
- input redirected into an interpreter (`bash < path`).

A program that leaves the scanned tree through a link is unreadable rather than
read from the scanner host. `backlog-sparse-closure.test.py` asserts that no
relay-backlog job hands a literal tracked path to any of these forms. For a
converted job the sparse list binds the bytes whatever form reaches them.

## Amendment (2026-10-17, #1827) — round-7 review: verify's trace fails closed

**Finding.** The round-7 review found that the `verify` binding missed code
`verify` runs:

- the package bins that `contract-tools-package.test.js` packs and runs;
- a `git archive` appended to `release-verify.sh`, which was not refused;
- a program first met as a data token or at the depth cap, which was never
  followed;
- `source`, `bash -c`, an assigned path, `python3 -m`, and a `.`-sourced
  file, which below depth 1 bound nothing.

**Decision (PM, 2026-10-17; Part of #1831).**

- **Fail closed.** Discovery fails closed. A program the job runs that
  discovery cannot resolve is the refusal kind `unresolved-program`, which no
  key tolerates, not even `verify`.
- **The trace.** `ProgramTrace` replaces the old program closure:
  - It reads shell as shell, including comments, heredocs, command
    substitutions, arithmetic, operators, array assignments, `"$@"` wrappers,
    command prefixes, redirects, and assignments. It reads Python through its
    imports, `-m`, `-c`, subprocess interpreter calls, and loader calls, and
    JavaScript through `require`, spawn and exec calls, package bins, and
    `npm pack`.
  - It keeps a bound set apart from a followed set, and follows each program
    from the shallowest depth any path reaches it at. A program at the depth
    cap that still runs something is unresolved, never truncated.
  - It refuses as unresolved: `eval`, `xargs`, `npx`, a pipe into an
    interpreter, an unresolvable variable or untracked path, perl, ruby, and
    pwsh, and computed loads.
  - An optional hook that the tree does not hold, and that runs only behind
    `[ -x path ]`, runs nothing.
- **Materializations.** A `git show`, `archive`, or `cat-file` in any traced
  shell text or JavaScript spawn refuses. The one exception is the frozen pin
  comparison named in the residual above.
- **Bytes and roots.** Bound programs are hashed as raw bytes. A relative root
  is made absolute.

## Amendment (2026-10-17, #1827) — round-8 review: bind every literal tracked program

**Finding.** The round-8 review found forms the round-7 trace did not model:

- a literal program path handed to `spawnSync("./scripts/x.sh")`,
  `subprocess.run(["scripts/x.sh"])`, or `subprocess.run("scripts/x.sh")`;
- `git show` spelled `/usr/bin/git show` or `"git" show`, and a
  `git checkout <rev> -- <path>` or `git restore --source=` in a traced
  program, which changes the checked-out tree at runtime;
- `import subprocess as sp`, a from-import overwritten by nested inline
  Python, and a spawn on a `child_process` object under another name;
- the prepack, prepare, and postpack scripts `npm pack` runs;
- a `pin=HEAD`, a DEBUG trap, or a `sed` function that changes what the
  frozen pin comparison runs while its line stays byte-identical;
- a program reached through a symbolic link;
- `bash -s` operands taken as a script path, and `npm --prefix` reading the
  root `package.json` in place of another package's.

**Decision (PM, 2026-10-17; Part of #1831).** Stop enumerating invocation
forms and generalize the binding.

- **Literal rule.** Any literal in a traced program text that names a tracked
  program is bound and followed. A program is a tracked file with a shebang,
  the exec bit, or a `.sh`, `.bash`, `.py`, `.js`, `.mjs`, or `.cjs`
  extension. Data files stay excluded.
- **Two modes.** A program reached by an invocation the trace models is
  followed as *invoked*: every form in it is traced, the depth cap applies,
  and a composed target it cannot resolve refuses. A program reached only
  because a literal names it is followed as *named*: its bytes are bound and
  its own literals followed, but its forms are not traced, because nothing
  shows that the job runs it. Per-form tracing remains only for composed
  targets (`python -m`, imports, spawns, `npm` verbs); an unresolvable
  composed target stays `unresolved-program`.
- **Symbolic links.** A program reached through a link binds the link text
  and the target's bytes and is followed into the target, through any chain
  of in-tree links. A link that leaves the tree, is broken, or loops refuses
  as `unresolved-program`.
- **Spawns.** Python spawn aliases (`import subprocess as X`,
  `from subprocess import run`) are computed per source. A `.spawn*`,
  `.exec*`, or `.fork(` call in JavaScript on any receiver other than
  `child_process` refuses, because the trace cannot identify the object;
  that also refuses a RegExp `.exec`, an accepted false positive.
- **Materializations.** `git` is matched when spelled by path or quoted. A
  tree-changing git verb (`checkout`, `restore`, `reset`, `switch`, and the
  rest of the runtime tree-change set) or `gh pr checkout` in a traced text
  refuses as `tracked-materialization`.
- **The frozen pin comparison.** It is tolerated only while everything that
  runs before it is reviewed bytes. The hook's leading logical lines must be
  the preamble, matched by digest, then the `$pin` derivation, its guard, and
  the show line, in that order. The derivation is stored as explicit text in
  `FROZEN_MATERIALIZATIONS` and must be the hook's only `pin=` assignment.
  `release.yml`'s header must name one 40-hex pin. The frozen show binds
  `release.yml`, the pin's source, into the row itself rather than through
  an incidental data token.
- **npm.** `npm pack` traces the package's prepack, prepare, and postpack
  scripts unless `--ignore-scripts` is given. A verb that reads package
  scripts in another package directory (`--prefix`, `-C`, `--workspace`,
  `-w`, `--workspaces`) refuses.
- **`bash -s`.** `-s` ends option parsing, its operands are arguments, and
  standard input is what the trace reads; a pipe into it refuses with a
  reason of its own.
- **PATH.** A PATH entry naming the workspace or working directory, or a
  relative one, refuses as `unresolved-program`. This covers an assignment,
  a `GITHUB_PATH` append, and a job or step `env` block.

**What the check guarantees.** The trace is a static approximation of what
`verify` runs, not a proof of it. It guarantees two things. Every literal
tracked program reachable from `verify` is bound into its row. Every
composed target that an invocation form the trace models cannot resolve
refuses before any row is read. It does not claim that the bound set is
everything the job executes. These residuals stay outside it:

1. **Bare command words resolve through PATH.** A workspace PATH entry
   refuses, but a bare word that resolves to a program outside the scanned
   tree, such as one on the runner image or in a tool cache, is not bound.
2. **`changelog.py` reads git objects as data.** It reads
   `git show <revision>:<path>` and `git cat-file -e <tag>:<path>`, for
   fragments and the pre-contract migration document. It parses those
   bytes and never executes them.
3. **An absent guarded hook binds nothing.** A hook that runs only behind
   `[ -x path ]` and that the tree does not hold runs nothing. Adding it
   changes the tree, and the trace then follows it.
4. **`package-lock.json` selects dependency code.** That code comes from the
   registry, not the scanned tree. It is what `npm ci`, `npm rebuild`,
   `npm pack`, and `node_modules/.bin` run.
5. **The frozen pin comparison runs a historical generator.**
   `git show "$pin:scripts/gen-changelog-caller.sh" | bash -s …` runs the
   generator as it stood at that 40-hex commit, not the scanned tree's copy.
6. **A resumed release runs the tag's tree.** When the version's tag already
   exists, `verify` checks the tag out. Its tree's bytes are what run, and
   the row, bound against the default-branch head, did not bind them. The
   step requires the tag to be an ancestor of that head, so those bytes were
   once on `main` and were reviewed under the gate as it stood then.
7. **The Markdown and link checks read data.** Their inputs are not code
   the job runs.
8. **Unmodeled forms with composed targets.** A literal is a path the
   checker's literal pattern matches: letters, digits, and `_.@+-`, either
   joined by `/` or ending in a program extension. A program named only by a string the code
   assembles at runtime, and run through an invocation form the trace does
   not model, is neither bound nor refused. Review of `main` is the
   protection there.

All of these are removed when #1831 moves `verify` off the persistent pool.

**Bound set.** `verify` binds 36 files on this branch:

- 10 invoked programs: `release-verify.sh`, `changelog.py`,
  `render-next.sh`, `gen-adr-index.sh`, `adr-relative-links.test.py`,
  `doc-fragment-names.test.sh`, `doc-fragment-names.sh`,
  `contract-tools-package.test.js`, `bin/render-next.js`, and
  `bin/gen-adr-index.js`;
- 2 data files: `release.yml` and `package.json`;
- 24 named programs, reached through chains of literal names that start in
  the job's steps and the invoked programs. They include this checker and
  its test file.

Because the checker and its tests are among the named programs, editing
either stales `verify`'s row. The pending row is regenerated in the same
commit.

**Trade-off.** A named program's edit now stales `verify`'s row even when
`verify` never runs it. That cost is deliberate: the alternative is
enumerating every way a literal can become an execution, and each review
round found another way. A future invocation form needs no checker change
to be bound, only to be refused when it composes its target.
