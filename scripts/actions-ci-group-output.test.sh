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
# Parallelism follows the host's CPU quota. Fixtures stub nproc, so keep the
# real cgroup out of them unless a case supplies its own.
export ACTIONS_CI_CGROUP_ROOT="$tmp/no-cgroup-limit"
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
  if grep -Eq 'actions-ci command group=platform status=0 elapsed=[0-9]+s budget=60s command=' "$tmp/fast.out"; then
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

# A row that declares its own budget is held to that number, in both
# directions, instead of the group default (#1736).
printf 'platform\t@budget=5 sleep 2\nplatform\t@exclusive @budget=1 sleep 4\n' \
  >"$tmp/declared-budget.tsv"
runner_status=0
timeout 20s env ACTIONS_CI_COMMAND_BUDGET_SECONDS=1 \
  ACTIONS_CI_GROUP_MANIFEST="$tmp/declared-budget.tsv" \
  bash "$runner" platform >"$tmp/declared-budget.out" 2>"$tmp/declared-budget.err" \
  || runner_status=$?
if [ "$runner_status" -eq 1 ] \
  && grep -Eq 'status=0 elapsed=[0-9]+s budget=5s command=sleep\\? 2$' "$tmp/declared-budget.out" \
  && grep -q 'command=sleep\\ 4 timed out after [0-9]*s (budget=1s)' "$tmp/declared-budget.out" \
  && grep -q '1 command(s) failed' "$tmp/declared-budget.err"; then
  pass "manifest rows run under their declared budget instead of the group default"
else
  cat "$tmp/declared-budget.out" "$tmp/declared-budget.err" >&2
  fail "a declared row budget was ignored or misreported (runner status $runner_status)"
fi
# Budgets are a pull-request tripwire; a push run leaves every row uncapped.
printf 'platform\t@budget=1 sleep 2\n' >"$tmp/uncapped.tsv"
if ACTIONS_CI_COMMAND_BUDGET_SECONDS='' ACTIONS_CI_GROUP_MANIFEST="$tmp/uncapped.tsv" \
  bash "$runner" platform >"$tmp/uncapped.out" 2>&1 \
  && grep -Eq 'status=0 elapsed=[0-9]+s command=sleep\\? 2$' "$tmp/uncapped.out"; then
  pass "declared row budgets apply only when the group budget is enabled"
else
  cat "$tmp/uncapped.out" >&2
  fail "a declared row budget capped a run without a group budget"
fi
printf 'platform\t@budget=0 true\n' >"$tmp/zero-budget.tsv"
printf 'platform\t@budgt=5 true\n' >"$tmp/unknown-directive.tsv"
if ACTIONS_CI_GROUP_MANIFEST="$tmp/zero-budget.tsv" bash "$runner" platform \
  >"$tmp/zero-budget.out" 2>&1; then
  fail "a zero row budget was accepted"
elif ACTIONS_CI_GROUP_MANIFEST="$tmp/unknown-directive.tsv" bash "$runner" platform \
  >"$tmp/unknown-directive.out" 2>&1; then
  fail "an unknown manifest directive was accepted"
elif grep -q 'invalid command budget' "$tmp/zero-budget.out" \
  && grep -q 'unknown manifest directive' "$tmp/unknown-directive.out"; then
  pass "malformed row budgets and unknown directives fail before any command runs"
else
  cat "$tmp/zero-budget.out" "$tmp/unknown-directive.out" >&2
  fail "malformed manifest directives failed without naming the defect"
fi

# A row that reads stdin must see an empty file, never the manifest or the
# runner's own input, and must not stop later rows from running (#1736). A
# Windows binary under WSL interop, `cat`, `read`, or `ssh` all read it.
# Rows start as background jobs in a worker subshell, where bash already
# substitutes /dev/null, so this guards the behavior rather than the runner's
# explicit redirect; no launch path exists today where only the redirect holds.
printf 'platform\tcat >"$ACTIONS_CI_STDIN_SEEN"\nplatform\ttouch "$ACTIONS_CI_LATER_RAN"\n' \
  >"$tmp/stdin.tsv"
