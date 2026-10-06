#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/ai-review-merge.yml"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fails=0
pass(){ printf 'ok   - %s\n' "$1"; }
fail(){ printf 'FAIL - %s\n' "$1"; fails=$((fails+1)); }

python3 - "$workflow" <<'PY'
import copy, sys, yaml

def valid(document):
    steps = document["jobs"]["complete-authorization"]["steps"]
    env = document["jobs"]["complete-authorization"]["env"]
    token = next(step for step in steps
                 if step.get("name") == "Mint dedicated authorization App token")
    complete = next(step for step in steps
                    if step.get("name") == "Complete exact head authorization")
    run = complete["run"]
    workflow_head = "current_head=\"$(GH_TOKEN=\"$ACTIONS_TOKEN\" gh api \"repos/$TARGET_REPO/pulls/$PR_NUMBER\" --jq '.head.sha // \"\"')\""
    return (
        env.get("EXPECTED_HEAD_SHA") == "${{ inputs.expected_head_sha }}"
        and env.get("EXPECTED_APP_ID") == "${{ vars.AI_REVIEW_APP_ID }}"
        and env.get("EXPECTED_REVIEWED_HEAD_SHA") == env.get("EXPECTED_HEAD_SHA")
        and env.get("EXPECTED_AUTHORIZED_HEAD_SHA") == "${{ inputs.expected_head_sha }}"
        and env.get("REVIEW_AUTHORITY") == "${{ needs.preflight.outputs.authority }}"
        and env.get("REVIEW_OUTCOME") == "${{ needs.gate.outputs.review_outcome || 'skipped' }}"
        and env.get("PREFLIGHT_STATUS") == "${{ needs.preflight.result }}"
        and env.get("PREFLIGHT_LANE") == "${{ needs.preflight.outputs.lane }}"
        and env.get("APP_CLIENT_ID") == "${{ vars.AI_REVIEW_CLIENT_ID }}"
        and token["with"].get("client-id") == "${{ vars.AI_REVIEW_CLIENT_ID }}"
        and "app-id" not in token["with"]
        and "permission-checks" not in token["with"]
        and token["with"].get("permission-contents") == "read"
        and token["with"].get("permission-pull-requests") == "write"
        and document["jobs"]["complete-authorization"]["permissions"].get("actions") == "write"
        and document["jobs"]["complete-authorization"]["permissions"].get("checks") == "write"
        and document["jobs"]["complete-authorization"]["permissions"].get("pull-requests") == "read"
        and complete["env"].get("APP_TOKEN") == "${{ steps.app-token.outputs.token }}"
        and complete["env"].get("MINTED_APP_SLUG") == "${{ steps.app-token.outputs.app-slug }}"
        and complete["env"].get("INSTALLATION_ID") == "${{ steps.app-token.outputs.installation-id }}"
        and complete["env"].get("REVERIFY_ACTOR_PERMISSION") == "false"
        and "GH_TOKEN" not in complete["env"]
        and 'GH_TOKEN="$APP_TOKEN" gh api' in run
        and 'app_api app-approval "$approval_file" --method POST' in run
        and 'GH_TOKEN="$ACTIONS_TOKEN" gh api --method PATCH "repos/$TARGET_REPO/check-runs/$AUTHORIZATION_CHECK_ID"' in run
        and "$check_token" not in run
        and "$legacy_app_id" not in run
        and "$legacy_app_slug" not in run
        and 'app_api persisted-approval "$persisted_file"' in run
        and 'approval="$(app_api' not in run
        and 'persisted="$(app_api' not in run
        and workflow_head in run
        and "workflow token REST head lookup failed" in run
        and "gh pr view" not in run
        and run.index("verify-arm-receipt.sh") < run.index("-f event=APPROVE")
        and run.index("-f event=APPROVE") < run.index("-f status=completed")
        and ('GH_TOKEN="$ACTIONS_TOKEN" bash .gate-trust/scripts/ci-gate/verify-arm-receipt.sh'
             ' || receipt_ok=false') in run
        and '[ "$GATE_STATUS" = success ] && [ "${REVIEW_AUTHORITY:-}" != ai-merge ]' in run
        and "CONSUME_AT_TERMINAL_SUCCESS" not in run
        and '[ "$receipt_ok" = true ] && [ "$GATE_STATUS" = success ]' in run
        and "AI review preflight failed before provider execution" in run
        and "AI review preflight held before provider execution" in run
        and "AI review gate did not complete after reaching the provider boundary" in run
        and "No provider reservation, submission, or review occurred" in run
        and "reviews/$approval_id" in run
        and '[ "$REVIEW_OUTCOME" = approved ]' in run
        and '[ "$REVIEW_AUTHORITY" = ai-approve ]' in run
        and '[ "$REVIEW_AUTHORITY" = ai-merge ]' in run
        and "conclusion=neutral" in run
        and run.count("conclusion=neutral") == 1
        and run.count("conclusion=success") == 1
        and "AI advisory blocking; human path ready" in run
        and "AI review inconclusive; human path ready" in run
        and "AI review skipped; human path ready" in run
        and "AI advisory non-blocking; human path ready" in run
        and "AI approval persisted for exact head" in run
        and '[ "$conclusion" != failure ] || exit 1' in run
        and 'echo "ai_authorized=$ai_authorized" >> "$GITHUB_OUTPUT"' in run
    )

