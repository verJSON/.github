#!/usr/bin/env bash
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
generator="$repo_root/scripts/gen-gate-rearm-caller.sh"
canonical="$repo_root/.github/workflows/gate-rearm.yml"
actions_ci="$repo_root/scripts/actions-ci-groups.tsv"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

contract_sha=0123456789abcdef0123456789abcdef01234567
caller="$tmp/gate-rearm.yml"

if [ ! -x "$generator" ]; then
  fail "gate re-arm caller generator is missing or not executable"
else
  "$generator" "$contract_sha" >"$caller" \
    && pass "generator emits a caller for an immutable contract SHA" \
    || fail "generator rejected a valid immutable contract SHA"
fi

if [ -s "$caller" ]; then
  python3 - "$caller" "$contract_sha" <<'PY' \
    && pass "generated caller has the exact thin, privileged trigger contract" \
    || fail "generated caller shape, permissions, trigger, or immutable target drifted"
import sys
import yaml

path, sha = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    doc = yaml.load(stream, Loader=yaml.BaseLoader)

expected_uses = f"Verjson/.github/.github/workflows/gate-rearm.yml@{sha}"
assert set(doc) == {"name", "on", "permissions", "jobs"}
assert doc["on"]["workflow_call"]["inputs"]["ai_review_environment"]["type"] == "string"
assert doc["on"]["workflow_call"]["inputs"]["ai_review_environment"]["required"] == "true"
assert doc["on"]["pull_request_target"] == {
    "types": ["opened", "reopened", "synchronize"],
}
assert doc["permissions"] == {"contents": "read"}
assert set(doc["jobs"]) == {"rearm"}
job = doc["jobs"]["rearm"]
# ADR 0166 fixes this role environment to `ai-review-app` for both trigger
# paths. The reusable input is required by the workflow-call contract; the
# generated-caller contract test pins these callers to the fixed role name.
assert job == {
    "permissions": {
        "contents": "read",
        "actions": "write",
        "checks": "write",
        "issues": "write",
        "pull-requests": "write",
    },
    "uses": expected_uses,
    "secrets": "inherit",
    "with": {"ai_review_environment": "ai-review-app"},
}
PY

  grep -qF "scripts/gen-gate-rearm-caller.sh $contract_sha" "$caller" \
    && pass "generated caller records an exact reproducible command" \
    || fail "generated caller lacks exact regeneration provenance"
  grep -qE '^    secrets: inherit$' "$caller" || fail "caller lacks inherited environment context"
  if grep -qE 'actions/checkout|github\.event\.pull_request\.(head|body|title)|^[[:space:]]+run:' "$caller"; then
    fail "generated pull_request_target caller can execute or expose PR-controlled content"
  else
    pass "generated caller delegates without checkout, shell, or PR prose"
  fi
fi

label_generator="$repo_root/scripts/gen-ai-review-label-rearm-caller.sh"
label_caller="$tmp/ai-review-label-rearm.yml"
lifecycle_generator="$repo_root/scripts/gen-ai-review-lifecycle-rearm-caller.sh"
lifecycle_workflow="$repo_root/.github/workflows/ai-review-lifecycle-rearm.yml"
lifecycle_caller="$tmp/ai-review-lifecycle-rearm.yml"
if [ ! -x "$label_generator" ]; then
  fail "AI review label re-arm caller generator missing or not executable"
elif ! "$label_generator" "$contract_sha" >"$label_caller"; then
  fail "AI review label re-arm caller generator rejected a valid contract SHA"
elif [ ! -f "$lifecycle_workflow" ]; then
  fail "canonical lifecycle-only re-arm caller is missing"
elif [ ! -x "$lifecycle_generator" ]; then
  fail "AI review lifecycle re-arm caller generator missing or not executable"
elif ! "$lifecycle_generator" --local >"$lifecycle_caller"; then
  fail "AI review lifecycle re-arm caller generator rejected --local"
