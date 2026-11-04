#!/usr/bin/env bash
set -uo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
reconcile="$root/.github/workflows/renovate-compatibility-reconcile.yml"
canary="$root/.github/workflows/renovate-compatibility-canary.yml"
generator="$root/scripts/gen-renovate-compatibility-caller.sh"
planner="$root/.github/workflows/renovate-grouping-plan.yml"
fails=0
pass() { echo "ok - $1"; }
fail() { echo "FAIL - $1"; fails=$((fails + 1)); }

grep -q 'schedule:' "$reconcile" && grep -q 'workflow_dispatch:' "$reconcile" \
  && pass "reconciler is scheduled and dispatchable" || fail "reconciler triggers drifted"
grep -q 'mode:\"observe-only\"' "$reconcile" \
  && pass "reconciler cannot create holds" || fail "observe-only boundary missing"
! grep -Eq 'ORG_ADMIN_TOKEN|contents: write|pull-requests: write|issues: write' "$reconcile" \
  && pass "reconciler has no mutation grant" || fail "reconciler is overprivileged"
[ "$(grep -c 'GH_TOKEN: \${{ steps.compatibility-app-token.outputs.token }}' "$reconcile")" -eq 2 ] \
  && grep -q 'RENOVATE_COMPATIBILITY_CLIENT_ID' "$reconcile" \
  && grep -q 'RENOVATE_COMPATIBILITY_APP_PRIVATE_KEY' "$reconcile" \
  && ! grep -q 'RENOVATE_COMPATIBILITY_PAT\|ORG_ADMIN_TOKEN' "$reconcile" \
  && pass "reconciler uses only the dedicated compatibility App token" \
  || fail "reconciler App authentication drifted"
grep -q 'persist-credentials: false' "$canary" \
  && grep -q 'unset NODE_AUTH_TOKEN NPM_TOKEN GH_TOKEN GITHUB_TOKEN' "$canary" \
  && grep -q -- '--ignore-scripts' "$canary" \
  && pass "candidate code receives no credential or lifecycle-script path" \
  || fail "canary trust boundary weakened"
! grep -Eq 'contents: write|packages: write|id-token: write|secrets: inherit|npm publish|gh release|git push' "$canary" \
  && pass "canary cannot push, publish, deploy, or mint cloud identity" \
  || fail "canary gained a destructive capability"
grep -q '^  receipt:$' "$canary" \
  && grep -q '^    permissions: {}$' "$canary" \
  && grep -q 'RESULT: \${{ needs.canary.result }}' "$canary" \
  && grep -q 'CALLER_SHA: \${{ github.workflow_sha }}' "$canary" \
  && grep -q 'commandContract:\["npm:build","npm:typecheck","npm:test"\]' "$canary" \
  && pass "credentialless control job authors the bound receipt" \
  || fail "candidate can influence the authoritative receipt"
receipt_line="$(grep -n '^  receipt:$' "$canary" | cut -d: -f1)"
if tail -n "+$receipt_line" "$canary" | grep 'npm run' >/dev/null; then
  fail "receipt control job executes candidate code"
else
  pass "receipt control job never executes candidate code"
fi
grep -qF "fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')" "$reconcile" \
  && [ "$(grep -cF "fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]')" "$canary")" -eq 1 ] \
  && [ "$(grep -cF "fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')" "$canary")" -eq 1 ] \
  && grep -qF "fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')" "$planner" \
  && pass "new jobs use the canonical portable organization lane boundary" \
  || fail "runner selector bypasses the canonical lane boundary"
grep -q 'dd08f8471fdfabbdbbb32051e03387fcf5df63bd' "$planner" \
  && grep -q 'scripts/plan-compatibility.mjs' "$planner" \
  && grep -q 'then \. else error("invalid candidates") end' "$planner" \
  && ! grep -Eq 'contents: write|pull-requests: write|issues: write' "$planner" \
  && pass "planner consumes the immutable prerequisite without mutation authority" \
  || fail "planner contract or authority drifted"
grep -q 'token: \${{ steps.compatibility-app-token.outputs.token }}' "$planner" \
  && grep -q 'RENOVATE_COMPATIBILITY_CLIENT_ID' "$planner" \
  && grep -q 'RENOVATE_COMPATIBILITY_APP_PRIVATE_KEY' "$planner" \
  && ! grep -q 'RENOVATE_COMPATIBILITY_PAT\|ORG_ADMIN_TOKEN' "$planner" \
  && pass "planner uses only the dedicated compatibility App token" \
  || fail "planner App authentication drifted"

sha=0123456789abcdef0123456789abcdef01234567
caller="$(bash "$generator" "$sha" typescript 7.1.0 node-jest-ts-jest)" || fail "caller generation failed"
grep -q "renovate-compatibility-canary.yml@$sha" <<<"$caller" \
  && grep -q '^  contents: read$' <<<"$caller" \
  && ! grep -Eq 'secrets: inherit|write' <<<"$caller" \
  && pass "generated caller is immutable and read-only" || fail "generated caller contract drifted"
bash "$generator" "$sha" 'bad;echo pwned' 7.1.0 node-jest-ts-jest >/dev/null 2>&1 \
  && fail "generator accepted package injection" || pass "generator rejects package injection"
# Each value is quoted into the caller's YAML, so a stray quote would end the
# string and inject keys. The validation fails before anything is emitted (#1995).
expect_generator_rejects() { # <label> <candidate> <profile>
  local output code
  output="$(bash "$generator" "$sha" typescript "$2" "$3" 2>/dev/null)"
  code=$?
  [ "$code" = 1 ] && [ -z "$output" ] && pass "$1" || fail "$1: exit $code, emitted ${#output} bytes"
}
expect_generator_rejects "generator rejects candidate injection" "7.1.0'"$'\n'"      secrets: inherit #" node-jest-ts-jest
expect_generator_rejects "generator rejects stack-profile injection" 7.1.0 "node-jest'"$'\n'"      secrets: inherit #"

if [ "$fails" -gt 0 ]; then exit 1; fi
echo "All tests passed."
