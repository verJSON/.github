#!/usr/bin/env bash
# Tests the reusable node-ci.yml optional Postgres DB-service step
# (Verjson/.github#108). The step is default-off: callers that don't set
# `db-image` get no database and no behavior change. When `db-image` is set, the
# step starts Postgres via `docker run` (there is no `if:` on a `services:`
# block, and an empty image is a hard error, so a conditional step is the only
# non-breaking toggle) and exports the caller's `db-env` pairs — including
# DATABASE_URL — to `$GITHUB_ENV` for later steps. It must also stay safe when
# two DB-backed jobs share one self-hosted host (#116). This extracts the exact
# `run:` block from node-ci.yml (single source of truth, so the test can't
# drift) and exercises it against a stubbed `docker`. Plain bash + awk; no
# test-framework dependency (runs on the bare pool).
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="$repo_root/.github/workflows/node-ci.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# (a) Default-off: the DB step must be guarded by an `inputs.db-image` check so
# callers that leave it empty never start a database.
guard="$(awk '
  $0 == "        id: db-service" { seen = 1 }
  seen && $0 ~ /^        if:/ { print; exit }
' "$wf")"
printf '%s' "$guard" | grep -F "inputs.db-image != ''" >/dev/null \
  && pass "DB step is guarded by inputs.db-image (default-off, no DB for current callers)" \
  || fail "DB step is not gated on inputs.db-image (would force a DB on every caller)"

# Extract the DB step's run script verbatim (10-space-indented body under
# `run: |`, scoped to the step with `id: db-service`). Stop at the next step's
# `- name:`, or the teardown step's own `run: |` gets appended to the start
# script and the harness silently exercises the two together.
script="$tmp/db.sh"
awk '
  $0 == "        id: db-service" { seen = 1 }
  seen && $0 ~ /^      - name:/ { exit }
  seen && $0 == "        run: |" { cap = 1; next }
  cap {
    if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
    if ($0 ~ /^[ \t]*$/) { print ""; next }
    cap = 0
  }
' "$wf" >"$script"
if ! grep -q 'GITHUB_ENV' "$script" || ! grep -q 'docker run' "$script"; then
  echo "FAIL - could not extract DB-service run block from $wf"
  echo "$fails test(s) failed."
  exit 1
fi

# Stub `docker`: `run` succeeds and records its args; `exec ... pg_isready`
# reports ready immediately so the health-wait loop breaks on the first probe;
# `port` reports the host port the daemon assigned this invocation's container.
mkdir -p "$tmp/bin"
cat >"$tmp/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_LOG"
case "${1:-}" in
  run)
    # `docker run -d` prints the container ID it assigned. When $DOCKER_NAMES is
    # set the stub also enforces the daemon's real rule that a name is unique on
    # the host, so a name collision can be modelled.
    name=""; prev=""
    for arg in "$@"; do [ "$prev" = "--name" ] && name="$arg"; prev="$arg"; done
    if [ -n "${DOCKER_NAMES:-}" ]; then
      if grep -qxF "$name" "$DOCKER_NAMES" 2>/dev/null; then
        printf 'docker: Error response from daemon: Conflict. The container name "/%s" is already in use.\n' \
          "$name" >&2
        exit 125
      fi
      printf '%s\n' "$name" >>"$DOCKER_NAMES"
    fi
    seq=$(( $(cat "$DOCKER_IDSEQ" 2>/dev/null || echo 0) + 1 ))
    printf '%s\n' "$seq" >"$DOCKER_IDSEQ"
    printf 'c0ffeeba5e%06d\n' "$seq" | tee "$DOCKER_LAST_ID"
    ;;
  # `MAPPED_PORT=` (set but empty) models a daemon that reports no host mapping.
  port) printf '%s:%s\n' "${MAPPED_HOST:-127.0.0.1}" "${MAPPED_PORT-0}" ;;
  inspect)
    if [ "${INSPECT_REQUIRE_LAST_ID:-0}" = 1 ] \
      && [ "${*: -1}" != "$(cat "$DOCKER_LAST_ID")" ]; then
      echo "inspect did not receive the exact started container ID" >&2
      exit 1
    fi
    printf '%s\n' "${CONTAINER_IP:-172.17.0.2}"
    ;;
  # `docker ps` REJECTS a filter it does not implement rather than returning an
  # empty list — `until` in particular is prune-only. Model that refusal, or a
  # sweep built on an invalid filter looks alive while sweeping nothing.
  ps)
    # $PS_FAIL models a daemon that refuses to list at all.
    [ -z "${PS_FAIL:-}" ] || { printf '%s\n' "$PS_FAIL" >&2; exit 1; }
    prev=""
    for arg in "$@"; do
      if [ "$prev" = "--filter" ]; then
        case "${arg%%=*}" in
          label|name|status|id|ancestor|before|since|health|exited|network|volume|publish|expose) ;;
          *) printf "Error response from daemon: invalid filter '%s'\n" "${arg%%=*}" >&2; exit 1 ;;
        esac
      fi
      prev="$arg"
    done
    printf '%s' "${STALE_CONTAINERS:-}"
    ;;
  # $RM_FAIL models a removal the daemon refuses, message and all.
  rm) [ -z "${RM_FAIL:-}" ] || { printf '%s\n' "$RM_FAIL" >&2; exit 1; } ;;
  # `exec` backs the health-check probe (#986). The full invocation, including
  # every token of the caller's health command, is already captured verbatim by
  # the unconditional log line above — proof the value reaches `docker exec` as
  # literal argv, never through a shell that could reinterpret it. $HEALTH_CMD_FAIL
  # models a health command that never succeeds.
  exec) [ -z "${HEALTH_CMD_FAIL:-}" ] || exit 1 ;;
esac
exit 0
DOCKER
chmod +x "$tmp/bin/docker"
cat >"$tmp/bin/timeout" <<'TIMEOUT'
#!/usr/bin/env bash
host="${@: -2:1}"
port="${@: -1}"
case ",${UNREACHABLE_ENDPOINTS:-}," in
  *",$host:$port,"*) exit 1 ;;
  *) exit 0 ;;
esac
TIMEOUT
chmod +x "$tmp/bin/timeout"
# A no-op `sleep`: only the health-retry-exhaustion test drives all 30 attempts,
# and a real 2s sleep per attempt would cost ~a minute of wall clock for a check
# whose content (not timing) is what's under test.
cat >"$tmp/bin/sleep" <<'SLEEP'
#!/usr/bin/env bash
exit 0
SLEEP
chmod +x "$tmp/bin/sleep"
export PATH="$tmp/bin:$PATH"

export DB_IMAGE="pgvector/pgvector:pg16"
# Single-quoted: `${DB_PORT}` is the literal placeholder the caller writes, and
# the step — not this test's shell — is what must expand it.
export DB_ENV='POSTGRES_USER=app
POSTGRES_PASSWORD=secret
POSTGRES_DB=app_test
DATABASE_URL=postgres://app:secret@localhost:${DB_PORT}/app_test'
# Run-level identity is shared by every job of one workflow run — it is exactly
# what two DB-backed jobs of the same run cannot be told apart by.
export GITHUB_RUN_ID="7000000001"
export GITHUB_RUN_ATTEMPT="1"

