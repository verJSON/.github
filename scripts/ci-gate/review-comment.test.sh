#!/usr/bin/env bash
# Execute the exact advisory-publication block from the trusted workflow.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
workflow="$root/.github/workflows/ai-review-merge.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

awk '
  $0 == "      - name: Submit deterministic PR review" { seen = 1 }
  seen && $0 == "        run: |" { capture = 1; next }
  capture {
    if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
    if ($0 ~ /^[ \t]*$/) { print ""; next }
    exit
  }
' "$workflow" >"$tmp/submit.sh"
grep -q 'AI review advisory' "$tmp/submit.sh" || { echo "FAIL - submit block extraction failed"; exit 1; }

mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
args=("$@"); body=""
for ((i=0; i<${#args[@]}; i++)); do
  [ "${args[$i]}" = --body ] && body="${args[$((i + 1))]}"
done
case "$1 $2" in
  "pr comment") printf '%s' "$body" >"$COMMENT_FILE"; echo COMMENT >>"$ACTION_LOG" ;;
  "pr edit") echo EDIT >>"$ACTION_LOG" ;;
  "pr review") echo REVIEW >>"$ACTION_LOG"; exit 90 ;;
esac
if [ "$1" = api ]; then
  printf '%s\n' "$CURRENT_HEAD"
fi
GH
chmod +x "$tmp/bin/gh"

run_submit() {
  export PATH="$tmp/bin:$PATH" TARGET_REPO=Verjson/example PR_NUMBER=7 HEAD_SHA=deadbeef MODEL=deepseek-v4-pro
  export PATCH_ID=patch123 GITHUB_RUN_ID=12345 GITHUB_OUTPUT="$tmp/output" COMMENT_FILE="$tmp/comment" ACTION_LOG="$tmp/actions"
  export VERDICT="$1" SELECTED_PASS_TERMINAL_ERROR="${2:-false}" BUDGET_EXHAUSTED="${3:-false}"
  export PRIMARY_EXTRACTION_DIAGNOSTIC="${5:-}"
  export FALLBACK_EXTRACTION_DIAGNOSTIC="${6:-}"
  export CURRENT_HEAD="${4:-deadbeef}" SUPERSEDED=false
  export CHANGED_LINES=42 BUDGET_USD=5.00 PERMISSION_DENIALS=0
  : >"$GITHUB_OUTPUT"; : >"$COMMENT_FILE"; : >"$ACTION_LOG"
  bash -eo pipefail "$tmp/submit.sh" >"$tmp/log" 2>&1
  printf 'rc=%s' "$?"
}
has_comment() { grep -qF "$1" "$tmp/comment"; }
has_output() { grep -qF "$1" "$tmp/output"; }
has_action() { grep -qF "$1" "$tmp/actions"; }

approved='{"blocking":false,"summary":"looks good","review_first":[{"location":"auth.ts:42","why":"gates admin"}],"findings":[],"followups":[]}'
rc="$(run_submit "$approved")"
{ [ "$rc" = rc=0 ] && has_comment 'AI review advisory: non-blocking verdict' && has_comment 'Review these first' && has_output outcome=approved && ! has_action REVIEW; } \
  && pass "non-blocking verdict comments and records approval eligibility without submitting a review" \
  || fail "non-blocking advisory path drifted ($rc)"
has_comment '`auth.ts:42` — gates admin' \
  && pass "canonical review_first.why reaches the rendered advisory" \
  || fail "review_first.why drifted from the renderer"
has_comment '<!-- ai-review-head:deadbeef patchid:patch123 model:deepseek-v4-pro -->' \
  && pass "advisory marker binds head, patch, and selected model" \
  || fail "advisory marker lost immutable review identity"

