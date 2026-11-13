#!/usr/bin/env python3
import base64
import json
import subprocess
from pathlib import Path

tool = Path(__file__).with_name("review-policy-envelope.py")
policy = {
    "actor": "maintainer;$(touch /tmp/not-executed)",
    "actor_permission": "maintain",
    "authority": "ai-merge",
    "budget_usd": "5.00",
    "fallback_budget_usd": "5.00",
    "fallback_model": "deepseek-v4-flash",
    "model": "deepseek-v4-pro",
    "pricing_version": "deepseek-v4-2026-08-10",
    "provider": "deepseek",
    "explicit_rereview": True,
}
canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"))


def run(mode, value):
    return subprocess.run(["python3", str(tool), mode, value], text=True, capture_output=True)


encoded = run("encode", canonical)
assert encoded.returncode == 0, encoded.stderr
envelope = encoded.stdout.strip()
decoded = run("decode", envelope)
assert decoded.returncode == 0 and decoded.stdout.strip() == canonical
assert "deepseek-v4-pro" in decoded.stdout and "ai-merge" in decoded.stdout

legacy = {key: policy[key] for key in ("actor", "actor_permission", "budget_usd", "model", "pricing_version", "provider")}
legacy_canonical = json.dumps(legacy, sort_keys=True, separators=(",", ":"))
legacy_encoded = run("encode", legacy_canonical)
assert legacy_encoded.returncode == 0
assert run("decode", legacy_encoded.stdout.strip()).stdout.strip() == legacy_canonical

ordinary = {key: value for key, value in policy.items() if key != "explicit_rereview"}
ordinary_canonical = json.dumps(ordinary, sort_keys=True, separators=(",", ":"))
ordinary_encoded = run("encode", ordinary_canonical)
assert ordinary_encoded.returncode == 0
assert run("decode", ordinary_encoded.stdout.strip()).stdout.strip() == ordinary_canonical

# Each mutation names the guard that must reject it: an envelope rejected by an
# earlier, unrelated check would otherwise prove nothing about its own guard.
mutations = {
    "quote stripping": (canonical.replace('"', ""), "policy envelope cannot be decoded"),
    "key reordering": (
        json.dumps(dict(reversed(tuple(policy.items()))), separators=(",", ":")),
        "policy JSON or envelope is not canonical",
    ),
    "whitespace": (json.dumps(policy, sort_keys=True), "policy JSON or envelope is not canonical"),
    "duplicate keys": (canonical[:-1] + ',"provider":"deepseek"}', "duplicate policy field: provider"),
    "extra field": (canonical[:-1] + ',"extra":"x"}', "policy must contain exactly one supported field set"),
    "missing field": (
        json.dumps({k: v for k, v in policy.items() if k != "model"}, sort_keys=True, separators=(",", ":")),
        "policy must contain exactly one supported field set",
    ),
    "string explicit flag": (
        canonical.replace('"explicit_rereview":true', '"explicit_rereview":"true"'),
        "explicit_rereview must be a boolean",
    ),
}


def assert_rejected(name, candidate, reason):
    result = run("decode", candidate)
    assert result.returncode == 1, f"accepted {name}"
    assert f"review policy rejected: {reason}" in result.stderr, f"{name} rejected without naming {reason!r}: {result.stderr!r}"


for name, (raw, reason) in mutations.items():
    assert_rejected(name, base64.urlsafe_b64encode(raw.encode()).decode().rstrip("="), reason)

for name, (candidate, reason) in {
    "invalid alphabet": (envelope + "+", "policy envelope has invalid alphabet or padding"),
    "explicit padding": (envelope + "=", "policy envelope has invalid alphabet or padding"),
    "oversized": ("A" * 2049, "policy envelope is empty or oversized"),
}.items():
    assert_rejected(name, candidate, reason)

assert not Path("/tmp/not-executed").exists(), "shell metacharacters executed"
print("PASS: canonical policy envelope rejects all boundary mutations")
