#!/usr/bin/env bash
# Exercises scripts/ci-gate/merge-admission.sh, the merge-admission producer's evaluation
# (ADR 0260, #1990, #2031), against a stubbed `gh` and a stubbed assertion.
#
# The producer copied into a scratch tree finds the stub assertion where the canonical
# checkout keeps scripts/assert-mergeable-head.sh, so each case sets only the assertion's
# outcome and the API answers it needs. Every case asserts the calls made, in order.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

REPO=Verjson/example
PR=42
HEAD_SHA=4f00000000000000000000000000000000000042
AI_REVIEW_APP_ID=4528902
CHECK_ID=777001

mkdir -p "$tmp/bin" "$tmp/tree/scripts/ci-gate"
cp "$here/merge-admission.sh" "$tmp/tree/scripts/ci-gate/merge-admission.sh"
producer="$tmp/tree/scripts/ci-gate/merge-admission.sh"

# The stub assertion records its arguments and the token it was handed, then answers.
cat >"$tmp/tree/scripts/assert-mergeable-head.sh" <<'ASSERT'
#!/usr/bin/env bash
printf 'assert %s token=%s\n' "$*" "${GH_TOKEN:-}" >>"$CALLS"
printf '%s\n' "${APP_TOKEN-<unset>}" >"$ASSERT_APP_TOKEN"
printf '%s' "$ASSERT_STDOUT"
exit "$ASSERT_RC"
ASSERT

cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
set -uo pipefail
printf 'gh %s token=%s\n' "$*" "${GH_TOKEN:-}" >>"$CALLS"
case "$*" in
  *"-X POST repos/"*"/check-runs "*)
    [ "${FAIL_CREATE:-0}" -eq 0 ] || { echo "gh: Server Error (HTTP 502)" >&2; exit 1; }
    printf '%s\n' "$CREATE_RESPONSE" ;;
  *"-X PATCH repos/"*"/check-runs/"*)
    [ "${FAIL_COMPLETE:-0}" -eq 0 ] || { echo "gh: Server Error (HTTP 502)" >&2; exit 1; }
    printf '%s\n' "$COMPLETE_RESPONSE" ;;
  *) echo "gh: unstubbed call: $*" >&2; exit 1 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" ASSERT_APP_TOKEN="$tmp/assert-app-token"

reset_env() {
  : >"$CALLS"
  unset FAIL_CREATE FAIL_COMPLETE
  export APP_TOKEN=app-token GH_TOKEN=read-token
  export ASSERT_STDOUT=true ASSERT_RC=0
  export CREATE_RESPONSE="{\"id\":$CHECK_ID,\"name\":\"merge-admission\",\"head_sha\":\"$HEAD_SHA\",\"status\":\"in_progress\",\"conclusion\":null,\"app\":{\"id\":$AI_REVIEW_APP_ID}}"
  export COMPLETE_RESPONSE="{\"id\":$CHECK_ID,\"status\":\"completed\",\"conclusion\":\"success\",\"app\":{\"id\":$AI_REVIEW_APP_ID}}"
}
run() {
  OUT="$(bash "$producer" "$REPO" "$PR" "$HEAD_SHA" 2>&1)"
  RC=$?
}
nth_call() { sed -n "${1}p" "$CALLS"; }

# --- the superseding in-progress check comes first ------------------------------------------
reset_env
run
first="$(nth_call 1)"
second="$(nth_call 2)"
if [ "$RC" -eq 0 ] \
  && [[ "$first" == "gh api -X POST repos/$REPO/check-runs "* ]] \
  && [[ "$first" == *" -f name=merge-admission "* ]] \
  && [[ "$first" == *" -f head_sha=$HEAD_SHA "* ]] \
  && [[ "$first" == *" -f status=in_progress "* ]] \
  && [[ "$first" == *" token=app-token" ]] \
  && [ "$second" = "assert $REPO $PR $HEAD_SHA token=read-token" ]; then
  pass "the producer creates an in-progress merge-admission check on the exact head with the App token before it runs the assertion"
else
  fail "the producer creates an in-progress merge-admission check on the exact head with the App token before it runs the assertion (rc=$RC, calls: $(cat "$CALLS"), output: $OUT)"
fi

