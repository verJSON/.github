#!/usr/bin/env python3
"""Pin #1817 / ADR 0238 (2026-10-23 amendment): pull requests fail fast, trunk keeps the inventory.

Behavioral: the workflow's own `if:` and `fail-fast:` expressions are evaluated per event,
and the real `shell-tests` aggregate script is run against each gate outcome. The cancel
job from the issue is deliberately not adopted, so no reusable CI workflow may grant
`actions: write` or call the run cancel endpoint, and AI review/arm/rearm workflows never
opt into matrix fail-fast.
"""
from itertools import product
from pathlib import Path
import os
import re
import subprocess
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
GENERATOR = ROOT / "scripts" / "gen-node-ci-caller.py"
EVENTS = ("pull_request", "pull_request_target", "push", "schedule", "workflow_dispatch", "merge_group")
HEAVY_JOBS = ("shell-test-groups", "hosted-compatibility-tests")
RESULTS = ("success", "failure", "skipped", "cancelled")


def load(name):
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def evaluate(expression, *, event, draft=False, heavy="true", results=None, cancelled=False, command="preview"):
    """Evaluate the small GitHub expression subset these workflows use."""
    body = re.fullmatch(r"\s*\$\{\{(.*)\}\}\s*", expression, re.S).group(1)
    values = {"scope": results["change-scope"], "docs": results["docs-contracts"], "iso": results["runner-isolation"]}
    translated = body
    translated = translated.replace("!cancelled()", "(not CANCELLED)")
    translated = translated.replace("github.event_name", "EVENT").replace("inputs.pulumi-command", "COMMAND")
    translated = translated.replace("!github.event.pull_request.draft", "(not DRAFT)")
    translated = translated.replace("needs.change-scope.outputs.heavy", "HEAVY")
    for job, key in (("change-scope", "scope"), ("docs-contracts", "docs"), ("runner-isolation", "iso")):
        translated = translated.replace(f"needs.{job}.result", f"R_{key}")
    translated = translated.replace("&&", " and ").replace("||", " or ").replace("==", " == ").replace("!=", " != ")
    leftovers = re.sub(r"'[a-z_]*'|\b(and|or|not|true|false)\b|EVENT|COMMAND|DRAFT|HEAVY|CANCELLED|R_\w+|[()=!\s]", "", translated)
    if leftovers:
        raise AssertionError(f"unsupported expression syntax {leftovers!r} in {body!r}")
    scope = {"EVENT": event, "COMMAND": command, "DRAFT": draft, "HEAVY": heavy, "CANCELLED": cancelled,
             **{f"R_{k}": v for k, v in values.items()}, "true": True, "false": False}
    return bool(eval(translated, {"__builtins__": {}}, scope))  # noqa: S307 - validated token set above


def gates(scope="success", docs="success", iso="success"):
    return {"change-scope": scope, "docs-contracts": docs, "runner-isolation": iso}


class HeavyJobGatingTest(unittest.TestCase):
    def runs(self, job, **kwargs):
        return evaluate(load("actions-ci.yml")["jobs"][job]["if"], **kwargs)

    def test_pull_request_waits_for_the_cheap_gates_to_succeed(self):
        for job, failing in product(HEAVY_JOBS, ("docs-contracts", "runner-isolation")):
            for result in ("failure", "cancelled", "skipped"):
                with self.subTest(job=job, failing=failing, result=result):
                    outcome = gates(**{"docs" if failing == "docs-contracts" else "iso": result})
                    self.assertFalse(self.runs(job, event="pull_request", results=outcome))

    def test_pull_request_with_green_gates_runs_the_heavy_jobs(self):
        for job in HEAVY_JOBS:
            self.assertTrue(self.runs(job, event="pull_request", results=gates()))

    def test_every_other_event_still_runs_the_full_inventory_after_a_failed_gate(self):
        for job, event in product(HEAVY_JOBS, [e for e in EVENTS if e != "pull_request"]):
            for outcome in (gates(docs="failure"), gates(iso="failure"), gates(docs="failure", iso="failure")):
                with self.subTest(job=job, event=event, outcome=outcome):
                    self.assertTrue(self.runs(job, event=event, results=outcome))

    def test_exclusions_hold_on_every_event(self):
        for job, event in product(HEAVY_JOBS, EVENTS):
            with self.subTest(job=job, event=event):
                self.assertFalse(self.runs(job, event=event, results=gates(), heavy="false"))
                self.assertFalse(self.runs(job, event=event, results=gates(scope="failure")))
                self.assertFalse(self.runs(job, event=event, results=gates(), cancelled=True))
        for job in HEAVY_JOBS:
            self.assertFalse(self.runs(job, event="pull_request", results=gates(), draft=True))

    def test_the_gates_do_not_wait_on_the_heavy_jobs(self):
        jobs = load("actions-ci.yml")["jobs"]
        for gate in ("docs-contracts", "runner-isolation"):
            self.assertNotIn("needs", jobs[gate])


