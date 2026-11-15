#!/usr/bin/env bash
# Pinned gitlink file: dependencies for container candidate acquisition.
# The credentialed npm install never runs this script. Callers download it from
# the contract commit and check its SHA-256 before executing it.
set -euo pipefail

fail() {
  printf 'container gitlink file dependency rejected: %s\n' "$*" >&2
  exit 1
}

safe_path() {
  [[ "$1" =~ ^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789][ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]*(/[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789][ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]*)*$ ]]
}

safe_owner() {
  [[ "$1" =~ ^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789][ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-]*$ ]]
}

read_proof_entries() {
  local proof="$1"
  [ -f "$proof" ] && [ ! -L "$proof" ] || fail "gitlink proof is missing"
  jq -e 'type == "object" and all(.[]; type == "string")' "$proof" >/dev/null \
    || fail "gitlink proof must be an object of commit ids"
  jq -r 'to_entries[] | [.key, .value] | @tsv' "$proof"
}

guard_submodule_urls() {
  local owner="$1"
  local lines line key url
  [ -f .gitmodules ] && [ ! -L .gitmodules ] || fail ".gitmodules is missing or is a symlink"
  lines="$(git config -f .gitmodules --get-regexp '^submodule\..*\.url$' || true)"
  [ -n "$lines" ] || fail ".gitmodules declares no submodule URL"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    key="${line%% *}"
    url="${line#* }"
    [[ "$key" == submodule.*.url ]] || fail "unexpected gitmodules url key"
    [[ "$url" =~ ^https://github\.com/${owner}/[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+\.git$ ]] \
      || fail "submodule URL is not an HTTPS GitHub repository in ${owner}"
  done <<< "$lines"
}

admit() {
  local lock="$1" owner="$2" output="$3"
  local path stage mode sha path_lines line key value matched url_key url json
  safe_owner "$owner" || fail "repository owner is not a GitHub owner name"
  [ -f "$lock" ] && [ ! -L "$lock" ] || fail "package lock is missing"
  jq -e '
    (.packages | type) == "object"
    and (([.packages[]? | select(.link == true)] | length)
      == ([.packages[]? | select(.link == true and (.resolved | type == "string"))] | length))
  ' "$lock" >/dev/null || fail "package lock file links must be relative paths"
  json='{}'
  local -a paths=()
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    safe_path "$path" || fail "lock link path is not a normalized relative path"
    paths+=("$path")
  done < <(jq -r '.packages | to_entries[] | select(.value.link == true) | .value.resolved' "$lock")
  if [ "${#paths[@]}" -eq 0 ]; then
    printf '%s\n' '{}' > "$output"
    return 0
  fi
  guard_submodule_urls "$owner"
  path_lines="$(git config -f .gitmodules --get-regexp '^submodule\..*\.path$' || true)"
  for path in "${paths[@]}"; do
    stage="$(git ls-files --stage -- "$path")"
    [ -n "$stage" ] || fail "lock link ${path} is not committed"
    [ "$(printf '%s\n' "$stage" | wc -l | tr -d ' ')" = 1 ] || fail "lock link ${path} matches more than one index entry"
    mode="${stage%% *}"
    [ "$mode" = 160000 ] || fail "lock link ${path} is not a git submodule"
    sha="$(printf '%s\n' "$stage" | awk '{print $2}')"
    [[ "$sha" =~ ^[0123456789abcdef]{40}$ ]] || fail "lock link ${path} is not pinned to a commit"
    [ ! -L "$path" ] || fail "gitlink path ${path} is a symlink"
    [ ! -e "$path" ] || [ -d "$path" ] || fail "gitlink path ${path} is not a directory"
    matched=""
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      key="${line%% *}"
      value="${line#* }"
      [ "$value" = "$path" ] || continue
      [ -z "$matched" ] || fail "gitmodules declares ${path} more than once"
      matched="$key"
    done <<< "$path_lines"
    [ -n "$matched" ] || fail "gitmodules does not declare ${path}"
    url_key="${matched%.path}.url"
    url="$(git config -f .gitmodules --get "$url_key")" || fail "gitmodules URL for ${path} is missing"
    [[ "$url" =~ ^https://github\.com/${owner}/[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+\.git$ ]] \
      || fail "submodule ${path} URL is not an HTTPS GitHub repository in ${owner}"
    json="$(jq -c --arg path "$path" --arg sha "$sha" '. + {($path): $sha}' <<< "$json")"
  done
  printf '%s\n' "$json" > "$output"
}

confirm() {
  local proof="$1"
  local path sha head
  while IFS=$'\t' read -r path sha; do
    [ -n "$path" ] || continue
    safe_path "$path" || fail "gitlink path is not a normalized relative path"
    [[ "$sha" =~ ^[0123456789abcdef]{40}$ ]] || fail "gitlink ${path} is not pinned to a commit"
    [ -d "$path" ] && [ ! -L "$path" ] || fail "gitlink ${path} is not a checked-out directory"
    head="$(git -C "$path" rev-parse HEAD)"
    [ "$head" = "$sha" ] || fail "gitlink ${path} is checked out at ${head}, not ${sha}"
  done < <(read_proof_entries "$proof")
}

require_npm() {
  local proof="$1"
  local count version major
  count="$(jq 'length' "$proof")"
  [ "$count" = 0 ] && return 0
  version="$(npm --version)"
  major="${version%%.*}"
  [[ "$major" =~ ^[0123456789]+$ ]] || fail "npm version is not numeric"
  [ "$major" -ge 11 ] || fail "pinned gitlink file: dependencies require npm 11 or newer so --ignore-scripts covers link prepare"
}

build_packages() {
  local proof="$1"
  local home path
  [ "$(jq 'length' "$proof")" = 0 ] && return 0
  home="$(mktemp -d)"
  while IFS=$'\t' read -r path _; do
    [ -n "$path" ] || continue
    safe_path "$path" || fail "gitlink path is not a normalized relative path"
    [ -f "$path/package.json" ] && [ ! -L "$path/package.json" ] || fail "gitlink ${path} package.json is missing"
    [ -f "$path/package-lock.json" ] && [ ! -L "$path/package-lock.json" ] || fail "gitlink ${path} has no package-lock.json"
    # The fixed command is npm ci. It runs the package's own prepare, which is
    # how a pinned schema builds dist/. No package token is in this environment.
    env -i HOME="$home" PATH="$PATH" \
      bash --noprofile --norc -c 'cd "$1" && npm ci --no-audit --no-fund' _ "$path"
  done < <(read_proof_entries "$proof")
}

copy_package() {
  local src="$1" dest="$2"
  local file rel links
  links="$(find "$src" \( -name node_modules -o -name .git \) -prune -o -type l -print | wc -l | tr -d ' ')"
  [ "$links" = 0 ] || fail "gitlink package ${src} contains a symlink"
  mkdir -p "$dest"
  while IFS= read -r -d '' file; do
    rel="${file#"$src"/}"
    mkdir -p "$dest/$(dirname "$rel")"
    cp -p -- "$file" "$dest/$rel"
  done < <(find "$src" \( -name node_modules -o -name .git \) -prune -o -type f -print0)
  [ -f "$dest/package.json" ] && [ ! -L "$dest/package.json" ] || fail "materialized package has no package.json"
}

materialize() {
  local proof="$1" modules="$2"
  local path sha expected raw abs found link dest
  [ "$(jq 'length' "$proof")" = 0 ] && return 0
  [ -d "$modules" ] && [ ! -L "$modules" ] || fail "node_modules is missing"
  while IFS=$'\t' read -r path sha; do
    [ -n "$path" ] || continue
    safe_path "$path" || fail "gitlink path is not a normalized relative path"
    [ -d "$path" ] && [ ! -L "$path" ] || fail "gitlink ${path} is not a directory"
    expected="$(cd "$path" && pwd -P)"
    found=0
    while IFS= read -r link; do
      [ -n "$link" ] || continue
      raw="$(readlink "$link")"
      if [[ "$raw" == /* ]]; then
        abs="$(realpath -m "$raw")"
      else
        abs="$(realpath -m "$(dirname "$link")/$raw")"
      fi
      [ "$abs" = "$expected" ] || continue
      found=1
      dest="$(mktemp -d)"
      copy_package "$path" "$dest"
      rm -- "$link"
      mkdir -p "$(dirname "$link")"
      mv "$dest" "$link"
      [ -d "$link" ] && [ ! -L "$link" ] || fail "materialized package ${path} is not a directory"
    done < <(find "$modules" -type l)
    [ "$found" = 1 ] || fail "node_modules has no link to ${path}"
  done < <(read_proof_entries "$proof")
}

usage() {
  printf 'usage: %s {admit|confirm|require-npm|build|materialize} ...\n' "$(basename "$0")" >&2
  exit 2
}

command="${1-}"
shift || usage
case "$command" in
  admit)
    [ "$#" -eq 3 ] || usage
    admit "$1" "$2" "$3"
    ;;
  confirm)
    [ "$#" -eq 1 ] || usage
    confirm "$1"
    ;;
  require-npm)
    [ "$#" -eq 1 ] || usage
    require_npm "$1"
    ;;
  build)
    [ "$#" -eq 1 ] || usage
    build_packages "$1"
    ;;
  materialize)
    [ "$#" -eq 2 ] || usage
    materialize "$1" "$2"
    ;;
  *)
    usage
    ;;
esac
