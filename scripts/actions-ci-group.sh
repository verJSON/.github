#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${ACTIONS_CI_GROUP_MANIFEST:-$root/scripts/actions-ci-groups.tsv}"
group="${1-}"

case "$group" in
  platform|merge-gate|changelog-release|docs) ;;
  *)
    printf 'unknown actions-ci group: %s\n' "$group" >&2
    exit 2
    ;;
esac

[ -r "$manifest" ] || {
  printf 'actions-ci group manifest is unreadable: %s\n' "$manifest" >&2
  exit 2
}

cd "$root" || exit 2
failures=0
commands=0

# Child contracts print ::error:: / ::warning:: / ::notice:: as the text under
# test. The runner would otherwise annotate the parent check, so a green group
# looks failed while it is still running (#1735). Only the runner's own
# commands, printed after this filter, may reach the annotation channel.
mask_workflow_commands() {
  sed -u -E \
    -e 's/^::(add-mask|stop-commands)::.*/[workflow-command:\1]:/' \
    -e 's/^::(error|warning|notice|debug|group|endgroup|echo)([^:]*)::/[workflow-command:\1]\2:/'
}

command_budget="${ACTIONS_CI_COMMAND_BUDGET_SECONDS:-}"

while IFS=$'\t' read -r command_group command; do
  case "$command_group" in
    ''|\#*) continue ;;
  esac
  [ "$command_group" = "$group" ] || continue
  commands=$((commands + 1))
  printf '::group::%s\n' "$command"
  started="$(date +%s)"
  status=0
  bash -o pipefail -c "$command" 2>&1 | mask_workflow_commands || status=$?
  elapsed="$(( $(date +%s) - started ))"
  if [ "$status" -ne 0 ]; then
    printf '::error::group=%s command=%q exit=%d\n' "$group" "$command" "$status"
    failures=$((failures + 1))
  elif [ -n "$command_budget" ] && [ "$elapsed" -gt "$command_budget" ]; then
    printf '::error::group=%s command=%q elapsed=%ss budget=%ss\n' \
      "$group" "$command" "$elapsed" "$command_budget"
    failures=$((failures + 1))
  fi
  printf '::endgroup::\n'
done <"$manifest"

if [ "$commands" -eq 0 ]; then
  printf 'actions-ci group has no commands: %s\n' "$group" >&2
  exit 2
fi
if [ "$failures" -ne 0 ]; then
  printf '%d command(s) failed in actions-ci group %s\n' "$failures" "$group" >&2
  exit 1
fi

printf 'actions-ci group %s passed %d command(s)\n' "$group" "$commands"
