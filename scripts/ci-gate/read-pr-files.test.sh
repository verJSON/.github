#!/usr/bin/env bash
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
helper="$here/read-pr-files.sh"
root="$(cd "$here/../.." && pwd)"
workflow="$root/.github/workflows/ai-review-merge.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

cat >"$tmp/gh" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
count=0
[ ! -f "$CALL_COUNT" ] || count="$(cat "$CALL_COUNT")"
count=$((count + 1))
printf '%s\n' "$count" >"$CALL_COUNT"
printf '%s\n' "$*" >>"$CALL_ARGS"

if [ "$MODE" = persistent ] || { [ "$MODE" = transient ] && [ "$count" -eq 1 ]; }; then
  echo 'gh: Server Error: diff temporarily unavailable (HTTP 500)' >&2
  exit 1
fi

printf '%s\n' \
  '{"filename":"src/first.py","changes":2}' \
  '{"filename":"src/second.py","changes":3}'
SH
chmod +x "$tmp/gh"

run_helper() {
  local mode="$1"
  : >"$tmp/count"
  : >"$tmp/args"
  MODE="$mode" CALL_COUNT="$tmp/count" CALL_ARGS="$tmp/args" \
    PATH="$tmp:$PATH" bash "$helper" Verjson/example 77 \
    >"$tmp/output" 2>"$tmp/diagnostics"
}

if run_helper transient \
  && [ "$(cat "$tmp/count")" -eq 2 ] \
  && jq -e 'length == 2 and .[0].filename == "src/first.py" and .[1].changes == 3' "$tmp/output" >/dev/null \
  && grep -qF 'status=retry attempt=1/4' "$tmp/diagnostics" \
  && grep -qF 'status=recovered attempt=2/4' "$tmp/diagnostics" \
  && [ "$(grep -cF -- '--paginate --jq .[]' "$tmp/args")" -eq 2 ]; then
  pass "transient HTTP 500 retries immediately and preserves paginated JSON aggregation"
else
  fail "transient PR-files failure did not recover deterministically"
fi

if run_helper persistent; then
  fail "persistent PR-files failure became reviewable input"
elif [ "$(cat "$tmp/count")" -eq 4 ] \
  && grep -qF 'status=retry attempt=1/4' "$tmp/diagnostics" \
  && grep -qF 'status=retry attempt=2/4' "$tmp/diagnostics" \
  && grep -qF 'status=retry attempt=3/4' "$tmp/diagnostics" \
  && grep -qF 'status=infrastructure_unavailable attempts=4; no review pass reserved' "$tmp/diagnostics" \
  && ! grep -qF 'diff temporarily unavailable' "$tmp/diagnostics"; then
  pass "persistent failure exhausts bounded attempts with concise fail-closed diagnostics"
else
  fail "persistent PR-files failure lost its bounded fail-closed contract"
fi

helper_line="$(grep -nF 'bash "$PR_FILES_READER" "$REPO" "$PR_NUMBER"' "$workflow" | cut -d: -f1)"
reservation_line="$(grep -nF 'name: Reserve cumulative AI review pass 1' "$workflow" | cut -d: -f1)"
if [ -n "$helper_line" ] && [ -n "$reservation_line" ] \
  && [ "$helper_line" -lt "$reservation_line" ] \
  && grep -qF 'PR_FILES_READER: .gate-policy/scripts/ci-gate/read-pr-files.sh' "$workflow" \
  && ! grep -qE '(^|[[:space:]])sleep([[:space:]]|$)' "$helper"; then
  pass "trusted preflight helper runs before reservations without runner sleeps"
else
  fail "PR-files recovery escaped preflight or introduced runner waiting"
fi

sed 's/max_attempts=4/max_attempts=1/' "$helper" >"$tmp/mutated-helper.sh"
: >"$tmp/count"
: >"$tmp/args"
if MODE=transient CALL_COUNT="$tmp/count" CALL_ARGS="$tmp/args" \
  PATH="$tmp:$PATH" bash "$tmp/mutated-helper.sh" Verjson/example 77 >/dev/null 2>"$tmp/mutant-diagnostics"; then
  fail "mutation survived: one-shot PR-files read recovered from a transient failure"
elif grep -qF 'status=infrastructure_unavailable attempts=1; no review pass reserved' "$tmp/mutant-diagnostics"; then
  pass "mutation rejected: transient recovery requires the bounded retry"
else
  fail "one-shot mutant failed without exhausting its single attempt: $(tail -1 "$tmp/mutant-diagnostics")"
fi

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
