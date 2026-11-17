#!/usr/bin/env python3
"""Shared provider contract suite (#1820).

Every adapter must answer probability and level questions with validated
scalars. The fake provider proves a second provider can satisfy the port
without workflow or rubric changes.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_review.port import validate_answer  # noqa: E402
from quality_review.providers.fake import FakeEvaluator  # noqa: E402


QUESTIONS = (
    {"id": "correctness", "kind": "probability", "prompt": "Is the change correct?"},
    {"id": "severity", "kind": "level", "prompt": "Security impact?", "levels": ["low", "medium", "high"]},
)


class FakeProviderContractTest(unittest.TestCase):
    def test_fake_answers_both_kinds(self) -> None:
        answers = FakeEvaluator().evaluate({"diff": "demo"}, QUESTIONS)
        self.assertEqual(len(answers), 2)
        self.assertEqual(answers[0]["kind"], "probability")
        self.assertEqual(answers[1]["kind"], "level")
        self.assertEqual(answers[0]["probability"], 0.5)
        self.assertEqual(answers[1]["level"], "medium")

    def test_validate_answer_rejects_out_of_range_probability(self) -> None:
        with self.assertRaises(ValueError):
            validate_answer(QUESTIONS[0], {"probability": 1.5})

    def test_validate_answer_rejects_missing_level(self) -> None:
        with self.assertRaises(ValueError):
            validate_answer(QUESTIONS[1], {"confidence": 0.5})

    def test_fake_is_importable_as_second_provider(self) -> None:
        module = importlib.import_module("quality_review.providers.fake")
        self.assertTrue(hasattr(module, "FakeEvaluator"))


if __name__ == "__main__":
    unittest.main()
