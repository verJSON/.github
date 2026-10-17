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
# A stub's bytes live in their own file, so the manifest command the runner
# echoes in its group and row lines never contains the forged text.
run_stub() { # <name> <printf-format> -> run_group <name> with one stub row
  printf '%s' "$2" >"$tmp/$1.format"
  run_group "$1" "platform	bash -c 'printf \"\$(cat \"\$0\")\"' $tmp/$1.format"
}
has_one_row_line() { # <name>: exactly one whole, unmodified row line was produced
  [ "$(grep -Ecx 'actions-ci command group=platform status=[0-9]+ elapsed=[0-9]+s( budget=[0-9]+s)? command=bash.*' \
    "$tmp/$1.out")" -eq 1 ]
}

# (a) Run 39064216676: output ending in `##[endgroup]` with no newline is
# stored as a bare `##[endgroup]` and the producer's next line is lost.
run_stub unterminated 'output line\n##[endgroup]' >/dev/null
if has_one_row_line unterminated \
  && ! grep -q '^##\[' "$tmp/unterminated.out"; then
  pass "output without a final newline cannot swallow the row or close its group"
else
  cat "$tmp/unterminated.out" >&2
  fail "unterminated output hid the row or replayed a bare ##[endgroup]"
fi

# (b) Run 39064216676: the runner trims leading whitespace, so
# ` ::add-mask::command group=` masked every later row as `actions-ci ***`.
run_stub leading-space ' ::add-mask::command group=\n' >/dev/null
if has_one_row_line leading-space \
  && ! grep -Eq '^[[:space:]]*::add-mask::' "$tmp/leading-space.out" \
  && ! grep -q 'command group=$' "$tmp/leading-space.out" \
  && grep -q '\[workflow-command:add-mask\]' "$tmp/leading-space.out"; then
  pass "an indented add-mask is neither executed nor allowed to publish its value"
else
  cat "$tmp/leading-space.out" >&2
  fail "an indented add-mask survived replay or published the value it masks"
fi

if [ "$fails" -ne 0 ]; then
  printf '%d test(s) failed\n' "$fails"
  exit 1
fi
printf 'All tests passed.\n'
