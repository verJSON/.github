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

# Every replayed byte sits inside one `::stop-commands::<token>` block per row,
# closed by the same random token, with each line prefixed; every line outside
# a block is a line the runner itself produces. <name> <expected-blocks>
stream_is_well_formed() {
  awk -v expected="$2" '
    function bad(why) { printf "line %d: %s: %s\n", NR, why, $0 > "/dev/stderr"; failed = 1 }
    token != "" {
      if ($0 == "::" token "::") { token = ""; next }
      if ($0 != "" && $0 !~ /^\| /) bad("unprefixed replayed line")
      next
    }
    /^::stop-commands::[0-9a-f]+$/ {
      token = substr($0, length("::stop-commands::") + 1)
      if (length(token) != 32) bad("short token")
      if (token in seen) bad("reused token")
      seen[token] = 1; blocks++
      next
    }
    /^actions-ci group=platform command_parallelism=[0-9]+ command_count=[0-9]+$/ { next }
    /^running actions-ci command [0-9]+\/[0-9]+ in platform: / { next }
    /^::group::/ || /^::endgroup::$/ || /^::error::/ || /^$/ { next }
    /^actions-ci command group=platform / { next }
    /^actions-ci group platform passed [0-9]+ command\(s\)$/ { next }
    { bad("not a producer line") }
    END {
      if (token != "") bad("unclosed stop-commands block")
      if (blocks != expected) { printf "%d blocks, expected %d\n", blocks, expected > "/dev/stderr"; failed = 1 }
      exit failed
    }
  ' "$tmp/$1.out"
}

# (c) Run 39064216676: inside stop-commands a forged `##[endgroup]` is stored
# byte-identical to a real one, and a stub can guess no token but its own.
run_stub stop-commands '::stop-commands::guess\n##[endgroup]\n::guess::\n::endgroup::\nactions-ci command group=platform status=0 elapsed=1s command=forged\n' >/dev/null
if has_one_row_line stop-commands \
  && stream_is_well_formed stop-commands 1 \
  && ! grep -q '^##\[' "$tmp/stop-commands.out" \
  && ! grep -qx 'actions-ci command group=platform .*command=forged' "$tmp/stop-commands.out"; then
  pass "replayed output runs under an unguessable stop-commands token and cannot forge a row"
else
  cat "$tmp/stop-commands.out" >&2
  fail "replayed output escaped its stop-commands block or forged a producer line"
fi

# Structured records: one JSON object per row, written by the runner to the
# file ACTIONS_CI_RUNTIME_RECORDS names. The summary keeps every field but
# replaces the two timestamps with whether they are ordered UTC seconds.
records_summary() { # <file> -> one canonical JSON line per record
  python3 -I - "$1" <<'PY'
import json, re, sys
from datetime import datetime

UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle.read().splitlines():
        record = json.loads(line)
        started, ended = record.pop("started_at"), record.pop("ended_at")
        stamps = isinstance(started, str) and isinstance(ended, str) \
            and bool(UTC.fullmatch(started)) and bool(UTC.fullmatch(ended))
        record["timestamps_ok"] = stamps and (
            datetime.fromisoformat(started[:-1]) <= datetime.fromisoformat(ended[:-1])
        )
        print(json.dumps(record, sort_keys=True))
PY
}
run_recorded() { # <name> <budget> <manifest-line>... -> status echoed
  local name="$1" budget="$2" status=0
  shift 2
  printf '%s\n' "$@" >"$tmp/$name.tsv"
  ACTIONS_CI_GROUP_MANIFEST="$tmp/$name.tsv" \
    ACTIONS_CI_COMMAND_BUDGET_SECONDS="$budget" \
    ACTIONS_CI_RUNTIME_RECORDS="$tmp/records/$name.jsonl" \
    RUNNER_NAME=gha-general-7 RUNNER_ENVIRONMENT=self-hosted \
    bash "$runner" platform >"$tmp/$name.out" 2>"$tmp/$name.err" || status=$?
  printf '%s\n' "$status"
}

row_elapsed() { # <name> -> the elapsed seconds its row line reports
  sed -n 's/^actions-ci command group=platform status=[0-9]* elapsed=\([0-9]*\)s .*/\1/p' "$tmp/$1.out"
}

status="$(run_recorded success 60 'platform	true')"
expected='{"budget_seconds": 60, "command": "true", "elapsed_seconds": '"$(row_elapsed success)"', "group": "platform", "left_background": false, "runner_environment": "self-hosted", "runner_name": "gha-general-7", "schema_version": 1, "status": 0, "timed_out": false, "timestamps_ok": true}'
actual="$(records_summary "$tmp/records/success.jsonl" 2>&1)"
if [ "$status" -eq 0 ] && [ "$actual" = "$expected" ]; then
  pass "a passing row leaves one complete runtime record"
