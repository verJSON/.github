#!/usr/bin/env bash
# Fixture-based unit tests for scripts/gen-adr-index.sh
# (Verjson/.github#67, #78, #79). CI runs the script with --check against live
# ADRs; this exercises its edge cases (dir with no README, malformed **Date:**,
# both H1 separators, missing index markers, reverse-sort, --check staleness)
# against a stubbed docs/decisions/ tree, with clear pass/fail. It copies and
# runs the REAL script into a fixture root so the test can't drift.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
script="$repo_root/scripts/gen-adr-index.sh"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$script" ] || { echo "FAIL - script not found: $script"; exit 1; }

tmproot="$(mktemp -d)"
trap 'rm -rf "$tmproot"' EXIT

# new_fixture -> fresh root with the real script + an index README carrying markers.
new_fixture() {
  local d; d="$(mktemp -d "$tmproot/fix.XXXXXX")"
  mkdir -p "$d/scripts" "$d/docs/decisions"
  cp "$script" "$d/scripts/gen-adr-index.sh"
  printf '# Decisions\n\n<!-- BEGIN ADR INDEX -->\n<!-- END ADR INDEX -->\n' > "$d/docs/decisions/README.md"
  printf '%s' "$d"
}
# adr <root> <slug> <h1> <date>
adr() { mkdir -p "$1/docs/decisions/$2"; printf '# %s\n\n- **Date:** %s\n' "$3" "$4" > "$1/docs/decisions/$2/README.md"; }
gen() { local fixture="$1"; shift; bash "$fixture/scripts/gen-adr-index.sh" "$@" >/dev/null 2>&1; }

# 1. Happy path: valid ADRs generate a reverse-sorted table; --check then passes.
d="$(new_fixture)"
adr "$d" "0001-first"  "0001 — First"  "2024-02-29"
adr "$d" "0002-second" "0002 - Second" "2026-07-02"
gen "$d" && pass "generate succeeds on valid ADRs" || fail "generate should succeed on valid ADRs"
idx="$d/docs/decisions/README.md"
l2="$(grep -n '\[0002\]' "$idx" | cut -d: -f1)"; l1="$(grep -n '\[0001\]' "$idx" | cut -d: -f1)"
{ [ -n "$l2" ] && [ -n "$l1" ] && [ "$l2" -lt "$l1" ]; } \
  && pass "index rows are reverse-sorted (0002 above 0001)" || fail "index order wrong (0002=$l2 0001=$l1)"
grep -qF '| Second |' "$idx" \
  && pass "plain hyphen H1 separator is stripped (#79)" \
  || fail "plain hyphen H1 separator was not stripped (#79)"
gen "$d" --check && pass "--check passes when the table is current" || fail "--check should pass when current"

# 1a. Only the directory's own ADR number is the heading prefix. A title that
# itself starts with digits and a hyphen keeps them under either separator, and
# a different leading number is content, not a prefix (#1889).
d2="$(new_fixture)"
adr "$d2" "0001-notice" "0001 — 24-hour notice" "2026-07-01"
adr "$d2" "0002-plain"  "0002 - 24-hour notice" "2026-07-02"
adr "$d2" "0003-other"  "0099 — Mismatched number" "2026-07-03"
adr "$d2" "0004-long"   "00045 — Longer number" "2026-07-04"
gen "$d2"
idx2="$d2/docs/decisions/README.md"
{ grep -qF '| [0001](0001-notice/README.md) | 2026-07-01 | 24-hour notice |' "$idx2" \
  && grep -qF '| [0002](0002-plain/README.md) | 2026-07-02 | 24-hour notice |' "$idx2"; } \
  && pass "a title that begins with digits and a hyphen keeps them (#1889)" \
  || fail "a numeric-hyphen title lost its leading digits (#1889)"
{ grep -qF '| 0099 — Mismatched number |' "$idx2" \
  && grep -qF '| 00045 — Longer number |' "$idx2"; } \
  && pass "a leading number other than the directory's is title content (#1889)" \
  || fail "a non-matching leading number was stripped as if it were the ADR prefix (#1889)"

