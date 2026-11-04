#!/usr/bin/env bash
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
verifier="$here/verify-arm-receipt.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

export TARGET_REPO=Verjson/example API_REPO=verJSON/example PR_NUMBER=7
export EXPECTED_HEAD_SHA=0123456789abcdef0123456789abcdef01234567
export AUTHORIZATION_CHECK_ID=9001 ARM_RUN_ID=7001 ARM_RUN_ATTEMPT=2
export EXPECTED_APP_ID=4242 EXPECTED_APP_SLUG=verjson-ai-review
export CHECK_APP_ID=15368 CHECK_APP_SLUG=github-actions
encode_policy() { python3 "$here/review-policy-envelope.py" encode "$1"; }
anthropic_policy='{"actor":"trusted-arm","actor_permission":"automation","authority":"human","budget_usd":"auto","fallback_budget_usd":"","fallback_model":"","model":"auto","pricing_version":"anthropic-native-v1","provider":"anthropic"}'
openai_policy='{"actor":"maintainer","actor_permission":"maintain","authority":"ai-approve","budget_usd":"1.00","fallback_budget_usd":"","fallback_model":"","model":"gpt-5.6-luna","pricing_version":"openai-luna-long-context-2026-08-08","provider":"openai"}'
REVIEW_POLICY="$(encode_policy "$anthropic_policy")"
export REVIEW_POLICY
export GITHUB_SERVER_URL=https://github.com RUNNER_TEMP="$tmp"
nonce=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
external_id="ai-review:v1:$TARGET_REPO:$PR_NUMBER:$EXPECTED_HEAD_SHA:$ARM_RUN_ID:$ARM_RUN_ATTEMPT:$nonce"
details_url="$GITHUB_SERVER_URL/$TARGET_REPO/actions/runs/$ARM_RUN_ID"
# GitHub ignores the details_url an Actions-owned check is created with and stores the
# check's own page; the receipt keeps the arm-run URL (#1648).
check_details_url="$GITHUB_SERVER_URL/$TARGET_REPO/runs/$AUTHORIZATION_CHECK_ID"

mkdir "$tmp/bin" "$tmp/archive"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
if [ -n "${API_FAILURE_MATCH:-}" ] && [[ "$*" == *"$API_FAILURE_MATCH"* ]]; then
  printf 'authorization: token leaked-test-token\nx-github-request-id: TEST:719\n' >&2
  exit 1
fi
case "$*" in
  *"actions/workflows/gate-rearm.yml"*"--jq"*)
    [ "${LOCAL_WORKFLOW_MISSING:-false}" = false ] || exit 1
    printf '%s\n' 77 ;;
  *"actions/workflows/ai-review-label-rearm.yml"*"--jq"*) printf '%s\n' 77 ;;
  *"actions/workflows/ai-review-lifecycle-rearm.yml"*"--jq"*) printf '%s\n' 88 ;;
  *"contents/.github/workflows/ai-review-lifecycle-rearm.yml?ref="*"--jq"*)
    case "$*" in
      *"ref=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"*) printf '%s\n' cccccccccccccccccccccccccccccccccccccccc ;;
      *) printf '%s\n' dddddddddddddddddddddddddddddddddddddddd ;;
    esac ;;
  *"contents/.github/workflows/ai-review-label-rearm.yml?ref="*"--jq"*)
    case "$*" in
      *"ref=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"*) printf '%s\n' cccccccccccccccccccccccccccccccccccccccc ;;
      *) printf '%s\n' dddddddddddddddddddddddddddddddddddddddd ;;
    esac ;;
  *"actions/runs/$ARM_RUN_ID/artifacts"*) cat "$ARTIFACTS_FILE" ;;
  *"actions/runs/$ARM_RUN_ID"*) cat "$RUN_FILE" ;;
  *"rules/branches/"*)
    [ -z "${RULES_REQUEST_LOG:-}" ] || printf '%s\n' "$*" >>"$RULES_REQUEST_LOG"
    cat "$RULES_FILE" ;;
  *"actions/artifacts/"*"/zip"*) cat "$ZIP_FILE" ;;
  *"--method DELETE"*"actions/artifacts/"*)
    [ "${DELETE_FAILURE:-false}" = false ] || exit 1
    printf '{}' ;;
  *"check-runs/$AUTHORIZATION_CHECK_ID"*) cat "$CHECK_FILE" ;;
  *"collaborators/maintainer/permission"*) printf '%s\n' "${CURRENT_PERMISSION:-maintain}" ;;
  *"pulls/$PR_NUMBER"*".base.ref"*) printf '%s\n' "${BASE_REF:-main}" ;;
  *"repos/$TARGET_REPO --jq .default_branch // \"\""*) printf '%s\n' main ;;
  *"pulls/$PR_NUMBER"*"--jq"*)
    [ "${CURRENT_STATE:-open}" = open ] && printf '%s\n' "${CURRENT_HEAD:-$EXPECTED_HEAD_SHA}" ;;
  *) echo "unexpected gh call: $*" >&2; exit 2 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" RUN_FILE="$tmp/run.json" ARTIFACTS_FILE="$tmp/artifacts.json"
