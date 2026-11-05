#!/usr/bin/env bash
# Exercises Gate R in scripts/assert-mergeable-head.sh (ADR 0257, #2031) against a
# stubbed `gh`.
#
# On a promotion-enabled repository (one whose base ref carries the organization's
# required `gate-rearm.yml` workflows rule) an autonomous merge must find a valid
# ADR 0202 independent-review receipt bound to the exact head it is about to merge.
# Every fixture below passes Gates A, W, B, and C, so a refusal can only be Gate R's.
#
# Every negative case asserts the gate, the exit code, and the specific reason.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/assert-mergeable-head.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

REPO=verJSON/verjson-identity-contracts
PR=121
HEAD_SHA=3b001575b09549217775577a20b9091be6de44a2
OTHER_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
HEAD_BRANCH=feat/example
APP_ID=15368
ARM_RULESET_ID=20722935
WF_REQUIRED=332379840
GATE_PATH=.github/workflows/gate-rearm.yml
PINNED_SHA=24db445fe410df7bfbd740d8766b2d9ad8e73915
MARKER="<!-- independent-review:v1 pr:$PR head:$HEAD_SHA verdict:approved -->"

mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
set -uo pipefail
args="$*"
printf '%s\n' "$args" >>"$CALLS"
if [ -n "${FAIL_PERMISSION_FOR:-}" ] && [[ "$args" == *"/collaborators/$FAIL_PERMISSION_FOR/permission"* ]]; then
  echo "gh: Server Error (HTTP 502)" >&2; exit 1
fi
case "$args" in
  # Like gh, answer only the requested fields, so a field the script stops asking for
  # is absent rather than served from the fixture anyway.
  *"pr view "*"--json headRefOid,baseRefName"*)
    fields="${args#*--json }"; fields="${fields%% *}"
    jq -c --arg fields "$fields" '($fields | split(",")) as $asked | with_entries(select(.key as $k | $asked | index($k)))' <<<"$PR_JSON_FIXTURE" ;;
  *"/rules/branches/"*) printf '%s\n' "$RULES_FIXTURE" ;;
  *"/rulesets/"*) printf '%s\n' "$RULESET_FIXTURE" ;;
  *"/check-runs?per_page"*) printf '%s\n' "$CHECK_RUNS_FIXTURE" ;;
  *"/commits/"*"/status"*) printf '{"total_count":0,"statuses":[]}\n' ;;
  *"/actions/runs?head_sha="*) printf '%s\n' "$RUNS_FIXTURE" ;;
  *"/annotations?per_page"*) printf '[]\n' ;;
  # The list is read twice: once to select the receipt, once to prove nothing later
  # superseded it. LATEST_REVIEWS_FIXTURE stands in for a review that lands between.
  # FAIL_REVIEW_LIST fails every listing; FAIL_REVIEW_LIST_AT fails only that one.
  *"/pulls/$PR/reviews?per_page=100"*)
    [ "${FAIL_REVIEW_LIST:-0}" -eq 0 ] || { echo "gh: Server Error (HTTP 502)" >&2; exit 1; }
    count="$(cat "$REVIEW_LIST_CALLS")"; count=$((count + 1)); printf '%s\n' "$count" >"$REVIEW_LIST_CALLS"
    [ "${FAIL_REVIEW_LIST_AT:-0}" -ne "$count" ] || { echo "gh: Server Error (HTTP 502)" >&2; exit 1; }
    if [ "$count" -le 1 ]; then printf '%s\n' "$REVIEWS_FIXTURE"
    else printf '%s\n' "${LATEST_REVIEWS_FIXTURE:-$REVIEWS_FIXTURE}"; fi ;;
  *"/pulls/$PR/reviews/"*)
    id="${args#*/reviews/}"; id="${id%% *}"
    jq -ce --argjson id "$id" '.[] | select(.id == $id)' <<<"$REVIEWS_FIXTURE" \
      || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; } ;;
  # The caller passes --jq '.role_name // ""', so the stub answers with the role itself.
  *"/collaborators/independent-reviewer/permission"*) printf '%s\n' "$REVIEWER_ROLE" ;;
  *"/collaborators/"*"/permission"*) printf '%s\n' none ;;
  *) echo "gh: unstubbed call: $args" >&2; exit 1 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" REVIEW_LIST_CALLS="$tmp/review-list-calls" PR

