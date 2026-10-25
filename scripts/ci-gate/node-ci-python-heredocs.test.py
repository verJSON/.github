#!/usr/bin/env python3
"""Compile Python snippets embedded in canonical Node CI workflow heredocs."""
from __future__ import annotations

import re
import ast
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ROOT / ".github/workflows/node-ci.yml",
    ROOT / ".github/workflows/node-ci-protected.yml",
)
PROTECTED = ROOT / ".github/workflows/node-ci-protected.yml"
TRUSTED_HELPER = ROOT / "scripts/ci_build_artifacts.py"
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
PYTHON = re.compile(r"(?:^|\s)(?:/usr/bin/)?python(?:3(?:\.\d+)?)?(?:\s|$)")


def _mapping(node):
    return {key.value: value for key, value in node.value}


def _is_python_heredoc(command: str, body: str) -> bool:
    if PYTHON.search(command):
        return True
    return bool(
        re.search(r"\bcat\b.*(?:>|\s)(?:[^\s]*\.py)(?:\s|$)", command)
        or body.lstrip().startswith("#!/usr/bin/env python")
    )


def python_heredocs(path: Path):
    workflow = yaml.compose(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    jobs = _mapping(_mapping(workflow)["jobs"])
    found = []
    for job_name, job_node in jobs.items():
        steps = _mapping(job_node).get("steps")
        for step_index, step_node in enumerate(steps.value if steps else ()):
            step = _mapping(step_node)
            run_node = step.get("run")
            if run_node is None:
                continue
            lines = run_node.value.splitlines()
            index = 0
            while index < len(lines):
                match = HEREDOC.search(lines[index])
                if match is None:
                    index += 1
                    continue
                delimiter = match.group(2)
                end = index + 1
                while end < len(lines) and lines[end].strip() != delimiter:
                    end += 1
                if end == len(lines):
                    raise AssertionError(
                        f"{path}:{job_name}:step-{step_index}: unterminated {delimiter} heredoc"
                    )
                body = "\n".join(lines[index + 1 : end]) + "\n"
                command = lines[index]
                if _is_python_heredoc(command, body):
                    found.append((job_name, step_index, body))
                index = end + 1
    return found


class NodeCiPythonHeredocsTest(unittest.TestCase):
    def test_every_embedded_python_heredoc_compiles(self):
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow.name):
                snippets = python_heredocs(workflow)
                self.assertTrue(snippets, f"no Python heredocs found in {workflow}")
                for index, (job, step, source) in enumerate(snippets, 1):
                    with self.subTest(job=job, step=step, snippet=index):
                        compile(source, f"{workflow}:{job}:step-{step}:heredoc-{index}", "exec")

    def test_each_executor_uses_the_receipt_supervisor_for_candidate_entries(self):
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow.name):
                if workflow == PROTECTED:
                    candidate_source = TRUSTED_HELPER.read_text(encoding="utf-8")
                else:
                    snippets = python_heredocs(workflow)
                    candidate_source = next(
                        source
                        for _job, _step, source in snippets
                        if "def execute_candidate_entry(" in source
                        and "run_script_plan_with_receipts(" in source
                    )

                tree = ast.parse(candidate_source)
                callback = next(
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "execute_candidate_entry"
                )
                callback_source = ast.get_source_segment(candidate_source, callback)
                self.assertIsNotNone(callback_source)
                callback_tree = ast.parse(callback_source)
                self.assertTrue(
                    any(
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "Popen"
                        for node in ast.walk(callback_tree)
                    ),
                    "candidate process launch must stay inside the executor callback",
                )
                supervisor_calls = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and (
                        (isinstance(node.func, ast.Attribute) and node.func.attr == "run_script_plan_with_receipts")
                        or (isinstance(node.func, ast.Name) and node.func.id == "run_script_plan_with_receipts")
                    )
                ]
                self.assertTrue(supervisor_calls, "executor must use the receipt supervisor")
                self.assertTrue(
                    any(
                        any(
                            isinstance(argument, ast.Name)
                            and argument.id == "execute_candidate_entry"
                            for argument in call.args
                        )
                        for call in supervisor_calls
                    ),
                    "receipt supervisor must invoke the executor callback per entry",
                )

    def test_protected_candidate_runtime_uses_the_pinned_trusted_helper(self):
        workflow = yaml.safe_load(PROTECTED.read_text(encoding="utf-8"))
        steps = workflow["jobs"]["build-test"]["steps"]
        fetch_index = next(
            index for index, step in enumerate(steps)
            if step.get("name") == "Download pinned protected candidate helper"
        )
        candidate_index = next(
            index for index, step in enumerate(steps)
            if step.get("name") == "Run exact credentialless consumer script plan"
        )
        fetch = steps[fetch_index]
        candidate = steps[candidate_index]
        self.assertLess(fetch_index, candidate_index)
        self.assertEqual(
            "${{ fromJSON(toJSON(job)).workflow_sha }}",
            fetch["env"]["JOB_WORKFLOW_SHA"],
        )
        self.assertIn("${JOB_WORKFLOW_SHA}", fetch["run"])
        self.assertIn(
            'python3 -I "$RUNNER_TEMP/ci_build_artifacts.py" run-protected-candidate-plan &',
            candidate["run"],
        )


if __name__ == "__main__":
    unittest.main()