export CHECK_FILE="$tmp/check.json" ZIP_FILE="$tmp/receipt.zip" RULES_FILE="$tmp/rules.json"

write_base() {
  printf '%s\n' '[{"type":"workflows","ruleset_source_type":"Organization","ruleset_source":"verJSON","parameters":{"workflows":[{"path":".github/workflows/gate-rearm.yml","ref":"refs/heads/main","repository_id":1269388380}]}}]' >"$RULES_FILE"
  jq -nc --argjson id "$ARM_RUN_ID" --argjson attempt "$ARM_RUN_ATTEMPT" --arg repo "$API_REPO" \
    --arg workflow_url "https://api.github.com/repos/$TARGET_REPO/actions/workflows/77" \
    '{id:$id,run_attempt:$attempt,workflow_id:77,event:"pull_request_target",path:".github/workflows/gate-rearm.yml",workflow_url:$workflow_url,head_branch:"main",head_repository:{full_name:$repo},repository:{full_name:$repo},actor:{login:"maintainer"}}' >"$RUN_FILE"
  jq -nc --argjson id "$AUTHORIZATION_CHECK_ID" --arg head "$EXPECTED_HEAD_SHA" --arg external "$external_id" \
    --arg url "$check_details_url" --argjson app "$CHECK_APP_ID" --arg slug "$CHECK_APP_SLUG" \
    '{id:$id,name:"AI review authorization",head_sha:$head,external_id:$external,details_url:$url,app:{id:$app,slug:$slug},status:"in_progress",conclusion:null}' >"$CHECK_FILE"
  jq -nc --arg repository "$TARGET_REPO" --argjson pr_number "$PR_NUMBER" --arg head_sha "$EXPECTED_HEAD_SHA" \
    --argjson check_run_id "$AUTHORIZATION_CHECK_ID" --argjson arm_run_id "$ARM_RUN_ID" --argjson arm_run_attempt "$ARM_RUN_ATTEMPT" \
    --arg nonce "$nonce" --arg external_id "$external_id" --arg details_url "$details_url" \
    --argjson app_id "$EXPECTED_APP_ID" --arg app_slug "$EXPECTED_APP_SLUG" \
  --argjson check_app_id "$CHECK_APP_ID" --arg check_app_slug "$CHECK_APP_SLUG" \
    --arg review_policy "$REVIEW_POLICY" \
    '{schema:1,repository:$repository,pr_number:$pr_number,head_sha:$head_sha,check_run_id:$check_run_id,
      arm_run_id:$arm_run_id,arm_run_attempt:$arm_run_attempt,nonce:$nonce,external_id:$external_id,
      details_url:$details_url,app_id:$app_id,app_slug:$app_slug,check_app_id:$check_app_id,check_app_slug:$check_app_slug,review_policy:$review_policy}' >"$tmp/archive/receipt.json"
  rm -f "$ZIP_FILE"
  (cd "$tmp/archive" && python3 -m zipfile -c "$ZIP_FILE" receipt.json)
  digest="$(sha256sum "$ZIP_FILE" | awk '{print $1}')"
  size="$(wc -c <"$ZIP_FILE")"
  jq -nc --argjson size "$size" --arg digest "sha256:$digest" --arg name "ai-review-arm-$ARM_RUN_ID-$ARM_RUN_ATTEMPT" \
    '{artifacts:[{id:8001,name:$name,expired:false,size_in_bytes:$size,digest:$digest}]}' >"$ARTIFACTS_FILE"
  unset CURRENT_HEAD
}

