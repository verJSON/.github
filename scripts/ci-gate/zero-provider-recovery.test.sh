#!/usr/bin/env bash
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
verifier="$here/verify-zero-provider-recovery.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

if python3 - "$here/../../.github/workflows/ai-review-merge.yml" <<'PY'
import copy
import sys
import yaml

def valid(document):
    preflight = document["jobs"]["preflight"]
    complete = document["jobs"]["complete-authorization"]
    steps = preflight["steps"]
    checkout = next(step for step in steps if step.get("name") == "Check out immutable zero-provider recovery verifier")
    recovery = next(step for step in steps if step.get("name") == "Verify receipt-bound direct review admission")
    freshness = next(step for step in steps if step.get("name") == "Update branch if behind; hold on conflict")
    return (
        preflight["permissions"].get("actions") == "read"
        and preflight["permissions"].get("checks") == "read"
        and preflight["outputs"].get("head_sha") == "${{ steps.classify.outputs.head_sha || inputs.expected_head_sha }}"
        and preflight["outputs"].get("zero_provider_recovery") == "${{ steps.zero-provider-recovery.outputs.eligible || 'false' }}"
        and checkout["if"] == "github.event_name == 'workflow_dispatch'"
        and checkout["with"].get("ref") == "${{ steps.trusted-revision.outputs.sha }}"
        and checkout["with"].get("persist-credentials") is False
        and "verify-zero-provider-recovery.sh" in checkout["with"].get("sparse-checkout", "")
        and recovery["if"] == checkout["if"]
        and recovery["env"].get("GH_TOKEN") == "${{ github.token }}"
        and recovery["env"].get("EXPECTED_HEAD_SHA") == "${{ inputs.expected_head_sha }}"
        and "bash .gate-recovery/scripts/ci-gate/verify-zero-provider-recovery.sh" in recovery["run"]
        and 'if [ "$REVIEW_RUN_ATTEMPT" -gt 1 ]; then' in recovery["run"]
        and "eligible=true" in recovery["run"]
        and "eligible=false" in recovery["run"]
        and freshness["env"].get("ZERO_PROVIDER_RECOVERY") == "${{ steps.zero-provider-recovery.outputs.eligible || 'false' }}"
        and '[ "${ZERO_PROVIDER_RECOVERY:-false}" != true ]; then' in freshness["run"]
        and complete["env"].get("EXPECTED_HEAD_SHA") == "${{ inputs.expected_head_sha }}"
        and complete["env"].get("EXPECTED_REVIEWED_HEAD_SHA") == "${{ inputs.expected_head_sha }}"
    )

with open(sys.argv[1], encoding="utf-8") as stream:
    workflow = yaml.safe_load(stream)
assert valid(workflow), "zero-provider workflow integration is invalid"

mutations = []
changed = copy.deepcopy(workflow)
del changed["jobs"]["preflight"]["permissions"]["checks"]
mutations.append(changed)
changed = copy.deepcopy(workflow)
recovery = next(
    step for step in changed["jobs"]["preflight"]["steps"]
    if step.get("name") == "Verify receipt-bound direct review admission"
)
del recovery["env"]["EXPECTED_HEAD_SHA"]
mutations.append(changed)
changed = copy.deepcopy(workflow)
checkout = next(step for step in changed["jobs"]["preflight"]["steps"] if step.get("name") == "Check out immutable zero-provider recovery verifier")
checkout["with"]["ref"] = "main"
mutations.append(changed)
changed = copy.deepcopy(workflow)
checkout = next(step for step in changed["jobs"]["preflight"]["steps"] if step.get("name") == "Check out immutable zero-provider recovery verifier")
checkout["if"] = "inputs.explicit_rereview == true && github.run_attempt != 1"
mutations.append(changed)
changed = copy.deepcopy(workflow)
recovery = next(step for step in changed["jobs"]["preflight"]["steps"] if step.get("name") == "Verify receipt-bound direct review admission")
recovery["run"] = "echo 'eligible=true' >>\"$GITHUB_OUTPUT\""
mutations.append(changed)
changed = copy.deepcopy(workflow)
freshness = next(step for step in changed["jobs"]["preflight"]["steps"] if step.get("name") == "Update branch if behind; hold on conflict")
freshness["run"] = freshness["run"].replace('[ "${ZERO_PROVIDER_RECOVERY:-false}" != true ]; then', 'false; then')
mutations.append(changed)
assert all(not valid(mutation) for mutation in mutations), "zero-provider workflow mutation escaped"
PY
then
  pass "trusted workflow admits only receipt-verified same-run recovery"
