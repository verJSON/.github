#!/usr/bin/env bash
# Pins the merge gate's cross-org `workflow_call` distribution seams
# (Verjson/.github#128, ADR 0022). The gate is the org's required merge check AND
# a reusable other orgs pin via `uses: …@v1`; a refactor that silently drops the
# reusable trigger, un-parameterizes the runner, or breaks the org direct path
# reaches every consumer. These are structural (`on:`/`inputs:`/`runs-on:`)
# invariants, not `run:` shell, so this asserts the YAML shape directly rather
# than extracting a block. Plain bash + awk/grep; no YAML-library or
# test-framework dependency (runs on the bare self-hosted pool).
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="$repo_root/.github/workflows/ai-review-merge.yml"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() {
  printf 'FAIL - %s\n' "$1"
  fails=$((fails + 1))
}

# Extract only the top-level `on:` mapping (from `on:` up to the next
# zero-indented key) so trigger assertions can't be fooled by the same words
# appearing in a comment or a job body further down.
on_block="$(awk '
  $0 == "on:" { cap = 1; print; next }
  cap && /^[A-Za-z]/ { exit }   # next top-level key ends the on: block
  cap { print }
' "$wf")"

# (a) Automatic PR events are admitted only by the trusted arm. Restoring this
# trigger would bypass head deduplication and repay the model on duplicate events.
if grep -qE '^  pull_request(_target)?:' <<<"$on_block"; then
  fail "model workflow has an automatic PR trigger outside the trusted arm"
else
  pass "model workflow is dispatch/call-only after trusted head deduplication"
fi

# (b) Operator re-gate path preserved.
grep -qE '^  workflow_dispatch:' <<<"$on_block" \
  && pass "workflow_dispatch trigger retained (operator re-gate)" \
  || fail "workflow_dispatch trigger missing"

# (c) Cross-org distribution: the reusable `workflow_call` trigger must exist.
grep -qE '^  workflow_call:' <<<"$on_block" \
  && pass "workflow_call trigger present (cross-org consumers can pin it)" \
  || fail "workflow_call trigger missing — cross-org consumers would have to hand-copy"

# (d) `runner_labels` input is declared under workflow_call so a consumer with a
# different fleet can parameterize runs-on instead of forking the file.
wc_block="$(awk '
  $0 == "  workflow_call:" { cap = 1; next }
  cap && /^[A-Za-z]/ { exit }   # workflow_call is the last trigger — the next
  cap { print }                 # top-level key (concurrency:) ends the block
' "$wf")"
grep -qE '^      runner_labels:' <<<"$wc_block" \
  && pass "workflow_call declares a runner_labels input" \
  || fail "workflow_call is missing the runner_labels input (fleet not parameterizable)"

for identity in expected_head_sha authorization_check_id arm_run_id arm_run_attempt; do
  grep -qE "^      ${identity}:" <<<"$wc_block" \
    || fail "workflow_call is missing receipt identity input $identity"
done

# (d2) runner_labels must be OPTIONAL under workflow_call (#405). It was
# required because of #130: omitting it left the job queued forever on labels the
# consumer's org has no runner for, so rejecting the call was the only fast-fail.
# That premise is gone — every `runs-on` here ends at
# `CI_LANE_FALLBACK || '["ubuntu-24.04"]'` (ADR 0040), so an omitted input
# resolves to a runner that exists. Keeping it required had a cost the #130
# analysis did not price: it forced every caller to spell out a fleet LABEL, and
# the generator supplied a Verjson one, putting a label the org relabels at will
# into ~90 repositories it would then have to open a pull request against.
awk '
  $0 == "      runner_labels:" { cap = 1; next }
  cap && /^      [A-Za-z]/ { exit }   # next input key ends this input block
  cap { print }
' <<<"$wc_block" | grep -E '^        required: false' >/dev/null \
  && pass "runner_labels is optional under workflow_call (an omitted fleet routes by lane, #405)" \
  || fail "runner_labels is required — every consumer must then name a fleet label the org cannot relabel (#405)"

# (e) Every gate job's runs-on prefers inputs.runner_labels before the org
# fallback — so a consumer's fleet actually takes effect. All three jobs
# (preflight, gate, dispatch-merge) must carry the same first term, which
# honors the input off Verjson and for a private Verjson repository only: a
# public Verjson caller cannot name the persistent pool (ADR 0235).
runs_on_parameterized="$(grep -cF "runs-on: \${{ (github.repository_owner != 'Verjson' || github.event.repository.private == true) && inputs.runner_labels && fromJSON(inputs.runner_labels) ||" "$wf")"
[ "${runs_on_parameterized:-0}" -eq 3 ] \
  && pass "all three gate jobs prefer inputs.runner_labels then fall back to the org pool" \
  || fail "runs-on is not runner_labels-parameterized on all three jobs (got ${runs_on_parameterized:-0}/3)"

# (f) Verjson gate jobs expose independent default/untrusted variables while
# both keep the compatible general fallback during the permissive exception.
# Non-Verjson consumers retain the hosted fallback and explicit fleet input.
# ADR 0235 sends every public target to the ephemeral untrusted lane, so each
# of the four relay jobs ends in it; a private target keeps its own lane. What must hold regardless: the cross-org hosted
# route survives, every lane is still variable-selected rather than hardcoded,
# and CI_LANE_FALLBACK remains the terminal lane term everywhere.
grep -qE "github\.repository_owner != 'Verjson' && 'ubuntu-24\.04'" "$wf" \
  && [ "$(grep -cF 'CI_RUNNER_FASTLANE' "$wf")" -ge 1 ] \
  && [ "$(grep -cF "|| fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') }}" "$wf")" -eq 4 ] \
  && [ "$(grep -cF 'CI_LANE_PRIVILEGED' "$wf")" -ge 1 ] \
  && [ "$(grep -cF 'CI_LANE_TRUSTED' "$wf")" -ge 1 ] \
  && [ "$(grep -cF 'CI_LANE_UNTRUSTED' "$wf")" -ge 1 ] \
  && [ "$(grep -cF 'CI_LANE_FALLBACK' "$wf")" -ge 3 ] \
  && pass "Verjson gate jobs use variable lanes while cross-org routing stays portable" \
  || fail "variable gate routing or cross-org portability drifted"

# (g) Reusable callers are repository-local. Cross-org portability is retained,
# but neither workflow_call nor dispatch may smuggle in a sibling target.
if ! grep -qE '^      repository:' <<<"$wc_block" \
   && grep -qF 'TARGET_REPO: ${{ github.repository }}' "$wf"; then
  pass "workflow_call is portable but repository-local"
else
  fail "workflow_call accepts a cross-repository target or TARGET_REPO drifted"
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
else
  echo "$fails test(s) failed."
  exit 1
fi
