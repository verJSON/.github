#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/node-ci.yml"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fails=0
pass(){ printf 'ok   - %s\n' "$1"; }
fail(){ printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

guard="$(awk '$0=="        id: cache-service"{f=1} f&&/^        if:/{print;exit}' "$workflow")"
grep -qF "inputs.cache-image != ''" <<< "$guard" \
  && pass "cache service is default-off" || fail "cache service is not guarded by cache-image"

for workflow in "$root/.github/workflows/node-ci.yml" \
    "$root/.github/workflows/node-ci-protected.yml"; do
  if grep -Fq "DB_SERVICE_ENABLED: \${{ inputs.db-image != '' }}" "$workflow"; then
    pass "$(basename "$workflow") maps the database-service input to cache validation"
  else
    fail "$(basename "$workflow") does not protect DB_HOST/DB_PORT when db-image is enabled"
  fi
done

start="$tmp/start.sh"
awk '
  $0=="        id: cache-service"{f=1}
  f&&/^      - name:/{exit}
  f&&$0=="        run: |"{r=1;next}
  r { if(substr($0,1,10)=="          "){print substr($0,11);next}; if($0~/^[[:space:]]*$/){print "";next}; exit }
' "$workflow" > "$start"

teardown="$tmp/teardown.sh"
awk '
  $0=="      - name: Stop cache service"{f=1}
  f&&$0=="        run: |"{r=1;next}
  r { if(substr($0,1,10)=="          "){print substr($0,11);next}; if($0~/^[[:space:]]*$/){print "";next}; exit }
' "$workflow" > "$teardown"

mkdir "$tmp/bin"
cat > "$tmp/bin/docker" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_LOG"
case "$1" in
  run) printf 'cache-container-id\n' ;;
  port) printf '127.0.0.1:%s\n' "${MAPPED_PORT:-49321}" ;;
  # `exec` backs the health-check probe (#986). The default `redis-cli ping`
  # keeps replying PONG so the legacy exact-stdout check is unaffected;
  # $CACHE_HEALTH_FAIL models a caller-supplied health command that never
  # succeeds. Every token of the caller's command is already captured verbatim
  # by the unconditional log line above.
  exec)
    [ -z "${CACHE_HEALTH_FAIL:-}" ] || exit 1
    printf 'PONG\n'
    ;;
  ps) printf '%s' "${STALE_CONTAINERS:-}" ;;
  rm) [ -z "${RM_FAIL:-}" ] || { printf '%s\n' "$RM_FAIL" >&2; exit 1; } ;;
esac
SH
chmod +x "$tmp/bin/docker"
# A no-op `sleep`: only the health-retry-exhaustion test drives all 30 attempts,
# and a real 1s sleep per attempt would cost ~30s of wall clock for a check
# whose content (not timing) is what's under test.
cat > "$tmp/bin/sleep" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$tmp/bin/sleep"
export PATH="$tmp/bin:$PATH" CACHE_IMAGE=redis:8 CACHE_ENV='REDIS_URL=redis://127.0.0.1:${CACHE_PORT}
REDIS_MODE=test' GITHUB_RUN_ID=42 GITHUB_RUN_ATTEMPT=1 GITHUB_JOB=build-test
export RUNNER_NAME=runner-1 RUNNER_WORKSPACE=/runner/_work/example

run_start(){
  : > "$tmp/docker.log"; : > "$tmp/env"; : > "$tmp/output"
  DOCKER_LOG="$tmp/docker.log" GITHUB_ENV="$tmp/env" GITHUB_OUTPUT="$tmp/output" \
    MAPPED_PORT="${MAPPED_PORT:-49321}" DB_SERVICE_ENABLED="${DB_SERVICE_ENABLED:-false}" \
    bash -eo pipefail "$start" >"$tmp/out" 2>&1
}

run_start_preserving_env(){
  : > "$tmp/docker.log"; : > "$tmp/output"
  DOCKER_LOG="$tmp/docker.log" GITHUB_ENV="$tmp/env" GITHUB_OUTPUT="$tmp/output" \
    MAPPED_PORT="${MAPPED_PORT:-49321}" DB_SERVICE_ENABLED="${DB_SERVICE_ENABLED:-false}" \
    bash -eo pipefail "$start" >"$tmp/out" 2>&1
}