class AggregateStaysRequiredTest(unittest.TestCase):
    def aggregate_status(self, *, docs, iso, groups, compat, heavy="true"):
        step = load("actions-ci.yml")["jobs"]["shell-tests"]["steps"][0]
        env = {"PATH": os.environ["PATH"], "SCOPE_RESULT": "success", "HEAVY": heavy, "GROUP_RESULT": groups,
               "DOCS_RESULT": docs, "COMPATIBILITY_RESULT": compat, "COLLISION_RESULT": "success",
               "ISOLATION_RESULT": iso}
        return subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True).returncode

    def test_a_gate_failure_that_skips_the_heavy_jobs_still_fails_the_required_context(self):
        self.assertNotEqual(self.aggregate_status(docs="failure", iso="success", groups="skipped", compat="skipped"), 0)
        self.assertNotEqual(self.aggregate_status(docs="success", iso="failure", groups="skipped", compat="skipped"), 0)

    def test_heavy_jobs_skipped_by_ordering_alone_never_read_as_green(self):
        self.assertNotEqual(self.aggregate_status(docs="success", iso="success", groups="skipped", compat="skipped"), 0)

    def test_a_fully_successful_run_passes(self):
        self.assertEqual(self.aggregate_status(docs="success", iso="success", groups="success", compat="success"), 0)

    def test_a_documentation_only_diff_passes_with_the_heavy_jobs_skipped(self):
        self.assertEqual(
            self.aggregate_status(docs="success", iso="success", groups="skipped", compat="skipped", heavy="false"), 0)


class MatrixFailFastTest(unittest.TestCase):
    CELLS = (("actions-ci.yml", "shell-test-groups"), ("pulumi-ci.yml", "preview"))

    def test_matrix_fails_fast_on_pull_request_only(self):
        for name, job in self.CELLS:
            expression = load(name)["jobs"][job]["strategy"]["fail-fast"]
            for event in EVENTS:
                with self.subTest(workflow=name, event=event):
                    got = evaluate(expression, event=event, results=gates())
                    self.assertEqual(got, event == "pull_request")

    def test_pulumi_up_and_refresh_are_never_cut_short_by_a_sibling_stack(self):
        expression = load("pulumi-ci.yml")["jobs"]["preview"]["strategy"]["fail-fast"]
        for command in ("up", "refresh"):
            self.assertFalse(evaluate(expression, event="pull_request", results=gates(), command=command))

    def test_publishing_matrices_are_left_alone(self):
        for name in ("container-candidate.yml", "container-candidate-publish.yml"):
            text = (WORKFLOWS / name).read_text()
            self.assertNotIn("fail-fast: true", text)
            self.assertNotIn("fail-fast: ${{", text)


class CancelJobNotAdoptedTest(unittest.TestCase):
    CI = ("node-ci.yml", "node-ci-protected.yml", "helm-ci.yml", "pulumi-ci.yml", "ui-ci.yml", "actions-ci.yml")
    AI_REVIEW = ("ai-review-merge.yml", "ai-review-label-rearm.yml", "ai-review-lifecycle-rearm.yml", "gate-rearm.yml")

    def test_no_ci_workflow_can_cancel_a_run(self):
        for name in self.CI:
            text = (WORKFLOWS / name).read_text()
            with self.subTest(workflow=name):
                self.assertIsNone(re.search(r"actions:\s*write", text))
                self.assertIsNone(re.search(r"actions/runs/[^\s\"']*/cancel|gh run cancel", text))

    def test_ai_review_workflows_never_opt_into_matrix_fail_fast(self):
        for name in self.AI_REVIEW:
            with self.subTest(workflow=name):
                self.assertIsNone(re.search(r"fail-fast:\s*(true|\$\{\{)", (WORKFLOWS / name).read_text()))


class GeneratedCallerSupersessionTest(unittest.TestCase):
    def test_default_generated_caller_carries_the_documented_concurrency_snippet_once(self):
        out = subprocess.run([sys.executable, str(GENERATOR), "a" * 40], capture_output=True, text=True, check=True).stdout
        docs = (ROOT / "docs" / "node-workflows.md").read_text()
        snippet = re.search(r"```yaml\n(concurrency:.*?)```", docs, re.S).group(1)
        self.assertEqual(out.count(snippet), 1)
        self.assertEqual(yaml.safe_load(out)["concurrency"]["cancel-in-progress"],
                         "${{ github.event_name == 'pull_request' }}")


if __name__ == "__main__":
    unittest.main()
