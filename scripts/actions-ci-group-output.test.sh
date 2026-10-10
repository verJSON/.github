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
process_is_live() {
  local pid="$1" stat
  stat="$(ps -o stat= -p "$pid" 2>/dev/null || true)"
  [ -n "$stat" ] && [[ "$stat" != Z* ]]
}
process_group_is_live() {
  local process_group="$1"
  [ "$(ps -eo pgid=,stat= | awk -v group="$process_group" \
    '$1 == group && $2 !~ /^Z/ { live++ } END { print live + 0 }')" -gt 0 ]
}

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

cat >"$tmp/timeout-worker.sh" <<'EOF'
#!/usr/bin/env bash
sleep 30 &
printf '%s\n' "$!" >"$ACTIONS_CI_TIMEOUT_CHILD_PID"
wait "$!"
touch "$ACTIONS_CI_TIMEOUT_MARKER"
EOF
chmod +x "$tmp/timeout-worker.sh"
printf 'platform\tbash %q\n' "$tmp/timeout-worker.sh" >"$tmp/slow.tsv"

runner_status=0
timeout 5s env ACTIONS_CI_COMMAND_BUDGET_SECONDS=1 ACTIONS_CI_GROUP_MANIFEST="$tmp/slow.tsv" \
  ACTIONS_CI_TIMEOUT_CHILD_PID="$tmp/timeout-child.pid" \
  ACTIONS_CI_TIMEOUT_MARKER="$tmp/finished-after-timeout" \
  bash "$runner" platform >"$tmp/slow.out" 2>"$tmp/slow.err" || runner_status=$?
child_was_live=0
if [ -s "$tmp/timeout-child.pid" ] \
  && process_is_live "$(cat "$tmp/timeout-child.pid")"; then
  child_was_live=1
  kill "$(cat "$tmp/timeout-child.pid")" 2>/dev/null || true
fi
if [ "$runner_status" -eq 0 ]; then
  fail "a command over the budget was accepted"
elif grep -q 'budget=1' "$tmp/slow.out" \
  && [ -s "$tmp/timeout-child.pid" ] \
  && [ "$child_was_live" -eq 0 ] \
  && [ ! -e "$tmp/finished-after-timeout" ]; then
  pass "a timed-out command terminates descendants that inherit its output"
else
  fail "a timed-out command hung or left an output-inheriting descendant alive"
fi

cat >"$tmp/orphan-worker.sh" <<'EOF'
#!/usr/bin/env bash
sleep 30 &
printf '%s\n' "$!" >"$ACTIONS_CI_ORPHAN_CHILD_PID"
EOF
chmod +x "$tmp/orphan-worker.sh"
printf 'platform\tbash %q\n' "$tmp/orphan-worker.sh" >"$tmp/orphan.tsv"
runner_status=0
timeout 5s env ACTIONS_CI_COMMAND_BUDGET_SECONDS=5 \
  ACTIONS_CI_GROUP_MANIFEST="$tmp/orphan.tsv" \
  ACTIONS_CI_ORPHAN_CHILD_PID="$tmp/orphan-child.pid" \
  bash "$runner" platform >"$tmp/orphan.out" 2>"$tmp/orphan.err" || runner_status=$?
child_was_live=0
if [ -s "$tmp/orphan-child.pid" ] \
  && process_is_live "$(cat "$tmp/orphan-child.pid")"; then
  child_was_live=1
  kill "$(cat "$tmp/orphan-child.pid")" 2>/dev/null || true
fi
if [ "$runner_status" -eq 0 ]; then
  fail "a command that left a background descendant was accepted"
elif [ "$runner_status" -ne 124 ] \
  && grep -q 'left background processes' "$tmp/orphan.out" \
  && [ -s "$tmp/orphan-child.pid" ] \
  && [ "$child_was_live" -eq 0 ]; then
  pass "a timed command stops a fast-exiting descendant that inherits its output"