if run_start \
  && grep -Fxq 'CACHE_PORT=49321' "$tmp/env" \
  && grep -Fxq 'REDIS_URL=redis://127.0.0.1:49321' "$tmp/env" \
  && grep -Fq -- '-p 127.0.0.1::6379' "$tmp/docker.log" \
  && grep -Fq -- 'ps -a --filter label=verjson-ci-cache=1' "$tmp/docker.log" \
  && grep -Fq -- '-e REDIS_MODE=test' "$tmp/docker.log" \
  && grep -Fq 'exec cache-container-id redis-cli ping' "$tmp/docker.log"; then
  pass "cache starts on a dynamic loopback port and exports substituted environment"
else
  fail "cache start contract failed: $(tail -1 "$tmp/out")"
fi

if CACHE_ENV='DB_HOST=cache-only.invalid
DB_PORT=12345' DB_SERVICE_ENABLED=false run_start \
    && grep -Fxq 'DB_HOST=cache-only.invalid' "$tmp/env" \
    && grep -Fxq 'DB_PORT=12345' "$tmp/env" \
    && grep -Fq -- '-e DB_HOST=cache-only.invalid' "$tmp/docker.log" \
    && grep -Fq -- '-e DB_PORT=12345' "$tmp/docker.log"; then
  pass "cache-only callers may use DB_HOST and DB_PORT as ordinary configuration"
else
  fail "cache-only DB_HOST/DB_PORT configuration was rejected or not passed through: $(cat "$tmp/out")"
fi

if grep -Fxq 'container-id=cache-container-id' "$tmp/output"; then
  pass "cache publishes its immutable container handle"
else
  fail "cache did not publish its container id"
fi

bad_env='not-a-pair'
if CACHE_ENV="$bad_env" run_start; then
  fail "malformed cache-env was accepted"
elif [ ! -s "$tmp/env" ] && [ ! -s "$tmp/docker.log" ]; then
  pass "malformed cache-env fails at the input boundary"
else
  fail "malformed cache-env was rejected after Docker or GITHUB_ENV side effects"
fi

if CACHE_ENV='BAD-KEY=value' run_start; then
  fail "cache-env accepted an invalid environment variable name"
elif grep -qF "cache-env: 'BAD-KEY' is not a valid environment variable name" "$tmp/out" \
    && [ ! -s "$tmp/env" ] && [ ! -s "$tmp/docker.log" ]; then
  pass "an invalid cache-env variable name fails before Docker or GITHUB_ENV"
else
  fail "cache-env did not reject an invalid variable name before side effects: $(cat "$tmp/out")"
fi

for key in BASH_ENV PATH NODE_OPTIONS LD_PRELOAD GITHUB_ENV GH_TOKEN GH_HOST gh_host \
  DOCKER_HOST DOCKER_CONTEXT docker_context \
    GIT_CONFIG_COUNT HTTPS_PROXY https_proxy FTP_PROXY ftp_proxy SSL_CERT_FILE GCONV_PATH PS4 \
    NPM_CONFIG_USERCONFIG Npm_Config_Userconfig NPM_CONFIG_GLOBALCONFIG \
    Npm_Config_Globalconfig npm_config_globalconfig NPM_CONFIG_HTTPS_PROXY \
    NPM_CONFIG_STRICT_SSL NPM_CONFIG_CAFILE; do
  if CACHE_ENV="$key=unsafe" run_start; then
    fail "cache-env accepted runner-control key $key"
  elif grep -qF "cache-env: '$key' cannot override runner execution" "$tmp/out" \
      && [ ! -s "$tmp/env" ] && [ ! -s "$tmp/docker.log" ]; then
    pass "cache-env rejects runner-control key $key before Docker or GITHUB_ENV"
  else
    fail "cache-env did not reject $key before Docker or GITHUB_ENV: $(cat "$tmp/out")"
  fi
done

# The cache service follows optional DB setup in the same job. When enabled, its
# input cannot replace the selected DB endpoint in the shared workflow env.
printf 'DB_HOST=127.0.0.1\nDB_PORT=49187\n' > "$tmp/env"
cp "$tmp/env" "$tmp/db-env-before-cache"
if CACHE_ENV='DB_HOST=attacker.example
DB_PORT=12345' DB_SERVICE_ENABLED=true run_start_preserving_env; then
  fail "cache-env accepted database endpoint keys after DB setup"
