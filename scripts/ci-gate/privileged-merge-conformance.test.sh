#!/usr/bin/env bash
# shellcheck disable=SC2015
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
audit="$root/scripts/privileged-merge-conformance.sh"
generator="$root/scripts/gen-privileged-merge-caller.sh"
workflow="$root/.github/workflows/privileged-merge-conformance.yml"
contract_sha=848c49fd4dac307f26180acd420760a27ceff0ba
alternate_contract_sha=a6b3ccc0590f4fcfdacd7818279ab3eea6b30155
absent_contract_sha=0123456789abcdef0123456789abcdef01234567
audit_sha=abcdef0123456789abcdef0123456789abcdef01
required_checks='[{"name":"shell-tests","app_id":15368,"workflow_id":315894159,"workflow_path":".github/workflows/actions-ci.yml"}]'
retry_workflow_names='["actions-ci"]'
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

mkdir "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$GH_CALLS"
case "$*" in
  "api orgs/Verjson/actions/secrets/MERGE_APP_PRIVATE_KEY --jq .visibility")
    printf '%s\n' "${SECRET_VISIBILITY:-selected}"
    ;;
  "api --paginate orgs/Verjson/actions/secrets/MERGE_APP_PRIVATE_KEY/repositories --jq .repositories[].full_name")
    printf '%s\n' "${SECRET_REPOSITORIES:-Verjson/alpha}"
    ;;
  "api --paginate orgs/Verjson/repos?type=all&per_page=100 --jq .[] | select((.archived or .fork or .is_template) | not) | .full_name")
    printf '%s\n' "${ACTIVE_REPOSITORIES:-Verjson/alpha}"
    ;;
  api*"repos/Verjson/alpha/contents/.github/workflows/ai-privileged-merge.yml?ref=main"*"--jq .content")
    case "${ALPHA_CALLER:-present}" in
      present) ;;
      missing) echo "HTTP 404: Not Found" >&2; exit 1 ;;
      unreadable) echo "HTTP 500: unavailable" >&2; exit 1 ;;
    esac
    printf '%s\n' "$ALPHA_CONTENT"
    ;;
  api*"repos/Verjson/alpha/contents/.github/workflows/ai-promotion-retry.yml?ref=main"*"--jq .content")
    [ "${ALPHA_RETRY_CALLER:-present}" = present ] || { echo "HTTP 404: Not Found" >&2; exit 1; }
    printf '%s\n' "$ALPHA_RETRY_CONTENT"
    ;;
  api*"repos/Verjson/beta/contents/.github/workflows/ai-privileged-merge.yml?ref=trunk"*"--jq .content")
    case "${BETA_CALLER:-present}" in
      present) ;;
      missing) echo "HTTP 404: Not Found" >&2; exit 1 ;;
      unreadable) echo "HTTP 500: unavailable" >&2; exit 1 ;;
    esac
    printf '%s\n' "$BETA_CONTENT"
    ;;
  api*"repos/Verjson/beta/contents/.github/workflows/ai-promotion-retry.yml?ref=trunk"*"--jq .content")
    [ "${BETA_RETRY_CALLER:-present}" = present ] || { echo "HTTP 404: Not Found" >&2; exit 1; }
    printf '%s\n' "$BETA_RETRY_CONTENT"
    ;;
  api*"repos/Verjson/.github/contents/.github/workflows/ai-privileged-merge.yml?ref=$PRIVILEGED_MERGE_AUDIT_SHA"*"--jq .content")
    case "${CANONICAL_CALLER:-present}" in
      present) ;;
      missing) echo "HTTP 404: Not Found" >&2; exit 1 ;;
      unreadable) echo "HTTP 500: unavailable" >&2; exit 1 ;;
    esac
    printf '%s\n' "$CANONICAL_CONTENT"
    ;;
  api*"repos/Verjson/.github/contents/.github/workflows/ai-promotion-retry.yml?ref=$PRIVILEGED_MERGE_AUDIT_SHA"*"--jq .content")
    [ "${CANONICAL_RETRY_CALLER:-present}" = present ] || { echo "HTTP 404: Not Found" >&2; exit 1; }
    printf '%s\n' "$CANONICAL_RETRY_CONTENT"
    ;;
  api*"repos/Verjson/.github/compare/"*"...main --jq .status")
    case "$*" in
      *"/0123456789abcdef0123456789abcdef01234567...main"*) echo "HTTP 404: Not Found" >&2; exit 1 ;;
      *) printf '%s\n' "${PIN_RELATION:-ahead}" ;;
    esac
    ;;
  api*"repos/Verjson/.github/contents/scripts/gen-privileged-merge-caller.sh?ref="*"--jq .content")
    printf '%s\n' "$HISTORICAL_GENERATOR_CONTENT"
    ;;
  api*"repos/Verjson/.github/contents/.github/workflows/ai-privileged-merge.yml?ref="*"--jq .content")
    printf '%s\n' "$HISTORICAL_WORKFLOW_CONTENT"
    ;;
  api*"repos/Verjson/.github/contents/.github/workflows/ai-promotion-retry.yml?ref="*"--jq .content")
    printf '%s\n' "$HISTORICAL_RETRY_WORKFLOW_CONTENT"
    ;;
  "api repos/Verjson/alpha --jq [.default_branch,.visibility] | @tsv")
    printf '%s\n' $'main\tpublic'
    ;;
  "api repos/Verjson/beta --jq [.default_branch,.visibility] | @tsv")
    printf '%s\n' $'trunk\tprivate'
    ;;
  "api repos/Verjson/.github --jq [.default_branch,.visibility] | @tsv")
    printf '%s\n' $'main\tpublic'
    ;;
  api*"repos/"*"/rules/branches/"*)
    case "${RULE_BINDING_MODE:-valid}" in
      valid) printf '[{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"shell-tests","integration_id":%s}]}}]\n' "${RULE_APP_ID:-15368}" ;;
      missing) printf '%s\n' '[{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"shell-tests"}]}}]' ;;
      ambiguous) printf '%s\n' '[{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"shell-tests","integration_id":15368},{"context":"shell-tests","integration_id":999}]}}]' ;;
    esac
    ;;
  api*"repos/"*"/actions/workflows/"*"/runs?event=pull_request&per_page=100&page="*)
    page=""
    for arg in "$@"; do [[ "$arg" == *'&page='* ]] && page="${arg##*&page=}"; done
    case "${RUN_SEARCH_MODE:-valid}" in
      valid) printf '%s\n' '{"workflow_runs":[{"id":7002,"head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","status":"completed","conclusion":"success"}]}' ;;
      missing) printf '%s\n' '{"workflow_runs":[]}' ;;
      queued-then-completed)
        if [ "$page" = 1 ]; then
          jq -nc '{workflow_runs:[range(0;100) as $i | {id:(8000+$i),head_sha:"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",status:"queued",conclusion:null}]}'
        else
          printf '%s\n' '{"workflow_runs":[{"id":7002,"head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","status":"completed","conclusion":"success"}]}'
        fi ;;
      saturated)
        jq -nc --argjson base "$((page * 1000))" '{workflow_runs:[range(0;100) as $i | {id:($base+$i),head_sha:"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",status:"queued",conclusion:null}]}' ;;
    esac
    ;;
  api*"repos/"*"/actions/workflows/"*)
    [ "${WORKFLOW_EVIDENCE_MODE:-valid}" != deleted ] || { echo 'HTTP 404: Not Found' >&2; exit 1; }
    printf '{"id":%s,"name":"%s","path":"%s","state":"%s"}\n' \
      "${WORKFLOW_ID:-315894159}" "${WORKFLOW_NAME:-actions-ci}" "${WORKFLOW_PATH:-.github/workflows/actions-ci.yml}" "${WORKFLOW_STATE:-active}"
    ;;
  api*"repos/"*"/actions/runs/7002/jobs?per_page=100"*)
    endpoint=""
    for arg in "$@"; do [[ "$arg" == repos/* ]] && endpoint="$arg"; done
    repository="$(sed -E 's#^repos/([^/]+/[^/]+)/.*#\1#' <<<"$endpoint")"
    if [ "${WORKFLOW_EVIDENCE_MODE:-valid}" = renamed-check ]; then
      printf '%s\n' "{\"jobs\":[{\"id\":8002,\"name\":\"renamed-shell-tests\",\"check_run_url\":\"https://api.github.com/repos/$repository/check-runs/9002\"}]}"
    else
      printf '%s\n' "{\"jobs\":[{\"id\":8002,\"name\":\"shell-tests\",\"check_run_url\":\"https://api.github.com/repos/$repository/check-runs/9002\"}]}"
    fi
    ;;
  api*"https://api.github.com/repos/"*"/check-runs/9002"*)
    printf '{"name":"shell-tests","app":{"id":%s}}\n' "${CHECK_APP_ID:-15368}"
    ;;
  *)
    printf 'unexpected gh call: %s\n' "$*" >&2
    exit 97
    ;;
esac
GH
chmod +x "$tmp/bin/gh"

canonical_caller_baseline="$(bash "$generator" "$contract_sha" "$required_checks" | base64 | tr -d '\n')"
canonical_retry_caller_baseline="$(bash "$generator" "$contract_sha" --retry "$retry_workflow_names" "$required_checks" | base64 | tr -d '\n')"
historical_workflow_baseline="$(printf '%s\n' \
  'on:' \
  '  workflow_call:' \
  '    inputs:' \
  '      required_checks:' \
  '        required: true' \
  '        type: string' \
  '      privileged_lane:' \
  '        required: false' \
  '        type: string' \
  'jobs:' \
  '  privileged_merge:' \
  '    runs-on: ubuntu-24.04' | base64 | tr -d '\n')"
historical_retry_workflow_baseline="$(printf '%s\n' \
  'on:' \
  '  workflow_call:' \
  '    inputs:' \
  '      required_checks:' \
  '        required: true' \
  '        type: string' \
  'jobs:' \
  '  retry:' \
  '    runs-on: ubuntu-24.04' | base64 | tr -d '\n')"
canonical_workflow_baseline="$(base64 <"$root/.github/workflows/ai-privileged-merge.yml" | tr -d '\n')"
canonical_retry_workflow_baseline="$(base64 <"$root/.github/workflows/ai-promotion-retry.yml" | tr -d '\n')"
historical_generator_baseline="$(base64 <"$generator" | tr -d '\n')"

run_audit() {
  : >"$tmp/gh-calls"
  PATH="$tmp/bin:$PATH" GH_TOKEN="${GH_TOKEN-test-token}" \
    ACTIVE_REPOSITORIES="${ACTIVE_REPOSITORIES-Verjson/alpha}" \
    SECRET_VISIBILITY="${SECRET_VISIBILITY-selected}" \
    SECRET_REPOSITORIES="${SECRET_REPOSITORIES-Verjson/alpha}" \
    ALPHA_CALLER="${ALPHA_CALLER-present}" \
    BETA_CALLER="${BETA_CALLER-present}" \
    CANONICAL_CALLER="${CANONICAL_CALLER-present}" \
    ALPHA_RETRY_CALLER="${ALPHA_RETRY_CALLER-present}" \
    BETA_RETRY_CALLER="${BETA_RETRY_CALLER-present}" \
    CANONICAL_RETRY_CALLER="${CANONICAL_RETRY_CALLER-present}" \
    ALPHA_CONTENT="${ALPHA_CONTENT-$canonical_caller_baseline}" \
    BETA_CONTENT="${BETA_CONTENT-$canonical_caller_baseline}" \
    CANONICAL_CONTENT="${CANONICAL_CONTENT-$canonical_workflow_baseline}" \
    ALPHA_RETRY_CONTENT="${ALPHA_RETRY_CONTENT-$canonical_retry_caller_baseline}" \
    BETA_RETRY_CONTENT="${BETA_RETRY_CONTENT-$canonical_retry_caller_baseline}" \
    CANONICAL_RETRY_CONTENT="${CANONICAL_RETRY_CONTENT-$canonical_retry_workflow_baseline}" \
    HISTORICAL_GENERATOR_CONTENT="${HISTORICAL_GENERATOR_CONTENT-$historical_generator_baseline}" \
    HISTORICAL_WORKFLOW_CONTENT="${HISTORICAL_WORKFLOW_CONTENT-$historical_workflow_baseline}" \
    HISTORICAL_RETRY_WORKFLOW_CONTENT="${HISTORICAL_RETRY_WORKFLOW_CONTENT-$historical_retry_workflow_baseline}" \
    PIN_RELATION="${PIN_RELATION-ahead}" \
    GH_CALLS="$tmp/gh-calls" \
    WORKFLOW_EVIDENCE_MODE="${WORKFLOW_EVIDENCE_MODE-valid}" \
    RUN_SEARCH_MODE="${RUN_SEARCH_MODE-valid}" \
    RULE_BINDING_MODE="${RULE_BINDING_MODE-valid}" \
    RULE_APP_ID="${RULE_APP_ID-15368}" \
    WORKFLOW_ID="${WORKFLOW_ID-315894159}" \
    WORKFLOW_NAME="${WORKFLOW_NAME-actions-ci}" \
    WORKFLOW_PATH="${WORKFLOW_PATH-.github/workflows/actions-ci.yml}" \
    WORKFLOW_STATE="${WORKFLOW_STATE-active}" \
    CHECK_APP_ID="${CHECK_APP_ID-15368}" \
    PRIVILEGED_MERGE_AUDIT_SHA="${PRIVILEGED_MERGE_AUDIT_SHA-$audit_sha}" \
    bash "$audit" >"$tmp/out" 2>&1
}

run_audit \
  && grep -q 'result=conformant repositories_scanned=1 consumers=1' "$tmp/out" \
  && pass "active managed repository with caller and selected secret access conforms" \
  || fail "conformant repository did not pass: $(<"$tmp/out")"

ALPHA_RETRY_CALLER=missing run_audit \
  && fail "consumer without a generated promotion retry reported green" \
  || {
    grep -q 'Missing or unreadable generated promotion retry' "$tmp/out" \
      && pass "conformance requires both generated privileged callers" \
      || fail "missing promotion retry lacks conformance evidence"
  }

ALPHA_RETRY_CONTENT="$(bash "$generator" "$contract_sha" --retry "$retry_workflow_names" "$required_checks" | sed 's/name: AI terminal promotion retry/name: drifted retry/' | base64 | tr -d '\n')" run_audit \
  && fail "non-canonical promotion retry reported green" \
  || {
    grep -q 'Non-canonical promotion retry caller' "$tmp/out" \
      && pass "promotion retry bytes are reconstructed with the pinned historical generator" \
      || fail "promotion retry byte drift lacks regeneration evidence"
  }

retry_drift_checks='[{"name":"shell-tests","app_id":15368,"workflow_id":999,"workflow_path":".github/workflows/renamed.yml"}]'
ALPHA_RETRY_CONTENT="$(bash "$generator" "$contract_sha" --retry "$retry_workflow_names" "$retry_drift_checks" | base64 | tr -d '\n')" run_audit \
  && fail "promotion retry with weakened required-check policy reported green" \
  || {
    grep -q 'Promotion retry required-check policy drift' "$tmp/out" \
      && pass "promotion retry cannot substitute or weaken the privileged caller policy" \
      || fail "promotion retry policy substitution was not detected"
  }

ALPHA_RETRY_CONTENT="$(bash "$generator" "$contract_sha" --retry "$retry_workflow_names" "$required_checks" | sed '/^      required_checks:/d' | base64 | tr -d '\n')" run_audit \
  && fail "promotion retry that omitted the required-check policy reported green" \
  || {
    grep -Eq 'Invalid reviewed promotion retry policy|Promotion retry required-check policy drift' "$tmp/out" \
      && pass "promotion retry cannot omit the privileged caller policy" \
      || fail "omitted promotion retry policy was not detected: $(<"$tmp/out")"
  }

ALPHA_RETRY_CONTENT="$(bash "$generator" "$contract_sha" --retry '["renamed-ci"]' "$required_checks" | base64 | tr -d '\n')" run_audit \
  && fail "promotion retry with unrelated workflow names reported green" \
  || {
    grep -q 'Retry workflow names do not match required-check workflow IDs' "$tmp/out" \
      && pass "retry workflow names are bound to the policy workflow IDs" \
      || fail "retry workflow name substitution was not detected"
  }

ALPHA_RETRY_CONTENT="$(bash "$generator" "$alternate_contract_sha" --retry "$retry_workflow_names" "$required_checks" | base64 | tr -d '\n')" run_audit \
  && fail "promotion retry pinned differently from privileged merge reported green" \
  || {
    grep -q 'Invalid promotion retry caller pin' "$tmp/out" \
      && pass "both generated callers must share the exact immutable pin" \
      || fail "promotion retry pin drift was not detected"
  }

RULE_BINDING_MODE=missing run_audit \
  && fail "required status check without an App binding reported green" \
  || {
    grep -q 'lacks an exact App binding' "$tmp/out" \
      && pass "conformance rejects a native required check without integration identity" \
      || fail "missing native App binding lacks conformance evidence"
  }

RULE_APP_ID=999 run_audit \
  && fail "required status check with a mismatched App binding reported green" \
  || {
    grep -q 'does not match effective branch protection' "$tmp/out" \
      && pass "conformance compares required context and App identity together" \
      || fail "mismatched native App binding lacks conformance evidence"
  }

RULE_BINDING_MODE=ambiguous run_audit \
  && fail "same-name required checks with different App identities reported green" \
  || {
    grep -q 'Ambiguous required-check rule identity' "$tmp/out" \
      && pass "conformance preserves multiplicity and rejects ambiguous same-name contexts" \
      || fail "ambiguous native App bindings were collapsed"
  }

RUN_SEARCH_MODE=queued-then-completed run_audit \
  && grep -q 'page=2' "$tmp/gh-calls" \
  && pass "bounded evidence pagination looks past newer queued noise" \
  || fail "completed evidence on a later bounded page was hidden: $(<"$tmp/out") calls=$(tr '\n' ';' <"$tmp/gh-calls")"

RUN_SEARCH_MODE=saturated run_audit \
  && fail "saturated workflow evidence search reported green" \
  || {
    grep -q 'evidence exceeded bound' "$tmp/out" \
      && grep -q 'page=5' "$tmp/gh-calls" \
      && ! grep -q 'page=6' "$tmp/gh-calls" \
      && pass "workflow evidence search fails closed at its explicit page bound" \
      || fail "workflow evidence page bound is missing or bypassable: $(<"$tmp/out") calls=$(tr '\n' ';' <"$tmp/gh-calls")"
  }

ALPHA_CONTENT="$(bash "$generator" "$contract_sha" "$required_checks" | sed '/^      required_checks:/d' | base64 | tr -d '\n')" run_audit \
  && fail "caller without a reviewed required-check policy reported green" \
  || {
    grep -q 'Missing reviewed required-check policy' "$tmp/out" \
      && pass "adoption fails closed when the generated caller omits its reviewed policy" \
      || fail "missing policy lacks adoption-time evidence"
  }

WORKFLOW_EVIDENCE_MODE=renamed-check run_audit \
  && fail "renamed required check reported green" \
  || {
    grep -q 'Missing or renamed required check' "$tmp/out" \
      && pass "conformance rejects a reviewed check name no longer published by its workflow" \
      || fail "renamed check lacks conformance-time evidence"
  }

WORKFLOW_EVIDENCE_MODE=deleted run_audit \
  && fail "deleted required workflow reported green" \
  || {
    grep -q 'Invalid required-check workflow identity' "$tmp/out" \
      && pass "conformance rejects a deleted reviewed workflow" \
      || fail "deleted workflow lacks conformance-time evidence"
  }

wrong_app_checks='[{"name":"shell-tests","app_id":999,"workflow_id":315894159,"workflow_path":".github/workflows/actions-ci.yml"}]'
RULE_APP_ID=999 ALPHA_CONTENT="$(bash "$generator" "$contract_sha" "$wrong_app_checks" | base64 | tr -d '\n')" run_audit \
  && fail "wrong required-check App identity reported green" \
  || {
    grep -q 'Wrong required-check App identity' "$tmp/out" \
      && pass "conformance rejects a required check published by the wrong GitHub App" \
      || fail "wrong App lacks conformance-time evidence"
  }

wrong_workflow_checks='[{"name":"shell-tests","app_id":15368,"workflow_id":999,"workflow_path":".github/workflows/actions-ci.yml"}]'
ALPHA_CONTENT="$(bash "$generator" "$contract_sha" "$wrong_workflow_checks" | base64 | tr -d '\n')" run_audit \
  && fail "wrong required-check workflow ID reported green" \
  || {
    grep -q 'Invalid required-check workflow identity' "$tmp/out" \
      && pass "conformance rejects a wrong repository-specific workflow ID" \
      || fail "wrong workflow ID lacks conformance-time evidence"
  }

wrong_path_checks='[{"name":"shell-tests","app_id":15368,"workflow_id":315894159,"workflow_path":".github/workflows/renamed.yml"}]'
ALPHA_CONTENT="$(bash "$generator" "$contract_sha" "$wrong_path_checks" | base64 | tr -d '\n')" run_audit \
  && fail "wrong required-check workflow path reported green" \
  || {
    grep -q 'Invalid required-check workflow identity' "$tmp/out" \
      && pass "conformance rejects a wrong repository-specific workflow path" \
      || fail "wrong workflow path lacks conformance-time evidence"
  }

ACTIVE_REPOSITORIES=$'Verjson/.github\nVerjson/alpha' \
  SECRET_REPOSITORIES=$'Verjson/.github\nVerjson/alpha' run_audit \
  && grep -q 'result=conformant repositories_scanned=2 consumers=2' "$tmp/out" \
  && pass "canonical direct workflow is inventoried and byte-bound to the audit SHA" \
  || fail "canonical direct consumer was omitted or misclassified"

ACTIVE_REPOSITORIES=Verjson/.github SECRET_REPOSITORIES=Verjson/.github \
  CANONICAL_RETRY_CALLER=missing run_audit \
  && fail "missing canonical promotion retry reported green" \
  || {
    grep -q 'Missing or unreadable canonical promotion retry' "$tmp/out" \
      && pass "canonical conformance inventories the promotion retry workflow" \
      || fail "missing canonical promotion retry lacks audit evidence"
  }

ACTIVE_REPOSITORIES=Verjson/.github SECRET_REPOSITORIES=Verjson/.github \
  CANONICAL_RETRY_CONTENT="$(printf '%s\n' 'name: corrupt canonical retry' | base64 | tr -d '\n')" run_audit \
  && fail "mismatched canonical promotion retry reported green" \
  || {
    grep -q 'Mismatched canonical promotion retry' "$tmp/out" \
      && pass "canonical promotion retry bytes are bound to the audit SHA" \
      || fail "canonical promotion retry mismatch lacks audit evidence"
  }

ACTIVE_REPOSITORIES=Verjson/.github SECRET_REPOSITORIES=Verjson/.github \
  CANONICAL_CALLER=missing run_audit \
  && fail "missing canonical direct workflow reported green" \
  || {
    grep -q 'Missing canonical privileged merge workflow' "$tmp/out" \
      && grep -q 'consumers=1' "$tmp/out" \
      && pass "canonical direct workflow is required even when its content API returns 404" \
      || fail "missing canonical direct workflow was treated as a non-consumer"
  }

ACTIVE_REPOSITORIES=Verjson/.github SECRET_REPOSITORIES=Verjson/.github \
  CANONICAL_CALLER=unreadable run_audit \
  && fail "unreadable canonical direct workflow reported green" \
  || {
    grep -q 'Unreadable canonical privileged merge workflow' "$tmp/out" \
      && pass "canonical direct workflow API failures fail closed" \
      || fail "unreadable canonical direct workflow was misclassified"
  }

ACTIVE_REPOSITORIES=Verjson/.github SECRET_REPOSITORIES=Verjson/.github \
  CANONICAL_CONTENT='not-base64!' run_audit \
  && fail "undecodable canonical direct workflow reported green" \
  || {
    grep -q 'Unreadable canonical privileged merge workflow' "$tmp/out" \
      && pass "undecodable canonical direct workflow fails closed" \
      || fail "undecodable canonical direct workflow was misclassified"
  }

ACTIVE_REPOSITORIES=Verjson/.github SECRET_REPOSITORIES=Verjson/.github \
  CANONICAL_CONTENT="$(printf '%s\n' 'name: corrupt direct workflow' | base64 | tr -d '\n')" run_audit \
  && fail "mismatched canonical direct workflow reported green" \
  || {
    grep -q 'Mismatched canonical privileged merge workflow' "$tmp/out" \
      && pass "canonical direct workflow must exactly match the checked-out audit revision" \
      || fail "canonical direct workflow mismatch lacks actionable evidence"
  }

ACTIVE_REPOSITORIES=Verjson/.github SECRET_REPOSITORIES=Verjson/alpha run_audit \
  && fail "canonical direct consumer without selected-secret access reported green" \
  || {
    grep -q 'Missing privileged merge App key access' "$tmp/out" \
      && grep -q 'repository=Verjson/.github' "$tmp/out" \
      && pass "canonical direct consumer requires selected-secret access evidence" \
      || fail "canonical direct consumer bypassed secret-scope validation"
  }

ACTIVE_REPOSITORIES=$'Verjson/alpha\nVerjson/beta' \
  SECRET_REPOSITORIES=$'Verjson/alpha\nVerjson/beta' \
  BETA_CALLER=missing run_audit \
  && grep -q 'result=conformant repositories_scanned=2 consumers=1' "$tmp/out" \
  && pass "repositories without a privileged caller are not invented as consumers" \
  || fail "non-consumer repository was treated as a missing caller"

ACTIVE_REPOSITORIES=$'Verjson/alpha\nVerjson/beta' \
  SECRET_REPOSITORIES=$'Verjson/alpha\nVerjson/beta' \
  BETA_CALLER=unreadable run_audit \
  && fail "unreadable caller state reported green" \
  || {
    grep -q 'Unreadable privileged merge caller' "$tmp/out" \
      && ! grep -q 'gen-privileged-merge-caller.sh' "$tmp/out" \
      && pass "caller API failure is distinct from confirmed absence" \
      || fail "caller API failure was misreported as missing configuration"
  }

ACTIVE_REPOSITORIES=$'Verjson/alpha\nVerjson/beta' \
  SECRET_REPOSITORIES=$'Verjson/alpha\nVerjson/beta' \
  BETA_CONTENT="$(bash "$generator" "$contract_sha" "$required_checks" | sed 's/name: AI privileged merge/name: drifted privileged merge/' | base64 | tr -d '\n')" run_audit \
  && fail "non-canonical generated caller reported green" \
  || {
    grep -q 'Non-canonical privileged merge caller' "$tmp/out" \
      && grep -q 'repository=Verjson/beta' "$tmp/out" \
      && pass "caller content drift fails with repository-scoped regeneration evidence" \
      || fail "caller content drift lacks actionable repository-scoped evidence"
  }

ALPHA_CONTENT='not-base64!' run_audit \
  && fail "undecodable caller content reported green" \
  || {
    grep -q 'Unreadable privileged merge caller' "$tmp/out" \
      && grep -q 'invalid base64 content' "$tmp/out" \
      && pass "undecodable caller content fails closed as unreadable" \
      || fail "undecodable caller content was misclassified: $(<"$tmp/out")"
  }

ACTIVE_REPOSITORIES=$'Verjson/alpha\nVerjson/beta' \
  SECRET_REPOSITORIES=Verjson/alpha run_audit \
  && fail "missing selected-secret access reported green" \
  || {
    grep -q 'repository=Verjson/beta' "$tmp/out" \
      && grep -q 'MERGE_APP_PRIVATE_KEY' "$tmp/out" \
      && pass "missing secret access fails with repository-scoped evidence" \
      || fail "missing secret access lacks actionable repository-scoped evidence"
  }

ACTIVE_REPOSITORIES=$'Verjson/alpha\nVerjson/beta' \
  SECRET_VISIBILITY=all SECRET_REPOSITORIES='' run_audit \
  && grep -q 'result=conformant repositories_scanned=2 consumers=2' "$tmp/out" \
  && pass "organization-wide secret visibility admits every active repository" \
  || fail "all-repository secret visibility was not honored"

ALPHA_CONTENT="$(bash "$generator" "$alternate_contract_sha" "$required_checks" | base64 | tr -d '\n')" \
  ALPHA_RETRY_CONTENT="$(bash "$generator" "$alternate_contract_sha" --retry "$retry_workflow_names" "$required_checks" | base64 | tr -d '\n')" run_audit \
  && grep -q 'result=conformant repositories_scanned=1 consumers=1' "$tmp/out" \
  && pass "caller bytes are canonical for a verified stable immutable pin, not the audit event SHA" \
  || fail "audit incorrectly rebound canonical caller bytes to an unrelated event SHA"

ALPHA_CONTENT="$(bash "$generator" "$absent_contract_sha" "$required_checks" | base64 | tr -d '\n')" run_audit \
  && fail "absent 40-hex canonical pin reported green" \
  || {
    grep -q 'Untrusted privileged merge caller pin' "$tmp/out" \
      && pass "caller pin must exist on canonical main history" \
      || fail "absent caller pin lacks canonical-history evidence"
  }

HISTORICAL_WORKFLOW_CONTENT="$(printf '%s\n' 'name: incompatible' | base64 | tr -d '\n')" run_audit \
  && fail "incompatible historical reusable interface reported green" \
  || {
    grep -q 'Incompatible privileged merge contract' "$tmp/out" \
      && pass "caller pin must expose the historical reusable interface" \
      || fail "incompatible caller pin lacks interface evidence"
  }

# A mutable ref is the most likely wrong pin, so it must draw the diagnostic that names it
# rather than the one for a missing or duplicated `uses:` line. Both refuse; only the
# explanation differs, and the wrong one sends an operator hunting a line that is present.
# The fixture is ordered: Verjson/alpha populates every loop-scoped variable with a
# conforming value, Verjson/beta then trips the guard, and Verjson/.github follows. Each
# must be judged on its own evidence.
mutable_pin_caller="$(bash "$generator" "$contract_sha" "$required_checks" | sed "s/@$contract_sha/@main/" | base64 | tr -d '\n')"
ACTIVE_REPOSITORIES=$'Verjson/alpha\nVerjson/beta\nVerjson/.github' \
  SECRET_REPOSITORIES=$'Verjson/alpha\nVerjson/.github' \
  BETA_CONTENT="$mutable_pin_caller" run_audit \
  && fail "mutable caller pin reported green" \
  || {
    grep -q "pin is not a 40-hex commit SHA" "$tmp/out" \
      && ! grep -q 'expected exactly one immutable canonical workflow pin' "$tmp/out" \
      && grep -q 'Missing privileged merge App key access::repository=Verjson/beta' "$tmp/out" \
      && ! grep -q 'repository=Verjson/\.github' "$tmp/out" \
      && grep -q 'result=nonconformant repositories_scanned=3 consumers=3' "$tmp/out" \
      && pass "a mutable pin draws the non-SHA diagnostic and is still judged on its own secret-access evidence" \
      || fail "mutable caller pin lacks the non-SHA diagnostic or abandoned its evidence: $(<"$tmp/out")"
  }

# The pin-count arm keeps its own meaning, which is what makes the split worth having: a
# caller carrying no canonical `uses:` line, and one carrying two, are structurally
# different from a present-but-mutable pin and must not borrow its message.
ALPHA_CONTENT="$(bash "$generator" "$contract_sha" "$required_checks" \
  | sed "s#uses: Verjson/\.github/\.github/workflows/ai-privileged-merge\.yml@#uses: Verjson/other/.github/workflows/ai-privileged-merge.yml@#" \
  | base64 | tr -d '\n')" run_audit \
  && fail "caller with no canonical pin reported green" \
  || {
    grep -q 'expected exactly one immutable canonical workflow pin' "$tmp/out" \
      && ! grep -q 'pin is not a 40-hex commit SHA' "$tmp/out" \
      && pass "a caller carrying zero canonical pins draws the pin-count diagnostic" \
      || fail "zero canonical pins lacks the pin-count diagnostic: $(<"$tmp/out")"
  }

ALPHA_CONTENT="$(bash "$generator" "$contract_sha" "$required_checks" \
  | sed "s#^\( *\)\(uses: Verjson/\.github/\.github/workflows/ai-privileged-merge\.yml@.*\)\$#\1\2\n\1\2#" \
  | base64 | tr -d '\n')" run_audit \
  && fail "caller with two canonical pins reported green" \
  || {
    grep -q 'expected exactly one immutable canonical workflow pin' "$tmp/out" \
      && ! grep -q 'pin is not a 40-hex commit SHA' "$tmp/out" \
      && pass "a caller carrying two canonical pins draws the pin-count diagnostic" \
      || fail "two canonical pins lacks the pin-count diagnostic: $(<"$tmp/out")"
  }

# A conformant pin PLUS a second mutable `uses:` line is the one shape where the two
# extractors differ in pin *count*, not only in diagnostic: the narrow capture saw one pin,
# reached the compare API, and accepted. The widened capture sees two and refuses. This
# closes a fail-open, so it is pinned rather than left to the count arm's general case.
ALPHA_CONTENT="$(bash "$generator" "$contract_sha" "$required_checks" \
  | sed "s#^\( *\)\(uses: Verjson/\.github/\.github/workflows/ai-privileged-merge\.yml@\).*\$#\1\2$contract_sha\n\1\2main#" \
  | base64 | tr -d '\n')" run_audit \
  && fail "a conformant pin alongside a mutable pin reported green" \
  || {
    grep -q 'expected exactly one immutable canonical workflow pin' "$tmp/out" \
      && pass "a conformant pin alongside a second mutable pin is refused, not accepted on the first" \
      || fail "a smuggled second mutable pin was accepted: $(<"$tmp/out")"
  }

# Renovate and Dependabot write the pin with a trailing version comment. That is a
# correctly-pinned caller, and refusing it with the pin-count message is exactly the
# wrong-diagnosis class this change exists to remove. Accepting it is safe because the
# 40-hex guard still judges the captured ref. Proof that the pin was accepted: the run
# reaches the canonical-content comparison, which only pin-bearing callers reach.
ALPHA_CONTENT="$(bash "$generator" "$contract_sha" "$required_checks" \
  | sed "s|\(ai-privileged-merge\.yml@$contract_sha\)\$|\1 # v1.2.3|" \
  | base64 | tr -d '\n')" run_audit \
  && fail "a pin with a trailing comment unexpectedly reported fully green" \
  || {
    ! grep -q 'Invalid privileged merge caller pin' "$tmp/out" \
      && grep -q 'Non-canonical privileged merge caller' "$tmp/out" \
      && pass "a pin carrying a trailing version comment is recognized as a pin" \
      || fail "a trailing-comment pin was not recognized: $(<"$tmp/out")"
  }

# The other side of that boundary: YAML needs whitespace before `#` to start a comment, so
# `@<sha>#v1` is one ref, not a pin plus a comment, and must stay refused as a non-SHA.
ALPHA_CONTENT="$(bash "$generator" "$contract_sha" "$required_checks" \
  | sed "s|\(ai-privileged-merge\.yml@$contract_sha\)\$|\1#v1|" \
  | base64 | tr -d '\n')" run_audit \
  && fail "a pin with an unseparated hash suffix reported green" \
  || {
    grep -q 'pin is not a 40-hex commit SHA' "$tmp/out" \
      && pass "a hash suffix with no separating space is not treated as a comment" \
      || fail "an unseparated hash suffix was mistaken for a trailing comment: $(<"$tmp/out")"
  }

# The retry pin extractor carried the identical narrow capture, so the fail-open just
# closed at the caller site was still open 113 lines below it: a conformant retry pin plus
# a mutable one yielded a single pin equal to the caller SHA and was accepted.
ALPHA_RETRY_CONTENT="$(bash "$generator" "$contract_sha" --retry "$retry_workflow_names" "$required_checks" \
  | sed "s#^\( *\)\(uses: Verjson/\.github/\.github/workflows/ai-promotion-retry\.yml@\).*\$#\1\2$contract_sha\n\1\2main#" \
  | base64 | tr -d '\n')" run_audit \
  && fail "a conformant retry pin alongside a mutable retry pin reported green" \
  || {
    grep -q 'retry must pin the same immutable contract SHA as privileged merge' "$tmp/out" \
      && pass "a second mutable promotion retry pin is refused, not accepted on the first" \
      || fail "a smuggled second mutable retry pin was accepted: $(<"$tmp/out")"
  }

# `base64 --decode` succeeds on an empty stream, so a fetch that returned nothing decodes
# to an empty artifact that is then read as content. Without a guard the audit does not go
# quiet -- it makes a confident claim about the adopter it never established. This script
# feeds merge-authorization conformance, so an empty decode reads as a conformance
# determination that was never made. The verdict must name a failed read, and must not be
# reachable from the vocabulary used for an adopter that genuinely lacks the wiring.
ALPHA_CONTENT="" run_audit \
  && fail "an empty fetched caller artifact reported green" \
  || {
    grep -q "Empty privileged merge caller::repository=Verjson/alpha" "$tmp/out" \
      && grep -q "decoded to no content" "$tmp/out" \
      && ! grep -q 'expected exactly one immutable canonical workflow pin' "$tmp/out" \
      && pass "an empty fetched caller artifact faults as a failed read, not as a pin problem" \
      || fail "empty caller artifact drew a verdict it never established: $(<"$tmp/out")"
  }

ALPHA_RETRY_CONTENT="" run_audit \
  && fail "an empty fetched promotion retry artifact reported green" \
  || {
    grep -q "Empty promotion retry::repository=Verjson/alpha" "$tmp/out" \
      && grep -q "decoded to no content" "$tmp/out" \
      && pass "an empty fetched promotion retry artifact faults as a failed read" \
      || fail "empty retry artifact drew a verdict it never established: $(<"$tmp/out")"
  }

# A guard that tests the byte count catches only a zero-byte payload. A one-byte newline
# (base64 `Cg==`) is a non-empty file whose content is still nothing: `"$(<file)"` strips
# it to the empty string, and every wrong verdict above returns. The guard must test the
# content it is about to read, not the size of the file it landed in.
blank_artifact="$(printf '\n' | base64 | tr -d '\n')"
ALPHA_CONTENT="$blank_artifact" run_audit \
  && fail "a whitespace-only fetched caller artifact reported green" \
  || {
    grep -q "Empty privileged merge caller::repository=Verjson/alpha" "$tmp/out" \
      && ! grep -q 'expected exactly one immutable canonical workflow pin' "$tmp/out" \
      && pass "a whitespace-only caller artifact faults as a failed read, not as a pin problem" \
      || fail "a whitespace-only caller artifact slipped past the emptiness guard: $(<"$tmp/out")"
  }

ALPHA_RETRY_CONTENT="$blank_artifact" run_audit \
  && fail "a whitespace-only fetched promotion retry artifact reported green" \
  || {
    grep -q "Empty promotion retry::repository=Verjson/alpha" "$tmp/out" \
      && pass "a whitespace-only promotion retry artifact faults as a failed read" \
      || fail "a whitespace-only retry artifact slipped past the emptiness guard: $(<"$tmp/out")"
  }

# The canonical repository reads the same two artifacts through the same branch, where an
# empty decode would otherwise be explained as remote bytes differing from the audited
# revision -- a byte-comparison verdict drawn from bytes that were never fetched.
ACTIVE_REPOSITORIES=$'Verjson/.github' SECRET_REPOSITORIES=$'Verjson/.github' \
  CANONICAL_CONTENT="" run_audit \
  && fail "an empty fetched canonical workflow reported green" \
  || {
    grep -q 'Empty canonical privileged merge workflow::repository=Verjson/\.github' "$tmp/out" \
      && grep -q "decoded to no content" "$tmp/out" \
      && ! grep -q 'remote bytes differ from the checked-out audit revision' "$tmp/out" \
      && pass "an empty fetched canonical workflow faults as a failed read, not as a byte mismatch" \
      || fail "empty canonical workflow drew a byte-comparison verdict: $(<"$tmp/out")"
  }

# The distinction the verdict exists to carry: an adopter that genuinely lacks the file
# still reports absence, and must not be relabelled a failed read by this guard.
ALPHA_CALLER=missing run_audit \
  && fail "a missing caller reported green" \
  || {
    ! grep -q 'Empty privileged merge caller' "$tmp/out" \
      && pass "a genuinely absent caller is not reported as an empty fetch" \
      || fail "absence was relabelled as a failed read: $(<"$tmp/out")"
  }

# The remaining early exits leak loop-scoped state without a reachable read-before-assignment
# today, so no fixture can observe them. Pin the two structural invariants that keep them
# unobservable: the per-repository reset runs before any branch can leave the iteration, and
# it names every variable the iteration assigns. The second is the one #1448 was made of --
# a variable added to the loop and forgotten from the reset list -- and a position-only
# check cannot see it.
collector="$tmp/reset-contract.py"
cat >"$collector" <<'RESET_CONTRACT'
import re
import subprocess
import sys

source = open(sys.argv[1], encoding="utf-8").read().splitlines()
opener = 'while IFS= read -r repository; do'
closer = 'done <<<"$repositories"'
assert source.count(opener) == 1, "the fleet loop header is no longer unique"
assert source.count(closer) == 1, "the fleet loop footer is no longer unique"
body = source[source.index(opener) + 1:source.index(closer)]

# Join backslash continuations before anything else looks at the text. The reset statement
# is continuation-joined and the loop already wraps long commands, so a collector that
# reads raw lines would drop every name written past a wrap -- silently, which is the
# failure mode this whole contract exists to prevent.
joined = []
pending = ""
for line in body:
    stripped = line.rstrip()
    if stripped.endswith("\\"):
        pending += stripped[:-1]
        continue
    joined.append(pending + line)
    pending = ""
assert not pending, "the loop body ends inside a line continuation"

# A word is an assignment when it leads with `NAME=`, `NAME[sub]=`, or `NAME+=`. The
# excision scanner needs this too: a leading assignment is a command *prefix*, so it
# must not be mistaken for the command word.
ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[[^]]*\])?\+?=")

# An embedded awk/sed/jq program is a foreign language that happens to spell assignment the
# same way bash does, so it is excised before the assignment walk ever sees it. Openers are
# decided in *word* position by the same character scanner that tracks quoting, not by a
# regex over the raw line: a raw-line regex cannot tell `jq -e '` from the word `jq` inside
# `echo "needs jq here"`, and the latter opened a region that ran to the next apostrophe
# anywhere later in the body and silently discarded every assignment in between.
#
# The region runs from its opening quote to the matching quote *of the same character*.
# The audited script's dominant embedded idiom is the double-quoted
# `sed -nE "s/…'(…)'…/p"`, and cutting that at the single quote inside the program leaves
# the remainder unbalanced -- the same silent-swallow failure, one quoting style over.
#
# `gh --jq '…'` is an embedded program too even though `gh` is the command word, so the
# option arms the next quoted word as well. That direction *forged*: a multi-line `--jq`
# program was handed to the bash tokenizer, and a name spelled in its body was collected as
# loop state, which is exactly what keeps a dead reset entry green.
EMBEDDED_COMMANDS = ("awk", "sed", "jq")
EMBEDDED_OPTIONS = ("--jq",)
PLACEHOLDER = "EMBEDDED_PROGRAM"


class Excision:
    """Scan the loop body once, replacing each embedded program with a single word.

    The scanner keeps a context stack rather than a flat quote flag, because the regions it
    must find sit inside `"$( … )"`: single quotes are literal inside `"…"` but active
    again inside the `$( … )` nested in it, and only a stack can tell those apart.
    """

    SEPARATORS = ";|&\n"

    def __init__(self, text):
        self.text = text
        self.out = []
        # Each command context records the word being built, the command word it has seen,
        # the previous word, and whether an embedded program may still follow.
        self.stack = [self.new_command_frame()]

    @staticmethod
    def new_command_frame():
        return {"kind": "cmd", "word": "", "command": None, "previous": None, "armed": False}

    @property
    def frame(self):
        return self.stack[-1]

    def emit(self, chunk):
        self.out.append(chunk)

    def end_word(self):
        frame = self.frame
        if frame["kind"] != "cmd" or not frame["word"]:
            return
        word = frame["word"]
        frame["word"] = ""
        if frame["command"] is None and not ASSIGN.match(word):
            frame["command"] = word
            frame["armed"] = word in EMBEDDED_COMMANDS
        frame["previous"] = word

    def end_command(self):
        self.end_word()
        frame = self.frame
        if frame["kind"] == "cmd":
            frame.update(command=None, previous=None, armed=False)

    def opens_embedded(self):
        """True when the quote about to open begins an embedded program's text."""
        frame = self.frame
        if frame["kind"] != "cmd" or frame["word"]:
            return False
        if frame["previous"] in EMBEDDED_OPTIONS:
            return True
        return bool(frame["armed"] and frame["command"] is not None)

    def skip_region(self, index):
        """Consume a quoted embedded program whole, newlines included, and return the index
        just past its closing quote. The region must close on its own quote character; a
        region that runs off the end of the loop body is reported rather than absorbed."""
        quote = self.text[index]
        cursor = index + 1
        while cursor < len(self.text):
            char = self.text[cursor]
            # A backslash escapes the next character inside a double-quoted region, so
            # `awk "a\"b"` does not close at the escaped quote. Single quotes have no
            # escape at all, so there the first apostrophe really is the close.
            if quote == '"' and char == "\\":
                cursor += 2
                continue
            if char == quote:
                return cursor + 1
            cursor += 1
        raise AssertionError(
            f"an embedded program region opened with {quote} and never closed"
        )

    def run(self):
        text, index = self.text, 0
        while index < len(text):
            char = text[index]
            pair = text[index:index + 2]
            frame = self.frame
            kind = frame["kind"]
            if kind == "single":
                # A newline inside a quoted string is data, not a command separator.
                # Collapsing it keeps the emitted line self-contained, so the per-line
                # tokenizer below cannot read the inner lines of a multi-line quoted string
                # as bash and forge a name out of them. A newline inside `$( … )` is a real
                # separator and is preserved.
                self.emit(" " if char == "\n" else char)
                index += 1
                if char == "'":
                    self.stack.pop()
                continue
            if kind == "double":
                if char == "\\" and index + 1 < len(text):
                    self.emit(text[index:index + 2])
                    index += 2
                    continue
                if char == "\n":
                    self.emit(" ")
                    index += 1
                    continue
                if pair in ("$(", "${"):
                    self.emit(pair)
                    self.stack.append(
                        self.new_command_frame() if pair == "$(" else {"kind": "param", "word": ""}
                    )
                    index += 2
                    continue
                self.emit(char)
                index += 1
                if char == '"':
                    self.stack.pop()
                continue
            if kind == "param":
                self.emit(char)
                index += 1
                if char == "}":
                    self.stack.pop()
                continue
            if kind == "comment":
                self.emit(char)
                index += 1
                if char == "\n":
                    self.stack.pop()
                    self.end_command()
                continue
            # A command context: this is the only place a word, and therefore a command
            # word, exists at all.
            if char == "#" and not frame["word"]:
                # A `#` starting a word is a comment. It is emitted verbatim so the
                # comment filter below still sees it, but its text must not be scanned:
                # an apostrophe in prose would otherwise open a quote.
                self.end_word()
                self.stack.append({"kind": "comment", "word": ""})
                self.emit(char)
                index += 1
                continue
            if char in "'\"":
                if self.opens_embedded():
                    self.emit(PLACEHOLDER)
                    index = self.skip_region(index)
                    frame["previous"] = PLACEHOLDER
                    continue
                frame["word"] += char
                self.emit(char)
                self.stack.append({"kind": "single" if char == "'" else "double", "word": ""})
                index += 1
                continue
            if pair in ("$(", "<(", ">("):
                self.emit(pair)
                self.stack.append(self.new_command_frame())
                index += 2
                continue
            if pair == "${":
                frame["word"] += pair
                self.emit(pair)
                self.stack.append({"kind": "param", "word": ""})
                index += 2
                continue
            if char == ")" and len(self.stack) > 1:
                self.end_command()
                self.stack.pop()
                self.emit(char)
                index += 1
                continue
            if char in self.SEPARATORS:
                self.end_command()
                self.emit(char)
                index += 1
                continue
            if char.isspace():
                self.end_word()
                self.emit(char)
                index += 1
                continue
            frame["word"] += char
            self.emit(char)
            index += 1
        if self.frame["kind"] == "comment":
            # A comment on the body's last line is terminated by the end of the body rather
            # than by a newline. Without this an ordinary trailing comment on the loop's
            # final line is reported as an unterminated region.
            self.stack.pop()
        self.end_command()
        assert len(self.stack) == 1 and self.stack[0]["kind"] == "cmd", (
            "the loop body ends inside an unterminated quote, command substitution, or "
            "embedded program region"
        )
        return "".join(self.out).split("\n")


code = Excision("\n".join(joined)).run()
# Prove the excision preserved the body's bash rather than assuming it: a region cut at the
# wrong quote, or one that swallowed a real command, leaves text that no longer parses. This
# is what makes the closure guard above worth its credit -- deleting an embedded program's
# closing quote in the audited script is caught here, where a "the region eventually closed"
# check absorbs it into the next apostrophe and reports nothing.
parse = subprocess.run(
    ["bash", "-n"],
    input="while :; do\n" + "\n".join(code) + "\ndone\n",
    capture_output=True,
    text=True,
)
# The membership findings are reported before this parse failure, because a real drift is
# the finding this contract exists for and a parse error would otherwise mask it behind a
# diagnosis about quoting. A parse failure does mean the excision cut wrong, so a membership
# finding raised alongside one may be an artifact; both are reported together rather than
# either hiding the other.
parse_note = "" if parse.returncode == 0 else (
    f" (the excised loop body also no longer parses as bash: {parse.stderr.strip()}"
    " -- so this finding may be an artifact of a mis-cut excision)"
)

# Whole-line comments are prose: neither a `continue` nor an `unset` written in one is
# control flow. Trailing comments are dropped by the tokenizer below.
uncommented = [line for line in code if not line.lstrip().startswith("#")]

# Anchor the reset on being the loop's only top-level `unset` rather than on being the first
# line that happens to start with one, so an unrelated `unset` cannot satisfy this vacuously.
reset_starts = [i for i, line in enumerate(uncommented) if re.match(r"^  unset\s", line)]
assert len(reset_starts) == 1, f"expected exactly one per-repository reset, found {len(reset_starts)}"
reset_at = reset_starts[0]
reset = set(uncommented[reset_at].replace("unset", "", 1).split())
assert reset, "the reset statement named nothing"

exits = [i for i, line in enumerate(uncommented) if re.search(r"\b(?:continue|break)\b", line)]
# The only legitimate pre-reset exit skips a blank inventory line before any state is set.
assert exits and uncommented[exits[0]].strip() == '[ -n "$repository" ] || continue', uncommented[exits[0]]
early = [uncommented[i].strip() for i in exits[1:] if i < reset_at]
assert not early, f"early exits precede the reset: {early}"

# An assignment can sit behind a `case` arm label, a control keyword, a `!`, a pipe, a brace
# group, or a short-circuit after a test -- `has_secret` alone is written three of those
# ways. Strip lead-ins from each segment rather than demanding the name lead the line.
LEADIN = re.compile(r"""^(?:
      (?:if|elif|while|until|then|else|do|done|fi|case|in|esac|time|!
        |command|builtin|env|exec|nohup|stdbuf)$
    | [{}]
    | \[\[?
    | \]\]?
    | \(?(?:[^()\s|\\]|\\.)*\)$  # a case arm label, `(x)` and `a=1)` too: an arm label is
                                #  a pattern, never an assignment, and excluding `=` here
                                #  let `case $x in a=1)` forge the name `a`. This is a
                                #  prefix match, so the whole word must be the label:
                                #  unanchored, `x="b)"`, `x='b)'`, `x=${r%)}` and `x=b\)`
                                #  were all discarded as arm labels and silently missed.
                                #  Escapes are consumed in *pairs*, so whether the final
                                #  `)` is escaped is decided by parity, not by one lookback:
                                #  `a\))`, `\))` and `a\\)` are all real arm labels under
                                #  bash and all strip, while `x=b\)` -- whose `)` really is
                                #  escaped -- is kept, which is what still separates it
                                #  from the label `a=1)`.
  )""", re.X)

# Options that consume the following word, per command. Getting this right is what keeps
# `read -d '' name` and `mapfile -d '' -t name` from losing their variable to an argument.
ARG_TAKING = {
    "mapfile": set("dnOsCcu"),
    "readarray": set("dnOsCcu"),
    "read": set("adinNptu"),
}
# For `read`, the argument of -a is itself a variable being assigned.
NAMES_ITS_ARG = {"read": set("a")}
DECLARATORS = ("declare", "typeset", "export", "readonly", "local")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LET_TARGET = re.compile(r"^[\"']?([A-Za-z_][A-Za-z0-9_]*)\s*(?:\+\+|--|[-+*/%&|^]?=)")
# `${name:=default}` and `${name=default}` assign wherever bash *expands* them -- unquoted
# and inside double quotes alike -- and nowhere else. Matched over the raw line this forged
# names bash never assigns: `echo '${ghost:=1}'` and a trailing `# ${ghost:=1}` were both
# collected, and a forged name is the silent direction, because it keeps a dead reset entry
# green. It is applied per word by `expansion_names` below instead, over the words
# `segments()` yields, which has already dropped a trailing comment and tracked the quoting.
ASSIGN_EXPANSION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?=")
# Arithmetic assigns through `=`, every compound operator, and pre/post increment.
ARITH_TARGET = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\+\+|--|(?:\*\*|[-+*/%&|^]|<<|>>)?=(?!=))"
)
ARITH_PREFIX = re.compile(r"(?:\+\+|--)\s*([A-Za-z_][A-Za-z0-9_]*)")