if printf 'runner-stdin-sentinel\n' \
  | ACTIONS_CI_GROUP_MANIFEST="$tmp/stdin.tsv" \
    ACTIONS_CI_STDIN_SEEN="$tmp/stdin-seen" ACTIONS_CI_LATER_RAN="$tmp/later-ran" \
    bash "$runner" platform >"$tmp/stdin.out" 2>&1 \
  && [ -f "$tmp/stdin-seen" ] && [ ! -s "$tmp/stdin-seen" ] \
  && [ -e "$tmp/later-ran" ] \
  && grep -q 'passed 2 command(s)' "$tmp/stdin.out"; then
  pass "a row that reads stdin sees /dev/null and later rows still run"
else
  cat "$tmp/stdin.out" >&2
  fail "a row read the runner's stdin or later rows were skipped"
fi

# The group fails unless it ran and reported every row the manifest selects.
# `read` drops a final row without a newline, so that row is a real early-exit
# path; the count invariant has to name it instead of passing with it unrun.
printf 'platform\ttrue\nmerge-gate\ttrue\nplatform\ttouch "$ACTIONS_CI_LATER_RAN"' \
  >"$tmp/unterminated.tsv"
rm -f "$tmp/later-ran"
if ACTIONS_CI_GROUP_MANIFEST="$tmp/unterminated.tsv" ACTIONS_CI_LATER_RAN="$tmp/later-ran" \
  bash "$runner" platform >"$tmp/unterminated.out" 2>&1; then
  fail "the group passed without running every manifest row it selects"
elif grep -q 'manifest selects 2 command(s) for platform; ran 1 and reported 1' \
  "$tmp/unterminated.out"; then
  pass "the group fails when it runs fewer rows than the manifest selects"
else
  cat "$tmp/unterminated.out" >&2
  fail "a dropped manifest row failed without the executed-count diagnostic"
fi

# A worker's result file is the only record of its row. A row cannot write it
# after it exits, so these fixtures pre-create it read-only: the worker's own
# write then fails and leaves the planted bytes for the parent to judge.
cat >"$tmp/plant-result.sh" <<'EOF'
#!/usr/bin/env bash
scratch="$(find "$TMPDIR" -mindepth 1 -maxdepth 1 -type d -name 'tmp.*' -print -quit)"
printf "$ACTIONS_CI_PLANTED_RESULT" >"$scratch/0.status"
chmod 444 "$scratch/0.status"
EOF
chmod +x "$tmp/plant-result.sh"
printf 'platform\tbash %q\n' "$tmp/plant-result.sh" >"$tmp/plant.tsv"
planted_result() { # <printf-format> -> runner output in $tmp/planted.out, status echoed
  local planted_status=0
  rm -rf "$tmp/plant-tmp"
  mkdir -p "$tmp/plant-tmp"
  TMPDIR="$tmp/plant-tmp" ACTIONS_CI_PLANTED_RESULT="$1" \
    ACTIONS_CI_GROUP_MANIFEST="$tmp/plant.tsv" \
    bash "$runner" platform >"$tmp/planted.out" 2>&1 || planted_status=$?
  printf '%s\n' "$planted_status"
}
malformed_ok=1
for planted in '' 'garbage\n' '0\t5' '0\t\n' '-1\t5\n'; do
  if [ "$(planted_result "$planted")" -ne 1 ] \
    || ! grep -q 'worker result is malformed' "$tmp/planted.out" \
    || ! grep -q 'actions-ci command group=platform status=invalid' "$tmp/planted.out"; then
    printf 'planted result %q was not rejected:\n' "$planted" >&2
    cat "$tmp/planted.out" >&2
    malformed_ok=0
  fi
done
if [ "$malformed_ok" -eq 1 ]; then
  pass "empty, garbage, unterminated, and incomplete worker results fail the group"
else
  fail "a malformed worker result was accepted"
