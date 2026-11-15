#!/usr/bin/env bash
# Verjson/.github#2120 — Renovate dependency PRs report needs-changelog-context
# without weakening check-pr.
#
# The step is extracted from changelog-validate.yml and executed. A grep for
# the title would pass if the step ran after check-pr (and therefore never ran
# on the failure it exists to explain) or if its failure became the merge gate.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
workflow="$repo_root/.github/workflows/changelog-validate.yml"
engine="$repo_root/scripts/changelog.py"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$workflow" ] && [ -f "$engine" ] \
  || { echo "FAIL - changelog-validate.yml or scripts/changelog.py is missing"; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

awk -v want='      - name: needs-changelog-context' '
  $0 == want { in_step = 1; next }
  in_step && $0 == "        run: |" { in_run = 1; next }
  in_run {
    if ($0 != "" && $0 !~ /^          /) { exit }
    sub(/^          /, "")
    print
  }
' "$workflow" >"$tmp/step.sh"

[ -s "$tmp/step.sh" ] \
  && pass "the needs-changelog-context step extracts from changelog-validate.yml" \
  || { fail "no needs-changelog-context step found in changelog-validate.yml"; echo "1 test(s) failed."; exit 1; }

# The step is informational. check-pr in the following step stays the gate.
awk '
  $0 == "      - name: needs-changelog-context" { in_step = 1 }
  in_step && $0 == "        continue-on-error: true" { found = 1 }
  in_step && $0 == "      - name: Validate fragments and pull-request policy" { exit }
  END { exit found ? 0 : 1 }
' "$workflow" \
  && pass "the handoff step continues on error and stays ahead of validation" \
  || fail "the handoff step is not continue-on-error immediately before validation"

grep -qF 'python3 -I .changelog-contract/scripts/changelog.py check-pr' "$workflow" \
  && pass "validation still runs check-pr" \
  || fail "validation no longer runs check-pr"

grep -qF 'PR_AUTHOR: ${{ github.event.pull_request.user.login }}' "$workflow" \
  && grep -qF -- '--author "${PR_AUTHOR:-}"' "$tmp/step.sh" \
  && ! grep -qF 'github.event.pull_request.user.login' "$tmp/step.sh" \
  && pass "the author reaches the step as an environment value, not an inline expression" \
  || fail "the author is not passed through the environment"

! grep -qF 'check-pr' "$tmp/step.sh" \
  && pass "the handoff step does not invoke check-pr" \
  || fail "the handoff step invokes check-pr"

make_repo() {
  local ws="$1"
  mkdir -p "$ws/NEXT" "$ws/.changelog-contract/scripts"
  cp "$engine" "$ws/.changelog-contract/scripts/changelog.py"
  git -C "$ws" init -q
  git -C "$ws" config user.name "Test"
  git -C "$ws" config user.email "test@example.com"
  printf 'base\n' >"$ws/README.md"
  git -C "$ws" add README.md
  git -C "$ws" commit -qm base
  printf '%s\n' '{"dependencies":{"commander":"^15.1.0"}}' >"$ws/package.json"
  git -C "$ws" add package.json
  git -C "$ws" commit -qm 'dependency update'
}

run_step() {
  local ws="$1"
  shift
  (
    cd "$ws" && env GITHUB_WORKSPACE="$ws" GITHUB_STEP_SUMMARY="$ws/summary.md" "$@" bash "$tmp/step.sh"
  )
}

ws="$tmp/missing"
make_repo "$ws"
base="$(git -C "$ws" rev-parse HEAD~1)"
head="$(git -C "$ws" rev-parse HEAD)"
set +e
out="$(run_step "$ws" BASE_SHA="$base" HEAD_SHA="$head" PR_AUTHOR='renovate[bot]' 2>"$tmp/missing.err")"
rc=$?
set -e
[ "$rc" -eq 1 ] \
  && printf '%s\n' "$out" | grep -qF '::error title=needs-changelog-context::needs-changelog-context:' \
  && grep -qF '### needs-changelog-context' "$ws/summary.md" \
  && grep -qF 'commander-15-1-0' "$ws/summary.md" \
  && grep -qF '0243-agent-authored-changelog-context' "$ws/summary.md" \
  && grep -qF 'dependency-upgrade/SKILL.md' "$ws/summary.md" \
  && pass "a Renovate dependency PR without a fragment fails with the handoff title" \
  || fail "Renovate missing-fragment case rc=$rc out=$(printf '%s' "$out" | head -c 400)"

ws="$tmp/human"
make_repo "$ws"
base="$(git -C "$ws" rev-parse HEAD~1)"
head="$(git -C "$ws" rev-parse HEAD)"
set +e
out="$(run_step "$ws" BASE_SHA="$base" HEAD_SHA="$head" PR_AUTHOR='pyousefi' 2>"$tmp/human.err")"
rc=$?
set -e
[ "$rc" -eq 0 ] && ! printf '%s\n' "$out" | grep -q '::error' \
  && pass "a non-Renovate author does not get the handoff failure" \
  || fail "human author case rc=$rc out=$(printf '%s' "$out" | head -c 400)"

ws="$tmp/valid"
make_repo "$ws"
cat >"$ws/NEXT/2026-11-15-issue-2120-commander-15-1-0.md" <<'EOF'
---
date: 2026-11-15
issue: 2120
impact: patch
title: Exercise commander 15.1.0
---

Installed commander 15.1.0 and exercised the consumer.
EOF
git -C "$ws" add NEXT/2026-11-15-issue-2120-commander-15-1-0.md
git -C "$ws" commit -qm 'add fragment'
base="$(git -C "$ws" rev-parse HEAD~2)"
head="$(git -C "$ws" rev-parse HEAD)"
set +e
out="$(run_step "$ws" BASE_SHA="$base" HEAD_SHA="$head" PR_AUTHOR='renovate[bot]' 2>"$tmp/valid.err")"
rc=$?
set -e
[ "$rc" -eq 0 ] && ! printf '%s\n' "$out" | grep -q '::error' \
  && pass "a valid added fragment clears the handoff" \
  || fail "valid fragment case rc=$rc out=$(printf '%s' "$out" | head -c 400)"

ws="$tmp/broken"
make_repo "$ws"
set +e
out="$(run_step "$ws" BASE_SHA='' HEAD_SHA='' PR_AUTHOR='renovate[bot]' 2>"$tmp/broken.err")"
rc=$?
set -e
[ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -qF 'not a pull request' \
  && pass "a missing pull-request range skips the handoff" \
  || fail "missing range case rc=$rc out=$(printf '%s' "$out" | head -c 400)"

ws="$tmp/badsha"
make_repo "$ws"
set +e
out="$(run_step "$ws" BASE_SHA='not-a-sha' HEAD_SHA='HEAD' PR_AUTHOR='renovate[bot]' 2>"$tmp/badsha.err")"
rc=$?
set -e
[ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -qF '::warning title=needs-changelog-context::handoff check could not run' \
  && ! printf '%s\n' "$out" | grep -q '::error' \
  && pass "an operational failure is not reported as a missing fragment" \
  || fail "bad sha case rc=$rc out=$(printf '%s' "$out" | head -c 400)"

[ "$fails" -eq 0 ] && echo "ok - needs-changelog-context" || { echo "$fails test(s) failed."; exit 1; }
