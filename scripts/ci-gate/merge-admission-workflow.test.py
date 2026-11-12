#!/usr/bin/env python3
"""Contract tests for the merge-admission producer workflows (ADR 0260, #1990, #2031).

The producer runs entirely outside the evaluated head's workflow-run and check-run
inventories (ADR 0260, action 3): it is started only by events whose runs belong to the
default-branch commit, while a short keyless trigger workflow carries the pull-request
events and completes before the producer starts.
"""

from __future__ import annotations

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

# Events whose runs belong to the pull request's head, and so to the inventory the
# assertion reads (ADR 0229 rule 2, Gate C).
HEAD_EVENTS = {"pull_request", "pull_request_target", "pull_request_review", "pull_request_review_comment", "merge_group"}


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / f"{name}.yml").read_text(encoding="utf-8"))


def triggers(doc: dict) -> dict:
    return doc.get("on", doc.get(True))


class CallerTriggerTests(unittest.TestCase):
    def test_the_producer_starts_only_from_default_branch_events(self):
        on = triggers(workflow("merge-admission-caller"))
        self.assertEqual(set(on), {"workflow_run", "schedule", "workflow_dispatch"})
        self.assertFalse(set(on) & HEAD_EVENTS)
        self.assertEqual(on["workflow_run"]["types"], ["completed"])
        self.assertIn("Merge admission trigger", on["workflow_run"]["workflows"])

    def test_the_periodic_sweep_runs_at_most_fifteen_minutes_apart(self):
        schedules = triggers(workflow("merge-admission-caller"))["schedule"]
        self.assertEqual(len(schedules), 1)
        minute, *rest = schedules[0]["cron"].split()
        self.assertEqual(rest, ["*", "*", "*", "*"])
        self.assertRegex(minute, r"^\*/([1-9]|1[0-5])$")



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
            {"scripts/assert-mergeable-head.sh", "scripts/ci-gate/independent-review-receipt.sh", "scripts/ci-gate/merge-admission.sh"},
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
        self.assertEqual(evaluate["env"]["PR_NUMBER"], "${{ matrix.pr }}")
        self.assertEqual(evaluate["env"]["HEAD_SHA"], "${{ matrix.head_sha }}")
        self.assertEqual(
            evaluate["run"].strip(),
            'bash .gate-trust/scripts/ci-gate/merge-admission.sh "$REPOSITORY" "$PR_NUMBER" "$HEAD_SHA"',
        )
        self.assertNotIn("continue-on-error", evaluate)
        # The job token reads; only the App writes the check.
        self.assertNotIn("write", self.evaluate["permissions"].values())

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
