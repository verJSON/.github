#!/usr/bin/env bash
# actions-ci smoke render of NEXT/ that survives the state a release leaves.
#
# A dispatched release consumes every NEXT/ fragment, after which `render-next`
# exits 1 with `changelog: no unreleased fragments`. Run bare, the docs row turned
# main red after every release and masked unrelated reds behind it.
#
# Exactly one state excuses that exit: NEXT/ is a directory holding no renderable
# fragment AND the renderer said exactly that and nothing else, with status 1 —
# what changelog.py main() returns for a ChangelogError. As in the
# generated adopter contract test (gen-changelog-caller.sh, #399), the tree and
# not the exit status alone decides — the sentence alone also appears for a missing
# NEXT/ and for a component-only NEXT/. The sentence is required as well, so a
# renderer that crashes right after a release is not excused by the empty tree.
# Any other failure prints the renderer's stderr and keeps its exit status.
set -uo pipefail
shopt -s nullglob

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
next_dir="$repo_root/NEXT"
empty_diagnostic='changelog: no unreleased fragments'
err="$(mktemp)"
trap 'rm -f "$err"' EXIT

# Mirrors changelog.py fragments(): every NEXT/*.md except README.md and
# 0000-archive.md, and anything else matched counts. A hidden `.x.md` is not
# counted here, but the engine rejects it by name with a different diagnostic,
# so the exact-diagnostic check below still fails it.
renderable_fragment_count() {
  local path count=0
  for path in "$next_dir"/*.md; do
    case "${path##*/}" in
      README.md | 0000-archive.md) ;;
      *) count=$((count + 1)) ;;
    esac
  done
  printf '%s' "$count"
}

rc=0
bash "$repo_root/scripts/render-next.sh" >/dev/null 2>"$err" || rc=$?
[ "$rc" -ne 0 ] || exit 0

if [ "$rc" -eq 1 ] \
  && [ -d "$next_dir" ] \
  && [ "$(renderable_fragment_count)" -eq 0 ] \
  && [ "$(cat "$err")" = "$empty_diagnostic" ]; then
  echo "ok - no unreleased fragments to render (a release consumed them)"
  exit 0
fi

echo "render-next exited $rc and NEXT/ is not the emptied post-release state:" >&2
cat "$err" >&2
exit "$rc"