elif grep -qF 'is reserved while the database service is enabled' "$tmp/out" \
    && cmp -s "$tmp/env" "$tmp/db-env-before-cache" \
    && [ ! -s "$tmp/docker.log" ]; then
  pass "cache-env preserves the database endpoint and rejects overrides before Docker"
else
  fail "cache-env changed the established DB endpoint or started Docker: $(cat "$tmp/out")"
fi

# A BASH_ENV value from cache-env must not reach a later token-bearing shell.
cache_env_hook="$tmp/cache-env-hook.sh"
cache_env_hook_marker="$tmp/cache-env-hook-marker"
cat > "$cache_env_hook" <<'HOOK'
printf '%s\n' "${GH_TOKEN:-}" > "$CACHE_ENV_HOOK_MARKER"
HOOK
if CACHE_ENV="SAFE=ok"$'\r'"BASH_ENV=$cache_env_hook" run_start; then
  fail "cache-env accepted a carriage-return BASH_ENV injection"
elif grep -qF 'cache-env: carriage returns are not permitted' "$tmp/out" \
    && [ ! -s "$tmp/env" ] && [ ! -s "$tmp/docker.log" ]; then
  pass "a carriage return cannot smuggle BASH_ENV into GITHUB_ENV"
else
  fail "cache-env accepted carriage-return input or started Docker before rejection"
fi
if CACHE_ENV="BASH_ENV=$cache_env_hook" run_start; then
  fail "cache-env accepted a caller BASH_ENV script"
else
  exported_bash_env="$(sed -n 's/^BASH_ENV=//p' "$tmp/env")"
  if [ -n "$exported_bash_env" ]; then
    env -u BASH_ENV GH_TOKEN=fixture-token CACHE_ENV_HOOK_MARKER="$cache_env_hook_marker" \
      BASH_ENV="$exported_bash_env" bash -c ':'
  fi
  { [ -z "$exported_bash_env" ] && [ ! -e "$cache_env_hook_marker" ] \
      && [ ! -s "$tmp/docker.log" ]; } \
    && pass "a caller BASH_ENV script cannot run in a later token-bearing shell" \
    || fail "cache-env exposed BASH_ENV to a later shell (token marker exists: $([ -e "$cache_env_hook_marker" ] && echo yes || echo no))"
fi

: > "$tmp/docker.log"
if DOCKER_LOG="$tmp/docker.log" CONTAINER_ID=cache-container-id bash "$teardown" \
  && grep -Fxq 'rm -f cache-container-id' "$tmp/docker.log"; then
  pass "cache teardown removes exactly the published container"
else
  fail "cache teardown did not remove the published container"
fi

: > "$tmp/docker.log"
DOCKER_LOG="$tmp/docker.log" CONTAINER_ID='' bash "$teardown"
[ ! -s "$tmp/docker.log" ] && pass "empty cache handle makes teardown a no-op" \
  || fail "empty cache handle invoked Docker"

# #986: a caller-supplied cache-health-cmd replaces `redis-cli ping`, run the
# same way (docker exec against the started container, retried on failure).
: > "$tmp/docker.log"; : > "$tmp/env"; : > "$tmp/output"
if DOCKER_LOG="$tmp/docker.log" GITHUB_ENV="$tmp/env" GITHUB_OUTPUT="$tmp/output" \
    MAPPED_PORT=49321 CACHE_HEALTH_CMD='memcached-tool 127.0.0.1:11211 stats' \
    bash -eo pipefail "$start" >"$tmp/out" 2>&1 \
  && grep -Fq 'exec cache-container-id memcached-tool 127.0.0.1:11211 stats' "$tmp/docker.log" \
  && ! grep -Fq 'exec cache-container-id redis-cli ping' "$tmp/docker.log"; then
  pass "cache-health-cmd replaces redis-cli ping with the caller's command"
else
  fail "cache-health-cmd was not honored: $(grep -E '^exec ' "$tmp/docker.log")"
fi

