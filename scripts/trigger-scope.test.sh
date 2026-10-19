#!/usr/bin/env bash
# Contract check: every file a repository-wide scanner reads must be matched by
# an `actions-ci` path trigger (Verjson/.github#305).
#
# `doc-tag-pins.sh` reads every tracked file and `doc-fragment-names.sh` every
# tracked `*.md`, but the trigger is an enumeration. A surface a scanner reads
# and the trigger omits produces the worst failure shape available: the PR that
# introduces the bad example passes, and the next unrelated PR to touch a
# triggering path fails instead — pointing at the wrong change and the wrong
# author. Widening the trigger once fixes today; asserting the two agree is what
# stops them drifting apart again.
#
# Test seam: TRIGGER_SCOPE_ROOT selects the repository to check.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="${TRIGGER_SCOPE_ROOT:-$(cd "$here/.." && pwd)}"
workflow="$root/.github/workflows/actions-ci.yml"
fails=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$workflow" ] || { echo "FAIL - workflow not found: $workflow"; exit 1; }

# The `paths:` list of the pull_request trigger, as written. Read from the
# workflow rather than restated here — a copy would let the two drift, which is
# the defect this file exists to prevent.
mapfile -t globs < <(
  awk '
    /^  pull_request:/ { in_pr = 1; next }
    in_pr && /^    paths:/ { in_paths = 1; next }
    in_paths && /^      - / {
      line = $0
      sub(/^      - /, "", line)
      gsub(/^['"'"'"]|['"'"'"]$/, "", line)
      print line
      next
    }
    in_paths && /^[^ ]/ { exit }
    in_paths && /^  [^ ]/ { exit }
  ' "$workflow"
)

[ "${#globs[@]}" -gt 0 ] \
  && pass "the pull_request path triggers were read from the workflow" \
  || fail "could not read any path trigger — the rest of this file would pass vacuously"

