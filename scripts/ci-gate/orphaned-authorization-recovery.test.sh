#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/gate-rearm.yml"
review_workflow="$root/.github/workflows/ai-review-merge.yml"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fails=0
pass(){ printf 'ok   - %s\n' "$1"; }
fail(){ printf 'FAIL - %s\n' "$1"; fails=$((fails+1)); }

python3 - "$workflow" "$review_workflow" <<'PY' || exit 1
import sys, yaml
arm = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
review = yaml.safe_load(open(sys.argv[2], encoding="utf-8"))
run = next(step["run"] for step in arm["jobs"]["arm"]["steps"] if step.get("name") == "Create exact-head authorization receipt")
assert "# BEGIN CROSS-RUN ORPHAN RECOVERY" in run
assert "# END CROSS-RUN ORPHAN RECOVERY" in run
assert "conclusion=success" not in run[run.index("# BEGIN CROSS-RUN ORPHAN RECOVERY"):run.index("# END CROSS-RUN ORPHAN RECOVERY")]
assert review["run-name"] == "AI review authorization ${{ inputs.authorization_check_id }} from arm ${{ inputs.arm_run_id }}.${{ inputs.arm_run_attempt }}"
PY

awk '
  /# BEGIN CROSS-RUN ORPHAN RECOVERY/{capture=1; next}
  /# END CROSS-RUN ORPHAN RECOVERY/{exit}
  capture{sub(/^          /, ""); print}
' "$workflow" >"$tmp/recover.sh"
[ -s "$tmp/recover.sh" ] || { echo 'recovery block missing'; exit 1; }

mkdir "$tmp/bin"
cat >"$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CALLS"
case "$*" in
  *"actions/runs/7001/artifacts"*)
    if [ "${RECEIPT_COUNT:-0}" = 1 ]; then
      printf '[{"artifacts":[{"name":"ai-review-arm-7001-1","expired":false}]}]\n'
    elif [ "${RECEIPT_COUNT:-0}" = 2 ]; then
      printf '[{"artifacts":[{"name":"ai-review-arm-7001-1","expired":false},{"name":"ai-review-arm-7001-1","expired":false}]}]\n'
    else
      printf '[{"artifacts":[]}]\n'
    fi ;;
  "api repos/Verjson/example/actions/runs/7001")
    printf '%s\n' "${SOURCE_RUN_JSON}" ;;
  *"actions/workflows/ai-review-label-rearm.yml"*) printf '%s\n' "${LOCAL_WORKFLOW_ID:-77}" ;;
  *"actions/workflows/ai-review-lifecycle-rearm.yml"*) printf '%s\n' 88 ;;
  *"contents/.github/workflows/ai-review-label-rearm.yml?ref=main"*) printf '%040d\n' 9 ;;
  *"contents/.github/workflows/ai-review-label-rearm.yml?ref="*) printf '%s\n' "${RECEIPT_BLOB:-0000000000000000000000000000000000000009}" ;;
  *"contents/.github/workflows/ai-review-lifecycle-rearm.yml?ref=main"*) printf '%040d\n' 9 ;;
  *"contents/.github/workflows/ai-review-lifecycle-rearm.yml?ref="*) printf '%s\n' "${RECEIPT_BLOB:-0000000000000000000000000000000000000009}" ;;
  *"actions/workflows/ai-review-merge.yml/runs"*)
    printf '[{"workflow_runs":%s}]\n' "${REVIEW_RUNS_JSON:-[]}" ;;
  "api repos/Verjson/example/check-runs/9001")
    printf '%s\n' "${CURRENT_CHECK_JSON}" ;;
  "api --method PATCH repos/Verjson/example/check-runs/9001"*)
    printf '%s\n' "${PATCH_JSON}" ;;
  "run download 7001 --repo Verjson/example --name ai-review-arm-7001-1 --dir "*)
    destination="${*: -1}"
    mkdir -p "$destination"
    printf '%s\n' "$RECEIPT_JSON" >"$destination/receipt.json" ;;
  *) echo "unexpected gh call: $*" >&2; exit 2 ;;
esac
SH
chmod +x "$tmp/bin/gh"