write_ruleset_run() {
  write_base
  # Required-workflow run head_sha is the PR head, while github.workflow_sha is
  # the protected workflow source revision. Schema 1 deliberately does not
  # collapse those distinct identities into one field.
  jq '.workflow_id=88 | .workflow_url="https://api.github.com/repos/Verjson/example/actions/required_workflows/88" | .head_sha="fedcba9876543210fedcba9876543210fedcba98"' \
    "$RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$RUN_FILE"
}

expect_pass() { label="$1"; shift; if "$@" >"$tmp/out" 2>&1; then pass "$label"; else fail "$label: $(tail -1 "$tmp/out")"; fi; }
expect_reason_fail() {
  label="$1" reason="$2"; shift 2
  if "$@" >"$tmp/out" 2>&1; then
    fail "$label"
  elif grep -qF -- "$reason" "$tmp/out"; then
    pass "$label"
  else
    fail "$label: $(tail -1 "$tmp/out")"
  fi
}
expect_provenance_fail() {
  label="$1"; shift
  expect_reason_fail "$label" '::error::arm run provenance mismatch: required-workflow ruleset source' "$@"
}
verify() { bash "$verifier"; }
repack() {
  rm -f "$ZIP_FILE"
  (cd "$tmp/archive" && python3 -m zipfile -c "$ZIP_FILE" receipt.json)
  digest="$(sha256sum "$ZIP_FILE" | awk '{print $1}')"; size="$(wc -c <"$ZIP_FILE")"
  jq -nc --argjson size "$size" --arg digest "sha256:$digest" --arg name "ai-review-arm-$ARM_RUN_ID-$ARM_RUN_ATTEMPT" \
    '{artifacts:[{id:8001,name:$name,expired:false,size_in_bytes:$size,digest:$digest}]}' >"$ARTIFACTS_FILE"
}

write_label_run() {
  export ARM_RUN_ATTEMPT=1
  REVIEW_POLICY="$(encode_policy "$openai_policy")"
  export REVIEW_POLICY
  external_id="ai-review:v1:$TARGET_REPO:$PR_NUMBER:$EXPECTED_HEAD_SHA:$ARM_RUN_ID:$ARM_RUN_ATTEMPT:$nonce"
  write_base
  workflow_sha=fedcba9876543210fedcba9876543210fedcba98
  jq --arg sha "$workflow_sha" \
    '.event="pull_request_target" | .path=".github/workflows/ai-review-label-rearm.yml" | .run_attempt=1 | .head_sha=$sha | .actor={login:"maintainer"}' \
    "$RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$RUN_FILE"
  jq --arg sha "$workflow_sha" \
    '.schema=2 | .delivery_event="labeled" | .delivery_actor="maintainer" |
     .workflow_ref="Verjson/example/.github/workflows/ai-review-label-rearm.yml@refs/heads/main" |
     .workflow_sha=$sha' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"
  repack
}

write_lifecycle_run() {
  write_label_run
  jq '.path=".github/workflows/ai-review-lifecycle-rearm.yml" | .workflow_id=88 | .head_branch="main"' \
    "$RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$RUN_FILE"
  jq '.delivery_event="ready_for_review" | .workflow_ref="Verjson/example/.github/workflows/ai-review-lifecycle-rearm.yml@refs/heads/main"' \
    "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"
  repack
}

write_base; expect_pass "exact Actions-owned check and immutable receipt are accepted" verify
write_ruleset_run
LOCAL_WORKFLOW_MISSING=true expect_pass "ruleset-created arm accepts distinct protected workflow and PR-head identities" verify
write_label_run; expect_pass "separate pull_request_target label caller accepts an exact schema-2 receipt" verify
write_label_run; jq 'del(.delivery_event,.delivery_actor,.workflow_ref,.workflow_sha) | .schema=1' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"; repack; expect_reason_fail "label caller rejects schema-1 receipt" '::error::local caller requires a source-bound schema-2 receipt' verify
for field in delivery_event delivery_actor workflow_ref workflow_sha; do
  write_label_run
  jq --arg field "$field" '.[$field]="attacker-substitution"' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"
  case "$field" in
    workflow_sha) substitution_reason='::error::arm receipt workflow SHA is not a 40-hex commit SHA' ;;
    *) substitution_reason='::error::arm receipt delivery identity mismatch' ;;
  esac
  repack; expect_reason_fail "schema-2 receipt rejects substituted $field" "$substitution_reason" verify
