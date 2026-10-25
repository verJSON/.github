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

    def _candidate_launch(self) -> tuple[ast.For, ast.List, ast.Assign]:
        cleanup_tries = [
            node for node in self.helper.body
            if isinstance(node, ast.Try)
            and node.finalbody
            and any(
                isinstance(statement, ast.For)
                and isinstance(statement.iter, ast.Call)
                and isinstance(statement.iter.func, ast.Name)
                and statement.iter.func.id == "enumerate"
                and statement.iter.args
                and isinstance(statement.iter.args[0], ast.Name)
                and statement.iter.args[0].id == "normalized"
                for statement in node.body
            )
        ]
        self.assertEqual(len(cleanup_tries), 1, "the explicit-plan loop must remain in cleanup scope")
        loops = [
            statement for statement in cleanup_tries[0].body
            if isinstance(statement, ast.For)
            and isinstance(statement.iter, ast.Call)
            and isinstance(statement.iter.func, ast.Name)
            and statement.iter.func.id == "enumerate"
            and statement.iter.args
            and isinstance(statement.iter.args[0], ast.Name)
            and statement.iter.args[0].id == "normalized"
        ]
        self.assertEqual(len(loops), 1)
        loop = loops[0]
        self.assertEqual(
            ast.unparse(loop.target),
            "(index, (script_directory, name, unset_env, requires_services))",
        )
        parents = {
            child: parent
            for parent in ast.walk(loop)
            for child in ast.iter_child_nodes(parent)
        }
        candidate_loop_aborts = []
        for node in ast.walk(loop):
            if not isinstance(node, (ast.Continue, ast.Break)):
                continue
            parent = parents[node]
            while parent is not loop:
                if isinstance(parent, (ast.For, ast.While)):
                    in_loop_else = any(
                        node in ast.walk(statement) for statement in parent.orelse
                    )
                    if not in_loop_else:
                        break
                parent = parents[parent]
            if parent is loop:
                candidate_loop_aborts.append(node)
        self.assertFalse(candidate_loop_aborts, "an explicit plan entry cannot be skipped")

        successful_process_exits = []
        for node in ast.walk(loop):
            if isinstance(node, ast.Call):
                exits_process = (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "sys"
                    and node.func.attr == "exit"
                )
                unconditionally_exits_process = (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr == "_exit"
                )
                status = node.args[0] if len(node.args) == 1 else None
                if unconditionally_exits_process or (
                    exits_process and not _provably_nonzero_exit_status(status)
                ):
                    successful_process_exits.append(node)
            if isinstance(node, ast.Raise):
                exception = node.exc
                if isinstance(exception, ast.Name) and exception.id == "SystemExit":
                    successful_process_exits.append(node)
                elif (
                    isinstance(exception, ast.Call)
                    and isinstance(exception.func, ast.Name)
                    and exception.func.id == "SystemExit"
                ):
                    status = exception.args[0] if len(exception.args) == 1 else None
                    if not _provably_nonzero_exit_status(status):
                        successful_process_exits.append(node)
        self.assertFalse(
            successful_process_exits,
            "candidate entries cannot exit the helper successfully",
        )
        candidate_returns = []
        for node in ast.walk(loop):
            if not isinstance(node, ast.Return):
                continue
            parent = parents[node]
            while parent is not loop and not isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
            ):
                parent = parents[parent]
            if parent is loop:
                candidate_returns.append(node)
        self.assertFalse(candidate_returns, "an explicit plan entry cannot return from the helper")
        for variable in ("script_directory", "name"):
            stores = [
                node for node in ast.walk(loop)
                if isinstance(node, ast.Name)
                and node.id == variable
                and isinstance(node.ctx, ast.Store)
            ]
            self.assertEqual(len(stores), 1, f"{variable} must stay bound to the plan tuple")
        launch_assignments = [
            statement
            for node in loop.body
            if isinstance(node, ast.Try)
            for statement in node.body
            if isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == "active_process"
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and isinstance(statement.value.func.value, ast.Name)
            and statement.value.func.value.id == "subprocess"
            and statement.value.func.attr == "Popen"
        ]
        self.assertEqual(len(launch_assignments), 1)
        self.assertTrue(launch_assignments[0].value.args)
        command = launch_assignments[0].value.args[0]
        self.assertIsInstance(command, ast.List)
        return loop, command, launch_assignments[0]

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

            loop, command, _launch = self._candidate_launch()
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