# --- least privilege: the assertion reads with the read-only token only ----------------------
# The App token can write check runs; the assertion has no use for it, so it never sees it.
reset_env
rm -f "$ASSERT_APP_TOKEN"
run
if [ "$RC" -eq 0 ] && [ "$(cat "$ASSERT_APP_TOKEN" 2>/dev/null)" = "<unset>" ]; then
  pass "the assertion runs without the App token in its environment"
else
  fail "the assertion runs without the App token in its environment (rc=$RC, assertion saw APP_TOKEN: $(cat "$ASSERT_APP_TOKEN" 2>/dev/null), output: $OUT)"
fi

# --- admission: exit 0 printing exactly true --------------------------------------------------
reset_env
run
third="$(nth_call 3)"
if [ "$RC" -eq 0 ] \
  && [[ "$third" == "gh api -X PATCH repos/$REPO/check-runs/$CHECK_ID "* ]] \
  && [[ "$third" == *" -f status=completed "* ]] \
  && [[ "$third" == *" -f conclusion=success "* ]] \
  && [[ "$third" == *" token=app-token" ]] \
  && [ "$(wc -l <"$CALLS")" -eq 3 ]; then
  pass "an assertion that exits 0 printing true completes that same check run as success with the App token"
else
  fail "an assertion that exits 0 printing true completes that same check run as success with the App token (rc=$RC, calls: $(cat "$CALLS"), output: $OUT)"
fi

# --- refusal and error: left in progress, and the producer itself succeeds ----------------------
# ADR 0260 precondition (a): the only blocking signal is the check left in progress, so
# the producer's own job concludes success. Each row is <assertion exit>:<its stdout>.
# `6:true` is a Gate R refusal that printed `true` anyway: the exit status alone refuses.
for row in '0:false' '0:' '0:true false' '6:' '6:true' '1:' '5:'; do
  reset_env
  ASSERT_RC="${row%%:*}"
  ASSERT_STDOUT="${row#*:}"
  run
  if [ "$RC" -eq 0 ] && ! grep -q -- '-X PATCH' "$CALLS" && [ "$(wc -l <"$CALLS")" -eq 2 ]; then
    pass "an assertion exiting $ASSERT_RC printing '$ASSERT_STDOUT' leaves the check in progress and the producer succeeds"
  else
    fail "an assertion exiting $ASSERT_RC printing '$ASSERT_STDOUT' leaves the check in progress and the producer succeeds (rc=$RC, calls: $(cat "$CALLS"), output: $OUT)"
  fi
done

# --- the created check must be the bound App's ---------------------------------------------------
# A token minted from another App's key would post a check the ruleset does not bind and
# the assertion does not set aside. Its own refusal would then be the assertion's input.
reset_env
CREATE_RESPONSE="$(jq -c '.app.id = 15368' <<<"$CREATE_RESPONSE")"
run
if [ "$RC" -eq 0 ] && [ "$(wc -l <"$CALLS")" -eq 1 ] \
  && grep -qF "::warning::merge-admission for $REPO#$PR head $HEAD_SHA was not evaluated: the created check run is not the AI review App's (app 4528902)" <<<"$OUT"; then
  pass "a check run created by any App other than the AI review App stops the evaluation"
else
  fail "a check run created by any App other than the AI review App stops the evaluation (rc=$RC, calls: $(cat "$CALLS"), output: $OUT)"
fi

# --- no superseding check, no evaluation ------------------------------------------------------
# Without its own new in-progress check the evaluation could only complete an older one,
# or none, so a failed creation stops it before the assertion runs.
reset_env
export FAIL_CREATE=1
run
if [ "$RC" -eq 0 ] && [ "$(wc -l <"$CALLS")" -eq 1 ] \
  && grep -qF "::warning::merge-admission for $REPO#$PR head $HEAD_SHA was not evaluated: could not create its merge-admission check run" <<<"$OUT"; then
  pass "a failed check-run creation stops the evaluation and the producer succeeds"
else
  fail "a failed check-run creation stops the evaluation and the producer succeeds (rc=$RC, calls: $(cat "$CALLS"), output: $OUT)"
fi

# A record without a numeric id names no check run to complete; the id is spent as a path
# segment of the completion, so it is validated before the assertion runs.
reset_env
CREATE_RESPONSE="$(jq -c '.id = "777001/../1"' <<<"$CREATE_RESPONSE")"
run
if [ "$RC" -eq 0 ] && [ "$(wc -l <"$CALLS")" -eq 1 ] \
  && grep -qF "::warning::merge-admission for $REPO#$PR head $HEAD_SHA was not evaluated: the created check run carries no numeric id" <<<"$OUT"; then
  pass "a created check run without a numeric id stops the evaluation"
