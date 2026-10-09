#!/usr/bin/env bash
# node-release.yml must refuse a package it cannot publish BEFORE it reaches
# `npm publish` (#1206).
#
# node-release.yml only ever runs as the `publish` job of a generated release
# caller, so by the time it starts, changelog-release.yml has already consumed
# NEXT/, written the immutable CHANGELOG/<version>.md, committed, tagged and
# pushed. A failure at `npm publish` is therefore a permanently red run over a
# release that already completed and cannot be re-cut. `Validate release package
# directories` is the first step in the job that reads package.json, so an
# unpublishable package is rejected there with a stated cause instead.
#
# The step is executed here rather than grepped for: a diagnostic that is present
# as text but unreachable as code is exactly the failure this file exists to
# catch.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
workflow="$root/.github/workflows/node-release.yml"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/tmp"

awk '
  /^      - name: Validate release package directories$/ { candidate = 1; next }
  candidate && /^        id: package-dirs$/ { found = 1; candidate = 0; next }
  candidate && /^      - / { candidate = 0 }
  found && /^        run: \|$/ { body = 1; next }
  body && /^      - / { exit }
  body { sub(/^          /, ""); print }
' "$workflow" >"$work/validate.sh"
[ -s "$work/validate.sh" ] \
  || { echo "FAIL - could not extract the package directory validation step" >&2; exit 1; }
bash -n "$work/validate.sh"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# package_fixture <dir> <json>
package_fixture() {
  mkdir -p "$1"
  printf '%s\n' "$2" >"$1/package.json"
}

# run_validate <dir> <package-dirs-json>
run_validate() {
  local dir="$1" package_dirs="$2"
  : >"$work/output"
  (cd "$dir" && PACKAGE_DIRS="$package_dirs" PACKAGE_VERSION=1.2.3 SCOPE=@verjson \
    RUNNER_TEMP="$work/tmp" GITHUB_OUTPUT="$work/output" bash -euo pipefail "$work/validate.sh") \
    >"$work/run.out" 2>&1
}

publishable="$work/publishable"
package_fixture "$publishable" '{"name":"@verjson/thing","version":"0.0.0"}'
if run_validate "$publishable" '["."]'; then
  grep -q 'retention-targets=' "$work/output" \
    && pass "a publishable scope-owned package still validates" \
    || fail "a publishable package validated without recording retention targets"
else
  fail "a publishable scope-owned package was rejected: $(cat "$work/run.out")"
fi

private_root="$work/private-root"
package_fixture "$private_root" '{"name":"@verjson/thing","version":"0.0.0","private":true}'
if run_validate "$private_root" '["."]'; then
  fail "a private:true package reached publication instead of being refused up front"
else
  grep -qi 'private' "$work/run.out" \
    && pass "a private:true package is refused before publication, naming the cause" \
    || fail "a private:true package was rejected without naming private: $(cat "$work/run.out")"
fi

[ "$fails" -eq 0 ] || exit 1
echo "All tests passed."