done
write_label_run; jq '.workflow_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"; repack; expect_reason_fail "schema-2 receipt rejects a different valid 40-hex caller workflow SHA" '::error::local caller workflow SHA does not resolve to the protected caller revision' verify
write_label_run; jq '.workflow_ref="Verjson/example/.github/workflows/ai-review-label-rearm.yml@refs/heads/topic"' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"; repack; expect_reason_fail "label caller rejects wrong protected source branch" '::error::arm receipt delivery identity mismatch' verify
write_label_run
export ARM_RUN_ATTEMPT=2
# Every other identity agrees on attempt 2, so the replay guard is the only check
# that can reject this receipt (#1995).
replayed_external_id="ai-review:v1:$TARGET_REPO:$PR_NUMBER:$EXPECTED_HEAD_SHA:$ARM_RUN_ID:$ARM_RUN_ATTEMPT:$nonce"
jq '.run_attempt=2' "$RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$RUN_FILE"
jq --arg external_id "$replayed_external_id" '.arm_run_attempt=2 | .external_id=$external_id' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"
jq --arg external_id "$replayed_external_id" '.external_id=$external_id' "$CHECK_FILE" >"$tmp/x" && mv "$tmp/x" "$CHECK_FILE"
repack; expect_reason_fail "label delivery rejects attempt greater than one" '::error::local caller delivery replay rejected' verify
write_label_run; jq '.path=".github/workflows/gate-rearm.yml"' "$RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$RUN_FILE"; expect_reason_fail "required arm run rejects label-caller schema-2 cross-mode receipt" '::error::schema-2 receipt requires an exact protected local caller' verify
for valid_event in ready_for_review converted_to_draft edited unlabeled; do
  write_lifecycle_run
  jq --arg event "$valid_event" '.delivery_event=$event' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"
  repack; expect_pass "lifecycle caller accepts exact action $valid_event" verify
done
for invalid_event in labeled pull_request_target opened; do
  write_lifecycle_run
  jq --arg event "$invalid_event" '.delivery_event=$event' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"
  repack; expect_reason_fail "lifecycle caller rejects delivery event $invalid_event" '::error::arm receipt delivery identity mismatch' verify