def unquote(word):
    """Strip shell quoting from a candidate name. `read -r "brandnew"` names a variable
    exactly as `read -r brandnew` does; leaving the quotes on drops it silently."""
    return re.sub(r"\$?[\"']", "", word)


def segments(line):
    """Split a line into command segments and each segment into words, honoring quotes,
    `$(…)`, `<(…)`, `${…}`, and `((…))` so a separator inside a string or an arithmetic
    header cannot split a token, and dropping an unquoted trailing comment."""
    words, current, out = [], "", []
    quote, depth, index = None, 0, 0

    def flush_word():
        nonlocal current
        if current:
            words.append(current)
            current = ""

    def flush_segment():
        nonlocal words
        flush_word()
        if words:
            out.append(words)
        words = []

    while index < len(line):
        char = line[index]
        pair = line[index:index + 2]
        if quote:
            current += char
            if char == quote and not (quote == '"' and line[index - 1] == "\\"):
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            current += char
            index += 1
            continue
        if pair in ("$(", "${", "<(", ">("):
            depth += 1
            current += pair
            index += 2
            continue
        if char == "(" and (depth or not current):
            depth += 1
            current += char
            index += 1
            continue
        if depth and char in ")}":
            depth -= 1
            current += char
            index += 1
            continue
        if depth:
            current += char
            index += 1
            continue
        if char == "#" and not current:
            break
        if pair in ("&&", "||"):
            flush_segment()
            index += 2
            continue
        if char in ";|&":
            flush_segment()
            index += 1
            continue
        if char.isspace():
            flush_word()
            index += 1
            continue
        current += char
        index += 1
    flush_segment()
    return out


