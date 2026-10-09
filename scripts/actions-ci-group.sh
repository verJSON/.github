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

# Child contracts print workflow annotations as test evidence. Mask those lines
# before buffering output so only this runner can annotate the parent check.
mask_workflow_commands() {
  sed -u -E \
    -e 's/^::(add-mask|stop-commands)::.*/[workflow-command:\1]:/' \
    -e 's/^::(error|warning|notice|debug|group|endgroup|echo)([^:]*)::/[workflow-command:\1]\2:/'
}

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

command_parallelism="$(nproc 2>/dev/null || printf '1')"
case "$command_parallelism" in
  ''|*[!0-9]*|0) command_parallelism=1 ;;
esac

declare -a commands=()
declare -a command_exclusive=()
declare -A command_index_by_pid=()
declare -A worker_wait_status_by_index=()
worker_wait_failures=0
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
trap 'rm -rf -- "$tmp"' EXIT
CHANGELOG_CALLER_CONTRACT_CACHE="$tmp/changelog-caller-contract-cache"
mkdir -p "$CHANGELOG_CALLER_CONTRACT_CACHE"
export CHANGELOG_CALLER_CONTRACT_CACHE

run_command() {
  local index="$1"
  local command="$2"
  local started status=0 elapsed

  started="$(date +%s)"
  if [ -n "$command_budget" ]; then
    timeout --kill-after=2s "${command_budget}s" \
      bash -o pipefail -c "$command" 2>&1 | mask_workflow_commands >"$tmp/$index.log" \
      || status=$?
  else
    bash -o pipefail -c "$command" 2>&1 | mask_workflow_commands >"$tmp/$index.log" \
      || status=$?
  fi
  elapsed="$(( $(date +%s) - started ))"
  printf '%s\t%s\n' "$status" "$elapsed" >"$tmp/$index.status"
}

record_worker_wait() {
  local finished_pid="$1" worker_status="$2"
  local index=''
  if [ -n "$finished_pid" ]; then
    index="${command_index_by_pid[$finished_pid]:-}"
  fi
  if [ -z "$finished_pid" ] || [ -z "$index" ]; then
    worker_wait_failures=$((worker_wait_failures + 1))
  elif [ "$worker_status" -ne 0 ]; then
    worker_wait_status_by_index["$index"]="$worker_status"
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

failures=0
for index in "${!commands[@]}"; do
  status_file="$tmp/$index.status"
  log_file="$tmp/$index.log"
  printf '::group::%s\n' "${commands[$index]}"
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
    failures=$((failures + 1))
    continue
  fi

  status=''
  elapsed=''
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
    failures=$((failures + 1))
    continue
  fi

  if ! cat -- "$log_file"; then
    printf '::error::group=%s command=%q log could not be read\n' \
      "$group" "${commands[$index]}"
    [ "$status" -ne 0 ] || status=1
  fi
  worker_status="${worker_wait_status_by_index[$index]:-0}"
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