with open(sys.argv[1], encoding="utf-8") as stream:
    workflow = yaml.safe_load(stream)
assert valid(workflow), "trusted completion head handoff is invalid"
missing = copy.deepcopy(workflow)
del missing["jobs"]["complete-authorization"]["env"]["EXPECTED_HEAD_SHA"]
assert not valid(missing), "missing EXPECTED_HEAD_SHA mutation escaped"
mismatch = copy.deepcopy(workflow)
mismatch["jobs"]["complete-authorization"]["env"]["EXPECTED_HEAD_SHA"] = "${{ needs.preflight.outputs.head_sha }}"
assert not valid(mismatch), "mismatched EXPECTED_HEAD_SHA mutation escaped"
reviewed_from_preflight = copy.deepcopy(workflow)
reviewed_from_preflight["jobs"]["complete-authorization"]["env"]["EXPECTED_REVIEWED_HEAD_SHA"] = "${{ needs.preflight.outputs.head_sha }}"
assert not valid(reviewed_from_preflight), "preflight-derived reviewed head mutation escaped"
consume_failed_preflight = copy.deepcopy(workflow)
complete = next(step for step in consume_failed_preflight["jobs"]["complete-authorization"]["steps"]
                if step.get("name") == "Complete exact head authorization")
complete["run"] = complete["run"].replace(
    '[ "$GATE_STATUS" = success ] && [ "${REVIEW_AUTHORITY:-}" != ai-merge ]',
    '[ "${REVIEW_AUTHORITY:-}" != ai-merge ]')
assert not valid(consume_failed_preflight), "failed-preflight receipt-consumption mutation escaped"
client_id_as_identity = copy.deepcopy(workflow)
client_id_as_identity["jobs"]["complete-authorization"]["env"]["EXPECTED_APP_ID"] = "${{ vars.AI_REVIEW_CLIENT_ID }}"
assert not valid(client_id_as_identity), "client ID substitution escaped numeric App identity contract"
no_pr_write = copy.deepcopy(workflow)
token = next(step for step in no_pr_write["jobs"]["complete-authorization"]["steps"]
             if step.get("name") == "Mint dedicated authorization App token")
del token["with"]["permission-pull-requests"]
assert not valid(no_pr_write), "missing App pull-request write permission escaped"
no_contents_read = copy.deepcopy(workflow)
token = next(step for step in no_contents_read["jobs"]["complete-authorization"]["steps"]
             if step.get("name") == "Mint dedicated authorization App token")
del token["with"]["permission-contents"]
assert not valid(no_contents_read), "missing App contents read permission escaped"
app_checks_write = copy.deepcopy(workflow)
token = next(step for step in app_checks_write["jobs"]["complete-authorization"]["steps"]
             if step.get("name") == "Mint dedicated authorization App token")
token["with"]["permission-checks"] = "write"
assert not valid(app_checks_write), "review App checks:write permission escaped retirement contract"
no_workflow_checks_write = copy.deepcopy(workflow)
del no_workflow_checks_write["jobs"]["complete-authorization"]["permissions"]["checks"]
assert not valid(no_workflow_checks_write), "missing workflow-token check read permission escaped"
reordered = copy.deepcopy(workflow)
complete = next(step for step in reordered["jobs"]["complete-authorization"]["steps"]
                if step.get("name") == "Complete exact head authorization")
complete["run"] = complete["run"].replace(
    'GH_TOKEN="$ACTIONS_TOKEN" bash .gate-trust/scripts/ci-gate/verify-arm-receipt.sh',
    'true # verifier removed') + '\nGH_TOKEN="$ACTIONS_TOKEN" bash .gate-trust/scripts/ci-gate/verify-arm-receipt.sh'
assert not valid(reordered), "approval-before-receipt-verification mutation escaped"
graphql_head = copy.deepcopy(workflow)
complete = next(step for step in graphql_head["jobs"]["complete-authorization"]["steps"]
                if step.get("name") == "Complete exact head authorization")