# 1b. The generated table does not depend on the ambient collation. The zero-
# padded number prefix dominates the sort today, so this guards the invariant
# rather than reproducing a break: it is what stops a later numbering or slug
# change from making `--check` pass on one machine and fail on another (#1214).
if locale -a 2>/dev/null | grep -ix 'en_US.utf8' >/dev/null; then
  utf8_index="$(LC_ALL=en_US.UTF-8 bash "$d/scripts/gen-adr-index.sh" --check >/dev/null 2>&1; echo $?)"
  [ "$utf8_index" = 0 ] \
    && pass "the generated index is current under a UTF-8 collation too" \
    || fail "--check disagreed with the C-collation index under en_US.UTF-8"
else
  pass "skipped UTF-8 collation check — en_US.UTF-8 not installed"
fi

# 1c. The generated region is a fixed point of prettier (#1382). prettier owns
# markdown formatting in a TypeScript adopter's canonical `ci` lane, and it
# reformats every table it is handed: padding each cell to the column width and
# separating the table from the closing marker with a blank line. That made
# `prettier --check` and `gen-adr-index.sh --check` mutually unsatisfiable over
# one generated file, and each adopter discovered it only by going red. The
# guard comment and the trailing blank line are what make the two agree, so
# assert the emitted shape exactly rather than only that the table is present.
d="$(new_fixture)"
adr "$d" "0001-first" "0001 — First" "2026-07-01"
adr "$d" "0002-second" "0002 — Second" "2026-07-02"
gen "$d"
idx="$d/docs/decisions/README.md"
begin_line="$(grep -n '^<!-- BEGIN ADR INDEX -->$' "$idx" | cut -d: -f1)"
end_line="$(grep -n '^<!-- END ADR INDEX -->$' "$idx" | cut -d: -f1)"
[ -n "$begin_line" ] && [ "$(sed -n "$((begin_line + 1))p" "$idx")" = '<!-- prettier-ignore -->' ] \
  && pass "the generated region opens with the prettier-ignore guard (#1382)" \
  || fail "the generated region does not open with the prettier-ignore guard (#1382)"
[ -n "$end_line" ] && [ -z "$(sed -n "$((end_line - 1))p" "$idx")" ] \
  && pass "the generated table is separated from the closing marker (#1382)" \
  || fail "no blank line before the closing marker — prettier will insert one (#1382)"

# Regeneration is a fixed point of itself as well: emitting the guard and the
# blank line must not make the second run see the first run's output as stale.
cp "$idx" "$d/index-first-pass"
gen "$d"
cmp -s "$d/index-first-pass" "$idx" \
  && pass "regenerating an already-current index changes nothing" \
  || fail "regeneration is not idempotent"

# The real agreement, where a prettier is actually installed. The assertions
# above pin the shape that produces it; this one proves the shape is the right
# one against the formatter itself, under each proseWrap setting — the padded
# table prettier emits by default, and the compact one it emits for `never`.
#
# No hub workflow installs prettier, so a bare `command -v` would skip the only
# assertion that tests this change's actual claim in exactly the environment
# that has to catch a regression. Fall back to `npx`, which the runners carry.
# An `npx` that cannot reach the registry is reported as a skip rather than a
# failure: this suite must stay runnable offline, and the shape assertions above
# still cover the mechanism.
#
# The version is exact, never a range: prettier decides what "formatted
# markdown" means, so a floating 3.x would redden this suite and every adopter's
# at once on an upstream formatting change unrelated to any local edit. There is
# deliberately no `npx --no prettier` preference for an already-installed copy:
# `npx --no prettier --version` reports npm's own version and succeeds with no
# prettier anywhere, so it probes nothing and selects a command that then fails.
prettier_pin='prettier@3.9.7'
prettier_source() {
  local expected_version="${prettier_pin#prettier@}" installed_version
  if command -v prettier >/dev/null 2>&1; then
    installed_version="$(prettier --version 2>/dev/null)" || installed_version=''
    if [ "$installed_version" = "$expected_version" ]; then
      printf 'prettier'
      return 0
    fi
  fi
  if command -v npx >/dev/null 2>&1; then
    installed_version="$(npx --yes "$prettier_pin" --version 2>/dev/null)" || installed_version=''
    if [ "$installed_version" = "$expected_version" ]; then
      printf 'npx'
      return 0
    fi
  fi
  return 1
}

prettier_outcome() {
  if [ "$1" -eq 0 ]; then
    printf 'mismatch'
  elif [ "$3" -gt 0 ] || [ "$2" -eq 0 ]; then
    printf 'incomplete'
  else
    printf 'matched'
  fi
}