elif ! cmp -s "$lifecycle_caller" "$lifecycle_workflow"; then
  fail "checked-in lifecycle re-arm caller differs from its generator"
elif python3 - "$label_caller" "$caller" "$canonical" "$lifecycle_caller" <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    label_doc = yaml.load(stream, Loader=yaml.BaseLoader)
with open(sys.argv[2], encoding="utf-8") as stream:
    gate_doc = yaml.load(stream, Loader=yaml.BaseLoader)
with open(sys.argv[3], encoding="utf-8") as stream:
    canonical_doc = yaml.load(stream, Loader=yaml.BaseLoader)
with open(sys.argv[4], encoding="utf-8") as stream:
    lifecycle_doc = yaml.load(stream, Loader=yaml.BaseLoader)

label_events = label_doc["on"]["pull_request_target"]["types"]
gate_events = gate_doc["on"]["pull_request_target"]["types"]
canonical_events = canonical_doc["on"]["pull_request_target"]["types"]
lifecycle_events = lifecycle_doc["on"]["pull_request_target"]["types"]
expected_lifecycle_events = ["ready_for_review", "converted_to_draft", "edited", "unlabeled"]
assert label_events == ["labeled"], f"label re-arm caller owns extra events: {label_events!r}"
assert gate_events == canonical_events, "generated gate caller diverged from canonical head-transition events"
assert lifecycle_events == expected_lifecycle_events, (
    f"local lifecycle caller does not own the remaining gate events: {lifecycle_events!r}"
)
assert set(lifecycle_events).isdisjoint(canonical_events), "lifecycle and head-transition triggers overlap"
assert set(lifecycle_events).isdisjoint(label_events), "lifecycle and explicit-label triggers overlap"
assert set(canonical_events).isdisjoint(label_events), "head-transition and explicit-label triggers overlap"

lifecycle_job = lifecycle_doc["jobs"]["rearm"]
assert set(lifecycle_doc["jobs"]) == {"rearm"}
assert lifecycle_job["uses"] == "./.github/workflows/gate-rearm.yml"
assert lifecycle_job["secrets"] == "inherit"
assert lifecycle_job["with"] == {"ai_review_environment": "ai-review-app"}
assert lifecycle_job["permissions"] == {
    "actions": "write",
    "checks": "write",
    "contents": "read",
    "issues": "write",
    "pull-requests": "write",
}
PY
then
  pass "canonical lifecycle caller covers only gate events beyond head and label triggers"
else
  fail "canonical event sets overlap or lifecycle re-arm caller contract drifted"
fi

for invalid_ref in main v1 '' \
  0123456789abcdef0123456789abcdef0123456 \
  '0123456789abcdef0123456789abcdef01234567
jobs: {}'; do
  "$generator" "$invalid_ref" >"$tmp/rejected.yml" 2>/dev/null \
    && fail "generator accepted mutable, malformed, or injected ref '$invalid_ref'" \
    || pass "generator rejects unsafe ref '${invalid_ref:-<empty>}'"
done
"$generator" "$contract_sha" extra >"$tmp/rejected.yml" 2>/dev/null \
  && fail "generator ignored an extra argument" \
  || pass "generator rejects extra arguments"

grep -qE '^  workflow_call:' "$canonical" \
  && pass "canonical bridge accepts generated reusable callers" \
  || fail "canonical bridge has no workflow_call entry point"
python3 - "$canonical" <<'PY' \
  && pass "canonical arm preserves exact permissions, concurrency, metadata validation and no-head-checkout boundaries" \
  || fail "canonical arm structural trust boundary drifted"
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    doc = yaml.load(stream, Loader=yaml.BaseLoader)

