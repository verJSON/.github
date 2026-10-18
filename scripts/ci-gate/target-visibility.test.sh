#!/usr/bin/env bash
# Pins the "Resolve target repo visibility" step in ai-review-merge.yml
# (Verjson/.github#170). The step resolves the org-guarded TARGET_REPO with `gh`
# and publishes `target_private` for diagnostics and restoration compatibility.
# ADR 0034 temporarily makes Verjson routing visibility-independent.
#
# An unreadable/erroring target must still leave the output empty without
# aborting the required preflight job. The same contract can be reused safely
# when #204 restores visibility-based routing. This test extracts the real
# `run:` block from the workflow and drives it with a stubbed `gh`.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="$repo_root/.github/workflows/ai-review-merge.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$wf" ] || { echo "FAIL - workflow not found: $wf"; exit 1; }

# Extract the step's run script verbatim (10-space-indented body after `run: |`,
# scoped to the step whose `id:` is target_visibility).
script="$tmp/target-visibility.sh"
awk '
  $0 == "        id: target_visibility" { seen = 1 }
  seen && !cap && $0 == "        run: |" { cap = 1; next }
  cap {
    if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
    if ($0 ~ /^[ \t]*$/) { print ""; next }
    exit  # end of this step: stop before the next step re-arms capture
  }
' "$wf" >"$script"
if ! grep -q 'target_private' "$script" || ! grep -q 'gh api' "$script"; then
  echo "FAIL - could not extract the target_visibility run block from $wf"
  exit 1
fi

# Stub `gh` on PATH: GH_STUB_OUT is what `gh api ... --jq .private` prints,
# GH_STUB_RC its exit status (nonzero simulating an unreadable/erroring target).
mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
if [ "$#" -ne 4 ] ||
  [ "$1" != "api" ] ||
  [ "$2" != "repos/$TARGET_REPO" ] ||
  [ "$3" != "--jq" ] ||
  [ "$4" != ".private" ]; then
  printf 'unexpected gh invocation:' >&2
  printf ' <%s>' "$@" >&2
  printf '\n' >&2
  exit 64
fi
if [ "${GH_STUB_RC:-0}" -ne 0 ]; then
  echo "HTTP 404: Not Found (https://api.github.com/repos/${TARGET_REPO:-})" >&2
  exit "$GH_STUB_RC"
fi
printf '%s\n' "${GH_STUB_OUT:-}"
GH
chmod +x "$tmp/bin/gh"

run_step() {
  # run_step <gh-stdout> <gh-rc> [script]
  export PATH="$tmp/bin:$PATH"
  export TARGET_REPO="Verjson/.github"
  export GITHUB_REPOSITORY="Verjson/dispatcher"
  export RUNNER_TEMP="$tmp/runner-temp"
  export GITHUB_OUTPUT="$tmp/github-output.txt"
  mkdir -p "$RUNNER_TEMP"
  : >"$GITHUB_OUTPUT"
  export GH_STUB_OUT="$1" GH_STUB_RC="$2"
  bash "${3:-$script}" >"$tmp/out.txt" 2>&1
  echo "rc=$?"
}
output_has() { grep -qxF "$1" "$tmp/github-output.txt"; }
log_has() { grep -q "$1" "$tmp/out.txt"; }

# (a) A public target remains observable as 'false'.
rc="$(run_step 'false' 0)"
{ [ "$rc" = "rc=0" ] && output_has 'target_private=false'; } \
  && pass "public target publishes target_private=false" \
  || fail "public target did not publish target_private=false ($rc)"

# (b) A private target remains observable as 'true'.
rc="$(run_step 'true' 0)"
{ [ "$rc" = "rc=0" ] && output_has 'target_private=true'; } \
  && pass "private target publishes target_private=true" \
  || fail "private target did not publish target_private=true ($rc)"

# (c) A failed lookup must NOT abort the required preflight job and must publish
# an EMPTY value with a ::warning.
rc="$(run_step '' 1)"
{ [ "$rc" = "rc=0" ] && output_has 'target_private=' && log_has '::warning::'; } \
  && pass "failed lookup exits 0 and publishes an empty target_private (#170)" \
  || fail "failed lookup aborted the step or published a non-empty value ($rc)"

# (d) Mutation fixture: looking up the dispatcher's repository must be
# observably different from looking up TARGET_REPO. The strict stub rejects the
# wrong endpoint even though it would return the same scalar in a permissive
# fixture.
dispatcher_lookup="$tmp/dispatcher-lookup.sh"
sed 's#repos/\$TARGET_REPO#repos/$GITHUB_REPOSITORY#' "$script" >"$dispatcher_lookup"
rc="$(run_step 'false' 0 "$dispatcher_lookup")"
{ [ "$rc" = "rc=0" ] && output_has 'target_private=' && log_has 'unexpected gh invocation'; } \
  && pass "dispatcher-repository lookup mutation is rejected" \
  || fail "visibility fixture accepted a lookup against the dispatcher repository ($rc)"

# (e) preflight publishes the target's visibility, and gate and dispatch-merge
# reach a persistent lane only when both the event payload and that lookup say
# private. A public target takes the ephemeral untrusted lane (ADR 0235); the
# runner-routing-policy matrix evaluates every polarity.
{ grep -q "target_private: \${{ steps.target_visibility.outputs.target_private }}" "$wf" \
    && [ "$(grep -cF "github.event.repository.private == true && needs.preflight.outputs.target_private != 'false' && " "$wf")" -eq 2 ] \
    && grep -qF "|| fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') }}" "$wf"; } \
  && pass "gate and dispatch-merge need a private payload and lookup to leave the untrusted lane" \
  || fail "gate routing drifted from the resolved-visibility policy"

# (f) The money guard. A private target must never spend hosted minutes just
# because the lookup failed: the persistent branch tests `!= 'false'`, never
# `== 'true'`, so unresolved visibility ('') keeps a private payload on its
# lane. A public target is routed by the event payload, which the lookup cannot
# widen back onto the pool (ADR 0235).
! grep -qF "target_private != 'true'" "$wf" \
  && ! grep -qF "repository.private != true" "$wf" \
  && pass "fast-lane routing never treats unresolved visibility as public" \
  || fail "routing uses a != polarity, so unreadable visibility would spend hosted minutes"

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
else
  echo "$fails test(s) failed."
  exit 1
fi
