#!/usr/bin/env python3
"""Protected Node candidate plans preserve each manifest's execution directory."""

from __future__ import annotations

import ast
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import yaml

import ci_build_artifacts as artifacts


def _target_names(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
    }


def _provably_nonzero_exit_status(status: ast.AST | None) -> bool:
    if status is None:
        return False
    try:
        value = ast.literal_eval(status)
    except (TypeError, ValueError):
        return ast.unparse(status) == "128 + received_signal"
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value % 256 != 0
    return isinstance(value, str)


class ProtectedCandidateManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load(
            (ROOT / ".github/workflows/node-ci-protected.yml").read_text(encoding="utf-8")
        )
        cls.helper = ast.parse(inspect.getsource(artifacts.run_protected_candidate_plan)).body[0]

    def _body_assignment(self, function: ast.FunctionDef, name: str) -> ast.Assign:
        matches = [
            statement
            for statement in function.body
            if isinstance(statement, ast.Assign) and name in _target_names(statement)
        ]
        self.assertEqual(len(matches), 1, f"expected one function-body assignment to {name}")
        return matches[0]

    def _execute(self, node: ast.stmt, namespace: dict[str, object]) -> None:
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, "ci_build_artifacts.py", "exec"), namespace)

    def test_process_exit_status_must_be_provably_nonzero(self) -> None:
        for expression in ("0", "-0", "256", "-256", "512", "None", "status"):
            with self.subTest(expression=expression):
                status = ast.parse(expression, mode="eval").body
                self.assertFalse(_provably_nonzero_exit_status(status))

        for expression in ("1", "-1", "255", "'failed'", "128 + received_signal"):
            with self.subTest(expression=expression):
                status = ast.parse(expression, mode="eval").body
                self.assertTrue(_provably_nonzero_exit_status(status))

    def _plan_nodes(self) -> tuple[ast.If, ast.For, ast.If]:
        nested_source = [
            node for node in self.helper.body
            if isinstance(node, ast.If) and ast.unparse(node.test) == "nested_source"
        ]
        normalizers = [
            node for node in self.helper.body
            if isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == "entry"
            and isinstance(node.iter, ast.Name)
            and node.iter.id == "normalized_entries"
        ]
        defaults = [
            node for node in self.helper.body
            if isinstance(node, ast.If) and ast.unparse(node.test) == "run_defaults"
        ]
        self.assertEqual((len(nested_source), len(normalizers), len(defaults)), (1, 1, 1))
        return nested_source[0], normalizers[0], defaults[0]

    def _run_plan_fragments(
        self,
        checkout_root: Path,
        nested_manifests: list[dict[str, object]],
        normalized_entries: list[dict[str, object]],
        *,
        default_plan: bool,
    ) -> dict[str, object]:
        test_environment = {
            "SCRIPT_PLAN_DEFAULT_PLAN": "true" if default_plan else "false",
            "NESTED_MANIFESTS": json.dumps(nested_manifests) if nested_manifests else "",
        }
        namespace: dict[str, object] = {
            "os": SimpleNamespace(environ=test_environment),
            "json": json,
            "re": re,
            "sys": sys,
            "Path": Path,
            "checkout_root": checkout_root.resolve(),
            "normalized_entries": normalized_entries,
        }
        initializers = [
            self._body_assignment(self.helper, name)
            for name in ("normalized", "run_defaults", "nested_manifests", "nested_directories", "nested_source")
        ]
        nested_source, normalizer, defaults = self._plan_nodes()
        for node in sorted([*initializers, nested_source, normalizer, defaults], key=lambda item: item.lineno):
            self._execute(node, namespace)
        return namespace

    def _write_package(self, directory: Path, scripts: dict[str, str]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "package.json").write_text(
            json.dumps({"name": directory.name, "scripts": scripts}),
            encoding="utf-8",
        )

    def _nested_manifest(self, path: str) -> dict[str, object]:
        return {"path": path, "approvedPackages": [], "scriptPlan": []}

    def _normalized_entry(
        self,
        script: str,
        *,
        manifest_path: str | None = None,
        manifest_index: int | None = None,
        unset_env: list[str] | None = None,
        requires_services: bool = False,
    ) -> dict[str, object]:
        return {
            "manifestPath": manifest_path,
            "manifestIndex": manifest_index,
            "script": script,
            "unsetEnv": [] if unset_env is None else unset_env,
            "requiresServices": requires_services,
        }

    def _candidate_launch(self) -> tuple[ast.FunctionDef, ast.List, ast.Assign]:
        runner_calls = [
            node
            for node in ast.walk(self.helper)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_script_plan_with_receipts"
        ]
        self.assertEqual(len(runner_calls), 1, "the explicit plan must use one receipt supervisor")
        runner_call = runner_calls[0]
        self.assertEqual(len(runner_call.args), 5)
        self.assertIsInstance(runner_call.args[0], ast.Name)
        self.assertEqual(runner_call.args[0].id, "command_records")
        self.assertIsInstance(runner_call.args[4], ast.Name)
        self.assertEqual(runner_call.args[4].id, "execute_candidate_entry")

        cleanup_scopes = [
            node
            for node in self.helper.body
            if isinstance(node, ast.Try)
            and runner_call in ast.walk(node)
            and node.finalbody
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "cleanup_cache_root"
                for statement in node.finalbody
                for child in ast.walk(statement)
            )
        ]
        self.assertEqual(
            len(cleanup_scopes),
            1,
            "the explicit plan runner must remain inside the outer cache cleanup scope",
        )

        command_records_stores = [
            node
            for node in ast.walk(self.helper)
            if isinstance(node, ast.Name)
            and node.id == "command_records"
            and isinstance(node.ctx, ast.Store)
        ]
        self.assertEqual(len(command_records_stores), 1, "the frozen expanded entry list cannot be rebound")
        command_record_mutations = [
            node
            for node in ast.walk(self.helper)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "command_records"
            and node.func.attr in {"append", "clear", "extend", "insert", "pop", "remove", "reverse", "sort"}
        ]
        self.assertFalse(command_record_mutations, "the frozen expanded entry list cannot be mutated")

        supervisor_source = inspect.getsource(artifacts.run_script_plan_with_receipts)
        supervisor = ast.parse(supervisor_source).body[0]
        supervisor_loops = [
            node
            for node in ast.walk(supervisor)
            if isinstance(node, ast.For)
            and isinstance(node.iter, ast.Call)
            and isinstance(node.iter.func, ast.Name)
            and node.iter.func.id == "enumerate"
            and node.iter.args
            and isinstance(node.iter.args[0], ast.Name)
            and node.iter.args[0].id == "normalized_entries"
        ]
        self.assertEqual(len(supervisor_loops), 1, "the receipt supervisor must cover every frozen entry")
        loop = supervisor_loops[0]
        self.assertEqual(ast.unparse(loop.target), "(ordinal, entry)")
        execute_calls = [
            node
            for node in ast.walk(loop)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "execute_entry"
        ]
        self.assertEqual(len(execute_calls), 1, "each plan entry must reach the executor exactly once")
        self.assertEqual(ast.unparse(execute_calls[0]), "execute_entry(ordinal, dict(entry))")

        callback = next(
            node
            for node in ast.walk(self.helper)
            if isinstance(node, ast.FunctionDef) and node.name == "execute_candidate_entry"
        )
        argument_assignments = [
            node
            for node in ast.walk(callback)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "arguments" for target in node.targets)
            and isinstance(node.value, ast.List)
        ]
        self.assertEqual(len(argument_assignments), 1, "one frozen Bubblewrap argv must be built per entry")
        arguments_assignment = argument_assignments[0]
        supervisor_calls = [
            node
            for node in ast.walk(callback)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run_supervised_bubblewrap"
        ]
        self.assertEqual(len(supervisor_calls), 1, "each entry must use the trusted sandbox supervisor")
        supervisor_call = supervisor_calls[0]
        self.assertTrue(supervisor_call.args)
        self.assertIsInstance(supervisor_call.args[0], ast.Name)
        self.assertEqual(supervisor_call.args[0].id, "arguments")
        self.assertIn("--unshare-pid", ast.unparse(arguments_assignment.value))
        self.assertIn("--as-pid-1", ast.unparse(arguments_assignment.value))

        cleanup_tries = [
            node
            for node in ast.walk(callback)
            if isinstance(node, ast.Try)
            and node.finalbody
            and supervisor_call in ast.walk(node)
        ]
        self.assertEqual(len(cleanup_tries), 1, "candidate execution must remain in unconditional cleanup scope")
        final_calls = [child for statement in cleanup_tries[0].finalbody for child in ast.walk(statement) if isinstance(child, ast.Call)]
        removed_cache_roots = {
            call.args[0].id
            for call in final_calls
            if isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "shutil"
            and call.func.attr == "rmtree"
            and call.args
            and isinstance(call.args[0], ast.Name)
        }
        self.assertTrue(
            {"script_cache", "script_tmp", "script_home"}.issubset(removed_cache_roots),
            "candidate cleanup must remove every private script cache root",
        )

        host_supervisor = inspect.getsource(artifacts.run_supervised_bubblewrap)
        host_supervisor_tree = ast.parse(host_supervisor)
        popen_calls = [
            node
            for node in ast.walk(host_supervisor_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr == "Popen"
        ]
        self.assertEqual(len(popen_calls), 1, "the host supervisor owns Bubblewrap launch")
        trusted_wrapper = artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT
        self.assertIn("close_fds=True", trusted_wrapper)
        self.assertIn("libc.prctl(4, 0", trusted_wrapper)
        self.assertIn("os.waitpid(-1", trusted_wrapper)
        self.assertIn("cleanup-complete", trusted_wrapper)

        command = arguments_assignment.value
        self.assertIsInstance(command, ast.List)
        return callback, command, arguments_assignment

    def _assert_plan_lists_are_not_mutated(self) -> None:
        allowed_methods = {"append", "clear", "extend", "insert", "pop", "remove", "reverse", "sort"}
        for name, allowed_calls in (("nested_directories", 1), ("normalized", 2), ("npm_command", 0)):
            stores = [
                node for node in ast.walk(self.helper)
                if isinstance(node, ast.Name)
                and node.id == name
                and isinstance(node.ctx, ast.Store)
            ]
            self.assertEqual(len(stores), 1, f"{name} must not be rebound after initialization")
            aliases = [
                node for node in ast.walk(self.helper)
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
                and isinstance(node.value, ast.Name)
                and node.value.id == name
                and (_target_names(node) - {name})
            ]
            self.assertFalse(aliases, f"{name} must not be aliased before launch")
            mutations = [
                node for node in ast.walk(self.helper)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == name
                and node.func.attr in allowed_methods
            ]
            self.assertEqual(len(mutations), allowed_calls, f"unexpected later mutation of {name}")
            writes = [
                node for node in ast.walk(self.helper)
                if isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == name
                and isinstance(node.ctx, (ast.Store, ast.Del))
            ]
            self.assertFalse(writes, f"unexpected indexed mutation of {name}")

    def test_protected_step_invokes_pinned_helper_and_propagates_status(self) -> None:
        steps = self.workflow["jobs"]["build-test"]["steps"]
        fetch_index = next(i for i, step in enumerate(steps) if step.get("name") == "Download pinned protected candidate helper")
        candidate_index = next(i for i, step in enumerate(steps) if step.get("name") == "Run exact credentialless consumer script plan")
        fetch = steps[fetch_index]
        candidate = steps[candidate_index]
        self.assertLess(fetch_index, candidate_index)
        self.assertEqual(fetch["env"]["JOB_WORKFLOW_SHA"], "${{ fromJSON(toJSON(job)).workflow_sha }}")
        self.assertIn("${JOB_WORKFLOW_SHA}/scripts/ci_build_artifacts.py", fetch["run"])

        with patch.object(
            sys, "argv", ["ci_build_artifacts.py", "run-protected-candidate-plan"]
        ), patch.object(artifacts, "run_protected_candidate_plan") as run_candidate_plan:
            self.assertEqual(artifacts.main(), 0)
            run_candidate_plan.assert_called_once_with()

        with tempfile.TemporaryDirectory(prefix="protected candidate workflow ") as temporary:
            temp_root = Path(temporary)
            runner_temp = temp_root / "runner temp"
            stub_bin = temp_root / "python shim"
            runner_temp.mkdir()
            stub_bin.mkdir()
            helper_path = runner_temp / "ci_build_artifacts.py"
            helper_path.write_text("# helper placeholder\n", encoding="utf-8")
            trace_path = temp_root / "invocation arguments"
            python_stub = stub_bin / "python3"
            python_stub.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$#\" \"$1\" \"$2\" \"$3\" >> \"$D1_HELPER_TRACE\"\n"
                "exit \"$D1_HELPER_STATUS\"\n",
                encoding="utf-8",
            )
            python_stub.chmod(0o755)
            github_env = runner_temp / "github env"
            github_output = runner_temp / "github output"
            github_env.touch()
            github_output.touch()
            environment = {
                "PATH": f"{stub_bin}{os.pathsep}/usr/bin:/bin",
                "RUNNER_TEMP": str(runner_temp),
                "GITHUB_ENV": str(github_env),
                "GITHUB_OUTPUT": str(github_output),
                "D1_HELPER_TRACE": str(trace_path),
            }
            for status in (0, 23):
                environment["D1_HELPER_STATUS"] = str(status)
                trace_path.unlink(missing_ok=True)
                result = subprocess.run(
                    ["/bin/bash", "-euo", "pipefail", "-c", candidate["run"]],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(
                    trace_path.read_text(encoding="utf-8").splitlines(),
                    ["3", "-I", str(helper_path), "run-protected-candidate-plan"],
                )

    def test_explicit_and_default_plans_keep_root_and_two_nested_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="protected manifest fixture ") as temporary:
            checkout_root = Path(temporary) / "workspace with spaces"
            self._write_package(
                checkout_root,
                {"verify": "true", "build": "true", "typecheck": "true", "test": "true", "lint": "true"},
            )
            nested_paths = ["examples/one", "examples/two"]
            self._write_package(checkout_root / nested_paths[0], {"verify": "true"})
            self._write_package(checkout_root / nested_paths[1], {"build": "true"})
            manifests = [self._nested_manifest(path) for path in nested_paths]
            entries = [
                self._normalized_entry("verify", unset_env=["ROOT_ONLY"]),
                self._normalized_entry(
                    "verify", manifest_path=nested_paths[0], manifest_index=0,
                    unset_env=["NESTED_ONE"], requires_services=True,
                ),
                self._normalized_entry(
                    "build", manifest_path=nested_paths[1], manifest_index=1,
                    unset_env=["NESTED_TWO"],
                ),
            ]
            result = self._run_plan_fragments(
                checkout_root, manifests, entries, default_plan=True
            )
            expected = [
                (checkout_root.resolve(), "verify", ["ROOT_ONLY"], False),
                ((checkout_root / nested_paths[0]).resolve(), "verify", ["NESTED_ONE"], True),
                ((checkout_root / nested_paths[1]).resolve(), "build", ["NESTED_TWO"], False),
                (checkout_root.resolve(), "build", [], False),
                (checkout_root.resolve(), "typecheck", [], False),
                (checkout_root.resolve(), "test", [], True),
                (checkout_root.resolve(), "lint", [], False),
            ]
            self.assertEqual(result["normalized"], expected)
            self._assert_plan_lists_are_not_mutated()

            _callback, command, _launch = self._candidate_launch()
            npm_assignment = self._body_assignment(self.helper, "npm_command")
            npm_launchers = (
                (
                    Path("/usr/lib/node modules/npm-cli.js"),
                    ["/usr/bin/node", "/usr/lib/node modules/npm-cli.js"],
                ),
                (None, ["/usr/bin/npm"]),
            )
            for npm_cli, expected_npm in npm_launchers:
                npm_namespace: dict[str, object] = {
                    "tool_executables": {"node": Path("/usr/bin/node")},
                    "npm_cli_executable": npm_cli,
                    "npm_executable": Path("/usr/bin/npm"),
                    "str": str,
                }
                self._execute(npm_assignment, npm_namespace)
                npm_command = npm_namespace["npm_command"]
                self.assertEqual(npm_command, expected_npm)
                for directory, script, _unset_env, _requires_services in expected:
                    argv = self._evaluate_candidate_argv(
                        command, checkout_root, directory, script, npm_command
                    )
                    separator = argv.index("--")
                    chdir = argv.index("--chdir")
                    self.assertEqual(argv.count("--chdir"), 1)
                    self.assertLess(chdir, separator)
                    self.assertEqual(argv[chdir + 1], str(directory))
                    self.assertEqual(
                        argv[separator + 1:],
                        [
                            "/usr/bin/python3", "-I", "-c", "sandbox-entrypoint",
                            *expected_npm, "run", script,
                        ],
                    )

    def test_root_only_plan_and_invalid_nested_bindings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="protected invalid manifests ") as temporary:
            base = Path(temporary)
            checkout_root = base / "workspace"
            self._write_package(checkout_root, {"verify": "true"})
            self._write_package(checkout_root / "examples/one", {"verify": "true"})
            self._write_package(checkout_root / "examples/two", {"verify": "true"})
            manifests = [self._nested_manifest("examples/one"), self._nested_manifest("examples/two")]

            root_only = self._run_plan_fragments(
                checkout_root, [], [self._normalized_entry("verify")], default_plan=False
            )
            self.assertEqual(root_only["nested_directories"], [])
            self.assertEqual(
                root_only["normalized"], [(checkout_root.resolve(), "verify", [], False)]
            )

            invalid_entries = (
                self._normalized_entry("verify", manifest_path="examples/two", manifest_index=0),
                self._normalized_entry("verify", manifest_path="examples/two", manifest_index=True),
            self._normalized_entry("verify", manifest_path="examples/two", manifest_index=-1),
                self._normalized_entry("verify", manifest_path="examples/one", manifest_index=2),
                self._normalized_entry("missing", manifest_path="examples/one", manifest_index=0),
            )
            for entry in invalid_entries:
                with self.subTest(entry=entry), self.assertRaises(SystemExit):
                    self._run_plan_fragments(
                        checkout_root, manifests, [entry], default_plan=False
                    )

            outside = base / "outside"
            self._write_package(outside, {"verify": "true"})
            linked = checkout_root / "examples/linked"
            linked.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(SystemExit):
                self._run_plan_fragments(
                    checkout_root,
                    [self._nested_manifest("examples/linked")],
                    [],
                    default_plan=False,
                )

    def _evaluate_candidate_argv(
        self,
        command: ast.List,
        checkout_root: Path,
        script_directory: Path,
        script: str,
        npm_command: list[str],
    ) -> list[str]:
        starred = {
            node.value.id: [f"<star:{node.value.id}>"]
            for node in ast.walk(command)
            if isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
        }
        namespace: dict[str, object] = {
            **starred,
            "bubblewrap": Path("/usr/bin/bwrap"),
            "status_write_fd": 3,
            "workspace": checkout_root,
            "script_cache": checkout_root / ".cache",
            "script_home": checkout_root / ".home",
            "script_tmp": checkout_root / ".tmp",
            "script_directory": script_directory,
            "name": script,
            "npm_command": npm_command,
            "sandbox_entrypoint": "sandbox-entrypoint",
            "str": str,
        }
        required = {
            node.id
            for node in ast.walk(command)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        self.assertFalse(required - namespace.keys(), "Popen argument fixture is missing a value")
        expression = ast.Expression(body=command)
        ast.fix_missing_locations(expression)
        argv = eval(compile(expression, "ci_build_artifacts.py", "eval"), namespace)
        self.assertIsInstance(argv, list)
        separator = argv.index("--")
        chdir = argv.index("--chdir")
        self.assertFalse(
            any(argument.startswith("<star:") for argument in argv[chdir + 2:separator]),
            "dynamic options must not override the checked Bubblewrap directory",
        )
        return argv


if __name__ == "__main__":
    unittest.main()