def arithmetic_names(word):
    """Names assigned inside `((…))`, including a C-style `for` header's three clauses."""
    if not word.startswith("(("):
        return set()
    inner = word[2:]
    if inner.endswith("))"):
        inner = inner[:-2]
    found = set()
    for clause in re.split(r"[,;]", inner):
        found |= set(ARITH_TARGET.findall(clause)) | set(ARITH_PREFIX.findall(clause))
    return found


def expansion_names(word):
    """Names assigned by a `${name:=…}` or `${name=…}` expansion inside one word.

    A single-quoted span is literal text, so an expansion written in one assigns nothing;
    a double-quoted span still expands, as does unquoted text."""
    found, index, quote = set(), 0, None
    while index < len(word):
        char = word[index]
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        # Bash honors a backslash escape outside quotes exactly as it does inside double
        # quotes: `echo \${x:=1}` expands nothing and assigns nothing. Only a
        # single-quoted span has no escape at all.
        if quote != "'" and char == "\\":
            index += 2
            continue
        if quote is None and char == "'":
            quote = "'"
            index += 1
            continue
        if char == '"':
            quote = None if quote == '"' else '"'
            index += 1
            continue
        match = ASSIGN_EXPANSION.match(word, index)
        if match:
            found.add(match.group(1))
            index = match.end()
            continue
        index += 1
    return found


