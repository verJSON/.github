#!/usr/bin/env bash
set -euo pipefail

# Pull requests lint changed shell scripts; pushes keep the full-tree audit.
# Both paths use tracked paths and NUL delimiters so spaces are safe.
base_sha="${ACTIONS_CI_SHELLCHECK_BASE_SHA:-}"
head_sha="${ACTIONS_CI_SHELLCHECK_HEAD_SHA:-}"
shell_files_path="$(mktemp)"
trap 'rm -f -- "$shell_files_path"' EXIT
if [ -n "$base_sha" ] || [ -n "$head_sha" ]; then
  if [[ ! "$base_sha" =~ ^[0-9a-f]{40}$ || ! "$head_sha" =~ ^[0-9a-f]{40}$ ]]; then
    printf 'invalid ShellCheck diff range: base=%s head=%s\n' "$base_sha" "$head_sha" >&2
    exit 2
  fi
  if [ "$(git rev-parse HEAD)" != "$head_sha" ]; then
    printf 'ShellCheck head does not match checkout: expected=%s actual=%s\n' \
      "$head_sha" "$(git rev-parse HEAD)" >&2
    exit 2
  fi
  if ! git cat-file -e "$base_sha^{commit}" 2>/dev/null; then
    git fetch --no-tags --depth=1 origin "$base_sha"
  fi
  git cat-file -e "$base_sha^{commit}"
  git diff --name-only --diff-filter=ACMR -z "$base_sha" "$head_sha" -- '*.sh' \
    >"$shell_files_path"
else
  git ls-files -z -- '*.sh' >"$shell_files_path"
fi
mapfile -d '' -t shell_files <"$shell_files_path"

# One shellcheck process per core keeps the full-tree push audit bounded.
shellcheck_jobs="$(nproc 2>/dev/null || echo 2)"
[ "$shellcheck_jobs" -gt 8 ] && shellcheck_jobs=8
[ "$shellcheck_jobs" -ge 1 ] || shellcheck_jobs=1
if [ "${#shell_files[@]}" -gt 0 ]; then
  printf '%s\0' "${shell_files[@]}" \
    | xargs -0 -r -P "$shellcheck_jobs" shellcheck --severity=warning --
fi