check_run() { # id name suite
  printf '{"id":%s,"name":"%s","status":"completed","conclusion":"success","app":{"id":%s},"check_suite":{"id":%s}}' \
    "$1" "$2" "$APP_ID" "$3"
}
workflow_entry="{\"repository_id\":1269388380,\"path\":\"$GATE_PATH\",\"sha\":\"$PINNED_SHA\",\"ref\":\"refs/heads/main\"}"
status_rule="{\"type\":\"required_status_checks\",\"parameters\":{\"required_status_checks\":[{\"context\":\"CI / test\",\"integration_id\":$APP_ID}]}}"
arm_rule="{\"type\":\"workflows\",\"parameters\":{\"do_not_enforce_on_create\":true,\"workflows\":[$workflow_entry]},\"ruleset_source_type\":\"Organization\",\"ruleset_source\":\"verJSON\",\"ruleset_id\":$ARM_RULESET_ID}"
arm_ruleset="{\"id\":$ARM_RULESET_ID,\"name\":\"ai-authorization-arm-required\",\"source_type\":\"Organization\",\"source\":\"verJSON\",\"enforcement\":\"active\",\"updated_at\":\"2026-11-03T11:39:01.138-05:00\",\"rules\":[{\"type\":\"workflows\",\"parameters\":{\"do_not_enforce_on_create\":true,\"workflows\":[$workflow_entry]}}]}"
# The org-required arm succeeded on the head, after the ruleset last changed.
required_run="$(jq -nc --arg head "$HEAD_SHA" --arg branch "$HEAD_BRANCH" --arg repo "$REPO" --arg path "$GATE_PATH" \
  --argjson wf "$WF_REQUIRED" '
  { id: 41711448295, check_suite_id: 703, workflow_id: $wf, run_number: 211,
    event: "pull_request_target", status: "completed", conclusion: "success",
    head_sha: $head, name: "AI review authorization arm", path: $path, run_attempt: 1,
    created_at: "2026-11-03T20:19:05Z", run_started_at: "2026-11-03T20:19:05Z",
    head_branch: $branch, head_repository: { full_name: $repo }, pull_requests: [],
    workflow_url: "https://api.github.com/repos/\($repo)/actions/required_workflows/\($wf)",
    referenced_workflows: [] }')"
# review id state commit login type body
review() {
  jq -nc --argjson id "$1" --arg state "$2" --arg commit "$3" --arg login "$4" --arg type "$5" --arg body "$6" \
    '{id: $id, state: $state, commit_id: $commit, user: {login: $login, type: $type}, body: $body}'
}
receipt="$(review 82 COMMENTED "$HEAD_SHA" independent-reviewer User "$MARKER")"
default_check_runs="$(printf '{"total_count":3,"check_runs":[%s,%s,%s]}' \
  "$(check_run 1 'CI / test' 500)" "$(check_run 31 'event-policy' 703)" "$(check_run 32 'arm' 703)")"

reset_env() {
  : >"$CALLS"
  printf '0\n' >"$REVIEW_LIST_CALLS"
  unset LATEST_REVIEWS_FIXTURE FAIL_REVIEW_LIST FAIL_REVIEW_LIST_AT FAIL_PERMISSION_FOR
  export REVIEWER_ROLE=maintain
  export PR_JSON_FIXTURE="{\"headRefOid\":\"$HEAD_SHA\",\"baseRefName\":\"main\",\"headRefName\":\"$HEAD_BRANCH\",\"headRepository\":{\"nameWithOwner\":\"$REPO\"},\"author\":{\"login\":\"delivery-author\"}}"
  export RULES_FIXTURE="[$status_rule,$arm_rule]"
  export RULESET_FIXTURE="$arm_ruleset"
  export CHECK_RUNS_FIXTURE="$default_check_runs"
  export RUNS_FIXTURE="{\"total_count\":1,\"workflow_runs\":[$required_run]}"
  export REVIEWS_FIXTURE="[$receipt]"
}

