#!/usr/bin/env bash
# ADR 0036 / #230: dispatch is repository-local. A repository input would be an
# unusable sibling target under github.token and would tempt reintroduction of
# a broad validation credential.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="$repo_root/.github/workflows/ai-review-merge-dispatch.yml"
reusable_wf="$repo_root/.github/workflows/ai-review-merge.yml"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

on_block="$(awk '
  $0 == "on:" { cap = 1; print; next }
  cap && /^[A-Za-z]/ { exit }
  cap { print }
' "$wf")"
dispatch_block="$(awk '
  $0 == "  workflow_dispatch:" { cap = 1; next }
  cap && /^  [A-Za-z_]+:/ { exit }
  cap { print }
' <<<"$on_block")"
call_block="$(awk '
  $0 == "  workflow_call:" { cap = 1; next }
  cap && /^[A-Za-z]/ { exit }
  cap { print }
' <<<"$on_block")"

if grep -qE '^      repository:' <<<"$dispatch_block"; then
  fail "workflow_dispatch still accepts a cross-repository target"
else
  pass "workflow_dispatch rejects the absent repository input by schema"
fi
if grep -qE '^      repository:' <<<"$call_block"; then
  fail "workflow_call still accepts a cross-repository target"
else
  pass "workflow_call rejects the absent repository input by schema"
fi
grep -qF 'TARGET_REPO: ${{ github.repository }}' "$reusable_wf" \
  && pass "target is bound to the repository where the workflow executes" \
  || fail "TARGET_REPO is not fixed to github.repository"
if grep -q 'inputs\.repository\|id: target_guard' "$wf"; then
  fail "retired sibling-dispatch target machinery remains"
else
  pass "sibling target guard/input machinery is removed"
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