complete["run"] = complete["run"].replace(
    """gh api "repos/$TARGET_REPO/pulls/$PR_NUMBER" --jq '.head.sha // ""'""",
    'gh pr view "$PR_NUMBER" --repo "$TARGET_REPO" --json headRefOid --jq \'.headRefOid // ""\'')
assert not valid(graphql_head), "GraphQL head lookup mutation escaped least-privilege contract"
app_token_head = copy.deepcopy(workflow)
complete = next(step for step in app_token_head["jobs"]["complete-authorization"]["steps"]
                if step.get("name") == "Complete exact head authorization")
complete["run"] = complete["run"].replace(
    'current_head="$(GH_TOKEN="$ACTIONS_TOKEN" gh api',
    'current_head="$(gh api')
assert not valid(app_token_head), "App-token head lookup escaped token separation"
fatal_verifier = copy.deepcopy(workflow)
complete = next(step for step in fatal_verifier["jobs"]["complete-authorization"]["steps"]
                if step.get("name") == "Complete exact head authorization")
complete["run"] = complete["run"].replace(
    'bash .gate-trust/scripts/ci-gate/verify-arm-receipt.sh || receipt_ok=false',
    'bash .gate-trust/scripts/ci-gate/verify-arm-receipt.sh')
assert not valid(fatal_verifier), "aborting verifier mutation escaped; a failed receipt would wedge the check run"
unguarded_success = copy.deepcopy(workflow)
complete = next(step for step in unguarded_success["jobs"]["complete-authorization"]["steps"]
                if step.get("name") == "Complete exact head authorization")
complete["run"] = complete["run"].replace(
    '[ "$receipt_ok" = true ] && [ "$GATE_STATUS" = success ]',
    '[ "$GATE_STATUS" = success ]')
assert not valid(unguarded_success), "fail-open mutation escaped; an unverified receipt could conclude success"
permission_reverification = copy.deepcopy(workflow)
complete = next(step for step in permission_reverification["jobs"]["complete-authorization"]["steps"]
                if step.get("name") == "Complete exact head authorization")
complete["env"]["REVERIFY_ACTOR_PERMISSION"] = True
assert not valid(permission_reverification), "completion actor-permission revalidation escaped token boundary"
finalizer = next(step for step in workflow["jobs"]["complete-authorization"]["steps"]
                 if step.get("name") == "Fail authorization if completion did not run")
assert "always()" in finalizer["if"] and "steps.complete.outcome != 'success'" in finalizer["if"]
assert "AI review authorization" in finalizer["run"]
assert ".app.id == 15368" in finalizer["run"] and '.app.slug == "github-actions"' in finalizer["run"]
assert "$legacy_app_id" not in finalizer["run"] and "$legacy_app_slug" not in finalizer["run"]
assert "$parts[5] == $run" in finalizer["run"] and "$parts[6] == $attempt" in finalizer["run"]
assert "APP_TOKEN" not in finalizer["env"] and "MINTED_APP_SLUG" not in finalizer["env"]
assert "EXPECTED_APP_ID" not in finalizer["env"] and "EXPECTED_APP_SLUG" not in finalizer["env"]
assert 'GH_TOKEN="$ACTIONS_TOKEN" gh api --method PATCH' in finalizer["run"]
assert "conclusion=failure" in finalizer["run"] and "conclusion=success" not in finalizer["run"]
PY
[ "$?" -eq 0 ] || exit 1

awk '$0=="      - name: Complete exact head authorization"{f=1;next} f&&$0=="        run: |"{r=1;next} r{if($0~/^      - name:/ || $0~/^  [A-Za-z0-9_-]+:/)exit;sub(/^          /,"");print}' \
  "$workflow" >"$tmp/complete.sh"
awk '$0=="      - name: Fail authorization if completion did not run"{f=1;next} f&&$0=="        run: |"{r=1;next} r{if($0~/^      - name:/ || $0~/^  [A-Za-z0-9_-]+:/)exit;sub(/^          /,"");print}' \
  "$workflow" >"$tmp/finalize.sh"
[ -s "$tmp/complete.sh" ] || { echo "FAIL - completion block missing"; exit 1; }

mkdir -p "$tmp/run/.gate-trust/scripts/ci-gate" "$tmp/bin"
cat >"$tmp/run/.gate-trust/scripts/ci-gate/verify-arm-receipt.sh" <<'SH'
#!/usr/bin/env bash
[ "$EXPECTED_HEAD_SHA" = "$EXPECTED_AUTHORIZED_HEAD_SHA" ] &&
  [ "$EXPECTED_HEAD_SHA" = "$EXPECTED_REVIEWED_HEAD_SHA" ]
