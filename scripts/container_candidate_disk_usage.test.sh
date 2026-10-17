#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
script="$root/scripts/container_candidate_disk_usage.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin" "$tmp/runner-temp" "$tmp/workspace"
cat > "$tmp/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$DOCKER_CALLS"
case "$*" in
  "image ls --no-trunc --format {{.ID}} {{.Repository}}:{{.Tag}}")
    printf '%s %s\n' "$IMAGE_IN_USE" 'registry/base:latest'
    printf '%s %s\n' "$IMAGE_UNUSED" 'registry/test:one'
    printf '%s %s\n' "$IMAGE_UNUSED" 'registry/test:two'
    printf '%s %s\n' "$IMAGE_CHANGED_SNAPSHOT" 'registry/test:changed'
    printf '%s %s\n' "$IMAGE_DANGLING" '<none>:<none>'
    ;;
  "ps -aq --no-trunc") printf 'container-one\n' ;;
  "inspect --format {{.Image}} container-one") printf '%s\n' "$IMAGE_IN_USE" ;;
  "image inspect --format {{.Id}} registry/base:latest") printf '%s\n' "$IMAGE_IN_USE" ;;
  "image inspect --format {{.Id}} registry/test:one"|"image inspect --format {{.Id}} registry/test:two") printf '%s\n' "$IMAGE_UNUSED" ;;
  "image inspect --format {{.Id}} registry/test:changed") printf '%s\n' "$IMAGE_CHANGED_CURRENT" ;;
  "image rm registry/test:one"|"image rm registry/test:two")
    [ "${FAIL_IMAGE_REMOVE:-false}" != true ] || exit 1
    ;;
  "image rm $IMAGE_DANGLING") ;;
  "system df"|"buildx du") ;;
  "info --format {{.DockerRootDir}}")
    [ "${DOCKER_ROOT_DIR:-}" != FAIL ] || exit 1
    printf '%s\n' "${DOCKER_ROOT_DIR:-/var/lib/docker}"
    ;;
  "image rm "*) exit 1 ;;
  *) exit 2 ;;
esac
DOCKER
chmod +x "$tmp/bin/docker"
cat > "$tmp/bin/sudo" <<'SUDO'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SUDO_CALLS"
[ "${FAIL_SUDO:-false}" != true ] || exit 1
SUDO
cat > "$tmp/bin/df" <<'DF'
#!/usr/bin/env bash
case "$*" in
  "-B1 --output=avail -- "*)
    [ -s "$DF_AVAIL_VALUES" ] || { echo "df stub has no queued value" >&2; exit 97; }
    value="$(head -n1 "$DF_AVAIL_VALUES")"
    sed -i 1d "$DF_AVAIL_VALUES"
    [ "$value" != FAIL ] || exit 1
    case "$value" in
      EMPTY) ;;
      EXTRA-LINE) printf 'Avail\n%s\n%s\n' 900000000000 1 ;;
      *) printf 'Avail\n%s\n' "$value" ;;
    esac
    ;;
esac
DF
cat > "$tmp/bin/du" <<'DU'
#!/usr/bin/env bash
[ "$1 $2 $3" = "-s -B1 --" ] || exit 98
[ "${DU_OUTPUT:-}" != FAIL ] || exit 1
[ "${DU_OUTPUT:-}" = EMPTY ] || printf '%s\t%s\n' "${DU_OUTPUT:-0}" "$4"
DU
chmod +x "$tmp/bin/sudo" "$tmp/bin/df" "$tmp/bin/du"

export PATH="$tmp/bin:$PATH"
export RUNNER_TEMP="$tmp/runner-temp"
export GITHUB_WORKSPACE="$tmp/workspace"
export DOCKER_CALLS="$tmp/docker-calls"
export SUDO_CALLS="$tmp/sudo-calls"
export DF_AVAIL_VALUES="$tmp/df-avail-values"
: > "$SUDO_CALLS"
export IMAGE_IN_USE="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
export IMAGE_UNUSED="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
export IMAGE_CHANGED_SNAPSHOT="sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
export IMAGE_CHANGED_CURRENT="sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
export IMAGE_DANGLING="sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

