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

    def test_every_candidate_entry_contains_its_process_launch_inside_the_loop(self):
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow.name):
                if workflow == PROTECTED:
                    helper_source = TRUSTED_HELPER.read_text(encoding="utf-8")
                    helper_tree = ast.parse(helper_source)
                    helper = next(
                        node
                        for node in helper_tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "run_protected_candidate_plan"
                    )
                    candidate = ast.get_source_segment(helper_source, helper)
                    self.assertIsNotNone(candidate)
                else:
                    snippets = python_heredocs(workflow)
                    candidate = next(
                        source for _job, _step, source in snippets
                        if "for index, (script_directory" in source or "for directory, name, unset_env" in source
                    )
                tree = ast.parse(candidate)
                launches_inside_plan_loop = []
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.For, ast.AsyncFor)):
                        continue
                    target_names = {
                        child.id for child in ast.walk(node.target) if isinstance(child, ast.Name)
                    }
                    if not {"name", "unset_env", "requires_services"}.issubset(target_names):
                        continue
                    for child in ast.walk(node):
                        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                            continue
                        if child.func.attr in {"Popen", "run"}:
                            launches_inside_plan_loop.append(child)
                self.assertTrue(launches_inside_plan_loop, "planned candidate process launch escaped its per-entry loop")

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
