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

command_parallelism="$(nproc 2>/dev/null || printf '1')"
case "$command_parallelism" in
  ''|*[!0-9]*|0) command_parallelism=1 ;;
esac

declare -a commands=()
declare -a command_exclusive=()
declare -A command_index_by_pid=()
declare -A worker_command_group_by_index=()
declare -A worker_wait_status_by_index=()
declare -A worker_reported_by_index=()
worker_wait_failures=0
failures=0
while IFS=$'\t' read -r command_group command; do
  case "$command_group" in
    ''|\#*) continue ;;
  esac
  [ "$command_group" = "$group" ] || continue
  [ -n "$command" ] || {
    printf 'empty command in actions-ci group %s\n' "$group" >&2
    exit 2
  }
  exclusive=0
  case "$command" in
    '@exclusive '*)
      exclusive=1
      command="${command#@exclusive }"
      ;;
    @exclusive*)
      printf 'invalid exclusive command in actions-ci group %s\n' "$group" >&2
      exit 2
      ;;
  esac
  [ -n "$command" ] || {
    printf 'empty command in actions-ci group %s\n' "$group" >&2
    exit 2
  }
  commands+=("$command")
  command_exclusive+=("$exclusive")
done <"$manifest"

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
CHANGELOG_CALLER_CONTRACT_CACHE="$tmp/changelog-caller-contract-cache"
mkdir -p "$CHANGELOG_CALLER_CONTRACT_CACHE"
export CHANGELOG_CALLER_CONTRACT_CACHE

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
  if [ -z "$process_group" ]; then
    process_group="${worker_command_group_by_index[$index]:-}"
  fi
  if [ -n "$process_group" ] \
    && command_group_has_live_processes "$process_group"; then
    terminate_command_group "$process_group"
  fi
  rm -f -- "$tmp/$index.pgid"
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
    cleanup_command_group_for_index "$index"
  done
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

run_command() {
  local index="$1"
  local command="$2"
  local started status=0 elapsed supervisor_pid process_group
  local background_processes=0
  local command_group_file="$tmp/$index.pgid"

  started="$(date +%s)"
  export ACTIONS_CI_WORKER_PID="$BASHPID"
  export ACTIONS_CI_COMMAND_PGID_FILE="$command_group_file"
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'cleanup_command_group_for_index "$index"' EXIT
cat >"$tmp/$index.supervisor.sh" <<'SUPERVISOR'
#!/usr/bin/env bash
command_budget="$1"
command="$2"
command_raw_log="$3"
command_group_file="$4"
printf '%s\n' "$BASHPID" >"$command_group_file"
status=0
if [ -n "$command_budget" ]; then
  timeout --foreground --kill-after=2s "${command_budget}s" \
    bash -o pipefail -c "$command" >"$command_raw_log" 2>&1 || status=$?
else
  bash -o pipefail -c "$command" >"$command_raw_log" 2>&1 || status=$?
fi
exit "$status"
SUPERVISOR
  setsid bash "$tmp/$index.supervisor.sh" "$command_budget" "$command" \
    "$tmp/$index.raw.log" "$command_group_file" &
  supervisor_pid="$!"
  worker_command_group_by_index["$index"]="$supervisor_pid"
  wait "$supervisor_pid" || status=$?
  process_group="$(command_group_for_index "$index")"
  if [ -n "$process_group" ] \
    && command_group_has_live_processes "$process_group"; then
    background_processes=1
    [ "$status" -ne 0 ] || status=1
    terminate_command_group "$process_group"
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
    if [ -n "$command_budget" ] \
      && { [ "$status" -eq 124 ] || [ "$status" -eq 137 ]; } \
      && [ "$elapsed" -ge "$command_budget" ]; then
      printf '::error::group=%s command=%q timed out after %ss (budget=%ss)\n' \
        "$group" "${commands[$index]}" "$elapsed" "$command_budget"
    else
      printf '::error::group=%s command=%q exit=%d elapsed=%ss\n' \
        "$group" "${commands[$index]}" "$status" "$elapsed"
    fi
    failures=$((failures + 1))
  elif [ -n "$command_budget" ] && [ "$elapsed" -gt "$command_budget" ]; then
    printf '::error::group=%s command=%q elapsed=%ss budget=%ss\n' \
      "$group" "${commands[$index]}" "$elapsed" "$command_budget"
    failures=$((failures + 1))
  fi
  printf 'actions-ci command group=%s status=%s elapsed=%ss command=%q\n' \
    "$group" "$status" "$elapsed" "${commands[$index]}"
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
    cleanup_command_group_for_index "$index"
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
  run_command "$index" "${commands[$index]}" &
  command_index_by_pid["$!"]="$index"
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
