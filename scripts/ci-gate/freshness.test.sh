#!/usr/bin/env bash
# Tests the merge gate's `freshness` step (Verjson/.github#41, ADR 0008) by
# extracting the exact `run:` block from ai-review-merge.yml — the single source
# of truth, so the test can't drift from the shipped logic — and exercising it
# against a stubbed `gh`. Guards the org-critical fail-open / fail-closed
# behavior: a regression that starts merging conflicting branches or wedges the
# gate must fail here before it reaches every repo. Plain bash + awk + jq; no
# test-framework or YAML-library dependency (runs on the bare self-hosted pool).
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="$repo_root/.github/workflows/ai-review-merge.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() {
  printf 'FAIL - %s\n' "$1"
  fails=$((fails + 1))
}

# Extract the freshness step's run script verbatim (10-space-indented body after
# `run: |`, scoped to the step whose `id:` is freshness).
script="$tmp/freshness.sh"
awk '
  $0 == "        id: freshness" { seen = 1 }
  seen && $0 == "        run: |" { cap = 1; next }
  cap {
    if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
    if ($0 ~ /^[ \t]*$/) { print ""; next }
    # Stop at the first dedented line. `seen` latches, so merely clearing `cap`
    # here let every LATER `run: |` block in the workflow re-arm capture and
    # append itself — the extracted "freshness step" was every run block in the
    # workflow concatenated, so an exit status asserted here could belong to
    # unrelated trailing code rather than to freshness.
    exit
  }
' "$wf" >"$script"
if ! grep -q 'update-branch' "$script" || ! grep -q 'behind_by' "$script"; then
  echo "FAIL - could not extract the freshness run block from $wf"
  exit 1
fi

# Fake gh: dispatches on args, driven by env fixtures, logging side effects.
mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  printf '%s ' "$@" | grep -- '--json comments' >/dev/null && { cat "$COMMENTS_FILE" 2>/dev/null; exit 0; }
  [ "${PRVIEW_FAIL:-0}" = "1" ] && exit 1
 if [ "${MERGEABLE_FAIL:-0}" = "1" ] && printf '%s ' "$@" | grep -- '--json mergeable' >/dev/null; then
   exit 1
 fi
  # After update-branch runs, return the post-update mergeable so a CONFLICTING
  # branch whose conflict was mere base drift reads as cleared on the re-check.
  if [ -n "${POST_UPDATE_MERGEABLE:-}" ] && grep -q UPDATE "$ACTIONLOG" 2>/dev/null; then
    jq -c --arg m "$POST_UPDATE_MERGEABLE" '.mergeable=$m' "$FIXTURE"; exit 0
  fi
  cat "$FIXTURE"; exit 0
fi
[ "$1" = "pr" ] && [ "$2" = "comment" ] && { echo COMMENT >>"$ACTIONLOG"; exit 0; }
if [ "$1" = "api" ]; then
  case "$*" in
    *compare*)
      echo COMPARE >>"$ACTIONLOG"
      # COMPARE_MODE drives the indeterminate shapes #1476 is about. `ok` is a
      # genuine answer; the rest must stay distinguishable from `behind=0`.
      # `recovers` fails COMPARE_FAILURES times, then answers — so a test can
      # tell a real bounded retry from a blanket swallow.
      case "${COMPARE_MODE:-ok}" in
        error) exit 1 ;;
        empty) exit 0 ;;
        unparseable) echo "<!DOCTYPE html>"; exit 0 ;;
        recovers)
          n=$(grep -c COMPARE "$ACTIONLOG")
          [ "$n" -le "${COMPARE_FAILURES:-2}" ] && exit 1
          echo "${BEHIND_BY:-0}"; exit 0 ;;
        *) echo "${BEHIND_BY:-0}"; exit 0 ;;
      esac ;;
    *update-branch*) echo UPDATE >>"$ACTIONLOG"; exit "${UPDATE_RC:-0}" ;;
  esac
fi
exit 0
GH
chmod +x "$tmp/bin/gh"

run_case() {
  # run_case <pr-json-fixture> [existing-comments]
  export PATH="$tmp/bin:$PATH" TARGET_REPO="Verjson/foo" PR_NUMBER=7
  export FIXTURE="$tmp/fix.json" GITHUB_OUTPUT="$tmp/out.txt" ACTIONLOG="$tmp/act.log" COMMENTS_FILE="$tmp/c.txt"
  : >"$GITHUB_OUTPUT"
  : >"$ACTIONLOG"
  printf '%s' "${2:-}" >"$COMMENTS_FILE"
  printf '%s' "$1" >"$FIXTURE"
  # Keep the hold's own output. Every freshness refusal exits 1, so the
  # reason line is what names the guard under test.
  bash -eo pipefail "$script" >"$tmp/err.txt" 2>&1
  echo "rc=$?"
}
out_has() { grep -q "$1" "$tmp/out.txt"; }
act_has() { grep -q "$1" "$tmp/act.log"; }
reason_has() { grep -qF -- "$1" "$tmp/err.txt"; }
conflict_reason='::error::Branch conflicts with main — holding until resolved.'
metadata_reason='::error::Could not read PR metadata after 3 attempts, so freshness could not be evaluated at all. Holding rather than merging on a base we could not check.'
mergeable_reason='::error::Could not read PR mergeability after bounded retries; holding rather than proceeding on an unverified state.'
compare_reason='::error::Could not determine whether the head is behind main: the compare API was unanswerable after 3 attempts. Holding rather than merging on a possibly stale base.'