SH
chmod 0644 "$tmp/run/.gate-trust/scripts/ci-gate/verify-arm-receipt.sh"
cat >"$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
printf 'token=%s %s\n' "${GH_TOKEN:-}" "$*" >>"$CALLS"
case "$*" in
  "api repos/Verjson/example/check-runs/9001")
    jq -nc --argjson id "$AUTHORIZATION_CHECK_ID" --arg head "${CHECK_RUN_HEAD:-$EXPECTED_AUTHORIZED_HEAD_SHA}" \
            --arg external_head "${CHECK_EXTERNAL_HEAD:-$EXPECTED_AUTHORIZED_HEAD_SHA}" \
            --arg repo "$TARGET_REPO" --arg pr "$PR_NUMBER" --arg run "$ARM_RUN_ID" --arg attempt "$ARM_RUN_ATTEMPT" \
            --arg url "${FORGED_DETAILS_URL:-$GITHUB_SERVER_URL/$TARGET_REPO/runs/$AUTHORIZATION_CHECK_ID}" \
            --argjson check_app_id "${CHECK_APP_ID:-15368}" --arg check_app_slug "${CHECK_APP_SLUG:-github-actions}" \
            '{id:$id,name:"AI review authorization",head_sha:$head,
             external_id:("ai-review:v1:"+$repo+":"+$pr+":"+$external_head+":"+$run+":"+$attempt+":"+("a"*64)),
             details_url:$url,status:"in_progress",conclusion:null,app:{id:$check_app_id,slug:$check_app_slug}}' ;;
  "api --method POST "*)
    if [ "${APPROVAL_RC:-0}" -ne 0 ]; then
      printf 'authorization: token leaked-test-token\nx-github-request-id: TEST:1234\n' >&2
      exit "$APPROVAL_RC"
    fi
    jq -nc --arg head "$EXPECTED_AUTHORIZED_HEAD_SHA" \
      --arg login "${EXPECTED_APP_SLUG}[bot]" --arg check "$AUTHORIZATION_CHECK_ID" \
      '{id:81,state:"APPROVED",commit_id:$head,user:{login:$login},body:("<!-- ai-review-authorization:"+$check+" -->")}' ;;
  "api repos/"*"/reviews/81")
    jq -nc --arg head "${PERSISTED_HEAD:-$EXPECTED_AUTHORIZED_HEAD_SHA}" \
      --arg login "${EXPECTED_APP_SLUG}[bot]" --arg check "$AUTHORIZATION_CHECK_ID" \
      '{id:81,state:"APPROVED",commit_id:$head,user:{login:$login},body:("<!-- ai-review-authorization:"+$check+" -->")}' ;;
  "api repos/"*"/pulls/"*)
    [ "${HEAD_RC:-0}" -eq 0 ] || exit "$HEAD_RC"
    printf '%s\n' "$EXPECTED_AUTHORIZED_HEAD_SHA" ;;
  "api --method PATCH "*) exit 0 ;;
  *) exit 2 ;;
esac
SH
chmod +x "$tmp/bin/gh"

export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" TARGET_REPO=Verjson/example PR_NUMBER=7
export AUTHORIZATION_CHECK_ID=9001 ARM_RUN_ID=7001 ARM_RUN_ATTEMPT=2
export EXPECTED_APP_ID=4242 EXPECTED_APP_SLUG=verjson-ai-review GITHUB_SERVER_URL=https://github.com
export EXPECTED_AUTHORIZED_HEAD_SHA=0123456789abcdef0123456789abcdef01234567
export EXPECTED_REVIEWED_HEAD_SHA="$EXPECTED_AUTHORIZED_HEAD_SHA" EXPECTED_HEAD_SHA="$EXPECTED_AUTHORIZED_HEAD_SHA"
export GATE_STATUS=success ACTIONS_TOKEN=actions-token APP_TOKEN=app-token APP_KEY_POLICY_RESULT=success
export MINTED_APP_SLUG="$EXPECTED_APP_SLUG" INSTALLATION_ID=1234 RUNNER_TEMP="$tmp"
export REVIEW_AUTHORITY=ai-merge REVIEW_OUTCOME=approved GITHUB_OUTPUT="$tmp/github-output"
export PREFLIGHT_STATUS=success PREFLIGHT_LANE=ai

