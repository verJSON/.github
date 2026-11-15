# Privileged merge adoption

Adopt privileged terminal promotion only through the canonical generator. The
required-check policy is repository-specific reviewed code; do not store it in an
organization or repository variable.

1. Read the effective rules for the default branch and list every non-promotion
   required status-check context.
2. For each context, identify a recent completed pull-request job that publishes
   the exact name. Record its GitHub App ID and the owning active Actions workflow's
   repository-local ID and path.
3. Review a compact JSON array with exactly these keys for every entry:

   ```json
   [{"name":"<exact-check>","app_id":123,"workflow_id":456,"workflow_path":".github/workflows/ci.yml"}]
   ```

4. Generate both callers at one immutable canonical contract SHA. The policy argument
   must be identical in both commands:

   ```bash
   scripts/gen-privileged-merge-caller.sh "$contract_sha" "$required_checks" > .github/workflows/ai-privileged-merge.yml
   scripts/gen-privileged-merge-caller.sh "$contract_sha" --retry "$workflow_names" "$required_checks" > .github/workflows/ai-promotion-retry.yml
   ```

5. Review the generated diff, run the generated caller contract tests, and let
   `privileged-merge-conformance.yml` byte-compare both callers, bind retry names to
   workflow IDs, compare each required context with its native `integration_id`, and
   verify the merged workflow/job/App evidence. A missing, ambiguous, or stale
   identity is a failed adoption, not a runtime configuration task.

When a required context, job name, App, workflow ID, or workflow path changes, update
the policy and regenerate both callers in the same pull request as the workflow or
ruleset adoption. Keep the existing path working until its replacement has produced
conformance evidence. Never remove exact-head, receipt, hold, same-owner, or workflow
provenance checks to make adoption pass.

## Checks produced by an organization ruleset-required workflow

A context produced by an organization ruleset-required workflow has no workflow
entity in the consumer repository: `repos/<repo>/actions/workflows/<id>` answers
404, and the executed file lives in `Verjson/.github` at the revision the active
organization ruleset pins. Declare it with a fifth key,
`"workflow_repository":"Verjson/.github"`. `workflow_id` and `workflow_path` are
the identity the run reports in the consumer: the `workflow_id` and `path` of a
run whose `workflow_url` ends in `/actions/required_workflows/<id>`.

`verjson-cli-projects` publishes `ci / build-test`, `ci / eligibility`, and
`admission` from required workflow 345626628
(`.github/workflows/cli-projects-package-surface-required.yml`). Rebind those
three through the generator and keep the consumer changelog checks. Retry names
are only the consumer workflows (`generated artifacts` and `changelog contract`);
a required-workflow entry contributes none:

```json
[
  {"name":"ci / build-test","app_id":15368,"workflow_id":345626628,"workflow_path":".github/workflows/cli-projects-package-surface-required.yml","workflow_repository":"Verjson/.github"},
  {"name":"ci / eligibility","app_id":15368,"workflow_id":345626628,"workflow_path":".github/workflows/cli-projects-package-surface-required.yml","workflow_repository":"Verjson/.github"},
  {"name":"admission","app_id":15368,"workflow_id":345626628,"workflow_path":".github/workflows/cli-projects-package-surface-required.yml","workflow_repository":"Verjson/.github"},
  {"name":"changelog / validate","app_id":15368,"workflow_id":325334018,"workflow_path":".github/workflows/changelog.yml"},
  {"name":"changelog-contract","app_id":15368,"workflow_id":339073811,"workflow_path":".github/workflows/changelog-contract.yml"}
]
```

No other `workflow_repository` value is accepted. For such an entry, terminal
promotion replaces the consumer-workflow metadata and default-branch blob checks
with live provenance
([ADR 0081](decisions/0081-event-driven-terminal-ai-promotion/README.md),
2026-11-15 correction): every active organization `workflows` rule on the default
branch must pin exactly one 40-hex revision of that path in `Verjson/.github`,
reachable from its `main`, and GitHub's GraphQL `WorkflowRun.file` must report
that the run executed exactly that file at exactly that revision. A run produced
under an older pin is refused after the ruleset rotates; re-run the required
workflow on the head. An evaluate-only ruleset does not appear in the effective
branch rules, so the declaration stays fail-closed until that ruleset is active.
