#!/usr/bin/env python3
"""Pin public reusable-workflow admission and routing for ADR 0235."""
from __future__ import annotations

import json
import re
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
BASELINE = json.loads(
    (ROOT / "scripts" / "ci-gate" / "fixtures" / "public-pr-workflow-policy-baseline.json").read_text()
)
PUBLIC_EVENT = "(github.event_name == 'pull_request' || github.event_name == 'pull_request_target')"
PUBLIC_CALL = f"{PUBLIC_EVENT} && github.event.repository.private != true"
ALLOW_GUARD = (
    "(github.event_name != 'pull_request' && github.event_name != 'pull_request_target' "
    "|| github.event.repository.private == true)"
)
SENTINEL_JOB = "reject-public-pull-request-caller"
SAFE_HOSTED_EXCEPTIONS = frozenset(
    {"app-key-environment.yml", "gate-rearm.yml"}
)
TRUSTED_GROUPS = {
    "changelog-release.yml": "format('changelog-release-{0}', github.repository)",
    "container-release.yml": "format('container-release-{0}', github.repository)",
    "container-deployment.yml": (
        "format('runner-deployment-{0}-{1}', github.repository, inputs.fleet-selector)"
    ),
}


def load_workflow(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def expression(value: object) -> str:
    value = str(value).strip()
    if value.startswith("${{") and value.endswith("}}"):
        value = value[3:-2].strip()
    return re.sub(r"\s+", " ", value)


def public_call_is_rejected(event_name: str, repository_private: object) -> bool:
    return event_name in {"pull_request", "pull_request_target"} and repository_private is not True


class PublicCallGuardContractTest(unittest.TestCase):
    def test_public_code_workflows_route_to_hosted_before_the_original_selector(self):
        for name, baseline in BASELINE["allowed_public_workflows"].items():
            if name in SAFE_HOSTED_EXCEPTIONS:
                continue
            with self.subTest(workflow=name):
                jobs = load_workflow(name)["jobs"]
                self.assertEqual(set(jobs), set(baseline["jobs"]))
                for job_id, original in baseline["jobs"].items():
                    with self.subTest(job=job_id):
                        if "uses" in original:
                            self.assertEqual(jobs[job_id]["uses"], original["uses"])
                            continue
                        current = jobs[job_id]["runs-on"]
                        if original["runs-on"] == "ubuntu-24.04":
                            self.assertEqual(current, "ubuntu-24.04")
                            continue
                        expected = (
                            f"{PUBLIC_EVENT} && github.event.repository.private != true "
                            f"&& 'ubuntu-24.04' || ({expression(original['runs-on'])})"
                        )
                        self.assertEqual(expression(current), expected)

    def test_safe_hosted_control_plane_exceptions_are_not_denied(self):
        for name in SAFE_HOSTED_EXCEPTIONS:
            with self.subTest(workflow=name):
                baseline_jobs = BASELINE["allowed_public_workflows"][name]["jobs"]
                jobs = load_workflow(name)["jobs"]
                self.assertEqual(set(jobs), set(baseline_jobs))
                self.assertNotIn(SENTINEL_JOB, jobs)
                for job_id, baseline in baseline_jobs.items():
                    with self.subTest(job=job_id):
                        self.assertEqual(jobs[job_id].get("uses"), baseline.get("uses"))

    def test_public_calls_are_failed_and_every_existing_job_keeps_its_condition(self):
        for name, baseline in BASELINE["rejected_public_workflows"].items():
            with self.subTest(workflow=name):
                jobs = load_workflow(name)["jobs"]
                self.assertEqual(set(jobs), set(baseline["jobs"]) | {SENTINEL_JOB})

                sentinel = jobs[SENTINEL_JOB]
                self.assertEqual(expression(sentinel["if"]), PUBLIC_CALL)
                self.assertEqual(sentinel["runs-on"], "ubuntu-24.04")
                self.assertEqual(sentinel.get("permissions"), {})
                self.assertNotIn("uses", sentinel)
                self.assertTrue(
                    any(re.search(r"\bexit\s+[1-9][0-9]*\b", str(step.get("run", ""))) for step in sentinel["steps"]),
                    "the rejection sentinel must fail the reusable workflow",
                )

                for job_id, original in baseline["jobs"].items():
                    with self.subTest(job=job_id):
                        expected = ALLOW_GUARD
                        if original["if"] is not None:
                            expected += f" && ({expression(original['if'])})"
                        self.assertEqual(expression(jobs[job_id].get("if")), expected)

    def test_unknown_visibility_fails_closed_for_both_pull_request_events(self):
        cases = (
            ("pull_request", False, True),
            ("pull_request", None, True),
            ("pull_request", True, False),
            ("pull_request_target", False, True),
            ("pull_request_target", None, True),
            ("pull_request_target", True, False),
            ("push", None, False),
            ("workflow_dispatch", False, False),
        )
        for event_name, repository_private, expected in cases:
            with self.subTest(event=event_name, private=repository_private):
                self.assertEqual(public_call_is_rejected(event_name, repository_private), expected)

    def test_rejected_calls_cannot_replace_trusted_pending_release_or_deployment(self):
        public_group = "format('public-rejected-{0}-{1}-{2}', github.repository, github.run_id, github.run_attempt)"
        for name, trusted_group in TRUSTED_GROUPS.items():
            with self.subTest(workflow=name):
                workflow = load_workflow(name)
                concurrency = workflow["concurrency"]
                expected = f"{PUBLIC_CALL} && {public_group} || {trusted_group}"
                self.assertEqual(expression(concurrency["group"]), expected)
                self.assertEqual(concurrency["cancel-in-progress"], "false")


if __name__ == "__main__":
    unittest.main()