run() {
  OUT="$("$script" "$REPO" "$PR" 2>&1)"
  RC=$?
}
expect() { # label expected-rc needle
  if [ "$RC" -eq "$2" ] && grep -qF -- "$3" <<<"$OUT"; then pass "$1"
  else fail "$1 (rc=$RC, output: $OUT)"; fi
}

# --- absent ---------------------------------------------------------------------
reset_env
REVIEWS_FIXTURE='[]'
run
expect "a promotion-enabled head with no independent-review receipt fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — an exact-head independent-review verdict is required before autonomous merge"

# --- valid -----------------------------------------------------------------------
reset_env
run
if [ "$RC" -eq 0 ] && [ "$OUT" = true ]; then
  pass "a valid exact-head receipt from a maintain reviewer passes Gate R"
else
  fail "a valid exact-head receipt from a maintain reviewer passes Gate R (rc=$RC, output: $OUT)"
fi

# --- shared identity ---------------------------------------------------------------
# Until #1964 issues separate identities, agents author and review through one account.
# ADR 0257 accepts that receipt as a named, time-boxed exception, so it still passes,
# but the output must name it and never present it as plain independent review.
reset_env
PR_JSON_FIXTURE="$(jq -c '.author.login = "independent-reviewer"' <<<"$PR_JSON_FIXTURE")"
run
if [ "$RC" -eq 0 ] && [ "$(tail -n 1 <<<"$OUT")" = true ] \
  && grep -qF -- "::notice::Gate R: receipt 82 on $REPO#$PR head $HEAD_SHA is a shared-identity-review: its reviewer independent-reviewer is the pull request's author" <<<"$OUT"; then
  pass "a receipt from the pull request's author passes Gate R and is named shared-identity-review"
else
  fail "a receipt from the pull request's author passes Gate R and is named shared-identity-review (rc=$RC, output: $OUT)"
fi

# --- scope -----------------------------------------------------------------------
# A base ref without the organization's arm rule is not promotion-enabled: Gate R
# neither demands a receipt nor reads the reviews.
reset_env
RULES_FIXTURE="[$status_rule]"
REVIEWS_FIXTURE='[]'
run
if [ "$RC" -eq 0 ] && [ "$OUT" = true ] && ! grep -q '/reviews' "$CALLS"; then
  pass "a repository without the arm rule is not asked for a receipt"
else
  fail "a repository without the arm rule is not asked for a receipt (rc=$RC, output: $OUT)"
fi

# Gate R recognizes the arm by the identity the reviewed arm ruleset image requires. If
# that image moves and the script does not, Gate R would stop seeing armed repositories.
config="$here/../config/ai-review-required-workflow-rollout.json"
reviewed_arm="$(jq -r '[.arm_ruleset.rules[] | select(.type == "workflows") | .parameters.workflows[]
  | "\(.repository_id) \(.path)"] | unique | join(",")' "$config")"
script_arm="$(sed -n 's/^readonly ARM_WORKFLOW_REPOSITORY_ID=//p' "$script") $(sed -n 's/^readonly ARM_WORKFLOW_PATH=//p' "$script")"
if [ -n "$reviewed_arm" ] && [ "$reviewed_arm" = "$script_arm" ]; then
  pass "Gate R's arm identity equals the reviewed arm ruleset image"
else
  fail "Gate R's arm identity equals the reviewed arm ruleset image (script: $script_arm, config: $reviewed_arm)"
fi

# An entry at the arm's path whose repository cannot be read must not read as "not
# promotion-enabled", which is the direction that skips the receipt.
reset_env
RULES_FIXTURE="$(jq -c '(.[] | select(.type == "workflows") | .parameters.workflows[0]) |= del(.repository_id)' <<<"$RULES_FIXTURE")"
RULESET_FIXTURE="$(jq -c '.rules[0].parameters.workflows[0] |= del(.repository_id)' <<<"$RULESET_FIXTURE")"
REVIEWS_FIXTURE='[]'
run
expect "an arm-path workflows entry without a repository id cannot be classified" 1 \
  "Gate R cannot establish whether $REPO@main is promotion-enabled: a workflows rule names $GATE_PATH without a numeric repository_id"

