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
generated="$repo_root/.github/workflows/generated-artifacts.yml"
engine="$repo_root/scripts/changelog.py"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$workflow" ] && [ -f "$generated" ] && [ -f "$engine" ] \
  || { echo "FAIL - changelog-validate.yml, generated-artifacts.yml, or scripts/changelog.py is missing"; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

extract_step() {
  local src="$1" dest="$2"
  awk -v want='      - name: needs-changelog-context' '
    $0 == want { in_step = 1; next }
    in_step && $0 == "        run: |" { in_run = 1; next }
    in_run {
      if ($0 != "" && $0 !~ /^          /) { exit }
      sub(/^          /, "")
      print
    }
  ' "$src" >"$dest"
}

extract_step "$workflow" "$tmp/step.sh"

[ -s "$tmp/step.sh" ] \
  && pass "the needs-changelog-context step extracts from changelog-validate.yml" \
  || { fail "no needs-changelog-context step found in changelog-validate.yml"; echo "1 test(s) failed."; exit 1; }

extract_step "$generated" "$tmp/generated-step.sh"
[ -s "$tmp/generated-step.sh" ] \
  && cmp -s "$tmp/step.sh" "$tmp/generated-step.sh" \
  && pass "generated-artifacts.yml carries the same handoff step body" \
  || fail "generated-artifacts.yml handoff step is missing or drifted from changelog-validate.yml"

# The step is informational. check-pr in the following step stays the gate.
awk '
  $0 == "      - name: needs-changelog-context" { in_step = 1 }
  in_step && $0 == "        continue-on-error: true" { found = 1 }
  in_step && $0 == "      - name: Validate fragments and pull-request policy" { exit }
  END { exit found ? 0 : 1 }
' "$workflow" \
  && pass "changelog-validate handoff continues on error and stays ahead of validation" \
  || fail "changelog-validate handoff is not continue-on-error immediately before validation"

awk '
  $0 == "      - name: needs-changelog-context" { in_step = 1 }
  in_step && $0 == "        continue-on-error: true" { found = 1 }
  in_step && $0 == "      - name: Validate generated artifacts" { exit }
  END { exit found ? 0 : 1 }
' "$generated" \
  && pass "generated-artifacts handoff continues on error and stays ahead of validation" \
  || fail "generated-artifacts handoff is not continue-on-error immediately before validation"

grep -qF 'python3 -I .changelog-contract/scripts/changelog.py check-pr' "$workflow" \
  && pass "changelog-validate still runs check-pr" \
  || fail "changelog-validate no longer runs check-pr"

grep -qF 'python3 -I "$engine" check-pr' "$generated" \
  && pass "generated-artifacts still runs check-pr" \
  || fail "generated-artifacts no longer runs check-pr"

grep -qF 'PR_AUTHOR: ${{ github.event.pull_request.user.login }}' "$workflow" \
  && grep -qF 'PR_AUTHOR: ${{ github.event.pull_request.user.login }}' "$generated" \
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
printf '%s\n' "$out" >"$tmp/missing.out"
# File-backed grep: pipe-fed `grep -q` trips pipefail under #1430/#1445.
[ "$rc" -eq 1 ] \
  && grep -qF '::error title=needs-changelog-context::needs-changelog-context:' "$tmp/missing.out" \
  && grep -qF '### needs-changelog-context' "$ws/summary.md" \
  && grep -qF 'commander-15-1-0' "$ws/summary.md" \
  && grep -qF '0243-agent-authored-changelog-context' "$ws/summary.md" \
  && grep -qF 'dependency-upgrade/SKILL.md' "$ws/summary.md" \
  && pass "a Renovate dependency PR without a fragment fails with the handoff title" \
  || fail "Renovate missing-fragment case rc=$rc out=$(head -c 400 "$tmp/missing.out")"

ws="$tmp/human"
make_repo "$ws"
base="$(git -C "$ws" rev-parse HEAD~1)"
head="$(git -C "$ws" rev-parse HEAD)"
set +e
out="$(run_step "$ws" BASE_SHA="$base" HEAD_SHA="$head" PR_AUTHOR='pyousefi' 2>"$tmp/human.err")"
rc=$?
set -e
printf '%s\n' "$out" >"$tmp/human.out"
[ "$rc" -eq 0 ] && ! grep -q '::error' "$tmp/human.out" \
  && pass "a non-Renovate author does not get the handoff failure" \
  || fail "human author case rc=$rc out=$(head -c 400 "$tmp/human.out")"

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
printf '%s\n' "$out" >"$tmp/valid.out"
[ "$rc" -eq 0 ] && ! grep -q '::error' "$tmp/valid.out" \
  && pass "a valid added fragment clears the handoff" \
  || fail "valid fragment case rc=$rc out=$(head -c 400 "$tmp/valid.out")"

ws="$tmp/broken"
make_repo "$ws"
set +e
out="$(run_step "$ws" BASE_SHA='' HEAD_SHA='' PR_AUTHOR='renovate[bot]' 2>"$tmp/broken.err")"
rc=$?
set -e
printf '%s\n' "$out" >"$tmp/broken.out"
[ "$rc" -eq 0 ] && grep -qF 'not a pull request' "$tmp/broken.out" \
  && pass "a missing pull-request range skips the handoff" \
  || fail "missing range case rc=$rc out=$(head -c 400 "$tmp/broken.out")"

ws="$tmp/badsha"
make_repo "$ws"
set +e
out="$(run_step "$ws" BASE_SHA='not-a-sha' HEAD_SHA='HEAD' PR_AUTHOR='renovate[bot]' 2>"$tmp/badsha.err")"
rc=$?
set -e
printf '%s\n' "$out" >"$tmp/badsha.out"
[ "$rc" -eq 0 ] \
  && grep -qF '::warning title=needs-changelog-context::handoff check could not run' "$tmp/badsha.out" \
  && ! grep -q '::error' "$tmp/badsha.out" \
  && pass "an operational failure is not reported as a missing fragment" \
  || fail "bad sha case rc=$rc out=$(head -c 400 "$tmp/badsha.out")"

[ "$fails" -eq 0 ] && echo "ok - needs-changelog-context" || { echo "$fails test(s) failed."; exit 1; }