run_complete(){ (cd "$tmp/run" && bash "$tmp/complete.sh"); }
run_finalizer(){ (cd "$tmp/run" && env "$@" bash "$tmp/finalize.sh"); }
if (cd "$tmp/run" && .gate-trust/scripts/ci-gate/verify-arm-receipt.sh) >"$tmp/out" 2>&1; then
  fail "non-executable completion verifier unexpectedly supports direct execution"
else
  pass "non-executable completion verifier rejects direct execution"
fi
: >"$CALLS"
: >"$GITHUB_OUTPUT"
if run_complete >"$tmp/out" 2>&1 && grep -q 'conclusion=success' "$CALLS" \
   && grep -Fq 'output[title]=AI approval persisted for exact head' "$CALLS" \
   && grep -Fq 'output[summary]=The required AI review approved this exact head.' "$CALLS" \
   && grep -q 'token=actions-token api --method PATCH' "$CALLS" \
   && grep -q "ai-review-authorized:v1:${AUTHORIZATION_CHECK_ID}:${EXPECTED_AUTHORIZED_HEAD_SHA}:ai-merge" "$CALLS"; then
  pass "trusted preflight head receives persisted App approval before authorization completion"
else fail "valid completion head handoff failed: $(tail -1 "$tmp/out")"; fi


: >"$CALLS"; : >"$GITHUB_OUTPUT"
if ! CHECK_APP_ID=4242 CHECK_APP_SLUG=verjson-ai-review run_complete >"$tmp/out" 2>&1 \
  && ! grep -q 'api --method PATCH' "$CALLS" \
  && ! grep -q 'api --method POST' "$CALLS" \
  && ! grep -q 'ai_authorized=true' "$GITHUB_OUTPUT"; then
  pass "legacy review-App-owned authorization check is rejected before completion"
else
  fail "legacy review-App-owned authorization check reached a mutation path"
fi

# #1648: details_url is an anti-forgery binding to the check's own page, not the
# arm-run URL GitHub no longer stores there. Neither shape may substitute for it.
for forged_url in "https://github.com/Verjson/example/actions/runs/$ARM_RUN_ID" \
                   "https://github.com/Verjson/example/runs/9002"; do
  : >"$CALLS"; : >"$GITHUB_OUTPUT"
  if ! FORGED_DETAILS_URL="$forged_url" run_complete >"$tmp/out" 2>&1 \
    && ! grep -q 'api --method PATCH' "$CALLS" \
    && ! grep -q 'api --method POST' "$CALLS" \
    && ! grep -q 'ai_authorized=true' "$GITHUB_OUTPUT"; then
    pass "a check whose details_url is not its own stored page is rejected before completion ($forged_url)"
  else
    fail "a check with a forged details_url ($forged_url) reached a mutation path"
  fi
done

: >"$CALLS"; : >"$GITHUB_OUTPUT"; APPROVAL_RC=1 run_complete >"$tmp/out" 2>&1
if [ "$?" -eq 0 ] && grep -q 'conclusion=neutral' "$CALLS" \
   && grep -q 'output\[title\]=AI approval unavailable; human path ready' "$CALLS" \
   && grep -Fq 'output[summary]=No exact-head App approval was persisted and verified.' "$CALLS" \
   && grep -q 'ai_authorized=false' "$GITHUB_OUTPUT" \
   && ! grep -q 'ai-review-authorized:v1:' "$CALLS" \
  && grep -q 'app-approval failed' "$tmp/out" \
  && grep -q 'x-github-request-id: TEST:1234' "$tmp/out" \
  && grep -q 'authorization: token \*\*\*' "$tmp/out" \
  && ! grep -q 'leaked-test-token' "$tmp/out"; then
  pass "rejected App approval falls back to human authorization without leaking credentials"
else fail "rejected App approval did not preserve the human path"; fi

: >"$CALLS"; : >"$GITHUB_OUTPUT"; PERSISTED_HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa run_complete >"$tmp/out" 2>&1
if [ "$?" -eq 0 ] && grep -q 'conclusion=neutral' "$CALLS" \
   && grep -Fq 'output[title]=AI approval unavailable; human path ready' "$CALLS" \
   && grep -Fq 'output[summary]=No exact-head App approval was persisted and verified.' "$CALLS" \
   && grep -q 'ai_authorized=false' "$GITHUB_OUTPUT" \
   && ! grep -q 'ai-review-authorized:v1:' "$CALLS"; then
  pass "stale App approval cannot authorize AI but leaves the human path ready"
else fail "stale persisted approval escaped into AI authority"; fi