# Translate the glob forms GitHub accepts and this workflow uses into a test.
# Deliberately narrow: an unrecognised form is a hard error, not a silent
# "matches nothing", because that would quietly weaken every assertion below.
matches_any() { # matches_any <path>
  local path="$1" glob
  for glob in "${globs[@]}"; do
    case "$glob" in
      '**/*.'*)  [ "${path##*.}" = "${glob##*.}" ] && return 0 ;;
      *'/**')    [ "${path#"${glob%/**}"/}" != "$path" ] && return 0 ;;
      *'**'*)    echo "unhandled glob form: $glob" >&2; return 2 ;;
      *)         [ "$path" = "$glob" ] && return 0 ;;
    esac
  done
  return 1
}

# Every file each scanner actually reads, from the same command the scanner uses.
assert_scanner_covered() { # assert_scanner_covered <label> <ls-files args...>
  local label="$1"; shift
  local uncovered=0 checked=0 file rc
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    checked=$((checked + 1))
    matches_any "$file"
    rc=$?
    if [ "$rc" -eq 2 ]; then
      fail "$label: a path trigger uses a glob form this check cannot evaluate"
      return
    fi
    if [ "$rc" -ne 0 ]; then
      printf '     uncovered: %s\n' "$file" >&2
      uncovered=$((uncovered + 1))
    fi
  done < <(git -C "$root" ls-files "$@")

  if [ "$checked" -eq 0 ]; then
    fail "$label: matched no tracked file, so this assertion proves nothing"
  elif [ "$uncovered" -eq 0 ]; then
    pass "$label: all $checked scanned file(s) are covered by a path trigger"
  else
    fail "$label: $uncovered of $checked scanned file(s) would not trigger their own PR"
  fi
}

assert_scanner_covered "doc-fragment-names.sh scans tracked *.md" '*.md'

# doc-tag-pins.sh reads every tracked file. Requiring a trigger for literally
# every path would mean running this suite on every PR, which is a cost the
# repository has already opened issues about (#233, #234). The honest contract is
# narrower and is asserted as such: every file that can carry a pin example — any
# markdown, and the workflow/action YAML where `uses:` lines live.
assert_scanner_covered "doc-tag-pins.sh scans documentation" '*.md'
assert_scanner_covered "doc-tag-pins.sh scans workflow definitions" '.github/workflows/*' '.github/actions/*'

# container-deployment-contract.test.sh (changelog-release-1) reads the pinned
# deployment CLI package under contracts/. A Renovate bump there must run the
# suite, and it must report the required shell-tests check that admission needs.
assert_scanner_covered "container-deployment-contract.test.sh reads contracts/" 'contracts/'

# doc-tag-pins.sh reads every tracked file, and the narrowing above leaves a real
# gap (#360): a pin example in a tracked file that is neither markdown nor
# workflow/action YAML — `.github/FUNDING.yml`, a release config — does not trigger
# `actions-ci`, so the next unrelated PR receives the failure.
#
# Rather than requiring a trigger for literally every path (which would run this
# suite on every PR — the cost #233/#234 exist for), assert the narrower thing that
# actually matters: every tracked file that ALREADY CARRIES a pin-shaped `uses:`
# line must be covered. That is the population `doc-tag-pins.sh` can fail on, so
# covering it closes the gap without widening the trigger to everything.
# The pattern is EXTRACTED from doc-tag-pins.sh, not restated here. Restating it
# is how this assertion goes quietly wrong: my first version anchored on `uses:`
# at line start, while the scanner matches a tag-shaped pin anywhere in any
# tracked file — so a pin in a comment (or in prose) was invisible to the check
# that exists to find exactly that. If the scanner's pattern changes, this fails
# loudly rather than silently narrowing.
IFS= read -r pin_pattern < <(sed -nE "s/.*grep -oE '([^']+)'.*/\1/p" "$root/scripts/doc-tag-pins.sh") || true
if [ -z "$pin_pattern" ]; then
  fail "could not extract the pin pattern from doc-tag-pins.sh; the coverage check below would be a guess"
  pin_carriers=""
else
  pass "the pin pattern was read from doc-tag-pins.sh: $pin_pattern"
  pin_carriers="$(git -C "$root" grep -lE "$pin_pattern" -- . 2>/dev/null || true)"
fi
if [ -z "$pin_carriers" ]; then
  fail "found no tracked file carrying a tag-shaped pin, so the pin-coverage assertion proves nothing"
else
  pin_uncovered=0 pin_checked=0
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    pin_checked=$((pin_checked + 1))
    if ! matches_any "$file"; then
      printf '     uncovered pin carrier: %s\n' "$file" >&2
      pin_uncovered=$((pin_uncovered + 1))
    fi
  done <<<"$pin_carriers"
  [ "$pin_uncovered" -eq 0 ] \
    && pass "doc-tag-pins.sh: all $pin_checked tracked file(s) carrying a pin are covered by a path trigger" \
    || fail "doc-tag-pins.sh: $pin_uncovered of $pin_checked file(s) carrying a pin would not trigger their own PR (#360)"
fi

# The two trigger lists must stay identical: a push-only gap means `main` can go
# red for a file whose PR never ran the check.
#
# Compared as SETS, not counts (#360). The previous form counted `^      - ` lines
# in each block, so swapping one push path for a different path kept the count and
# passed while the sets diverged — which is the exact divergence the comment above
# says must not happen. The old label ("the same number of paths") was honest about
# what it checked; it just was not the invariant.
read_paths() { # read_paths <start-anchor> <end-anchor>
  awk -v start="$1" -v end="$2" '
    $0 ~ start { in_block = 1; next }
    in_block && $0 ~ end { exit }
    in_block && /^      - / {
      line = $0
      sub(/^      - /, "", line)
      gsub(/^['"'"'"]|['"'"'"]$/, "", line)
      print line
    }
  ' "$workflow" | sort
}
pr_paths="$(read_paths '^  pull_request:' '^  push:')"
push_paths="$(read_paths '^  push:' '^permissions:')"
pr_count="$(printf '%s\n' "$pr_paths" | grep -c .)"
if [ "$pr_count" -eq 0 ]; then
  fail "read no pull_request paths, so the trigger-parity assertion proves nothing"
elif [ "$pr_paths" = "$push_paths" ]; then
  pass "the pull_request and push triggers list the same $pr_count paths, as sets"
else
  printf '     only in pull_request:\n%s\n' "$(comm -23 <(printf '%s\n' "$pr_paths") <(printf '%s\n' "$push_paths") | sed 's/^/       /')" >&2
  printf '     only in push:\n%s\n' "$(comm -13 <(printf '%s\n' "$pr_paths") <(printf '%s\n' "$push_paths") | sed 's/^/       /')" >&2
  fail "trigger lists diverge as sets, not merely in count (#360)"
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
