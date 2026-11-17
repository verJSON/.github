#!/usr/bin/env python3
"""Structural checks for quality-review workflow and caller generator (#1820)."""

from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/quality-review.yml"
CANARY = ROOT / ".github/workflows/quality-review-canary.yml"
GENERATOR = ROOT / "scripts/gen-quality-review-caller.sh"


class QualityReviewWorkflowTest(unittest.TestCase):
    def test_reusable_workflow_is_pull_request_only_and_skips_forks_drafts(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_call:", text)
        self.assertNotIn("pull_request_target", text)
        self.assertIn("github.event.pull_request.draft == false", text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)
        self.assertIn("ubuntu-24.04", text)
        self.assertIn("quality evidence not obtained", text)
        self.assertIn(",quality-review,", text)
        self.assertIn("<!-- quality-review:v1 -->", text)
        self.assertIn("quality-review-report", text)
        # always() sticky/artifact must not import from the fixed checkout path
        # (scripts/ci-gate/fixed-path-checkout-refusal.test.py).
        write_step = text.split("- name: Write artifact and sticky comment", 1)[1]
        write_step = write_step.split("- name: Upload quality-review artifact", 1)[0]
        run_block = write_step.split("run: |", 1)[1]
        self.assertNotIn(".quality-review-contract", run_block)
        self.assertNotIn("quality_review.report", run_block)
        self.assertIn("upload-artifact@", text)
        self.assertIn("application/vnd.github.diff", text)

    def test_canary_uses_local_reusable_path(self) -> None:
        text = CANARY.read_text(encoding="utf-8")
        self.assertIn("uses: ./.github/workflows/quality-review.yml", text)
        self.assertNotIn("pull_request_target", text)

    def test_generator_emits_immutable_pin(self) -> None:
        sha = "a" * 40
        completed = subprocess.run(
            [str(GENERATOR), sha],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn(f"uses: Verjson/.github/.github/workflows/quality-review.yml@{sha}", completed.stdout)
        self.assertIn(f"contract_ref: {sha}", completed.stdout)
        self.assertIn("secrets: inherit", completed.stdout)

    def test_generator_rejects_mutable_ref(self) -> None:
        completed = subprocess.run([str(GENERATOR), "main"], capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
