#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "required_workflow_revision", ROOT / "scripts/required_workflow_revision.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PREVIOUS = "a" * 40
TARGET = "b" * 40
WORKFLOW_PATH = ".github/workflows/required.yml"


class ContractError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


class AdmitPredecessorTest(unittest.TestCase):
    def admit(self, *, status="ahead", workflow=b"generated\n", regenerated=b"generated\n"):
        reads = []

        def compare(base, head):
            self.assertEqual((PREVIOUS, TARGET), (base, head))
            return status

        def read_file(path, ref):
            reads.append((path, ref))
            return workflow

        def regenerate(revision, workflow_bytes):
            self.assertEqual(PREVIOUS, revision)
            self.assertEqual(workflow, workflow_bytes)
            return regenerated

        MODULE.admit_predecessor(
            PREVIOUS, TARGET,
            compare=compare, read_file=read_file, regenerate=regenerate,
            workflow_path=WORKFLOW_PATH, require=require,
        )
        return reads

    def test_a_revision_that_is_not_an_ancestor_of_the_target_is_refused_unread(self):
        for status in ("behind", "diverged", None, ""):
            reads = []
            with self.subTest(status=status), self.assertRaisesRegex(
                ContractError, "not an ancestor of the rotation target"
            ):
                reads = self.admit(status=status)
            self.assertEqual([], reads)

    def test_a_revision_whose_workflow_is_not_its_generator_output_is_refused(self):
        with self.assertRaisesRegex(ContractError, "not its generator's output"):
            self.admit(workflow=b"hand edited\n", regenerated=b"generated\n")

    def test_a_forward_generated_revision_is_admitted_from_its_own_bytes(self):
        for status in ("ahead", "identical"):
            with self.subTest(status=status):
                self.assertEqual(
                    [(WORKFLOW_PATH, PREVIOUS)], self.admit(status=status)
                )


def workflows_rule(*shas):
    return {"type": "workflows", "parameters": {"workflows": [
        {"path": WORKFLOW_PATH, "repository_id": 1, "ref": "refs/heads/main", "sha": sha}
        for sha in shas
    ]}}


class BoundWorkflowShaTest(unittest.TestCase):
    def test_the_single_bound_revision_is_read_from_the_live_ruleset(self):
        freshness = {"type": "required_status_checks", "parameters": {}}
        self.assertEqual(PREVIOUS, MODULE.bound_workflow_sha(
            {"rules": [workflows_rule(PREVIOUS), freshness]}, require
        ))

    def test_absent_ambiguous_or_mutable_bindings_fail_closed(self):
        cases = (
            None,
            [],
            {},
            {"rules": None},
            {"rules": []},
            {"rules": ["workflows"]},
            {"rules": [{"type": "workflows"}]},
            {"rules": [{"type": "workflows", "parameters": None}]},
            {"rules": [workflows_rule()]},
            {"rules": [workflows_rule(PREVIOUS, TARGET)]},
            {"rules": [workflows_rule(PREVIOUS), workflows_rule(PREVIOUS)]},
            {"rules": [{"type": "workflows", "parameters": {"workflows": ["x"]}}]},
            {"rules": [workflows_rule("main")]},
            {"rules": [workflows_rule("A" * 40)]},
            {"rules": [workflows_rule("a" * 39)]},
            {"rules": [workflows_rule("a" * 40 + "\n")]},
            {"rules": [workflows_rule(None)]},
        )
        for ruleset in cases:
            with self.subTest(ruleset=ruleset), self.assertRaisesRegex(
                ContractError, "live ruleset"
            ):
                MODULE.bound_workflow_sha(ruleset, require)


if __name__ == "__main__":
    unittest.main()