prettier_check() { # <fixture-root> <formatter-command> [args...]
  local fixture="$1" index="$1/docs/decisions/README.md"
  local agrees=1 runs=0 failures=0 prose_wrap
  local -a formatter_cmd
  shift
  formatter_cmd=("$@")
  for prose_wrap in preserve never always; do
    printf '{"proseWrap":"%s"}\n' "$prose_wrap" >"$fixture/.prettierrc"
    cp "$index" "$fixture/index-before-prettier"
    # A failed invocation and a real reformat are tracked apart: a half-warm npx
    # cache or registry blip must not be reported as the regression this test
    # exists to catch.
    if "${formatter_cmd[@]}" --write "$index" >/dev/null 2>&1; then
      runs=$((runs + 1))
      cmp -s "$fixture/index-before-prettier" "$index" || agrees=0
      gen "$fixture" --check || agrees=0
    else
      failures=$((failures + 1))
    fi
  done
  rm -f "$fixture/.prettierrc"
  case "$(prettier_outcome "$agrees" "$runs" "$failures")" in
    mismatch)
      fail "prettier reformatted the generated index or left --check stale (#1382)"
      return 1
      ;;
    incomplete)
      fail "prettier could not complete every proseWrap check"
      return 1
      ;;
    matched)
      pass "prettier leaves the generated index byte-identical and --check current (#1382)"
      ;;
  esac
}

# The selector must ignore a different globally installed version and use the
# exact package pin; an exact local install remains usable without npx.
prettier_selector_fixture="$(mktemp -d "$tmproot/prettier-selector.XXXXXX")"
mkdir -p "$prettier_selector_fixture/bin"
cat >"$prettier_selector_fixture/bin/prettier" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "${MOCK_PRETTIER_VERSION:?}"
SH
cat >"$prettier_selector_fixture/bin/npx" <<'SH'
#!/usr/bin/env bash
if [ "$#" -ne 3 ] || [ "$1" != --yes ] || [ "$2" != prettier@3.9.7 ] \
  || [ "$3" != --version ]; then
  exit 2
fi
printf '%s\n' "${MOCK_NPX_VERSION:?}"
SH
chmod +x "$prettier_selector_fixture/bin/prettier" "$prettier_selector_fixture/bin/npx"
prettier_original_path="$PATH"
PATH="$prettier_selector_fixture/bin:$PATH"
MOCK_PRETTIER_VERSION=9.9.9
MOCK_NPX_VERSION=3.9.7
export PATH MOCK_PRETTIER_VERSION MOCK_NPX_VERSION
selected_prettier_source="$(prettier_source)" || selected_prettier_source=''
PATH="$prettier_original_path"
export PATH
unset MOCK_PRETTIER_VERSION MOCK_NPX_VERSION
if [ "$selected_prettier_source" = npx ]; then
  pass "an unpinned system formatter falls back to the exact package pin"
else
  fail "an unpinned system formatter was selected instead of the exact package pin"
fi
PATH="$prettier_selector_fixture/bin:$PATH"
MOCK_PRETTIER_VERSION=3.9.7
MOCK_NPX_VERSION=3.9.7
export PATH MOCK_PRETTIER_VERSION MOCK_NPX_VERSION
selected_prettier_source="$(prettier_source)" || selected_prettier_source=''
PATH="$prettier_original_path"
export PATH
unset MOCK_PRETTIER_VERSION MOCK_NPX_VERSION prettier_original_path
if [ "$selected_prettier_source" = prettier ]; then
  pass "the exact local formatter is selected without npx"
else
  fail "the exact local formatter was not selected"
fi

prettier_loop_mock="$tmproot/prettier-loop-mock"
cat >"$prettier_loop_mock" <<'SH'
#!/usr/bin/env bash
count=0
if [ -f "$MOCK_PRETTIER_COUNT_FILE" ]; then
  read -r count <"$MOCK_PRETTIER_COUNT_FILE"
fi
count=$((count + 1))
printf '%s\n' "$count" >"$MOCK_PRETTIER_COUNT_FILE"
case "$MOCK_PRETTIER_MODE:$count" in
  all-fail:*) exit 1 ;;
  partial-fail:2) exit 1 ;;
  mismatch-then-fail:1) printf '\n' >>"$2" ;;
  mismatch-then-fail:2) exit 1 ;;
esac
SH
chmod +x "$prettier_loop_mock"