# A present but non-numeric id is the same unreadable repository, not a foreign one: a
# truthiness check would accept the string and then find it unequal to the numeric arm.
reset_env
RULES_FIXTURE="$(jq -c '(.[] | select(.type == "workflows") | .parameters.workflows[0].repository_id) |= tostring' <<<"$RULES_FIXTURE")"
RULESET_FIXTURE="$(jq -c '.rules[0].parameters.workflows[0].repository_id |= tostring' <<<"$RULESET_FIXTURE")"
REVIEWS_FIXTURE='[]'
run
expect "an arm-path workflows entry with a string repository id cannot be classified" 1 \
  "Gate R cannot establish whether $REPO@main is promotion-enabled: a workflows rule names $GATE_PATH without a numeric repository_id"

# --- malformed -------------------------------------------------------------------
reset_env
REVIEWS_FIXTURE="[$(review 82 COMMENTED "$HEAD_SHA" independent-reviewer User "LGTM $MARKER")]"
run
expect "a receipt marker inside other text fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — latest independent-review verdict is edited, withdrawn, dismissed, or not approving"

# Only a COMMENTED review carries the receipt: a dismissed review keeps its exact-head
# commit and its marker body, so the state is the one field that withdraws it.
reset_env
REVIEWS_FIXTURE="[$(review 82 DISMISSED "$HEAD_SHA" independent-reviewer User "$MARKER")]"
run
expect "a dismissed review carrying a valid marker fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — latest independent-review verdict is edited, withdrawn, dismissed, or not approving"

# --- stale head ------------------------------------------------------------------
# The receipt the reviewer published for the head before the last push.
reset_env
stale_marker="<!-- independent-review:v1 pr:$PR head:$OTHER_SHA verdict:approved -->"
REVIEWS_FIXTURE="[$(review 82 COMMENTED "$OTHER_SHA" independent-reviewer User "$stale_marker")]"
run
expect "a receipt for the previous head fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — an exact-head independent-review verdict is required before autonomous merge"

# --- App-authored ----------------------------------------------------------------
reset_env
REVIEWS_FIXTURE="[$(review 82 COMMENTED "$HEAD_SHA" independent-reviewer Bot "$MARKER")]"
run
expect "an App-authored receipt fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — an exact-head independent-review verdict is required before autonomous merge"

# --- superseded ------------------------------------------------------------------
# A later exact-head verdict from the same reviewer governs, so a request for changes
# after the receipt withdraws it.
reset_env
REVIEWS_FIXTURE="[$receipt,$(review 83 CHANGES_REQUESTED "$HEAD_SHA" independent-reviewer User 'one more fix')]"
run
expect "a later changes-requested verdict supersedes the receipt and fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — latest independent-review verdict is edited, withdrawn, dismissed, or not approving"

# A verdict that lands while the receipt is being revalidated also supersedes it.
reset_env
LATEST_REVIEWS_FIXTURE="[$receipt,$(review 83 COMMENTED "$HEAD_SHA" independent-reviewer User 'later verdict')]"
export LATEST_REVIEWS_FIXTURE
run
expect "a verdict landing during revalidation supersedes the receipt and fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — a later independent-review verdict superseded receipt 82"

# --- insufficiently authorized ---------------------------------------------------
reset_env
REVIEWER_ROLE='write'
run
expect "a receipt from a write-only collaborator fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — an exact-head independent-review verdict is required before autonomous merge"

# --- unreadable ----------------------------------------------------------------------
# A read that fails leaves the receipt unanswered, not refused: like every other API
# failure in the assertion it exits 1, never 6, and names the read.
# A reviewer whose permission cannot be read is unknown, not unprivileged. Skipping it
# would let an older receipt govern past a newer verdict that may have withdrawn it.
reset_env
REVIEWS_FIXTURE="[$receipt,$(review 83 CHANGES_REQUESTED "$HEAD_SHA" second-maintainer User 'one more fix')]"
export FAIL_PERMISSION_FOR=second-maintainer
run
expect "an unreadable reviewer permission leaves Gate R unevaluated and names the lookup that failed" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: could not read the repository permission of second-maintainer on $REPO"

