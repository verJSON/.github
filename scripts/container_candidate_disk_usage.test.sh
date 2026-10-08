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
  "image rm "*) exit 1 ;;
  *) exit 2 ;;
esac
DOCKER
chmod +x "$tmp/bin/docker"

export PATH="$tmp/bin:$PATH"
export RUNNER_TEMP="$tmp/runner-temp"
export GITHUB_WORKSPACE="$tmp/workspace"
export DOCKER_CALLS="$tmp/docker-calls"
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
