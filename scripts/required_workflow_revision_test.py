#!/usr/bin/env python3
import importlib.util
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import yaml


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


class SecurityFloorTest(unittest.TestCase):
    FLOOR = "21eb48e713bd8304951fb7b26a900d08359af900"
    PROTECTED = "Verjson/.github/.github/workflows/node-ci-protected.yml@"

    def workflow(self, *uses):
        return yaml.safe_dump({"jobs": {
            f"lane-{index}": {"uses": value} for index, value in enumerate(uses)
        } | {"steps-only": {"runs-on": "ubuntu-24.04", "steps": [{"run": "true"}]}}})

    def check(self, text, statuses, on_main=None):
        compared = []

        def compare(base, head):
            compared.append((base, head))
            if head == "main":
                return (on_main or {}).get(base, "ahead")
            return statuses[head]

        MODULE.require_security_floor(text, compare=compare, require=require)
        return compared

    def test_every_protected_lane_pin_must_contain_the_isolation_fixes(self):
        text = self.workflow(self.PROTECTED + PREVIOUS, self.PROTECTED + TARGET)
        self.assertEqual(
            [(self.FLOOR, PREVIOUS), (PREVIOUS, "main"), (self.FLOOR, TARGET), (TARGET, "main")],
            self.check(text, {PREVIOUS: "ahead", TARGET: "identical"}),
        )
        for stale in ("behind", "diverged", None):
            with self.subTest(stale=stale), self.assertRaisesRegex(
                ContractError, "predates the protected-lane security floor"
            ):
                self.check(text, {PREVIOUS: "ahead", TARGET: stale})

    def test_a_fork_commit_descending_from_the_floor_is_not_a_canonical_pin(self):
        # A commit in the fork network can descend from the floor while never having
        # been reviewed onto protected main; GitHub resolves it by SHA all the same.
        text = self.workflow(self.PROTECTED + PREVIOUS)
        for status in ("diverged", "behind", None):
            with self.subTest(status=status), self.assertRaisesRegex(
                ContractError, "not reachable from protected main"
            ):
                self.check(text, {PREVIOUS: "ahead"}, on_main={PREVIOUS: status})

    def test_a_workflow_without_an_immutable_protected_lane_pin_fails_closed(self):
        for text in (
            "",
            "[]",
            "jobs: []",
            "{jobs: {lane: []}}",
            "jobs: {lane: {uses: [x]}}",
            ": not yaml",
            self.workflow("Verjson/.github/.github/workflows/node-ci.yml@" + PREVIOUS),
            self.workflow(self.PROTECTED + "main"),
            self.workflow(self.PROTECTED + "A" * 40),
            self.workflow(self.PROTECTED + PREVIOUS, self.PROTECTED + "v4"),
            self.workflow(self.PROTECTED + PREVIOUS, "./.github/workflows/node-ci-protected.yml"),
            self.workflow(
                self.PROTECTED + PREVIOUS,
                "attacker/.github/.github/workflows/node-ci-protected.yml@" + PREVIOUS,
            ),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(
                ContractError, "node-ci-protected|required workflow"
            ):
                self.check(text, {PREVIOUS: "ahead"})


class AuditLiveRevisionTest(unittest.TestCase):
    FLOOR = "21eb48e713bd8304951fb7b26a900d08359af900"
    VERIFIER = "scripts/verifier.py"

    def audit(self, *, on_main="ahead", live=None, main=None, floor="ahead", pin_on_main="ahead"):
        workflow = yaml.safe_dump({"jobs": {"lane": {
            "uses": "Verjson/.github/.github/workflows/node-ci-protected.yml@" + TARGET,
        }}}).encode()
        trees = {
            PREVIOUS: {WORKFLOW_PATH: workflow, self.VERIFIER: b"v1\n"} | (live or {}),
            "main": {WORKFLOW_PATH: workflow, self.VERIFIER: b"v1\n"} | (main or {}),
        }

        def compare(base, head):
            if (base, head) == (PREVIOUS, "main"):
                return on_main
            if (base, head) == (TARGET, "main"):
                return pin_on_main
            self.assertEqual((self.FLOOR, TARGET), (base, head))
            return floor

        MODULE.audit_live_revision(
            PREVIOUS,
            compare=compare,
            read_file=lambda path, ref: trees[ref][path],
            workflow_path=WORKFLOW_PATH,
            runtime_paths=(WORKFLOW_PATH, self.VERIFIER),
            require=require,
        )

    def test_a_live_revision_running_the_reviewed_main_bytes_passes(self):
        self.audit()

    def test_a_live_revision_outside_protected_main_fails(self):
        for status in ("behind", "diverged", None):
            with self.subTest(status=status), self.assertRaisesRegex(
                ContractError, "outside protected main"
            ):
                self.audit(on_main=status)

    def test_any_runtime_byte_drift_from_main_reports_a_pending_rotation(self):
        for drift in ({self.VERIFIER: b"v2\n"}, {WORKFLOW_PATH: b"jobs: {}\n"}):
            with self.subTest(drift=list(drift)), self.assertRaisesRegex(
                ContractError, "rotation pending"
            ):
                self.audit(main=drift)

    def test_a_live_revision_below_the_security_floor_fails(self):
        with self.assertRaisesRegex(ContractError, "security floor"):
            self.audit(floor="behind")

    def test_a_live_revision_pinning_a_fork_commit_fails(self):
        with self.assertRaisesRegex(ContractError, "not reachable from protected main"):
            self.audit(pin_on_main="diverged")


class CanonicalReadTest(unittest.TestCase):
    def test_only_main_or_an_immutable_sha_reaches_a_github_url(self):
        calls = []

        def gh_json(path):
            calls.append(path)
            return {"status": "ahead"}

        self.assertEqual("ahead", MODULE.canonical_compare(
            PREVIOUS, "main", gh_json=gh_json, require=require))
        self.assertEqual(["repos/Verjson/.github/compare/" + PREVIOUS + "...main"], calls)
        for ref in ("HEAD", "main?x=1", "../main", "A" * 40, PREVIOUS + "\n", None, 7):
            with self.subTest(ref=ref), mock.patch("subprocess.run") as run:
                with self.assertRaisesRegex(ContractError, "canonical revision is invalid"):
                    MODULE.canonical_compare(ref, TARGET, gh_json=gh_json, require=require)
                with self.assertRaisesRegex(ContractError, "canonical revision is invalid"):
                    MODULE.canonical_file(WORKFLOW_PATH, ref, require)
                run.assert_not_called()
        self.assertEqual(1, len(calls))

    def test_a_malformed_compare_response_is_not_forward(self):
        for response in ([], None, {"status": ["ahead"]}, {}):
            with self.subTest(response=response):
                status = MODULE.canonical_compare(
                    PREVIOUS, TARGET, gh_json=lambda _path: response, require=require
                )
                self.assertNotIn(status, MODULE.FORWARD)

    def test_an_unreadable_canonical_file_fails_closed(self):
        failed = mock.Mock(returncode=1, stdout=b"partial")
        with mock.patch("subprocess.run", return_value=failed), self.assertRaisesRegex(
            ContractError, "canonical file unreadable"
        ):
            MODULE.canonical_file(WORKFLOW_PATH, PREVIOUS, require)


class RunGeneratorTest(unittest.TestCase):
    def test_historical_generator_runs_from_its_own_bytes_without_inherited_secrets(self):
        files = {
            "scripts/gen.py": (
                b"import json, os, sys\n"
                b"from sibling import VALUE\n"
                # LC_CTYPE is set by the interpreter itself (PEP 538 locale coercion).
                b"print(VALUE, open(sys.argv[1]).read().strip(),"
                b" json.dumps({k: v for k, v in os.environ.items() if k != 'LC_CTYPE'}, sort_keys=True))\n"
            ),
            "scripts/sibling.py": b"VALUE = 'sibling'\n",
            "config/input.json": b"config\n",
        }
        inherited = {
            "GH_TOKEN": "secret", "PYTHONPATH": "/x",
            "LD_LIBRARY_PATH": "/attacker/lib", "LD_PRELOAD": "/attacker/x.so",
        }
        with mock.patch.dict(os.environ, inherited):
            output = MODULE.run_generator(
                files, ["scripts/gen.py", "config/input.json"], require
            )
        value, config, environment = output.decode().rstrip("\n").split(" ", 2)
        self.assertEqual(("sibling", "config"), (value, config))
        observed = json.loads(environment)
        # Exactly what the interpreter needs to start, and nothing the caller held.
        self.assertEqual(MODULE.generator_environment(), observed)
        self.assertFalse(set(observed) & {"GH_TOKEN", "PYTHONPATH", "LD_PRELOAD"})
        self.assertNotEqual("/attacker/lib", observed.get("LD_LIBRARY_PATH"))

    def test_a_failing_or_escaping_generator_fails_closed(self):
        cases = (
            ({"scripts/gen.py": b"raise SystemExit(2)\n"}, ["scripts/gen.py"]),
            ({"../escape.py": b"print(1)\n"}, ["../escape.py"]),
            ({"/abs.py": b"print(1)\n"}, ["/abs.py"]),
        )
        for files, argv in cases:
            with self.subTest(files=list(files)), self.assertRaisesRegex(
                ContractError, "generator"
            ):
                MODULE.run_generator(files, argv, require)

    def test_a_failing_generator_reports_its_own_stderr(self):
        with self.assertRaisesRegex(ContractError, "historical generator failed: .*boom"):
            MODULE.run_generator(
                {"scripts/gen.py": b"import sys\nsys.exit('boom')\n"}, ["scripts/gen.py"], require
            )


class GeneratorEnvironmentTest(unittest.TestCase):
    """A shared-libpython interpreter (setup-python's builds) cannot start without its
    library directory; that directory comes from the interpreter, never the caller."""

    def config(self, shared, soname="libpython3.12.so.1.0"):
        return {"Py_ENABLE_SHARED": shared, "INSTSONAME": soname}.get

    def test_a_shared_interpreter_gets_only_its_own_library_directory(self):
        with tempfile.TemporaryDirectory() as prefix:
            (Path(prefix) / "lib").mkdir()
            (Path(prefix) / "lib/libpython3.12.so.1.0").write_bytes(b"")
            with mock.patch.dict(os.environ, {
                "LD_LIBRARY_PATH": "/attacker/lib", "LD_PRELOAD": "/attacker/x.so",
                "GH_TOKEN": "secret", "PYTHONPATH": "/attacker",
            }):
                env = MODULE.generator_environment(prefix, self.config(1))
        self.assertEqual({"PATH": os.defpath, "LD_LIBRARY_PATH": str(Path(prefix) / "lib")}, env)

    def test_a_static_or_unlocatable_interpreter_gets_no_library_path(self):
        with tempfile.TemporaryDirectory() as prefix:
            (Path(prefix) / "lib").mkdir()
            (Path(prefix) / "lib/libpython3.12.so.1.0").write_bytes(b"")
            for name, config in (
                ("static", self.config(0)),
                ("unknown", self.config(None)),
                ("library elsewhere", self.config(1, "libpython3.13.so.1.0")),
                ("no soname", self.config(1, None)),
                ("soname escapes", self.config(1, "../lib/libpython3.12.so.1.0")),
            ):
                with self.subTest(name), mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/attacker/lib"}):
                    self.assertEqual({"PATH": os.defpath}, MODULE.generator_environment(prefix, config))

    def test_the_running_interpreter_can_start_a_generator(self):
        output = MODULE.run_generator({"gen.py": b"print('started')\n"}, ["gen.py"], require)
        self.assertEqual(b"started\n", output)


if __name__ == "__main__":
    unittest.main()