assert_prettier_loop_failure() {
  local mode="$1" expected="$2" label="$3"
  local fixture count_file output status call_count
  fixture="$(new_fixture)"
  adr "$fixture" "0001-first" "0001 — First" "2026-07-01"
  if ! gen "$fixture"; then
    fail "$label — could not prepare fixture"
    return
  fi
  count_file="$(mktemp "$tmproot/prettier-count.XXXXXX")"
  if output="$(MOCK_PRETTIER_MODE="$mode" MOCK_PRETTIER_COUNT_FILE="$count_file" prettier_check "$fixture" "$prettier_loop_mock")"; then
    status=0
  else
    status=$?
  fi
  read -r call_count <"$count_file"
  if [ "$status" -eq 1 ] && [[ "$output" == *"$expected"* ]] && [ "$call_count" -eq 3 ]; then
    pass "$label"
  else
    fail "$label — expected failure after three formatter calls; output: $output"
  fi
}

assert_prettier_loop_failure partial-fail "could not complete every proseWrap check" \
  "partial formatter failures fail even when other calls succeed"
assert_prettier_loop_failure all-fail "could not complete every proseWrap check" \
  "all formatter invocations failing is not skipped"
assert_prettier_loop_failure mismatch-then-fail "reformatted the generated index" \
  "a later formatter failure does not mask an earlier mismatch"

prettier_choice="$(prettier_source)" || prettier_choice=''
prettier_cmd=()
if [ "$prettier_choice" = prettier ]; then
  prettier_cmd=(prettier)
elif [ "$prettier_choice" = npx ]; then
  prettier_cmd=(npx --yes "$prettier_pin")
fi

if [ "${#prettier_cmd[@]}" -gt 0 ]; then
  prettier_check "$d" "${prettier_cmd[@]}"
else
  pass "skipped prettier agreement check — no reachable prettier"
fi
# 2. ADR directory with no README -> fail fast.
d="$(new_fixture)"; mkdir -p "$d/docs/decisions/0001-noreadme"
gen "$d" && fail "an ADR dir without README must fail" || pass "ADR dir without README fails fast"

# 3. Malformed ADR (missing **Date:**) -> fail fast.
d="$(new_fixture)"; mkdir -p "$d/docs/decisions/0001-nodate"
printf '# 0001 — No Date\n\n(no date line here)\n' > "$d/docs/decisions/0001-nodate/README.md"
gen "$d" && fail "missing **Date:** must fail" || pass "missing **Date:** fails fast"

# 4. Malformed **Date:** value -> fail fast.
d="$(new_fixture)"
adr "$d" "0001-bad-date" "0001 — Bad Date" "July 1, 2026"
gen "$d" && fail "malformed **Date:** must fail (#78)" || pass "malformed **Date:** fails fast (#78)"

# 5. ISO-shaped but impossible dates -> fail fast; leap-day boundaries matter.
d="$(new_fixture)"
adr "$d" "0001-bad-day" "0001 — Bad Day" "2026-02-30"
gen "$d" && fail "impossible calendar date must fail (#78)" || pass "impossible calendar date fails fast (#78)"

d="$(new_fixture)"
adr "$d" "0001-non-leap" "0001 — Non Leap" "2025-02-29"
gen "$d" && fail "non-leap February 29 must fail (#78)" || pass "non-leap February 29 fails fast (#78)"

# 6. Index README missing the markers -> fail fast.
d="$(new_fixture)"; printf '# Decisions\n(no markers)\n' > "$d/docs/decisions/README.md"
adr "$d" "0001-x" "0001 — X" "2026-07-01"
gen "$d" && fail "missing index markers must fail" || pass "missing index markers fails fast"

for missing_marker in BEGIN END; do
  d="$(new_fixture)"
  adr "$d" "0001-x" "0001 — X" "2026-07-01"
  sed -i "/<!-- $missing_marker ADR INDEX -->/d" "$d/docs/decisions/README.md"
  before="$(cat "$d/docs/decisions/README.md")"
  if gen "$d"; then
    fail "missing $missing_marker marker must fail"
  elif [ "$(cat "$d/docs/decisions/README.md")" != "$before" ]; then
    fail "missing $missing_marker marker modified the index"
  else
    pass "missing $missing_marker marker fails without changing the index"
  fi
done

