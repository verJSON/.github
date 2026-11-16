#!/usr/bin/env bash
# Part of #1945 / ADR 0269: autonomous workflows and scripts must not invoke
# owner `gh pr merge --admin`. Test and fixture negatives remain allowed.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

# #2156: prove the inventory pattern catches --admin regardless of flag order
# after `gh pr merge` (AI-review follow-up on #2155 inverted the risk; both
# orderings must match, and a bare --admin without gh pr merge must not).
pattern='gh pr merge.{0,240}--admin'
probe_hits="$(
  printf '%s\n' \
    'gh pr merge --admin --auto' \
    'gh pr merge --auto --admin' \
    'gh pr merge "$n" --squash --admin' \
    | rg -n -U --multiline-dotall -e "$pattern" || true
)"
probe_count="$(printf '%s\n' "$probe_hits" | sed '/^$/d' | wc -l | tr -d ' ')"
if [ "$probe_count" -ne 3 ]; then
  printf 'FAIL - inventory pattern misses a gh pr merge … --admin ordering (#2156):\n%s\n' "$probe_hits"
  exit 1
fi
if printf '%s\n' 'echo --admin only' 'gh pr view --json number' | rg -q -U --multiline-dotall -e "$pattern"; then
  printf 'FAIL - inventory pattern matched a non-merge --admin probe (#2156)\n'
  exit 1
fi

hits="$(
  # Production trees only; exclude tests, fixtures, NEXT, and docs.
  # Match merge + --admin on one line or across a short multiline run block
  # (#2153; Bugbot multiline follow-up on #2155).
  rg -n -U --multiline-dotall \
    -e "$pattern" \
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
printf 'ok - inventory pattern covers both --admin flag orderings (#2156)\n'
