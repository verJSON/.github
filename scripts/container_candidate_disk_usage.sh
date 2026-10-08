#!/usr/bin/env bash
set -euo pipefail

operation="${1-}"
log_file="${RUNNER_TEMP:?RUNNER_TEMP is required}/container-candidate-disk-usage.log"
image_ids_file="${RUNNER_TEMP}/container-candidate-preloaded-image-ids.txt"

measure() {
  local phase="$1"
  {
    printf '\n::group::OCI candidate runner usage: %s\n' "$phase"
    printf 'time_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'runner_environment=%s\n' "${RUNNER_ENVIRONMENT:-unknown}"
    printf 'filesystem_usage_bytes\n'
    df -B1 -P -- "$GITHUB_WORKSPACE" "$RUNNER_TEMP" || true
    printf 'docker_usage\n'
    docker system df || true
    printf 'buildkit_usage\n'
    docker buildx du || true
    printf '::endgroup::\n'
  } 2>&1 | tee -a "$log_file" || true
}

case "$operation" in
  snapshot)
    : > "$log_file" || true
    measure "before-buildx-setup"
    docker image ls --no-trunc --format '{{.ID}} {{.Repository}}:{{.Tag}}' | sort -u > "$image_ids_file"
    ;;
  measure)
    [ "$#" -eq 2 ] || { echo "usage: $0 measure <phase>" >&2; exit 2; }
    measure "$2"
    ;;
  cleanup)
    [ "${CLEANUP_UNUSED_DOCKER_IMAGES:-false}" = true ] || {
      measure "cleanup-disabled"
      exit 0
    }
    [ "${RUNNER_ENVIRONMENT:-}" = github-hosted ] || {
      echo "::error::cleanupUnusedDockerImages is supported only on isolated GitHub-hosted runners" >&2
      exit 1
    }
    [ -f "$image_ids_file" ] || {
      echo "::error::preloaded Docker image snapshot is missing" >&2
      exit 1
    }
    active_images="$(docker ps -aq --no-trunc | xargs -r docker inspect --format '{{.Image}}')"
    failures=0
    while IFS=' ' read -r image_id image_reference; do
      [ -n "$image_id" ] || continue
      [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
        echo "::error::invalid image ID in preloaded image snapshot" >&2
        failures=1
        continue
      }
      if grep -Fxq "$image_id" <<<"$active_images"; then
        echo "Keeping preloaded image used by an existing container: $image_id"
        continue
      fi
      if [ "$image_reference" = '<none>:<none>' ]; then
        removal_target="$image_id"
      elif [[ "$image_reference" =~ ^[A-Za-z0-9._:/-]+$ ]]; then
        current_id="$(docker image inspect --format '{{.Id}}' "$image_reference" 2>/dev/null || true)"
        if [ "$current_id" != "$image_id" ]; then
          echo "Keeping preloaded image reference that changed during job setup: $image_reference"
          continue
        fi
        removal_target="$image_reference"
      else
        echo "::error::invalid image reference in preloaded image snapshot" >&2
        failures=1
        continue
      fi
      if docker image rm "$removal_target"; then
        echo "Removed unused preloaded image reference: $removal_target"
      else
        echo "::error::failed to remove unused preloaded image reference: $removal_target" >&2
        failures=1
      fi
    done < "$image_ids_file"
    exit "$failures"
    ;;
  *)
    echo "usage: $0 {snapshot|measure <phase>|cleanup}" >&2
    exit 2
    ;;
esac