assert doc["permissions"] == {"actions": "read", "contents": "read"}
assert doc["concurrency"] == {"group": "ai-review-arm-${{ github.event.pull_request.number }}", "cancel-in-progress": "false"}
arm = doc["jobs"]["arm"]
assert arm["permissions"] == {
    "actions": "write",
    "checks": "write",
    "contents": "read",
    "issues": "write",
    "pull-requests": "write",
}
assert arm["env"]["PR_NUMBER"] == "${{ github.event.pull_request.number }}"
assert arm["env"]["TARGET_REPO"] == "${{ github.repository }}"
arm_run = next(step["run"] for step in arm["steps"] if step.get("id") == "arm")
assert "app.id == 15368" in arm_run and "app.id == $APP_ID" not in arm_run
assert "check_app_id" in arm_run and "check_app_slug" in arm_run
assert ".check_app_id // .app_id" not in arm_run and ".check_app_slug // .app_slug" not in arm_run
assert "legacy AI App-owned authorization could not be safely recovered" not in arm_run
assert "no duplicate review was dispatched" in arm_run
assert arm["needs"] == ["event-policy", "app-key-policy"]
app_key_policy = doc["jobs"]["app-key-policy"]
assert app_key_policy["uses"] == "Verjson/.github/.github/workflows/app-key-environment.yml@ebe23fafbdb77dd2cca6f1862d932fde881b80d7"
assert app_key_policy["needs"] == "event-policy"
assert not any(step.get("uses", "").startswith("actions/checkout@") for step in arm["steps"])
assert not any(".gate-trust" in str(step) for step in arm["steps"])
create = next(step for step in arm["steps"] if step.get("id") == "arm")
assert create["env"]["APP_KEY_POLICY_RESULT"] == "${{ needs.app-key-policy.result }}"
assert "AI_REVIEW_APP_PRIVATE_KEY" not in create["env"]
assert 'if [ "$APP_KEY_POLICY_RESULT" != success ]; then' in create["run"]
script = "\n".join(step.get("run", "") for step in arm["steps"])
for marker in (
    '[[ "$TARGET_REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]',
    '[[ "$PR_NUMBER" =~ ^[1-9][0-9]*$ ]]',
    'gh pr view "$PR_NUMBER" --repo "$TARGET_REPO"',
    '--json id,state,isDraft,title,labels,headRefOid,headRepositoryOwner,autoMergeRequest',
    '[[ "$head_sha" =~ ^[0-9a-f]{40}$ ]]',
    '[ "$state" = OPEN ] || exit 0',
):
    assert marker in script
PY
if grep -qF 'APP_ID: ${{ vars.AI_REVIEW_APP_ID }}' "$canonical" \
   && grep -qF -- '--argjson app_id 15368' "$canonical" \
   && grep -qF '.app.id == $app_id' "$canonical" \
   && ! grep -qF 'Mint dedicated authorization App token' "$canonical"; then
  pass "canonical arm binds the Actions check identity without minting an App token"
else
  fail "canonical arm lost Actions check identity or regained App token authority"
fi

# The immutable target matters only while the current executable arm, event
# authorization, and receipt verifier contracts remain green. Point at the
# registered suites that own each behavior instead of asking the receipt verifier
# suite to stand in for unrelated arm logic (#733).
for replacement in \
  'bash scripts/ci-gate/gate-hold-disable.test.sh' \
  'python3 scripts/ci-gate/event-driven-authorization.test.py' \
  'bash scripts/ci-gate/arm-receipt.test.sh'; do
  grep -q $'\t'"$replacement"'$' "$actions_ci" \
    && pass "actions-ci registers the canonical replacement: $replacement" \
    || fail "canonical replacement is not registered: $replacement"
done
# The behavior suites above are separate manifest rows. Their independent runs
# preserve the coverage without replaying them inside this caller-contract test.
grep -q $'\tbash scripts/ci-gate/gate-rearm-caller-contract.test.sh$' "$actions_ci" \
  && pass "actions-ci executes the generated caller contract" \
  || fail "generated caller contract is not wired into actions-ci"

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
