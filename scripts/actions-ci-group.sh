#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${ACTIONS_CI_GROUP_MANIFEST:-$root/scripts/actions-ci-groups.tsv}"
group="${1-}"

case "$group" in
  platform|merge-gate|changelog-release-1|changelog-release-2|changelog-release-3|changelog-release-4|docs) ;;
  *)
    printf 'unknown actions-ci group: %s\n' "$group" >&2
    exit 2
    ;;
esac

[ -r "$manifest" ] || {
  printf 'actions-ci group manifest unreadable: %s\n' "$manifest" >&2
  exit 2
}

cd "$root" || exit 2

command_budget="${ACTIONS_CI_COMMAND_BUDGET_SECONDS:-}"
if [ -n "$command_budget" ]; then
  if [[ ! "$command_budget" =~ ^[1-9][0-9]*$ ]]; then
    printf 'invalid actions-ci command budget: %s\n' "$command_budget" >&2
    exit 2
  fi
  command -v timeout >/dev/null 2>&1 || {
    printf 'timeout is required when ACTIONS_CI_COMMAND_BUDGET_SECONDS is set\n' >&2
    exit 2
  }
fi
command -v setsid >/dev/null 2>&1 || {
  printf 'setsid is required to isolate actions-ci command process groups\n' >&2
  exit 2
}
command -v ps >/dev/null 2>&1 || {
  printf 'ps is required to supervise actions-ci command process groups\n' >&2
  exit 2
}

# nproc honors CPU affinity but not a CFS quota, so a container limited to 1.5
# CPUs on a large host would start one row per host CPU and stretch every row's
# wall clock past its budget. Use the quota, rounded up, when it is lower.
available_cpus() {
  local cpus quota period cgroup_root="${ACTIONS_CI_CGROUP_ROOT:-/sys/fs/cgroup}"
  local own_cgroup='' cpu_max
  cpus="$(nproc 2>/dev/null || printf '1')"
  [[ "$cpus" =~ ^[1-9][0-9]*$ ]] || cpus=1
  if [ -r /proc/self/cgroup ]; then
    own_cgroup="$(sed -n 's/^0::\(.*\)$/\1/p' /proc/self/cgroup 2>/dev/null)" || own_cgroup=''
  fi
  for cpu_max in "$cgroup_root$own_cgroup/cpu.max" "$cgroup_root/cpu.max"; do
    [ -r "$cpu_max" ] || continue
    read -r quota period <"$cpu_max" || continue
    if [[ "$quota" =~ ^[1-9][0-9]*$ && "$period" =~ ^[1-9][0-9]*$ ]]; then
      quota=$(( (quota + period - 1) / period ))
      [ "$quota" -ge "$cpus" ] || cpus="$quota"
    fi
    break
  done
  printf '%s\n' "$cpus"
}

ACTIONS_CI_AVAILABLE_CPUS="$(available_cpus)"
export ACTIONS_CI_AVAILABLE_CPUS
command_parallelism="${ACTIONS_CI_COMMAND_PARALLELISM:-$ACTIONS_CI_AVAILABLE_CPUS}"
[[ "$command_parallelism" =~ ^[1-9][0-9]*$ ]] || {
  printf 'invalid actions-ci command parallelism: %s\n' "$command_parallelism" >&2
  exit 2
}

declare -a commands=()
declare -a command_exclusive=()
declare -a command_budgets=()
declare -A command_index_by_pid=()
declare -A worker_wait_status_by_index=()
declare -A worker_reported_by_index=()
worker_wait_failures=0
failures=0
commands_started=0
# Count the selected rows independently of the parser below, so any path that
# drops a row (a final line without a newline, a command eating the manifest)
# fails the group instead of passing with that row unrun (#1736).
selected_rows="$(awk -F '\t' -v group="$group" '
  $1 == group && $1 !~ /^#/ { count++ }
  END { print count + 0 }
