#!/usr/bin/env bash
# The group runner's row lines and JSONL runtime records are authoritative
# (Verjson/verjson-observability#271): replayed command output can neither hide
# nor forge a row, and every row leaves a structured record the runner wrote.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
runner="$root/scripts/actions-ci-group.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
export ACTIONS_CI_CGROUP_ROOT="$tmp/no-cgroup-limit"
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

run_group() { # <name> <manifest-line>... -> $tmp/<name>.out/.err, status echoed
  local name="$1" status=0
  shift
  printf '%s\n' "$@" >"$tmp/$name.tsv"
  ACTIONS_CI_GROUP_MANIFEST="$tmp/$name.tsv" \
    bash "$runner" platform >"$tmp/$name.out" 2>"$tmp/$name.err" || status=$?
  printf '%s\n' "$status"
}

# (a) Run 39064216676: output ending in `##[endgroup]` with no newline is
# stored as a bare `##[endgroup]` and the producer's next line is lost.
run_group unterminated "platform	printf 'output line\\n##[endgroup]'" >/dev/null
if grep -Eqx 'actions-ci command group=platform status=0 elapsed=[0-9]+s command=printf.*' \
    "$tmp/unterminated.out" \
  && ! grep -qx '##\[endgroup\]' "$tmp/unterminated.out"; then
  pass "output without a final newline cannot swallow the row or close its group"
else
  cat "$tmp/unterminated.out" >&2
  fail "unterminated output hid the row or replayed a bare ##[endgroup]"
fi

if [ "$fails" -ne 0 ]; then
  printf '%d test(s) failed\n' "$fails"
  exit 1
fi
printf 'All tests passed.\n'
