#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/ai-promotion-retry.yml"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fails=0
pass(){ printf 'ok   - %s\n' "$1"; }
fail(){ printf 'FAIL - %s\n' "$1"; fails=$((fails+1)); }

python3 - "$workflow" <<'PY'
import sys
import yaml

workflow = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
jobs = workflow["jobs"]
allow_guard = "(github.event_name != 'pull_request' && github.event_name != 'pull_request_review' && github.event_name != 'pull_request_review_comment' && github.event_name != 'merge_group' && github.event_name != 'pull_request_target' || github.event.repository.private == true)"
resolve = jobs["resolve"]
steps = {step.get("name"): step for step in resolve["steps"]}
assert resolve["outputs"]["ready"] == "${{ steps.resolve.outputs.ready || steps.adoption.outputs.ready }}"
assert steps["Resolve executing trusted workflow revision"]["if"] == "steps.adoption.outputs.adopted == 'true'"
assert steps["Check out immutable review-policy decoder"]["if"] == "steps.adoption.outputs.adopted == 'true'"
assert steps["Resolve exact-head authorization"]["if"] == "steps.adoption.outputs.adopted == 'true'"
assert resolve["if"] == "${{ " + allow_guard + " }}"
assert jobs["promote"]["if"] == "${{ " + allow_guard + " && (needs.resolve.outputs.ready == 'true') }}"
PY
if [ "$?" -eq 0 ]; then
  pass "unadopted resolution skips checkout and only ready=true can promote"
else
  fail "workflow adoption or promotion guard drifted"
fi

awk '$0=="      - name: Resolve AI review adoption"{f=1;next} f&&$0=="        run: |"{r=1;next} r{if($0~/^      - name:/ || $0~/^  [A-Za-z0-9_-]+:/)exit;sub(/^          /,"");print}' \
  "$workflow" >"$tmp/adoption.sh"
[ -s "$tmp/adoption.sh" ] || { echo "FAIL - retry adoption block missing"; exit 1; }

awk '$0=="      - name: Resolve exact-head authorization"{f=1;next} f&&$0=="        run: |"{r=1;next} r{if($0~/^      - name:/ || $0~/^  [A-Za-z0-9_-]+:/)exit;sub(/^          /,"");print}' \
  "$workflow" >"$tmp/resolve.sh"
[ -s "$tmp/resolve.sh" ] || { echo "FAIL - retry resolution block missing"; exit 1; }

mkdir -p "$tmp/bin" "$tmp/run/.promotion-trust/scripts/ci-gate"
cp "$root/scripts/ci-gate/review-policy-envelope.py" \
  "$tmp/run/.promotion-trust/scripts/ci-gate/review-policy-envelope.py"
cat >"$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CALLS"
case "$*" in
  *"commits/"*"/check-runs --jq "*)
    expected='[.check_runs[] | select(.name == "AI review authorization" and .head_sha == "'$HEAD_SHA'" and .app.id == 15368 and .app.slug == "github-actions")] | sort_by(.id) | last // {}'
    [ "${*: -1}" = "$expected" ] || { echo "unexpected check selector: ${*: -1}" >&2; exit 2; }
    cat "$CHECK_FILE" ;;
  *"actions/runs/7001 --jq "*) printf '2\n' ;;
  "run download 7001 "*)
    for arg in "$@"; do destination="$arg"; done
    mkdir -p "$destination"
    printf '{"review_policy":"%s"}\n' "$REVIEW_POLICY" >"$destination/receipt.json" ;;
  *) echo "unexpected gh call: $*" >&2; exit 2 ;;
esac
SH
chmod +x "$tmp/bin/gh"

export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" CHECK_FILE="$tmp/check.json"
export GITHUB_REPOSITORY=Verjson/example GITHUB_OUTPUT="$tmp/github-output" RUNNER_TEMP="$tmp"
export EXPECTED_APP_ID=4242 EXPECTED_APP_SLUG=ai-review-authorization
export HEAD_SHA=0123456789abcdef0123456789abcdef01234567
export PULL_REQUESTS='[{"number":7}]'