else
  printf '%s\n' "$tmp/orphan.out" "$tmp/orphan.err" >&2
  fail "a timed command hung or left a fast-exiting command descendant alive"
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
  if grep -q 'actions-ci group=platform command_parallelism=1 command_count=1' "$tmp/fast.out"; then
    pass "the group reports its effective command parallelism"
  else
    fail "the group did not report its effective command parallelism"
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
cat >"$tmp/kill-worker.sh" <<'EOF'
#!/usr/bin/env bash
printf '::error::worker diagnostic before kill\n'
sleep 30 >/dev/null 2>&1 &
printf '%s\n' "$!" >"$ACTIONS_CI_KILLED_WORKER_CHILD_PID"
kill -KILL "$ACTIONS_CI_WORKER_PID"
EOF
chmod +x "$tmp/kill-worker.sh"
printf 'platform\tbash %q\n' "$tmp/kill-worker.sh" >"$tmp/worker-dies.tsv"
mkdir -p "$tmp/worker-dies-tmp"

if ACTIONS_CI_COMMAND_BUDGET_SECONDS='' \
  ACTIONS_CI_GROUP_MANIFEST="$tmp/worker-dies.tsv" \
  ACTIONS_CI_KILLED_WORKER_CHILD_PID="$tmp/killed-worker-child.pid" \
  TMPDIR="$tmp/worker-dies-tmp" \
  PATH="$tmp/single-bin:$PATH" bash "$runner" platform \
  >"$tmp/worker-dies.out" 2>"$tmp/worker-dies.err"; then
  fail "the group runner accepted a worker that died before writing its result"
elif grep -q 'worker did not produce complete result files' "$tmp/worker-dies.out" \
  && grep -q 'worker diagnostic before kill' "$tmp/worker-dies.out" \
  && grep -q '\[workflow-command:error\]' "$tmp/worker-dies.out" \
  && ! grep -q '^::error::worker diagnostic before kill' "$tmp/worker-dies.out" \
  && grep -q '1 command(s) failed' "$tmp/worker-dies.err" \
  && [ -s "$tmp/killed-worker-child.pid" ] \
  && ! process_is_live "$(cat "$tmp/killed-worker-child.pid")" \
  && [ -z "$(find "$tmp/worker-dies-tmp" -mindepth 1 -print -quit)" ]; then
  pass "a killed worker is diagnosed, its process group is stopped, and scratch is removed"
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

cat >"$tmp/fast-fail.sh" <<'EOF'
#!/usr/bin/env bash
printf 'fast worker diagnostic\n'
exit 7
EOF
cat >"$tmp/slow-worker.sh" <<'EOF'
#!/usr/bin/env bash
touch "$ACTIONS_CI_SLOW_STARTED"
printf '%s\n' "$BASHPID" >"$ACTIONS_CI_SLOW_PID_FILE"
printf '%s\n' "$(ps -o pgid= -p "$BASHPID" | tr -d ' ')" \
  >"$ACTIONS_CI_SLOW_PGID_FILE"
sleep 30
EOF
chmod +x "$tmp/fast-fail.sh" "$tmp/slow-worker.sh"
mkdir -p "$tmp/runner-tmp"
printf 'platform\tbash %q\nplatform\tbash %q\n' \
  "$tmp/fast-fail.sh" "$tmp/slow-worker.sh" >"$tmp/partial-logs.tsv"
ACTIONS_CI_COMMAND_BUDGET_SECONDS='' \
ACTIONS_CI_GROUP_MANIFEST="$tmp/partial-logs.tsv" \
ACTIONS_CI_SLOW_STARTED="$tmp/slow-started" \
ACTIONS_CI_SLOW_PID_FILE="$tmp/slow-pid" \
 ACTIONS_CI_SLOW_PGID_FILE="$tmp/slow-pgid" \