fi
if [ "$(planted_result '0\t1\n')" -eq 1 ] \
  && grep -q 'worker exited with status=1' "$tmp/planted.out" \
  && grep -q 'actions-ci command group=platform status=1 ' "$tmp/planted.out"; then
  pass "a worker that exits nonzero fails its row even when its result reads 0"
else
  cat "$tmp/planted.out" >&2
  fail "a nonzero worker exit was hidden by a passing result"
fi

# A failed ps must read as live (#1736): treating it as dead would delete
# scratch data under running descendants. With ps failing, the group cannot
# prove its command groups ended, so it fails and keeps its scratch data.
mkdir -p "$tmp/ps-fail-bin" "$tmp/ps-fail-tmp"
printf '#!/usr/bin/env bash\nexit 1\n' >"$tmp/ps-fail-bin/ps"
chmod +x "$tmp/ps-fail-bin/ps"
printf 'platform\ttrue\n' >"$tmp/ps-fail.tsv"
ps_fail_status=0
TMPDIR="$tmp/ps-fail-tmp" PATH="$tmp/ps-fail-bin:$PATH" \
  ACTIONS_CI_GROUP_MANIFEST="$tmp/ps-fail.tsv" \
  bash "$runner" platform >"$tmp/ps-fail.out" 2>&1 || ps_fail_status=$?
if [ "$ps_fail_status" -ne 0 ] \
  && grep -q 'left background processes' "$tmp/ps-fail.out"; then
  pass "a failed ps counts as live processes and fails the row"
else
  cat "$tmp/ps-fail.out" >&2
  fail "a failed ps was read as a finished command group"
fi
if grep -q 'survived SIGKILL' "$tmp/ps-fail.out" \
  && grep -q 'preserving scratch data' "$tmp/ps-fail.out" \
  && [ -n "$(find "$tmp/ps-fail-tmp" -mindepth 1 -print -quit)" ]; then
  pass "a command group that survives SIGKILL fails the group and keeps its scratch data"
else
  cat "$tmp/ps-fail.out" >&2
  fail "a SIGKILL survivor was ignored or its scratch data was removed"
fi

# A row must not be able to switch off the background-process check by
# removing or rewriting the runner's record of its process group.
cat >"$tmp/pgid-tamper.sh" <<'EOF'
#!/usr/bin/env bash
if [ -n "${ACTIONS_CI_COMMAND_PGID_FILE:-}" ]; then
  rm -f "$ACTIONS_CI_COMMAND_PGID_FILE"
fi
sleep 30 &
printf '%s\n' "$!" >"$ACTIONS_CI_TAMPER_CHILD_PID"
EOF
chmod +x "$tmp/pgid-tamper.sh"
printf 'platform\tbash %q\n' "$tmp/pgid-tamper.sh" >"$tmp/pgid-tamper.tsv"
tamper_status=0
ACTIONS_CI_GROUP_MANIFEST="$tmp/pgid-tamper.tsv" \
  ACTIONS_CI_TAMPER_CHILD_PID="$tmp/tamper-child.pid" \
  bash "$runner" platform >"$tmp/pgid-tamper.out" 2>&1 || tamper_status=$?
tamper_child_live=0
if [ -s "$tmp/tamper-child.pid" ] && process_is_live "$(cat "$tmp/tamper-child.pid")"; then
  tamper_child_live=1
  kill "$(cat "$tmp/tamper-child.pid")" 2>/dev/null || true
fi
if [ "$tamper_status" -ne 0 ] && [ "$tamper_child_live" -eq 0 ] \
  && grep -q 'left background processes' "$tmp/pgid-tamper.out"; then
  pass "a row cannot hide a background descendant by removing the process-group record"
else
  cat "$tmp/pgid-tamper.out" >&2
  fail "a row hid its background descendant from the runner"
fi

