#!/usr/bin/env bash
# Fixture-based unit tests for scripts/render-next.sh (Verjson/.github#67). CI only
# smoke-runs the script against live NEXT/ content; this exercises its edge cases
# (missing dir, no fragments, newest-first ordering, README exclusion, archive
# sorting last) against a stubbed NEXT/ tree, with clear pass/fail. It copies and
# runs the REAL script into a fixture root so the test can't drift from it.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
script="$repo_root/scripts/render-next.sh"
contract="$repo_root/scripts/changelog.py"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$script" ] && [ -f "$contract" ] \
  || { echo "FAIL - changelog scripts not found"; exit 1; }

tmproot="$(mktemp -d)"
trap 'rm -rf "$tmproot"' EXIT

# new_fixture -> prints a fresh fixture root with the real script + an empty NEXT/.
new_fixture() {
  local d; d="$(mktemp -d "$tmproot/fix.XXXXXX")"
  mkdir -p "$d/scripts" "$d/NEXT"
  cp "$script" "$d/scripts/render-next.sh"
  cp "$contract" "$d/scripts/changelog.py"
  printf '%s' "$d"
}

# 1. Happy path: metadata date controls order; README and archive are excluded.
d="$(new_fixture)"
printf '%s\n' '---' 'date: 2026-07-19' 'issue: 19' 'title: older' '---' '' 'old' \
  > "$d/NEXT/2026-07-19-issue-19-older.md"
printf '%s\n' '---' 'date: 2026-07-20' 'issue: 20' 'title: newer' '---' '' 'new' \
  > "$d/NEXT/2026-07-20-issue-20-newer.md"
printf '# archive\n' > "$d/NEXT/0000-archive.md"
printf 'ignore me\n' > "$d/NEXT/README.md"
out="$(bash "$d/scripts/render-next.sh")"
[ "$(printf '%s\n' "$out" | grep -c '^ignore me$')" -eq 0 ] \
  && pass "README.md is excluded from the log" || fail "README.md must be excluded"
order="$(printf '%s\n' "$out" | grep -E '^## ' | paste -sd, -)"
[ "$order" = "## newer,## older" ] \
  && pass "fragments render newest-first by metadata date" || fail "wrong order: $order"
# The archive is pre-contract history, not an unreleased fragment, so strict
# rendering omits it entirely rather than sorting it last. It rendered last
# only while --allow-legacy-next loaded it as a legacy entry; that switch came
# off with the #289 migration. The file is unchanged and still in git.
[ "$(printf '%s\n' "$out" | grep -c '^# archive$')" -eq 0 ] \
  && pass "0000-archive.md is excluded from the log" || fail "archive must be excluded"

# The engine exits 1 for every refusal and names it on a `changelog:` line; a
# missing NEXT/ directory holds no fragments, so both cases share that line.
renders_nothing() { # renders_nothing <fixture>
  local status=0
  LC_ALL=C bash "$1/scripts/render-next.sh" >/dev/null 2>"$1/render.err" || status=$?
  [ "$status" -eq 1 ] && [ "$(cat "$1/render.err")" = 'changelog: no unreleased fragments' ]
}

# 2. Missing NEXT/ directory -> non-zero exit.
d="$(new_fixture)"; rm -rf "$d/NEXT"
renders_nothing "$d" \
  && pass "missing NEXT/ exits non-zero" || fail "missing NEXT/ must exit non-zero: $(cat "$d/render.err")"

# 3. No fragments (only README) -> non-zero exit.
d="$(new_fixture)"; printf 'x\n' > "$d/NEXT/README.md"
renders_nothing "$d" \
  && pass "no fragments exits non-zero" || fail "no fragments must exit non-zero: $(cat "$d/render.err")"

if [ "$fails" -eq 0 ]; then echo "All tests passed."; exit 0; else echo "$fails test(s) failed."; exit 1; fi