def names_from_command(words):
    """Variables named by a command rather than by a bare `name=` assignment."""
    command, rest = words[0], words[1:]
    found = set()
    if command == "printf":
        for index, word in enumerate(rest):
            if word == "-v" and index + 1 < len(rest):
                candidate = unquote(rest[index + 1]).split("[", 1)[0]
                if NAME.match(candidate):
                    found.add(candidate)
        return found
    # `for x in …`, `select x in …`, and the `in`-less `for x` that iterates the positional
    # parameters all assign x.
    if command in ("for", "select") and rest and (len(rest) == 1 or rest[1] == "in"):
        candidate = unquote(rest[0])
        return {candidate} if NAME.match(candidate) else set()
    if command == "coproc" and rest:
        candidate = unquote(rest[0])
        return {candidate} if NAME.match(candidate) else set()
    if command == "getopts" and len(rest) >= 2:
        candidate = unquote(rest[1])
        return {candidate} if NAME.match(candidate) else set()
    if command == "let":
        for word in rest:
            match = LET_TARGET.match(unquote(word))
            if match:
                found.add(match.group(1))
        return found
    if command in DECLARATORS:
        for word in rest:
            if word.startswith("-"):
                continue
            candidate = unquote(word).split("=", 1)[0].split("[", 1)[0]
            if NAME.match(candidate):
                found.add(candidate)
        return found
    if command not in ARG_TAKING:
        return found
    takes, names_arg = ARG_TAKING[command], NAMES_ITS_ARG.get(command, set())
    index = 0
    while index < len(rest):
        word = rest[index]
        if word.startswith("-") and len(word) > 1:
            cluster = set(word[1:])
            if cluster & takes and index + 1 < len(rest):
                if cluster & names_arg:
                    candidate = unquote(rest[index + 1]).split("[", 1)[0]
                    if NAME.match(candidate):
                        found.add(candidate)
                index += 2
                continue
            index += 1
            continue
        if word.startswith("<") or word.startswith(">"):
            break
        candidate = unquote(word).split("[", 1)[0]
        if NAME.match(candidate):
            found.add(candidate)
        index += 1
    # `read` with no name of its own assigns REPLY, which is loop state like any other.
    if command == "read" and not found:
        found.add("REPLY")
    return found


