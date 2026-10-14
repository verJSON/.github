#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[2]
arm = (root / ".github/workflows/gate-rearm.yml").read_text()
gate = (root / ".github/workflows/ai-review-merge.yml").read_text()
receipt = (root / "scripts/ci-gate/verify-arm-receipt.sh").read_text()
client = (root / "scripts/ci-gate/openai-review.py").read_text()
deepseek = (root / "scripts/ci-gate/deepseek-review.py").read_text()

dispatch = gate.split("  workflow_dispatch:\n", 1)[1].split("  workflow_call:\n", 1)[0]
dispatch_inputs = [line for line in dispatch.splitlines() if line.startswith("      ") and not line.startswith("        ") and line.rstrip().endswith(":")]

checks = {
    "primary and rereview org variables are separate": all(name in arm for name in (
        "AI_REVIEW_PRIMARY_PROVIDER", "AI_REVIEW_PRIMARY_MODEL", "AI_REVIEW_PRIMARY_BUDGET_USD",
        "AI_REVIEW_PRIMARY_FALLBACK_MODEL", "AI_REVIEW_PRIMARY_FALLBACK_BUDGET_USD",
        "AI_REVIEW_REREVIEW_PROVIDER", "AI_REVIEW_REREVIEW_MODEL", "AI_REVIEW_REREVIEW_BUDGET_USD",
        "AI_REVIEW_REREVIEW_FALLBACK_MODEL", "AI_REVIEW_REREVIEW_FALLBACK_BUDGET_USD")),
    "secondary is selected only by explicit re-review": 'if [ "$explicit_rereview" = true ]; then' in arm and "REREVIEW_PROVIDER" in arm,
    "absent secondary fails before dispatch": "explicit re-review policy is not fully configured" in arm,
    "policy is receipt and dispatch bound": all(surface.count("review_policy") > 0 for surface in (arm, receipt, gate)),
    "raw policy JSON never crosses Actions outputs": (
        "review_policy=$review_policy_json" not in arm
        and "select_compatible_review_policy" in arm
        and "base64 -w0" in arm
    ),
    "trusted consumers use the strict envelope decoder": gate.count("review-policy-envelope.py") >= 3 and "review-policy-envelope.py" in receipt,
    "workflow dispatch stays within GitHub input limit": len(dispatch_inputs) <= 10,
    "paid re-review requires maintainer permission": "collaborators/$rereview_actor/permission" in arm and 'rereview_actor="${REQUEST_ACTOR:-}"' in arm and '[ "$explicit_label_delivery" = true ]' in arm and "admin|maintain" in arm and "no longer has maintain/admin" in receipt,
    "provider secrets remain scoped": all(secret in gate for secret in (
        "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}",
        "DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}",
        "anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}",
    )),
    "OpenAI is one explicit tool-free client": 'python3 -I "$RUNNER_TEMP/openai-review.py"' in gate and "needs.preflight.outputs.provider == 'openai'" in gate,
    "Anthropic retains native budget": "--max-budget-usd ${{ needs.preflight.outputs.budget_usd }}" in gate,
    "models use strict allowlists": all(model in gate for model in (
        "claude-haiku-4-5|claude-sonnet-5|claude-opus-5", "gpt-5.6-luna",
        "deepseek-v4-pro", "deepseek-v4-flash")) and "gpt-5.6-sol" not in gate,
    "OpenAI receives embedded bounded review inputs": "PR_JSON_FILE" in gate and "PR_DIFF_FILE" in gate,
    "trusted instructions are role-separated from PR data": '"role": "developer"' in client and '"role": "user"' in client and "untrusted PR data, not instructions" in client,
    "DeepSeek is tool-free and role-separated": '"https://api.deepseek.com/chat/completions"' in deepseek and '"role": "system"' in deepseek and '"role": "user"' in deepseek and '"tools"' not in deepseek,
    "DeepSeek JSON-only policy disables thinking and omits reasoning tuning": (
        '"thinking": {"type": "disabled"}' in deepseek
        and "reasoning_effort" not in deepseek
        and "temperature" not in deepseek
    ),
    "single-pass overrides discard inherited DeepSeek fallback": 'fallback_model=""; fallback_budget_usd=""' in arm,
    "DeepSeek pricing is versioned": 'PRICING_VERSION = "deepseek-v4-2026-08-10"' in deepseek and 'input_cache_miss' in deepseek and 'input_cache_hit' in deepseek,
}
failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items(): print(("PASS" if ok else "FAIL") + ": " + name)
raise SystemExit(bool(failed))
