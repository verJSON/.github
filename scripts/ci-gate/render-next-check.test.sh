#!/usr/bin/env bash
# Behavioral tests for the actions-ci docs row that smoke-renders NEXT/.
#
# A dispatched release consumes every NEXT/ fragment, so main is left with an
# empty NEXT/ after each release and `render-next` exits 1 with
# `changelog: no unreleased fragments`. Run bare, that row turned main red after
# every release and masked unrelated reds behind it. Exactly one state may pass
# that way: an emptied NEXT/. Every other renderer failure must still fail.
#
# The row is read from scripts/actions-ci-groups.tsv and executed the way
# scripts/actions-ci-group.sh executes it (`bash -o pipefail -c`, from the repo
# root), inside a fixture root carrying the real scripts. Testing the registered
# command rather than a helper by name keeps the test from passing while the row
# itself still runs something else.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
manifest="$repo_root/scripts/actions-ci-groups.tsv"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# The renderer row, not this test's own registration beside it.
row="$(awk -F '\t' '$1 == "docs" && $2 ~ /render-next/ && $2 !~ /\.test\.sh$/ { print $2 }' "$manifest")"
[ "$(printf '%s\n' "$row" | grep -c .)" -eq 1 ] \
  || { echo "FAIL - expected exactly one docs row that renders NEXT/, found: $row"; exit 1; }

tmproot="$(mktemp -d)"
trap 'rm -rf "$tmproot"' EXIT

# new_fixture -> prints a fresh fixture root with the real scripts and a NEXT/
# holding only the two files changelog.py never treats as fragments.
new_fixture() {
  local d; d="$(mktemp -d "$tmproot/fix.XXXXXX")"
  mkdir -p "$d/scripts" "$d/NEXT"
  cp "$repo_root"/scripts/render-next*.sh "$d/scripts/"
  cp "$repo_root/scripts/changelog.py" "$d/scripts/changelog.py"
  printf '# NEXT\n' > "$d/NEXT/README.md"
  printf '# archive\n' > "$d/NEXT/0000-archive.md"
  printf '%s' "$d"
}

# run_row <fixture> -> runs the registered row there; output lands in $d/out.
run_row() {
  (cd "$1" && bash -o pipefail -c "$row") >"$1/out" 2>&1
}

# 1. The post-release state: NEXT/ holds no fragment, so the row passes.
d="$(new_fixture)"
if run_row "$d"; then
  pass "an emptied NEXT/ (README.md and 0000-archive.md only) passes"
else
  fail "an emptied NEXT/ must pass: $(tail -3 "$d/out")"
fi

# 2. A malformed fragment is a broken log, not an emptied one: the row fails and
# the renderer's own diagnostic reaches the log instead of being discarded.
d="$(new_fixture)"
printf '%s\n' '---' 'date: 2026-07-20' 'title: no identity' '---' '' 'body' \
  > "$d/NEXT/2026-07-20-issue-20-malformed.md"
if run_row "$d"; then
  fail "a malformed fragment must fail the row"
elif grep -q '^changelog: ' "$d/out"; then
  pass "a malformed fragment fails the row and surfaces the renderer diagnostic"
else
  fail "a malformed fragment failed without the renderer diagnostic: $(tail -3 "$d/out")"
fi

# 3. A renderer that crashes is broken even when a release has just emptied
# NEXT/ — the one state where the tree alone would excuse a non-zero exit. The
# crash is a real import failure in the real engine, not a stub exit status.
d="$(new_fixture)"
sed -i '/^from __future__ import annotations$/a import verjson_renderer_dependency_that_is_missing' "$d/scripts/changelog.py"
if run_row "$d"; then
  fail "a renderer crash on an emptied NEXT/ must fail the row"
elif grep -q 'ModuleNotFoundError' "$d/out"; then
  pass "a renderer crash fails the row even on an emptied NEXT/"
else
  fail "a renderer crash failed without its traceback: $(tail -3 "$d/out")"
fi

# 4. The diagnostic text is not proof of an emptied NEXT/: the tree decides. A
# valid component-stream fragment makes the real renderer print exactly
# `changelog: no unreleased fragments` for the default stream while NEXT/ still
# holds work this repository's log would silently drop.
d="$(new_fixture)"
printf '%s\n' '---' 'date: 2026-07-20' 'issue: 20' 'component: api' 'impact: patch' \
  'title: component only' '---' '' 'body' > "$d/NEXT/2026-07-20-issue-20-component-only.md"
if run_row "$d"; then
  fail "the empty-NEXT/ diagnostic with a fragment still present must fail the row"
elif grep -qx 'changelog: no unreleased fragments' "$d/out"; then
  pass "the empty-NEXT/ diagnostic does not excuse a NEXT/ that still holds a fragment"
else
  fail "fixture did not produce the empty-NEXT/ diagnostic, so this case is vacuous: $(tail -3 "$d/out")"
fi

# 5. A missing NEXT/ is not an emptied one. A release leaves README.md behind;
# a deleted or renamed directory is a broken repository, even though the real
# renderer reports it with the same `no unreleased fragments` sentence.
d="$(new_fixture)"; rm -rf "$d/NEXT"
if run_row "$d"; then
  fail "a missing NEXT/ must fail the row"
elif grep -qx 'changelog: no unreleased fragments' "$d/out"; then
  pass "a missing NEXT/ fails the row although the renderer calls it empty"
else
  fail "fixture did not produce the empty-NEXT/ diagnostic, so this case is vacuous: $(tail -3 "$d/out")"
fi

# 6. The diagnostic must be the renderer's whole stderr, not a substring of it.
# Here the real engine reports the emptied NEXT/ and then crashes in teardown;
# matching on containment would excuse the crash.
d="$(new_fixture)"
sed -i '/^from __future__ import annotations$/a import atexit; atexit.register(lambda: 1 / 0)' \
  "$d/scripts/changelog.py"
if run_row "$d"; then
  fail "a teardown crash after the empty-NEXT/ diagnostic must fail the row"
elif grep -qx 'changelog: no unreleased fragments' "$d/out" && grep -q 'ZeroDivisionError' "$d/out"; then
  pass "extra renderer stderr beside the empty-NEXT/ diagnostic fails the row"
else
  fail "fixture did not produce the diagnostic and the crash, so this case is vacuous: $(tail -3 "$d/out")"
fi

# 7. The diagnostic is excused only with the status changelog.py returns for a
# ChangelogError (1). Here the real engine prints exactly the empty-NEXT/ sentence
# and then exits 3 from an atexit hook; the row must fail and keep that status
# rather than flattening it to a fixed code.
d="$(new_fixture)"
sed -i '/^from __future__ import annotations$/a import atexit, os; atexit.register(lambda: os._exit(3))' \
  "$d/scripts/changelog.py"
run_row "$d"; row_status=$?
if [ "$row_status" -eq 0 ]; then
  fail "the empty-NEXT/ diagnostic with exit status 3 must fail the row"
elif ! grep -qx 'changelog: no unreleased fragments' "$d/out"; then
  fail "fixture did not produce the empty-NEXT/ diagnostic, so this case is vacuous: $(tail -3 "$d/out")"
elif [ "$row_status" -eq 3 ]; then
  pass "the empty-NEXT/ diagnostic with a non-ChangelogError status fails the row and keeps status 3"
else
  fail "the row must keep the renderer's exit status 3, got $row_status"
fi

if [ "$fails" -eq 0 ]; then echo "All tests passed."; exit 0; else echo "$fails test(s) failed."; exit 1; fi