# Run the extracted step in its own sandbox so two "concurrent jobs" can be
# compared: each invocation gets a private docker log, $GITHUB_ENV and
# $GITHUB_OUTPUT. Trailing KEY=VALUE args apply to that invocation only.
run_db_step() {
  local box="$tmp/$1"; shift
  mkdir -p "$box"
  : >"$box/docker.log"; : >"$box/github_env"; : >"$box/github_output"
  env DOCKER_LOG="$box/docker.log" DOCKER_IDSEQ="$tmp/docker-idseq" \
    DOCKER_LAST_ID="$box/docker-last-id" \
      GITHUB_ENV="$box/github_env" GITHUB_OUTPUT="$box/github_output" "$@" \
      bash -eo pipefail "$script" >"$box/out.txt" 2>&1
}

# The container name the step actually used, read back from the docker stub log.
container_name_of() {
  local name
  IFS= read -r name < <(sed -n 's/.*--name \([^ ]*\).*/\1/p' "$tmp/$1/docker.log") || true
  printf '%s\n' "$name"
}

# The handle the step published for teardown to consume.
published_handle_of() {
  local handle
  IFS= read -r handle < <(sed -n 's/^container-id=//p' "$tmp/$1/github_output") || true
  printf '%s\n' "$handle"
}

# The first `-p` flag the step passed, for the diagnostic below. Reading with
# `read` keeps the first-line read off a truncated pipe (#1445); these three
# helpers all took `| head -n1` before that.
published_port_flag_of() {
  local flag
  IFS= read -r flag < <(grep -o -- '-p [^ ]*' "$tmp/$1/docker.log") || true
  printf '%s\n' "$flag"
}

# Two DB-backed jobs sharing one self-hosted host. Inside the reusable they even
# share a job id (`build-test`) — what differs is the runner process serving
# them, because a runner executes one job at a time.
job_a=(GITHUB_JOB=build-test MAPPED_PORT=49187
       RUNNER_NAME=gcp-pool-1 RUNNER_WORKSPACE=/runner-1/_work/api)
job_b=(GITHUB_JOB=build-test MAPPED_PORT=49188
       RUNNER_NAME=gcp-pool-2 RUNNER_WORKSPACE=/runner-2/_work/web)

# (b) When db-image is set, DATABASE_URL (and the POSTGRES_* pairs) reach the
# test step via $GITHUB_ENV, and the POSTGRES_* pairs are passed into the
# container.
run_db_step job-a "${job_a[@]}"
rc=$?
[ "$rc" -eq 0 ] || { echo "---- db step output ----"; cat "$tmp/job-a/out.txt"; }

grep -qE '^DATABASE_URL=postgres://app:secret@localhost:[0-9]+/app_test$' "$tmp/job-a/github_env" \
  && pass "DATABASE_URL is exported to \$GITHUB_ENV for the test step" \
  || fail "DATABASE_URL was not written to \$GITHUB_ENV"

grep -qF 'POSTGRES_DB=app_test' "$tmp/job-a/github_env" \
  && pass "POSTGRES_* pairs are exported to \$GITHUB_ENV" \
  || fail "POSTGRES_* pairs were not written to \$GITHUB_ENV"

grep -qF -- '-e POSTGRES_USER=app' "$tmp/job-a/docker.log" \
  && pass "POSTGRES_* env is passed into the database container" \
  || fail "POSTGRES_* env was not passed into the container"

grep -qF -- "$DB_IMAGE" "$tmp/job-a/docker.log" \
  && pass "the caller-supplied db-image is the container started" \
  || fail "the caller db-image was not started"

# (c) Concurrency: run_id/run_attempt alone are shared by every job of a run, so
# a name scoped only by them collides the moment two DB-backed jobs land on one
# host (#116). The step must fold the job's own identity into the name.
run_db_step job-b "${job_b[@]}"
rc=$?
[ "$rc" -eq 0 ] || { echo "---- db step output ----"; cat "$tmp/job-b/out.txt"; }
name_a="$(container_name_of job-a)"
name_b="$(container_name_of job-b)"
{ [ -n "$name_a" ] && [ -n "$name_b" ] && [ "$name_a" != "$name_b" ]; } \
  && pass "two concurrent DB-backed jobs on one host get distinct container names" \
  || fail "both jobs used the container name '$name_a' (the second docker run would fail: name already in use)"

# (c2) Runner identity is what separates two jobs of one run, and only one of the
# two parts needs to differ: a runner name may repeat across hosts while the
# workspace path does not (and vice versa).
run_db_step job-same-name GITHUB_JOB=build-test MAPPED_PORT=49401 \
  RUNNER_NAME=gcp-pool-1 RUNNER_WORKSPACE=/runner-1/_work/api
run_db_step job-same-ws GITHUB_JOB=build-test MAPPED_PORT=49402 \
  RUNNER_NAME=gcp-pool-1 RUNNER_WORKSPACE=/runner-1/_work/web
[ "$(container_name_of job-same-name)" != "$(container_name_of job-same-ws)" ] \
  && pass "jobs differing in only one of RUNNER_NAME/RUNNER_WORKSPACE still get distinct names" \
  || fail "both jobs used '$(container_name_of job-same-name)' (a differing workspace alone did not separate them)"

# (c3) The name premise can fail outright: with GITHUB_JOB constant inside this
# reusable and RUNNER_NAME/RUNNER_WORKSPACE both absent, two jobs of one run hash
# identically. Docker then refuses the second `docker run --name`, and that job
# MUST fail with no handle to tear down — a name-based handle would let it
# `docker rm -f` the FIRST job's live container and kill a healthy job.
clash_names="$tmp/docker-names"; : >"$clash_names"
run_db_step job-clash-a GITHUB_JOB=build-test MAPPED_PORT=49501 \
  RUNNER_NAME= RUNNER_WORKSPACE= DOCKER_NAMES="$clash_names"
rc_clash_a=$?
run_db_step job-clash-b GITHUB_JOB=build-test MAPPED_PORT=49502 \
  RUNNER_NAME= RUNNER_WORKSPACE= DOCKER_NAMES="$clash_names"
rc_clash_b=$?
{ [ "$rc_clash_a" -eq 0 ] && [ "$rc_clash_b" -ne 0 ] \
    && [ -n "$(published_handle_of job-clash-a)" ] \
    && [ -z "$(published_handle_of job-clash-b)" ]; } \
  && pass "a name collision fails the losing job and gives it no handle to tear down" \
  || fail "collision left rc_a=$rc_clash_a rc_b=$rc_clash_b handle_b='$(published_handle_of job-clash-b)' (job B could remove job A's container)"

# (d) The container must publish on an OS-assigned host port: a fixed 5432:5432
# bind fails outright for the second DB-backed job on the host (#116).
{ grep -qE -- '-p [^ ]*::5432' "$tmp/job-a/docker.log" \
    && ! grep -qE -- '-p [0-9.]+:5432 ' "$tmp/job-a/docker.log"; } \
  && pass "the container publishes on an OS-assigned host port (no fixed bind)" \
  || fail "the container binds a fixed host port (a second DB-backed job on the host cannot start): $(published_port_flag_of job-a)"