# ...and it is a TRUST BOUNDARY, not a second shell: cache-health-cmd is caller
# data executed in CI. It must reach `docker exec` as literal argv — split on
# whitespace only — and never through a shell/eval that would let a `;` or
# `$()` escape the container it targets and run on the runner itself. Proven
# here by planting a marker: the injected `touch` becomes an inert docker-exec
# ARGUMENT (logged verbatim by the stub), never an actual command the test's
# shell runs.
marker="$tmp/cache-pwned-marker"
: > "$tmp/docker.log"; : > "$tmp/env"; : > "$tmp/output"
if DOCKER_LOG="$tmp/docker.log" GITHUB_ENV="$tmp/env" GITHUB_OUTPUT="$tmp/output" \
    MAPPED_PORT=49321 CACHE_HEALTH_CMD="memcached-tool; touch $marker" \
    bash -eo pipefail "$start" >"$tmp/out" 2>&1 \
  && grep -Fq "exec cache-container-id memcached-tool; touch $marker" "$tmp/docker.log" \
  && [ ! -e "$marker" ]; then
  pass "cache-health-cmd reaches docker exec as literal argv; shell metacharacters cannot escape to the runner"
else
  fail "a ';'-separated cache-health-cmd token was not passed literally, or the runner executed it (marker exists: $( [ -e "$marker" ] && echo yes || echo no ))"
fi

# A health command that never succeeds fails the step after the same bound
# `redis-cli ping` always had (30 attempts) — naming the image and the exact
# command, so the operator does not have to guess which changed engine failed.
: > "$tmp/docker.log"; : > "$tmp/env"; : > "$tmp/output"
if DOCKER_LOG="$tmp/docker.log" GITHUB_ENV="$tmp/env" GITHUB_OUTPUT="$tmp/output" \
    MAPPED_PORT=49321 CACHE_HEALTH_CMD='memcached-tool 127.0.0.1:11211 stats' \
    CACHE_HEALTH_FAIL=1 bash -eo pipefail "$start" >"$tmp/out" 2>&1; then
  fail "step exited 0 despite a cache health command that never succeeds"
else
  grep -Fq "cache image 'redis:8' failed health check 'memcached-tool 127.0.0.1:11211 stats' after 30 attempts" \
    "$tmp/out" \
    && pass "a cache health command that never succeeds fails after 30 attempts, naming the image and command" \
    || fail "cache health-check exhaustion did not name the image/command: $(cat "$tmp/out")"
fi

# #986 hardening: cache-health-cmd must reach `docker exec` with NEITHER shell
# reinterpretation (proven above) NOR bash's own pathname/glob expansion. An
# earlier revision expanded $CACHE_HEALTH_CMD unquoted, which undergoes
# word-splitting AND glob expansion — a command containing `*`/`?`/`[...]`
# that happened to match a file in the runner's working directory would
# silently gain extra argv the caller never wrote. Run the step from a
# directory salted with filenames that WOULD match the glob if expanded, and
# assert the literal `*` reaches docker unexpanded.
glob_cwd="$tmp/cache-glob-cwd"
mkdir -p "$glob_cwd"
touch "$glob_cwd/memcached-tool-extra" "$glob_cwd/memcached-tool-other"
: > "$tmp/docker.log"; : > "$tmp/env"; : > "$tmp/output"
(
  cd "$glob_cwd" || exit 1
  DOCKER_LOG="$tmp/docker.log" GITHUB_ENV="$tmp/env" GITHUB_OUTPUT="$tmp/output" \
    MAPPED_PORT=49321 CACHE_HEALTH_CMD='memcached-tool-*' \
    bash -eo pipefail "$start" >"$tmp/out" 2>&1
)
rc=$?
{ [ "$rc" -eq 0 ] \
    && grep -qF -- 'exec cache-container-id memcached-tool-*' "$tmp/docker.log" \
    && ! grep -qF -- 'memcached-tool-extra' "$tmp/docker.log" \
    && ! grep -qF -- 'memcached-tool-other' "$tmp/docker.log"; } \
  && pass "a glob-shaped cache-health-cmd reaches docker exec literally, never pathname-expanded" \
  || fail "cache-health-cmd underwent pathname expansion: $(grep -E '^exec ' "$tmp/docker.log")"

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."; exit 1
