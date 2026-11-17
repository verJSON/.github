"""Orchestrate advisory quality review (#1820).

Always exits 0 for expected provider/scanner outcomes so Gate C never
treats a missing quality signal as a required-check failure. Callers must
surface ``result.summary`` (including "quality evidence not obtained").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from quality_review.config import load_providers, load_rubric, rubric_questions
from quality_review.gitleaks import Scanner, scan_chunks
from quality_review.payload import build_payload
from quality_review.port import Evaluator
from quality_review.providers.fake import FakeEvaluator
from quality_review.providers.jev import JevError, JevEvaluator

NOT_OBTAINED_PREFIX = "quality evidence not obtained:"


@dataclass
class ReviewResult:
    obtained: bool
    summary: str
    answers: list[Mapping[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None


def _not_obtained(reason: str) -> ReviewResult:
    return ReviewResult(obtained=False, summary=f"{NOT_OBTAINED_PREFIX} {reason}")


def build_evaluator(
    provider_id: str,
    *,
    api_key: str | None = None,
    model_override: str | None = None,
    endpoint_override: str | None = None,
    providers: Mapping[str, Any] | None = None,
) -> tuple[Evaluator, str, str]:
    manifest = providers if providers is not None else load_providers()
    entry = manifest["providers"].get(provider_id)
    if not isinstance(entry, dict):
        raise KeyError(f"unknown provider {provider_id!r}")
    model = model_override or str(entry["default_model"])
    adapter = str(entry["adapter"])
    if adapter == "fake":
        return FakeEvaluator(), provider_id, model
    if adapter == "jev":
        if not api_key:
            raise JevError("JEV_API_KEY is empty")
        endpoint = endpoint_override or str(entry["endpoint"])
        timeout = float(entry.get("timeout_seconds") or 30)
        retries = int(entry.get("max_retries") or 2)
        return (
            JevEvaluator(
                api_key=api_key,
                endpoint=endpoint,
                model=model,
                timeout_seconds=timeout,
                max_retries=retries,
            ),
            provider_id,
            model,
        )
    raise KeyError(f"unsupported adapter {adapter!r}")


def run_review(
    diff_text: str,
    *,
    provider_id: str = "fake",
    api_key: str | None = None,
    model_override: str | None = None,
    endpoint_override: str | None = None,
    scanner: Scanner | None = None,
    providers: Mapping[str, Any] | None = None,
    rubric: Mapping[str, Any] | None = None,
) -> ReviewResult:
    """Filter, scan, and evaluate a PR diff. Never raises for provider outages."""
    try:
        manifest = providers if providers is not None else load_providers()
        entry = manifest["providers"][provider_id]
        token_cap = int(entry["request_token_cap"])
        # Total budget: 3× one chunk by default until the workflow pins a job budget.
        total_budget = token_cap * 3
        plan = build_payload(diff_text, token_cap=token_cap, total_budget=total_budget)
        scanned = scan_chunks(plan.chunks, scanner)
        if scanned.reason:
            return _not_obtained(scanned.reason)
        if not scanned.sent:
            detail = "all chunks dropped"
            if plan.dropped or scanned.dropped:
                detail = "no sendable chunks after filter/gitleaks"
            return _not_obtained(detail)

        evaluator, resolved_provider, model = build_evaluator(
            provider_id,
            api_key=api_key,
            model_override=model_override,
            endpoint_override=endpoint_override,
            providers=manifest,
        )
        questions = rubric_questions(rubric if rubric is not None else load_rubric())
        state = {"diff": "\n".join(scanned.sent)}
        answers = evaluator.evaluate(state, questions)
        dropped = list(plan.dropped) + list(plan.omitted_for_budget) + list(scanned.dropped)
        return ReviewResult(
            obtained=True,
            summary=f"quality evidence obtained via {resolved_provider} ({model})",
            answers=list(answers),
            dropped=dropped,
            provider=resolved_provider,
            model=model,
        )
    except JevError as exc:
        return _not_obtained(str(exc))
    except Exception as exc:  # noqa: BLE001 — advisory path stays green
        return _not_obtained(f"unexpected failure: {exc}")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI stub for workflow wiring; always exits 0 and prints the summary."""
    del argv
    result = _not_obtained("CLI not wired; use run_review from the workflow")
    print(result.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