# (d2) A leaked container used to announce itself by breaking the next job's
# 5432 bind; with an OS-assigned port it is invisible and accumulates on a
# persistent host. So the container is labelled, and each start sweeps aged
# containers carrying that label — best-effort, and scoped by the label so it can
# never touch anything that isn't ours.
grep -qF -- '--label verjson-ci=1' "$tmp/job-a/docker.log" \
  && pass "the database container is labelled so leaks can be found later" \
  || fail "the container carries no verjson-ci label (a leak would be unidentifiable)"

# The sweep is age-bounded, so it must be exercised against containers that
# carry a real creation time — `docker ps` prints `{{.CreatedAt}}` as
# `YYYY-MM-DD HH:MM:SS ±ZZZZ TZ`. Two leaked containers are old enough to reap;
# the third was created moments ago and stands in for a CONCURRENTLY RUNNING
# job's container, which the sweep must never remove.
docker_created_at() { date -d "$1" '+%Y-%m-%d %H:%M:%S %z %Z'; }
sweep_listing="aa11bb22 $(docker_created_at '8 hours ago')
cc33dd44 $(docker_created_at '3 days ago')
beefface $(docker_created_at '2 minutes ago')"
run_db_step job-sweep "${job_a[@]}" STALE_CONTAINERS="$sweep_listing"
rc=$?
{ [ "$rc" -eq 0 ] \
    && grep -qE '^ps .*label=verjson-ci=1' "$tmp/job-sweep/docker.log" \
    && grep -qE '^rm -f .*aa11bb22' "$tmp/job-sweep/docker.log" \
    && grep -qE '^rm -f .*cc33dd44' "$tmp/job-sweep/docker.log"; } \
  && pass "aged containers carrying the CI label are swept before the job's own starts" \
  || fail "step exited $rc without sweeping leaked labelled containers: $(grep -E '^(ps|rm) ' "$tmp/job-sweep/docker.log" | tr '\n' ' ')"

# ...and the age bound is the whole safety argument: a container younger than the
# 6h GitHub job cap may belong to a job running RIGHT NOW on this host, so
# removing it would kill a healthy concurrent job (#116).
! grep -qE '^rm .*beefface' "$tmp/job-sweep/docker.log" \
  && pass "the sweep leaves a freshly-created container alone (it may be a live job's)" \
  || fail "the sweep removed a container created 2 minutes ago — it can kill a concurrent job: $(grep -E '^rm ' "$tmp/job-sweep/docker.log" | tr '\n' ' ')"

# ...and a sweep that cannot run must SAY so. A best-effort cleanup that
# swallows its own failure is indistinguishable from one that works — which is
# how an invalid `--filter until=6h` sat here as a permanent no-op.
run_db_step job-sweep-blind "${job_a[@]}" \
  PS_FAIL="Error response from daemon: invalid filter 'nope'"
rc=$?
{ [ "$rc" -eq 0 ] && grep -qF "invalid filter 'nope'" "$tmp/job-sweep-blind/out.txt"; } \
  && pass "a sweep that cannot list containers warns instead of silently sweeping nothing" \
  || fail "step exited $rc and swallowed the listing failure: $(cat "$tmp/job-sweep-blind/out.txt")"

# ...and a container whose creation time cannot be read is left alone WITH a
# warning: an unreadable age must never be treated as "old enough to remove",
# because the container may be a live job's.
run_db_step job-sweep-badtime "${job_a[@]}" \
  STALE_CONTAINERS="deadbeef not-a-timestamp"
rc=$?
{ [ "$rc" -eq 0 ] \
    && ! grep -qE '^rm .*deadbeef' "$tmp/job-sweep-badtime/docker.log" \
    && grep -qF 'deadbeef' "$tmp/job-sweep-badtime/out.txt"; } \
  && pass "a container with an unreadable creation time is warned about, not swept" \
  || fail "step exited $rc for an unparsable creation time: $(cat "$tmp/job-sweep-badtime/out.txt")"

# ...and so is a removal the daemon REFUSES. The removal is deliberately
# best-effort — a leak must not fail an otherwise healthy job — but silence is
# the failure mode that made the old sweep dead code: the container is still
# squatting on the host and, with an OS-assigned port, nothing downstream will
# notice it. So the step must warn AND still succeed.
run_db_step job-sweep-rmfail "${job_a[@]}" \
  STALE_CONTAINERS="$sweep_listing" \
  RM_FAIL="Error response from daemon: cannot remove container: device or resource busy"
rc=$?
{ [ "$rc" -eq 0 ] \
    && grep -qE 'warning:.*remove.*aa11bb22' "$tmp/job-sweep-rmfail/out.txt"; } \
  && pass "a sweep removal the daemon refuses warns instead of failing or going silent" \
  || fail "step exited $rc and swallowed the removal failure: $(cat "$tmp/job-sweep-rmfail/out.txt")"

# (e) The published port is OS-assigned, so the caller's DATABASE_URL cannot name
# it up front. The contract is an explicit placeholder: the caller writes
# `${DB_PORT}` where the port belongs and the step substitutes the real one. That
# is a literal token replacement — no scheme sniffing, no URL parsing — so the
# step can neither mangle a value it misreads nor miss one it fails to recognise.
grep -qF 'DATABASE_URL=postgres://app:secret@localhost:49187/app_test' "$tmp/job-a/github_env" \
  && pass "a \${DB_PORT} placeholder becomes the host port docker actually mapped" \
  || fail "exported DATABASE_URL does not use the mapped port 49187: $(grep '^DATABASE_URL=' "$tmp/job-a/github_env" || echo '<absent>')"

# `${DB_PORT}` is the ONLY placeholder spelling, and the brace-less `$DB_PORT` is
# REJECTED rather than substituted. It has no closing boundary, so `$DB_PORTX`
# silently becomes `49187X` while `${DB_PORT}X` is exact — a second spelling that
# can be got subtly wrong buys nothing. Rejection must be loud and name the fix:
# leaving the token unexpanded would ship a URL pointing at a literal `$DB_PORT`.
i=0
while IFS= read -r bare; do
  [ -n "$bare" ] || continue
  i=$((i + 1))
  run_db_step "job-bare-token-$i" "${job_a[@]}" "DB_ENV=POSTGRES_USER=app
$bare"
  rc=$?
  key="${bare%%=*}"
  { [ "$rc" -ne 0 ] \
      && ! grep -q "^$key=" "$tmp/job-bare-token-$i/github_env" \
      && grep -qF "$key" "$tmp/job-bare-token-$i/out.txt" \
      && grep -qF '${DB_PORT}' "$tmp/job-bare-token-$i/out.txt"; } \
    && pass "a brace-less \$DB_PORT is rejected by name, pointing at \${DB_PORT}: $bare" \
    || fail "step exited $rc and exported $(grep "^$key=" "$tmp/job-bare-token-$i/github_env" || echo 'nothing') for: $bare"
done <<'BARE'
DATABASE_URL=postgres://app@localhost:$DB_PORT/app_test
SUFFIXED_URL=postgres://app@localhost:$DB_PORTX/app_test
BARE