done
write_lifecycle_run; jq '.path=".github/workflows/ai-review-label-rearm.yml"' "$RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$RUN_FILE"; expect_reason_fail "lifecycle receipt rejects a different protected caller path" '::error::arm run provenance mismatch: workflow ID' verify
write_lifecycle_run; jq '.workflow_ref="Verjson/example/.github/workflows/ai-review-lifecycle-rearm.yml@refs/heads/topic"' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"; repack; expect_reason_fail "lifecycle caller rejects wrong protected branch" '::error::arm receipt delivery identity mismatch' verify
write_lifecycle_run; jq '.workflow_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"; repack; expect_reason_fail "lifecycle caller rejects a different valid workflow blob" '::error::local caller workflow SHA does not resolve to the protected caller revision' verify
write_lifecycle_run; jq '.workflow_sha="malformed"' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"; repack; expect_reason_fail "lifecycle caller rejects malformed workflow metadata" '::error::arm receipt workflow SHA is not a 40-hex commit SHA' verify
write_lifecycle_run; jq '.workflow_id=77' "$RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$RUN_FILE"; expect_reason_fail "lifecycle caller rejects a mismatched workflow ID" '::error::arm run provenance mismatch: workflow ID' verify
write_lifecycle_run; jq '.run_attempt=2' "$RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$RUN_FILE"; expect_reason_fail "lifecycle caller rejects replayed source run" '::error::arm run provenance mismatch: run ID, attempt, event, workflow path, or repository' verify
export ARM_RUN_ATTEMPT=2
REVIEW_POLICY="$(encode_policy "$anthropic_policy")"
export REVIEW_POLICY
external_id="ai-review:v1:$TARGET_REPO:$PR_NUMBER:$EXPECTED_HEAD_SHA:$ARM_RUN_ID:$ARM_RUN_ATTEMPT:$nonce"
write_ruleset_run
printf '%s\n' '[{"type":"workflows","ruleset_source_type":"Repository","ruleset_source":"Verjson/example","parameters":{"workflows":[{"path":".github/workflows/gate-rearm.yml","ref":"refs/heads/main","repository_id":1269388380}]}}]' >"$RULES_FILE"
LOCAL_WORKFLOW_MISSING=true expect_provenance_fail "matching-path required workflow from an untrusted ruleset origin is rejected" verify
write_ruleset_run
jq '.[0].ruleset_source="OtherOrg"' "$RULES_FILE" >"$tmp/x" && mv "$tmp/x" "$RULES_FILE"
LOCAL_WORKFLOW_MISSING=true expect_provenance_fail "matching-path required workflow from the wrong organization is rejected" verify
write_ruleset_run
jq '.[0].ruleset_source="v\u0435rJSON"' "$RULES_FILE" >"$tmp/x" && mv "$tmp/x" "$RULES_FILE"
LOCAL_WORKFLOW_MISSING=true expect_provenance_fail "confusable workflow-source name is rejected" verify
write_ruleset_run
jq '.[0].parameters.workflows[0].repository_id=999' "$RULES_FILE" >"$tmp/x" && mv "$tmp/x" "$RULES_FILE"
LOCAL_WORKFLOW_MISSING=true expect_provenance_fail "matching-path required workflow from the wrong canonical repository is rejected" verify
write_ruleset_run
printf '%s\n%s\n' \
  '[{"type":"workflows","ruleset_source_type":"Organization","ruleset_source":"Verjson","parameters":{"workflows":[{"path":".github/workflows/gate-rearm.yml","ref":"refs/heads/main","repository_id":1269388380}]}}]' \
  '[{"type":"workflows","ruleset_source_type":"Repository","ruleset_source":"Verjson/example","parameters":{"workflows":[{"path":".github/workflows/gate-rearm.yml","ref":"refs/heads/main","repository_id":1269388380}]}}]' >"$RULES_FILE"
LOCAL_WORKFLOW_MISSING=true expect_provenance_fail "a later matching-path impostor rule withdraws required-workflow trust" verify
write_ruleset_run
jq '.[0].parameters.workflows[0].ref="refs/heads/topic"' "$RULES_FILE" >"$tmp/x" && mv "$tmp/x" "$RULES_FILE"
LOCAL_WORKFLOW_MISSING=true expect_provenance_fail "a matching-path rule at an untrusted source ref is rejected" verify
write_base; REVIEW_POLICY="$(encode_policy "$openai_policy")" expect_reason_fail "a provider change after authorization is rejected" '::error::arm receipt content mismatch' verify
REVIEW_POLICY="$(encode_policy "$openai_policy")"; write_base; expect_pass "maintainer re-review evidence is reauthorized" verify
write_base; CURRENT_PERMISSION=triage REVERIFY_ACTOR_PERMISSION=false expect_pass "completion trusts the immutable arm permission without repository administration access" verify
write_base; CURRENT_PERMISSION=triage expect_reason_fail "permission revocation after authorization fails closed" '::error::re-review actor no longer has maintain/admin permission' verify; unset CURRENT_PERMISSION
REVIEW_POLICY="$(encode_policy "$anthropic_policy")"
export REVIEW_POLICY
write_base; jq '.details_url="https://github.com/Verjson/example/actions/runs/9999"' "$CHECK_FILE" >"$tmp/x" && mv "$tmp/x" "$CHECK_FILE"; expect_reason_fail "forged same-App details URL is rejected" '::error::authorization check is not receipt-bound' verify
write_base; jq '.details_url="https://github.com/Verjson/example/actions/runs/7001"' "$CHECK_FILE" >"$tmp/x" && mv "$tmp/x" "$CHECK_FILE"; expect_reason_fail "an Actions-owned check carrying the arm-run URL is not the stored check (#1648)" '::error::authorization check is not receipt-bound' verify
write_base; jq '.details_url="https://github.com/Verjson/example/runs/9002"' "$CHECK_FILE" >"$tmp/x" && mv "$tmp/x" "$CHECK_FILE"; expect_reason_fail "a check page URL for another check ID is rejected" '::error::authorization check is not receipt-bound' verify
write_base; jq '.check_run_id=9002' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"; repack; expect_reason_fail "receipt/check ID mismatch is rejected" '::error::arm receipt content mismatch' verify
write_base; jq '.nonce="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"; repack; expect_reason_fail "nonce/external_id mismatch is rejected" '::error::arm receipt content mismatch' verify
write_base; CURRENT_HEAD=ffffffffffffffffffffffffffffffffffffffff expect_reason_fail "stale PR head is rejected" '::error::authorization head is stale' verify
write_base; API_FAILURE_MATCH='actions/workflows/gate-rearm.yml' expect_reason_fail "workflow API failures fail closed" '::error title=arm-workflow failed::Workflow-token arm receipt read failed' verify
grep -q 'arm-workflow failed' "$tmp/out" \
  && grep -q 'x-github-request-id: TEST:719' "$tmp/out" \
  && grep -q 'authorization: token \*\*\*' "$tmp/out" \
  && ! grep -q 'leaked-test-token' "$tmp/out" \
  && pass "workflow API failure identifies its phase without leaking credentials" \
  || fail "workflow API failure diagnostics are unsafe or ambiguous"