: >"$CALLS"; : >"$GITHUB_OUTPUT"; REVIEW_AUTHORITY=ai-approve run_complete >"$tmp/out" 2>&1
if [ "$?" -eq 0 ] && grep -q 'ai_authorized=true' "$GITHUB_OUTPUT" \
   && grep -q "ai-review-authorized:v1:${AUTHORIZATION_CHECK_ID}:${EXPECTED_AUTHORIZED_HEAD_SHA}:ai-approve" "$CALLS"; then
  pass "ai-approve persistence records non-merging authority in the exact-head marker"
else fail "ai-approve marker did not preserve its non-merging authority"; fi

: >"$CALLS"; : >"$GITHUB_OUTPUT"; REVIEW_AUTHORITY=human REVIEW_OUTCOME=skipped run_complete >"$tmp/out" 2>&1
if [ "$?" -eq 0 ] && grep -q 'conclusion=neutral' "$CALLS" \
   && grep -q 'output\[title\]=AI review skipped; human path ready' "$CALLS" \
   && grep -Fq 'output[summary]=AI review was not required or requested.' "$CALLS" \
   && ! grep -q 'api --method POST' "$CALLS" \
   && grep -q 'ai_authorized=false' "$GITHUB_OUTPUT" && ! grep -q 'ai-review-authorized:v1:' "$CALLS"; then
  pass "human authority never asks the App to approve"
else fail "human authority still depends on AI approval"; fi

: >"$CALLS"; : >"$GITHUB_OUTPUT"; REVIEW_AUTHORITY=ai-merge REVIEW_OUTCOME=superseded run_complete >"$tmp/out" 2>&1
if [ "$?" -eq 0 ] \
   && grep -Fq 'conclusion=neutral' "$CALLS" \
   && ! grep -Fq 'conclusion=success' "$CALLS" \
   && ! grep -Fq 'conclusion=failure' "$CALLS" \
   && grep -Fq 'output[title]=AI review superseded; human path ready' "$CALLS" \
   && grep -Fq 'output[summary]=The reviewed head was superseded before publication. This result grants no AI authority; GitHub branch protection remains authoritative for the current head.' "$CALLS" \
   && ! grep -q 'api --method POST' "$CALLS" \
   && grep -Fxq 'ai_authorized=false' "$GITHUB_OUTPUT" \
   && ! grep -Fq 'ai_authorized=true' "$GITHUB_OUTPUT" \
   && ! grep -Fq 'ai-review-authorized:v1:' "$CALLS"; then
  pass "superseded AI review is a neutral human fallback without AI authority"
else fail "superseded AI review escaped its neutral human-fallback contract"; fi

for unknown_outcome in future-outcome vendor_timeout_v2 42; do
  : >"$CALLS"; : >"$GITHUB_OUTPUT"; REVIEW_AUTHORITY=ai-merge REVIEW_OUTCOME="$unknown_outcome" run_complete >"$tmp/out" 2>&1
  if [ "$?" -eq 0 ] \
   && grep -Fq 'conclusion=neutral' "$CALLS" \
   && ! grep -Fq 'conclusion=success' "$CALLS" \
   && ! grep -Fq 'conclusion=failure' "$CALLS" \
   && grep -Fq 'output[title]=AI review unavailable; human path ready' "$CALLS" \
   && grep -Fq 'output[summary]=AI did not authorize this exact head. Deterministic policy completed and GitHub branch protection remains authoritative for human approval.' "$CALLS" \
   && ! grep -q 'api --method POST' "$CALLS" \
   && grep -Fxq 'ai_authorized=false' "$GITHUB_OUTPUT" \
   && ! grep -Fq 'ai_authorized=true' "$GITHUB_OUTPUT" \
   && ! grep -Fq 'ai-review-authorized:v1:' "$CALLS"; then
    pass "unknown AI review outcome $unknown_outcome is a neutral human fallback without AI authority"
  else fail "unknown AI review outcome $unknown_outcome escaped the wildcard human-fallback contract"; fi
done

: >"$CALLS"; : >"$GITHUB_OUTPUT"; REVIEW_AUTHORITY=ai-merge REVIEW_OUTCOME=blocking run_complete >"$tmp/out" 2>&1
if [ "$?" -eq 0 ] && grep -q 'conclusion=neutral' "$CALLS" \
   && grep -q 'output\[title\]=AI advisory blocking; human path ready' "$CALLS" \
   && grep -Fq 'output[summary]=AI reported blocking findings.' "$CALLS" \
   && ! grep -q 'api --method POST' "$CALLS" \
   && grep -q 'ai_authorized=false' "$GITHUB_OUTPUT"; then
  pass "blocking advisory is visibly neutral and leaves the human path ready"
else fail "blocking advisory was green or vetoed the human path"; fi

