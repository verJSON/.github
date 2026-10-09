#!/usr/bin/env python3
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
EXPECTED_REVIEW_TYPES = {
    "opened",
    "reopened",
    "synchronize",
    "ready_for_review",
    "converted_to_draft",
}


def load_workflow(name):
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def pull_request_types(workflow):
    trigger = workflow["on"]["pull_request"]
    if isinstance(trigger, str):
        return set()
    return set(trigger.get("types", []))


def condition(workflow, job_name):
    return str(workflow["jobs"][job_name].get("if", ""))


class PullRequestCiTierTests(unittest.TestCase):
    def test_actions_ci_runs_fast_scope_checks_but_skips_heavy_jobs_for_drafts(self):
        workflow = load_workflow("actions-ci.yml")
        jobs = workflow["jobs"]

        self.assertTrue(EXPECTED_REVIEW_TYPES <= pull_request_types(workflow))
        self.assertIn("push", workflow["on"])
        self.assertIn(
            "github.event.pull_request.number || github.ref",
            workflow["concurrency"]["group"],
        )
        self.assertIn(
            "github.ref != 'refs/heads/main'",
            workflow["concurrency"]["cancel-in-progress"],
        )
        self.assertEqual(condition(workflow, "change-scope"), "")
        self.assertEqual(condition(workflow, "docs-contracts"), "")
        self.assertEqual(
            condition(workflow, "adr-number-collision"),
            "github.event_name == 'pull_request'",
        )

        for job_name in (
            "shell-test-groups",
            "hosted-compatibility-tests",
            "shell-tests",
        ):
            self.assertIn("!github.event.pull_request.draft", condition(workflow, job_name), job_name)

        self.assertIn("needs.change-scope.outputs.heavy == 'true'", condition(workflow, "shell-test-groups"))
        self.assertIn("needs.change-scope.outputs.heavy == 'true'", condition(workflow, "hosted-compatibility-tests"))
        self.assertIn("always()", condition(workflow, "shell-tests"))

    def test_cli_required_workflow_keeps_admission_fast_and_skips_full_ci_for_drafts(self):
        workflow = load_workflow("cli-projects-package-surface-required.yml")

        self.assertTrue(EXPECTED_REVIEW_TYPES <= pull_request_types(workflow))
        self.assertIn(
            "github.event.pull_request.number || github.ref",
            workflow["concurrency"]["group"],
        )
        self.assertIn(
            "github.event_name == 'pull_request'",
            workflow["concurrency"]["cancel-in-progress"],
        )
        self.assertEqual(
            condition(workflow, "admission"),
            "github.repository == 'Verjson/verjson-cli-projects'",
        )
        for job_name in ("ci", "ci-node-floor", "package-surface"):
            self.assertIn("!github.event.pull_request.draft", condition(workflow, job_name), job_name)

    def test_candidate_validation_skips_drafts_without_gating_manual_publication(self):
        workflow = load_workflow("container-candidate-reusable-contract.yml")

        self.assertTrue(EXPECTED_REVIEW_TYPES <= pull_request_types(workflow))
        self.assertIn(
            "github.event.pull_request.number || github.ref",
            workflow["concurrency"]["group"],
        )
        self.assertIn(
            "github.event_name == 'pull_request'",
            workflow["concurrency"]["cancel-in-progress"],
        )
        self.assertIn("!github.event.pull_request.draft", condition(workflow, "validate"))
        self.assertIn("github.event_name == 'workflow_dispatch'", condition(workflow, "publish"))
        self.assertIn(
            "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)",
            condition(workflow, "publish"),
        )

    def test_fast_workflows_remain_active_for_drafts(self):
        for name in (
            "actionlint.yml",
            "actionlint-reusable-contract.yml",
            "authn-type-surface-required.yml",
        ):
            workflow = load_workflow(name)
            self.assertNotIn("draft", str(workflow["jobs"]), name)

    def test_tier_policy_documents_current_workflow_classification(self):
        documentation = (ROOT / "docs" / "ci-workflow-tiers.md").read_text(
            encoding="utf-8"
        )
        for expected in (
            "Tier 1",
            "Tier 2",
            "change-scope",
            "docs-contracts",
            "adr-number-collision",
            "shell-test-groups",
            "CLI projects required package surface",
            "container candidate reusable-call contract",
            "ready_for_review",
        ):
            self.assertIn(expected, documentation)


if __name__ == "__main__":
    unittest.main()