policy() {
  python3 "$root/scripts/ci-gate/review-policy-envelope.py" encode \
    "{\"actor\":\"trusted-arm\",\"actor_permission\":\"automation\",\"authority\":\"$1\",\"budget_usd\":\"auto\",\"fallback_budget_usd\":\"auto\",\"fallback_model\":\"auto\",\"model\":\"auto\",\"pricing_version\":\"test-v1\",\"provider\":\"test\"}"
}

write_check() {
  local summary="$1" head="${2:-$HEAD_SHA}" app_id="${3:-15368}" app_slug="${4:-github-actions}"
  jq -nc --arg head "$head" --arg summary "$summary" --argjson app_id "$app_id" \
    --arg slug "$app_slug" \
    '{id:9001,name:"AI review authorization",head_sha:$head,status:"completed",conclusion:"success",external_id:"ai-review:v1:Verjson/example:7:0123456789abcdef0123456789abcdef01234567:7001:2:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",details_url:"https://github.com/Verjson/example/runs/9001",app:{id:$app_id,slug:$slug},output:{summary:$summary}}' \
    >"$CHECK_FILE"
}

run_case() {
  : >"$CALLS"; : >"$GITHUB_OUTPUT"
  (cd "$tmp/run" && bash "$tmp/resolve.sh") >"$tmp/out" 2>&1
}

expect_noop() {
  local label="$1"
  if run_case && grep -qx 'ready=false' "$GITHUB_OUTPUT" && ! grep -q '^ready=true' "$GITHUB_OUTPUT"; then
    pass "$label"
  else
    fail "$label"
  fi
}

expect_identity_error() {
  local label="$1" diagnostic="$2"
  if ! (cd "$tmp/run" && bash "$tmp/adoption.sh") >"$tmp/out" 2>&1 \
     && grep -qxF "::error::$diagnostic" "$tmp/out" \
     && ! grep -q '^ready=true' "$GITHUB_OUTPUT"; then
    pass "$label"
  else
    fail "$label"
  fi
}

export EXPECTED_APP_ID='' EXPECTED_APP_SLUG=''
if : >"$GITHUB_OUTPUT" \
   && (cd "$tmp/run" && bash "$tmp/adoption.sh") \
   && grep -qx 'adopted=false' "$GITHUB_OUTPUT" \
   && grep -qx 'ready=false' "$GITHUB_OUTPUT" \
   && ! grep -q '^ready=true' "$GITHUB_OUTPUT" \
   && [ ! -s "$CALLS" ]; then
  pass "an unadopted repository is a quiet no-op"
else
  fail "an unadopted repository did not stop before promotion lookup"
fi

export EXPECTED_APP_ID=4242 EXPECTED_APP_SLUG='' PULL_REQUESTS='[]'
expect_identity_error \
  "partial App identity fails closed before event eligibility" \
  "AI_REVIEW_APP_SLUG is required when AI_REVIEW_APP_ID is configured"

export EXPECTED_APP_ID='' EXPECTED_APP_SLUG=ai-review-authorization PULL_REQUESTS='[{"number":7}]'
expect_identity_error \
  "partial App identity fails closed when the ID is absent" \
  "AI_REVIEW_APP_ID is required when AI_REVIEW_APP_SLUG is configured"

export EXPECTED_APP_ID=not-an-id EXPECTED_APP_SLUG=ai-review-authorization
expect_identity_error \
  "malformed App ID fails closed with a diagnostic" \
  "AI_REVIEW_APP_ID must be a positive decimal App ID"

export EXPECTED_APP_ID=4242 EXPECTED_APP_SLUG=Invalid_Slug
expect_identity_error \
  "malformed App slug fails closed with a diagnostic" \
  "AI_REVIEW_APP_SLUG must be a lowercase GitHub App slug"

export EXPECTED_APP_ID=4242 EXPECTED_APP_SLUG=ai-review-authorization
if : >"$GITHUB_OUTPUT" \
   && (cd "$tmp/run" && bash "$tmp/adoption.sh") \
   && grep -qx 'adopted=true' "$GITHUB_OUTPUT" \
   && ! grep -q '^ready=' "$GITHUB_OUTPUT"; then
  pass "valid App identity enters exact-head authorization resolution"
else
  fail "valid App identity did not enter exact-head authorization resolution"