else
  fail "trusted workflow recovery integration is invalid"
fi

export TARGET_REPO=Verjson/example PR_NUMBER=7
export EXPECTED_HEAD_SHA=0123456789abcdef0123456789abcdef01234567
export AUTHORIZATION_CHECK_ID=9001 ARM_RUN_ID=7001 ARM_RUN_ATTEMPT=1
export EXPECTED_APP_ID=4528902 EXPECTED_APP_SLUG=ai-review-authorization
export REVIEW_POLICY=ZXhhY3QtcG9saWN5
export EXPECTED_POLICY="$REVIEW_POLICY"
export REVIEW_RUN_ID=8001 REVIEW_RUN_ATTEMPT=2 DEFAULT_BRANCH=main
export GITHUB_SERVER_URL=https://github.com RUNNER_TEMP="$tmp"

mkdir "$tmp/bin"
cat >"$tmp/receipt-verifier" <<'SH'
#!/usr/bin/env bash
[ "$REVIEW_POLICY" = "$EXPECTED_POLICY" ] &&
  [ "$AUTHORIZATION_CHECK_ID" = 9001 ] &&
  [ "$EXPECTED_APP_ID" = 4528902 ] &&
  [ "$EXPECTED_APP_SLUG" = ai-review-authorization ]
SH
chmod +x "$tmp/receipt-verifier"
export ZERO_PROVIDER_RECEIPT_VERIFIER="$tmp/receipt-verifier"

cat >"$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
if [[ "${TARGET_REPO,,}" == "verjson/.github" ]]; then
  dispatch_workflow="ai-review-merge-dispatch.yml"
else
  dispatch_workflow="ai-review-merge.yml"