# Hardening: the placeholder is substituted wherever it appears, in shapes no URL
# parser handles — an IPv6-literal host, a `jdbc:` URL, a libpq keyword string, a
# quoted value, an `@` in the password. Each of these had to be REJECTED while
# the port was inferred by parsing; a literal token needs none of that
# understanding, so they all just work.
run_db_step job-token-shapes "${job_a[@]}" 'DB_ENV=POSTGRES_USER=app
IPV6_URL=postgres://app@[::1]:${DB_PORT}/app_test
JDBC_URL=jdbc:postgresql://localhost:${DB_PORT}/app_test
LIBPQ_DSN=host=localhost port=${DB_PORT} dbname=app
QUOTED_URL="postgres://app@localhost:${DB_PORT}/app_test"
AT_PASSWORD_URL=postgres://app:p@ss@localhost:${DB_PORT}/app_test
PGHOST=127.0.0.1
PGPORT=${DB_PORT}'
rc=$?
[ "$rc" -eq 0 ] || { echo "---- db step output ----"; cat "$tmp/job-token-shapes/out.txt"; }
token_shape_misses=""
while IFS= read -r expected; do
  grep -qxF "$expected" "$tmp/job-token-shapes/github_env" \
    || token_shape_misses="$token_shape_misses [$expected]"
done <<EXPECTED
IPV6_URL=postgres://app@[::1]:49187/app_test
JDBC_URL=jdbc:postgresql://localhost:49187/app_test
LIBPQ_DSN=host=localhost port=49187 dbname=app
QUOTED_URL="postgres://app@localhost:49187/app_test"
AT_PASSWORD_URL=postgres://app:p@ss@localhost:49187/app_test
PGHOST=127.0.0.1
PGPORT=49187
EXPECTED
{ [ "$rc" -eq 0 ] && [ -z "$token_shape_misses" ]; } \
  && pass "the placeholder substitutes in IPv6/jdbc/libpq/quoted/@-password values alike" \
  || fail "step exited $rc and did not substitute:$token_shape_misses"

# Hardening: a value that hardcodes 5432 must be REJECTED by name, never
# rewritten behind the caller's back. With an OS-assigned port a literal 5432
# aims the suite at whatever else listens on the host's 5432 — on a persistent
# self-hosted runner that can be a real database a migration would truncate —
# and guessing which 5432 was meant is exactly the parsing this design removed.
i=0
while IFS= read -r shape; do
  [ -n "$shape" ] || continue
  i=$((i + 1))
  run_db_step "job-shape-$i" "${job_a[@]}" "DB_ENV=POSTGRES_USER=app
$shape"
  rc=$?
  key="${shape%%=*}"
  { [ "$rc" -ne 0 ] \
      && ! grep -q "^$key=" "$tmp/job-shape-$i/github_env" \
      && grep -qF "$key" "$tmp/job-shape-$i/out.txt" \
      && grep -qF 'DB_PORT' "$tmp/job-shape-$i/out.txt"; } \
    && pass "a hardcoded 5432 is rejected by name, pointing at \${DB_PORT}: $shape" \
    || fail "step exited $rc and exported $(grep "^$key=" "$tmp/job-shape-$i/github_env" || echo 'nothing') for: $shape"
done <<'SHAPES'
DATABASE_URL=postgres://app@localhost:5432/app_test
DATABASE_URL="postgres://app@localhost:5432/app_test"
DATABASE_URL=jdbc:postgresql://localhost:5432/app_test
DATABASE_URL=postgres://app@[::1]:5432/app_test
LIBPQ_DSN=host=localhost port=5432 dbname=app
PGPORT=5432
SHAPES

# ...but the rejection is scoped to a `port=5432` in KEYWORD position, and the
# left-hand side needs a boundary just as much as the right one does. Without it
# `*port=5432` matches any suffix, so `--report=5432` and `--export=5432` — which
# only contain "port" by accident, spelled inside another word — were rejected,
# contradicting the documented "a libpq `port=5432`" rule.
run_db_step job-word-boundary "${job_a[@]}" 'DB_ENV=POSTGRES_USER=app
EXTRA_ARGS=--report=5432
EXPORT_ARGS=--export=5432'
rc=$?
{ [ "$rc" -eq 0 ] \
    && grep -qxF 'EXTRA_ARGS=--report=5432' "$tmp/job-word-boundary/github_env" \
    && grep -qxF 'EXPORT_ARGS=--export=5432' "$tmp/job-word-boundary/github_env"; } \
  && pass "a 5432 after \"port\" spelled inside another word (--report=/--export=) is not a port" \
  || fail "step exited $rc and rejected an accidental 'port' substring: $(cat "$tmp/job-word-boundary/out.txt")"

# Hardening: everything that carries neither the placeholder nor a hardcoded 5432
# is exported BYTE-IDENTICAL. Each line below was mangled or hard-failed by the
# scheme-matching rewrite this design replaced: a non-postgres URL had the DB
# port spliced into it (`https://internal.svc/v1` -> `https://internal.svc:49187/v1`),
# a portless postgres URL with a query string was silently corrupted, and any
# value containing `port=<digits>` — `--port=3000`, `--inspect-port=9229`, even
# `--report=1` — failed the whole job. The step now inspects no schemes at all,
# so none of that is reachable.
passthrough='API_URL=https://internal.svc/v1
REDIS_URL=redis://cache/0
S3_URL=s3://bucket/key
SSLMODE_DSN=postgres://localhost?sslmode=require
PORTLESS_DSN=postgres://app@localhost/app_test
ALT_PORT_DSN=postgres://app@localhost:54322/app_test
PREFIX_PORT_URL=redis://localhost:54321/0
NON_PORT_URL=redis://localhost:5432a/0
PLAYWRIGHT_ARGS=--port=3000
NODE_ARGS=--inspect-port=9229
VITE_ARGS=--port=5173
EXTRA_ARGS=--report=1'
run_db_step job-passthrough "${job_a[@]}" "DB_ENV=$passthrough"
rc=$?
[ "$rc" -eq 0 ] || { echo "---- db step output ----"; cat "$tmp/job-passthrough/out.txt"; }
passthrough_misses=""
while IFS= read -r expected; do
  grep -qxF "$expected" "$tmp/job-passthrough/github_env" \
    || passthrough_misses="$passthrough_misses [$expected]"
done <<< "$passthrough"
{ [ "$rc" -eq 0 ] && [ -z "$passthrough_misses" ]; } \
  && pass "values with neither the placeholder nor a hardcoded 5432 are exported byte-identical" \
  || fail "step exited $rc and did not pass through:$passthrough_misses"

# Hardening: pass-through must not become SILENT for the one shape that still
# resolves to a database port on its own — a postgres URL with no port at all
# defaults to libpq's 5432, so `postgres://localhost?sslmode=require` reaches
# whatever else listens on the host. The value is still exported untouched (no
# rewrite, no failed job), but the step says which key looks like it meant this
# database and never named it.
{ grep -qF 'SSLMODE_DSN' "$tmp/job-passthrough/out.txt" \
    && grep -qF 'PORTLESS_DSN' "$tmp/job-passthrough/out.txt" \
    && ! grep -qF 'REDIS_URL' "$tmp/job-passthrough/out.txt"; } \
  && pass "a postgres URL carrying no placeholder is flagged (exported, but not silently)" \
  || fail "no advisory for a portless postgres URL — it silently points the suite at host 5432: $(cat "$tmp/job-passthrough/out.txt")"