H='{"author":{"login":"human"},"headRefName":"feat/x","headRefOid":"abc","baseRefName":"main","mergeable":"MERGEABLE"}'

BEHIND_BY=5 run_case '{"author":{"login":"renovate[bot]"},"headRefName":"renovate/x","headRefOid":"s","baseRefName":"main","mergeable":"MERGEABLE"}' >/dev/null
{ out_has 'proceed=true' && ! act_has UPDATE; } && pass "renovate author is skipped (no update)" || fail "renovate author not skipped"

BEHIND_BY=5 run_case '{"author":{"login":"someone"},"headRefName":"renovate/x","headRefOid":"s","baseRefName":"main","mergeable":"MERGEABLE"}' >/dev/null
{ out_has 'proceed=true' && ! act_has UPDATE; } && pass "renovate/ branch prefix is skipped" || fail "renovate branch prefix not skipped"

BEHIND_BY=3 run_case '{"author":{"login":"human"},"headRefName":"feat/renovate-cleanup","headRefOid":"s","baseRefName":"main","mergeable":"MERGEABLE"}' >/dev/null
{ act_has UPDATE && out_has 'proceed=false'; } && pass "human branch merely containing 'renovate' is NOT skipped" || fail "over-matched renovate on a human branch"

rc=$(BEHIND_BY=0 run_case '{"author":{"login":"human"},"headRefName":"feat/x","headRefOid":"s","baseRefName":"main","mergeable":"CONFLICTING"}')
{ [ "$rc" = "rc=1" ] && act_has COMMENT && reason_has "$conflict_reason"; } && pass "conflict holds (exit 1) and comments" || fail "conflict path wrong ($rc)"

rc=$(BEHIND_BY=0 run_case '{"author":{"login":"human"},"headRefName":"feat/x","headRefOid":"s","baseRefName":"main","mergeable":"CONFLICTING"}' '{"comments":[{"body":"<!-- gate:branch-conflict --> old"}]}')
{ [ "$rc" = "rc=1" ] && ! act_has COMMENT && reason_has "$conflict_reason"; } && pass "conflict comment is deduped" || fail "conflict comment not deduped"

# (a) CONFLICTING from mere base drift → update-branch clears it → step aside
# like the BEHIND success path (proceed=false), no conflict comment.
rc=$(POST_UPDATE_MERGEABLE=MERGEABLE BEHIND_BY=0 run_case '{"author":{"login":"human"},"headRefName":"feat/x","headRefOid":"s","baseRefName":"main","mergeable":"CONFLICTING"}')
{ [ "$rc" = "rc=0" ] && act_has UPDATE && out_has 'proceed=false' && ! act_has COMMENT; } \
  && pass "conflict cleared by update-branch → proceed=false, no comment" || fail "conflict-then-update path wrong ($rc)"

# (b) CONFLICTING + update-branch API fails → hold (exit 1) + conflict comment.
rc=$(UPDATE_RC=1 BEHIND_BY=0 run_case '{"author":{"login":"human"},"headRefName":"feat/x","headRefOid":"s","baseRefName":"main","mergeable":"CONFLICTING"}')
{ [ "$rc" = "rc=1" ] && act_has COMMENT && reason_has "$conflict_reason"; } && pass "conflict + failed update-branch holds + comments" || fail "failed-update conflict path wrong ($rc)"

# (c) CONFLICTING + update-branch "succeeds" but branch is STILL CONFLICTING
# (genuine content conflict) → hold (exit 1) + conflict comment.
rc=$(POST_UPDATE_MERGEABLE=CONFLICTING BEHIND_BY=0 run_case '{"author":{"login":"human"},"headRefName":"feat/x","headRefOid":"s","baseRefName":"main","mergeable":"CONFLICTING"}')
{ [ "$rc" = "rc=1" ] && act_has UPDATE && act_has COMMENT && reason_has "$conflict_reason"; } && pass "genuine conflict survives update-branch → holds + comments" || fail "genuine-conflict path wrong ($rc)"

# (d) Renovate CONFLICTING branch → still exempt: no update attempt, no comment.
POST_UPDATE_MERGEABLE=MERGEABLE BEHIND_BY=0 run_case '{"author":{"login":"renovate[bot]"},"headRefName":"renovate/x","headRefOid":"s","baseRefName":"main","mergeable":"CONFLICTING"}' >/dev/null
{ out_has 'proceed=true' && ! act_has UPDATE && ! act_has COMMENT; } && pass "renovate conflict left to Renovate (no update, no comment)" || fail "renovate conflict not exempt"

