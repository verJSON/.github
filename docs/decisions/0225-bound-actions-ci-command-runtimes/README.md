# 0225 — Bound actions-ci command runtimes

- **Date:** 2026-10-09
- **Status:** Accepted
- **Issue:** [verJSON/.github#1736](https://github.com/verJSON/.github/issues/1736)
- **Extends:** [ADR 0076](../0076-bounded-actions-ci-shell-test-groups/README.md)
- **Extends:** [ADR 0224](../0224-shard-changelog-release-actions-ci/README.md)
- **Category:** CI policy

## Context

ADR 0224 split the changelog-release group into four matrix cells and assigned each caller-contract case to exactly one cell. Each case still ran the full generated contract, and every shard repeated generator-wide setup and assertions. The remaining manifest commands also ran serially. In run [37874848459](https://github.com/VerJSON/.github/actions/runs/37874848459), the affected commands ranged from 71 to 202 seconds, while `platform` and `merge-gate` occupied their runners for 13m 20s and 9m 49s.

The 60-second command budget requires reducing each command's work while preserving every assertion. ADR 0224 already partitions caller mutations across four matrix cells, and requires every mutation to run the complete generated contract. Issue #1743 records that four multi-case commands still take four to five minutes; this decision gives each mutation its own manifest row inside those same cells. The remaining manifest commands also repeat workflow parsing, generated-caller construction, and per-file scans. The four cells and six-job concurrency provide the available runner capacity; adding hosted jobs would repeat checkout and setup. The first full run also showed that CPU-heavy rows could exceed the budget while competing with sibling rows.

## Decision

1. Run independent actions-ci manifest rows concurrently up to the runner's reported `nproc`. An `@exclusive` row waits for active siblings and completes before the next row starts, reserving the full runner for commands that otherwise time out under contention. Buffer each row's output and emit its output, status, and elapsed time as soon as that worker is reaped, so a hanging sibling cannot hide completed diagnostics. Report the group result after all rows finish.
2. Enforce `ACTIONS_CI_COMMAND_BUDGET_SECONDS=60` on every pull-request invocation of the group runner, including the documentation-contract job. Keep the push-to-`main` ShellCheck audit over the full tracked tree; pull requests check changed tracked shell scripts.
3. Cache parsed workflow inputs, generated-caller output, and per-file logical-line scans where commands repeat that work. Keep these caches scoped to one group invocation and remove them with its temporary directory.
4. Give each of the 233 caller-contract mutation executions its own command shard across the existing four cells. Run the generator-wide assertions once, retain `max-parallel: 6`, and run the complete generated contract for every selected mutation. Do not add a section selector or skip assertions for any mutation.
5. Keep the `platform` and `merge-gate` jobs and the required `shell-tests` context unchanged. Do not add hosted runner jobs for these shards.

## Consequences

Every caller mutation still runs exactly once with the full generated assertions. Each runtime shard must select exactly one case, and the shard inventory test checks that the 233 expected executions map one-to-one across the four cells. Generator-wide checks run in their own command row. The required-checks audit, conformance suite, actions-ci manifest contract, and privileged-merge conformance rows are marked `@exclusive`; all other independent rows retain `nproc` parallelism. The audit contract suite divides its cases into two isolated partitions and runs them concurrently within one exclusive row, preserving every assertion while avoiding sibling-row contention. The effective parallelism and command count are logged. Each command runs in an isolated process group; budgeted commands use `timeout --foreground` so descendants stay in that tracked group. Output is captured to a per-command file and sanitized after the process exits, so a descendant inheriting stdout cannot keep the supervisor blocked. Surviving descendants are terminated and reported, timeouts stop the full command group, and cancellation waits for workers to clean up before removing shared temporary data. If a worker dies before sanitizing output, the parent sanitizes and reports its raw log before removing scratch data. Each worker's output, status, and elapsed time are emitted when it is reaped, so completed diagnostics remain visible if another row hangs or the job is interrupted. Missing or malformed worker results fail the group instead of reusing stale status. The pull-request command budget makes duration regressions fail at the affected row; the `main` full-tree ShellCheck audit remains uncapped so its complete scan stays enabled.

Measure the next `actions-ci` run against the issue's eight-minute `platform` and `merge-gate` targets and the 60-second pull-request command budget. Rollback can remove command-level parallelism, the budget, and the repeated-work caches while keeping the four-cell changelog-release split from ADR 0224.