else
  fail "a created check run without a numeric id stops the evaluation (rc=$RC, calls: $(cat "$CALLS"), output: $OUT)"
fi

# --- a completion that fails leaves the check in progress -------------------------------------
reset_env
export FAIL_COMPLETE=1
run
if [ "$RC" -eq 0 ] && [ "$(wc -l <"$CALLS")" -eq 3 ] \
  && grep -qF "::warning::merge-admission for $REPO#$PR head $HEAD_SHA was admitted, but check run $CHECK_ID could not be completed as success, so it stays in progress" <<<"$OUT"; then
  pass "a failed completion is named and the producer succeeds"
else
  fail "a failed completion is named and the producer succeeds (rc=$RC, calls: $(cat "$CALLS"), output: $OUT)"
fi

# --- no App token: nothing to supersede with ---------------------------------------------------
# The mint step tolerates failure (precondition (a)), so an absent token reaches here.
reset_env
unset APP_TOKEN
run
if [ "$RC" -eq 0 ] && [ ! -s "$CALLS" ] \
  && grep -qF "::warning::merge-admission for $REPO#$PR head $HEAD_SHA was not evaluated: no AI review App token is available" <<<"$OUT"; then
  pass "an absent App token stops the evaluation before any call and the producer succeeds"
else
  fail "an absent App token stops the evaluation before any call and the producer succeeds (rc=$RC, calls: $(cat "$CALLS"), output: $OUT)"
fi

# --- usage --------------------------------------------------------------------------------------
# The repository, number, and head are spent in API paths and the check run's head, so a
# malformed one is refused before any call.
for args in "$REPO $PR ${HEAD_SHA^^}" "$REPO 0 $HEAD_SHA" "not/a/repo $PR $HEAD_SHA" "$REPO $PR"; do
  reset_env
  # shellcheck disable=SC2086 # each row is deliberately split into arguments
  OUT="$(bash "$producer" $args 2>&1)"
  RC=$?
  if [ "$RC" -eq 2 ] && [ ! -s "$CALLS" ] && grep -qF "usage:" <<<"$OUT"; then
    pass "malformed arguments ($args) are a usage error before any call"
  else
    fail "malformed arguments ($args) are a usage error before any call (rc=$RC, calls: $(cat "$CALLS"), output: $OUT)"
  fi
done

# --- the in-progress check links the evaluation that holds it ---------------------------------
# A refusal leaves the check in progress with no conclusion, so its details link is the way
# to the assertion's reason in the producer run's log.
reset_env
export DETAILS_URL="https://github.com/$REPO/actions/runs/9001"
run
if [ "$RC" -eq 0 ] && [[ "$(nth_call 1)" == *" -f details_url=$DETAILS_URL "* ]]; then
  pass "the in-progress check links the producer run that evaluates it"
else
  fail "the in-progress check links the producer run that evaluates it (rc=$RC, calls: $(cat "$CALLS"), output: $OUT)"
fi
unset DETAILS_URL

# --- one App binding ---------------------------------------------------------------------------
# The producer checks the App it created as; the assertion sets aside that App's checks. Two
# different ids would make every evaluation read its own in-progress check as pending.
bound_id() { sed -n 's/^readonly MERGE_ADMISSION_APP_ID=\([0-9][0-9]*\)$/\1/p' "$1"; }
producer_app="$(bound_id "$here/merge-admission.sh")"
assertion_app="$(bound_id "$here/../assert-mergeable-head.sh")"
if [ -n "$producer_app" ] && [ "$producer_app" = "$assertion_app" ] && [ "$producer_app" = "$AI_REVIEW_APP_ID" ]; then
  pass "the producer and the assertion bind the same AI review App id"
else
  fail "the producer and the assertion bind the same AI review App id (producer: '$producer_app', assertion: '$assertion_app', expected: $AI_REVIEW_APP_ID)"
fi

if [ "$fails" -ne 0 ]; then
  printf '%s merge-admission producer case(s) failed\n' "$fails" >&2
  exit 1
fi
echo "merge-admission producer: all cases passed"