RUNNER_ENVIRONMENT=github-hosted "$script" snapshot
RUNNER_ENVIRONMENT=github-hosted CLEANUP_UNUSED_DOCKER_IMAGES=false "$script" cleanup
if grep -q '^image rm ' "$DOCKER_CALLS"; then
  echo "cleanup removed an image when disabled" >&2
  exit 1
fi

RUNNER_ENVIRONMENT=github-hosted "$script" snapshot
RUNNER_ENVIRONMENT=github-hosted CLEANUP_UNUSED_DOCKER_IMAGES=true "$script" cleanup
RUNNER_ENVIRONMENT=github-hosted "$script" measure after-cleanup
grep -Fxq 'image rm registry/test:one' "$DOCKER_CALLS"
grep -Fxq 'image rm registry/test:two' "$DOCKER_CALLS"
grep -Fxq "image rm $IMAGE_DANGLING" "$DOCKER_CALLS"
if grep -Fxq "image rm $IMAGE_IN_USE" "$DOCKER_CALLS"; then
  echo "cleanup removed an image used by a container" >&2
  exit 1
fi
if grep -Fxq 'image rm registry/test:changed' "$DOCKER_CALLS"; then
  echo "cleanup removed a tag that changed during job setup" >&2
  exit 1
fi

if RUNNER_ENVIRONMENT=github-hosted FAIL_IMAGE_REMOVE=true CLEANUP_UNUSED_DOCKER_IMAGES=true "$script" cleanup; then
  echo "cleanup succeeded after Docker rejected image removal" >&2
  exit 1
fi
if grep -Eq 'prune|system rm' "$DOCKER_CALLS"; then
  echo "cleanup used a global Docker removal command" >&2
  exit 1
fi

calls_before_self_hosted="$(wc -l < "$DOCKER_CALLS")"
if RUNNER_ENVIRONMENT=self-hosted CLEANUP_UNUSED_DOCKER_IMAGES=true "$script" cleanup; then
  echo "cleanup unexpectedly ran on a self-hosted runner" >&2
  exit 1
fi
[ "$(wc -l < "$DOCKER_CALLS")" -eq "$calls_before_self_hosted" ]
grep -q 'before-buildx-setup' "$RUNNER_TEMP/container-candidate-disk-usage.log"
grep -q 'after-cleanup' "$RUNNER_TEMP/container-candidate-disk-usage.log"

# Hosted toolchains the publish build never uses are reclaimed before the dependency transfer.
printf '%s\n' 10000000000 30000000000 > "$DF_AVAIL_VALUES"
RUNNER_ENVIRONMENT=github-hosted "$script" reclaim > "$tmp/reclaim.out"
grep -Fxq -- '-n rm -rf -- /usr/local/lib/android /usr/share/dotnet /opt/ghc /usr/local/.ghcup /opt/hostedtoolcache/CodeQL /usr/share/swift /usr/local/share/powershell' "$SUDO_CALLS"
grep -Fq 'reclaimed_bytes=20000000000' "$tmp/reclaim.out"
grep -Fq 'reclaimed_bytes=20000000000' "$RUNNER_TEMP/container-candidate-disk-usage.log"

# Deleting system paths is destructive outside an ephemeral GitHub-hosted runner.
for environment in self-hosted '' github-hosted-lookalike; do
  : > "$SUDO_CALLS"
  printf '%s\n' 10000000000 30000000000 > "$DF_AVAIL_VALUES"
  RUNNER_ENVIRONMENT="$environment" "$script" reclaim > "$tmp/reclaim-skip.out"
  [ ! -s "$SUDO_CALLS" ] || { echo "reclaim deleted system paths on runner environment '$environment'" >&2; exit 1; }
  grep -Fq 'Skipping hosted toolchain reclaim' "$tmp/reclaim-skip.out"
done

# The image build may start only when the Docker data filesystem holds the context twice plus a margin.
context="$tmp/node-modules-context"
mkdir "$context"
margin=4294967296
printf '%s\n' "$((2 * 4000000000 + margin))" > "$DF_AVAIL_VALUES"
DU_OUTPUT=4000000000 "$script" assert-space "$context" > "$tmp/assert-ok.out"
grep -Fq "required_bytes=$((2 * 4000000000 + margin))" "$tmp/assert-ok.out"