blocking='{"blocking":true,"summary":"bug","review_first":[],"findings":[{"location":"app.py:7","reason":"broken","failure_scenario":"request fails","evidence":"return <value> && done"}],"followups":[]}'
rc="$(run_submit "$blocking")"
{ [ "$rc" = rc=0 ] && has_comment 'AI review advisory: blocking verdict' && has_comment 'app.py:7' && has_output outcome=blocking && ! has_action REVIEW; } \
  && pass "blocking AI verdict remains advisory and leaves human approval available" \
  || fail "blocking advisory path became a merge veto ($rc)"
has_comment 'Source evidence: <code>return &lt;value&gt; &amp;&amp; done</code>' \
  && pass "validated exact-head evidence is safely published as code" \
  || fail "blocking source evidence was not safely published"

rc="$(run_submit not-json)"
{ [ "$rc" = rc=0 ] && has_action EDIT && has_comment 'review could not complete' && has_output outcome=inconclusive; } \
  && pass "inconclusive provider result cannot block human approval" \
  || fail "inconclusive provider result blocked the required workflow ($rc)"

extraction_diagnostic='{"stage":"completed_response_extraction","kind":"json_decode","content_bytes":42,"content_sha256":"1111111111111111111111111111111111111111111111111111111111111111","json_error":{"line":1,"column":43,"position":42}}'
rc="$(run_submit '' false false deadbeef "$extraction_diagnostic")"
{ [ "$rc" = rc=0 ] && has_action EDIT && has_comment 'completed-response extraction' && has_comment 'DeepSeek pass 1' && has_comment '`json_decode`' && ! has_comment 'DeepSeek pass 2' && ! has_comment 'selected completed DeepSeek response' && has_output outcome=inconclusive && ! has_action REVIEW; } \
  && pass "mixed extraction/other failure attributes the typed cause only to pass 1" \
  || fail "typed extraction failure collapsed into a generic advisory or authorized review ($rc)"

fallback_extraction_diagnostic='{"stage":"completed_response_extraction","kind":"usage_envelope","content_bytes":60,"content_sha256":"2222222222222222222222222222222222222222222222222222222222222222"}'
rc="$(run_submit '' false false deadbeef "$extraction_diagnostic" "$fallback_extraction_diagnostic")"
{ [ "$rc" = rc=0 ] && has_comment 'DeepSeek pass 1' && has_comment '`json_decode`' && has_comment 'DeepSeek pass 2' && has_comment '`usage_envelope`' && has_output outcome=inconclusive && ! has_action REVIEW; } \
  && pass "two distinct extraction failures retain their pass-specific typed causes" \
  || fail "two pass-specific extraction diagnostics were collapsed or misattributed ($rc)"

rc="$(run_submit "$approved" true)"
{ [ "$rc" = rc=0 ] && has_output outcome=inconclusive && ! has_output outcome=approved; } \
  && pass "terminal provider evidence cannot authorize AI approval" \
  || fail "terminal provider error escaped into AI approval ($rc)"

rc="$(run_submit "$approved" true true)"
{ [ "$rc" = rc=0 ] && has_comment 'budget exceeded' && has_comment 'Human approval remains available'; } \
  && pass "budget exhaustion is advisory and actionable" \
  || fail "budget exhaustion lost the human fallback ($rc)"

rc="$(run_submit '' false false cafebabe)"
{ [ "$rc" = rc=0 ] && has_comment 'reviewed head superseded' && has_comment 'deadbeef' && has_comment 'cafebabe' && has_output outcome=superseded && ! has_action EDIT; } \
  && pass "a canceled stale-head review reports the exact supersession instead of a generic no-verdict advisory" \
  || fail "stale-head no-verdict diagnosis was not persisted ($rc)"

rc="$(run_submit "$blocking" false false cafebabe)"
{ [ "$rc" = rc=0 ] && has_comment 'stale-head verdict retained' && has_comment 'app.py:7' && has_comment 'cannot authorize the current head' && has_output outcome=superseded && ! has_output outcome=blocking; } \
  && pass "a validator-confirmed stale-head verdict is retained without authorizing the replacement head" \
  || fail "stale-head verdict evidence was lost or mis-authorized ($rc)"