assigned = set()
ifs_sites = []
for line in uncommented:
    for words in segments(line):
        for word in words:
            assigned |= arithmetic_names(word)
            assigned |= expansion_names(word)
        # `case "$x" in` is scaffolding before the first arm label; on a single-line `case`
        # the arm and its assignment follow on the same segment, and a `case` nested inside
        # such an arm puts a second header in front of the assignment. Strip headers and
        # lead-ins in one loop so nesting cannot leave the assignment behind scaffolding.
        while words:
            if words[0] == "case":
                while words and words[0] != "in":
                    words = words[1:]
                continue
            if LEADIN.match(words[0]):
                words = words[1:]
                continue
            break
        # Leading `NAME=value` words are assignments; more than one may stack, and anything
        # after them is the command they prefix.
        while words:
            match = ASSIGN.match(words[0])
            if not match:
                break
            assigned.add(match.group(1))
            if match.group(1) == "IFS":
                ifs_sites.append((line, len(words) > 1))
            words = words[1:]
        if words:
            assigned |= names_from_command(words)

# Confirmed by reading the script, not inherited from review: these are the only loop-body
# assignments that must survive an iteration. The three counters are fleet totals reported
# after the loop closes.
carried = {"repositories_scanned", "consumers", "failures", "IFS"}
assert carried <= assigned, f"the carry-over list names something the loop never assigns: {sorted(carried - assigned)}"
assert not (reset & carried), f"the reset clears a value the fleet audit must carry: {reset & carried}"
# IFS rides the exemption only as a command prefix, which bash scopes to the single command
# it precedes. A standalone `IFS=,` would be ordinary loop state and must not inherit it.
assert ifs_sites, "IFS is exempted but never assigned; drop the exemption"
for site, is_prefix in ifs_sites:
    assert is_prefix, f"IFS is assigned as loop state, not a command prefix: {site.strip()}"
