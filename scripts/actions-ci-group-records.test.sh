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

# Every replayed byte sits inside one `::stop-commands::<token>` block per row,
# closed by the same random token, with each line prefixed; every line outside
# a block is a line the runner itself produces. The Actions runner reads its
# output with .NET ReadLine, which also ends a line at a bare carriage return,
# so the check splits there too. <name> <expected-blocks>
stream_is_well_formed() {
  tr '\r' '\n' <"$tmp/$1.out" >"$tmp/$1.lines"
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
  ' "$tmp/$1.lines"
}

# (a) Run 39064216676: output ending in `##[endgroup]` with no newline is
# stored as a bare `##[endgroup]` and the producer's next line is lost.
run_stub unterminated 'output line\n##[endgroup]' >/dev/null
if has_one_row_line unterminated \
  && stream_is_well_formed unterminated 1 \
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
  && stream_is_well_formed leading-space 1 \
  && ! grep -Eq '^[[:space:]]*::add-mask::' "$tmp/leading-space.out" \
  && ! grep -q 'command group=$' "$tmp/leading-space.out" \
  && grep -q '\[workflow-command:add-mask\]' "$tmp/leading-space.out"; then
  pass "an indented add-mask is neither executed nor allowed to publish its value"
else
  cat "$tmp/leading-space.out" >&2
  fail "an indented add-mask survived replay or published the value it masks"
fi

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

# A bare carriage return starts a new stored line, so a prefix only at each
# newline would leave `\r##[endgroup]` and `\r::error::` unprefixed.
run_stub carriage-return 'progress 50%%\r##[endgroup]\r::error::forged\r\n' >/dev/null
if has_one_row_line carriage-return \
  && stream_is_well_formed carriage-return 1; then
  pass "a carriage return in output cannot start an unprefixed stored line"
else
  cat -A "$tmp/carriage-return.out" >&2
  fail "a carriage return in output started an unprefixed stored line"
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

# Outside Actions (a local run) the runner identity is unknown, not empty.
printf 'platform\ttrue\n' >"$tmp/no-runner.tsv"
env -u RUNNER_NAME -u RUNNER_ENVIRONMENT ACTIONS_CI_GROUP_MANIFEST="$tmp/no-runner.tsv" \
  ACTIONS_CI_RUNTIME_RECORDS="$tmp/records/no-runner.jsonl" \
  bash "$runner" platform >"$tmp/no-runner.out" 2>&1
actual="$(record_fields no-runner runner_name runner_environment 2>&1)"
if [ "$actual" = 'null null' ]; then
  pass "an unknown runner identity is recorded as null"
else
  printf 'actual: %s\n' "$actual" >&2
  fail "an unknown runner identity was recorded as a value"
fi

# Commands are recorded byte-for-byte: quotes, backslashes, non-ASCII text,
# and the carriage return a CRLF manifest leaves on every command.
printf 'platform\tprintf "%%s\\n" "caf\xc3\xa9 \xe2\x9c\x93 \\\\ q" >/dev/null\r\n' >"$tmp/escaping.tsv"
ACTIONS_CI_GROUP_MANIFEST="$tmp/escaping.tsv" \
  ACTIONS_CI_RUNTIME_RECORDS="$tmp/records/escaping.jsonl" \
  bash "$runner" platform >"$tmp/escaping.out" 2>&1
# Command substitution drops the newline and keeps the carriage return.
expected_command="$(cut -f2- "$tmp/escaping.tsv")"
if python3 -I - "$tmp/records/escaping.jsonl" "$expected_command" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    (line,) = handle.read().splitlines()
record = json.loads(line)
expected = sys.argv[2]
assert expected.endswith("\r") and "é ✓ \\\\ q" in expected, repr(expected)
assert record["command"] == expected, (record["command"], expected)
PY
then
  pass "commands with quotes, backslashes, unicode, and control characters round-trip as JSON"
else
  fail "a command was not recorded as exactly its manifest text"
fi

