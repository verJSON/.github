#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
wf="$root/.github/workflows/actions-ci.yml"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

group_expr="$(awk '/^concurrency:/{cap=1; next} cap && /^  group:/{sub(/^  group:[[:space:]]*/, ""); print; exit}' "$wf")"
cancel_expr="$(awk '/^concurrency:/{cap=1; next} cap && /^  cancel-in-progress:/{sub(/^  cancel-in-progress:[[:space:]]*/, ""); print; exit}' "$wf")"
group_timeout="$(awk '/^  shell-test-groups:/{cap=1; next} cap && /^    timeout-minutes:/{print $2; exit}' "$wf")"
required_timeout="$(awk '/^  shell-tests:/{cap=1; next} cap && /^    timeout-minutes:/{print $2; exit}' "$wf")"

[ "$group_expr" = '${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}' ] \
  && pass "concurrency is keyed by workflow plus PR number/ref" \
  || fail "unexpected concurrency group: $group_expr"
[ "$cancel_expr" = '${{ github.ref != '"'"'refs/heads/main'"'"' }}' ] \
  && pass "only main is exempt from cancellation" \
  || fail "unexpected cancel-in-progress predicate: $cancel_expr"
[ "$group_timeout" = 30 ] && [ "$required_timeout" = 2 ] \
  && pass "group workers and required-context aggregation have exact ceilings" \
  || fail "actions-ci group/aggregate timeouts drifted: ${group_timeout:-unset}/${required_timeout:-unset}"

group_for() {
  local workflow="$1" pr="$2" ref="$3"
  printf '%s-%s\n' "$workflow" "${pr:-$ref}"
}
cancel_for() { [ "$1" != refs/heads/main ]; }

[ "$(group_for actions-ci 231 refs/pull/231/merge)" = "$(group_for actions-ci 231 refs/pull/231/merge)" ] \
  && pass "same-PR pushes collide and cancel obsolete work" \
  || fail "same-PR pushes do not share a concurrency group"
[ "$(group_for actions-ci 231 refs/pull/231/merge)" != "$(group_for actions-ci 232 refs/pull/232/merge)" ] \
  && pass "distinct PRs do not collide" \
  || fail "distinct PRs share a concurrency group"
cancel_for refs/pull/231/merge \
  && pass "PR runs are cancelable" \
  || fail "PR run cancellation is disabled"
if cancel_for refs/heads/main; then
  fail "main runs are cancelable"
else
  pass "main runs are never canceled"
fi

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
