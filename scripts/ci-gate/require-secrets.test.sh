#!/usr/bin/env bash
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
review_workflow="$repo_root/.github/workflows/ai-review-merge.yml"
privileged_workflow="$repo_root/.github/workflows/ai-privileged-merge.yml"
terminal_helper="$repo_root/scripts/ci-gate/terminal-merge.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
failures=0

pass() { printf 'ok - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; failures=$((failures + 1)); }

job() {
  local workflow="$1" name="$2"
  awk -v wanted="$name" '
    /^jobs:$/ { in_jobs=1; next }
    in_jobs && $0 ~ "^  " wanted ":$" { found=1; print; next }
    found && /^  [A-Za-z0-9_-]+:$/ { exit }
    found { print }
  ' "$workflow"
}

step() {
  local workflow="$1" job_name="$2" step_name="$3" block
  # Captured rather than piped, and captured in its own statement: a here-string
  # cannot report the producer's status, so reading `job` inline would discard
  # the failure this file exists to notice.
  block="$(job "$workflow" "$job_name")" || return "$?"
  awk -v wanted="$step_name" '
    $0 == "      - name: " wanted { found=1; print; next }
    found && /^      - name:/ { exit }
    found { print }
  ' <<<"$block"
}

contract_errors() {
  local review="$1" privileged="$2" guard terminal checkout merge_step
  guard="$(job "$privileged" invalid_verjson_route)"
  terminal="$(job "$privileged" privileged_merge)"
  checkout="$(step "$privileged" privileged_merge 'Check out immutable arm verifier')"

  ! grep -qF '${{ secrets.ORG_ADMIN_TOKEN }}' "$review" \
    || printf '%s\n' 'review workflow receives ORG_ADMIN_TOKEN'
  ! grep -qF 'ORG_ADMIN_TOKEN' "$privileged" \
    || printf '%s\n' 'privileged workflow still consumes ORG_ADMIN_TOKEN'
  ! grep -qE 'needs\..*outputs|resolve_privileged_route' <<<"$terminal" \
    || printf '%s\n' 'runner-produced data can select terminal credential placement'
  ! grep -qF 'ACTIONS_VARIABLES_TOKEN' "$privileged" \
    || printf '%s\n' 'privileged workflow still depends on an organization-variable PAT'
  ! grep -qE 'secrets\.|ORG_ADMIN_TOKEN|GH_TOKEN:' <<<"$guard" \
    || printf '%s\n' 'invalid-route observability guard receives a terminal credential'
  grep -qF 'permissions: {}' <<<"$guard" \
    || printf '%s\n' 'invalid-route observability guard is not credentialless'
  ! grep -qE 'merge-app-token|MERGE_APP_PRIVATE_KEY|actions/create-github-app-token|gh pr merge' <<<"$terminal" \
    || printf '%s\n' 'retired terminal job contains a merge credential or operation'
  grep -qF 'Hold autonomous terminal merge' <<<"$terminal" \
    && grep -qF 'exit 1' "$terminal_helper" \
    && ! grep -qF 'gh pr merge' "$terminal_helper" \
    || printf '%s\n' 'terminal merge hold is missing'
  grep -qF 'uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1' <<<"$checkout" \
    || printf '%s\n' 'immutable verifier checkout is not pinned'
  grep -qF 'repository: Verjson/.github' <<<"$checkout" \
    || printf '%s\n' 'verifier checkout is not restricted to the canonical repository'
  grep -qF 'ref: ${{ steps.trusted-revision.outputs.sha }}' <<<"$checkout" \
    || printf '%s\n' 'verifier checkout is not bound to the executing trusted revision'
  grep -qF 'persist-credentials: false' <<<"$checkout" \
    || printf '%s\n' 'verifier checkout persists credentials'
  grep -qF 'scripts/ci-gate/verify-arm-receipt.sh' <<<"$checkout" \
    || printf '%s\n' 'verifier checkout is not sparse-scoped to the arm verifier'
  [ "$(grep -cE '^[[:space:]]+(- )?uses:' <<<"$terminal")" -eq 1 ] \
    || printf '%s\n' 'terminal merge job action boundary drifted'
  ! grep -qE 'github\.event\.pull_request|github\.head_ref|repository:.*TARGET_REPO|ref:.*EXPECTED_HEAD_SHA' <<<"$terminal" \
    || printf '%s\n' 'terminal merge job can check out PR-controlled material'
}

assert_contract() {
  local review="$1" privileged="$2" description="$3" errors
  errors="$(contract_errors "$review" "$privileged")"
  if [ -z "$errors" ]; then pass "$description"; else printf '  %s\n' "$errors"; fail "$description"; fi
}

assert_mutation_rejected() {
  local description="$1" errors
  errors="$(contract_errors "$tmp/review.yml" "$tmp/privileged.yml")"
  if [ -n "$errors" ]; then pass "$description"; else fail "$description"; fi
}

reset_fixtures() {
  cp "$review_workflow" "$tmp/review.yml"
  cp "$privileged_workflow" "$tmp/privileged.yml"
}

assert_contract "$review_workflow" "$privileged_workflow" \
  'retired terminal job has no merge token or mutation'

reset_fixtures
sed -i '/^permissions:$/i\\  ORG_ADMIN_TOKEN: ${{ secrets.ORG_ADMIN_TOKEN }}' "$tmp/review.yml"
assert_mutation_rejected 'mutation: review workflow cannot receive ORG_ADMIN_TOKEN'

reset_fixtures
sed -i '/^  privileged_merge:$/a\\    needs: attacker_route\n    runs-on: ${{ needs.attacker_route.outputs.selector }}' "$tmp/privileged.yml"
assert_mutation_rejected 'mutation: runner-produced output cannot route the terminal credential'

reset_fixtures
sed -i '/^  invalid_verjson_route:$/a\\    env:\n      GH_TOKEN: ${{ secrets.MERGE_APP_PRIVATE_KEY }}' "$tmp/privileged.yml"
assert_mutation_rejected 'mutation: invalid-route observability cannot receive the terminal credential'

reset_fixtures
sed -i '0,/persist-credentials: false/s//persist-credentials: true/' "$tmp/privileged.yml"
assert_mutation_rejected 'mutation: immutable verifier checkout cannot persist credentials'

reset_fixtures
sed -i '0,/repository: Verjson\/.github/s//repository: ${{ github.repository }}/' "$tmp/privileged.yml"
assert_mutation_rejected 'mutation: verifier cannot be checked out from the target repository'

reset_fixtures
sed -i '0,/ref: ${{ steps.trusted-revision.outputs.sha }}/s//ref: ${{ inputs.expected_head_sha }}/' "$tmp/privileged.yml"
assert_mutation_rejected 'mutation: verifier cannot be checked out from the PR head'

reset_fixtures
sed -i '/^      - name: Authorize terminal merge from trusted metadata$/i\\      - uses: attacker/pr-controlled-action@main' "$tmp/privileged.yml"
assert_mutation_rejected 'mutation: terminal merge job cannot execute an additional action'

if [ "$failures" -ne 0 ]; then
  printf '%d test(s) failed.\n' "$failures" >&2
  exit 1
fi
printf 'All require-secrets tests passed.\n'
