#!/usr/bin/env bash
# Verify that a dedicated-App check is named by immutable evidence from the
# exact trusted arm run. All identity inputs arrive through the environment.
set -euo pipefail

for value in TARGET_REPO PR_NUMBER EXPECTED_HEAD_SHA AUTHORIZATION_CHECK_ID ARM_RUN_ID ARM_RUN_ATTEMPT EXPECTED_APP_ID EXPECTED_APP_SLUG REVIEW_POLICY; do
  [ -n "${!value:-}" ] || { echo "::error::missing arm receipt identity: $value"; exit 1; }
done
[[ "$TARGET_REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || exit 1
[[ "$PR_NUMBER" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$EXPECTED_HEAD_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 1
[[ "$AUTHORIZATION_CHECK_ID" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$ARM_RUN_ID" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$ARM_RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$EXPECTED_APP_ID" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$EXPECTED_APP_SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]] || exit 1
for tool in gh jq unzip sha256sum; do command -v "$tool" >/dev/null || exit 1; done
tmp="$(mktemp -d "${RUNNER_TEMP:-/tmp}/ai-review-receipt.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
workflow_api() {
  local phase="$1"
  local output="$2"
  shift 2
  local diagnostics="$tmp/${phase}.stderr"
  if ! GH_DEBUG=api gh api "$@" >"$output" 2>"$diagnostics"; then
    echo "::error title=${phase} failed::Workflow-token arm receipt read failed" >&2
    sed -E 's/(authorization: (token|bearer) )[A-Za-z0-9._-]+/\1***/Ig' "$diagnostics" >&2
    return 1
  fi
}
review_policy_json="$(python3 "$(dirname "$0")/review-policy-envelope.py" decode "$REVIEW_POLICY")" || exit 1
review_actor="$(jq -r .actor <<<"$review_policy_json")"
receipt_permission="$(jq -r .actor_permission <<<"$review_policy_json")"
if [ "$receipt_permission" != automation ] && [ "${REVERIFY_ACTOR_PERMISSION:-true}" != false ]; then
  [[ "$review_actor" =~ ^[A-Za-z0-9][A-Za-z0-9-]*$ ]] || exit 1
  case "$receipt_permission" in admin|maintain) ;; *) exit 1 ;; esac
  workflow_api actor-permission "$tmp/actor-permission" \
    "repos/$TARGET_REPO/collaborators/$review_actor/permission" --jq '.permission // ""' || exit 1
  current_permission="$(<"$tmp/actor-permission")"
  case "$current_permission" in admin|maintain) ;; *) echo "::error::re-review actor no longer has maintain/admin permission"; exit 1 ;; esac
fi

workflow_api arm-run "$tmp/arm-run.json" "repos/$TARGET_REPO/actions/runs/$ARM_RUN_ID" || exit 1
arm_run="$(<"$tmp/arm-run.json")"
arm_workflow_id="$(jq -r '.workflow_id // ""' <<<"$arm_run")"
[[ "$arm_workflow_id" =~ ^[1-9][0-9]*$ ]] || exit 1
jq -e --argjson run_id "$ARM_RUN_ID" --argjson attempt "$ARM_RUN_ATTEMPT" --arg repo "$TARGET_REPO" '
  .id == $run_id and .run_attempt == $attempt and
  .event == "pull_request_target" and
  (.path == ".github/workflows/gate-rearm.yml" or .path == ".github/workflows/ai-review-label-rearm.yml" or .path == ".github/workflows/ai-review-lifecycle-rearm.yml") and
  (.head_repository.full_name | ascii_downcase) == ($repo | ascii_downcase) and
  (.repository.full_name | ascii_downcase) == ($repo | ascii_downcase)
