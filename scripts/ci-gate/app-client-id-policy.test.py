#!/usr/bin/env python3
"""Exercise the configured GitHub App client-ID preflight policy and mints."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import app_client_id_contract
import gen_app_client_id_guards

WORKFLOWS = ROOT / ".github/workflows"
TOKEN_ACTION = "actions/create-github-app-token@"
EXPECTED_WORKFLOWS = {
    "ai-privileged-merge.yml",
    "ai-review-merge.yml",
    "capability-floor-observe.yml",
    "changelog-contract-fleet-report.yml",
    "changelog-release.yml",
    "container-deployment-review-producer.yml",
    "container-deployment.yml",
    "container-release.yml",
    "dependency-supersession-observe.yml",
    "dependency-supersession-reconcile.yml",
    "gate-rearm.yml",
    "release-app-canary.yml",
    "renovate-changelog.yml",
    "renovate-compatibility-reconcile.yml",
    "renovate-grouping-plan.yml",
}


def load_workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def guarded_mints():
    for path in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        workflow = load_workflow(path)
        for job_id, job in workflow.get("jobs", {}).items():
            steps = job.get("steps", [])
            for index, step in enumerate(steps):
                if str(step.get("uses", "")).startswith(TOKEN_ACTION):
                    yield path, job_id, steps, index, step


class AppClientIdPolicyTests(unittest.TestCase):
    def test_policy_accepts_documented_dotted_and_length_boundary_ids(self):
        for client_id in (
            "Iv1.0123456789abcdef",
            "Iv" + "a" * 8,
            "Iv" + "b" * 126,
        ):
            with self.subTest(client_id=client_id):
                self.assertTrue(app_client_id_contract.is_valid_client_id(client_id))

    def test_policy_rejects_numeric_legacy_malformed_and_out_of_bounds_ids(self):
        invalid_ids = (
            "123456789",
            "",
            "Iv" + "a" * 7,
            "Iv" + "a" * 127,
            " Iv" + "a" * 8,
            "Iv" + "a" * 7 + " ",
            "Iv" + "a" * 7 + "$",
            "Iv" + "a" * 7 + "é",
        )
        for client_id in invalid_ids:
            with self.subTest(client_id=client_id):
                self.assertFalse(app_client_id_contract.is_valid_client_id(client_id))

    def test_shell_guard_rejects_invalid_values_without_interpreting_them(self):
        def run_guard(client_id: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["bash", "-c", app_client_id_contract.CLIENT_ID_GUARD_RUN],
                cwd=ROOT,
                env={"APP_CLIENT_ID": client_id, "LC_ALL": "C", "PATH": os.environ["PATH"], **extra_env},
                capture_output=True,
                text=True,
                check=False,
            )

        for client_id in ("Iv1.0123456789abcdef", "Iv" + "a" * 8, "Iv" + "b" * 126):
            with self.subTest(client_id=client_id):
                self.assertEqual(run_guard(client_id).returncode, 0)

        with tempfile.TemporaryDirectory() as temporary_directory:
            marker = Path(temporary_directory) / "executed"
            payload = f"Ivabcdefgh$(touch${{IFS}}{marker})"
            result = run_guard(payload, CLIENT_ID_MARKER=str(marker))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

        for client_id in ("", "123456789", "Iv" + "a" * 127, "Iv" + "a" * 7 + "é"):
            with self.subTest(client_id=client_id):
                self.assertNotEqual(run_guard(client_id).returncode, 0)

    def test_every_canonical_token_mint_has_an_adjacent_exact_guard(self):
        mints = list(guarded_mints())
        self.assertEqual(len(mints), 19)
        self.assertEqual({path.name for path, *_ in mints}, EXPECTED_WORKFLOWS)
        self.assertEqual(
            sum(path.read_text(encoding="utf-8").count("Iv[") for path in WORKFLOWS.glob("*.yml")),
            len(mints),
            "workflows may not keep a second client-ID pattern outside the generated guards",
        )

        for path, _job_id, steps, index, mint in mints:
            with self.subTest(workflow=path.name, mint=mint.get("name")):
                self.assertGreater(index, 0)
                guard = steps[index - 1]
                self.assertEqual(guard.get("name"), "Validate GitHub App client ID")
                self.assertTrue(str(guard.get("id", "")).startswith("generated_app_client_id_"))
                self.assertEqual(guard.get("shell"), "bash")
                self.assertEqual(guard.get("run"), app_client_id_contract.CLIENT_ID_GUARD_RUN)
                self.assertEqual(guard.get("env", {}).get("APP_CLIENT_ID"), mint.get("with", {}).get("client-id"))
                self.assertEqual(guard.get("env", {}).get("LC_ALL"), "C")
                self.assertEqual(guard.get("if"), mint.get("if"))
                self.assertNotIn("continue-on-error", guard)

    def test_generator_checks_the_checked_in_workflow_steps(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "gen_app_client_id_guards.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("19 GitHub App token mints", result.stdout)

    def test_workflow_guard_binding_order_and_body_mutations_are_detected(self):
        path = WORKFLOWS / "dependency-supersession-reconcile.yml"
        source = path.read_text(encoding="utf-8")
        self.assertEqual(gen_app_client_id_guards._guard_problems(path, source), [])

        workflow = load_workflow(path)
        mint = workflow["jobs"]["reconcile"]["steps"][
            next(
                index for index, step in enumerate(workflow["jobs"]["reconcile"]["steps"])
                if step.get("id") == "supersession-token"
            )
        ]
        guard = workflow["jobs"]["reconcile"]["steps"][
            next(
                index for index, step in enumerate(workflow["jobs"]["reconcile"]["steps"])
                if step.get("id", "").startswith("generated_app_client_id_")
                and step.get("env", {}).get("APP_CLIENT_ID") == mint["with"]["client-id"]
            )
        ]

        expression = guard["env"]["APP_CLIENT_ID"]
        guard_line = next(line for line in source.splitlines() if "APP_CLIENT_ID:" in line and expression in line)
        mismatched_id = source.replace(guard_line, guard_line.replace(expression, expression + "_WRONG"), 1)
        self.assertTrue(any("IDs differ" in item for item in gen_app_client_id_guards._guard_problems(path, mismatched_id)))

        guard_id_line = next(line for line in source.splitlines() if line.strip() == f"id: {guard['id']}")
        missing_adjacent_guard = source.replace(guard_id_line, guard_id_line.replace(guard["id"], "wrong_guard_id"), 1)
        self.assertTrue(any("not preceded" in item for item in gen_app_client_id_guards._guard_problems(path, missing_adjacent_guard)))

        mutated_body = source.replace("Iv[A-Za-z0-9.]{8,126}", "Iv[A-Za-z0-9.]{8,125}", 1)
        self.assertTrue(any("body drifted" in item for item in gen_app_client_id_guards._guard_problems(path, mutated_body)))

        self.assertEqual(mint["if"], "${{ inputs.enforce }}")
        self.assertEqual(guard["if"], mint["if"])


if __name__ == "__main__":
    unittest.main()
