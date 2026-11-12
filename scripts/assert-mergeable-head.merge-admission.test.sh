#!/usr/bin/env bash
# Exercises the merge-admission self-exclusion in scripts/assert-mergeable-head.sh
# (ADR 0260, #1990, #2031) against a stubbed `gh`.
#
# The merge-admission producer creates its `merge-admission` check run in progress on the
# head BEFORE it runs this assertion, and leaves it in progress on every refusal. So the
# assertion must not read the producer's own check as pending, or it could never answer
# `true`. The exclusion is bound to the AI review App: a same-named check from any other
# producer, GitHub Actions included, stays a failure.
#
# Every fixture passes every gate apart from the merge-admission checks it adds, and every
# negative case asserts the gate, the exit code, and the specific reason.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/assert-mergeable-head.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

REPO=Verjson/example
PR=42
HEAD_SHA=4f00000000000000000000000000000000000042
ACTIONS_APP_ID=15368
AI_REVIEW_APP_ID=4528902

mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
set -uo pipefail
args="$*"
case "$args" in
  *"pr view "*"--json headRefOid,baseRefName"*) printf '%s\n' "$PR_JSON_FIXTURE" ;;
  *"/rules/branches/"*) printf '%s\n' "$RULES_FIXTURE" ;;
  *"/check-runs?per_page"*) printf '%s\n' "$CHECK_RUNS_FIXTURE" ;;
  *"/commits/"*"/status"*) printf '{"total_count":0,"statuses":[]}\n' ;;
  *"/actions/runs?head_sha="*) printf '{"total_count":0,"workflow_runs":[]}\n' ;;
  *"/annotations?per_page"*) printf '[]\n' ;;
  *) echo "gh: unstubbed call: $args" >&2; exit 1 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH"

check_run() { # id name status conclusion app-id
  jq -nc --argjson id "$1" --arg name "$2" --arg status "$3" --arg conclusion "$4" --argjson app "$5" \
    '{id: $id, name: $name, status: $status, conclusion: (if $conclusion == "" then null else $conclusion end),
      app: {id: $app}, check_suite: {id: ($id + 1000)}}'
}
required_rule() { # json-array-of-required-contexts
  printf '[{"type":"required_status_checks","parameters":{"required_status_checks":%s}}]' "$1"
}
ci_check="$(check_run 1 'CI / test' completed success "$ACTIONS_APP_ID")"