export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" RUNNER_TEMP="$tmp"
export TARGET_REPO=Verjson/example PR_NUMBER=7 REPOSITORY_ID=42 DEFAULT_BRANCH=main
export head_sha=0123456789abcdef0123456789abcdef01234567 APP_ID=4528902 APP_SLUG=ai-review-authorization
export ACTIONS_TOKEN=actions-token GH_TOKEN=actions-token GITHUB_RUN_ID=8002 GITHUB_RUN_ATTEMPT=1
export GITHUB_SERVER_URL=https://github.com explicit_rereview=false explicit_ai_review=false
export hold_removed=false
external_id="ai-review:v1:Verjson/example:7:$head_sha:7001:1:$(printf 'a%.0s' {1..64})"
export SOURCE_RUN_JSON='{"id":7001,"run_attempt":1,"status":"completed","event":"pull_request_target","path":".github/workflows/gate-rearm.yml","head_branch":"main","completed_at":"2020-01-01T00:00:00Z","actor":{"login":"maintainer"},"head_repository":{"full_name":"Verjson/example"},"repository":{"id":42}}'
export CURRENT_CHECK_JSON="{\"id\":9001,\"status\":\"in_progress\",\"conclusion\":null,\"head_sha\":\"$head_sha\",\"external_id\":\"$external_id\",\"details_url\":\"https://github.com/Verjson/example/runs/9001\",\"app\":{\"id\":15368,\"slug\":\"github-actions\"}}"
export PATCH_JSON="{\"id\":9001,\"status\":\"completed\",\"conclusion\":\"failure\",\"head_sha\":\"$head_sha\",\"external_id\":\"$external_id\",\"details_url\":\"https://github.com/Verjson/example/runs/9001\",\"app\":{\"id\":15368,\"slug\":\"github-actions\"},\"output\":{\"title\":\"Orphaned authorization recovered\"}}"
export RECEIPT_JSON="{\"schema\":1,\"repository\":\"Verjson/example\",\"pr_number\":7,\"head_sha\":\"$head_sha\",\"check_run_id\":9001,\"arm_run_id\":7001,\"arm_run_attempt\":1,\"external_id\":\"$external_id\",\"details_url\":\"https://github.com/Verjson/example/actions/runs/7001\",\"app_id\":4528902,\"app_slug\":\"ai-review-authorization\",\"check_app_id\":15368,\"check_app_slug\":\"github-actions\"}"

write_latest(){
  export latest="$CURRENT_CHECK_JSON" latest_id=9001 latest_status=in_progress latest_conclusion='' latest_title=''
}
run_case(){
  : >"$CALLS"; write_latest
  AI_REVIEW_DISPATCH_WORKFLOW=ai-review-merge.yml \
    bash -c 'set -euo pipefail; source "$1"; printf "latest_id=%s\n" "$latest_id"' _ "$tmp/recover.sh"
}

export RECEIPT_COUNT=0 REVIEW_RUNS_JSON='[]'
if output="$(run_case 2>&1)" && grep -q '^latest_id=$' <<<"$output" \
   && grep -q 'method PATCH.*check-runs/9001' "$CALLS"; then
  pass 'a terminal source with accepted-but-lost activation and no dispatch owner is recovered'
else fail 'accepted-but-lost activation was not recovered'; fi

# GitHub can report a source run status=completed with completed_at still
# null (an observed API anomaly, not evidence the run might still be
# active); fall back to updated_at for the eventual-consistency buffer
# rather than refusing to recover forever.
source_run_json_saved="$SOURCE_RUN_JSON"
export SOURCE_RUN_JSON='{"id":7001,"run_attempt":1,"status":"completed","event":"pull_request_target","path":".github/workflows/gate-rearm.yml","completed_at":null,"updated_at":"2020-01-01T00:00:00Z","head_repository":{"full_name":"Verjson/example"},"repository":{"id":42}}'
export RECEIPT_COUNT=0 REVIEW_RUNS_JSON='[]'
if output="$(run_case 2>&1)" && grep -q '^latest_id=$' <<<"$output" \
   && grep -q 'method PATCH.*check-runs/9001' "$CALLS"; then
  pass 'a completed source run with an anomalous null completed_at is still recovered via updated_at'
else fail 'null completed_at on a completed source run blocked recovery'; fi
export SOURCE_RUN_JSON="$source_run_json_saved"

genuine_check_json="$CURRENT_CHECK_JSON"
for forged_url in https://github.com/Verjson/example/actions/runs/7001 https://github.com/Verjson/example/runs/9002; do
  forged_check_json="$(jq -c --arg url "$forged_url" '.details_url=$url' <<<"$genuine_check_json")"
  export CURRENT_CHECK_JSON="$forged_check_json"
  if ! run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then
    pass "a live check whose details_url is not its own stored page is not recovered ($forged_url) (#1648)"
  else fail "a check with a forged details_url was recovered ($forged_url)"; fi
done
export CURRENT_CHECK_JSON="$genuine_check_json"