BEHIND_BY=4 run_case "$H" >/dev/null
{ act_has UPDATE && out_has 'proceed=false'; } && pass "behind (clean) updates branch, proceed=false" || fail "behind path wrong"

BEHIND_BY=0 run_case "$H" >/dev/null
{ out_has 'proceed=true' && ! act_has UPDATE; } && pass "up-to-date proceeds, no update" || fail "up-to-date path wrong"

export PRVIEW_FAIL=1
rc=$(run_case "$H")
unset PRVIEW_FAIL
# Verjson/.github#1496 — an unreadable PR metadata read is indeterminate, not
# permissive. This used to emit a ::warning:: and proceed=true, skipping the
# freshness evaluation entirely. That mattered more than it looks: this read
# sits EARLIER in the step than the compare guard below, so a correlated
# outage tripped it first and the compare guard was never reached — the guard
# #1476 added was unreachable on exactly the total-outage path it exists for.
{ [ "$rc" = "rc=1" ] && out_has 'proceed=false' && ! out_has 'proceed=true' && reason_has "$metadata_reason"; } \
  && pass "unreadable PR metadata holds (proceed=false, exit 1)" \
  || fail "unreadable PR metadata did not hold ($rc)"

# Verjson/.github#1476 — an indeterminate compare answer must never decode to a
# genuine `behind=0`. Each shape below reached `proceed=true` before this fix:
# the request failing, an empty body, and an unparseable body all collapsed
# through `|| echo 0` / `|| behind=0` into "not behind", so the gate merged on a
# review taken against a possibly stale base. These assert the fail-CLOSED
# direction: no proceed=true, no merge, and a nonzero exit the human can see.
for mode in error empty unparseable; do
  rc=$(COMPARE_MODE="$mode" run_case "$H")
  { [ "$rc" = "rc=1" ] && ! out_has 'proceed=true' && ! act_has UPDATE && reason_has "$compare_reason"; } \
    && pass "indeterminate compare ($mode) holds instead of reading as behind=0" \
    || fail "indeterminate compare ($mode) did not hold ($rc)"
done

# The hold is reached only after a bounded retry, so a single transient blip
# does not wedge the gate: two failures then a genuine 0 still proceeds.
COMPARE_MODE=recovers COMPARE_FAILURES=2 BEHIND_BY=0 run_case "$H" >/dev/null
{ out_has 'proceed=true' && ! act_has UPDATE; } \
  && pass "transient compare blip recovers on retry, then proceeds" \
  || fail "bounded retry did not recover a transient compare blip"

# ...and the retry is genuinely re-asking AND genuinely bounded. Assert the
# exact attempt count in both directions: `-gt 1` would be satisfied by an
# unbounded loop, so it pins only the floor and a raised ceiling slips through.
COMPARE_MODE=error run_case "$H" >/dev/null
[ "$(grep -c COMPARE "$tmp/act.log")" -eq 3 ] \
  && pass "an unanswerable compare is asked exactly 3 times, then holds" \
  || fail "compare attempt count is not exactly 3 ($(grep -c COMPARE "$tmp/act.log"))"

# A recovered blip must stop asking the moment it gets an answer.
COMPARE_MODE=recovers COMPARE_FAILURES=1 BEHIND_BY=0 run_case "$H" >/dev/null
[ "$(grep -c COMPARE "$tmp/act.log")" -eq 2 ] \
  && pass "the retry stops as soon as the compare answers" \
  || fail "retry did not stop on the first answer ($(grep -c COMPARE "$tmp/act.log"))"

# A mergeability read through pr_json must not become `{}` and then UNKNOWN
# that proceeds through the freshness gate. An unreadable or permanently
# UNKNOWN state holds the PR after the bounded retries.
rc=$(MERGEABLE_FAIL=1 run_case '{"author":{"login":"human"},"headRefName":"feat/x","headRefOid":"s","baseRefName":"main","mergeable":"UNKNOWN"}')
[ "$rc" = "rc=1" ] && ! out_has 'proceed=true' && reason_has "$mergeable_reason" \
  && pass "unreadable mergeability holds instead of proceeding" \
  || fail "unreadable mergeability did not hold"

rc=$(run_case '{"author":{"login":"human"},"headRefName":"feat/x","headRefOid":"s","baseRefName":"main","mergeable":"UNKNOWN"}')
[ "$rc" = "rc=1" ] && ! out_has 'proceed=true' && reason_has "$mergeable_reason" \
  && pass "persistently unknown mergeability holds instead of proceeding" \
  || fail "persistently unknown mergeability did not hold"

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
else
  echo "$fails test(s) failed."
  exit 1
fi
