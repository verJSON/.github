"""Load and validate quality-review provider and rubric manifests (#1820)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS_PATH = ROOT / "config" / "quality-review-providers.json"
RUBRIC_PATH = ROOT / "config" / "quality-review-rubric.json"

_ALLOWED_KINDS = frozenset({"probability", "level"})


def load_json(path: Path) -> Mapping[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def load_providers(path: Path = PROVIDERS_PATH) -> Mapping[str, Any]:
    data = load_json(path)
    if data.get("version") != 1:
        raise ValueError("quality-review-providers.json version must be 1")
    providers = data.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise ValueError("providers map is required")
    default = data.get("default_provider")
    if not isinstance(default, str) or default not in providers:
        raise ValueError("default_provider must name an entry in providers")
    for provider_id, entry in providers.items():
        if not isinstance(entry, dict):
            raise ValueError(f"provider {provider_id!r} must be an object")
        model = entry.get("default_model")
        if not isinstance(model, str) or not model or model.endswith("-latest"):
            raise ValueError(f"provider {provider_id!r} needs a pinned default_model (never *-latest)")
        adapter = entry.get("adapter")
        if not isinstance(adapter, str) or not adapter:
            raise ValueError(f"provider {provider_id!r} needs an adapter id")
        cap = entry.get("request_token_cap")
        if not isinstance(cap, int) or cap <= 0:
            raise ValueError(f"provider {provider_id!r} needs a positive request_token_cap")
    return data


def load_rubric(path: Path = RUBRIC_PATH) -> Mapping[str, Any]:
    data = load_json(path)
    if data.get("version") != 1:
        raise ValueError("quality-review-rubric.json version must be 1")
    dimensions = data.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise ValueError("rubric dimensions are required")
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            raise ValueError("each dimension must be an object")
        kind = dimension.get("kind")
        if kind not in _ALLOWED_KINDS:
            raise ValueError(f"dimension kind must be probability or level, got {kind!r}")
        if not isinstance(dimension.get("id"), str) or not dimension["id"]:
            raise ValueError("dimension id is required")
        if kind == "level":
            levels = dimension.get("levels")
            if not isinstance(levels, list) or len(levels) < 2 or not all(isinstance(x, str) and x for x in levels):
                raise ValueError(f"level dimension {dimension['id']!r} needs at least two level strings")
    for key in ("screening_threshold", "confidence_floor"):
        value = data.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{key} must be a number in [0, 1]")
    return data


def rubric_questions(rubric: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return port questions derived from the rubric dimensions."""
    data = rubric if rubric is not None else load_rubric()
    questions: list[dict[str, Any]] = []
    for dimension in data["dimensions"]:
        question: dict[str, Any] = {
            "id": dimension["id"],
            "kind": dimension["kind"],
            "prompt": dimension.get("prompt", dimension["id"]),
        }
        if dimension["kind"] == "level":
            question["levels"] = list(dimension["levels"])
        questions.append(question)
    return questions