# 7. --check detects a stale table (new ADR added without regenerating).
d="$(new_fixture)"; adr "$d" "0001-x" "0001 — X" "2026-07-01"; gen "$d"
adr "$d" "0002-y" "0002 — Y" "2026-07-02"
gen "$d" --check && fail "--check must detect a stale table" || pass "--check detects a stale table"

# 8. Different slugs cannot reuse one durable ADR number. Both conflicting
# paths must be actionable, and a failed generation must not rewrite the index.
d="$(new_fixture)"
adr "$d" "0042-first-decision" "0042 — First Decision" "2026-07-03"
adr "$d" "0042-second-decision" "0042 — Second Decision" "2026-07-04"
index_before="$(cat "$d/docs/decisions/README.md")"
if duplicate_output="$(bash "$d/scripts/gen-adr-index.sh" 2>&1)"; then
  fail "duplicate ADR numbers must fail before rendering"
elif grep -qF "$d/docs/decisions/0042-first-decision" <<<"$duplicate_output" \
  && grep -qF "$d/docs/decisions/0042-second-decision" <<<"$duplicate_output"; then
  pass "duplicate ADR failure names both conflicting paths (#555)"
else
  fail "duplicate ADR failure did not name both conflicting paths: $duplicate_output"
fi
[ "$(cat "$d/docs/decisions/README.md")" = "$index_before" ] \
  && pass "duplicate ADR failure leaves the generated index untouched" \
  || fail "duplicate ADR failure rewrote the index"
if duplicate_check_output="$(bash "$d/scripts/gen-adr-index.sh" --check 2>&1)"; then
  fail "--check must reject duplicate ADR numbers"
elif grep -qF "$d/docs/decisions/0042-first-decision" <<<"$duplicate_check_output" \
  && grep -qF "$d/docs/decisions/0042-second-decision" <<<"$duplicate_check_output"; then
  pass "--check rejects duplicates and names both conflicting paths"
else
  fail "--check duplicate failure did not name both conflicting paths: $duplicate_check_output"
fi

# A three-way collision names all three. Stopping at the first pair means
# whoever fixes the two that were named hits the same error again on a directory
# nothing mentioned (Verjson/.github#1371, found via verjson-cli-cloud#234).
d="$(new_fixture)"
adr "$d" "0007-alpha" "0007 — Alpha" "2026-07-03"
adr "$d" "0007-beta" "0007 — Beta" "2026-07-04"
adr "$d" "0007-gamma" "0007 — Gamma" "2026-07-05"
adr "$d" "0008-unrelated" "0008 — Unrelated" "2026-07-06"
if three_way_output="$(bash "$d/scripts/gen-adr-index.sh" 2>&1)"; then
  fail "a three-way ADR collision must fail before rendering"
elif grep -qF "$d/docs/decisions/0007-alpha" <<<"$three_way_output" \
  && grep -qF "$d/docs/decisions/0007-beta" <<<"$three_way_output" \
  && grep -qF "$d/docs/decisions/0007-gamma" <<<"$three_way_output" \
  && ! grep -qF "0008-unrelated" <<<"$three_way_output"; then
  pass "a three-way collision names every colliding directory and nothing else"
else
  fail "a three-way collision did not name all three: $three_way_output"
fi

# Two independent collisions are both reported in one run, so a repository does
# not discover them one regeneration at a time.
d="$(new_fixture)"
adr "$d" "0011-a" "0011 — A" "2026-07-03"
adr "$d" "0011-b" "0011 — B" "2026-07-04"
adr "$d" "0022-a" "0022 — A" "2026-07-05"
adr "$d" "0022-b" "0022 — B" "2026-07-06"
if multi_output="$(bash "$d/scripts/gen-adr-index.sh" 2>&1)"; then
  fail "independent ADR collisions must fail before rendering"
elif grep -qF "duplicate ADR number 0011" <<<"$multi_output" \
  && grep -qF "duplicate ADR number 0022" <<<"$multi_output"; then
  pass "independent collisions are both named in one run"
else
  fail "independent collisions were not both named: $multi_output"
fi

# Supersession is a relationship between distinct decisions, not duplicate
# numbering. Preserve that valid shape while rejecting number collisions.
d="$(new_fixture)"
adr "$d" "0042-original" "0042 — Original" "2026-07-03"
adr "$d" "0043-replacement" "0043 — Replacement" "2026-07-04"
printf '%s\n' '- **Supersedes:** ADR 0042' >>"$d/docs/decisions/0043-replacement/README.md"
gen "$d" \
  && grep -qF '[0042](0042-original/README.md)' "$d/docs/decisions/README.md" \
  && grep -qF '[0043](0043-replacement/README.md)' "$d/docs/decisions/README.md" \
  && pass "distinct superseding ADRs remain valid" \
  || fail "duplicate guard rejected valid supersession"