# Hardening: $GITHUB_ENV is last-wins, so a caller's own DB_PORT line — a
# plausible thing to write, since the step advertises $DB_PORT — must not
# overwrite the real mapped port for every later step.
run_db_step job-caller-dbport "${job_a[@]}" "DB_ENV=POSTGRES_USER=app
DB_PORT=5432"
rc=$?
{ [ "$rc" -eq 0 ] \
    && [ "$(grep -c '^DB_PORT=' "$tmp/job-caller-dbport/github_env")" -eq 1 ] \
    && [ "$(grep '^DB_PORT=' "$tmp/job-caller-dbport/github_env" | tail -n1)" = "DB_PORT=49187" ]; } \
  && pass "a caller-supplied DB_PORT cannot override the mapped port" \
  || fail "step exited $rc and left DB_PORT as: $(grep '^DB_PORT=' "$tmp/job-caller-dbport/github_env" | tr '\n' ' ')"

# Hardening: `db-env` is dotenv-shaped, so a `#` comment is a natural thing to
# write in the block scalar — and a line with no `=` reached $GITHUB_ENV RAW,
# where the runner failed the job with an opaque "Invalid format" that never
# names db-env. Comments and blank lines are skipped, at any indentation.
run_db_step job-comment "${job_a[@]}" 'DB_ENV=# postgres settings
POSTGRES_USER=app

  # indented note
DATABASE_URL=postgres://app@localhost:${DB_PORT}/app_test'
rc=$?
{ [ "$rc" -eq 0 ] \
    && grep -qxF 'DATABASE_URL=postgres://app@localhost:49187/app_test' "$tmp/job-comment/github_env" \
    && ! grep -q '#' "$tmp/job-comment/github_env"; } \
  && pass "db-env comments and blank lines are skipped, never written to \$GITHUB_ENV" \
  || fail "step exited $rc and wrote to \$GITHUB_ENV: $(tr '\n' ' ' <"$tmp/job-comment/github_env")"

# ...and any OTHER line that is not KEY=VALUE fails the step naming the offending
# line, rather than being deferred to the runner's context-free parse error.
run_db_step job-noneq "${job_a[@]}" 'DB_ENV=POSTGRES_USER=app
DATABASE_URL postgres://app@localhost:${DB_PORT}/app_test'
rc=$?
{ [ "$rc" -ne 0 ] \
    && grep -qF 'DATABASE_URL postgres' "$tmp/job-noneq/out.txt" \
    && ! grep -q 'DATABASE_URL' "$tmp/job-noneq/github_env" \
    && [ ! -s "$tmp/job-noneq/docker.log" ]; } \
  && pass "a db-env line that is not KEY=VALUE fails the step, quoting the line" \
  || fail "step exited $rc, wrote \$GITHUB_ENV, or started Docker before rejecting malformed input"

run_db_step job-invalid-key "${job_a[@]}" 'DB_ENV=BAD-KEY=value'
rc=$?
{ [ "$rc" -ne 0 ] \
    && grep -qF "db-env: 'BAD-KEY' is not a valid environment variable name" "$tmp/job-invalid-key/out.txt" \
    && [ ! -s "$tmp/job-invalid-key/github_env" ] \
    && [ ! -s "$tmp/job-invalid-key/docker.log" ]; } \
  && pass "an invalid db-env variable name fails before Docker or GITHUB_ENV" \
  || fail "db-env accepted an invalid variable name or started Docker before rejecting it"

# Caller data may configure tests, but it cannot change how later runner
# processes start or redirect the workflow's command files.
for key in BASH_ENV PATH NODE_OPTIONS LD_PRELOAD GITHUB_ENV GH_TOKEN GH_HOST gh_host \
  DOCKER_HOST DOCKER_CONTEXT docker_context \
    GIT_CONFIG_COUNT HTTPS_PROXY https_proxy FTP_PROXY ftp_proxy SSL_CERT_FILE GCONV_PATH PS4 \
    NPM_CONFIG_USERCONFIG Npm_Config_Userconfig NPM_CONFIG_GLOBALCONFIG \
    Npm_Config_Globalconfig npm_config_globalconfig NPM_CONFIG_HTTPS_PROXY \
    NPM_CONFIG_STRICT_SSL NPM_CONFIG_CAFILE; do
  run_db_step "job-runner-key-$key" "${job_a[@]}" "DB_ENV=$key=unsafe"
  rc=$?
  { [ "$rc" -ne 0 ] \
      && grep -qF "db-env: '$key' cannot override runner execution" "$tmp/job-runner-key-$key/out.txt" \
      && [ ! -s "$tmp/job-runner-key-$key/github_env" ] \
      && [ ! -s "$tmp/job-runner-key-$key/docker.log" ]; } \
    && pass "db-env rejects runner-control key $key before Docker or GITHUB_ENV" \
    || fail "db-env accepted runner-control key $key or started Docker before rejecting it"
done

# Prove the BASH_ENV path cannot reach a later token-bearing shell step.
db_env_hook="$tmp/db-env-hook.sh"
db_env_hook_marker="$tmp/db-env-hook-marker"
cat > "$db_env_hook" <<'HOOK'
printf '%s\n' "${GH_TOKEN:-}" > "$DB_ENV_HOOK_MARKER"
HOOK
run_db_step job-bash-env "${job_a[@]}" "DB_ENV=BASH_ENV=$db_env_hook"
rc=$?
exported_bash_env="$(sed -n 's/^BASH_ENV=//p' "$tmp/job-bash-env/github_env")"
if [ -n "$exported_bash_env" ]; then
  env -u BASH_ENV GH_TOKEN=fixture-token DB_ENV_HOOK_MARKER="$db_env_hook_marker" \
    BASH_ENV="$exported_bash_env" bash -c ':'
fi
{ [ "$rc" -ne 0 ] \
    && [ -z "$exported_bash_env" ] \
    && [ ! -e "$db_env_hook_marker" ] \
    && [ ! -s "$tmp/job-bash-env/docker.log" ]; } \
  && pass "a caller BASH_ENV script cannot run in a later token-bearing shell" \
  || fail "db-env exposed BASH_ENV to a later shell (token marker exists: $([ -e "$db_env_hook_marker" ] && echo yes || echo no))"

run_db_step job-cr-smuggle "${job_a[@]}" "DB_ENV=SAFE=ok"$'\r'"BASH_ENV=$db_env_hook"
rc=$?
{ [ "$rc" -ne 0 ] \
    && grep -qF 'db-env: carriage returns are not permitted' "$tmp/job-cr-smuggle/out.txt" \
    && [ ! -s "$tmp/job-cr-smuggle/github_env" ] \
    && [ ! -s "$tmp/job-cr-smuggle/docker.log" ]; } \
  && pass "a carriage return cannot smuggle BASH_ENV into GITHUB_ENV" \
  || fail "db-env accepted a carriage-return command-file injection"

# Hardening: readiness is probed with pg_isready INSIDE the container, which says
# nothing about the loopback publish — the thing this change actually introduced.
# The step must also probe the mapped port from the host and say so when it is
# not accepting connections (port 9/discard stands in for a dead publish).
run_db_step job-bare-host "${job_a[@]}" \
  'DB_ENV=DATABASE_URL=postgres://app@$DB_HOST:${DB_PORT}/app_test'
rc=$?
{ [ "$rc" -ne 0 ] && grep -qF 'brace-less $DB_HOST' "$tmp/job-bare-host/out.txt"; } \
  && pass "brace-less \$DB_HOST is rejected by name, pointing at \${DB_HOST}" \
  || fail "brace-less DB_HOST was not rejected directly: $(cat "$tmp/job-bare-host/out.txt")"

