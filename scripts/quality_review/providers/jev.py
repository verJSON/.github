"""Jev (TypeSafe) adapter for the quality-review evaluator port (#1820).

Maps port kinds to TypeSafe SystemOne fields:
- probability -> noul in [0, 1]
- level -> score ordinal plus confidence in [0, 1]

Transport failures and schema violations raise; callers treat those as
"not obtained" for the advisory report.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Sequence

from quality_review.port import validate_answer

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.14.0"
DEFAULT_TIMEOUT_S = 30.0


class JevEvaluator:
    """HTTP adapter; injectable opener for contract tests (no live network)."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        opener: Callable[[urllib.request.Request, float], Any] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Jev API key is required")
        if model.endswith("-latest"):
            raise ValueError("Jev model must be pinned; -latest is forbidden")
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout_s = timeout_s
        self._opener = opener or self._default_opener

    @staticmethod
    def _default_opener(request: urllib.request.Request, timeout_s: float) -> Any:
        return urllib.request.urlopen(request, timeout=timeout_s)  # noqa: S310 — pinned HTTPS endpoint

    def evaluate(
        self, state: Mapping[str, Any], questions: Sequence[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        payload = {
            "model": self.model,
            "state": dict(state),
            "questions": [dict(q) for q in questions],
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener(request, self.timeout_s) as response:
                status = getattr(response, "status", None) or response.getcode()
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ValueError(f"Jev HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"Jev transport failure: {exc.reason}") from exc
        if status != 200:
            raise ValueError(f"Jev HTTP {status}")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Jev response is not JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Jev response must be an object")
        answers_raw = parsed.get("answers")
        if not isinstance(answers_raw, list) or len(answers_raw) != len(questions):
            raise ValueError("Jev answers length must match questions")
        answers: list[Mapping[str, Any]] = []
        for question, raw_answer in zip(questions, answers_raw, strict=True):
            if not isinstance(raw_answer, dict):
                raise ValueError("Jev answer must be an object")
            kind = question.get("kind")
            if kind == "probability":
                mapped = {"probability": raw_answer.get("noul", raw_answer.get("probability"))}
            elif kind == "level":
                mapped = {
                    "level": raw_answer.get("score", raw_answer.get("level")),
                    "confidence": raw_answer.get("confidence"),
                }
            else:
                raise ValueError(f"unknown question kind: {kind!r}")
            answers.append(validate_answer(question, mapped))
        return answers
