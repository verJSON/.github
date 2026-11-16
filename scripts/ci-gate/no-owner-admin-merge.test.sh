#!/usr/bin/env bash
# Part of #1945 / ADR 0269: autonomous workflows and scripts must not invoke
# owner `gh pr merge --admin`. Test and fixture negatives remain allowed.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

hits="$(
  # Production trees only; exclude tests, fixtures, NEXT, and docs.
  # Match merge + --admin on one line or across a short multiline run block
  # (#2153; Bugbot multiline follow-up on #2155).
  rg -n -U --multiline-dotall \
    -e 'gh pr merge.{0,240}--admin' \
    -e 'mergePullRequest.{0,240}[Aa]dmin' \
    --glob '!**/*test*' \
    --glob '!**/fixtures/**' \
    --glob '!NEXT/**' \
    --glob '!docs/**' \
    --glob '!**/README.md' \
    scripts/ .github/workflows/ || true
)"

if [ -n "$hits" ]; then
  printf 'FAIL - live --admin call sites remain (ADR 0269):\n%s\n' "$hits"
  exit 1
fi
printf 'ok - no live owner --admin merge call sites in scripts/ or workflows\n'
