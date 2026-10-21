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

A context produced by an organization ruleset-required workflow (for example the
`verjson-cli-projects` package-surface workflow) has no workflow entity in the
consumer repository: `repos/<repo>/actions/workflows/<id>` answers 404, and the
workflow file lives in `Verjson/.github` at the revision the ruleset pins. Declare it
with a fifth key, `"workflow_repository":"Verjson/.github"`; `workflow_id` and
`workflow_path` are the identity the run itself reports in the consumer (`workflow_id`
and `path` of a run whose `workflow_url` ends `/actions/required_workflows/<id>`):

```json
{"name":"admission","app_id":15368,"workflow_id":345626628,"workflow_path":".github/workflows/cli-projects-package-surface-required.yml","workflow_repository":"Verjson/.github"}
```

No other `workflow_repository` value is accepted. For such an entry, terminal promotion
replaces the consumer-workflow metadata and default-branch blob checks with live
provenance ([ADR 0081](decisions/0081-event-driven-terminal-ai-promotion/README.md),
2026-10-21 correction): every active organization `workflows` rule on the default
branch must pin exactly one 40-hex revision of that path in `Verjson/.github`,
reachable from its `main`, and GitHub's GraphQL `WorkflowRun.file` must report that the
run executed exactly that file at exactly that revision. A run produced under an older
pin is refused after the ruleset rotates; re-run the required workflow on the head.
Such an entry contributes no retry workflow name — list only the consumer workflows'
names in the `--retry` argument.
