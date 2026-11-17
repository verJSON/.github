"""Jev (TypeSafe) adapter for the quality-review port (#1820).

Maps port kinds to Jev noul/score on POST /v1/systemone. Uses the standard
library only. Provider output is untrusted: only validated scalars are returned.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence

from quality_review.port import validate_answer

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.14.0"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2


class JevError(RuntimeError):
    """Transport or schema failure talking to Jev."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _score_to_level(score: object, levels: Sequence[str]) -> str:
    """Map a TypeSafe score onto the rubric's ordinal levels.

    TypeSafe Score answers use 0 through len(criteria)-1 (not a fixed 0..9
    scale). The adapter sends one criterion per rubric level, so the score
    index aligns with the level list.
    """
    if not levels:
        raise ValueError("levels required")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise ValueError("score must be a number")
    high = float(len(levels) - 1)
    bounded = max(0.0, min(high, float(score)))
    idx = int(round(bounded))
    return levels[idx]


def _question_to_jev(question: Mapping[str, Any]) -> dict[str, Any]:
    kind = question.get("kind")
    prompt = str(question.get("prompt") or question.get("id") or "")
    if kind == "probability":
        return {
            "type": "noul",
            "instructions": prompt,
            "criteria": {
                "true": "Yes — the proposition holds for the supplied state.",
                "false": "No — the proposition does not hold for the supplied state.",
            },
        }
    if kind == "level":
        levels = question.get("levels") or []
        if not isinstance(levels, list) or len(levels) < 2:
            raise ValueError("level questions need levels")
        criteria = [f"{i + 1} — {label}" for i, label in enumerate(levels)]
        # Pad to look like a score rubric; Jev score uses criteria list.
        return {
            "type": "score",
            "instructions": prompt,
            "criteria": criteria,
        }
    raise ValueError(f"unknown question kind: {kind!r}")


def _answer_from_jev(question: Mapping[str, Any], raw: Mapping[str, Any]) -> Mapping[str, Any]:
    kind = question.get("kind")
    answer_type = raw.get("type")
    if kind == "probability":
        if answer_type != "noul":
            raise ValueError(f"expected noul answer, got {answer_type!r}")
        return validate_answer(question, {"probability": raw.get("noul")})
    if kind == "level":
        if answer_type != "score":
            raise ValueError(f"expected score answer, got {answer_type!r}")
        levels = question.get("levels") or []
        level = _score_to_level(raw.get("score"), levels)
        confidence = raw.get("confidence")
        return validate_answer(question, {"level": level, "confidence": confidence})
    raise ValueError(f"unknown question kind: {kind!r}")


class JevEvaluator:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_RETRIES,
        opener: urllib.request.OpenerDirector | None = None,
    ):
        key = api_key.strip()
        if not key:
            raise JevError("JEV_API_KEY is empty")
        if model.endswith("-latest"):
            raise JevError("model must be pinned; *-latest is refused")
        self.api_key = key
        self.endpoint = endpoint
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.opener = opener or urllib.request.build_opener()

    def evaluate(
        self, state: Mapping[str, Any], questions: Sequence[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        # System One requires state as a string (docs.typesafe.ai/api/system-one).
        diff = state.get("diff")
        if isinstance(diff, str) and diff:
            state_text = diff
        else:
            state_text = json.dumps(dict(state), sort_keys=True, separators=(",", ":"))
        jev_questions = {str(q.get("id") or i): _question_to_jev(q) for i, q in enumerate(questions)}
        payload = {"state": state_text, "model": self.model, "questions": jev_questions}
        body = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
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
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    status = getattr(response, "status", None) or response.getcode()
                    raw_body = response.read()
                if status != 200:
                    raise JevError(f"Jev HTTP {status}", status=status)
                parsed = json.loads(raw_body.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise JevError("Jev response was not an object")
                answers_map = parsed.get("answers")
                if not isinstance(answers_map, dict):
                    raise JevError("Jev response missing answers object")
                results: list[Mapping[str, Any]] = []
                for i, question in enumerate(questions):
                    qid = str(question.get("id") or i)
                    raw_answer = answers_map.get(qid)
                    if not isinstance(raw_answer, dict):
                        raise JevError(f"Jev omitted answer for {qid!r}")
                    results.append(_answer_from_jev(question, raw_answer))
                return results
            except urllib.error.HTTPError as exc:
                last_error = JevError(f"Jev HTTP {exc.code}", status=exc.code)
                if exc.code in {429, 529} or exc.code >= 500:
                    if attempt < self.max_retries:
                        retry_after = exc.headers.get("Retry-After") if exc.headers else None
                        delay = 1.0
                        if retry_after:
                            try:
                                delay = min(8.0, max(0.0, float(retry_after)))
                            except ValueError:
                                delay = 1.0
                        time.sleep(delay * (attempt + 1))
                        continue
                raise last_error from exc
            except urllib.error.URLError as exc:
                last_error = JevError(f"Jev unreachable: {exc.reason}")
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise last_error from exc
            except TimeoutError as exc:
                last_error = JevError("Jev transport timeout")
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise last_error from exc
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise JevError(f"Jev schema error: {exc}") from exc
        raise JevError(f"Jev failed after retries: {last_error}")