fi
dispatch_workflow_path=".github/workflows/$dispatch_workflow"
[ -z "${API_CAPTURE:-}" ] || printf "%s\n" "$*" >>"$API_CAPTURE"
case "$*" in
  "api repos/$TARGET_REPO/actions/runs/$REVIEW_RUN_ID")
    jq -nc --argjson id "$REVIEW_RUN_ID" --argjson attempt "$REVIEW_RUN_ATTEMPT" \
      --arg title "${RUN_TITLE:-AI review authorization $AUTHORIZATION_CHECK_ID from arm $ARM_RUN_ID.$ARM_RUN_ATTEMPT}" \
      --arg repo "${RUN_REPOSITORY:-verJSON/example}" --arg branch "$DEFAULT_BRANCH" \
      --arg actor "${RUN_ACTOR:-github-actions[bot]}" \
      --arg status "${RUN_STATUS:-in_progress}" --arg workflow_path "$dispatch_workflow_path" \
      '{id:$id,run_attempt:$attempt,event:"workflow_dispatch",path:$workflow_path,
        display_title:$title,head_branch:$branch,head_repository:{full_name:$repo},repository:{full_name:$repo},
        actor:{login:$actor},triggering_actor:{login:$actor},status:$status}' ;;
  "api --paginate --slurp repos/$TARGET_REPO/actions/workflows/$dispatch_workflow/runs?event=workflow_dispatch&per_page=100")
    # Verjson/.github#1480 — count the polls so a test can prove which
    # conditions are retried and which are refused on the first look.
    polls=1
    if [ -n "${CORRELATED_POLLS:-}" ]; then
      polls=$(( $(cat "$CORRELATED_POLLS" 2>/dev/null || echo 0) + 1 ))
      printf '%s' "$polls" >"$CORRELATED_POLLS"
    fi
    # The run is absent until the APPEARS_AFTER-th poll: eventual consistency on
    # GET /actions/runs, which is the race this issue is about. MISSING keeps it
    # absent forever.
    if [ "${MISSING_CORRELATED_RUN:-false}" = true ] ||
       [ "$polls" -le "${CORRELATED_RUN_APPEARS_AFTER:-0}" ]; then
      printf '[{"workflow_runs":[]}]\n'; exit 0
    fi
    jq -nc --argjson id "$REVIEW_RUN_ID" \
      --arg title "${RUN_TITLE:-AI review authorization $AUTHORIZATION_CHECK_ID from arm $ARM_RUN_ID.$ARM_RUN_ATTEMPT}" \
      --arg repo "${RUN_REPOSITORY:-verJSON/example}" --arg branch "$DEFAULT_BRANCH" \
      --argjson attempt "${CORRELATED_RUN_ATTEMPT:-1}" \
      --argjson duplicate "${DUPLICATE_CORRELATED_RUN:-false}" \
      --arg workflow_path "$dispatch_workflow_path" '
      [{workflow_runs: ([{
        id:$id,run_attempt:$attempt,event:"workflow_dispatch",path:$workflow_path,
        display_title:$title,head_branch:$branch,head_repository:{full_name:$repo},repository:{full_name:$repo}
      }] + (if $duplicate then [{
        id:($id + 1),run_attempt:1,event:"workflow_dispatch",path:$workflow_path,
        display_title:$title,head_branch:$branch,head_repository:{full_name:$repo},repository:{full_name:$repo}
      }] else [] end))}]' ;;
  "api --paginate --slurp repos/$TARGET_REPO/actions/runs/$REVIEW_RUN_ID/jobs?filter=all&per_page=100")
    pre="${PREFLIGHT_CONCLUSION:-failure}"; gate="${GATE_CONCLUSION:-skipped}"
    gate_steps='[]'; [ "$gate" = skipped ] || gate_steps='[{"name":"Reserve AI review pass 1","status":"completed","conclusion":"success"}]'
    prefix="${JOB_NAME_PREFIX:-review / }"
    jq -nc --arg pre "$pre" --arg gate "$gate" --arg prefix "$prefix" --argjson gate_steps "$gate_steps" \
      --argjson attempt "$REVIEW_RUN_ATTEMPT" '
      [{jobs:[range(1; $attempt) as $n |
        {name:($prefix + "preflight"),run_attempt:$n,status:"completed",conclusion:$pre,steps:[]},
        {name:($prefix + "gate"),run_attempt:$n,status:"completed",conclusion:$gate,steps:$gate_steps},
        {name:($prefix + "complete-authorization"),run_attempt:$n,status:"completed",conclusion:"failure",steps:[]},
        {name:($prefix + "dispatch-merge"),run_attempt:$n,status:"completed",conclusion:"skipped",steps:[]}
      ]}]' ;;
  "api repos/$TARGET_REPO/pulls/$PR_NUMBER")
    jq -nc --arg head "${CURRENT_HEAD:-$EXPECTED_HEAD_SHA}" '{state:"open",head:{sha:$head}}' ;;
  "api repos/$TARGET_REPO/check-runs/$AUTHORIZATION_CHECK_ID")
    jq -nc --argjson id "${RETURNED_CHECK_ID:-$AUTHORIZATION_CHECK_ID}" --arg head "$EXPECTED_HEAD_SHA" \
      --argjson app "${RETURNED_APP_ID:-15368}" --arg slug "${RETURNED_APP_SLUG:-github-actions}" \
      --argjson attempt "$REVIEW_RUN_ATTEMPT" \
      '{id:$id,name:"AI review authorization",head_sha:$head,app:{id:$app,slug:$slug}} +
       (if $attempt == 1 and env.AUTHORIZATION_STATE != "retained" then {status:"in_progress",conclusion:null}
        else {status:"completed",conclusion:"failure"} end)' ;;
  "api --paginate --slurp repos/$TARGET_REPO/pulls/$PR_NUMBER/reviews?per_page=100")
    if [ "${PROVIDER_REVIEW:-false}" = true ]; then
      jq -nc --arg head "$EXPECTED_HEAD_SHA" --arg login "${EXPECTED_APP_SLUG}[bot]" \
        '[[{commit_id:$head,state:"COMMENTED",user:{login:$login},body:"reserved"}]]'
    else printf '[[]]\n'; fi ;;
  *) echo "unexpected gh call: $*" >&2; exit 2 ;;
