#!/usr/bin/env bash
# Part of #1945 / ADR 0269: autonomous workflows and scripts must not invoke
# owner `gh pr merge --admin`. Test and fixture negatives remain allowed.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

hits="$(
  # Production trees only; exclude tests, fixtures, NEXT, and docs.
  rg -n -e '--admin' \
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