# `visibility` and `selected_repositories` are assigned before the loop and never inside it,
# so they need no exemption; if that changes, this reddens rather than silently widening.
assert not ({"visibility", "selected_repositories"} & assigned), "fleet-wide state moved into the loop body"

forgotten = assigned - reset - carried
assert not forgotten, f"assigned per repository but never reset: {sorted(forgotten)}{parse_note}"
dead = reset - assigned
assert not dead, f"reset but never assigned in the loop: {sorted(dead)}{parse_note}"

# Last, so a membership finding is never masked by it.
assert parse.returncode == 0, f"the excised loop body no longer parses as bash: {parse.stderr.strip()}"
RESET_CONTRACT

if python3 "$collector" "$audit"; then
  pass "the per-repository reset precedes every early exit and names every variable the iteration assigns"
else
  fail "the per-repository reset is mispositioned or has drifted from the loop's assignments"
fi

# A contract that only ever runs against a conforming script proves nothing about what it
# would catch, and every case below was a silent green at some point in this change's
# history. So each claimed capability is exercised against a mutated copy of the audited
# script rather than described in prose, and each mutation must find its anchor, so the
# corpus cannot rot into vacuous passes when the loop moves underneath it.
mutator="$tmp/reset-contract-mutate.py"
cat >"$mutator" <<'MUTATE'
"""Build a mutated copy of the audited script for one injection case.

Every mutation is required to find its anchor, so a fixture cannot rot into a vacuous pass
when the audited script moves underneath it -- the whole corpus exists because a check that
silently stops checking is the failure mode this contract was written for.
"""
import sys

source, destination = sys.argv[1], sys.argv[2]
operations, current = [], []
for argument in sys.argv[3:]:
    if argument == "::":
        operations.append(current)
        current = []
        continue
    current.append(argument)
operations.append(current)

lines = open(source, encoding="utf-8").read().splitlines()
RESET_HEAD = "  unset metadata default_branch visibility_type has_secret \\"
RESET_TAIL = "    historical_retry_workflow retry_workflow_response"
LOOP_END = 'done <<<"$repositories"'