PATH="$tmp/bin:$PATH" \
TMPDIR="$tmp/runner-tmp" \
setsid bash "$runner" platform >"$tmp/partial-logs.out" 2>"$tmp/partial-logs.err" &
stream_pid=$!
streamed_result=0
for _ in {1..300}; do
  slow_pid=''
  if [ -f "$tmp/slow-pid" ]; then
    IFS= read -r slow_pid <"$tmp/slow-pid"
  fi
  if [ -f "$tmp/slow-started" ] \
    && grep -q 'fast worker diagnostic' "$tmp/partial-logs.out" \
    && grep -Eq 'actions-ci command group=platform status=7 elapsed=[0-9]+s command=' \
      "$tmp/partial-logs.out" \
    && [ -n "$slow_pid" ] && kill -0 "$slow_pid" 2>/dev/null; then
    streamed_result=1
    break
  fi
  if ! kill -0 "$stream_pid" 2>/dev/null; then
    break
  fi
  sleep 0.05
done
if kill -0 "$stream_pid" 2>/dev/null; then
  kill -TERM "$stream_pid" 2>/dev/null || true
fi
stream_status=0
wait "$stream_pid" 2>/dev/null || stream_status=$?
group_count="$(grep -c '^::group::' "$tmp/partial-logs.out" || true)"
if [ "$streamed_result" -eq 1 ] && [ -f "$tmp/slow-started" ] \
  && [ "$group_count" -eq 1 ] \
  && [ "$stream_status" -eq 143 ] \
  && [ -s "$tmp/slow-pgid" ] \
  && ! process_group_is_live "$(cat "$tmp/slow-pgid")" \
  && [ -z "$(find "$tmp/runner-tmp" -mindepth 1 -print -quit)" ]; then
  pass "completed commands report early and cancellation reaps siblings before scratch cleanup"
else
  printf '%s\n' "$tmp/partial-logs.out" "$tmp/partial-logs.err" >&2
  fail "a fast worker's output and status were delayed by a hanging sibling"
fi

cat >"$tmp/normal-check.sh" <<'EOF'
#!/usr/bin/env bash
if [ -e "$ACTIONS_CI_EXCLUSIVE_ACTIVE" ]; then
  exit 11
fi
mkdir "$ACTIONS_CI_NORMAL_ACTIVE" || exit 12
sleep 0.1
if [ -e "$ACTIONS_CI_EXCLUSIVE_ACTIVE" ]; then
  exit 13
fi
rmdir "$ACTIONS_CI_NORMAL_ACTIVE"
EOF
cat >"$tmp/exclusive-check.sh" <<'EOF'
#!/usr/bin/env bash
if [ -e "$ACTIONS_CI_NORMAL_ACTIVE" ]; then
  exit 21
fi
mkdir "$ACTIONS_CI_EXCLUSIVE_ACTIVE" || exit 22
sleep 0.1
if [ -e "$ACTIONS_CI_NORMAL_ACTIVE" ]; then
  exit 23
fi
rmdir "$ACTIONS_CI_EXCLUSIVE_ACTIVE"
EOF
chmod +x "$tmp/normal-check.sh" "$tmp/exclusive-check.sh"
printf 'platform\tbash %q\nplatform\t@exclusive bash %q\nplatform\tbash %q\n' \
  "$tmp/normal-check.sh" "$tmp/exclusive-check.sh" "$tmp/normal-check.sh" \
  >"$tmp/exclusive.tsv"

if ACTIONS_CI_COMMAND_BUDGET_SECONDS=10 \
  ACTIONS_CI_GROUP_MANIFEST="$tmp/exclusive.tsv" \
  ACTIONS_CI_NORMAL_ACTIVE="$tmp/normal-active" \
  ACTIONS_CI_EXCLUSIVE_ACTIVE="$tmp/exclusive-active" \
  PATH="$tmp/bin:$PATH" bash "$runner" platform \
  >"$tmp/exclusive.out" 2>"$tmp/exclusive.err"; then
  pass "exclusive manifest commands run without sibling rows"
else
  cat "$tmp/exclusive.out" "$tmp/exclusive.err" >&2
  fail "an exclusive command overlapped a sibling manifest row"
fi

[ "$fails" -eq 0 ] || exit 1
echo "All tests passed."