' "$manifest")" || {
  printf 'could not count actions-ci group %s rows in %s\n' "$group" "$manifest" >&2
  exit 2
}
exec {manifest_fd}<"$manifest"
while IFS=$'\t' read -r -u "$manifest_fd" command_group command; do
  case "$command_group" in
    ''|\#*) continue ;;
  esac
  [ "$command_group" = "$group" ] || continue
  [[ "$command" != *$'\t'* ]] || {
    printf 'malformed manifest row in actions-ci group %s: expected exactly two tab-separated fields\n' "$group" >&2
    exit 2
  }
  [ -n "$command" ] || {
    printf 'empty command in actions-ci group %s\n' "$group" >&2
    exit 2
  }
  # Leading @directives configure the row and are not part of the command:
  # `@exclusive` reserves the runner, `@budget=<seconds>` replaces the group
  # default budget for a row whose measured cost needs more (#1736).
  exclusive=0
  row_budget=''
  while [[ "$command" == @* ]]; do
    directive="${command%% *}"
    [ "$directive" != "$command" ] || {
      printf 'empty command in actions-ci group %s\n' "$group" >&2
      exit 2
    }
    command="${command#"$directive" }"
    case "$directive" in
      @exclusive)
        [ "$exclusive" -eq 0 ] || {
          printf 'duplicate manifest directive in actions-ci group %s: %s\n' "$group" "$directive" >&2
          exit 2
        }
        exclusive=1
        ;;
      @budget=*)
        [ -z "$row_budget" ] || {
          printf 'duplicate manifest directive in actions-ci group %s: %s\n' "$group" "$directive" >&2
          exit 2
        }
        row_budget="${directive#@budget=}"
        [[ "$row_budget" =~ ^[1-9][0-9]*$ ]] && [ "$row_budget" -le 600 ] || {
          printf 'invalid command budget in actions-ci group %s: %s\n' "$group" "$directive" >&2
          exit 2
        }
        ;;
      *)
        printf 'unknown manifest directive in actions-ci group %s: %s\n' "$group" "$directive" >&2
        exit 2
        ;;
    esac
  done
  [ -n "$command" ] || {
    printf 'empty command in actions-ci group %s\n' "$group" >&2
    exit 2
  }
  # Row budgets are a pull-request tripwire: without a group budget no row is capped.
  [ -n "$command_budget" ] || row_budget=''
  commands+=("$command")
  command_exclusive+=("$exclusive")
  command_budgets+=("${row_budget:-$command_budget}")
done
exec {manifest_fd}<&-

if [ "${#commands[@]}" -eq 0 ]; then
  printf 'actions-ci group has no commands: %s\n' "$group" >&2
  exit 2
fi

if [ "$command_parallelism" -gt "${#commands[@]}" ]; then
  command_parallelism="${#commands[@]}"
fi
printf 'actions-ci group=%s command_parallelism=%s command_count=%s\n' \
  "$group" "$command_parallelism" "${#commands[@]}"

tmp="$(mktemp -d)"

command_group_has_live_processes() {
  local process_group="$1" listing
  # A failed ps must read as live: treating it as dead would delete scratch
  # data under running descendants.
  listing="$(ps -eo pgid=,stat=)" || return 0
  [ "$(awk -v group="$process_group" \
    '$1 == group && $2 !~ /^Z/ { live++ } END { print live + 0 }' <<<"$listing")" -gt 0 ]
}

terminate_command_group() {
  local process_group="$1"
  [[ "$process_group" =~ ^[1-9][0-9]*$ ]] || return 0
  kill -TERM -- "-$process_group" 2>/dev/null || true
  for _ in {1..20}; do
    command_group_has_live_processes "$process_group" || return 0
    sleep 0.1
  done
  kill -KILL -- "-$process_group" 2>/dev/null || true
  for _ in {1..20}; do
    command_group_has_live_processes "$process_group" || return 0
    sleep 0.1
  done
  echo "command process group $process_group survived SIGKILL" >&2
  # Scratch data may still be in use by the survivor; never remove it.
  : >"$tmp/survivors"
  return 1
}

