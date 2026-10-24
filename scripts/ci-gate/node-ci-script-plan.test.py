#!/usr/bin/env python3
"""Golden contract for canonical Node CI script-plan parsing."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/node-ci-script-plan"
MODULE_PATH = ACTION / "script_plan.py"
GOLDEN = ROOT / "scripts/ci-gate/fixtures/node-ci-script-plan/normalized.golden.json"
sys.dont_write_bytecode = True
sys.path.insert(0, str(ACTION))
import script_plan  # noqa: E402


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class GoldenPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_normalized_plan_cases_match_committed_golden(self) -> None:
        cases = {
            "blank": ("", ""),
            "strings": (encoded(["build", "test"]), ""),
            "objects": (
                encoded(
                    [
                        {"script": "integration:test", "unsetEnv": ["PUBLIC_FLAG"], "requiresServices": False},
                        {"script": "db:test", "unsetEnv": []},
                    ]
                ),
                "",
            ),
            "nested": (
                encoded(["build"]),
                encoded(
                    [
                        {
                            "path": "packages/db",
                            "approvedPackages": ["@verjson/db"],
                            "scriptPlan": [{"script": "test:db", "unsetEnv": ["CACHE_DEBUG"]}],
                        }
                    ]
                ),
            ),
        }
        for name, (plan_source, nested_source) in cases.items():
            with self.subTest(name=name):
                entries, default_plan = script_plan.normalize_outputs(plan_source, nested_source)
                self.assertEqual(
                    {"entries": entries, "defaultPlan": default_plan},
                    self.golden[name],
                )

    def test_nested_plans_keep_manifest_scope_and_allow_same_script_in_two_manifests(self) -> None:
        nested = encoded(
            [
                {
                    "path": "packages/one",
                    "approvedPackages": [],
                    "scriptPlan": ["test"],
                },
                {
                    "path": "packages/two",
                    "approvedPackages": [],
                    "scriptPlan": ["test"],
                },
            ]
        )

        entries, _ = script_plan.normalize_outputs("", nested)

        self.assertEqual(
            [(entry["manifestPath"], entry["manifestIndex"]) for entry in entries],
            [("packages/one", 0), ("packages/two", 1)],
        )

    def test_compatibility_grants_use_explicit_and_inferred_service_flags(self) -> None:
        plan_source = encoded(
            [
                "test",
                {"script": "test:isolated", "requiresServices": False},
                {"script": "build", "requiresServices": True},
            ]
        )

        self.assertTrue(script_plan.requires_services_for_script("test", plan_source))
        self.assertFalse(script_plan.requires_services_for_script("test:isolated", plan_source))
        self.assertTrue(script_plan.requires_services_for_script("build", plan_source))
        self.assertFalse(script_plan.requires_services_for_script("lint", plan_source))

    def test_blank_plan_preserves_test_name_default_service_grant(self) -> None:
        self.assertTrue(script_plan.requires_services_for_script("unit:test", " \n "))
        self.assertFalse(script_plan.requires_services_for_script("build", ""))

    def test_compatibility_grant_rejects_surrounding_unicode_whitespace(self) -> None:
        with self.assertRaises(script_plan.ScriptPlanError):
            script_plan.requires_services_for_script(
                "unit:test",
                ' ["unit:test"] ',
            )

    def test_duplicate_json_members_keep_python_json_last_value_behavior(self) -> None:
        source = '{"script":"build","requiresServices":false,"requiresServices":true}'

        entries = script_plan.parse_plan_value([json.loads(source)])

        self.assertTrue(entries[0].requires_services)

    def test_duplicate_script_and_duplicate_unset_environment_entries_are_rejected(self) -> None:
        with self.assertRaises(script_plan.ScriptPlanError):
            script_plan.parse_plan_value(["test", {"script": "test"}])
        with self.assertRaises(script_plan.ScriptPlanError):
            script_plan.parse_plan_value([{"script": "build", "unsetEnv": ["PUBLIC_FLAG", "PUBLIC_FLAG"]}])

    def test_unknown_fields_wrong_flags_and_scriptless_level_objects_are_rejected(self) -> None:
        invalid_plans = [
            [{"script": "test", "typo": True}],
            [{"script": "test", "requiresServices": "true"}],
            [{"id": "test", "inputs": {"secretless-ci-script-plan": '["test"]'}}],
        ]
        for plan in invalid_plans:
            with self.subTest(plan=plan), self.assertRaises(script_plan.ScriptPlanError):
                script_plan.parse_plan_value(plan)

    def test_explicit_empty_plan_is_rejected_but_nested_empty_plan_and_blank_are_allowed(self) -> None:
        self.assertIsNone(script_plan.parse_script_plan(""))
        with self.assertRaises(script_plan.ScriptPlanError):
            script_plan.parse_plan_value([])
        nested = script_plan.parse_nested_manifests(
            encoded([{"path": "packages/db", "approvedPackages": [], "scriptPlan": []}])
        )
        self.assertEqual(nested[0].entries, ())

    def test_malformed_json_is_rejected(self) -> None:
        with self.assertRaises(script_plan.ScriptPlanError):
            script_plan.parse_script_plan("[")

    def test_root_and_nested_plans_trim_unicode_whitespace_before_json_parsing(self) -> None:
        root_entries = script_plan.parse_script_plan("\u00a0[\"build\"]\u00a0")
        self.assertEqual(["build"], [entry.script for entry in root_entries or ()])

        nested = script_plan.parse_nested_manifests(
            "\u00a0"
            + encoded(
                [
                    {
                        "path": "packages/db",
                        "approvedPackages": [],
                        "scriptPlan": ["test"],
                    }
                ]
            )
            + "\u00a0"
        )
        self.assertEqual("packages/db", nested[0].path)
        self.assertEqual(["test"], [entry.script for entry in nested[0].entries])

    def test_normalized_output_is_deterministic_utf8_json(self) -> None:
        first, _ = script_plan.normalize_outputs(encoded(["test"]), "")
        second, _ = script_plan.normalize_outputs(encoded(["test"]), "")

        encoded_first = script_plan.encode_json_output(first)
        self.assertEqual(encoded_first, script_plan.encode_json_output(second))
        self.assertEqual(json.loads(encoded_first), first)

    def test_composite_action_cli_emits_private_file_output(self) -> None:
        with tempfile.TemporaryDirectory() as runner_temp:
            result = subprocess.run(
                [sys.executable, "-I", str(MODULE_PATH)],
                env={
                    "PATH": os.environ["PATH"],
                    "RUNNER_TEMP": runner_temp,
                    "SCRIPT_PLAN_MODE": "normalize",
                    "SCRIPT_PLAN_SOURCE": encoded(["test"]),
                    "NESTED_MANIFESTS_SOURCE": "",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output_lines = result.stdout.splitlines()
            self.assertEqual(len(output_lines), 2)
            self.assertTrue(output_lines[0].startswith("entries-file="))
            entries_path = Path(output_lines[0].partition("=")[2])
            self.assertEqual(entries_path.parent, Path(runner_temp))
            self.assertEqual(stat.S_IMODE(entries_path.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(entries_path.read_text(encoding="utf-8")),
                [{
                    "manifestPath": None,
                    "manifestIndex": None,
                    "script": "test",
                    "unsetEnv": [],
                    "requiresServices": True,
                }],
            )
            self.assertEqual(output_lines[1], "default-plan=false")

    def test_valid_plan_over_8_kib_remains_accepted(self) -> None:
        plan = [
            {
                "script": f"verify:{script_index:02d}",
                "unsetEnv": [
                    f"CI_TEST_{script_index:02d}_{env_index:02d}_{'X' * 32}"
                    for env_index in range(16)
                ],
            }
            for script_index in range(32)
        ]
        plan_source = encoded(plan)
        self.assertGreater(len(plan_source.encode("utf-8")), 8 * 1024)
        normalized, _ = script_plan.normalize_outputs(plan_source, "")
        with tempfile.TemporaryDirectory() as runner_temp:
            result = subprocess.run(
                [sys.executable, "-I", str(MODULE_PATH)],
                env={
                    "PATH": os.environ["PATH"],
                    "RUNNER_TEMP": runner_temp,
                    "SCRIPT_PLAN_MODE": "normalize",
                    "SCRIPT_PLAN_SOURCE": plan_source,
                    "NESTED_MANIFESTS_SOURCE": "",
                    "COMPATIBILITY_REQUEST_SOURCE": "",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output_lines = result.stdout.splitlines()
            self.assertEqual(len(output_lines), 2)
            output_path = Path(output_lines[0].partition("=")[2])
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), normalized)
            self.assertEqual(len(normalized), 32)
            self.assertEqual(len(normalized[0]["unsetEnv"]), 16)
            self.assertEqual(output_lines[1], "default-plan=false")

    def test_compatibility_cli_emits_service_grant(self) -> None:
        env = {
            "PATH": os.environ["PATH"],
            "SCRIPT_PLAN_MODE": "compatibility",
            "SCRIPT_PLAN_SOURCE": encoded(["test"]),
            "COMPATIBILITY_REQUEST_SOURCE": encoded({"script": "test"}),
        }

        result = subprocess.run(
            [sys.executable, "-I", str(MODULE_PATH)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "requires-services=true\n")

    def test_compatibility_cli_honors_explicit_service_denial(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(MODULE_PATH)],
            env={
                "PATH": os.environ["PATH"],
                "SCRIPT_PLAN_MODE": "compatibility",
                "SCRIPT_PLAN_SOURCE": encoded(
                    [{"script": "test:isolated", "requiresServices": False}]
                ),
                "COMPATIBILITY_REQUEST_SOURCE": encoded({"script": "test:isolated"}),
            },
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "requires-services=false\n")

    def test_action_metadata_uses_fixed_entrypoint_and_mode_scoped_inputs(self) -> None:
        action = (ACTION / "action.yml").read_text(encoding="utf-8")
        expected_inputs = (
            "SCRIPT_PLAN_MODE: ${{ inputs.mode }}",
            "SCRIPT_PLAN_SOURCE: ${{ inputs.script-plan }}",
            "NESTED_MANIFESTS_SOURCE: ${{ inputs.mode == 'normalize' && inputs.nested-manifests || '' }}",
            "COMPATIBILITY_REQUEST_SOURCE: ${{ inputs.mode == 'compatibility' && inputs.compatibility-request || '' }}",
        )
        for mapping in expected_inputs:
            self.assertIn(mapping, action)

        run_lines = [line.strip() for line in action.splitlines() if line.lstrip().startswith("run:")]
        self.assertEqual(
            run_lines,
            ['run: python3 -I "$GITHUB_ACTION_PATH/script_plan.py" >> "$GITHUB_OUTPUT"'],
        )
        self.assertNotIn("${{ inputs.", run_lines[0])


if __name__ == "__main__":
    unittest.main()