def require(condition, what):
    if not condition:
        sys.exit(f"mutation fixture is stale, no unique anchor for: {what}")


def anchor_index(anchor):
    if anchor == "before-done":
        require(lines.count(LOOP_END) == 1, LOOP_END)
        return lines.index(LOOP_END)
    require(lines.count(RESET_TAIL) == 1, RESET_TAIL)
    return lines.index(RESET_TAIL) + 1


for operation in operations:
    verb, arguments = operation[0], operation[1:]
    if verb == "insert":
        at = anchor_index(arguments[0])
        lines[at:at] = arguments[1:]
    elif verb == "drop":
        require(lines.count(arguments[0]) == 1, arguments[0])
        del lines[lines.index(arguments[0])]
    elif verb == "sub":
        old, new = arguments
        hits = [index for index, line in enumerate(lines) if old in line]
        require(len(hits) == 1, old)
        lines[hits[0]] = lines[hits[0]].replace(old, new)
    elif verb == "move-reset":
        require(lines.count(RESET_HEAD) == 1 and lines.count(RESET_TAIL) == 1, "the reset block")
        start, end = lines.index(RESET_HEAD), lines.index(RESET_TAIL)
        block = lines[start:end + 1]
        del lines[start:end + 1]
        require(lines.count(LOOP_END) == 1, LOOP_END)
        at = lines.index(LOOP_END)
        lines[at:at] = block
    else:
        sys.exit(f"unknown mutation verb: {verb}")

open(destination, "w", encoding="utf-8").write("\n".join(lines) + "\n")
MUTATE
mutated="$tmp/mutated-audit.sh"
injection_cases=0

# reddens <name> <expected substring> <mutation> [:: <mutation>...]
reddens() {
  local name="$1" expected="$2" output
  shift 2
  injection_cases=$((injection_cases + 1))
  if ! output="$(python3 "$mutator" "$audit" "$mutated" "$@" 2>&1)"; then
    fail "injection fixture '$name' could not be built: $output"
  elif output="$(python3 "$collector" "$mutated" 2>&1)"; then
    fail "injection '$name' left the reset contract green"
  elif grep -qF "$expected" <<<"$output"; then
    pass "injection reddens: $name"
  else
    fail "injection '$name' reddened for the wrong reason: $(tail -1 <<<"$output")"
  fi
}

# absorbed <name> <mutation> [:: <mutation>...] -- text the collector must NOT read as bash
absorbed() {
  local name="$1" output
  shift
  injection_cases=$((injection_cases + 1))
  if ! output="$(python3 "$mutator" "$audit" "$mutated" "$@" 2>&1)"; then
    fail "injection fixture '$name' could not be built: $output"
  elif output="$(python3 "$collector" "$mutated" 2>&1)"; then
    pass "injection is excised, not read as loop state: $name"
  else
    fail "injection '$name' leaked into the assignment walk: $(tail -1 <<<"$output")"
  fi
}

# boundary <name> <mutation> [:: <mutation>...] -- a documented miss, pinned in the
# direction it is silent in, so the fragment's boundary list cannot drift from the code.
boundary() {
  local name="$1" output
  shift
  injection_cases=$((injection_cases + 1))
  if ! output="$(python3 "$mutator" "$audit" "$mutated" "$@" 2>&1)"; then
    fail "injection fixture '$name' could not be built: $output"
  elif output="$(python3 "$collector" "$mutated" 2>&1)"; then
    pass "documented boundary, pinned as a known miss: $name"
  else
    fail "documented boundary '$name' now reddens; correct the recorded boundary: $(tail -1 <<<"$output")"
  fi
}

# Membership drift in both directions -- the bug class #1448 was made of.
reddens "the reset forgets the names it opens with" \
  "assigned per repository but never reset: ['default_branch', 'has_secret', 'metadata', 'visibility_type']" \
  sub '  unset metadata default_branch visibility_type has_secret \' '  unset \'
reddens "a new loop variable is added and forgotten" \
  "assigned per repository but never reset: ['newly_added_state']" \
  insert before-done '  newly_added_state=1'
reddens "the reset names something the loop never assigns" \
  "reset but never assigned in the loop: ['never_assigned_anywhere']" \
  sub '  unset metadata' '  unset never_assigned_anywhere metadata'
reddens "the reset clears a fleet accumulator" \
  "the reset clears a value the fleet audit must carry" \
  sub '  unset metadata' '  unset consumers metadata'
reddens "the reset returns to the bottom of the loop" \
  'early exits precede the reset:' move-reset
reddens "a second top-level unset makes the reset ambiguous" \
  'expected exactly one per-repository reset, found 2' \
  insert before-done '  unset some_other_thing'
reddens "fleet-wide state moves into the loop body" \
  'fleet-wide state moved into the loop body' \
  insert before-done '  visibility=x'
reddens "IFS becomes loop state instead of a command prefix" \
  'IFS is assigned as loop state, not a command prefix' \
  insert before-done '  IFS=,'

# Every assignment form the collector claims to see, one injection per form.
form() {
  local name="$1" variable="$2"
  shift 2
  reddens "$name" "assigned per repository but never reset: ['$variable']" \
    insert before-done "$@"
}

form 'a bare assignment' form_bare '  form_bare=1'
form 'an appending assignment' form_append '  form_append+=1'
form 'an indexed element assignment' form_indexed '  form_indexed[0]=1'
reddens 'a stacked command prefix' \
  "assigned per repository but never reset: ['form_prefix', 'form_second']" \
  insert before-done '  form_prefix=1 form_second=2 true'
form 'a case arm' form_case_arm '  case "$relation" in ahead) form_case_arm=1 ;; esac'
form 'a parenthesized case arm' form_case_paren \
  '  case "$relation" in (ahead) form_case_paren=1 ;; esac'
form 'a nested single-line case' form_case_nested \
  '  case a in a) case b in b) form_case_nested=1 ;; esac ;; esac'
# An assignment word may itself contain a `)`. The arm-label lead-in is a prefix match, so
# every one of these was discarded as an arm label and silently missed; all four are real
# assignments under `declare -p`.
form 'an assignment whose value ends in a double-quoted )' form_dq_paren \
  '  form_dq_paren="b)"'
form 'an assignment whose value ends in a single-quoted )' form_sq_paren \
  "  form_sq_paren='b)'"
form 'an assignment whose value is a ) -trimming expansion' form_param_paren \
  '  form_param_paren=${repository%)}'
form 'an assignment whose value ends in an escaped )' form_escaped_paren \
  '  form_escaped_paren=b\)'
form 'a short-circuit after a test' form_and '  [ -n "$repository" ] && form_and=1'
form 'the failure arm of a test' form_or '  [ -z "$repository" ] || form_or=1'
form 'a negated command' form_negated '  ! form_negated=1'
form 'an assignment after an unquoted pipe' form_pipe '  true | form_pipe=1'
form 'an assignment inside a brace group' form_brace '  { form_brace=1; }'
form 'an assignment behind the command builtin' form_builtin \
  '  command true && form_builtin=1'
form 'declare' form_declare '  declare form_declare=1'
form 'typeset' form_typeset '  typeset form_typeset=1'
form 'export' form_export '  export form_export=1'
form 'readonly' form_readonly '  readonly form_readonly=1'
form 'local' form_local '  local form_local=1'
form 'a quoted declare target' form_declare_quoted '  declare "form_declare_quoted=1"'
form 'a nameref declaration' form_nameref '  declare -n form_nameref=repository'
form 'for … in' form_for '  for form_for in a; do :; done'
form 'for over the positional parameters' form_for_positional \
  '  for form_for_positional; do :; done'
form 'select' form_select '  select form_select in a; do break; done </dev/null'
form 'coproc' form_coproc '  coproc form_coproc { true; }' '  wait'
form 'getopts' form_getopts '  getopts ab form_getopts || :'
form 'let' form_let '  let form_let=1 || :'
form 'arithmetic assignment' form_arith '  (( form_arith = 1 ))'
form 'arithmetic increment' form_increment '  (( form_increment++ )) || :'
form 'an arithmetic exponent assignment' form_power '  (( form_power **= 2 ))'
reddens 'comma-separated arithmetic targets' \
  "assigned per repository but never reset: ['form_comma', 'form_comma_second']" \
  insert before-done '  (( form_comma = 1, form_comma_second = 2 ))'
form 'a C-style for header' form_cstyle \
  '  for (( form_cstyle = 0; form_cstyle < 2; form_cstyle++ )); do :; done'
form 'read -a' form_read_array '  read -a form_read_array <<<"x"'
form "read -d ''" form_read_delim "  read -d '' form_read_delim <<<\"x\" || :"
form 'a quoted read target' form_read_quoted '  read -r "form_read_quoted" <<<"x"'
form 'bare read, which assigns REPLY' REPLY '  read -r <<<"x"'
form 'readarray -t' form_readarray '  readarray -t form_readarray <<<"x"'
form "mapfile -d '' -t" form_mapfile "  mapfile -d '' -t form_mapfile <<<\"x\""
form 'printf -v' form_printf "  printf -v form_printf '%s' x"
form 'printf -v into an array element' form_printf_element \
  "  printf -v 'form_printf_element[0]' '%s' x"
form 'an assigning parameter expansion' form_expansion '  : "${form_expansion:=x}"'
form 'a command substitution bound by if !' form_if_assign \
  '  if ! form_if_assign="$(true)"; then :; fi'
form 'a name written past a backslash continuation' form_continued \
  '  read -r default_branch \' '    form_continued <<<"x"'

# Embedded programs: the two directions that read as conformance while proving nothing.
# Each of these was a reproducible silent green before this change.
reddens 'real bash after a jq word inside a string' \
  "assigned per repository but never reset: ['swallowed_by_word']" \
  insert before-done '  echo "needs jq here" && swallowed_by_word='"'"'1'"'"''
reddens 'real bash after an apostrophe in a jq diagnostic' \
  "assigned per repository but never reset: ['swallowed_by_apostrophe']" \
  insert before-done '  echo "::error::the jq binary isn'"'"'t present"' \
  '  swallowed_by_apostrophe=1' '  echo '"'"'unrelated'"'"''
reddens 'real bash after a double-quoted sed program' \
  "assigned per repository but never reset: ['swallowed_by_double_quote']" \
  insert before-done '  sed -nE "s/a'"'"'b/c/p" /dev/null && swallowed_by_double_quote=1' \
  '  echo '"'"'unrelated'"'"''