{ grep -qxF 'DB_HOST=127.0.0.1' "$tmp/job-a/github_env" \
  && grep -qxF 'DB_PORT=49187' "$tmp/job-a/github_env"; } \
  && pass "auto selects and exports a reachable loopback endpoint" \
  || fail "auto did not export the reachable loopback endpoint"

run_db_step job-hostprobe "${job_a[@]}" MAPPED_PORT=9 DB_HOST_MODE=loopback \
  UNREACHABLE_ENDPOINTS=127.0.0.1:9
rc=$?
{ [ "$rc" -ne 0 ] && grep -qF 'database endpoint 127.0.0.1:9 is not reachable' "$tmp/job-hostprobe/out.txt"; } \
  && pass "configured loopback fails early when unreachable" \
  || fail "configured unreachable loopback exited $rc without a direct diagnosis: $(cat "$tmp/job-hostprobe/out.txt")"

run_db_step job-container-fallback "${job_a[@]}" MAPPED_PORT=49189 \
  CONTAINER_IP=172.18.0.7 UNREACHABLE_ENDPOINTS=127.0.0.1:49189 \
  'DB_ENV=DATABASE_URL=postgres://app@${DB_HOST}:${DB_PORT}/app_test'
rc=$?
{ [ "$rc" -eq 0 ] \
  && grep -qxF 'DB_HOST=172.18.0.7' "$tmp/job-container-fallback/github_env" \
  && grep -qxF 'DB_PORT=5432' "$tmp/job-container-fallback/github_env" \
  && grep -qxF 'DATABASE_URL=postgres://app@172.18.0.7:5432/app_test' "$tmp/job-container-fallback/github_env"; } \
  && pass "auto selects the exact container endpoint when host loopback is isolated" \
  || fail "auto fallback exited $rc without exporting the container endpoint"

run_db_step job-container-dead "${job_a[@]}" DB_HOST_MODE=container \
  CONTAINER_IP=172.18.0.8 UNREACHABLE_ENDPOINTS=172.18.0.8:5432
rc=$?
{ [ "$rc" -ne 0 ] \
  && grep -qF 'database endpoint 172.18.0.8:5432 is not reachable' "$tmp/job-container-dead/out.txt" \
  && ! grep -q '^DATABASE_URL=' "$tmp/job-container-dead/github_env"; } \
  && pass "configured container endpoint fails before caller environment export when unreachable" \
  || fail "configured unreachable container endpoint did not fail early"

run_db_step job-invalid-host-mode "${job_a[@]}" DB_HOST_MODE=external
rc=$?
{ [ "$rc" -ne 0 ] \
  && grep -qF "db-host: 'external' is invalid; use auto, loopback, or container" \
    "$tmp/job-invalid-host-mode/out.txt" \
  && ! grep -q '^DATABASE_URL=' "$tmp/job-invalid-host-mode/github_env"; } \
  && pass "an invalid db-host selector fails closed before caller environment export" \
  || fail "invalid db-host selector did not fail closed"

for fixture in malformed out-of-range multi-network; do
  case "$fixture" in
    malformed) container_ip=not-an-ip ;;
    out-of-range) container_ip=172.18.0.256 ;;
    multi-network) container_ip=172.18.0.7172.19.0.8 ;;
  esac
  run_db_step "job-container-$fixture" "${job_a[@]}" DB_HOST_MODE=container \
    CONTAINER_IP="$container_ip"
  rc=$?
  { [ "$rc" -ne 0 ] \
    && grep -qF "could not read a valid database container bridge IPv4 from '$container_ip'" \
      "$tmp/job-container-$fixture/out.txt" \
    && ! grep -q '^DATABASE_URL=' "$tmp/job-container-$fixture/github_env"; } \
    && pass "$fixture Docker inspect address fails closed before environment export" \
    || fail "$fixture Docker inspect address was accepted or diagnosed ambiguously"
done

run_db_step job-inspect-identity "${job_a[@]}" DB_HOST_MODE=container \
  INSPECT_REQUIRE_LAST_ID=1
rc=$?
started_id="$(published_handle_of job-inspect-identity)"
{ [ "$rc" -eq 0 ] \
  && grep -qF "inspect --format {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} $started_id" \
    "$tmp/job-inspect-identity/docker.log"; } \
  && pass "container endpoint inspection is bound to the exact captured container ID" \
  || fail "container endpoint inspection was not bound to captured ID $started_id"

# Hardening: if the port can't be read back there is no usable connection at all,
# so the step must fail instead of exporting a URL pointing nowhere.
run_db_step job-noport "${job_a[@]}" MAPPED_PORT=
rc=$?
{ [ "$rc" -ne 0 ] && ! grep -q '^DATABASE_URL=' "$tmp/job-noport/github_env"; } \
  && pass "an unreadable port mapping fails the step instead of exporting a dead URL" \
  || fail "step exited $rc and exported $(grep '^DATABASE_URL=' "$tmp/job-noport/github_env" || echo 'no URL') despite an unreadable port"

# ...and it must say WHY. An image that exits immediately dies on this branch,
# before the health loop's log dump, so without logs here the operator gets only
# "could not read the mapped host port" and no cause.
grep -q '^logs ' "$tmp/job-noport/docker.log" \
  && pass "an unreadable port mapping dumps the container's logs for diagnosis" \
  || fail "the port-read failure branch exits without dumping container logs: $(cat "$tmp/job-noport/out.txt")"

# (f) A teardown step must always remove the container so a finished or failed
# job never leaks it onto the persistent self-hosted runner — AND it must stay
# gated on inputs.db-image so it only runs for DB-backed callers. Assert the two
# conditions are on the SAME `if:` line (#115): checking that `always()` merely
# appears somewhere would pass even if the db-image guard were dropped, making
# teardown run unconditionally on every caller.
teardown_if="$(awk '
  $0 == "      - name: Stop database service" { seen = 1; next }
  seen && $0 ~ /^        if:/ { print; exit }
  seen && $0 ~ /^      - name:/ { exit }
' "$wf")"
teardown_body="$(awk '
  $0 == "      - name: Stop database service" { seen = 1 }
  seen && $0 ~ /docker rm -f/ { print }
  seen && $0 ~ /^      - name:/ && $0 != "      - name: Stop database service" { exit }
' "$wf")"
{ printf '%s' "$teardown_if" | grep -F 'always()' >/dev/null \
    && printf '%s' "$teardown_if" | grep -F "inputs.db-image" >/dev/null \
    && printf '%s' "$teardown_body" | grep -F 'docker rm -f' >/dev/null; } \
  && pass "teardown if: combines always() with the inputs.db-image guard and removes the container" \
  || fail "teardown must gate always() on inputs.db-image on the same if: line (else it runs unconditionally)"

# (g) Teardown must remove the container the start step actually created, and the
# handle it consumes must be the ID docker assigned — not the name. The ID is
# unique by construction, so teardown can never reach another job's container
# even if the name's uniqueness premise fails; the name is kept for debugging.
teardown_script="$tmp/teardown.sh"
awk '
  $0 == "      - name: Stop database service" { seen = 1; next }
  seen && $0 ~ /^      - name:/ { exit }
  seen && $0 == "        run: |" { cap = 1; next }
  seen && cap {
    if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
    if ($0 ~ /^[ \t]*$/) { print ""; next }
    cap = 0
  }
  seen && !cap && $0 ~ /^        run: / { sub(/^        run: /, ""); print }