command_group_for_index() {
  local index="$1"
  local process_group=''
  if [ -r "$tmp/$index.pgid" ] \
    && IFS= read -r process_group <"$tmp/$index.pgid" \
    && [[ "$process_group" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "$process_group"
  fi
}

cleanup_command_group_for_index() {
  local index="$1"
  local process_group
  process_group="$(command_group_for_index "$index")"
  local survived=0
  if [ -n "$process_group" ] \
    && command_group_has_live_processes "$process_group"; then
    terminate_command_group "$process_group" || survived=1
  fi
  rm -f -- "$tmp/$index.pgid"
  return "$survived"
}

cleanup_group_runner() {
  trap - EXIT INT TERM
  local worker_pid index
  for worker_pid in "${!command_index_by_pid[@]}"; do
    kill -TERM "$worker_pid" 2>/dev/null || true
  done
  for worker_pid in "${!command_index_by_pid[@]}"; do
    wait "$worker_pid" 2>/dev/null || true
  done
  for index in "${!commands[@]}"; do
    cleanup_command_group_for_index "$index" || true
  done
  if [ -e "$tmp/survivors" ]; then
    printf '::error::actions-ci group=%s has command processes that survived SIGKILL; preserving scratch data in %s\n' \
      "$group" "$tmp"
    exit 1
  fi
  rm -rf -- "$tmp"
}

sanitize_command_output_for_index() {
  local index="$1"
  local raw_log="$tmp/$index.raw.log"
  local log_file="$tmp/$index.log"

  if ! sed -u -E \
    -e 's/^::(add-mask|stop-commands)::.*/[workflow-command:\1]:/' \
    -e 's/^::(error|warning|notice|debug|group|endgroup|echo)([^:]*)::/[workflow-command:\1]\2:/' \
    "$raw_log" >"$log_file"; then
    printf 'failed to sanitize command output\n' >>"$log_file"
    return 1
  fi
  rm -f -- "$raw_log"
}

trap cleanup_group_runner EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

# The launched child holds on its stdin before running its command until this
# check releases it, so it is alive and nothing a row does can affect the answer.
# Wait for it to call setsid(), then report whether its process group is its
# own PID. A child of its own means setsid forked; any unreadable or
# unparsable state also fails closed.
supervisor_leads_its_group() {
  local pid="$1" stat state process_group children
  supervisor_group_evidence=''
  for _ in {1..500}; do
    if ! { stat="$(<"/proc/$pid/stat")"; } 2>/dev/null || [[ "$stat" != *") "* ]]; then
      supervisor_group_evidence='(its /proc stat is unreadable)'
      return 1
    fi
    read -r state _ process_group _ <<<"${stat##*) }"
    if [[ ! "$process_group" =~ ^[0-9]+$ ]]; then
      supervisor_group_evidence='(its /proc stat did not parse)'
      return 1
    fi
    [ "$process_group" != "$pid" ] || return 0
    children=''
    [ ! -r "/proc/$pid/task/$pid/children" ] || children="$(<"/proc/$pid/task/$pid/children")"
    if [ "$state" = Z ] || [ -n "${children// /}" ]; then
      supervisor_group_evidence="is in process group $process_group and did not become its leader"
      return 1
    fi
    sleep 0.01
  done
  supervisor_group_evidence="stayed in process group $process_group"
  return 1
}

run_command() {
  local index="$1"
  local command="$2"
  local started status=0 elapsed supervisor_pid process_group release_fd release_token
  local background_processes=0
  local command_group_file="$tmp/$index.pgid"

  started="$(date +%s)"
  export ACTIONS_CI_WORKER_PID="$BASHPID"
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'cleanup_command_group_for_index "$index"' EXIT
cat >"$tmp/$index.supervisor.sh" <<'SUPERVISOR'
#!/usr/bin/env bash
command_budget="$1"
command="$2"
command_raw_log="$3"
# Run nothing until the worker has confirmed this process leads its group.
# stdin is a pipe whose only write end the worker holds, so a dead worker reads
# as EOF here rather than a hang. The row then gets /dev/null, never that pipe.
IFS= read -r token || exit 125
[ "$token" = go ] || exit 125
exec </dev/null
status=0
if [ -n "$command_budget" ]; then
  timeout --foreground --kill-after=2s "${command_budget}s" \
    bash -o pipefail -c "$command" >"$command_raw_log" 2>&1 || status=$?
else
  bash -o pipefail -c "$command" >"$command_raw_log" 2>&1 || status=$?
fi
exit "$status"
SUPERVISOR
  # The supervisor's stdin is a pipe whose only write end the worker holds; a
  # process substitution gives that without `coproc`, which the ref-encoding
  # scan declines to model.
  exec {release_fd}> >(setsid bash "$tmp/$index.supervisor.sh" "${command_budgets[$index]}" \
    "$command" "$tmp/$index.raw.log" >/dev/null)
  # setsid execs in place from this non-leader job, so the supervisor's PID is
  # its session and process-group ID. The worker keeps it; the file only lets
  # the parent clean up if this worker dies, and no row is told where it is.
  supervisor_pid="$!"
  process_group="$supervisor_pid"
  printf '%s\n' "$process_group" >"$command_group_file"
  # setsid forks instead when its caller leads a process group, and then $! is
  # not the supervisor. Confirm it from the kernel while the supervisor is held,
  # before its row starts; release it only if it leads its own group.
  local group_confirmed=0
  if supervisor_leads_its_group "$supervisor_pid"; then
    group_confirmed=1
    release_token=go
  else
    release_token=stop
  fi
  # A supervisor that already exited closed the pipe; write from a subshell so
  # SIGPIPE ends that subshell, not this worker and its result.
  ( printf '%s\n' "$release_token" >&"$release_fd" ) 2>/dev/null || true
  exec {release_fd}>&-
  wait "$supervisor_pid" || status=$?
  if [ "$group_confirmed" -ne 1 ]; then
    printf 'supervisor process group could not be confirmed: pid %s %s\n' \
      "$supervisor_pid" "$supervisor_group_evidence" >>"$tmp/$index.raw.log"
    [ "$status" -ne 0 ] || status=1
  fi
  if command_group_has_live_processes "$process_group"; then
    background_processes=1
    [ "$status" -ne 0 ] || status=1
    if ! terminate_command_group "$process_group"; then
      printf 'command process group %s survived SIGKILL\n' "$process_group" >>"$tmp/$index.raw.log"
    fi
  fi
  rm -f -- "$command_group_file"
  if ! sanitize_command_output_for_index "$index"; then
    [ "$status" -ne 0 ] || status=1
  fi
  if [ "$background_processes" -eq 1 ]; then
    printf 'command left background processes in process group %s; terminating them\n' \
      "$process_group" >>"$tmp/$index.log"
  fi
  trap - EXIT INT TERM
  elapsed="$(( $(date +%s) - started ))"
  printf '%s\t%s\n' "$status" "$elapsed" >"$tmp/$index.status"
}

report_worker_result() {
  local index="$1"
  local worker_status="$2"
  local status_file="$tmp/$index.status"
  local log_file="$tmp/$index.log"
  local status=''
  local elapsed=''
  local budget="${command_budgets[$index]}"
  local budget_report=''
  [ -z "$budget" ] || budget_report=" budget=${budget}s"

  printf '::group::%s\n' "${commands[$index]}"
  if [ -f "$tmp/$index.raw.log" ] \
    && ! sanitize_command_output_for_index "$index"; then
    printf '::error::group=%s command=%q raw worker output could not be sanitized\n' \
      "$group" "${commands[$index]}"
  fi
  if [ ! -f "$status_file" ] || [ ! -f "$log_file" ]; then
    printf '::error::group=%s command=%q worker did not produce complete result files\n' \
      "$group" "${commands[$index]}"
    if [ -f "$log_file" ] && ! cat -- "$log_file"; then
      printf '::error::group=%s command=%q log could not be read\n' \
        "$group" "${commands[$index]}"
    fi
    printf 'actions-ci command group=%s status=missing elapsed=unknown command=%q\n' \
      "$group" "${commands[$index]}"
    printf '::endgroup::\n'
    worker_reported_by_index["$index"]=1
    failures=$((failures + 1))
    return
  fi

  if ! IFS=$'\t' read -r status elapsed <"$status_file" \
    || [[ ! "$status" =~ ^[0-9]+$ || ! "$elapsed" =~ ^[0-9]+$ ]]; then
    printf '::error::group=%s command=%q worker result is malformed\n' \
      "$group" "${commands[$index]}"
    if ! cat -- "$log_file"; then
      printf '::error::group=%s command=%q log could not be read\n' \
        "$group" "${commands[$index]}"
    fi
    printf 'actions-ci command group=%s status=invalid elapsed=unknown command=%q\n' \
      "$group" "${commands[$index]}"
    printf '::endgroup::\n'
    worker_reported_by_index["$index"]=1
    failures=$((failures + 1))
    return
  fi

  if ! cat -- "$log_file"; then
    [ "$status" -ne 0 ] || status=1
    printf '::error::group=%s command=%q log could not be read\n' \
      "$group" "${commands[$index]}"
  fi

  if [ "$worker_status" -ne 0 ]; then
    printf '::error::group=%s command=%q worker exited with status=%d\n' \
      "$group" "${commands[$index]}" "$worker_status"
    [ "$status" -ne 0 ] || status="$worker_status"
  fi

  if [ "$status" -ne 0 ]; then
    if [ -n "$budget" ] \
      && { [ "$status" -eq 124 ] || [ "$status" -eq 137 ]; } \
      && [ "$elapsed" -ge "$budget" ]; then
      printf '::error::group=%s command=%q timed out after %ss (budget=%ss)\n' \
        "$group" "${commands[$index]}" "$elapsed" "$budget"
    else
      printf '::error::group=%s command=%q exit=%d elapsed=%ss\n' \
        "$group" "${commands[$index]}" "$status" "$elapsed"
    fi
    failures=$((failures + 1))
  elif [ -n "$budget" ] && [ "$elapsed" -gt "$budget" ]; then
    printf '::error::group=%s command=%q elapsed=%ss budget=%ss\n' \
      "$group" "${commands[$index]}" "$elapsed" "$budget"
    failures=$((failures + 1))
  fi
  printf 'actions-ci command group=%s status=%s elapsed=%ss%s command=%q\n' \
    "$group" "$status" "$elapsed" "$budget_report" "${commands[$index]}"
  printf '::endgroup::\n'
  worker_reported_by_index["$index"]=1
}

record_worker_wait() {
  local finished_pid="$1" worker_status="$2"
  local index=''
  if [ -n "$finished_pid" ]; then
    index="${command_index_by_pid[$finished_pid]:-}"
    unset 'command_index_by_pid[$finished_pid]'
  fi
  if [ -z "$finished_pid" ] || [ -z "$index" ]; then
    worker_wait_failures=$((worker_wait_failures + 1))
  else
    if [ "$worker_status" -ne 0 ]; then
      cleanup_command_group_for_index "$index" || true
      worker_wait_status_by_index["$index"]="$worker_status"
    fi
    report_worker_result "$index" \
      "${worker_wait_status_by_index[$index]:-0}"
  fi
}

wait_for_worker() {
  local finished_pid='' worker_status=0
  if wait -n -p finished_pid; then
    :
  else
    worker_status=$?
  fi
  record_worker_wait "$finished_pid" "$worker_status"
}

active=0
for index in "${!commands[@]}"; do
  if [ "${command_exclusive[$index]}" -eq 1 ]; then
    while [ "$active" -gt 0 ]; do
      wait_for_worker
      active=$((active - 1))
    done
  else
    while [ "$active" -ge "$command_parallelism" ]; do
      wait_for_worker
      active=$((active - 1))
    done
  fi
  printf 'running actions-ci command %s/%s in %s: %s\n' \
    "$((index + 1))" "${#commands[@]}" "$group" "${commands[$index]}"
  run_command "$index" "${commands[$index]}" </dev/null &
  command_index_by_pid["$!"]="$index"
  commands_started=$((commands_started + 1))
  active=$((active + 1))
  if [ "${command_exclusive[$index]}" -eq 1 ]; then
    wait_for_worker
    active=$((active - 1))
  fi
done

while [ "$active" -gt 0 ]; do
  wait_for_worker
  active=$((active - 1))
done

for index in "${!commands[@]}"; do
  if [ "${worker_reported_by_index[$index]:-0}" -ne 1 ]; then
    report_worker_result "$index" \
      "${worker_wait_status_by_index[$index]:-0}"
  fi
done

commands_reported="${#worker_reported_by_index[@]}"
if [ "$commands_started" -ne "$selected_rows" ] \
  || [ "$commands_reported" -ne "$selected_rows" ]; then
  printf '::error::actions-ci manifest selects %d command(s) for %s; ran %d and reported %d\n' \
    "$selected_rows" "$group" "$commands_started" "$commands_reported"
  failures=$((failures + 1))
fi

if [ "$worker_wait_failures" -ne 0 ]; then
  printf '::error::actions-ci group=%s could not identify %d completed worker(s)\n' \
    "$group" "$worker_wait_failures"
  failures=$((failures + worker_wait_failures))
fi

if [ "$failures" -ne 0 ]; then
  printf '%d command(s) failed in actions-ci group %s\n' "$failures" "$group" >&2
  exit 1
fi

printf 'actions-ci group %s passed %d command(s)\n' "$group" "${#commands[@]}"
