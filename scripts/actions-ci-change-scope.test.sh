#!/usr/bin/env bash
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
classify="$here/actions-ci-change-scope.sh"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

scope_of() {
  printf '%s\n' "$@" | bash "$classify"
}

[ "$(scope_of "")" = heavy=true ] \
  && pass "an empty diff stays on the heavy matrix" \
  || fail "an empty diff was classified $(scope_of "")"

[ "$(scope_of "docs/decisions/0222-example/README.md" "NEXT/2026-10-09-issue-1734-example.md" "CHANGELOG/v1.md" "README.md")" = heavy=false ] \
  && pass "documentation paths skip the heavy matrix" \
  || fail "documentation paths were classified heavy"

[ "$(scope_of "scripts/README.md")" = heavy=true ] \
  && pass "markdown under scripts stays on the heavy matrix" \
  || fail "scripts markdown skipped the heavy matrix"

[ "$(scope_of ".github/workflows/actions-ci.yml")" = heavy=true ] \
  && pass "workflow edits stay on the heavy matrix" \
  || fail "a workflow edit skipped the heavy matrix"

[ "$(scope_of "docs/helper.sh")" = heavy=true ] \
  && pass "a shell script under docs stays on the heavy matrix" \
  || fail "a shell script under docs skipped the heavy matrix"

[ "$(scope_of "Makefile")" = heavy=true ] \
  && pass "a non-documentation path stays on the heavy matrix" \
  || fail "Makefile skipped the heavy matrix"

[ "$(scope_of "docs/a.md" "scripts/actions-ci-group.sh")" = heavy=true ] \
  && pass "one heavy path keeps the heavy matrix" \
  || fail "a mixed diff skipped the heavy matrix"

[ "$fails" -eq 0 ] || exit 1
echo "All tests passed."