' "$wf" >"$teardown_script"
teardown_handle_expr="$(awk '
  $0 == "      - name: Stop database service" { seen = 1; next }
  seen && $0 ~ /^      - name:/ { exit }
  seen && $0 ~ /^          CONTAINER_ID: / { sub(/^          CONTAINER_ID: /, ""); print; exit }
' "$wf")"
# Resolve the teardown env expression the way the runner would, from the start
# step's published outputs and the run context.
id_a="$(published_handle_of job-a)"
resolved_handle="$teardown_handle_expr"
resolved_handle="${resolved_handle//\$\{\{ steps.db-service.outputs.container-id \}\}/$id_a}"
resolved_handle="${resolved_handle//\$\{\{ github.run_id \}\}/$GITHUB_RUN_ID}"
resolved_handle="${resolved_handle//\$\{\{ github.run_attempt \}\}/$GITHUB_RUN_ATTEMPT}"
mkdir -p "$tmp/teardown"
: >"$tmp/teardown/docker.log"
env DOCKER_LOG="$tmp/teardown/docker.log" CONTAINER_ID="$resolved_handle" \
  bash "$teardown_script" >"$tmp/teardown/out.txt" 2>&1
{ [ -n "$id_a" ] && [ "$id_a" != "$name_a" ] && grep -qF -- "rm -f $id_a" "$tmp/teardown/docker.log"; } \
  && pass "teardown removes the container by the ID docker assigned, not by name" \
  || fail "teardown targeted '$resolved_handle' but the start step created container id '$id_a' (name '$name_a')"

# The start step must also drive `docker port`/`docker exec` off that same ID, so
# every operation after `docker run` is pinned to the container it created.
{ grep -qE "^port $id_a " "$tmp/job-a/docker.log" \
    && grep -qE "^exec $id_a " "$tmp/job-a/docker.log"; } \
  && pass "the start step reads the port and probes readiness via the container ID" \
  || fail "start step used a non-ID handle: $(grep -E '^(port|exec) ' "$tmp/job-a/docker.log" | tr '\n' ' ')"

# Hardening: a removal the daemon refuses must fail the teardown step. With an
# OS-assigned port a leaked container no longer breaks the next job's bind, so
# this is the only signal that one is squatting on the host.
: >"$tmp/teardown/docker.log"
env DOCKER_LOG="$tmp/teardown/docker.log" CONTAINER_ID="$resolved_handle" \
  RM_FAIL="Error response from daemon: cannot remove container: device or resource busy" \
  bash "$teardown_script" >"$tmp/teardown/out-rmfail.txt" 2>&1
rc=$?
{ [ "$rc" -ne 0 ] && grep -qF 'device or resource busy' "$tmp/teardown/out-rmfail.txt"; } \
  && pass "teardown fails loudly when the container cannot be removed" \
  || fail "teardown exited $rc on a refused removal: $(cat "$tmp/teardown/out-rmfail.txt")"

# ...but a container that is already gone is not a failure: nothing is leaking.
: >"$tmp/teardown/docker.log"
env DOCKER_LOG="$tmp/teardown/docker.log" CONTAINER_ID="$resolved_handle" \
  RM_FAIL="Error: No such container: $resolved_handle" \
  bash "$teardown_script" >"$tmp/teardown/out-rmgone.txt" 2>&1
rc=$?
[ "$rc" -eq 0 ] \
  && pass "teardown tolerates a container that is already gone" \
  || fail "teardown exited $rc for an already-removed container: $(cat "$tmp/teardown/out-rmgone.txt")"

# Hardening: teardown also runs when the start step never reached `docker run`
# (its output is then empty) — it must be a no-op, not a `docker rm -f ""`.
: >"$tmp/teardown/docker.log"
env DOCKER_LOG="$tmp/teardown/docker.log" CONTAINER_ID="" \
  bash "$teardown_script" >"$tmp/teardown/out-empty.txt" 2>&1
[ ! -s "$tmp/teardown/docker.log" ] \
  && pass "teardown is a no-op when no container was started" \
  || fail "teardown called docker with no container id: $(cat "$tmp/teardown/docker.log")"

# The teardown must be the final step, after every consumer command and the
# best-effort cache upload. `always()` then makes it run after any earlier
# success or failure without destroying the database before npm test (#585).
python3 - "$wf" <<'PY' \
  && pass "database teardown is the final step after every consumer command" \
  || fail "database teardown can run before a consumer or cache step"
import sys
from pathlib import Path

import yaml

workflow = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
steps = workflow["jobs"]["build-test"]["steps"]
names = [step.get("name") for step in steps]
stop = names.index("Stop database service")
start = names.index("Start database service")
assert stop == len(steps) - 1
assert names[stop - 1] == "Stop cache service"
assert start < stop
commands = {
    "npm ci",
    "npm run build",
    "npm run typecheck --if-present",
    "npm test",
    "npm run lint --if-present",
}
for index, step in enumerate(steps):
    if step.get("run") in commands or step.get("name") in {
        "Install schema submodule deps",
        "Bound npm cache upload",
    }:
        assert start < index < stop
assert steps[stop]["if"] == (
    "always() && needs.eligibility.outputs.should-run != 'false' "
    "&& inputs.db-image != ''"
)
PY

# (h) The sweep's 6h bound is only safe while no job in THIS workflow can outlive
# it: a labelled container older than 6h cannot belong to a job still running.
# That rests on GitHub's default `timeout-minutes: 360` (a caller `uses:`-ing a
# reusable workflow cannot raise it, so the only way to break the invariant is to
# declare a longer timeout here). It is argued in a comment but nothing enforced
# it — a later `timeout-minutes: 720` on build-test would silently let the sweep
# remove a live job's database. Pin the invariant itself.
over_cap="$(awk '
  /^[[:space:]]*timeout-minutes:[[:space:]]*[0-9]+[[:space:]]*$/ {
    v = $0
    sub(/^[[:space:]]*timeout-minutes:[[:space:]]*/, "", v)
    sub(/[[:space:]]*$/, "", v)
    if (v + 0 > 360) print "line " FNR ": timeout-minutes: " v
  }
' "$wf")"
[ -z "$over_cap" ] \
  && pass "no job declares a timeout-minutes above the 6h bound the sweep relies on" \
  || fail "a timeout-minutes above 360 lets a job outlive the sweep's age bound, so the sweep can remove a LIVE container: $over_cap"

# (i) #986: a caller-supplied db-port changes which container-internal port is
# published and looked up. Every assertion above (none of which set
# DB_CONTAINER_PORT) already proves the unset default stays the literal 5432.
run_db_step job-custom-port "${job_a[@]}" DB_CONTAINER_PORT=7687 MAPPED_PORT=54321
rc=$?
{ [ "$rc" -eq 0 ] \
    && grep -qF -- '-p 127.0.0.1::7687' "$tmp/job-custom-port/docker.log" \
    && grep -qF -- 'port c0ffeeba5e' "$tmp/job-custom-port/docker.log" \
    && grep -qF -- '7687/tcp' "$tmp/job-custom-port/docker.log" \
    && grep -qxF 'DB_PORT=54321' "$tmp/job-custom-port/github_env"; } \
  && pass "db-port publishes and looks up the caller-selected container port" \
  || fail "step exited $rc without honoring a custom db-port: $(grep -E '^(run|port)' "$tmp/job-custom-port/docker.log" | tr '\n' ' ')"