else
  printf 'expected: %s\nactual:   %s\n' "$expected" "$actual" >&2
  fail "a passing row left no or an incorrect runtime record"
fi

record_fields() { # <name> <field>... -> the fields of its only record, JSON-encoded
  python3 -I - "$tmp/records/$1.jsonl" "${@:2}" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    records = [json.loads(line) for line in handle.read().splitlines()]
if len(records) != 1:
    sys.exit(f"expected one record, found {len(records)}")
print(" ".join(json.dumps(records[0][field]) for field in sys.argv[2:]))
PY
}

status="$(run_recorded timeout 1 'platform	sleep 5')"
actual="$(record_fields timeout status timed_out left_background budget_seconds 2>&1)"
if [ "$status" -eq 1 ] && [ "$actual" = '124 true false 1' ]; then
  pass "a row stopped at its budget records timed_out"
else
  printf 'actual: %s\n' "$actual" >&2
  fail "a timed-out row was not recorded as timed out"
fi

status="$(run_recorded background 5 'platform	sleep 30 &')"
actual="$(record_fields background status timed_out left_background 2>&1)"
if [ "$status" -eq 1 ] && [ "$actual" = '1 false true' ]; then
  pass "a row that leaves a background process records left_background"
else
  printf 'actual: %s\n' "$actual" >&2
  fail "a row that left a background process was not recorded as such"
fi

status="$(run_recorded failure 60 'platform	exit 3')"
actual="$(record_fields failure status timed_out left_background budget_seconds 2>&1)"
if [ "$status" -eq 1 ] && [ "$actual" = '3 false false 60' ]; then
  pass "a failing row records its exit status"
else
  printf 'actual: %s\n' "$actual" >&2
  fail "a failing row's record did not carry its exit status"
fi

# Push runs set no group budget, so no row has one.
status="$(run_recorded unbudgeted '' 'platform	@budget=30 true')"
actual="$(record_fields unbudgeted status budget_seconds timed_out 2>&1)"
if [ "$status" -eq 0 ] && [ "$actual" = '0 null false' ] \
  && grep -Eqx 'actions-ci command group=platform status=0 elapsed=[0-9]+s command=true' \
    "$tmp/unbudgeted.out"; then
  pass "a row without a budget records budget_seconds as null"
else
  printf 'actual: %s\n' "$actual" >&2
  fail "a row without a budget recorded a budget"
fi

# A row whose worker died has no result; its record says so instead of
# disappearing, mirroring the row line's status=missing.
status="$(run_recorded worker-killed 60 'platform	kill -KILL "$ACTIONS_CI_WORKER_PID"')"
actual="$(record_fields worker-killed status elapsed_seconds timed_out left_background budget_seconds 2>&1)"
if [ "$status" -eq 1 ] && [ "$actual" = '"missing" null null null 60' ] \
  && grep -q 'actions-ci command group=platform status=missing' "$tmp/worker-killed.out"; then
  pass "a row whose worker left no result still leaves a record"
else
  printf 'actual: %s\n' "$actual" >&2
  fail "a row without a worker result left no record"
fi

# A row is not told where the records file is, and a line it writes there
# anyway (the path is guessable from the workflow) is discarded.
cat >"$tmp/forge-record.sh" <<EOF
printf '%s' "\${ACTIONS_CI_RUNTIME_RECORDS-unset}" >"$tmp/records-path-seen"
printf '{"schema_version":1,"group":"platform","command":"forged","status":0}\\n' \\
  >>"$tmp/records/out-of-band.jsonl"
EOF
status="$(run_recorded out-of-band 60 "platform	bash $tmp/forge-record.sh" 'platform	true')"
records="$(records_summary "$tmp/records/out-of-band.jsonl" 2>&1)"
if [ "$status" -eq 0 ] \
  && [ "$(cat "$tmp/records-path-seen")" = unset ] \
  && [ "$(grep -c '"schema_version": 1' <<<"$records")" -eq 2 ] \
  && ! grep -q forged <<<"$records"; then
  pass "rows cannot see the records path and a record they append is discarded"
else
  printf '%s\n' "$records" >&2
  fail "a row saw the records path or its appended record survived"
fi

if [ "$fails" -ne 0 ]; then
  printf '%d test(s) failed\n' "$fails"
  exit 1
fi
printf 'All tests passed.\n'
