#!/usr/bin/env python3
"""Jev adapter contract + transport tests (#1820)."""

from __future__ import annotations

import io
import json
import pathlib
import sys
import unittest
import urllib.error
import urllib.request
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_review.providers.jev import JevEvaluator  # noqa: E402

QUESTIONS = (
    {"id": "correctness", "kind": "probability", "prompt": "Is the change correct?"},
    {
        "id": "severity",
        "kind": "level",
        "prompt": "Security impact?",
        "levels": ["low", "medium", "high"],
    },
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class JevAdapterTest(unittest.TestCase):
    def test_maps_systemone_wire_shape(self) -> None:
        def opener(request: urllib.request.Request, timeout_s: float) -> _FakeResponse:
            self.assertEqual(timeout_s, 30.0)
            self.assertEqual(request.get_method(), "POST")
            self.assertIn("Bearer test-key", request.headers["Authorization"])
            body = json.loads(request.data.decode("utf-8"))
            self.assertEqual(body["model"], "jev-1.14.0")
            self.assertEqual(body["state"], "diff-text")
            self.assertEqual(
                body["questions"],
                {
                    "correctness": {
                        "type": "noul",
                        "instructions": "Is the change correct?",
                    },
                    "severity": {
                        "type": "score",
                        "instructions": "Security impact?",
                        "criteria": ["low", "medium", "high"],
                    },
                },
            )
            return _FakeResponse(
                {
                    "answers": {
                        "correctness": {"type": "noul", "noul": 0.7},
                        "severity": {
                            "type": "score",
                            "score": 1.7,
                            "confidence": 0.9,
                            "legend": {"0": "low", "1": "medium", "2": "high"},
                        },
                    }
                }
            )

        answers = JevEvaluator("test-key", opener=opener).evaluate(
            {"diff": "diff-text"}, QUESTIONS
        )
        self.assertEqual(answers[0]["probability"], 0.7)
        self.assertEqual(answers[1]["level"], "high")
        self.assertEqual(answers[1]["confidence"], 0.9)

    def test_rejects_unpinned_latest_model(self) -> None:
        with self.assertRaises(ValueError):
            JevEvaluator("test-key", model="jev-latest")

    def test_rejects_schema_violation(self) -> None:
        def opener(request: urllib.request.Request, timeout_s: float) -> _FakeResponse:
            del request, timeout_s
            return _FakeResponse(
                {"answers": {"correctness": {"type": "noul", "noul": 2.0}}}
            )

        with self.assertRaises(ValueError):
            JevEvaluator("test-key", opener=opener).evaluate(
                {"diff": "x"}, QUESTIONS[:1]
            )

    def test_http_error_is_value_error(self) -> None:
        def opener(request: urllib.request.Request, timeout_s: float) -> Any:
            del request, timeout_s
            raise urllib.error.HTTPError(
                "https://api.typesafe.ai/v1/systemone",
                429,
                "Too Many Requests",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b""),
            )

        with self.assertRaises(ValueError) as ctx:
            JevEvaluator("test-key", opener=opener).evaluate({"diff": "x"}, QUESTIONS)
        self.assertIn("429", str(ctx.exception))

    def test_timeout_is_value_error(self) -> None:
        def opener(request: urllib.request.Request, timeout_s: float) -> Any:
            del request, timeout_s
            raise TimeoutError("read timed out")

        with self.assertRaises(ValueError) as ctx:
            JevEvaluator("test-key", opener=opener).evaluate({"diff": "x"}, QUESTIONS)
        self.assertIn("timeout", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
