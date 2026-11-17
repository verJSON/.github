"""Format quality-review summaries, sticky comments, and artifacts (#1820)."""

from __future__ import annotations

import json
from typing import Any, Mapping

from quality_review.run import ReviewResult

MARKER = "<!-- quality-review:v1 -->"


def render_comment(result: ReviewResult) -> str:
    lines = [
        MARKER,
        "## Advisory quality review",
        "",
        result.summary,
        "",
    ]
    if result.provider:
        lines.append(f"- Provider: `{result.provider}`")
    if result.model:
        lines.append(f"- Model: `{result.model}`")
    if result.dropped:
        lines.append(f"- Dropped paths/chunks: {len(result.dropped)}")
    if result.obtained and result.answers:
        lines.append("")
        lines.append("| Dimension | Answer |")
        lines.append("| --- | --- |")
        for answer in result.answers:
            kind = answer.get("kind")
            if kind == "probability":
                cell = f"probability={answer.get('probability')}"
            elif kind == "level":
                cell = f"level={answer.get('level')} confidence={answer.get('confidence')}"
            else:
                cell = str(answer)
            lines.append(f"| `{answer.get('kind', '?')}` | {cell} |")
    lines.append("")
    lines.append("_Advisory only — never a required check (ADR 0270)._")
    return "\n".join(lines) + "\n"


def artifact_payload(result: ReviewResult, *, rubric_version: int | None = None) -> Mapping[str, Any]:
    return {
        "obtained": result.obtained,
        "summary": result.summary,
        "provider": result.provider,
        "model": result.model,
        "rubric_version": rubric_version,
        "answers": list(result.answers),
        "dropped": list(result.dropped),
    }


def write_artifact(path: str, result: ReviewResult, *, rubric_version: int | None = None) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(artifact_payload(result, rubric_version=rubric_version), handle, indent=2, sort_keys=True)
        handle.write("\n")