esac
SH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH"

expect_pass() {
  local label="$1"; shift
  if "$@" >"$tmp/out" 2>&1; then pass "$label"; else fail "$label: $(tail -1 "$tmp/out")"; fi
}
expect_fail() {
  local label="$1" reason="$2"; shift 2
  if "$@" >"$tmp/out" 2>&1; then fail "$label"
  elif grep -qF "$reason" "$tmp/out"; then pass "$label"
  else fail "$label: $(tail -1 "$tmp/out")"; fi
}
verify() { bash "$verifier"; }

expect_pass "failed preflight with skipped gate is eligible for same-receipt recovery" verify
RUN_REPOSITORY=$'v\u0435rJSON/example' \
  expect_fail "Unicode lookalike repository cannot correlate recovery" "run identity mismatch" verify
RUN_STATUS=queued expect_pass "queued direct review run remains eligible while a sibling waits" verify
RUN_STATUS=completed expect_fail "terminal review run cannot be replayed as live" "run identity mismatch" verify
PREFLIGHT_CONCLUSION=success expect_pass "held preflight with skipped gate is eligible for same-receipt recovery" verify
PREFLIGHT_CONCLUSION=skipped expect_pass "skipped preflight is eligible for same-receipt recovery" verify
REVIEW_RUN_ATTEMPT=10 expect_pass "later same-run retry remains eligible with complete prior evidence" verify
RUN_TITLE='AI review' \
  expect_fail "generic run name cannot correlate recovery" "run identity mismatch" verify
RUN_TITLE="AI review authorization 9002 from arm $ARM_RUN_ID.$ARM_RUN_ATTEMPT" \
  expect_fail "substituted authorization check id cannot correlate recovery" "run identity mismatch" verify
RUN_TITLE="AI review authorization $AUTHORIZATION_CHECK_ID from arm 7002.$ARM_RUN_ATTEMPT" \
  expect_fail "substituted arm run id cannot correlate recovery" "run identity mismatch" verify
RUN_TITLE="AI review authorization $AUTHORIZATION_CHECK_ID from arm $ARM_RUN_ID.2" \
  expect_fail "substituted arm attempt cannot correlate recovery" "run identity mismatch" verify
unset RUN_TITLE
JOB_NAME_PREFIX='unexpected / ' \
  expect_fail "unexpected nested job-name prefix cannot correlate recovery" "approached the provider boundary" verify
CURRENT_HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  expect_fail "stale head recovery mutation fails closed" "recovery head is stale" verify
REVIEW_POLICY=different-policy \
  expect_fail "mismatched receipt policy mutation fails closed" "receipt identity mismatch" verify
RETURNED_CHECK_ID=9002 \
  expect_fail "mismatched authorization check mutation fails closed" "check or App identity mismatch" verify
RETURNED_APP_ID=9999 \
  expect_fail "mismatched authorization App mutation fails closed" "check or App identity mismatch" verify
GATE_CONCLUSION=success \
  expect_fail "provider-reservation step mutation fails closed" "approached the provider boundary" verify
PROVIDER_REVIEW=true \
  expect_fail "persisted provider review mutation fails closed" "provider reservation, submission, or review evidence" verify
REVIEW_RUN_ATTEMPT=1 \
  expect_pass "trusted-arm-owned unique initial dispatch admits the receipt" verify
REVIEW_RUN_ATTEMPT=1 RUN_ACTOR=maintainer \
  expect_fail "manual attempt-one dispatch cannot replay retained receipt" "not trusted-arm owned" verify
REVIEW_RUN_ATTEMPT=1 AUTHORIZATION_STATE=retained \
  expect_fail "attempt-one dispatch cannot replay a completed retained authorization" "cannot replay a retained authorization" verify
# Verjson/.github#1480 — one message used to cover three unrelated conditions
# ("missing or not unique"), so a not-yet-indexed run and a genuinely duplicated
# run were indistinguishable in the log and neither could be triaged. Each
# condition now reports distinctly, and only the eventually-consistent one is
# retried. These assert the distinctness, not merely that each still fails.
REVIEW_RUN_ATTEMPT=1 DUPLICATE_CORRELATED_RUN=true \
  expect_fail "duplicate attempt-one dispatch is refused as a duplicate" "dispatch is duplicated" verify
