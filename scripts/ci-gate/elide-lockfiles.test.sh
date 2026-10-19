#!/usr/bin/env bash
# Pins the lockfile-elision logic in ai-review-merge.yml's "Prepare bounded
# review context" step (Verjson/.github#110). A generated lockfile in the diff
# (e.g. an 8k-line package-lock) once consumed the whole AI review budget and
# forced a no-verdict, fail-closed run on an otherwise-small PR. The gate now
# filters lockfiles out of the payload it hands the model while keeping the
# full diff on disk. Elision selects files with git pathspecs, never by parsing
# diff headers (ADR 0237). This extracts the real fragment from the workflow —
# single source of truth, no drift — and drives it against real commits.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="$repo_root/.github/workflows/ai-review-merge.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$wf" ] || { echo "FAIL - workflow not found: $wf"; exit 1; }

block="$tmp/block.sh"
awk '
  $0 == "      - name: Prepare bounded review context" { seen = 1 }
  seen && $0 == "        run: |" { cap = 1; next }
  cap {
    if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
    if ($0 ~ /^[ \t]*$/) { print ""; next }
    cap = 0
  }
' "$wf" > "$block"

slice="$tmp/elide.sh"
awk '/^lockfile_excludes=\(\)$/ { c = 1 } c { print } /^reviewed_path_count=/ { if (c) exit }' "$block" > "$slice"
grep -q '^lockfile_excludes=()$' "$slice" || { echo "FAIL - could not extract the lockfile pathspecs from $wf"; exit 1; }
grep -q '^reviewed_path_count=' "$slice" || { echo "FAIL - could not extract the elided-path list from $wf"; exit 1; }

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
# run_filter <file>=<content>...: commit the files on top of a base commit,
# then run the real slice between them.
run_filter() {
  rm -rf "$tmp/work"; mkdir -p "$tmp/work/repo" "$tmp/work/review"
  (
    cd "$tmp/work/repo" || exit 1
    git init -q && git -c user.name=t -c user.email=t@example.invalid commit -q --allow-empty -m base
    for spec in "$@"; do
      file="${spec%%=*}"; mkdir -p "$(dirname "$file")"; printf '%s\n' "${spec#*=}" >"$file"
    done
    git add -A && git -c user.name=t -c user.email=t@example.invalid commit -qm head
    merge_base="$(git rev-parse HEAD~1)" review_dir="$tmp/work/review" GITHUB_OUTPUT="$tmp/work/output"
    # shellcheck disable=SC1090,SC2154 # The extracted fragment defines these values.
    source "$slice" && printf '%s' "$elided_lockfiles" >"$tmp/work/elided.out"
  )
  filtered="$tmp/work/review/pr.diff"
  elided="$(cat "$tmp/work/elided.out" 2>/dev/null)"
}
kept() { grep -qF -- "$1" "$filtered"; }

run_filter 'src/app.ts=const a = 2' 'package.json={"dep": "2.0.0"}' 'package-lock.json={"version": "2.0.0"}'
kept 'diff --git a/src/app.ts' && pass "keeps source file" || fail "source file must survive filtering"
kept 'diff --git a/package.json b/package.json' \
  && pass "keeps package.json manifest" || fail "manifest (package.json) must survive"
! kept 'package-lock.json' \
  && pass "drops package-lock.json section entirely" || fail "package-lock.json must be dropped"
! grep -q '"version": "2.0.0"' "$filtered" \
  && pass "drops lockfile hunk body" || fail "lockfile hunk body leaked into review diff"
[ "$elided" = "package-lock.json" ] \
  && pass "reports the elided lockfile" || fail "elided list wrong: '$elided'"

run_filter 'svc/pnpm-lock.yaml=y' 'Cargo.lock=y' 'go.sum=y' 'src/main.rs=fn main() { work() }'
kept 'diff --git a/src/main.rs' && pass "keeps source amid many lockfiles" || fail "source dropped among lockfiles"
{ ! kept 'pnpm-lock.yaml' && ! kept 'Cargo.lock' && ! kept 'go.sum'; } \
  && pass "drops nested + multi-ecosystem lockfiles" || fail "a lockfile leaked (pnpm/cargo/go)"
[ "$elided" = $'Cargo.lock\ngo.sum\nsvc/pnpm-lock.yaml' ] \
  && pass "reports all elided lockfiles in path order" || fail "elided list wrong: '$elided'"

run_filter 'Cargo.toml=version = "2"'
kept 'Cargo.toml' && [ -z "$elided" ] \
  && pass "Cargo.toml manifest is not elided" || fail "Cargo.toml must not be treated as a lockfile"

run_filter 'tools/run b/yarn.lock now.sh=lockfile-named-content' 'notes/yarn.lock.md=named-like-a-lockfile'
{ kept '+lockfile-named-content' && kept '+named-like-a-lockfile' && [ -z "$elided" ]; } \
  && pass "a path that only contains a lockfile name is reviewed" || fail "a non-lockfile path was elided: '$elided'"

run_filter 'yarn.lock=y'
[ ! -s "$filtered" ] && [ "$elided" = "yarn.lock" ] \
  && pass "lockfile-only diff filters to empty payload" || fail "lockfile-only diff not handled"

if [ "$fails" -eq 0 ]; then echo "All tests passed."; exit 0; else echo "$fails test(s) failed."; exit 1; fi