export RECEIPT_COUNT=1
export REVIEW_RUNS_JSON='[{"display_title":"AI review authorization 9001 from arm 7001.1","event":"workflow_dispatch","path":".github/workflows/ai-review-merge.yml","head_branch":"main","status":"completed","conclusion":"failure","head_repository":{"full_name":"Verjson/example"},"repository":{"full_name":"Verjson/example"}}]'
if output="$(run_case 2>&1)" && grep -q '^latest_id=$' <<<"$output"; then
  pass 'exhausted cleanup is recovered only after its exact receipt-bound review is terminal'
else fail 'terminal receipt-bound cleanup was not recovered'; fi

export REVIEW_RUNS_JSON='[]'
if run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then
  pass 'a published receipt with a hidden or late accepted dispatch remains ambiguous'
else fail 'run-list absence was treated as proof despite a published receipt'; fi

export REVIEW_RUNS_JSON='[{"display_title":"AI review authorization 9001 from arm 7001.1","event":"workflow_dispatch","path":".github/workflows/ai-review-merge.yml","head_branch":"main","status":"in_progress","head_repository":{"full_name":"Verjson/example"},"repository":{"full_name":"Verjson/example"}}]'
if run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then
  pass 'an active exact receipt-bound downstream review retains ownership'
else fail 'active downstream ownership was terminated'; fi

export REVIEW_RUNS_JSON='[]' RECEIPT_COUNT=0
CURRENT_CHECK_JSON="${CURRENT_CHECK_JSON/\"status\":\"in_progress\"/\"status\":\"completed\"}"
if ! run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then
  pass 'a concurrent terminal transition fails closed without overwriting state'
else fail 'concurrent recovery overwrote terminal state'; fi
CURRENT_CHECK_JSON="${CURRENT_CHECK_JSON/\"status\":\"completed\"/\"status\":\"in_progress\"}"

export RECEIPT_COUNT=2
if ! run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then
  pass 'ambiguous durable receipts fail closed'
else fail 'ambiguous receipt state reached mutation'; fi

export RECEIPT_COUNT=0
SOURCE_RUN_JSON="${SOURCE_RUN_JSON/\"status\":\"completed\"/\"status\":\"in_progress\"}"
if run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then
  pass 'a nonterminal source arm cannot be recovered'
else fail 'nonterminal source arm was mutated'; fi
SOURCE_RUN_JSON="${SOURCE_RUN_JSON/\"status\":\"in_progress\"/\"status\":\"completed\"}"

if latest="$PATCH_JSON" latest_id=9001 latest_status=completed latest_conclusion=failure \
   latest_title='Orphaned authorization recovered' bash -c \
   'set -euo pipefail; hold_removed=false; explicit_rereview=false; explicit_ai_review=false; source "$1"; [ -z "$latest_id" ]' _ "$tmp/recover.sh"; then
  pass 'a rerun resumes after a prior exact recovery marker without another patch'
else fail 'idempotent recovery rerun did not resume'; fi

SOURCE_RUN_JSON="$(jq --arg head "$head_sha" '.path=".github/workflows/ai-review-label-rearm.yml" | .head_sha=$head | .workflow_id=77 | .actor={login:"maintainer"}' <<<"$SOURCE_RUN_JSON")"
export SOURCE_RUN_JSON
export RECEIPT_COUNT=0 REVIEW_RUNS_JSON='[]'
if run_case >/dev/null 2>&1 && grep -q 'method PATCH' "$CALLS"; then
  pass 'interrupted lifecycle arm with no receipt is recovered without model dispatch'