export PRIMARY_TRANSPORT_FAILURE=pre_token_http PRIMARY_HTTP_STATUS_FAMILY=4xx PRIMARY_FAILURE_CLASS=quota
export PRIMARY_HTTP_STATUS=429 AUTOMATIC_FALLBACK_SUPPRESSED=true PASS_CAP_EXHAUSTED=false REVIEW_PASS_COUNT=1
rc="$(run_submit '')"
{ [ "$rc" = rc=0 ] && has_comment 'provider transport failed' && has_comment '`quota`' && has_comment '`4xx`' \
  && has_comment '`429`' && has_comment 'not a semantic verdict' && has_comment 'Human approval remains available' \
  && has_output outcome=provider_transport && ! has_comment 'two-pass maximum reached' && ! has_action REVIEW; } \
  && pass "identical pre-token HTTP failure names the status class and preserves the second pass" \
  || fail "pre-token HTTP failure was silent or consumed the advisory as a semantic verdict ($rc)"

export PRIMARY_HTTP_STATUS_FAMILY='4xx private-body-sentinel'
export PRIMARY_FAILURE_CLASS='quota private-key-sentinel'
export PRIMARY_HTTP_STATUS='429 private-reason-sentinel'
rc="$(run_submit '')"
{ [ "$rc" = rc=0 ] && has_comment '`unclassified`' && has_output outcome=provider_transport \
  && ! has_comment 'private-body-sentinel' && ! has_comment 'private-key-sentinel' && ! has_comment 'private-reason-sentinel'; } \
  && pass "transport advisory drops untrusted status text" \
  || fail "transport advisory published unsanitized provider text ($rc)"

export PRIMARY_HTTP_STATUS_FAMILY=4xx PRIMARY_FAILURE_CLASS=quota PRIMARY_HTTP_STATUS=429
rc="$(run_submit "$approved")"
{ [ "$rc" = rc=0 ] && has_comment 'non-blocking verdict' && ! has_comment 'provider transport failed' && ! has_output outcome=provider_transport; } \
  && pass "a real verdict still publishes when a stale transport flag is present" \
  || fail "transport classification overrode a real verdict ($rc)"

export PRIMARY_TRANSPORT_FAILURE=pre_token_http PRIMARY_HTTP_STATUS_FAMILY=4xx PRIMARY_FAILURE_CLASS=not_found
export PRIMARY_HTTP_STATUS=404 FALLBACK_TRANSPORT_FAILURE=pre_token_http FALLBACK_HTTP_STATUS_FAMILY=4xx
export FALLBACK_FAILURE_CLASS=not_found FALLBACK_HTTP_STATUS=404 AUTOMATIC_FALLBACK_SUPPRESSED=false
export PASS_CAP_EXHAUSTED=true REVIEW_PASS_COUNT=2
rc="$(run_submit '')"
{ [ "$rc" = rc=0 ] && has_comment 'two-pass maximum reached' && has_comment '`not_found`' && has_comment '`404`' \
  && has_output outcome=inconclusive && ! has_output outcome=provider_transport; } \
  && pass "a model-specific HTTP cascade that consumes both passes stays a two-pass advisory" \
  || fail "model-specific HTTP failure hid the two-pass cap ($rc)"
unset PRIMARY_TRANSPORT_FAILURE PRIMARY_HTTP_STATUS_FAMILY PRIMARY_FAILURE_CLASS PRIMARY_HTTP_STATUS
unset FALLBACK_TRANSPORT_FAILURE FALLBACK_HTTP_STATUS_FAMILY FALLBACK_FAILURE_CLASS FALLBACK_HTTP_STATUS
unset AUTOMATIC_FALLBACK_SUPPRESSED PASS_CAP_EXHAUSTED REVIEW_PASS_COUNT

grep -q -- '--request-changes' "$tmp/submit.sh" \
  && fail "AI advisory still submits a blocking review" \
  || pass "AI advisory never submits CHANGES_REQUESTED"

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
