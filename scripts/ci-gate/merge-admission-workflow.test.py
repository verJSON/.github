#!/usr/bin/env python3
"""Contract tests for the merge-admission producer workflows (ADR 0260, #1990, #2031).

The producer runs entirely outside the evaluated head's workflow-run and check-run
inventories (ADR 0260, action 3): it is started only by events whose runs belong to the
default-branch commit, while a short keyless trigger workflow carries the pull-request
events and completes before the producer starts.
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

# Events whose runs belong to the pull request's head, and so to the inventory the
# assertion reads (ADR 0229 rule 2, Gate C).
HEAD_EVENTS = {"pull_request", "pull_request_target", "pull_request_review", "pull_request_review_comment", "merge_group"}
# Of those, the events that start a run on a pull request's head; a merge-group run belongs
# to the merge-group ref instead.
PULL_REQUEST_HEAD_EVENTS = HEAD_EVENTS - {"merge_group"}
# The producer, its trigger, and its caller: never derived as completion triggers of their own.
MERGE_ADMISSION_FILES = {"merge-admission.yml", "merge-admission-trigger.yml", "merge-admission-caller.yml"}
# Workflows that no pull-request event starts, yet that complete a check on the head: the AI
# review gate patches its authorization check run there. It is dispatched on the default
# branch, so its completed run names no pull-request head, and the producer sweeps instead.
DISPATCHED_HEAD_REPORTERS = {"AI review + auto-merge"}
# The caller's concurrency group: the head a narrowed plan evaluates, or one sweep key. It
# must not equal the evaluate job's per-head group, or a run would wait on itself.
CALLER_CONCURRENCY_GROUP = (
    "merge-admission-caller-${{ contains(fromJSON('[\"pull_request\",\"pull_request_target\",\"pull_request_review\"]'), "
    "github.event.workflow_run.event) && github.event.workflow_run.head_sha || 'sweep' }}"
)


EXPRESSION_TOKEN = re.compile(r"\s*(\|\||&&|\(|\)|,|'(?:[^']|'')*'|[A-Za-z_][A-Za-z0-9_.-]*)")


def evaluate(expression: str, context: dict) -> object:
    """Evaluate the GitHub expression subset the caller's concurrency key uses.

    Models the documented semantics that matter here: a missing property is null, `||` and
    `&&` return an operand, `fromJSON` parses its string, and `contains` on an array is a
    case-insensitive element match. Any other construct is rejected rather than guessed.
    """
    tokens, position = [], 0
    while position < len(expression):
        match = EXPRESSION_TOKEN.match(expression, position)
        assert match and match.end() > position, f"unmodelled expression syntax at {expression[position:]!r}"
        tokens.append(match[1])
        position = match.end()
        if not expression[position:].strip():
            break
    cursor = [0]

    def peek():
        return tokens[cursor[0]] if cursor[0] < len(tokens) else None

    def take(expected=None):
        token = peek()
        assert expected is None or token == expected, (expected, token, expression)
        cursor[0] += 1
        return token

    def truthy(value):
        return value not in ("", None, False, 0)

    def same(left, right):
        if isinstance(left, str) and isinstance(right, str):
            return left.lower() == right.lower()
        return left == right

    def call(name, arguments):
        if name == "fromJSON":
            (text,) = arguments
            return json.loads(text)
        assert name == "contains", f"unmodelled function {name}"
        haystack, needle = arguments
        assert isinstance(haystack, list), f"unmodelled contains() over {type(haystack).__name__}"
        return any(same(item, needle) for item in haystack)

    def primary():
        token = take()
        if token == "(":
            value = disjunction()
            take(")")
            return value
        if token.startswith("'"):
            return token[1:-1].replace("''", "'")
        if peek() == "(":
            take("(")
            arguments = [disjunction()]
            while peek() == ",":
                take(",")
                arguments.append(disjunction())
            take(")")
            return call(token, arguments)
        root, *path = token.split(".")
        assert root in context, f"unmodelled context {root}"
        value = context[root]
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        return value

    def conjunction():
        value = primary()
        while peek() == "&&":
            take("&&")
            other = primary()
            value = other if truthy(value) else value
        return value

    def disjunction():
        value = conjunction()
        while peek() == "||":
            take("||")
            other = conjunction()
            value = value if truthy(value) else other
        return value

    result = disjunction()
    assert cursor[0] == len(tokens), f"trailing expression tokens in {expression!r}"
    return result


def interpolate(template: str, context: dict) -> str:
    # GitHub renders each `${{ }}` and concatenates; null renders as the empty string.
    def render(match):
        value = evaluate(match[1], context)
        return "" if value is None else str(value)

    return re.sub(r"\$\{\{\s*(.+?)\s*\}\}", render, template)


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / f"{name}.yml").read_text(encoding="utf-8"))


def triggers(doc: dict) -> dict:
    return doc.get("on", doc.get(True))


def event_specs(doc: dict) -> dict:
    on = triggers(doc)
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return dict.fromkeys(on)
    return on


def reports_on_a_pull_request_head(doc: dict) -> bool:
    # A workflow that runs only when a pull request closes reports nothing a merge waits on.
    specs = {event: spec for event, spec in event_specs(doc).items() if event in PULL_REQUEST_HEAD_EVENTS}
    close_only = all(
        isinstance(spec, dict) and spec.get("types") and set(spec["types"]) <= {"closed"} for spec in specs.values()
    )
    return bool(specs) and not close_only


class CallerTriggerTests(unittest.TestCase):
    def test_the_producer_starts_only_from_default_branch_events(self):
        on = triggers(workflow("merge-admission-caller"))
        self.assertEqual(set(on), {"workflow_run", "schedule", "workflow_dispatch"})
        self.assertFalse(set(on) & HEAD_EVENTS)
        self.assertEqual(on["workflow_run"]["types"], ["completed"])
        self.assertIn("Merge admission trigger", on["workflow_run"]["workflows"])

    def test_the_periodic_sweep_is_scheduled_at_most_fifteen_minutes_apart(self):
        schedules = triggers(workflow("merge-admission-caller"))["schedule"]
        self.assertEqual(len(schedules), 1)
        minute, *rest = schedules[0]["cron"].split()
        self.assertEqual(rest, ["*", "*", "*", "*"])
        self.assertRegex(minute, r"^\*/([1-9]|1[0-5])$")

    def test_every_workflow_that_reports_on_a_pull_request_head_is_a_completion_trigger(self):
        # ADR 0260 action 5: re-evaluate on completion of each workflow that reports a check
        # on the head. Derived from the workflows themselves, so a new one cannot be missed.
        expected = {workflow("merge-admission-trigger")["name"]} | DISPATCHED_HEAD_REPORTERS
        for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
            if path.name in MERGE_ADMISSION_FILES:
                continue
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if reports_on_a_pull_request_head(doc):
                expected.add(doc["name"])
        listed = triggers(workflow("merge-admission-caller"))["workflow_run"]["workflows"]
        self.assertEqual(len(listed), len(set(listed)), "a completion trigger is listed twice")
        self.assertEqual(set(listed), expected)

    def test_the_producer_is_not_started_by_its_own_completion(self):
        # ADR 0260 action 5 excludes the producer's own path: listing it would start a new
        # evaluation on the completion of every evaluation.
        doc = workflow("merge-admission-caller")
        own_names = {doc["name"], workflow("merge-admission")["name"]}
        self.assertFalse(own_names & set(triggers(doc)["workflow_run"]["workflows"]))

    def test_producer_runs_are_bounded_per_planned_head_without_cancelling_a_running_one(self):
        # ADR 0260, 2026-11-12 fan-out amendment: one running and one pending run per group.
        # The group is keyed exactly as the planner chooses between a narrowed head and a sweep.
        doc = workflow("merge-admission-caller")
        self.assertIn("concurrency", doc, "the caller declares no run-level concurrency")
        concurrency = doc["concurrency"]
        self.assertEqual(concurrency["group"], CALLER_CONCURRENCY_GROUP)
        self.assertIs(concurrency["cancel-in-progress"], False)

    def test_the_concurrency_key_is_the_head_the_plan_narrows_to_or_the_sweep(self):
        # A run whose pending successor is cancelled loses nothing only if both plan the same
        # work: the same head, or a sweep of every head. Evaluated against the plan job's own
        # EVENT_HEAD over the same events, so the key cannot drift from the planner.
        group = workflow("merge-admission-caller")["concurrency"]["group"]
        plan = workflow("merge-admission")["jobs"]["plan"]["steps"]
        event_head = next(step for step in plan if step.get("name") == "Plan the heads this run evaluates")["env"]["EVENT_HEAD"]
        head = "0123456789abcdef0123456789abcdef01234567"

        def completed(event):
            return {"github": {"event_name": "workflow_run", "sha": "f" * 40,
                               "event": {"workflow_run": {"event": event, "head_sha": head}}}}

        cases = {f"completed {event} run": (completed(event), head)
                 for event in ("pull_request", "pull_request_target", "pull_request_review")}
        cases |= {f"completed {event} run": (completed(event), "sweep")
                  for event in ("push", "workflow_dispatch", "schedule", "merge_group",
                                "pull_request_review_comment", "issue_comment", "dynamic")}
        cases |= {f"{event} event": ({"github": {"event_name": event, "sha": "f" * 40, "event": {}}}, "sweep")
                  for event in ("schedule", "workflow_dispatch")}
        empty_head = completed("pull_request")
        empty_head["github"]["event"]["workflow_run"]["head_sha"] = ""
        cases["completed pull_request run with an empty head"] = (empty_head, "sweep")
        for name, (context, key) in cases.items():
            with self.subTest(name):
                self.assertEqual(interpolate(group, context), f"merge-admission-caller-{key}")
                self.assertEqual(interpolate(event_head, context) or "sweep", key)


class TriggerWorkflowTests(unittest.TestCase):
    def test_the_trigger_carries_every_pull_request_and_review_event_of_action_five(self):
        on = triggers(workflow("merge-admission-trigger"))
        self.assertEqual(set(on), {"pull_request_target", "pull_request_review"})
        self.assertEqual(
            set(on["pull_request_target"]["types"]),
            {"opened", "synchronize", "reopened", "ready_for_review", "edited", "labeled", "unlabeled"},
        )
        self.assertEqual(set(on["pull_request_review"]["types"]), {"submitted", "edited", "dismissed"})

    def test_the_trigger_holds_no_credential_and_runs_no_pull_request_code(self):
        doc = workflow("merge-admission-trigger")
        self.assertEqual(doc["permissions"], {})
        text = (WORKFLOWS / "merge-admission-trigger.yml").read_text(encoding="utf-8")
        self.assertNotIn("secrets.", text)
        for job in doc["jobs"].values():
            self.assertNotIn("environment", job)
            self.assertIn(job.get("permissions", {}), ({}, None))
            for step in job["steps"]:
                self.assertNotIn("uses", step)



class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.doc = workflow("merge-admission")
        self.evaluate = self.doc["jobs"]["evaluate"]

    def test_evaluations_of_one_head_are_serialized_and_never_cancelled(self):
        concurrency = self.evaluate["concurrency"]
        self.assertIn("${{ matrix.head_sha }}", concurrency["group"])
        self.assertIn("${{ github.repository }}", concurrency["group"])
        self.assertIs(concurrency["cancel-in-progress"], False)
        self.assertNotIn("concurrency", self.doc)


    def step(self, job: str, name: str) -> dict:
        return next(step for step in self.doc["jobs"][job]["steps"] if step.get("name") == name)

    def test_the_assertion_runs_from_this_repository_at_the_executing_workflow_sha(self):
        revision = self.step("evaluate", "Resolve executing trusted workflow revision")
        self.assertEqual(revision["env"]["EXECUTING_WORKFLOW_SHA"], "${{ job.workflow_sha }}")
        checkout = self.step("evaluate", "Check out the canonical merge assertion")["with"]
        self.assertEqual(checkout["repository"], "Verjson/.github")
        self.assertEqual(checkout["ref"], "${{ steps.trusted-revision.outputs.sha }}")
        self.assertEqual(checkout["path"], ".gate-trust")
        self.assertEqual(
            set(checkout["sparse-checkout"].split()),
            {
                "scripts/assert-mergeable-head.sh",
                "scripts/ci-gate/independent-review-receipt.sh",
                "scripts/ci-gate/merge-admission.sh",
                "scripts/ci-gate/merge-admission-plan.sh",
            },
        )
        self.assertIs(checkout["persist-credentials"], False)

    def test_the_check_is_posted_with_the_ai_review_app_and_the_assertion_reads_with_the_job_token(self):
        mint = self.step("evaluate", "Mint the AI review App token for the merge-admission check")
        self.assertIs(mint.get("continue-on-error"), True)
        self.assertEqual(mint["with"]["private-key"], "${{ secrets.AI_REVIEW_APP_PRIVATE_KEY }}")
        self.assertEqual({key for key in mint["with"] if key.startswith("permission-")}, {"permission-checks"})
        self.assertEqual(mint["with"]["permission-checks"], "write")
        self.assertEqual(mint["with"]["repositories"], "${{ steps.target.outputs.name }}")
        evaluate = self.step("evaluate", "Evaluate the head and post merge-admission")
        self.assertEqual(evaluate["env"]["APP_TOKEN"], "${{ steps.app-token.outputs.token }}")
        self.assertEqual(evaluate["env"]["GH_TOKEN"], "${{ github.token }}")
        self.assertEqual(evaluate["env"]["HEAD_SHA"], "${{ matrix.head_sha }}")
        self.assertNotIn("continue-on-error", evaluate)
        # The job token reads; only the App writes the check.
        self.assertNotIn("write", self.evaluate["permissions"].values())

    def test_one_evaluation_answers_for_every_pull_request_at_its_head(self):
        # A check run belongs to the head, not to a pull request (ADR 0260), so the matrix
        # leg is one head and hands the producer every pull request the plan found there.
        evaluate = self.step("evaluate", "Evaluate the head and post merge-admission")
        self.assertEqual(evaluate["env"]["PR_NUMBERS"], "${{ join(matrix.prs, ' ') }}")
        self.assertEqual(
            evaluate["run"].strip().splitlines(),
            [
                'read -ra prs <<<"$PR_NUMBERS"',
                'bash .gate-trust/scripts/ci-gate/merge-admission.sh "$REPOSITORY" "$HEAD_SHA" "${prs[@]}"',
            ],
        )
        self.assertNotIn("PR_NUMBER", evaluate["env"])

    def test_only_a_completed_pull_request_run_narrows_the_plan_to_its_head(self):
        # A completed run started by a pull-request or review event names the head it ran
        # for. Any other completed run, such as the AI review gate dispatched on the default
        # branch, names a default-branch commit no pull request is at, so the plan sweeps.
        plan = self.step("plan", "Plan the heads this run evaluates")
        self.assertEqual(
            plan["env"]["EVENT_HEAD"],
            "${{ contains(fromJSON('[\"pull_request\",\"pull_request_target\",\"pull_request_review\"]'), "
            "github.event.workflow_run.event) && github.event.workflow_run.head_sha || '' }}",
        )
        self.assertIn('merge-admission-plan.sh "$REPOSITORY" "$EVENT_HEAD"', plan["run"])

    def test_the_key_is_admitted_only_through_the_main_only_environment_policy(self):
        policy = self.doc["jobs"]["app-key-policy"]
        self.assertEqual(policy["uses"], "./.github/workflows/app-key-environment.yml")
        self.assertEqual(policy["with"], {"role": "ai-review", "environment": "${{ inputs.ai_review_environment }}"})
        self.assertEqual(self.evaluate["environment"], "${{ inputs.ai_review_environment }}")
        self.assertIn("app-key-policy", self.evaluate["needs"])
        self.assertEqual(self.evaluate["if"], "${{ needs.app-key-policy.result == 'success' }}")

    def test_a_caller_wired_to_a_pull_request_event_evaluates_nothing(self):
        self.assertEqual(
            self.doc["jobs"]["plan"]["if"],
            "${{ github.event_name == 'workflow_run' || github.event_name == 'schedule' || github.event_name == 'workflow_dispatch' }}",
        )
        self.assertEqual(self.doc["jobs"]["app-key-policy"]["needs"], "plan")
        self.assertIn("plan", self.evaluate["needs"])

    def test_the_environment_policy_reads_a_scheduled_runs_default_branch_from_its_ref(self):
        # A schedule event carries no repository record, and always runs on the default
        # branch, so the policy reads the default branch from the run's ref there and only
        # there; every other event keeps reading the repository record.
        policy = workflow("app-key-environment")
        step = next(
            step for step in policy["jobs"]["validate"]["steps"]
            if step.get("name") == "Verify the caller's main-only role environment before key-job admission"
        )
        self.assertEqual(
            step["env"]["DEFAULT_BRANCH"],
            "${{ github.event.repository.default_branch || (github.event_name == 'schedule' && github.ref_name) || '' }}",
        )


if __name__ == "__main__":
    unittest.main()
