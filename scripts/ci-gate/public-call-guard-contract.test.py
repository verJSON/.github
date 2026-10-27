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
PUBLIC_EVENTS = (
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "merge_group",
    "pull_request_target",
)
PUBLIC_EVENT = "(" + " || ".join(f"github.event_name == '{event}'" for event in PUBLIC_EVENTS) + ")"
PUBLIC_CALL = f"{PUBLIC_EVENT} && github.event.repository.private != true"
ALLOW_GUARD = (
    "(github.event_name != 'pull_request' && github.event_name != 'pull_request_review' "
    "&& github.event_name != 'pull_request_review_comment' && github.event_name != 'merge_group' "
    "&& github.event_name != 'pull_request_target' "
    "|| github.event.repository.private == true)"
)
SENTINEL_JOB = "reject-public-pull-request-caller"
SAFE_HOSTED_EXCEPTIONS = frozenset(
    {"app-key-environment.yml", "gate-rearm.yml"}
)
TRUSTED_GROUPS = BASELINE["trusted_concurrency_groups"]
PUBLIC_GROUPS = BASELINE.get("public_concurrency_groups", {})


def load_workflow(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def expression(value: object) -> str:
    value = str(value).strip()
    if value.startswith("${{") and value.endswith("}}"):
        value = value[3:-2].strip()
    value = re.sub(r"\s+", " ", value)
    return re.sub(r"\(\s+|\s+\)", lambda match: match.group().strip(), value)


def public_call_is_rejected(event_name: str, repository_private: object) -> bool:
    return event_name in PUBLIC_EVENTS and repository_private is not True


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

    def test_unknown_visibility_fails_closed_for_all_public_pull_request_events(self):
        cases = tuple(
            (event_name, repository_private, expected)
            for event_name in (*PUBLIC_EVENTS, "push", "workflow_dispatch")
            for repository_private, expected in ((False, event_name in PUBLIC_EVENTS), (None, event_name in PUBLIC_EVENTS), (True, False))
        )
        for event_name, repository_private, expected in cases:
            with self.subTest(event=event_name, private=repository_private):
                self.assertEqual(public_call_is_rejected(event_name, repository_private), expected)

    def test_public_calls_cannot_replace_trusted_pending_release_or_deployment(self):
        public_group = "format('public-rejected-{0}-{1}-{2}', github.repository, github.run_id, github.run_attempt)"
        for name, trusted_group in TRUSTED_GROUPS.items():
            with self.subTest(workflow=name):
                workflow = load_workflow(name)
                concurrency = workflow["concurrency"]
                workflow_public_group = PUBLIC_GROUPS.get(name, public_group)
                expected = f"{PUBLIC_CALL} && {workflow_public_group} || {trusted_group}"
                self.assertTrue(concurrency["group"].startswith("${{"))
                self.assertTrue(concurrency["group"].endswith("}}"))
                self.assertEqual(expression(concurrency["group"]), expected)
                self.assertEqual(concurrency["cancel-in-progress"], "false")


if __name__ == "__main__":
    unittest.main()