write_base; jq '.run_attempt=3' "$RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$RUN_FILE"; expect_reason_fail "run-attempt replay is rejected" '::error::arm run provenance mismatch: run ID, attempt, event, workflow path, or repository' verify
write_base; printf '{"artifacts":[]}\n' >"$ARTIFACTS_FILE"; expect_reason_fail "missing arm artifact is rejected" '::error::arm receipt artifact missing or ambiguous' verify
write_base; rm "$tmp/archive/receipt.json" "$ZIP_FILE"; printf 'bad\n' >"$tmp/archive/other"; (cd "$tmp/archive" && python3 -m zipfile -c "$ZIP_FILE" other); digest="$(sha256sum "$ZIP_FILE"|awk '{print $1}')"; size="$(wc -c <"$ZIP_FILE")"; jq -nc --argjson size "$size" --arg digest "sha256:$digest" '{artifacts:[{id:8001,name:"ai-review-arm-7001-2",expired:false,size_in_bytes:$size,digest:$digest}]}' >"$ARTIFACTS_FILE"; expect_reason_fail "malformed artifact archive is rejected" '::error::arm receipt archive shape is invalid' verify
write_base; jq '.artifacts[0].digest="sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"' "$ARTIFACTS_FILE" >"$tmp/x" && mv "$tmp/x" "$ARTIFACTS_FILE"; expect_reason_fail "substituted artifact digest is rejected" '::error::arm receipt artifact digest mismatch' verify
write_base; jq '.app.id=9999' "$CHECK_FILE" >"$tmp/x" && mv "$tmp/x" "$CHECK_FILE"; expect_reason_fail "shared or wrong check-run owner is rejected" '::error::authorization check is not receipt-bound' verify
write_base
jq 'del(.check_app_id, .check_app_slug)' "$tmp/archive/receipt.json" >"$tmp/x" && mv "$tmp/x" "$tmp/archive/receipt.json"
jq --argjson app "$EXPECTED_APP_ID" --arg slug "$EXPECTED_APP_SLUG" \
  '.app.id=$app | .app.slug=$slug' "$CHECK_FILE" >"$tmp/x" && mv "$tmp/x" "$CHECK_FILE"
repack
expect_reason_fail "legacy review-App-owned receipt is rejected after compatibility retirement" '::error::arm receipt content mismatch' verify

# #931/#943: this script never deletes the receipt itself -- deletion happens
# too early here, before the calling step's own later work (completing the
# check run, or the terminal merge) has actually succeeded. A caller that has
# reached its own true terminal consumption point may ask, via
# ARM_RECEIPT_ARTIFACT_ID_FILE, to be told which artifact this was, so it can
# delete it itself once that later success is confirmed.
write_base; artifact_id_file="$tmp/artifact-id"; rm -f "$artifact_id_file"
if ARM_RECEIPT_ARTIFACT_ID_FILE="$artifact_id_file" verify >"$tmp/out" 2>&1 \
    && [ "$(cat "$artifact_id_file")" = 8001 ] \
    && ! grep -qF 'Consumed arm receipt artifact' "$tmp/out"; then
  pass "ARM_RECEIPT_ARTIFACT_ID_FILE records the verified artifact ID without deleting it"