: >"$CALLS"; : >"$GITHUB_OUTPUT"; REVIEW_AUTHORITY=ai-merge REVIEW_OUTCOME=inconclusive run_complete >"$tmp/out" 2>&1
if [ "$?" -eq 0 ] && grep -q 'conclusion=neutral' "$CALLS" \
   && grep -q 'output\[title\]=AI review inconclusive; human path ready' "$CALLS" \
   && grep -Fq 'output[summary]=AI did not produce a reusable verdict.' "$CALLS" \
   && ! grep -q 'api --method POST' "$CALLS" \
   && grep -q 'ai_authorized=false' "$GITHUB_OUTPUT"; then
  pass "inconclusive AI review is visibly neutral and leaves the human path ready"
else fail "inconclusive AI review was green or vetoed the human path"; fi

: >"$CALLS"; : >"$GITHUB_OUTPUT"; REVIEW_AUTHORITY=human REVIEW_OUTCOME=approved run_complete >"$tmp/out" 2>&1
if [ "$?" -eq 0 ] && grep -q 'conclusion=neutral' "$CALLS" \
   && grep -q 'output\[title\]=AI advisory non-blocking; human path ready' "$CALLS" \
   && grep -Fq 'output[summary]=AI returned a non-blocking verdict, but no App approval has been persisted.' "$CALLS" \
   && ! grep -q 'api --method POST' "$CALLS" \
   && grep -q 'ai_authorized=false' "$GITHUB_OUTPUT"; then
  pass "non-authorizing approval verdict stays neutral on the human path"
else fail "non-authorizing approval verdict appeared as App approval"; fi

: >"$CALLS"; : >"$GITHUB_OUTPUT"
PREFLIGHT_STATUS=failure PREFLIGHT_LANE='' GATE_STATUS=skipped REVIEW_AUTHORITY='' REVIEW_OUTCOME=skipped \
  run_complete >"$tmp/out" 2>&1
if [ "$?" -ne 0 ] \
   && grep -Fq 'output[title]=AI review preflight failed before provider execution' "$CALLS" \
   && grep -Fq 'No provider reservation, submission, or review occurred' "$CALLS" \
   && grep -Fq 'conclusion=failure' "$CALLS" \
   && ! grep -Fq 'api --method POST' "$CALLS"; then
  pass "failed preflight reports causal zero-provider state without approval"
else fail "failed preflight lost its causal zero-provider state"; fi

: >"$CALLS"; : >"$GITHUB_OUTPUT"
PREFLIGHT_STATUS=success PREFLIGHT_LANE=held GATE_STATUS=skipped REVIEW_AUTHORITY='' REVIEW_OUTCOME=skipped \
  run_complete >"$tmp/out" 2>&1
if [ "$?" -ne 0 ] \
   && grep -Fq 'output[title]=AI review preflight held before provider execution' "$CALLS" \
   && grep -Fq 'No provider reservation, submission, or review occurred' "$CALLS" \
   && grep -Fq 'conclusion=failure' "$CALLS" \
   && ! grep -Fq 'api --method POST' "$CALLS"; then
  pass "held preflight reports causal zero-provider state without approval"
else fail "held preflight lost its causal zero-provider state"; fi

for gate_status in failure cancelled; do
  : >"$CALLS"; : >"$GITHUB_OUTPUT"
  PREFLIGHT_STATUS=success PREFLIGHT_LANE=ai GATE_STATUS="$gate_status" REVIEW_AUTHORITY=human REVIEW_OUTCOME=skipped \
    run_complete >"$tmp/out" 2>&1
  if [ "$?" -ne 0 ] \
     && grep -Fq 'output[title]=AI review gate did not complete after reaching the provider boundary' "$CALLS" \
     && grep -Fq 'after reaching or ambiguously approaching the provider boundary' "$CALLS" \
     && grep -Fq 'the retained receipt is ineligible for zero-provider recovery' "$CALLS" \
     && grep -Fq 'conclusion=failure' "$CALLS" \
     && ! grep -Fq 'api --method POST' "$CALLS"; then
    pass "$gate_status provider gate reports that zero-provider recovery is unavailable"
  else fail "$gate_status provider gate reported an ambiguous recovery state"; fi
done

# A failed receipt must still complete the check run. The job carries `if: always()`
# so it can report that failure; aborting before the PATCH leaves the check run
# `in_progress` forever, which blocks the PR with no signal and no rerun path.
# The approval POST must still never happen — that is the authorization mutation.
: >"$CALLS"; : >"$GITHUB_OUTPUT"; EXPECTED_HEAD_SHA='' run_complete >"$tmp/out" 2>&1
run_finalizer >"$tmp/out" 2>&1
if [ "$?" -eq 0 ] \
   && grep -q 'api --method PATCH' "$CALLS" && grep -q 'conclusion=failure' "$CALLS" \
   && ! grep -q 'api --method POST' "$CALLS"; then
  pass "missing completion head fails its exact Actions check without requesting approval"