reset_env() {
  export PR_JSON_FIXTURE="{\"headRefOid\":\"$HEAD_SHA\",\"baseRefName\":\"main\",\"headRefName\":\"feat/example\",\"headRepository\":{\"nameWithOwner\":\"$REPO\"},\"author\":{\"login\":\"delivery-author\"}}"
  export RULES_FIXTURE
  RULES_FIXTURE="$(required_rule "[{\"context\":\"CI / test\",\"integration_id\":$ACTIONS_APP_ID}]")"
}
with_checks() { # check-run json...
  local IFS=,
  export CHECK_RUNS_FIXTURE="{\"total_count\":$#,\"check_runs\":[$*]}"
}

run() {
  OUT="$("$script" "$REPO" "$PR" 2>&1)"
  RC=$?
}
expect() { # label expected-rc needle
  if [ "$RC" -eq "$2" ] && grep -qF -- "$3" <<<"$OUT"; then pass "$1"
  else fail "$1 (rc=$RC, output: $OUT)"; fi
}
expect_true() { # label
  if [ "$RC" -eq 0 ] && [ "$OUT" = true ]; then pass "$1"
  else fail "$1 (rc=$RC, output: $OUT)"; fi
}

# --- Gate C: the producer's own check -------------------------------------------------
reset_env
with_checks "$ci_check" "$(check_run 2 merge-admission in_progress '' "$AI_REVIEW_APP_ID")"
run
expect_true "Gate C sets aside an in-progress merge-admission check produced by the bound AI review App"

# --- Gate A: the context the ruleset requires from the bound App ------------------------
# Once the ruleset requires merge-admission from the AI review App (ADR 0260, action 2),
# the assertion is what computes that context, so it cannot also require it.
reset_env
RULES_FIXTURE="$(required_rule "[{\"context\":\"CI / test\",\"integration_id\":$ACTIONS_APP_ID},{\"context\":\"merge-admission\",\"integration_id\":$AI_REVIEW_APP_ID}]")"
with_checks "$ci_check" "$(check_run 2 merge-admission in_progress '' "$AI_REVIEW_APP_ID")"
run
expect_true "Gate A sets aside the merge-admission context the ruleset binds to the bound AI review App"

# --- forgery: the same name from another producer ----------------------------------------
# A same-repository branch's pull_request workflow can post any check name as GitHub
# Actions. Setting it aside by name alone would hide it, so it stays a Gate C failure.
reset_env
with_checks "$ci_check" "$(check_run 2 merge-admission in_progress '' "$AI_REVIEW_APP_ID")" \
  "$(check_run 3 merge-admission completed failure "$ACTIONS_APP_ID")"
run
expect "a same-named merge-admission check from GitHub Actions stays a Gate C failure" 5 \
  "Gate C: $REPO#$PR head $HEAD_SHA has pending or non-passing checks outside the required set — merge-admission=FAILURE"

# A ruleset that binds the context to another App, here GitHub Actions, is not the
# producer's binding, so Gate A keeps requiring it and finds no check from that App.
reset_env
RULES_FIXTURE="$(required_rule "[{\"context\":\"CI / test\",\"integration_id\":$ACTIONS_APP_ID},{\"context\":\"merge-admission\",\"integration_id\":$ACTIONS_APP_ID}]")"
with_checks "$ci_check" "$(check_run 2 merge-admission in_progress '' "$AI_REVIEW_APP_ID")"
run
expect "Gate A keeps a merge-admission context the ruleset binds to GitHub Actions" 3 \
  "absent: merge-admission"

# --- exact head ----------------------------------------------------------------------------
# The producer's check run belongs to one SHA, so it states that SHA and the assertion
# refuses to answer for any other: a head that moved since the check was created is not
# evaluated on that check's behalf.
reset_env
with_checks "$ci_check"
OUT="$("$script" "$REPO" "$PR" 0123456789abcdef0123456789abcdef01234567 2>&1)"
RC=$?
expect "an expected head that is not the pull request's head is an evaluation fault" 1 \
  "$REPO#$PR head $HEAD_SHA is not the expected head 0123456789abcdef0123456789abcdef01234567"

OUT="$("$script" "$REPO" "$PR" "$HEAD_SHA" 2>&1)"
RC=$?
expect_true "an expected head equal to the pull request's head is evaluated"

OUT="$("$script" "$REPO" "$PR" "${HEAD_SHA^^}" 2>&1)"
RC=$?
expect "an expected head that is not a lowercase 40-hex SHA is a usage error" 2 "usage:"

# --- hardening (#2031) ----------------------------------------------------------------------
# Once the ruleset binds merge-admission to the AI review App, Gate A no longer evaluates
# that context, so Gate C is the only gate left that sees a forged same-named check. A Gate C
# that skipped required context names would admit the forgery in exactly the configuration
# the rollout canaries run.
reset_env
RULES_FIXTURE="$(required_rule "[{\"context\":\"CI / test\",\"integration_id\":$ACTIONS_APP_ID},{\"context\":\"merge-admission\",\"integration_id\":$AI_REVIEW_APP_ID}]")"
with_checks "$ci_check" "$(check_run 2 merge-admission in_progress '' "$AI_REVIEW_APP_ID")" \
  "$(check_run 3 merge-admission completed failure "$ACTIONS_APP_ID")"
run
expect "a forged GitHub Actions merge-admission stays a Gate C failure while the ruleset binds the context to the AI review App" 5 \
  "Gate C: $REPO#$PR head $HEAD_SHA has pending or non-passing checks outside the required set — merge-admission=FAILURE"

# A check run that names no App is not the bound App's; an unknown producer is never read as
# the producer.
reset_env
with_checks "$ci_check" "$(check_run 2 merge-admission in_progress '' "$AI_REVIEW_APP_ID" | jq -c 'del(.app)')"
run
expect "a merge-admission check run that names no App stays a Gate C pending check" 5 \
  "Gate C: $REPO#$PR head $HEAD_SHA has pending or non-passing checks outside the required set — merge-admission=IN_PROGRESS"

# The producer only ever leaves its check in progress or completes it as success, so a
# completed non-success from the bound App is not its output: whatever concluded it, the
# assertion still reads it as a Gate C failure.
reset_env
with_checks "$ci_check" "$(check_run 2 merge-admission completed failure "$AI_REVIEW_APP_ID")"
run
expect "a merge-admission check from the bound AI review App that concluded failure stays a Gate C failure" 5 \
  "Gate C: $REPO#$PR head $HEAD_SHA has pending or non-passing checks outside the required set — merge-admission=FAILURE"

# The same holds for every other completed non-success: only stale is GitHub's own verdict.
reset_env
with_checks "$ci_check" "$(check_run 2 merge-admission completed cancelled "$AI_REVIEW_APP_ID")"
run
expect "a merge-admission check from the bound AI review App that concluded cancelled stays a Gate C failure" 5 \
  "Gate C: $REPO#$PR head $HEAD_SHA has pending or non-passing checks outside the required set — merge-admission=CANCELLED"

# GitHub alone concludes an incomplete check run `stale` after 14 days, and the producer
# leaves its check in progress on every refusal, so a stale one from the bound App is that
# producer's own output. Reading it as a failure would refuse the head forever.
reset_env
with_checks "$ci_check" "$(check_run 2 merge-admission completed stale "$AI_REVIEW_APP_ID")" \
  "$(check_run 3 merge-admission completed success "$AI_REVIEW_APP_ID")"
run
expect_true "a stale merge-admission check from the bound AI review App is set aside"

if [ "$fails" -ne 0 ]; then
  printf '%s merge-admission self-exclusion case(s) failed\n' "$fails" >&2
  exit 1
fi
echo "merge-admission self-exclusion: all cases passed"
