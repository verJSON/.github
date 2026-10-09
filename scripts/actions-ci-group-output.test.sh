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
platform	test -n "$CHANGELOG_CALLER_CONTRACT_CACHE" && test -d "$CHANGELOG_CALLER_CONTRACT_CACHE" && printf 'shared caller-contract cache is available\n'
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
if grep -q 'shared caller-contract cache is available' "$tmp/out"; then
  pass "manifest commands share a temporary generated-contract cache"
else
  fail "the group runner did not provide its generated-contract cache"
fi

printf 'platform\tsleep 5; touch %q\n' "$tmp/finished-after-timeout" >"$tmp/slow.tsv"

if ACTIONS_CI_COMMAND_BUDGET_SECONDS=1 ACTIONS_CI_GROUP_MANIFEST="$tmp/slow.tsv" \
  bash "$runner" platform >"$tmp/slow.out" 2>"$tmp/slow.err"; then
  fail "a command over the budget was accepted"
elif grep -q 'budget=1' "$tmp/slow.out" && [ ! -e "$tmp/finished-after-timeout" ]; then
  pass "a command over its budget is terminated and fails the group"
else
  fail "a budget overrun was not terminated and reported"
fi

cat >"$tmp/fast.tsv" <<'EOF'
platform	true
EOF

if ACTIONS_CI_COMMAND_BUDGET_SECONDS=60 ACTIONS_CI_GROUP_MANIFEST="$tmp/fast.tsv" \
  bash "$runner" platform >"$tmp/fast.out" 2>"$tmp/fast.err"; then
  if grep -Eq 'actions-ci command group=platform status=0 elapsed=[0-9]+s command=' "$tmp/fast.out"; then
    pass "a command inside its budget passes and reports its elapsed time"
  else
    fail "a passing command did not report its elapsed time"
  fi
else
  fail "a command inside its budget failed the group"
fi

mkdir -p "$tmp/single-bin"
cat >"$tmp/single-bin/nproc" <<'EOF'
#!/usr/bin/env bash
printf '1\n'
EOF
chmod +x "$tmp/single-bin/nproc"
cat >"$tmp/worker-dies.tsv" <<'EOF'
platform	true
platform	kill -KILL "$PPID"
EOF

if ACTIONS_CI_COMMAND_BUDGET_SECONDS='' \
  ACTIONS_CI_GROUP_MANIFEST="$tmp/worker-dies.tsv" \
  PATH="$tmp/single-bin:$PATH" bash "$runner" platform \
  >"$tmp/worker-dies.out" 2>"$tmp/worker-dies.err"; then
  fail "the group runner accepted a worker that died before writing its result"
elif grep -q 'worker did not produce complete result files' "$tmp/worker-dies.out" \
  && grep -q '1 command(s) failed' "$tmp/worker-dies.err"; then
  pass "a killed worker without complete result files fails the group"
else
  printf '%s\n' "$tmp/worker-dies.out" "$tmp/worker-dies.err" >&2
  fail "a killed worker was not diagnosed as a missing result"
fi

mkdir -p "$tmp/bin"
cat >"$tmp/bin/nproc" <<'EOF'
#!/usr/bin/env bash
printf '2\n'
EOF
chmod +x "$tmp/bin/nproc"
cat >"$tmp/wait-for-peer.sh" <<'EOF'
#!/usr/bin/env bash
printf x >>"$ACTIONS_CI_PARALLEL_STARTS"
while [ "$(wc -c <"$ACTIONS_CI_PARALLEL_STARTS")" -lt 2 ]; do
  sleep 0.01
done
EOF
chmod +x "$tmp/wait-for-peer.sh"
printf 'platform\tbash %q\nplatform\tbash %q\n' \
  "$tmp/wait-for-peer.sh" "$tmp/wait-for-peer.sh" >"$tmp/parallel.tsv"

if ACTIONS_CI_COMMAND_BUDGET_SECONDS=10 \
  ACTIONS_CI_GROUP_MANIFEST="$tmp/parallel.tsv" \
  ACTIONS_CI_PARALLEL_STARTS="$tmp/parallel-starts" \
  PATH="$tmp/bin:$PATH" bash "$runner" platform \
  >"$tmp/parallel.out" 2>"$tmp/parallel.err"; then
  if [ "$(wc -c <"$tmp/parallel-starts")" -eq 2 ]; then
    pass "independent manifest commands run concurrently up to nproc"
  else
    fail "parallel manifest commands did not both start"
  fi
else
  fail "independent manifest commands did not complete concurrently"
fi

[ "$fails" -eq 0 ] || exit 1
echo "All tests passed."
