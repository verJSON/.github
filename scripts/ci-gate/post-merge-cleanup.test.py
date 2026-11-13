#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ai-privileged-merge.yml"
RETRY_WORKFLOW = ROOT / ".github/workflows/ai-promotion-retry.yml"
GENERATOR = ROOT / "scripts/gen-privileged-merge-caller.sh"
REQUIRED_CHECKS = json.dumps(
    [
        {
            "name": "shell-tests",
            "app_id": 15368,
            "workflow_id": 315894159,
            "workflow_path": ".github/workflows/actions-ci.yml",
        }
    ],
    separators=(",", ":"),
)
ALLOW_GUARD = (
    "(github.event_name != 'pull_request' && github.event_name != 'pull_request_review' "
    "&& github.event_name != 'pull_request_review_comment' && github.event_name != 'merge_group' "
    "&& github.event_name != 'pull_request_target' "
    "|| github.event.repository.private == true)"
)
CLEANUP_CONDITION = (
    "${{ " + ALLOW_GUARD + " && "
    "(always() && needs.privileged_merge.outputs.terminal_merge_succeeded == 'true') }}"
)


class PostMergeCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.merge_job = self.workflow["jobs"]["privileged_merge"]
        self.merge_steps = self.merge_job["steps"]
        self.cleanup_job = self.workflow["jobs"]["cleanup_arm_receipt"]
        self.cleanup_step = self.cleanup_job["steps"][0]

    def merge_step(self, name: str) -> dict:
        matches = [step for step in self.merge_steps if step.get("name") == name]
        self.assertEqual(len(matches), 1, f"expected exactly one {name!r} step")
        return matches[0]

    def generated_caller(self, *arguments: str) -> dict:
        result = subprocess.run(
            [str(GENERATOR), "a" * 40, *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return yaml.safe_load(result.stdout)

    def run_cleanup(
        self, artifact_id: str, *, delete_mode: str = "success"
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as temporary:
            temp = pathlib.Path(temporary)
            bin_dir = temp / "bin"
            bin_dir.mkdir()
            delete_log = temp / "delete.log"
            gh = bin_dir / "gh"
            gh.write_text(
                """#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >>"$DELETE_LOG"
if [ "$GH_DELETE_MODE" = failure ]; then
  printf 'authorization: Bearer SUPER_SECRET_DELETE_TOKEN\n' >&2
  exit 29
fi
exit 0
""",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "RUNNER_TEMP": str(temp),
                "TARGET_REPO": "Verjson/example",
                "ARM_RECEIPT_ARTIFACT_ID": artifact_id,
                "GH_DELETE_MODE": delete_mode,
                "DELETE_LOG": str(delete_log),
            }
            result = subprocess.run(
                ["bash", "-c", self.cleanup_step["run"]],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            calls = (
                delete_log.read_text(encoding="utf-8").splitlines()
                if delete_log.exists()
                else []
            )
            return result, calls

    def test_cleanup_has_the_only_actions_write_job_permission(self) -> None:
        self.assertEqual(self.workflow["permissions"]["actions"], "read")
        self.assertEqual(self.merge_job["permissions"]["actions"], "read")
        self.assertEqual(self.cleanup_job["permissions"], {"actions": "write"})
        writers = [
            name
            for name, job in self.workflow["jobs"].items()
            if job.get("permissions", {}).get("actions") == "write"
        ]
        self.assertEqual(writers, ["cleanup_arm_receipt"])

    def test_cleanup_job_is_secret_free_and_has_no_merge_environment(self) -> None:
        self.assertNotIn("environment", self.cleanup_job)
        self.assertNotIn("secrets", self.cleanup_job)
        cleanup_text = json.dumps(self.cleanup_job)
        for forbidden in (
            "MERGE_APP_PRIVATE_KEY",
            "MERGE_APP_CLIENT_ID",
            "merge-app-token",
            "permission-actions",
            "issues: write",
            "PAT",
            "actions/checkout",
        ):
            self.assertNotIn(forbidden, cleanup_text)

    def test_retired_merge_never_enables_cleanup(self) -> None:
        self.assertEqual(self.merge_job["outputs"]["terminal_merge_succeeded"], "false")
        self.assertEqual(self.merge_job["outputs"]["arm_receipt_artifact_id"], "")
        self.assertFalse(any(step.get("name") == "Export terminal merge cleanup receipt" for step in self.merge_steps))

    def test_cleanup_consumes_only_the_merge_job_outputs(self) -> None:
        self.assertEqual(self.cleanup_job["needs"], "privileged_merge")
        self.assertEqual(self.cleanup_job["if"], CLEANUP_CONDITION)
        self.assertEqual(
            self.cleanup_job["env"],
            {
                "ARM_RECEIPT_ARTIFACT_ID": (
                    "${{ needs.privileged_merge.outputs.arm_receipt_artifact_id }}"
                ),
                "TARGET_REPO": "${{ github.repository }}",
            },
        )
        self.assertEqual(self.cleanup_step["env"], {"GH_TOKEN": "${{ github.token }}"})

    def test_generated_and_current_callers_grant_actions_write(self) -> None:
        direct = self.generated_caller(REQUIRED_CHECKS)
        retry = self.generated_caller("--retry", '["actions-ci"]', REQUIRED_CHECKS)
        current_retry = yaml.safe_load(RETRY_WORKFLOW.read_text(encoding="utf-8"))
        self.assertEqual(direct["permissions"]["actions"], "write")
        self.assertEqual(retry["permissions"]["actions"], "write")
        self.assertEqual(current_retry["permissions"]["actions"], "read")
        self.assertEqual(
            current_retry["jobs"]["resolve"]["permissions"],
            {
                "actions": "read",
                "checks": "read",
                "contents": "read",
                "pull-requests": "read",
            },
        )
        self.assertEqual(
            current_retry["jobs"]["promote"]["permissions"],
            {
                "actions": "write",
                "checks": "read",
                "contents": "read",
                "issues": "read",
                "pull-requests": "read",
            },
        )

    def test_successful_cleanup_deletes_the_exact_artifact_once(self) -> None:
        result, calls = self.run_cleanup("123")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            calls,
            ["api --method DELETE repos/Verjson/example/actions/artifacts/123"],
        )
        self.assertIn("Consumed arm receipt artifact 123", result.stdout)

    def test_delete_failure_is_sanitized_visible_and_nonfatal(self) -> None:
        result, calls = self.run_cleanup("123", delete_mode="failure")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("::warning::failed to delete", result.stdout)
        self.assertIn("authorization: Bearer ***", result.stderr)
        self.assertNotIn("SUPER_SECRET_DELETE_TOKEN", result.stderr)

    def test_invalid_output_never_attempts_cleanup(self) -> None:
        for artifact_id in ("", "0", "abc"):
            with self.subTest(artifact_id=artifact_id):
                result, calls = self.run_cleanup(artifact_id)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(calls, [])
                self.assertIn("::warning::not deleting", result.stdout)


if __name__ == "__main__":
    unittest.main()