REVIEW_RUN_ATTEMPT=1 MISSING_CORRELATED_RUN=true \
  expect_fail "a run absent from the listing is refused as absent, not as a duplicate" \
  "has not appeared in the workflow run listing" verify
REVIEW_RUN_ATTEMPT=1 CORRELATED_RUN_ATTEMPT=2 \
  expect_fail "a retried initial dispatch is refused as retried, not as a duplicate" \
  "was retried" verify

# The absent case is eventually consistent, so it is bound-retried: a run that
# shows up on the second poll must be admitted rather than refused outright.
polls_file="$tmp/polls"
: >"$polls_file"
REVIEW_RUN_ATTEMPT=1 CORRELATED_POLLS="$polls_file" CORRELATED_RUN_APPEARS_AFTER=1 \
  expect_pass "a run that appears on a later poll is admitted" verify
[ "$(cat "$polls_file")" -eq 2 ] \
  && pass "the absent listing is re-polled, and stops as soon as the run appears" \
  || fail "the absent listing was not re-polled exactly twice (polls=$(cat "$polls_file"))"

# The retry must also be BOUNDED. `-ge 2` above pins only the floor, so a raised
# or unbounded ceiling would slip through it; assert the exact maximum here.
: >"$polls_file"
REVIEW_RUN_ATTEMPT=1 MISSING_CORRELATED_RUN=true CORRELATED_POLLS="$polls_file" \
  expect_fail "a permanently absent run is refused after a bounded number of polls" \
  "has not appeared in the workflow run listing" verify
[ "$(cat "$polls_file")" -eq 5 ] \
  && pass "a permanently absent listing is polled exactly 5 times, then refused" \
  || fail "absent-listing poll count is not exactly 5 (polls=$(cat "$polls_file"))"

# A duplicate is NOT eventually consistent — retrying it would only widen the
# window in which a second dispatch could be accepted as the trusted one, so it
# must be refused on the very first look.
: >"$polls_file"
REVIEW_RUN_ATTEMPT=1 DUPLICATE_CORRELATED_RUN=true CORRELATED_POLLS="$polls_file" \
  expect_fail "a duplicate is refused without retrying" "dispatch is duplicated" verify
[ "$(cat "$polls_file")" -eq 1 ] \
  && pass "a duplicate dispatch is never re-polled" \
  || fail "a duplicate dispatch was re-polled (polls=$(cat "$polls_file"))"


# Execute the actual preflight and gate programs against the same failed check.
python3 - "$here/../../.github/workflows/ai-review-merge.yml" "$tmp" <<'PYTHON'
import pathlib
import sys
import yaml
workflow = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text())
root = pathlib.Path(sys.argv[2])
preflight = next(s for s in workflow['jobs']['preflight']['steps'] if s.get('id') == 'zero-provider-recovery')
gate = next(s for s in workflow['jobs']['gate']['steps'] if s.get('id') == 'trusted-authorization')
assert gate['env']['ZERO_PROVIDER_RECOVERY'] == '${{ needs.preflight.outputs.zero_provider_recovery }}'
for name, expression in [('REVIEW_EVENT', '${{ github.event_name }}'),
                         ('REVIEW_RUN_ID', '${{ github.run_id }}'),
                         ('REVIEW_RUN_ATTEMPT', '${{ github.run_attempt }}'),
                         ('DEFAULT_BRANCH', '${{ github.event.repository.default_branch }}')]:
    assert gate['env'][name] == expression
checkout = next(s for s in workflow['jobs']['gate']['steps'] if s.get('name') == 'Check out immutable arm verifier')
assert checkout['with']['ref'] == '${{ steps.trusted-revision.outputs.sha }}'
assert 'scripts/ci-gate/verify-zero-provider-recovery.sh' in checkout['with']['sparse-checkout']
for step in workflow['jobs']['gate']['steps']:
    if step.get('id') in ('replay_primary', 'replay_fallback'):
        assert "steps.trusted-authorization.outcome == 'success'" in step['if']