# A records path the runner cannot honor fails before any row runs: a relative
# path would land in the checkout copy the workflow step deletes, and an
# uncreatable one would silently drop every record.
: >"$tmp/not-a-directory"
printf 'platform\ttouch %q\n' "$tmp/row-ran" >"$tmp/bad-path.tsv"
bad_path_ok=1
for records_path in records.jsonl "$tmp/not-a-directory/records.jsonl"; do
  bad_status=0
  rm -f "$tmp/row-ran"
  (cd "$tmp" && ACTIONS_CI_GROUP_MANIFEST="$tmp/bad-path.tsv" \
    ACTIONS_CI_RUNTIME_RECORDS="$records_path" \
    bash "$runner" platform >"$tmp/bad-path.out" 2>&1) || bad_status=$?
  if [ "$bad_status" -ne 2 ] || [ -e "$tmp/row-ran" ] \
    || ! grep -q 'actions-ci runtime records' "$tmp/bad-path.out"; then
    printf 'records path %s: status %s\n' "$records_path" "$bad_status" >&2
    cat "$tmp/bad-path.out" >&2
    bad_path_ok=0
  fi
done
if [ "$bad_path_ok" -eq 1 ]; then
  pass "a relative or uncreatable records path fails before any row runs"
else
  fail "the runner ran rows without a usable records path"
fi

# A records file the runner can no longer write fails the group by name
# rather than passing with its records silently missing.
mkdir -p "$tmp/records-locked"
status="$(
  printf 'platform\tchmod 555 %q\n' "$tmp/records-locked" >"$tmp/locked.tsv"
  ACTIONS_CI_GROUP_MANIFEST="$tmp/locked.tsv" \
    ACTIONS_CI_RUNTIME_RECORDS="$tmp/records-locked/locked.jsonl" \
    bash "$runner" platform >"$tmp/locked.out" 2>"$tmp/locked.err" || printf '%s' "$?"
)"
chmod 755 "$tmp/records-locked"
if [ "${status:-0}" -eq 1 ] \
  && grep -q '^::error::.*runtime records could not be written' "$tmp/locked.out"; then
  pass "a records file that cannot be written fails the group"
else
  cat "$tmp/locked.out" "$tmp/locked.err" >&2
  fail "the group passed although its runtime records could not be written"
fi

# The row line verjson-observability#272 parses is byte-for-byte what the
# runner printed before this change, for ordinary output.
status="$(run_recorded plain 60 "platform	printf 'hello\\n'")"
printf -v expected_row 'actions-ci command group=platform status=0 elapsed=%ss budget=60s command=%q' \
  "$(row_elapsed plain)" "printf 'hello\\n'"
if [ "$status" -eq 0 ] && grep -qxF -- "$expected_row" "$tmp/plain.out" \
  && grep -qxF '| hello' "$tmp/plain.out" \
  && stream_is_well_formed plain 1; then
  pass "the row line format is byte-unchanged for ordinary output"
else
  printf 'expected row: %s\n' "$expected_row" >&2
  cat "$tmp/plain.out" >&2
  fail "the row line format changed for ordinary output"
fi

# Without a stop-commands token the output is withheld, never replayed bare.
mkdir -p "$tmp/broken-od"
printf '#!/usr/bin/env bash\nprintf "zz\\n"\n' >"$tmp/broken-od/od"
chmod +x "$tmp/broken-od/od"
printf 'platform\tprintf "secret-free-but-unguarded\\n"\n' >"$tmp/no-token.tsv"
no_token_status=0
PATH="$tmp/broken-od:$PATH" ACTIONS_CI_GROUP_MANIFEST="$tmp/no-token.tsv" \
  bash "$runner" platform >"$tmp/no-token.out" 2>&1 || no_token_status=$?
if [ "$no_token_status" -eq 1 ] \
  && grep -q '^::error::.*could not draw a stop-commands token' "$tmp/no-token.out" \
  && ! grep -q '^| secret-free-but-unguarded' "$tmp/no-token.out" \
  && grep -q 'actions-ci command group=platform status=1 ' "$tmp/no-token.out"; then
  pass "output is withheld and the row fails when no stop-commands token can be drawn"
else
  cat "$tmp/no-token.out" >&2
  fail "output was replayed without a stop-commands token or the row passed"
fi

# Concurrent rows finish in any order; records stay in manifest order.
status="$(ACTIONS_CI_COMMAND_PARALLELISM=3 run_recorded parallel 60 \
  'platform	sleep 2' 'platform	sleep 1' 'platform	true')"