' <<<"$arm_run" >/dev/null || { echo "::error::arm run provenance mismatch: run ID, attempt, event, workflow path, or repository"; exit 1; }
arm_run_path="$(jq -r '.path' <<<"$arm_run")"
required_workflow_url="https://api.github.com/repos/$TARGET_REPO/actions/required_workflows/$arm_workflow_id"
if [ "$arm_run_path" = .github/workflows/gate-rearm.yml ] && [ "$(jq -r '.workflow_url // ""' <<<"$arm_run")" = "$required_workflow_url" ]; then
  workflow_api arm-base "$tmp/arm-base" \
    "repos/$TARGET_REPO/pulls/$PR_NUMBER" --jq '.base.ref // ""' || exit 1
  arm_base_branch="$(<"$tmp/arm-base")"
  [[ "$arm_base_branch" =~ ^[A-Za-z0-9._/-]+$ ]] &&
    [[ "$arm_base_branch" != /* && "$arm_base_branch" != *..* && "$arm_base_branch" != *//* ]] || exit 1
  # Spend the base ref as one percent-encoded path segment (#1470): the charset guard
  # above rejects traversal, and the encoding keeps a `release/1.x` ref addressed as
  # `release%2F1.x`, the form every other rules/branches/ reader in this repository uses.
  arm_base_branch_path="$(jq -rn --arg value "$arm_base_branch" '$value|@uri')"
  workflow_api arm-rules "$tmp/arm-rules-pages.json" --paginate \
    "repos/$TARGET_REPO/rules/branches/$arm_base_branch_path" || exit 1
  jq -se '
    all(.[]; type == "array") and
    ([.[][]
      | select(.type == "workflows") as $rule
      | $rule.parameters.workflows[]?
      | select(.path == ".github/workflows/gate-rearm.yml")
      | {source_type: $rule.ruleset_source_type, source: $rule.ruleset_source,
         repository_id, ref}]
     | length > 0 and all(.[];
         .source_type == "Organization" and (.source | ascii_downcase) == "verjson" and
         .repository_id == 1269388380 and .ref == "refs/heads/main"))
  ' "$tmp/arm-rules-pages.json" >/dev/null || {
    echo "::error::arm run provenance mismatch: required-workflow ruleset source"; exit 1;
  }
else
  case "$arm_run_path" in
    .github/workflows/gate-rearm.yml) local_workflow=gate-rearm.yml ;;
    .github/workflows/ai-review-label-rearm.yml) local_workflow=ai-review-label-rearm.yml ;;
    .github/workflows/ai-review-lifecycle-rearm.yml) local_workflow=ai-review-lifecycle-rearm.yml ;;
    *) exit 1 ;;
  esac
  workflow_api arm-workflow "$tmp/arm-workflow-id" \
    "repos/$TARGET_REPO/actions/workflows/$local_workflow" --jq '.id // ""' || exit 1
  [ "$(<"$tmp/arm-workflow-id")" = "$arm_workflow_id" ] || {
    echo "::error::arm run provenance mismatch: workflow ID"; exit 1;
  }
  if [ "$arm_run_path" != .github/workflows/gate-rearm.yml ]; then
    [ "$ARM_RUN_ATTEMPT" = 1 ] || { echo "::error::local caller delivery replay rejected"; exit 1; }
    jq -e '.head_sha | test("^[0-9a-f]{40}$")' <<<"$arm_run" >/dev/null || exit 1
    workflow_api repository-default "$tmp/default-branch" \
      "repos/$TARGET_REPO" --jq '.default_branch // ""' || exit 1
    default_branch="$(<"$tmp/default-branch")"
    [[ "$default_branch" =~ ^[A-Za-z0-9._/-]+$ ]] || exit 1
    branch_ref="$(jq -rn --arg branch "$default_branch" '$branch | @uri | gsub("%2F"; "/")')"
  fi
fi

artifact_name="ai-review-arm-$ARM_RUN_ID-$ARM_RUN_ATTEMPT"
workflow_api arm-artifacts "$tmp/arm-artifacts.json" \
  "repos/$TARGET_REPO/actions/runs/$ARM_RUN_ID/artifacts?per_page=100" || exit 1
artifacts="$(<"$tmp/arm-artifacts.json")"
artifact="$(jq -c --arg name "$artifact_name" '
  [.artifacts[] | select(.name == $name and .expired == false)]
  | if length == 1 then .[0] else error("receipt artifact missing or ambiguous") end
' <<<"$artifacts")" || { echo "::error::arm receipt artifact missing or ambiguous"; exit 1; }
artifact_id="$(jq -r '.id // ""' <<<"$artifact")"
artifact_size="$(jq -r '.size_in_bytes // ""' <<<"$artifact")"
artifact_digest="$(jq -r '.digest // ""' <<<"$artifact")"
[[ "$artifact_id" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$artifact_size" =~ ^[1-9][0-9]*$ ]] && [ "$artifact_size" -le 16384 ] || exit 1
[[ "$artifact_digest" =~ ^sha256:([0-9a-f]{64})$ ]] || exit 1
expected_zip_sha="${BASH_REMATCH[1]}"

workflow_api arm-artifact-download "$tmp/receipt.zip" \
  "repos/$TARGET_REPO/actions/artifacts/$artifact_id/zip" || exit 1
actual_zip_sha="$(sha256sum "$tmp/receipt.zip" | awk '{print $1}')"
[ "$actual_zip_sha" = "$expected_zip_sha" ] || { echo "::error::arm receipt artifact digest mismatch"; exit 1; }
[ "$(unzip -Z1 "$tmp/receipt.zip")" = receipt.json ] || { echo "::error::arm receipt archive shape is invalid"; exit 1; }
unzip -p "$tmp/receipt.zip" receipt.json >"$tmp/receipt.json"
[ "$(wc -c <"$tmp/receipt.json")" -le 8192 ] || exit 1

workflow_api authorization-check "$tmp/authorization-check.json" \
  "repos/$TARGET_REPO/check-runs/$AUTHORIZATION_CHECK_ID" || exit 1
check="$(<"$tmp/authorization-check.json")"
details_url="$GITHUB_SERVER_URL/$TARGET_REPO/actions/runs/$ARM_RUN_ID"
receipt_schema="$(jq -r '.schema // ""' "$tmp/receipt.json")"
receipt_check_app_id="$(jq -r '.check_app_id // ""' "$tmp/receipt.json")"
receipt_check_app_slug="$(jq -r '.check_app_slug // ""' "$tmp/receipt.json")"
if [ "$receipt_schema" = 1 ]; then
  [ "$arm_run_path" = .github/workflows/gate-rearm.yml ] || {
    echo "::error::local caller requires a source-bound schema-2 receipt"; exit 1;
  }
elif [ "$receipt_schema" = 2 ]; then
  case "$arm_run_path" in
    .github/workflows/ai-review-label-rearm.yml)
      local_caller=ai-review-label-rearm.yml
      ;;
    .github/workflows/ai-review-lifecycle-rearm.yml)
      local_caller=ai-review-lifecycle-rearm.yml
      ;;
    *) echo "::error::schema-2 receipt requires an exact protected local caller"; exit 1 ;;
  esac
  receipt_workflow_sha="$(jq -r '.workflow_sha // ""' "$tmp/receipt.json")"
  [[ "$receipt_workflow_sha" =~ ^[0-9a-f]{40}$ ]] || {
    echo "::error::arm receipt workflow SHA is not a 40-hex commit SHA"; exit 1;
  }
  workflow_api caller-at-protected-ref "$tmp/caller-protected-blob" \
    "repos/$TARGET_REPO/contents/.github/workflows/$local_caller?ref=$branch_ref" --jq '.sha // ""' || exit 1
  workflow_api caller-at-receipt-sha "$tmp/caller-receipt-blob" \
    "repos/$TARGET_REPO/contents/.github/workflows/$local_caller?ref=$receipt_workflow_sha" --jq '.sha // ""' || exit 1
  protected_caller_blob="$(<"$tmp/caller-protected-blob")"
  receipt_caller_blob="$(<"$tmp/caller-receipt-blob")"
  [[ "$protected_caller_blob" =~ ^[0-9a-f]{40}$ ]] && [ "$receipt_caller_blob" = "$protected_caller_blob" ] || {
    echo "::error::local caller workflow SHA does not resolve to the protected caller revision"; exit 1;
  }
  jq -e --arg actor "$(jq -r '.actor.login // ""' <<<"$arm_run")" \
    --arg repo "$TARGET_REPO" --arg branch "$default_branch" --arg caller "$local_caller" '
      .delivery_actor == $actor and
      .workflow_ref == ($repo + "/.github/workflows/" + $caller + "@refs/heads/" + $branch) and
      (.workflow_sha | test("^[0-9a-f]{40}$")) and
      (if $caller == "ai-review-label-rearm.yml" then .delivery_event == "labeled"
       else (.delivery_event == "ready_for_review" or .delivery_event == "converted_to_draft" or
             .delivery_event == "edited" or .delivery_event == "unlabeled") end)
    ' "$tmp/receipt.json" >/dev/null || { echo "::error::arm receipt delivery identity mismatch"; exit 1; }
else
  echo "::error::unsupported arm receipt schema"; exit 1
fi

jq -e \
  --arg repository "$TARGET_REPO" --argjson pr_number "$PR_NUMBER" --arg head "$EXPECTED_HEAD_SHA" \
  --argjson check_id "$AUTHORIZATION_CHECK_ID" --argjson run_id "$ARM_RUN_ID" --argjson attempt "$ARM_RUN_ATTEMPT" \
  --arg details_url "$details_url" --argjson app_id "$EXPECTED_APP_ID" --arg app_slug "$EXPECTED_APP_SLUG" \
  --argjson check_app_id "$receipt_check_app_id" --arg check_app_slug "$receipt_check_app_slug" \
  --arg review_policy "$REVIEW_POLICY" '
  ((.schema == 1 and (keys | sort) == (["app_id","app_slug","arm_run_attempt","arm_run_id","check_app_id","check_app_slug","check_run_id","details_url","external_id","head_sha","nonce","pr_number","repository","review_policy","schema"] | sort)) or
   (.schema == 2 and (keys | sort) == (["app_id","app_slug","arm_run_attempt","arm_run_id","check_app_id","check_app_slug","check_run_id","delivery_actor","delivery_event","details_url","external_id","head_sha","nonce","pr_number","repository","review_policy","schema","workflow_ref","workflow_sha"] | sort))) and
  .check_app_id == 15368 and .check_app_slug == "github-actions" and
    .repository == $repository and .pr_number == $pr_number and .head_sha == $head and
  .check_run_id == $check_id and .arm_run_id == $run_id and .arm_run_attempt == $attempt and
  .details_url == $details_url and .app_id == $app_id and .app_slug == $app_slug and
  .review_policy == $review_policy and
  (.nonce | type == "string" and test("^[0-9a-f]{64}$")) and
  .external_id == ("ai-review:v1:" + $repository + ":" + ($pr_number|tostring) + ":" + $head + ":" +
                   ($run_id|tostring) + ":" + ($attempt|tostring) + ":" + .nonce)
' "$tmp/receipt.json" >/dev/null || { echo "::error::arm receipt content mismatch"; exit 1; }

receipt_external_id="$(jq -r .external_id "$tmp/receipt.json")"
# An Actions-owned check does not keep the details_url it was created with: GitHub
# stores the check's own page (#1648). The receipt still names the arm run; the live
# check is bound through its ID, the nonce-bearing external_id, and that stored page.
check_details_url="$GITHUB_SERVER_URL/$TARGET_REPO/runs/$AUTHORIZATION_CHECK_ID"
jq -e --arg head "$EXPECTED_HEAD_SHA" --argjson check_id "$AUTHORIZATION_CHECK_ID" \
  --arg external_id "$receipt_external_id" --arg details_url "$check_details_url" \
  --argjson check_app_id "$receipt_check_app_id" --arg check_app_slug "$receipt_check_app_slug" '
  .id == $check_id and .name == "AI review authorization" and .head_sha == $head and
  .external_id == $external_id and .details_url == $details_url and
  .app.id == $check_app_id and .app.slug == $check_app_slug
' <<<"$check" >/dev/null || { echo "::error::authorization check is not receipt-bound"; exit 1; }

workflow_api current-head "$tmp/current-head" "repos/$TARGET_REPO/pulls/$PR_NUMBER" \
  --jq 'select(.state == "open") | .head.sha // ""' || exit 1
current_head="$(<"$tmp/current-head")"
[ "$current_head" = "$EXPECTED_HEAD_SHA" ] || { echo "::error::authorization head is stale"; exit 1; }
echo "Arm receipt verified for check $AUTHORIZATION_CHECK_ID on $EXPECTED_HEAD_SHA."

# Deleting here, on successful verification, is one call too early: this
# script runs before the rest of the calling step's own work (completing the
# check run, or the terminal merge) has actually succeeded. If that later
# work fails and the step is retried, the retry's own re-verification would
# find the receipt already gone (#943). Instead, a caller that has reached
# its own true terminal consumption point -- confirmed by ITS success, not
# by this script's -- may ask to be told which artifact this was, so it can
# delete it itself once that later success is confirmed. Writing the ID here
# does not delete anything; every caller that leaves this unset keeps
# retention-only behavior.
if [ -n "${ARM_RECEIPT_ARTIFACT_ID_FILE:-}" ]; then
  # Best-effort like the deletion it enables (#947): a write failure here is
  # bookkeeping for a cleanup optimization, not a verification result, and
  # must not turn an otherwise-successful verification into a failure. Written
  # via a same-directory temp file and atomic rename (#950): a partial write
  # (e.g. ENOSPC/EIO) can then never leave a truncated artifact ID at the
  # target path for a later deletion step to read.
  receipt_id_tmp="$(mktemp "${ARM_RECEIPT_ARTIFACT_ID_FILE}.XXXXXX" 2>/dev/null)" && \
    printf '%s\n' "$artifact_id" >"$receipt_id_tmp" && \
    mv -f "$receipt_id_tmp" "$ARM_RECEIPT_ARTIFACT_ID_FILE" || {
    echo "::warning::failed to record consumed arm receipt artifact ID for deferred deletion (non-fatal; verification itself succeeded)"
    rm -f "${receipt_id_tmp:-}" 2>/dev/null
  }
fi