(root / 'preflight.sh').write_text(preflight['run'])
(root / 'gate.sh').write_text(gate['run'])
PYTHON
[ "$?" -eq 0 ] || fail 'gate/recovery context or replay setup boundary drifted'
mkdir -p "$tmp/.gate-trust/scripts/ci-gate" "$tmp/.gate-recovery/scripts/ci-gate"
for tree in .gate-trust .gate-recovery; do
  cp "$verifier" "$tmp/$tree/scripts/ci-gate/verify-zero-provider-recovery.sh"
  cp "$tmp/receipt-verifier" "$tmp/$tree/scripts/ci-gate/verify-arm-receipt.sh"
done
for helper in openai-review deepseek-review prepare-deepseek-replay review-verdict review-attempt-count; do
  printf '# trusted fixture\n' >"$tmp/.gate-trust/scripts/ci-gate/$helper.py"
done
export REVIEW_EVENT=workflow_dispatch GITHUB_OUTPUT="$tmp/gate-output"
gate_admission() { (cd "$tmp" && bash gate.sh); }
preflight_to_gate() {
  : >"$GITHUB_OUTPUT"
  (cd "$tmp" && bash preflight.sh) || return
  ZERO_PROVIDER_RECOVERY="$(sed -n 's/^eligible=//p' "$GITHUB_OUTPUT")" gate_admission
}
export API_CAPTURE="$tmp/recovery-api-calls"
: >"$API_CAPTURE"
expect_pass 'verified failed exact authorization passes actual preflight then gate' preflight_to_gate
[ "$(grep -c 'jobs?filter=all' "$API_CAPTURE")" -eq 2 ] \
  && pass 'gate independently repeats prior-provider proof' \
  || fail 'gate did not repeat prior-provider proof'
expect_pass 'adopter recovery selects its caller-prefixed reusable jobs' verify
TARGET_REPO=Verjson/.github RUN_REPOSITORY=Verjson/.github JOB_NAME_PREFIX='review / ' \
  expect_pass 'canonical repository uses its dispatch-wrapper path for recovery' verify
unset API_CAPTURE
ZERO_PROVIDER_RECOVERY=false expect_fail 'failed authorization without recovery proof remains refused' 'not pending review' gate_admission
ZERO_PROVIDER_RECOVERY=garbage expect_fail 'malformed recovery proof flag is refused' 'invalid zero-provider recovery flag' gate_admission
ZERO_PROVIDER_RECOVERY=true REVIEW_EVENT=workflow_call expect_fail 'reusable delivery cannot claim direct recovery' 'invalid zero-provider gate recovery context' gate_admission
ZERO_PROVIDER_RECOVERY=true REVIEW_RUN_ATTEMPT=1 expect_fail 'first attempt cannot claim recovery' 'invalid zero-provider gate recovery context' gate_admission
ZERO_PROVIDER_RECOVERY=true CURRENT_HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa expect_fail 'gate rejects stale recovery head' 'recovery head is stale' gate_admission
ZERO_PROVIDER_RECOVERY=true RETURNED_CHECK_ID=9002 expect_fail 'gate rejects substituted recovery check' 'check or App identity mismatch' gate_admission
ZERO_PROVIDER_RECOVERY=true RETURNED_APP_ID=9999 expect_fail 'gate rejects substituted recovery App' 'check or App identity mismatch' gate_admission
ZERO_PROVIDER_RECOVERY=true RETURNED_APP_ID="$EXPECTED_APP_ID" RETURNED_APP_SLUG="$EXPECTED_APP_SLUG" expect_fail 'gate rejects a legacy review-App-owned authorization check' 'check or App identity mismatch' gate_admission
ZERO_PROVIDER_RECOVERY=true GATE_CONCLUSION=success expect_fail 'preflight flag cannot bypass prior provider boundary' 'approached the provider boundary' gate_admission
ZERO_PROVIDER_RECOVERY=true PROVIDER_REVIEW=true expect_fail 'gate refuses existing provider reservation' 'provider reservation, submission, or review evidence' gate_admission
REVIEW_POLICY=different-policy expect_fail 'invalid receipt cannot reach recovered gate' 'receipt identity mismatch' preflight_to_gate
REVIEW_RUN_ATTEMPT=1 expect_pass 'normal pending authorization still passes preflight and gate' preflight_to_gate

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."; exit 1