else
  fail "ARM_RECEIPT_ARTIFACT_ID_FILE did not record the artifact ID, or the script deleted it itself"
fi
write_base; expect_pass "ARM_RECEIPT_ARTIFACT_ID_FILE unset (default) never writes or deletes anything" verify
grep -qF 'Consumed arm receipt artifact' "$tmp/out" && fail "default run unexpectedly deleted the receipt artifact"

# #947: recording the artifact ID for deferred deletion is best-effort too --
# a write failure must not turn an otherwise-successful verification into one.
write_base
if ARM_RECEIPT_ARTIFACT_ID_FILE="$tmp/nonexistent-dir/artifact-id" verify >"$tmp/out" 2>&1 \
    && grep -qF '::warning::failed to record consumed arm receipt artifact ID' "$tmp/out"; then
  pass "an unwritable ARM_RECEIPT_ARTIFACT_ID_FILE is non-fatal to an otherwise-valid verification (#947)"
else
  fail "an unwritable ARM_RECEIPT_ARTIFACT_ID_FILE incorrectly failed verification (#947)"
fi

# #950: the artifact ID is written via a same-directory temp file and atomic
# rename, so a write failure can never leave a truncated ID at the target
# path. A pre-existing (e.g. prior-run) target file must survive untouched.
# #953: a root runner ignores directory permission bits, so chmod 555 would
# not actually deny the write there -- skip rather than false-fail.
if [ "$(id -u)" -eq 0 ]; then
  pass "a write failure never truncates or overwrites a pre-existing artifact ID file (#950, skipped: running as root, permission bits do not restrict root)"
else
  write_base
  readonly_dir="$tmp/readonly-dir"; mkdir -p "$readonly_dir"
  artifact_id_file="$readonly_dir/artifact-id"
  printf 'sentinel-prior-id\n' >"$artifact_id_file"
  chmod 555 "$readonly_dir"
  if ARM_RECEIPT_ARTIFACT_ID_FILE="$artifact_id_file" verify >"$tmp/out" 2>&1 \
      && grep -qF '::warning::failed to record consumed arm receipt artifact ID' "$tmp/out" \
      && [ "$(cat "$artifact_id_file")" = sentinel-prior-id ]; then
    pass "a write failure never truncates or overwrites a pre-existing artifact ID file (#950)"
  else
    fail "a write failure corrupted or replaced the pre-existing artifact ID file (#950)"
  fi
  chmod 755 "$readonly_dir"
fi

# The base ref is spent as ONE percent-encoded path segment of rules/branches/ (#1470):
# a nested ref must travel as release%2F1.x, never with a literal slash.
write_ruleset_run
export RULES_REQUEST_LOG="$tmp/rules-requests"; : >"$RULES_REQUEST_LOG"
if BASE_REF="release/1.x" LOCAL_WORKFLOW_MISSING=true verify >"$tmp/out" 2>&1 \
    && grep -qE -- "rules/branches/release%2F1\.x( |$)" "$RULES_REQUEST_LOG" \
    && ! grep -qF -- "rules/branches/release/1.x" "$RULES_REQUEST_LOG"; then
  pass "a slash-bearing base ref is requested as rules/branches/release%2F1.x (#1470)"
else
  fail "a slash-bearing base ref was not requested as rules/branches/release%2F1.x: $(cat "$RULES_REQUEST_LOG"; tail -1 "$tmp/out")"
fi
: >"$RULES_REQUEST_LOG"
if LOCAL_WORKFLOW_MISSING=true verify >"$tmp/out" 2>&1 && grep -qE -- "rules/branches/main( |$)" "$RULES_REQUEST_LOG"; then
  pass "a plain base ref is requested as rules/branches/main"
else
  fail "a plain base ref was not requested as rules/branches/main: $(cat "$RULES_REQUEST_LOG"; tail -1 "$tmp/out")"
fi
unset RULES_REQUEST_LOG

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
