"""Evaluator port: evaluate(state, questions) -> answers.

Question kinds:
- probability: yes/no likelihood in [0, 1]
- level: ordinal level plus confidence in [0, 1]

Provider output is untrusted; adapters must validate scalars before return.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence


class Evaluator(Protocol):
    def evaluate(
        self, state: Mapping[str, Any], questions: Sequence[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        """Answer each question. Raises on transport or schema failure."""


def validate_answer(question: Mapping[str, Any], answer: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a validated answer dict or raise ValueError."""
    kind = question.get("kind")
    if kind == "probability":
        value = answer.get("probability")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("probability answer must be a number")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("probability must be in [0, 1]")
        return {"kind": "probability", "probability": float(value)}
    if kind == "level":
        level = answer.get("level")
        confidence = answer.get("confidence")
        if not isinstance(level, str) or not level:
            raise ValueError("level answer needs a non-empty level string")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise ValueError("level answer needs numeric confidence")
        if not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        return {"kind": "level", "level": level, "confidence": float(confidence)}
    raise ValueError(f"unknown question kind: {kind!r}")