# The same holds on the revalidating search: a newer reviewer that appears only on the
# second read, and whose permission cannot be read, must not let the receipt stand.
reset_env
LATEST_REVIEWS_FIXTURE="[$receipt,$(review 83 CHANGES_REQUESTED "$HEAD_SHA" second-maintainer User 'one more fix')]"
export LATEST_REVIEWS_FIXTURE FAIL_PERMISSION_FOR=second-maintainer
run
expect "an unreadable permission on the revalidating search leaves Gate R unevaluated and names the lookup" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: could not read the repository permission of second-maintainer on $REPO"

reset_env
export FAIL_REVIEW_LIST=1
run
expect "an unreadable review listing leaves Gate R unevaluated and names the read that failed" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: could not list the reviews of $REPO#$PR"

# Only the revalidating listing fails: the receipt was found, but nothing proves that no
# later verdict superseded it.
reset_env
export FAIL_REVIEW_LIST_AT=2
run
expect "an unreadable revalidating review listing leaves Gate R unevaluated" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: could not list the reviews of $REPO#$PR"

# --- verifier contract -------------------------------------------------------------
# Gate R trusts only the verifier's documented results: 0 with a well-formed result
# line, 1 (refused), and 3 (a read failed). Anything else is an evaluation fault. The
# author is null, as for a deleted account, so the shared-identity comparison cannot
# stand in for a check on the result.
run_with_verifier() { # verifier-body
  rm -rf "$tmp/stubbed"; mkdir -p "$tmp/stubbed/ci-gate"
  cp "$script" "$tmp/stubbed/assert-mergeable-head.sh"
  printf '#!/usr/bin/env bash\n%s\n' "$1" >"$tmp/stubbed/ci-gate/independent-review-receipt.sh"
  OUT="$(bash "$tmp/stubbed/assert-mergeable-head.sh" "$REPO" "$PR" 2>&1)"
  RC=$?
}
reset_env
PR_JSON_FIXTURE="$(jq -c '.author = null' <<<"$PR_JSON_FIXTURE")"
run_with_verifier 'exit 0'
expect "a verifier that passes without writing its result leaves Gate R unevaluated" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: could not read the verifier's result"

reset_env
PR_JSON_FIXTURE="$(jq -c '.author = null' <<<"$PR_JSON_FIXTURE")"
# A digit prefix is not a review id: only an anchored match rejects it.
run_with_verifier 'printf "%s\t%s\n" 82x independent-reviewer >"$4"'
expect "a verifier result without a numeric review id leaves Gate R unevaluated" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: the verifier's result is malformed"

reset_env
PR_JSON_FIXTURE="$(jq -c '.author = null' <<<"$PR_JSON_FIXTURE")"
run_with_verifier 'printf "%s\t\n" 82 >"$4"'
expect "a verifier result without a reviewer leaves Gate R unevaluated" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: the verifier's result is malformed"

reset_env
run_with_verifier 'echo "::error::pull request number is malformed"; exit 2'
expect "a verifier usage failure (exit 2) leaves Gate R unevaluated, not refused" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: its receipt verifier exited with unexpected status 2"

reset_env
run_with_verifier 'kill -TERM $$'
expect "a verifier killed by a signal leaves Gate R unevaluated, not refused" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: its receipt verifier exited with unexpected status 143"

# --- verifier unavailable ------------------------------------------------------------
# Gate R runs the verifier beside this script in the canonical checkout. A copy of the
# script without it cannot evaluate the receipt: that is exit 1, not a receipt refusal.
reset_env
mkdir -p "$tmp/standalone"
cp "$script" "$tmp/standalone/assert-mergeable-head.sh"
OUT="$(bash "$tmp/standalone/assert-mergeable-head.sh" "$REPO" "$PR" 2>&1)"
RC=$?
expect "a script copied without its receipt verifier cannot evaluate Gate R" 1 \
  "Gate R cannot evaluate $REPO#$PR: its receipt verifier $tmp/standalone/ci-gate/independent-review-receipt.sh is missing; run the assertion from a canonical verJSON/.github checkout"

[ "$fails" -eq 0 ] || { printf '%s failure(s)\n' "$fails"; exit 1; }
printf 'all Gate R cases passed\n'