fi

REVIEW_POLICY="$(policy ai-merge)"
export REVIEW_POLICY
write_check 'Deterministic merge policy completed. GitHub branch protection remains authoritative for human approval.'
expect_noop "human-path authorization is a successful terminal no-op"

write_check 'The model was skipped; human approval remains authoritative.'
expect_noop "skipped AI outcome cannot dispatch privileged promotion"

write_check 'The dedicated App approval could not be persisted. Deterministic policy is green.'
expect_noop "failed App approval remains on the human path"

jq '.conclusion="failure"' "$CHECK_FILE" >"$tmp/changed.json" && mv "$tmp/changed.json" "$CHECK_FILE"
expect_noop "newest unsuccessful authorization supersedes older success"

write_check $'The opted-in AI review approved this exact head.\n\n<!-- ai-review-authorized:v1:9001:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:ai-merge -->'
expect_noop "stale exact-head AI authorization marker is rejected"

write_check $'The opted-in AI review approved this exact head.\n\n<!-- ai-review-authorized:v1:9999:0123456789abcdef0123456789abcdef01234567:ai-merge -->'
expect_noop "forged authorization-check marker is rejected"

write_check $'The opted-in AI review approved this exact head.\n\n<!-- ai-review-authorized:v1:9001:0123456789abcdef0123456789abcdef01234567:ai-merge -->'
if run_case && grep -qx 'ready=true' "$GITHUB_OUTPUT" \
   && grep -qx 'pr_number=7' "$GITHUB_OUTPUT" \
   && grep -qx 'expected_head_sha=0123456789abcdef0123456789abcdef01234567' "$GITHUB_OUTPUT"; then
  pass "exact-head ai-merge App authorization proceeds to promotion"
else
  fail "exact-head ai-merge App authorization did not become promotion-ready"
fi

write_check $'The opted-in AI review approved this exact head.\n\n<!-- ai-review-authorized:v1:9001:0123456789abcdef0123456789abcdef01234567:ai-merge -->' "$HEAD_SHA" "$EXPECTED_APP_ID" "$EXPECTED_APP_SLUG"
expect_noop "legacy review-App-owned authorization cannot trigger promotion retry"

write_check $'The opted-in AI review approved this exact head.\n\n<!-- ai-review-authorized:v1:9001:0123456789abcdef0123456789abcdef01234567:ai-merge -->'
jq 'del(.external_id)' "$CHECK_FILE" >"$tmp/x" && mv "$tmp/x" "$CHECK_FILE"
expect_noop "an authorization check that carries no arm identity in external_id cannot trigger promotion retry (#1648)"

write_check $'The opted-in AI review approved this exact head.\n\n<!-- ai-review-authorized:v1:9001:0123456789abcdef0123456789abcdef01234567:ai-merge -->'
jq '.external_id |= sub(":7:"; ":8:")' "$CHECK_FILE" >"$tmp/x" && mv "$tmp/x" "$CHECK_FILE"
expect_noop "an external_id bound to another pull request cannot trigger promotion retry (#1648)"

write_check $'The opted-in AI review approved this exact head.\n\n<!-- ai-review-authorized:v1:9001:0123456789abcdef0123456789abcdef01234567:ai-merge -->'
jq '.external_id |= sub(":2:a{64}$"; ":0:" + ("a" * 64))' "$CHECK_FILE" >"$tmp/x" && mv "$tmp/x" "$CHECK_FILE"
expect_noop "an external_id with a non-positive arm attempt cannot trigger promotion retry (#1648)"

write_check $'The opted-in AI review approved this exact head.\n\n<!-- ai-review-authorized:v1:9001:0123456789abcdef0123456789abcdef01234567:ai-approve -->'
expect_noop "ai-approve marker cannot satisfy an ai-merge receipt"

REVIEW_POLICY="$(policy ai-approve)"
export REVIEW_POLICY
write_check $'The opted-in AI review approved this exact head.\n\n<!-- ai-review-authorized:v1:9001:0123456789abcdef0123456789abcdef01234567:ai-approve -->'
expect_noop "ai-approve authorization cannot dispatch privileged promotion"

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."; exit 1