else fail 'interrupted lifecycle arm was not recovered'; fi
export LOCAL_WORKFLOW_ID=88
if ! run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then pass 'wrong lifecycle workflow identity rejected'; else fail 'wrong workflow accepted'; fi
unset LOCAL_WORKFLOW_ID
export RECEIPT_COUNT=1
RECEIPT_JSON="$(jq '.schema=2 | .workflow_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" | .workflow_ref="Verjson/example/.github/workflows/ai-review-label-rearm.yml@refs/heads/main" | .workflow_sha="0000000000000000000000000000000000000009" | .delivery_event="labeled" | .delivery_actor="maintainer"' <<<"$RECEIPT_JSON")"
export RECEIPT_JSON
export REVIEW_RUNS_JSON='[{"display_title":"AI review authorization 9001 from arm 7001.1","event":"workflow_dispatch","path":".github/workflows/ai-review-merge.yml","head_branch":"main","status":"completed","head_repository":{"full_name":"Verjson/example"},"repository":{"full_name":"Verjson/example"}}]'
if run_case >/dev/null 2>&1 && grep -q 'method PATCH' "$CALLS"; then pass 'schema-2 lifecycle receipt recovers terminal review'; else fail 'schema-2 recovery failed'; fi
RECEIPT_JSON="$(jq '.schema=1' <<<"$RECEIPT_JSON")"
export RECEIPT_JSON
if ! run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then pass 'schema-1 receipt rejected for lifecycle wrapper'; else fail 'crossed lifecycle schema accepted'; fi
RECEIPT_JSON="$(jq '.schema=2' <<<"$RECEIPT_JSON")"
export RECEIPT_JSON
SOURCE_RUN_JSON="$(jq '.path=".github/workflows/gate-rearm.yml"' <<<"$SOURCE_RUN_JSON")"
export SOURCE_RUN_JSON
if ! run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then pass 'schema-2 receipt rejected for legacy arm'; else fail 'crossed legacy schema accepted'; fi
SOURCE_RUN_JSON="$(jq '.path=".github/workflows/ai-review-label-rearm.yml"' <<<"$SOURCE_RUN_JSON")"
export SOURCE_RUN_JSON
export RECEIPT_BLOB=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
if ! run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then pass 'changed protected caller fails lifecycle recovery closed'; else fail 'changed caller accepted'; fi
unset RECEIPT_BLOB
RECEIPT_JSON="$(jq '.delivery_actor="attacker"' <<<"$RECEIPT_JSON")"
export RECEIPT_JSON
if ! run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then pass 'substituted lifecycle receipt actor rejected'; else fail 'substituted actor accepted'; fi

SOURCE_RUN_JSON="$(jq '.path=".github/workflows/ai-review-lifecycle-rearm.yml" | .workflow_id=88 | .head_sha="'"$head_sha"'" | .head_branch="main" | .actor={login:"maintainer"}' <<<"$SOURCE_RUN_JSON")"
export SOURCE_RUN_JSON
RECEIPT_JSON="$(jq '.schema=2 | .workflow_ref="Verjson/example/.github/workflows/ai-review-lifecycle-rearm.yml@refs/heads/main" | .workflow_sha="0000000000000000000000000000000000000009" | .delivery_event="ready_for_review" | .delivery_actor="maintainer"' <<<"$RECEIPT_JSON")"
export RECEIPT_JSON
if run_case >/dev/null 2>&1 && grep -q 'method PATCH' "$CALLS"; then pass 'lifecycle caller schema-2 receipt recovers terminal review'; else fail 'lifecycle caller recovery failed'; fi
for invalid_metadata in wrong-event wrong-ref wrong-branch malformed-sha wrong-workflow-id changed-blob; do
  case "$invalid_metadata" in
    wrong-event) RECEIPT_JSON="$(jq '.delivery_event="pull_request_target"' <<<"$RECEIPT_JSON")" ;;
    wrong-ref) RECEIPT_JSON="$(jq '.workflow_ref="Verjson/example/.github/workflows/ai-review-label-rearm.yml@refs/heads/main"' <<<"$RECEIPT_JSON")" ;;
    wrong-branch) RECEIPT_JSON="$(jq '.workflow_ref="Verjson/example/.github/workflows/ai-review-lifecycle-rearm.yml@refs/heads/topic"' <<<"$RECEIPT_JSON")" ;;
    malformed-sha) RECEIPT_JSON="$(jq '.workflow_sha="bad"' <<<"$RECEIPT_JSON")" ;;
    wrong-workflow-id) SOURCE_RUN_JSON="$(jq '.workflow_id=77' <<<"$SOURCE_RUN_JSON")" ;;
    changed-blob) export RECEIPT_BLOB=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ;;
  esac
  export SOURCE_RUN_JSON RECEIPT_JSON
  if ! run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then pass "lifecycle recovery rejects $invalid_metadata"; else fail "lifecycle recovery accepted $invalid_metadata"; fi
  SOURCE_RUN_JSON="$(jq '.head_branch="main" | .workflow_id=88' <<<"$SOURCE_RUN_JSON")"
  RECEIPT_JSON="$(jq '.workflow_ref="Verjson/example/.github/workflows/ai-review-lifecycle-rearm.yml@refs/heads/main" | .workflow_sha="0000000000000000000000000000000000000009" | .delivery_event="ready_for_review"' <<<"$RECEIPT_JSON")"
  unset RECEIPT_BLOB
done
SOURCE_RUN_JSON="$(jq '.path=".github/workflows/ai-review-label-rearm.yml"' <<<"$SOURCE_RUN_JSON")"
export SOURCE_RUN_JSON
if ! run_case >/dev/null 2>&1 && ! grep -q 'method PATCH' "$CALLS"; then pass 'lifecycle receipt rejected for label caller path'; else fail 'cross-caller receipt accepted'; fi

printf '%d failing assertion(s)\n' "$fails"
[ "$fails" -eq 0 ]