# The runner treats the supervisor's PID as its process group, which holds only
# while setsid execs in place. setsid forks when its caller leads a process
# group (job control); a stub that forks and waits reproduces that, and the row
# must fail closed instead of supervising the wrong group.
mkdir -p "$tmp/forking-setsid-bin"
real_setsid="$(command -v setsid)"
printf '#!/usr/bin/env bash\n%q "$@" &\nwait "$!"\n' "$real_setsid" \
  >"$tmp/forking-setsid-bin/setsid"
chmod +x "$tmp/forking-setsid-bin/setsid"
printf 'platform\ttrue\n' >"$tmp/forking-setsid.tsv"
forking_status=0
PATH="$tmp/forking-setsid-bin:$PATH" ACTIONS_CI_GROUP_MANIFEST="$tmp/forking-setsid.tsv" \
  bash "$runner" platform >"$tmp/forking-setsid.out" 2>&1 || forking_status=$?
if [ "$forking_status" -ne 0 ] \
  && grep -q 'supervisor process group could not be confirmed' "$tmp/forking-setsid.out"; then
  pass "a supervisor whose PID is not its process group fails the row closed"
else
  cat "$tmp/forking-setsid.out" >&2
  fail "the runner supervised a process group it could not confirm"
fi

# A row must not be able to vouch for its own supervisor. This one walks up to
# the supervisor, finds any record path on its command line, and rewrites it to
# claim the PID the worker launched is a process-group leader.
cat >"$tmp/forge-identity.sh" <<'FORGE'
#!/usr/bin/env bash
pid="$$"
while [ "$pid" -gt 1 ]; do
  stat="$(<"/proc/$pid/stat")"
  read -r _ parent _ <<<"${stat##*) }"
  mapfile -d '' -t argv <"/proc/$pid/cmdline"
  if [[ "${argv[1]:-}" == *.supervisor.sh ]]; then
    launched="$parent"
    for arg in "${argv[@]:2}"; do
      if [ -f "$arg" ] && [[ "$arg" != *.log ]]; then
        printf '%s %s\n' "$launched" "$launched" >"$arg"
      fi
    done
    exit 0
  fi
  pid="$parent"
done
FORGE
chmod +x "$tmp/forge-identity.sh"
printf 'platform\tbash %q\n' "$tmp/forge-identity.sh" >"$tmp/forge-identity.tsv"
forge_status=0
PATH="$tmp/forking-setsid-bin:$PATH" ACTIONS_CI_GROUP_MANIFEST="$tmp/forge-identity.tsv" \
  bash "$runner" platform >"$tmp/forge-identity.out" 2>&1 || forge_status=$?
if [ "$forge_status" -ne 0 ] \
  && grep -q 'supervisor process group could not be confirmed' "$tmp/forge-identity.out"; then
  pass "a row cannot forge its supervisor's process-group identity"
else
  cat "$tmp/forge-identity.out" >&2
  fail "a row forged its supervisor's process-group identity"
fi

# The worker releases its supervisor through a pipe on the supervisor's stdin.
# A row holding either end would keep that pipe alive after the runner dies, or
# could read the handshake; the row's stdin must be /dev/null and it must hold
# no pipe the runner created. Pipes this test itself inherited pass through.
# The row lists its descriptors into a file: a command substitution or pipe
# would add a pipe of its own to the very table being inspected.
printf 'platform\tls -l /proc/$$/fd >"$ACTIONS_CI_FD_LISTING"\n' >"$tmp/handshake-handles.tsv"
ls -l "/proc/$$/fd" >"$tmp/test-fds"
inherited_pipes="$(grep -o 'pipe:\[[0-9]*\]' "$tmp/test-fds" | sort -u)"
if ACTIONS_CI_FD_LISTING="$tmp/row-fds" \
  ACTIONS_CI_GROUP_MANIFEST="$tmp/handshake-handles.tsv" bash "$runner" platform \
  >"$tmp/handshake-handles.out" 2>&1 \
  && grep -qE ' 0 -> /dev/null$' "$tmp/row-fds" \
  && [ -z "$(grep -o 'pipe:\[[0-9]*\]' "$tmp/row-fds" | sort -u | comm -23 - <(printf '%s\n' "$inherited_pipes"))" ]; then
  pass "rows read /dev/null and hold no handle on the supervisor handshake"