actual="$(python3 -I -c '
import json, sys
print(" ".join(json.loads(line)["command"] for line in open(sys.argv[1], encoding="utf-8")))
' "$tmp/records/parallel.jsonl" 2>&1)"
if [ "$status" -eq 0 ] && [ "$actual" = 'sleep 2 sleep 1 true' ] \
  && stream_is_well_formed parallel 3; then
  pass "concurrent rows each leave one record, in manifest order"
else
  printf 'actual: %s\n' "$actual" >&2
  fail "concurrent rows lost, duplicated, or reordered their records"
fi

# A cancelled group keeps the records of the rows that already finished, so
# the always() upload still has them.
printf 'platform\ttrue\nplatform\tsleep 30\n' >"$tmp/cancelled.tsv"
ACTIONS_CI_COMMAND_PARALLELISM=2 ACTIONS_CI_GROUP_MANIFEST="$tmp/cancelled.tsv" \
  ACTIONS_CI_RUNTIME_RECORDS="$tmp/records/cancelled.jsonl" \
  bash "$runner" platform >"$tmp/cancelled.out" 2>&1 &
cancelled_pid=$!
for _ in {1..100}; do
  [ "$(wc -l <"$tmp/records/cancelled.jsonl" 2>/dev/null || echo 0)" -ge 1 ] && break
  sleep 0.1
done
kill -TERM "$cancelled_pid"
wait "$cancelled_pid"
cancelled_status=$?
actual="$(record_fields cancelled command status 2>&1)"
if [ "$cancelled_status" -ne 0 ] && [ "$actual" = '"true" 0' ]; then
  pass "a cancelled group keeps the records of its finished rows"
else
  printf 'status %s, actual: %s\n' "$cancelled_status" "$actual" >&2
  fail "a cancelled group lost the records of its finished rows"
fi

# The runner stages each rewrite under a name no row can predict, so a row
# that plants symlinks at the guessable staging names cannot redirect it. An
# `mv` shim logs each staging name so two runs can be compared.
mkdir -p "$tmp/mv-shim"
real_mv="$(command -v mv)"
cat >"$tmp/mv-shim/mv" <<EOF
#!/usr/bin/env bash
[ "\$#" -ne 4 ] || [ "\$1 \$2" != '-f --' ] || printf '%s\n' "\$3" >>"$tmp/staging-names"
exec "$real_mv" "\$@"
EOF
chmod +x "$tmp/mv-shim/mv"
cat >"$tmp/plant-staging.sh" <<EOF
records="\$1"
for suffix in .tmp .new '~' \$(pgrep -f "$runner" | sed 's/^/./'); do
  printf 'precious\n' >"$tmp/precious\$suffix"
  ln -sfn "$tmp/precious\$suffix" "\$records\$suffix"
done
EOF
staging_ok=1
: >"$tmp/staging-names"
for attempt in 1 2; do
  status="$(PATH="$tmp/mv-shim:$PATH" run_recorded "staging-$attempt" 60 \
    "platform	bash $tmp/plant-staging.sh $tmp/records/staging-$attempt.jsonl")"
  if [ "$status" -ne 0 ] || [ -L "$tmp/records/staging-$attempt.jsonl" ] \
    || [ "$(record_fields "staging-$attempt" status 2>&1)" != 0 ]; then
    staging_ok=0
  fi
done
for planted in "$tmp"/precious.* "$tmp"/precious~; do
  [ "$(cat "$planted")" = precious ] || { printf 'overwritten: %s\n' "$planted" >&2; staging_ok=0; }
done
# One rewrite per run; the two names differ once the per-run path is removed.
names="$(sed -E 's#/staging-[12]\.jsonl#/staging-N.jsonl#' "$tmp/staging-names")"
if [ "$staging_ok" -eq 1 ] && [ "$(grep -c '' <<<"$names")" -eq 2 ] \
  && [ "$(sort -u <<<"$names" | grep -c '')" -eq 2 ] \
  && ! grep -Eq '\.(tmp|new|[0-9]+)$|~$' <<<"$names"; then
  pass "staging names are unpredictable and planted symlinks cannot redirect the records"
else
  printf 'staging names:\n%s\n' "$(cat "$tmp/staging-names")" >&2
  fail "the records rewrite used a predictable staging name or followed a planted symlink"
fi

if [ "$fails" -ne 0 ]; then
  printf '%d test(s) failed\n' "$fails"
  exit 1
fi
printf 'All tests passed.\n'
