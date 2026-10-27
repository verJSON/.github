#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
sha=0123456789abcdef0123456789abcdef01234567

"$root/scripts/gen-ai-review-caller.sh" "$sha" >"$tmp/caller.yml"
python3 - "$tmp/caller.yml" "$sha" <<'PY'
import sys, yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    workflow = yaml.load(stream, Loader=yaml.BaseLoader)
sha = sys.argv[2]
expected_run_name = "AI review authorization ${{ inputs.authorization_check_id }} from arm ${{ inputs.arm_run_id }}.${{ inputs.arm_run_attempt }}"
def validate_run_name(candidate):
    assert candidate.get("run-name") == expected_run_name

validate_run_name(workflow)
for invalid in (
    None,
    "AI review",
    "AI review authorization ${{ inputs.pr_number }} from arm ${{ inputs.arm_run_id }}.${{ inputs.arm_run_attempt }}",
    "AI review authorization ${{ inputs.authorization_check_id }} from arm ${{ inputs.pr_number }}.${{ inputs.arm_run_attempt }}",
    "AI review authorization ${{ inputs.authorization_check_id }} from arm ${{ inputs.arm_run_id }}.${{ inputs.authorization_check_id }}",
    expected_run_name + " " + expected_run_name,
):
    mutation = dict(workflow)
    if invalid is None:
        mutation.pop("run-name")
    else:
        mutation["run-name"] = invalid
    try:
        validate_run_name(mutation)
    except AssertionError:
        pass
    else:
        raise AssertionError(f"invalid run-name mutation was accepted: {invalid!r}")
inputs = workflow["on"]["workflow_dispatch"]["inputs"]
assert inputs == {
    "pr_number": {"required": "true", "type": "string"},
    "expected_head_sha": {"required": "true", "type": "string"},
    "authorization_check_id": {"required": "true", "type": "string"},
    "arm_run_id": {"required": "true", "type": "string"},
    "arm_run_attempt": {"required": "true", "type": "string"},
    "explicit_rereview": {"required": "false", "type": "boolean", "default": "false"},
    "review_policy": {"required": "true", "type": "string"},
"allow_absent_checks": {"required": "false", "type": "boolean", "default": "false"},
}
assert workflow["concurrency"] == {
    "group": "ai-review-${{ github.workflow }}-${{ inputs.pr_number }}",
    "cancel-in-progress": "true",
}
assert workflow["permissions"] == {
    "actions": "write", "checks": "write", "contents": "read",
    "issues": "write", "pull-requests": "write", "statuses": "read",
}
job = workflow["jobs"]["review"]
assert job["uses"] == f"Verjson/.github/.github/workflows/ai-review-merge.yml@{sha}"
assert set(job) == {"uses", "secrets", "with"}
assert job["secrets"] == "inherit"
assert set(job["with"]) == {
    "pr_number", "expected_head_sha", "authorization_check_id", "arm_run_id",
    "arm_run_attempt", "explicit_rereview", "review_policy", "ai_review_environment",
    "allow_absent_checks",
    "contract_ref",
}
assert job["with"]["contract_ref"] == sha

def validate_contract_binding(candidate):
    review = candidate["jobs"]["review"]
    prefix = "Verjson/.github/.github/workflows/ai-review-merge.yml@"
    assert review["uses"].startswith(prefix)
    uses_sha = review["uses"][len(prefix):]
    contract_ref = review["with"]["contract_ref"]
    assert len(contract_ref) == 40
    assert all(character in "0123456789abcdef" for character in contract_ref)
    assert contract_ref == uses_sha

validate_contract_binding(workflow)
for label, invalid in (
    ("consumer head substitution", "1" * 40),
    ("stale contract substitution", "2" * 40),
    ("malformed contract input", "main"),
):
    mutation = yaml.load(yaml.dump(workflow), Loader=yaml.BaseLoader)
    mutation["jobs"]["review"]["with"]["contract_ref"] = invalid
    try:
        validate_contract_binding(mutation)
    except AssertionError:
        pass
    else:
        raise AssertionError(f"{label} escaped the generated caller contract")
PY

"$root/scripts/gen-ai-review-caller.sh" --local >"$tmp/local-caller.yml"
python3 - "$tmp/local-caller.yml" <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    workflow = yaml.load(stream, Loader=yaml.BaseLoader)
assert workflow["name"] == "AI review + auto-merge"
inputs = workflow["on"]["workflow_dispatch"]["inputs"]
assert inputs["ai_review_environment"]["default"] == "ai-review-app"
assert workflow["concurrency"]["cancel-in-progress"] == "true"
job = workflow["jobs"]["review"]
assert job["uses"] == "./.github/workflows/ai-review-merge.yml"
assert job["with"]["contract_ref"] == "${{ github.sha }}"
PY

grep -qF "scripts/gen-ai-review-caller.sh $sha > .github/workflows/ai-review-merge.yml" "$tmp/caller.yml"
grep -qF "scripts/gen-ai-review-caller.sh --local > .github/workflows/ai-review-merge-dispatch.yml" "$tmp/local-caller.yml"
if grep -Eq '(^|@)(main|master|v[0-9]+)([[:space:]]|$)' "$tmp/caller.yml"; then
  echo 'generated review caller contains a mutable reference' >&2
  exit 1
fi
for invalid in main v1 0123456789abcdef0123456789abcdef0123456 ''; do
  if "$root/scripts/gen-ai-review-caller.sh" "$invalid" >/dev/null 2>&1; then
    echo "generator accepted unsafe ref: ${invalid:-<empty>}" >&2
    exit 1
  fi
done

echo 'AI review caller generator contract: ok'