else
  cat "$tmp/handshake-handles.out" "$tmp/row-fds" >&2
  fail "a row inherited the supervisor handshake or a non-null stdin"
fi

# Malformed rows fail in the runner itself, not only in the manifest contract.
printf 'platform\ttrue\textra\n' >"$tmp/three-fields.tsv"
printf 'platform\t@budget=601 true\n' >"$tmp/over-cap.tsv"
if ! ACTIONS_CI_GROUP_MANIFEST="$tmp/three-fields.tsv" bash "$runner" platform \
    >"$tmp/three-fields.out" 2>&1 \
  && grep -q 'malformed manifest row' "$tmp/three-fields.out" \
  && ! ACTIONS_CI_GROUP_MANIFEST="$tmp/over-cap.tsv" bash "$runner" platform \
    >"$tmp/over-cap.out" 2>&1 \
  && grep -q 'invalid command budget' "$tmp/over-cap.out"; then
  pass "rows without exactly two fields and budgets above 600s fail before any command runs"
else
  cat "$tmp/three-fields.out" "$tmp/over-cap.out" >&2
  fail "the runner accepted a malformed row or a budget above the 600s cap"
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

# nproc counts schedulable CPUs, not a CFS quota: a container limited to 1.5
# CPUs on a 4-CPU host reports 4. Rows run up to the quota, rounded up, and the
# count is exported so a row's own workers can use the same figure.
mkdir -p "$tmp/four-bin" "$tmp/quota/limited" "$tmp/quota/unlimited"
cat >"$tmp/four-bin/nproc" <<'EOF'
#!/usr/bin/env bash
printf '4\n'
EOF
chmod +x "$tmp/four-bin/nproc"
printf '150000 100000\n' >"$tmp/quota/limited/cpu.max"
printf 'max 100000\n' >"$tmp/quota/unlimited/cpu.max"
printf 'platform\tprintf "cpus=%%s\\n" "$ACTIONS_CI_AVAILABLE_CPUS"\n%.0s' 1 2 3 4 \
  >"$tmp/quota.tsv"
quota_parallelism() { # <cgroup-root> [parallelism-override]
  ACTIONS_CI_CGROUP_ROOT="$1" ACTIONS_CI_COMMAND_PARALLELISM="${2:-}" \
    ACTIONS_CI_GROUP_MANIFEST="$tmp/quota.tsv" PATH="$tmp/four-bin:$PATH" \
    bash "$runner" platform 2>&1
}
limited="$(quota_parallelism "$tmp/quota/limited")"
unlimited="$(quota_parallelism "$tmp/quota/unlimited")"
forced="$(quota_parallelism "$tmp/quota/limited" 3)"
invalid_status=0
quota_parallelism "$tmp/quota/limited" 0 >"$tmp/invalid-parallelism.out" || invalid_status=$?
if grep -q 'command_parallelism=2 command_count=4' <<<"$limited" \
  && grep -q '^cpus=2$' <<<"$limited" \
  && grep -q 'command_parallelism=4 command_count=4' <<<"$unlimited" \
  && grep -q '^cpus=4$' <<<"$unlimited" \
  && grep -q 'command_parallelism=3 command_count=4' <<<"$forced" \
  && grep -q '^cpus=2$' <<<"$forced" \
  && [ "$invalid_status" -eq 2 ] \
  && grep -q 'invalid actions-ci command parallelism: 0' "$tmp/invalid-parallelism.out"; then
  pass "row parallelism follows the CPU quota, not nproc, and an explicit override wins"
else
  printf '%s\n---\n' "$limited" "$unlimited" "$forced" >&2
  cat "$tmp/invalid-parallelism.out" >&2
  fail "row parallelism ignored the CPU quota or mishandled the override"
fi
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
