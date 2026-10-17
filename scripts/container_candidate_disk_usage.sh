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

# Large preinstalled hosted-image toolchains that no publish build step uses. The image
# build runs inside BuildKit, so host SDKs are dead weight competing with node_modules.
unused_hosted_toolchains=(
  /usr/local/lib/android
  /usr/share/dotnet
  /opt/ghc
  /usr/local/.ghcup
  /opt/hostedtoolcache/CodeQL
  /usr/share/swift
  /usr/local/share/powershell
)

# A necessary floor, not a sufficiency guarantee: the node_modules context is copied at
# least twice (BuildKit context transfer and the image layer), and the margin is headroom
# for base layers, install scripts, and image export. Run 39001337204 hit ENOSPC after the
# transfer, in npm rebuild install scripts and layer export, whose demand the publisher
# cannot predict; passing this floor only rules out the known-undersized case.
build_margin_bytes=4294967296

report() {
  printf '%s\n' "$1" | tee -a "$log_file"
}

# Writes to stderr so the error is visible even when raised inside a command substitution.
measurement_failure() {
  printf '::error::could not measure %s; refusing to continue without a trustworthy disk measurement\n' "$1" \
    | tee -a "$log_file" >&2
  exit 1
}

# Prints the free bytes of the filesystem holding $1, or fails closed on any malformed reading.
available_bytes() {
  local output value
  output="$(df -B1 --output=avail -- "$1")" || measurement_failure "free space on the filesystem holding $1"
  [ "$(wc -l <<<"$output")" -eq 2 ] || measurement_failure "free space on the filesystem holding $1"
  value="$(sed -n '2{s/^[[:space:]]*//;p}' <<<"$output")"
  [[ "$value" =~ ^[0-9]{1,18}$ ]] || measurement_failure "free space on the filesystem holding $1"
  printf '%s\n' "$value"
}

# Prints the on-disk bytes of directory $1, or fails closed on any malformed reading.
directory_bytes() {
  local output pattern=$'^([0-9]{1,18})\t[^\n]+$'
  [ -d "$1" ] || measurement_failure "the build context: $1 is not a directory"
  output="$(du -s -B1 -- "$1")" || measurement_failure "the size of $1"
  [[ "$output" =~ $pattern ]] || measurement_failure "the size of $1"
  printf '%s\n' "${BASH_REMATCH[1]}"
}

case "$operation" in
  reclaim)
    [ "${RUNNER_ENVIRONMENT:-}" = github-hosted ] || {
      report "Skipping hosted toolchain reclaim on runner environment '${RUNNER_ENVIRONMENT:-unknown}'"
      exit 0
    }
    before="$(available_bytes /)" || exit 1
    sudo -n rm -rf -- "${unused_hosted_toolchains[@]}" || {
      report "::error::could not remove unused hosted toolchains: ${unused_hosted_toolchains[*]}"
      exit 1
    }
    after="$(available_bytes /)" || exit 1
    report "reclaimed_bytes=$((after - before)) available_before=$before available_after=$after"
    ;;
  assert-space)
    [ "$#" -eq 2 ] || { echo "usage: $0 assert-space <build-context>" >&2; exit 2; }
    context_bytes="$(directory_bytes "$2")" || exit 1
    docker_root="$(docker info --format '{{.DockerRootDir}}')" || measurement_failure "the Docker data root"
    [[ "$docker_root" == /* ]] || measurement_failure "the Docker data root: it is not an absolute path"
    available="$(available_bytes "$docker_root")" || exit 1
    required=$((2 * context_bytes + build_margin_bytes))
    report "context_bytes=$context_bytes required_bytes=$required available_bytes=$available filesystem=$docker_root"
    [ "$available" -ge "$required" ] || {
      report "::error::insufficient free disk for the image build: required $required bytes, available $available bytes on the filesystem holding $docker_root (2 x node_modules context $context_bytes + margin $build_margin_bytes)"
      exit 1
    }
    ;;
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
    echo "usage: $0 {snapshot|measure <phase>|cleanup|reclaim|assert-space <build-context>}" >&2
    exit 2
    ;;
esac