else
  fail "missing completion head left the authorization check unresolved"
fi

: >"$CALLS"; : >"$GITHUB_OUTPUT"; EXPECTED_HEAD_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa run_complete >"$tmp/out" 2>&1
run_finalizer >"$tmp/out" 2>&1
if [ "$?" -eq 0 ] \
   && grep -q 'api --method PATCH' "$CALLS" && grep -q 'conclusion=failure' "$CALLS" \
   && ! grep -q 'api --method POST' "$CALLS"; then
  pass "mismatched completion head fails its exact Actions check without requesting approval"
else
  fail "mismatched completion head left the authorization check unresolved"
fi


# The fallback must reject a legacy review-App-owned check without attempting
# any mutation through either token.
for forged_url in "https://github.com/Verjson/example/actions/runs/$ARM_RUN_ID" \
                   "https://github.com/Verjson/example/runs/9002"; do
  : >"$CALLS"
  if ! FORGED_DETAILS_URL="$forged_url" run_finalizer >"$tmp/out" 2>&1 \
    && ! grep -q 'api --method PATCH' "$CALLS" \
    && ! grep -q 'api --method POST' "$CALLS"; then
    pass "a check with a forged details_url ($forged_url) is outside the finalizer contract"
  else
    fail "a forged details_url ($forged_url) reached the finalizer's mutation path"
  fi
done

: >"$CALLS"
if ! CHECK_EXTERNAL_HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa run_finalizer >"$tmp/out" 2>&1 \
  && grep -Fq 'refusing to modify a check outside this exact arm run' "$tmp/out" \
  && ! grep -q 'api --method PATCH' "$CALLS" \
  && ! grep -q 'api --method POST' "$CALLS"; then
  pass "another head in the authorization identity cannot reach finalizer mutation"
else
  fail "another head in the authorization identity reached finalizer mutation"
fi

: >"$CALLS"
if ! CHECK_RUN_HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa run_finalizer >"$tmp/out" 2>&1 \
  && grep -Fq 'refusing to modify a check outside this exact arm run' "$tmp/out" \
  && ! grep -q 'api --method PATCH' "$CALLS" \
  && ! grep -q 'api --method POST' "$CALLS"; then
  pass "a check run for another head cannot reach finalizer mutation"
else
  fail "a check run for another head reached finalizer mutation"
fi

: >"$CALLS"
if ! CHECK_APP_ID=4242 CHECK_APP_SLUG=verjson-ai-review run_finalizer >"$tmp/out" 2>&1 \
  && ! grep -q 'api --method PATCH' "$CALLS" \
  && ! grep -q 'api --method POST' "$CALLS"; then
  pass "legacy review-App-owned authorization check is outside the finalizer contract"
else
  fail "legacy review-App-owned authorization check reached a mutation path"
fi

: >"$CALLS"
if run_finalizer APP_KEY_POLICY_RESULT=failure AI_REVIEW_ENVIRONMENT=ai-review-app >"$tmp/out" 2>&1 \
  && grep -Fq 'AI_REVIEW_APP_PRIVATE_KEY validation or review environment admission failed for ai-review-app' "$CALLS"; then
  pass "failed key admission reports its environment and secret on the authorization check"
else
  fail "failed key admission did not produce an actionable authorization check"
fi

: >"$CALLS"; : >"$GITHUB_OUTPUT"; HEAD_RC=1 run_complete >"$tmp/out" 2>&1
if [ "$?" -ne 0 ] && grep -q 'api --method PATCH' "$CALLS" \
  && grep -q 'conclusion=failure' "$CALLS" \
  && ! grep -q 'api --method POST' "$CALLS" \
  && grep -q 'workflow token REST head lookup failed' "$tmp/out"; then
  pass "failed head lookup completes the check run as failure without requesting approval"
else fail "failed head lookup left the authorization check unresolved"; fi

# Fail-closed direction: a verifier failure must never reach conclusion=success,
# even when every other precondition is green.
: >"$CALLS"; : >"$GITHUB_OUTPUT"; EXPECTED_HEAD_SHA='' GATE_STATUS=success run_complete >"$tmp/out" 2>&1
if ! grep -q 'conclusion=success' "$CALLS"; then
  pass "unverified receipt cannot conclude success while the gate is green"
else fail "unverified receipt concluded success — fail-open"; fi

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."; exit 1
