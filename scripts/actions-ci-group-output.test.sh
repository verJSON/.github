#!/usr/bin/env bash
# Passing contracts must not annotate the parent check (#1735), and a command
# that exceeds an explicit budget fails the group (#1736).
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
runner="$root/scripts/actions-ci-group.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

cat >"$tmp/manifest.tsv" <<'EOF'
platform	printf '%s\n' '::error::fixture failed' '::warning file=x::fixture warned' '::notice::fixture noted' '::stop-commands::token' '::add-mask::super-secret-value'
EOF

if ACTIONS_CI_GROUP_MANIFEST="$tmp/manifest.tsv" bash "$runner" platform >"$tmp/out" 2>"$tmp/err"; then
  if grep -qE '^::(error|warning|notice|stop-commands|add-mask)(:| )' "$tmp/out" \
    || grep -qE '^::(error|warning|notice|stop-commands|add-mask)(:| )' "$tmp/err"; then
    fail "a passing contract still emitted a workflow command"
  elif grep -qE 'workflow-command:add-mask.*super-secret-value|workflow-command:stop-commands.*token' "$tmp/out"; then
    fail "masking an add-mask or stop-commands line published its value"
  elif grep -q 'workflow-command:error' "$tmp/out" \
    && grep -q 'fixture failed' "$tmp/out" \
    && grep -q 'workflow-command:warning' "$tmp/out" \
    && grep -q 'workflow-command:notice' "$tmp/out" \
    && grep -q 'workflow-command:stop-commands' "$tmp/out" \
    && grep -q 'workflow-command:add-mask' "$tmp/out"; then
    pass "passing contracts keep their text and do not annotate the check"
  else
    fail "masked output dropped the fixture text"
  fi
else
  fail "the group runner rejected a passing contract that prints workflow commands"
fi

cat >"$tmp/slow.tsv" <<'EOF'
platform	sleep 1
EOF

if ACTIONS_CI_COMMAND_BUDGET_SECONDS=0 ACTIONS_CI_GROUP_MANIFEST="$tmp/slow.tsv" \
  bash "$runner" platform >"$tmp/slow.out" 2>"$tmp/slow.err"; then
  fail "a command over the budget was accepted"
elif grep -q 'budget=0' "$tmp/slow.out"; then
  pass "a command over its budget fails the group"
else
  fail "a budget overrun was not reported"
fi

cat >"$tmp/fast.tsv" <<'EOF'
platform	true
EOF

if ACTIONS_CI_COMMAND_BUDGET_SECONDS=60 ACTIONS_CI_GROUP_MANIFEST="$tmp/fast.tsv" \
  bash "$runner" platform >"$tmp/fast.out" 2>"$tmp/fast.err"; then
  pass "a command inside its budget passes"
else
  fail "a command inside its budget failed the group"
fi

[ "$fails" -eq 0 ] || exit 1
echo "All tests passed."