# Invalid modes must fail before touching the index or allocating temporary files.
d="$(new_fixture)"; adr "$d" "0001-x" "0001 — X" "2026-07-01"
mkdir "$d/tmp"
cp "$d/docs/decisions/README.md" "$d/index-before"
for argument in '' --chek check --verify --help; do
  TMPDIR="$d/tmp" bash "$d/scripts/gen-adr-index.sh" "$argument" >"$d/output" 2>&1
  status=$?
  if [ "$status" -eq 2 ] && grep -q 'expected no argument' "$d/output" \
    && cmp -s "$d/index-before" "$d/docs/decisions/README.md" \
    && [ -z "$(find "$d/tmp" -mindepth 1 -print)" ]; then
    pass "invalid argument '$argument' exits 2 without modifying the index"
  else
    fail "invalid argument '$argument' was accepted or changed state"
  fi
done
for extra in extra --check ''; do
  TMPDIR="$d/tmp" bash "$d/scripts/gen-adr-index.sh" --check "$extra" >"$d/output" 2>&1
  status=$?
  [ "$status" -eq 2 ] && cmp -s "$d/index-before" "$d/docs/decisions/README.md" \
    && pass "--check rejects extra argument '$extra'" \
    || fail "--check accepted extra argument '$extra' or changed the index"
done

# Both the table and rendered output belong to the parent shell's EXIT trap.
for scenario in regenerate current stale malformed-regenerate malformed-check; do
  d="$(new_fixture)"; adr "$d" "0001-x" "0001 — X" "2026-07-01"
  mkdir "$d/tmp"
  mode=(--check); expected=0
  case "$scenario" in
    regenerate) mode=() ;;
    current) gen "$d" ;;
    stale) expected=1 ;;
    malformed-*)
      gen "$d"
      mkdir "$d/docs/decisions/0002-missing-readme"
      expected=1
      [ "$scenario" = malformed-regenerate ] && mode=()
      ;;
  esac
  cp "$d/docs/decisions/README.md" "$d/index-before"
  TMPDIR="$d/tmp" bash "$d/scripts/gen-adr-index.sh" "${mode[@]}" >"$d/output" 2>&1
  status=$?
  if [ "$status" -eq "$expected" ] \
    && [ -z "$(find "$d/tmp" -mindepth 1 -print)" ] \
    && [ -z "$(find "$d/docs/decisions" -maxdepth 1 \
      \( -name '.adr-index.*' -o -name 'README.md.tmp' \) -print)" ]; then
    pass "$scenario cleans every owned temporary file"
  else
    fail "$scenario returned $status or leaked a temporary file"
  fi
  if [ "$scenario" != regenerate ]; then
    cmp -s "$d/index-before" "$d/docs/decisions/README.md" \
      && pass "$scenario leaves the original index unchanged" \
      || fail "$scenario changed the original index"
  fi
done

# Observe both real sort calls, independent of which locales CI installed.
d="$(new_fixture)"; adr "$d" "0001-x" "0001 — X" "2026-07-01"
mkdir "$d/bin"
real_sort="$(command -v sort)"
cat >"$d/bin/sort" <<'SH'
#!/usr/bin/env bash
printf '%s:%s\n' "${LC_ALL:-unset}" "$*" >>"$SORT_CALLS"
exec "$REAL_SORT" "$@"
SH
chmod +x "$d/bin/sort"
SORT_CALLS="$d/sort-calls" REAL_SORT="$real_sort" PATH="$d/bin:$PATH" LC_ALL=C.UTF-8 \
  bash "$d/scripts/gen-adr-index.sh" >"$d/output" 2>&1
status=$?
printf 'C:\nC:-r\n' >"$d/expected-calls"
[ "$status" -eq 0 ] && cmp -s "$d/expected-calls" "$d/sort-calls" \
  && pass "duplicate validation and reverse rendering both pin LC_ALL=C" \
  || fail "an ADR sort inherited ambient collation"

if [ "$fails" -eq 0 ]; then echo "All tests passed."; exit 0; else echo "$fails test(s) failed."; exit 1; fi