# ...and the container-bridge fallback (auto/container mode) exports THAT port,
# not the hardcoded 5432 the fallback used before db-port existed.
run_db_step job-custom-port-fallback "${job_a[@]}" DB_CONTAINER_PORT=7687 \
  MAPPED_PORT=54322 CONTAINER_IP=172.20.0.9 UNREACHABLE_ENDPOINTS=127.0.0.1:54322
rc=$?
{ [ "$rc" -eq 0 ] \
    && grep -qxF 'DB_HOST=172.20.0.9' "$tmp/job-custom-port-fallback/github_env" \
    && grep -qxF 'DB_PORT=7687' "$tmp/job-custom-port-fallback/github_env"; } \
  && pass "the container-bridge fallback exports the caller-selected db-port, not a hardcoded 5432" \
  || fail "step exited $rc and did not export DB_PORT=7687 on fallback: $(cat "$tmp/job-custom-port-fallback/github_env")"

# (j) #986: a caller-supplied db-health-cmd replaces pg_isready, run the same way
# (docker exec against the started container, retried on failure).
run_db_step job-custom-health "${job_a[@]}" \
  'DB_HEALTH_CMD=cypher-shell -u neo4j -p neo4j RETURN 1'
rc=$?
id_custom_health="$(published_handle_of job-custom-health)"
{ [ "$rc" -eq 0 ] \
    && grep -qF -- "exec $id_custom_health cypher-shell -u neo4j -p neo4j RETURN 1" \
      "$tmp/job-custom-health/docker.log" \
    && ! grep -qF -- "exec $id_custom_health pg_isready" "$tmp/job-custom-health/docker.log"; } \
  && pass "db-health-cmd replaces pg_isready with the caller's command" \
  || fail "step exited $rc and did not run the caller's health command: $(grep -E '^exec ' "$tmp/job-custom-health/docker.log")"

# ...and the default, unset case still runs exactly `pg_isready` with no extra
# args ever appended — every earlier test in this file left DB_HEALTH_CMD unset.
grep -qE '^exec [^ ]+ pg_isready$' "$tmp/job-a/docker.log" \
  && pass "the default db-health-cmd invocation is exactly 'pg_isready', unchanged" \
  || fail "the default invocation is not the bare 'pg_isready' command: $(grep -E '^exec ' "$tmp/job-a/docker.log")"

# ...and it is a TRUST BOUNDARY, not a second shell: db-health-cmd is caller data
# executed in CI. It must reach `docker exec` as literal argv — split on
# whitespace only — and never through a shell/eval that would let a `;` or `$()`
# escape the container it targets and run on the runner itself. Proven here by
# planting a marker: the injected `touch` becomes an inert docker-exec ARGUMENT
# (logged verbatim by the stub), never an actual command the test's shell runs.
marker="$tmp/pwned-marker"
run_db_step job-health-injection "${job_a[@]}" \
  "DB_HEALTH_CMD=pg_isready; touch $marker"
rc=$?
id_injection="$(published_handle_of job-health-injection)"
{ [ "$rc" -eq 0 ] \
    && grep -qF -- "exec $id_injection pg_isready; touch $marker" \
      "$tmp/job-health-injection/docker.log" \
    && [ ! -e "$marker" ]; } \
  && pass "db-health-cmd reaches docker exec as literal argv; shell metacharacters cannot escape to the runner" \
  || fail "a ';'-separated db-health-cmd token was not passed literally, or the runner executed it (marker exists: $( [ -e "$marker" ] && echo yes || echo no ))"

# #1007: `read` without `-r` interprets a backslash rather than passing it
# through literally, which would silently corrupt any db-health-cmd token
# containing one (e.g. a Windows-style path or an escaped character some
# health-check tool expects verbatim). Prove the backslash survives intact.
run_db_step job-health-backslash "${job_a[@]}" \
  'DB_HEALTH_CMD=pg_isready --host=C:\pgdata'
rc=$?
id_backslash="$(published_handle_of job-health-backslash)"
{ [ "$rc" -eq 0 ] \
    && grep -qF -- "exec $id_backslash pg_isready --host=C:\\pgdata" \
      "$tmp/job-health-backslash/docker.log"; } \
  && pass "a backslash in db-health-cmd reaches docker exec literally, not interpreted by read" \
  || fail "the backslash in db-health-cmd was altered before reaching docker exec: $(grep -E '^exec ' "$tmp/job-health-backslash/docker.log")"

# (k) #986: a health command that never succeeds fails the step after the same
# bound pg_isready always had (30 attempts) — naming the image and the exact
# command, so the operator does not have to guess which changed engine failed.
run_db_step job-health-never-ready "${job_a[@]}" \
  'DB_HEALTH_CMD=cypher-shell -u neo4j -p neo4j RETURN 1' HEALTH_CMD_FAIL=1
rc=$?
{ [ "$rc" -ne 0 ] \
    && grep -qF "database image '$DB_IMAGE' failed health check 'cypher-shell -u neo4j -p neo4j RETURN 1' after 30 attempts" \
      "$tmp/job-health-never-ready/out.txt" \
    && ! grep -q '^DATABASE_URL=' "$tmp/job-health-never-ready/github_env"; } \
  && pass "a health command that never succeeds fails after 30 attempts, naming the image and command" \
  || fail "step exited $rc without naming the image/command on health-check exhaustion: $(cat "$tmp/job-health-never-ready/out.txt")"

# (l) #986 hardening: db-health-cmd must reach `docker exec` with NEITHER shell
# reinterpretation (already proven above) NOR bash's own pathname/glob
# expansion. An earlier revision expanded $DB_HEALTH_CMD unquoted, which
# undergoes word-splitting AND glob expansion — a command containing
# `*`/`?`/`[...]` that happened to match a file in the runner's working
# directory would silently gain extra argv the caller never wrote. Run the step
# from a directory salted with filenames that WOULD match the glob if expanded,
# and assert the literal `*` reaches docker unexpanded.
glob_cwd="$tmp/glob-cwd"
mkdir -p "$glob_cwd"
touch "$glob_cwd/pg_isready-extra" "$glob_cwd/pg_isready-other"
(
  cd "$glob_cwd" || exit 1
  run_db_step job-glob-payload "${job_a[@]}" 'DB_HEALTH_CMD=pg_isready-*'
)
rc=$?
{ [ "$rc" -eq 0 ] \
    && grep -qE '^exec [^ ]+ pg_isready-\*$' "$tmp/job-glob-payload/docker.log" \
    && ! grep -qF -- 'pg_isready-extra' "$tmp/job-glob-payload/docker.log" \
    && ! grep -qF -- 'pg_isready-other' "$tmp/job-glob-payload/docker.log"; } \
  && pass "a glob-shaped db-health-cmd reaches docker exec literally, never pathname-expanded" \
  || fail "db-health-cmd underwent pathname expansion: $(grep -E '^exec ' "$tmp/job-glob-payload/docker.log")"

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
else
  echo "$fails test(s) failed."
  exit 1
fi
