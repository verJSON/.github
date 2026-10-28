#!/usr/bin/env python3
"""Exercise the configured GitHub App client-ID preflight policy and mints."""

from __future__ import annotations

import importlib.util
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertEqual(len(mints), 18)
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
        self.assertIn("18 GitHub App token mints", result.stdout)

    def test_orphan_final_guard_removal_preserves_the_following_job(self):
        path = WORKFLOWS / "synthetic.yml"
        guards = (
            """      - name: Validate GitHub App client ID
        id: generated_app_client_id_first_1
        shell: bash
        run: |
          set -euo pipefail
          true
""",
            """      - name: Require the selected App client ID
        run: |
          set -euo pipefail
          true
""",
        )
        for guard in guards:
            with self.subTest(guard=guard.splitlines()[0]):
                source = (
                    "name: synthetic\njobs:\n  first:\n    runs-on: ubuntu-latest\n    steps:\n"
                    + guard
                    + "  following:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo keep\n"
                )
                edits, _, errors = gen_app_client_id_guards._collect_edits(path, source)
                self.assertEqual(errors, [])
                result = gen_app_client_id_guards._apply_edits(source, edits)
                parsed = yaml.safe_load(result)
                self.assertEqual(set(parsed["jobs"]), {"first", "following"})
                self.assertEqual(parsed["jobs"]["following"]["steps"], [{"run": "echo keep"}])

    def test_generator_rejects_status_check_conditions_that_can_bypass_a_failed_guard(self):
        path = WORKFLOWS / "dependency-supersession-reconcile.yml"
        for expression in (
            "contains('always()', inputs.enforce)",
            "${{ contains(inputs.foo, 'always()') }}",
            "contains(inputs.note, '}}')",
            "${{ contains(inputs.note, '}}') }}",
            "${{ contains(inputs.note, '${{ always() }}') }}",
            "'always()'",
        ):
            with self.subTest(expression=expression):
                self.assertIsNone(gen_app_client_id_guards._status_check_problem(expression))
        for expression in (
            "always()",
            "${{ always() }}",
            "${{ always() }} && true",
            "${{ contains(inputs.foo, 'value') && ${{ always() }} }}",
            "contains(inputs.note, '${{ always() }}')",
            "'${{ always() }}'",
            "${{ 'always()' }}",
            "${{ 'failure()' }}",
            "${{ 'success()' }}",
            "${{ 'cancelled()' }}",
        ):
            with self.subTest(expression=expression):
                self.assertIsNotNone(gen_app_client_id_guards._status_check_problem(expression))
        workflow = load_workflow(path)
        steps = workflow["jobs"]["reconcile"]["steps"]
        mint_index = next(index for index, step in enumerate(steps) if step.get("id") == "supersession-token")
        steps[mint_index - 1]["if"] = "always()"
        steps[mint_index]["if"] = "always()"
        source = yaml.safe_dump(workflow, sort_keys=False)

        problems = gen_app_client_id_guards._guard_problems(path, source)
        self.assertTrue(any("explicit status-check function" in problem for problem in problems))
        _, _, errors = gen_app_client_id_guards._collect_edits(path, source)
        self.assertTrue(any("explicit status-check function" in error for error in errors))

    def test_write_mode_reports_errors_without_partially_rewriting_workflows(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            valid = workflows / "a-valid.yml"
            invalid = workflows / "z-invalid.yml"
            valid_source = """name: valid
jobs:
  mint:
    steps:
      - uses: actions/create-github-app-token@v3
        with:
          client-id: ${{ vars.APP_CLIENT_ID }}
"""
            invalid_source = """name: invalid
jobs:
  mint:
    steps:
      - uses: actions/create-github-app-token@v3
        with:
          private-key: ${{ secrets.APP_PRIVATE_KEY }}
      - uses: actions/create-github-app-token@v3
        if: always()
        with:
          client-id: ${{ vars.APP_CLIENT_ID }}
"""
            valid.write_text(valid_source, encoding="utf-8")
            invalid.write_text(invalid_source, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                mock.patch.object(gen_app_client_id_guards, "ROOT", root),
                mock.patch.object(gen_app_client_id_guards, "WORKFLOWS", workflows),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = gen_app_client_id_guards.main(["--write"])

            self.assertNotEqual(result, 0)
            self.assertEqual(valid.read_text(encoding="utf-8"), valid_source)
            self.assertEqual(invalid.read_text(encoding="utf-8"), invalid_source)
            self.assertIn("lacks string client-id", stderr.getvalue())
            self.assertIn("explicit status-check function", stderr.getvalue())
            self.assertNotIn("generated guards", stdout.getvalue())

    def test_check_mode_does_not_rewrite_shared_yaml_step_anchors(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            workflow = workflows / "shared.yml"
            guard_run = "\n".join(
                f"          {line}" for line in app_client_id_contract.CLIENT_ID_GUARD_RUN.splitlines()
            )
            source = (
                "name: shared\njobs:\n  first:\n    steps: &shared_steps\n"
                "      - name: Validate GitHub App client ID\n"
                "        id: generated_app_client_id_shared_1\n"
                "        shell: bash\n"
                "        env:\n"
                "          APP_CLIENT_ID: \"${{ vars.APP_CLIENT_ID }}\"\n"
                "          LC_ALL: C\n"
                "        run: |\n"
                f"{guard_run}\n"
                "      - uses: actions/create-github-app-token@v3\n"
                "        with:\n"
                "          client-id: ${{ vars.APP_CLIENT_ID }}\n"
                "  second:\n    steps: *shared_steps\n"
            )
            workflow.write_text(source, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                mock.patch.object(gen_app_client_id_guards, "ROOT", root),
                mock.patch.object(gen_app_client_id_guards, "WORKFLOWS", workflows),
                mock.patch.object(Path, "write_text") as writer,
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = gen_app_client_id_guards.main([])

            self.assertEqual(result, 0, stderr.getvalue())
            self.assertEqual(writer.call_count, 0)
            self.assertIn("2 GitHub App token mints", stdout.getvalue())

    def test_write_mode_generates_valid_guards_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            workflow = workflows / "mint.yml"
            source = """name: mint
jobs:
  mint:
    steps:
      - uses: actions/create-github-app-token@v3
        with:
          client-id: ${{ vars.APP_CLIENT_ID }}
"""
            workflow.write_text(source, encoding="utf-8")

            with (
                mock.patch.object(gen_app_client_id_guards, "ROOT", root),
                mock.patch.object(gen_app_client_id_guards, "WORKFLOWS", workflows),
            ):
                self.assertEqual(gen_app_client_id_guards.main(["--write"]), 0)
                generated = workflow.read_text(encoding="utf-8")
                self.assertNotEqual(generated, source)
                self.assertEqual(gen_app_client_id_guards._guard_problems(workflow, generated), [])
                self.assertEqual(gen_app_client_id_guards.main(["--write"]), 0)
                self.assertEqual(workflow.read_text(encoding="utf-8"), generated)

    def test_write_mode_refuses_to_empty_a_job_when_cleaning_its_only_guard(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            valid = workflows / "a-valid.yml"
            orphan = workflows / "z-orphan.yml"
            valid_source = """name: valid
jobs:
  mint:
    steps:
      - uses: actions/create-github-app-token@v3
        with:
          client-id: ${{ vars.APP_CLIENT_ID }}
"""
            orphan_source = """name: orphan
jobs:
  only:
    runs-on: ubuntu-latest
    steps:
      - name: Validate GitHub App client ID
        id: generated_app_client_id_only_1
        shell: bash
        run: |
          set -euo pipefail
          true
"""
            valid.write_text(valid_source, encoding="utf-8")
            orphan.write_text(orphan_source, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                mock.patch.object(gen_app_client_id_guards, "ROOT", root),
                mock.patch.object(gen_app_client_id_guards, "WORKFLOWS", workflows),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = gen_app_client_id_guards.main(["--write"])

            self.assertNotEqual(result, 0)
            self.assertEqual(valid.read_text(encoding="utf-8"), valid_source)
            self.assertEqual(orphan.read_text(encoding="utf-8"), orphan_source)
            self.assertIn("job only steps is not a sequence", stderr.getvalue())
            self.assertNotIn("generated guards", stdout.getvalue())

    def test_write_mode_refuses_flow_style_steps_without_partial_rewrites(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            valid = workflows / "a-valid.yml"
            orphan = workflows / "b-orphan.yml"
            mint = workflows / "c-mint.yml"
            multiline_orphan = workflows / "d-multiline-orphan.yml"
            valid_source = """name: valid
jobs:
  mint:
    steps:
      - uses: actions/create-github-app-token@v3
        with:
          client-id: ${{ vars.APP_CLIENT_ID }}
"""
            orphan_source = """name: orphan
jobs:
  first:
    runs-on: ubuntu-latest
    steps: [{run: echo keep}, {name: Require the selected App client ID, run: echo guard}]
  following:
    runs-on: ubuntu-latest
    steps:
      - run: echo following
"""
            mint_source = """name: mint
jobs:
  inline:
    runs-on: ubuntu-latest
    steps: [{uses: actions/create-github-app-token@v3, with: {client-id: \"${{ vars.APP_CLIENT_ID }}\"}}]
"""
            multiline_orphan_source = """name: multiline-orphan
jobs:
  first:
    runs-on: ubuntu-latest
    steps: [
      {run: echo keep},
      {name: Require the selected App client ID, run: echo guard}
    ]
"""
            valid.write_text(valid_source, encoding="utf-8")
            orphan.write_text(orphan_source, encoding="utf-8")
            mint.write_text(mint_source, encoding="utf-8")
            multiline_orphan.write_text(multiline_orphan_source, encoding="utf-8")
            stderr = io.StringIO()

            with (
                mock.patch.object(gen_app_client_id_guards, "ROOT", root),
                mock.patch.object(gen_app_client_id_guards, "WORKFLOWS", workflows),
                contextlib.redirect_stderr(stderr),
            ):
                result = gen_app_client_id_guards.main(["--write"])

            self.assertNotEqual(result, 0)
            self.assertEqual(valid.read_text(encoding="utf-8"), valid_source)
            self.assertEqual(orphan.read_text(encoding="utf-8"), orphan_source)
            self.assertEqual(mint.read_text(encoding="utf-8"), mint_source)
            self.assertEqual(multiline_orphan.read_text(encoding="utf-8"), multiline_orphan_source)
            self.assertIn("legacy guard step layout cannot be rewritten safely", stderr.getvalue())
            self.assertIn("token mint step layout cannot be rewritten safely", stderr.getvalue())

    def test_write_mode_rejects_each_unsafe_step_layout_without_writing(self):
        cases = (
            (
                "block sequence flow token",
                """name: flow-token
jobs:
  first:
    runs-on: ubuntu-latest
    steps:
      - {uses: actions/create-github-app-token@v3, with: {client-id: '${{ vars.APP_CLIENT_ID }}'}}
""",
                "token mint step layout cannot be rewritten safely",
            ),
            (
                "block sequence flow legacy guard",
                """name: flow-legacy-guard
jobs:
  first:
    runs-on: ubuntu-latest
    steps:
      - {name: Require the selected App client ID, run: echo guard}
""",
                "legacy guard step layout cannot be rewritten safely",
            ),
            (
                "block sequence flow generated guard",
                """name: flow-generated-guard
jobs:
  first:
    runs-on: ubuntu-latest
    steps:
      - {name: Validate GitHub App client ID, id: generated_app_client_id_inline_1, run: echo guard}
      - uses: actions/create-github-app-token@v3
        with:
          client-id: ${{ vars.APP_CLIENT_ID }}
""",
                "generated guard step layout cannot be rewritten safely",
            ),
            (
                "multiline flow sequence guard",
                """name: multiline-flow-guard
jobs:
  first:
    runs-on: ubuntu-latest
    steps: [
      {run: echo keep},
      {name: Require the selected App client ID, run: echo guard}
    ]
""",
                "legacy guard step layout cannot be rewritten safely",
            ),
            (
                "legacy split dash guard",
                """name: legacy-split-dash
jobs:
  first:
    runs-on: ubuntu-latest
    steps:
      -
        name: Require the selected App client ID
        run: echo guard
""",
                "legacy guard step layout cannot be rewritten safely",
            ),
            (
                "generated split dash guard",
                """name: generated-split-dash
jobs:
  first:
    runs-on: ubuntu-latest
    steps:
      -
        name: Validate GitHub App client ID
        id: generated_app_client_id_split_1
        run: echo guard
""",
                "generated guard step layout cannot be rewritten safely",
            ),
            (
                "legacy guard before split dash successor",
                """name: legacy-guard-before-split-dash
jobs:
  first:
    runs-on: ubuntu-latest
    steps:
      - run: echo keep-first
      - name: Require the selected App client ID
        run: echo guard
      -
        run: echo keep-last
""",
                "following step layout cannot be preserved while removing a legacy guard",
            ),
            (
                "generated guard before split dash successor",
                """name: generated-guard-before-split-dash
jobs:
  first:
    runs-on: ubuntu-latest
    steps:
      - run: echo keep-first
      - name: Validate GitHub App client ID
        id: generated_app_client_id_before_split_1
        run: echo guard
      -
        run: echo keep-last
""",
                "following step layout cannot be preserved while removing a generated guard",
            ),
        )

        for label, source, expected_error in cases:
            with self.subTest(layout=label), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                workflows = root / ".github" / "workflows"
                workflows.mkdir(parents=True)
                workflow = workflows / "workflow.yml"
                workflow.write_text(source, encoding="utf-8")
                stderr = io.StringIO()

                with (
                    mock.patch.object(gen_app_client_id_guards, "ROOT", root),
                    mock.patch.object(gen_app_client_id_guards, "WORKFLOWS", workflows),
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(stderr),
                ):
                    result = gen_app_client_id_guards.main(["--write"])

                self.assertNotEqual(result, 0)
                self.assertEqual(workflow.read_text(encoding="utf-8"), source)
                self.assertIn(expected_error, stderr.getvalue())

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

        mutated_body = source.replace(
            "Iv[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.]{8,126}",
            "Iv[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.]{8,125}",
            1,
        )
        self.assertTrue(any("body drifted" in item for item in gen_app_client_id_guards._guard_problems(path, mutated_body)))

        self.assertEqual(mint["if"], "${{ inputs.enforce }}")
        self.assertEqual(guard["if"], mint["if"])


if __name__ == "__main__":
    unittest.main()
