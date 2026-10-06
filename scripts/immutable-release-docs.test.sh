#!/usr/bin/env bash
# Documentation contracts for immutable reusable-workflow and release refs (#353).
set -uo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
versioning_docs="$root/docs/reusable-workflow-versioning.md"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

awk '
  /^[[:space:]]*```ya?ml[[:space:]]*$/ { in_yaml = 1; next }
  in_yaml && /^[[:space:]]*```[[:space:]]*$/ { in_yaml = 0; next }
  in_yaml && /uses:[[:space:]]*Verjson\/\.github\/\.github\/workflows\/.*@main([[:space:]#]|$)/ {
    print FILENAME ":" FNR ":" $0
  }
' "$root"/docs/*.md >"$tmp/mutable-workflow-refs"

if [ ! -s "$tmp/mutable-workflow-refs" ]; then
  pass "copyable workflow examples never target @main"
else
  fail "copyable workflow examples target mutable @main refs"
  sed 's/^/diag - /' "$tmp/mutable-workflow-refs"
fi

release_instructions="$(sed -n '/^## Cutting a release$/,$p' "$versioning_docs")"

if grep -qF 'Dispatch the pinned `.github/workflows/release.yml` with that version' <<<"$release_instructions" \
  && grep -qF 'dispatch commit' <<<"$release_instructions" \
  && grep -qF 'concurrent change to `main` makes publication fail closed' <<<"$release_instructions" \
  && ! grep -qF 'gh release create' <<<"$release_instructions"; then
  pass "release instructions use an explicit-version dispatch bound to its verified source"
else
  fail "release instructions omit the exact-source dispatch or revive manual release creation"
fi

if grep -qF 'immutable release commit' <<<"$release_instructions" \
  && grep -qF 'pin that release commit SHA' <<<"$release_instructions" \
  && grep -qF 'neither creates nor moves them' <<<"$release_instructions"; then
  pass "release instructions require immutable readback and leave major aliases static"
else
  fail "release instructions omit immutable readback or static major-alias policy"
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi

echo "$fails test(s) failed."
exit 1