reddens 'real bash past an embedded region closing quote' \
  "assigned per repository but never reset: ['past_the_region']" \
  insert before-done "  jq -e '.x' <<<'{}' >/dev/null || past_the_region=1"
reddens 'an embedded program whose closing quote is deleted' \
  'no longer parses as bash' \
  drop '      '"'"' <<<"$historical_workflow")"'
absorbed 'a multi-line awk program body' \
  insert before-done "  awk '" '    { forged_by_awk=1 }' "  ' </dev/null"
absorbed 'a multi-line jq program body' \
  insert before-done "  jq -n '" '    forged_by_jq=1' "  ' >/dev/null || :"
absorbed 'a multi-line gh --jq program body' \
  insert before-done "  gh api x --jq '" '    forged_by_option=1' "  ' >/dev/null || :"
absorbed 'a multi-line double-quoted sed program body' \
  insert before-done '  sed -nE "' '    s/forged_by_sed=1//p' '  " /dev/null || :'
absorbed 'a multi-line quoted string that is not a program at all' \
  insert before-done "  echo 'first line" '    forged_by_quoted_string=1' "    last line'"
absorbed 'a multi-line --jq=program written as one word' \
  insert before-done "  gh api x --jq='" '    forged_by_inline_option=1' "  ' >/dev/null || :"
reddens 'a dead reset entry spelled only in awk program text' \
  "reset but never assigned in the loop: ['forged_by_awk']" \
  sub '  unset metadata' '  unset forged_by_awk metadata' \
  :: insert before-done "  awk '" '    { forged_by_awk=1 }' "  ' </dev/null"
reddens 'a dead reset entry spelled only in gh --jq program text' \
  "reset but never assigned in the loop: ['forged_by_option']" \
  sub '  unset metadata' '  unset forged_by_option metadata' \
  :: insert before-done "  gh api x --jq '" '    forged_by_option=1' "  ' >/dev/null || :"
# `${name:=…}` assigns only where bash *expands* it. Matched over the raw line rather than
# over the tokenizer's words it forged names from text bash expands nowhere -- single-quoted
# text and a trailing comment -- and a forged name is the silent direction, because it is
# what keeps a dead reset entry green. Both directions are pinned for each surface.
absorbed 'an assigning expansion inside single quotes' \
  insert before-done "  echo '\${forged_by_single_quote:=1}'"
reddens 'a dead reset entry spelled only inside single quotes' \
  "reset but never assigned in the loop: ['forged_by_single_quote']" \
  sub '  unset metadata' '  unset forged_by_single_quote metadata' \
  :: insert before-done "  echo '\${forged_by_single_quote:=1}'"
absorbed 'an assigning expansion in a trailing comment' \
  insert before-done '  metadata=2  # see ${forged_by_comment:=1}'
reddens 'a dead reset entry spelled only in a trailing comment' \
  "reset but never assigned in the loop: ['forged_by_comment']" \
  sub '  unset metadata' '  unset forged_by_comment metadata' \
  :: insert before-done '  metadata=2  # see ${forged_by_comment:=1}'
# Bash honors a backslash escape outside quotes as well as inside double quotes, so
# `echo \${x:=1}` expands nothing and assigns nothing. Both directions are pinned.
absorbed 'an assigning expansion behind a backslash escape' \
  insert before-done '  echo \${forged_by_escape:=1}'
reddens 'a dead reset entry spelled only behind a backslash escape' \
  "reset but never assigned in the loop: ['forged_by_escape']" \
  sub '  unset metadata' '  unset forged_by_escape metadata' \
  :: insert before-done '  echo \${forged_by_escape:=1}'

# The excision's own load-bearing case. Every multi-line `absorbed` row above is absorbed by
# the newline collapse, which is a separate mechanism: disabling the excision entirely left
# all of them green. An assigning expansion inside a single-line double-quoted program is
# text the per-word walk would otherwise read, so only the excision can absorb it.
absorbed 'a double-quoted sed program body containing an assigning expansion' \
  insert before-done '  sed -nE "s/a/${forged_by_sed_expansion:=1}/" /dev/null || :'
# An escaped quote does not close a double-quoted region. Getting this wrong never forged
# and never swallowed, but it misreported a valid program as unparseable bash.
absorbed 'a double-quoted awk program containing an escaped quote' \
  insert before-done '  awk "BEGIN { print \"x\" }" </dev/null || :'

# A `case` arm label is a pattern, not an assignment, even when it spells one. This forged
# in both directions for the same reason the two expansion surfaces above did.
absorbed 'a case arm label that spells an assignment' \
  insert before-done '  case "$relation" in forged_by_arm_label=1) : ;; *) : ;; esac'
reddens 'a dead reset entry spelled only by a case arm label' \
  "reset but never assigned in the loop: ['forged_by_arm_label']" \
  sub '  unset metadata' '  unset forged_by_arm_label metadata' \
  :: insert before-done '  case "$relation" in forged_by_arm_label=1) : ;; *) : ;; esac'
# Anchoring the arm-label alternative to the end of the word is what lets an assignment
# word contain a `)`. Escapes are consumed in *pairs*, so a label whose text contains a
# backslash still strips and the assignment sharing its segment is still seen. All three
# shapes below are real arm labels under bash -- each yields `declare -- y="1"` -- and each
# would be a silent miss if the alternative decided escapedness by a single lookback.
reddens 'an arm label whose own ) is backslash-escaped, followed by an assignment' \
  "assigned per repository but never reset: ['form_arm_escaped']" \
  insert before-done '  case "$relation" in a\)) form_arm_escaped=1 ;; *) : ;; esac'
reddens 'an arm label that is nothing but an escaped ), followed by an assignment' \
  "assigned per repository but never reset: ['form_arm_bare_escaped']" \
  insert before-done '  case "$relation" in \)) form_arm_bare_escaped=1 ;; *) : ;; esac'
reddens 'an arm label ending in an escaped backslash, whose own ) is unescaped' \
  "assigned per repository but never reset: ['form_arm_doubled']" \
  insert before-done '  case "$relation" in a\\) form_arm_doubled=1 ;; *) : ;; esac'
# The label is still decided by the unquoted `)` that terminates it, so a label whose own
# `)` sits *inside* quotes -- `case … in "a)") x=1 ;;` -- is not stripped, and an assignment
# sharing its segment is missed. bash really treats it as an arm label (`declare -- z="1"`).
# Closing it means tracking quoting inside the alternative, which is the point at which a
# real `case`-label parser is warranted rather than a fourth regex; the audited script's
# quoted labels are `""`-shaped and carry no `)`, so it is recorded instead.
boundary 'a quoted arm label whose ) sits inside the quotes hides an assignment' \
  insert before-done '  case "$relation" in "a)") form_arm_quoted=1 ;; *) : ;; esac'

# A heredoc body is parsed as bash, so it forges a name. That is a documented boundary, and
# it is silent in one direction and loud in the other: a name the reset does not list
# reddens as forgotten, while a name the reset *does* list is kept green by the forgery.
# Both directions are pinned so the recorded boundary cannot drift from the code.
reddens 'a heredoc body, which forges loudly in the forgotten direction' \
  "assigned per repository but never reset: ['forged_by_heredoc']" \
  insert before-done "  cat <<'EOT' >/dev/null" 'forged_by_heredoc=1' 'EOT'
boundary 'a heredoc body keeping a dead reset entry green, the silent direction of the same miss' \
  sub '  unset metadata' '  unset forged_by_heredoc metadata' \
  :: insert before-done "  cat <<'EOT' >/dev/null" 'forged_by_heredoc=1' 'EOT'

# The corpus size is asserted, not merely reported: a case deleted or skipped would
# otherwise shrink it silently, which is the failure mode this whole contract exists for.
[ "$injection_cases" -eq 83 ] \
  && pass "the reset contract was exercised against all $injection_cases injected mutations" \
  || fail "the injection corpus has changed size: expected 83 cases, ran $injection_cases"

GH_TOKEN='' run_audit \
  && fail "missing audit credential reported green" \
  || {
    grep -q 'Missing ORG_ADMIN_TOKEN' "$tmp/out" \
      && pass "missing audit credential fails before claiming fleet conformance" \
      || fail "missing audit credential lacks an actionable error"
  }

PRIVILEGED_MERGE_AUDIT_SHA='' run_audit \
  && fail "missing audit SHA reported green" \
  || {
    grep -q 'Invalid privileged merge audit SHA' "$tmp/out" \
      && pass "audit requires a canonical lowercase event SHA" \
      || fail "missing audit SHA lacks an actionable error"
  }

PRIVILEGED_MERGE_AUDIT_SHA="${audit_sha^^}" run_audit \
  && fail "uppercase audit SHA reported green" \
  || {
    grep -q 'Invalid privileged merge audit SHA' "$tmp/out" \
      && pass "audit rejects non-canonical uppercase SHA spelling" \
      || fail "uppercase audit SHA lacks an actionable error"
  }

if python3 - "$workflow" <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    document = yaml.safe_load(stream)
on = document.get(True, document.get("on"))
assert set(on) == {"schedule"}
assert document["permissions"] == {"contents": "read"}
job = document["jobs"]["audit"]
source = ".privileged-merge-conformance-source-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}"
assert set(job) == {"runs-on", "defaults", "timeout-minutes", "steps"}
assert job["runs-on"] == "ubuntu-24.04"
assert job["defaults"] == {"run": {"working-directory": source}}
assert job["timeout-minutes"] == 10
checkout, audit, cleanup = job["steps"]
assert checkout["uses"].startswith("actions/checkout@")
assert len(checkout["uses"].split("@", 1)[1]) == 40
assert checkout["with"] == {
    "ref": "${{ github.sha }}",
    "path": source,
    "persist-credentials": False,
}
assert audit["env"] == {
    "GH_TOKEN": "${{ secrets.ORG_ADMIN_TOKEN }}",
    "PRIVILEGED_MERGE_AUDIT_SHA": "${{ github.sha }}",
}
assert audit["run"] == "bash scripts/privileged-merge-conformance.sh"
assert cleanup["if"] == "${{ always() }}"
assert cleanup["working-directory"] == "${{ github.workspace }}"
assert cleanup["run"] == f'rm -rf "{source}"'
PY
then
  pass "scheduled fleet audit binds code to the event SHA and its privileged token to fixed hosted capacity"
else
  fail "scheduled fleet audit is missing or its privileged execution surface drifted"
fi

grep -q $'\tbash scripts/ci-gate/privileged-merge-conformance.test.sh$' \
  "$root/scripts/actions-ci-groups.tsv" \
  && pass "fleet conformance contract runs in actions CI" \
  || fail "fleet conformance contract is not wired into actions CI"

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