# One byte short of the requirement fails before BuildKit can hit an opaque ENOSPC.
printf '%s\n' "$((2 * 4000000000 + margin - 1))" > "$DF_AVAIL_VALUES"
if DU_OUTPUT=4000000000 "$script" assert-space "$context" > "$tmp/assert-short.out" 2>&1; then
  echo "assert-space accepted a filesystem one byte short of the requirement" >&2
  exit 1
fi
grep -Fq "::error::insufficient free disk for the image build: required $((2 * 4000000000 + margin)) bytes, available $((2 * 4000000000 + margin - 1)) bytes" "$tmp/assert-short.out"

# Every measurement failure fails closed. Each case queues ample free space and a small context,
# so only the malformed measurement under test can stop the build.
plenty=900000000000
assert_fails_closed() {
  local label="$1" du_output="$2" df_value="$3" docker_root="$4" target="$5"
  printf '%s\n' "$df_value" > "$DF_AVAIL_VALUES"
  if DU_OUTPUT="$du_output" DOCKER_ROOT_DIR="$docker_root" "$script" assert-space "$target" > "$tmp/assert-closed.out" 2>&1; then
    echo "assert-space failed open on $label" >&2
    exit 1
  fi
  grep -Fq '::error::could not measure' "$tmp/assert-closed.out" \
    || { echo "assert-space did not report the measurement failure for $label" >&2; cat "$tmp/assert-closed.out" >&2; exit 1; }
}
assert_fails_closed 'du exit status' FAIL "$plenty" /var/lib/docker "$context"
assert_fails_closed 'empty du output' EMPTY "$plenty" /var/lib/docker "$context"
assert_fails_closed 'non-numeric du output' abc "$plenty" /var/lib/docker "$context"
assert_fails_closed 'overflowing du output' 99999999999999999999 "$plenty" /var/lib/docker "$context"
assert_fails_closed 'df exit status' 1000 FAIL /var/lib/docker "$context"
assert_fails_closed 'empty df output' 1000 EMPTY /var/lib/docker "$context"
assert_fails_closed 'non-numeric df output' 1000 12abc /var/lib/docker "$context"
assert_fails_closed 'negative df output' 1000 -5 /var/lib/docker "$context"
assert_fails_closed 'df output with an extra line' 1000 EXTRA-LINE /var/lib/docker "$context"
assert_fails_closed 'docker info exit status' 1000 "$plenty" FAIL "$context"
assert_fails_closed 'relative Docker root' 1000 "$plenty" var/lib/docker "$context"
assert_fails_closed 'missing build context' 1000 "$plenty" /var/lib/docker "$tmp/absent-context"

# A rejected removal is reported, never swallowed.
printf '%s\n' 10000000000 30000000000 > "$DF_AVAIL_VALUES"
if FAIL_SUDO=true RUNNER_ENVIRONMENT=github-hosted "$script" reclaim > "$tmp/reclaim-fail.out" 2>&1; then
  echo "reclaim succeeded after the toolchain removal failed" >&2
  exit 1
fi
grep -Fq '::error::could not remove unused hosted toolchains' "$tmp/reclaim-fail.out"

# Reclaim deletes nothing when it cannot first measure the root filesystem.
for df_value in FAIL EMPTY garbage; do
  : > "$SUDO_CALLS"
  printf '%s\n' "$df_value" 30000000000 > "$DF_AVAIL_VALUES"
  if RUNNER_ENVIRONMENT=github-hosted "$script" reclaim > "$tmp/reclaim-unmeasured.out" 2>&1; then
    echo "reclaim succeeded without a trustworthy measurement ($df_value)" >&2
    exit 1
  fi
  [ ! -s "$SUDO_CALLS" ] || { echo "reclaim deleted system paths without a trustworthy measurement ($df_value)" >&2; exit 1; }
  grep -Fq '::error::could not measure' "$tmp/reclaim-unmeasured.out"
done

# An empty context (no private packages) needs exactly the margin.
printf '%s\n' "$margin" > "$DF_AVAIL_VALUES"
DU_OUTPUT=0 "$script" assert-space "$context" > /dev/null
printf '%s\n' "$((margin - 1))" > "$DF_AVAIL_VALUES"
if DU_OUTPUT=0 "$script" assert-space "$context" > /dev/null 2>&1; then
  echo "assert-space accepted an empty context with less than the margin free" >&2
  exit 1
fi
