"""Jev (TypeSafe System One) adapter for the quality-review evaluator port (#1820).

Wire shape follows https://docs.typesafe.ai/api/system-one (verified 2026-11-17):

Request:
  POST {endpoint} with Bearer token
  { "model", "state": <string>, "questions": { <id>: {type, instructions, criteria?} } }

Response:
  { "answers": { <id>: {type, noul?} | {type, score, confidence, legend} }, "usage"? }

Port mapping:
- probability -> type "noul"; answer.noul -> probability
- level -> type "score" with criteria from question.levels; numeric score + legend
  -> nearest legend label as level, plus confidence

Live canary still required before treating production rubric levels as calibrated.
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


def _state_text(state: Mapping[str, Any]) -> str:
    diff = state.get("diff")
    if isinstance(diff, str) and diff:
        return diff
    return json.dumps(dict(state), sort_keys=True, separators=(",", ":"))


def _port_questions_to_systemone(
    questions: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for question in questions:
        qid = question.get("id")
        if not isinstance(qid, str) or not qid:
            raise ValueError("each question needs a non-empty string id")
        kind = question.get("kind")
        prompt = question.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError(f"question {qid!r} needs a prompt string")
        if kind == "probability":
            out[qid] = {"type": "noul", "instructions": prompt}
        elif kind == "level":
            levels = question.get("levels")
            if not isinstance(levels, (list, tuple)) or len(levels) < 2:
                raise ValueError(f"level question {qid!r} needs at least two levels")
            if any(not isinstance(level, str) or not level for level in levels):
                raise ValueError(f"level question {qid!r} levels must be non-empty strings")
            out[qid] = {
                "type": "score",
                "instructions": prompt,
                "criteria": list(levels),
            }
        else:
            raise ValueError(f"unknown question kind: {kind!r}")
    return out


def _level_from_score(score: float, legend: Mapping[str, Any]) -> str:
    if not legend:
        raise ValueError("score answer missing legend")
    keys: list[int] = []
    for key in legend:
        try:
            keys.append(int(key))
        except (TypeError, ValueError) as exc:
            raise ValueError("score legend keys must be integers") from exc
    nearest = min(keys, key=lambda k: abs(k - score))
    label = legend.get(str(nearest), legend.get(nearest))  # type: ignore[arg-type]
    if not isinstance(label, str) or not label:
        raise ValueError("score legend entry must be a non-empty string")
    return label


def _map_answer(question: Mapping[str, Any], raw: Mapping[str, Any]) -> Mapping[str, Any]:
    kind = question.get("kind")
    if kind == "probability":
        return validate_answer(question, {"probability": raw.get("noul")})
    if kind == "level":
        score = raw.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ValueError("score answer needs a numeric score")
        legend = raw.get("legend")
        if not isinstance(legend, dict):
            raise ValueError("score answer needs a legend object")
        return validate_answer(
            question,
            {
                "level": _level_from_score(float(score), legend),
                "confidence": raw.get("confidence"),
            },
        )
    raise ValueError(f"unknown question kind: {kind!r}")


class JevEvaluator:
    """HTTP adapter; injectable opener for offline contract tests."""

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
        return urllib.request.urlopen(request, timeout=timeout_s)  # noqa: S310 — HTTPS endpoint from config

    def evaluate(
        self, state: Mapping[str, Any], questions: Sequence[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        system_questions = _port_questions_to_systemone(questions)
        payload = {
            "model": self.model,
            "state": _state_text(state),
            "questions": system_questions,
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
        except TimeoutError as exc:
            raise ValueError("Jev transport timeout") from exc
        if status != 200:
            raise ValueError(f"Jev HTTP {status}")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Jev response is not JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Jev response must be an object")
        answers_raw = parsed.get("answers")
        if not isinstance(answers_raw, dict):
            raise ValueError("Jev answers must be an object keyed by question id")
        answers: list[Mapping[str, Any]] = []
        for question in questions:
            qid = question["id"]
            raw_answer = answers_raw.get(qid)
            if not isinstance(raw_answer, dict):
                raise ValueError(f"Jev answer missing for {qid!r}")
            answers.append(_map_answer(question, raw_answer))
        return answers
