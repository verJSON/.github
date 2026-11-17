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
    {"id": "severity", "kind": "level", "prompt": "Security impact?", "levels": ["low", "medium", "high"]},
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
    def test_maps_noul_and_score_through_port(self) -> None:
        def opener(request: urllib.request.Request, timeout_s: float) -> _FakeResponse:
            self.assertEqual(timeout_s, 30.0)
            self.assertEqual(request.get_method(), "POST")
            self.assertIn("Bearer test-key", request.headers["Authorization"])
            body = json.loads(request.data.decode("utf-8"))
            self.assertEqual(body["model"], "jev-1.14.0")
            return _FakeResponse(
                {
                    "answers": [
                        {"noul": 0.7},
                        {"score": "low", "confidence": 0.9},
                    ]
                }
            )

        answers = JevEvaluator("test-key", opener=opener).evaluate({"diff": "x"}, QUESTIONS)
        self.assertEqual(answers[0]["probability"], 0.7)
        self.assertEqual(answers[1]["level"], "low")
        self.assertEqual(answers[1]["confidence"], 0.9)

    def test_rejects_unpinned_latest_model(self) -> None:
        with self.assertRaises(ValueError):
            JevEvaluator("test-key", model="jev-latest")

    def test_rejects_schema_violation(self) -> None:
        def opener(request: urllib.request.Request, timeout_s: float) -> _FakeResponse:
            del request, timeout_s
            return _FakeResponse({"answers": [{"noul": 2.0}, {"score": "low", "confidence": 0.5}]})

        with self.assertRaises(ValueError):
            JevEvaluator("test-key", opener=opener).evaluate({"diff": "x"}, QUESTIONS)

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


if __name__ == "__main__":
    unittest.main()
