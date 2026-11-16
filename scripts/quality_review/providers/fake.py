"""Deterministic fake provider for the shared contract suite (#1820)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from quality_review.port import validate_answer


class FakeEvaluator:
    """Returns fixed scalars; never performs network I/O."""

    def __init__(self, probability: float = 0.5, level: str = "medium", confidence: float = 0.8):
        self.probability = probability
        self.level = level
        self.confidence = confidence

    def evaluate(
        self, state: Mapping[str, Any], questions: Sequence[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        del state  # unused; real adapters read the filtered diff from state
        answers: list[Mapping[str, Any]] = []
        for question in questions:
            kind = question.get("kind")
            if kind == "probability":
                raw = {"probability": self.probability}
            elif kind == "level":
                raw = {"level": self.level, "confidence": self.confidence}
            else:
                raise ValueError(f"unknown question kind: {kind!r}")
            answers.append(validate_answer(question, raw))
        return answers
