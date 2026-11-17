#!/usr/bin/env python3
"""Tests for quality-review sticky comment and artifact formatting (#1820)."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_review.report import MARKER, render_comment, write_artifact  # noqa: E402
from quality_review.run import ReviewResult  # noqa: E402


class ReportTest(unittest.TestCase):
    def test_not_obtained_comment_keeps_marker(self) -> None:
        result = ReviewResult(obtained=False, summary="quality evidence not obtained: demo")
        body = render_comment(result)
        self.assertTrue(body.startswith(MARKER))
        self.assertIn("quality evidence not obtained: demo", body)
        self.assertIn("Advisory only", body)

    def test_obtained_comment_lists_answers(self) -> None:
        result = ReviewResult(
            obtained=True,
            summary="quality evidence obtained via fake (fake-0)",
            provider="fake",
            model="fake-0",
            answers=[{"kind": "probability", "probability": 0.5}],
        )
        body = render_comment(result)
        self.assertIn("probability=0.5", body)
        self.assertIn("Provider: `fake`", body)

    def test_artifact_json(self) -> None:
        result = ReviewResult(obtained=False, summary="quality evidence not obtained: x")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "report.json"
            write_artifact(str(path), result, rubric_version=1)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(payload["obtained"])
        self.assertEqual(payload["rubric_version"], 1)


if __name__ == "__main__":
    unittest.main()
