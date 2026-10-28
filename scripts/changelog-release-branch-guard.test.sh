#!/usr/bin/env bash
# A release may only be dispatched from the default branch (#466).
#
# The check used to be a job-level `if:`, which is the wrong instrument: GitHub
# reports a skipped job as successful to its caller, so dispatching from any
# other ref produced a green run that released nothing. A reusable workflow can
# only fail its caller by running and exiting non-zero, so the properties worth
# pinning are that the check is a step, that it is the *first* step — before the
# checkout that would otherwise act on the wrong assumption — and that it really
# rejects a non-default ref rather than merely mentioning one.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
workflow="$root/.github/workflows/changelog-release.yml"
step_name='Release only from the default branch'
fails=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT

# --------------------------------------------------------------------------
# The guard must be the first step. A checkout ahead of it would already have
# resolved a ref under the assumption the guard exists to verify.
# --------------------------------------------------------------------------
first_step="$(awk '
  /^  release:$/          { injob = 1 }
  injob && /^    steps:$/ { insteps = 1; next }
  insteps && /^      - / { print; exit }
' "$workflow")"

case "$first_step" in
  *"$step_name"*) pass "the branch guard is the first step of the release job" ;;
  *) fail "the release job runs '$first_step' before checking the dispatch ref" ;;
esac

# --------------------------------------------------------------------------
# ...and the job-level gate may only exclude public reusable calls from public
# repositories. It must not skip invalid dispatch refs; the step below rejects
# those with a failing status.
# --------------------------------------------------------------------------
job_header="$(awk '
  /^  release:$/          { inside = 1; next }
  inside && /^    steps:$/ { exit }
  inside && /^  [^ ]/      { exit }
  inside                   { print }
' "$workflow")"

expected_job_if="if: \${{ (github.event_name != 'pull_request' && github.event_name != 'pull_request_review' && github.event_name != 'pull_request_review_comment' && github.event_name != 'merge_group' && github.event_name != 'pull_request_target' || github.event.repository.private == true) }}"
job_if="$(printf '%s\n' "$job_header" | sed -n '/^[[:space:]]*if:/p' | sed 's/^[[:space:]]*//')"
if [ "$job_if" = "$expected_job_if" ]; then
  pass "the job gate only excludes public events from public repositories"
else
  fail "the release job gate diverged from the public-call guard: ${job_if:-missing}"
fi

# --------------------------------------------------------------------------
# Execute the real block rather than asserting on its text. A comparison that
# is inverted, or reads an environment variable the workflow never sets, looks
# identical to grep and passes everything at runtime.
# --------------------------------------------------------------------------
guard="$(awk -v name="$step_name" '
  index($0, name) && /^      - name:/ { instep = 1; next }
  instep && /^      - /               { exit }
  instep && /^        run: \|$/       { capture = 1; next }
  capture && /^        [^ ]/          { exit }
  capture                             { print }
' "$workflow" | sed 's/^          //')"

printf '%s\n' "$guard" >"$sandbox/guard.sh"

# Bounded, not merely non-empty: the terminator is indentation, so a reshaped
# step could extend the extraction into unrelated YAML and every assertion
# below would then be passing on code the step never runs.
guard_lines="$(grep -c . <"$sandbox/guard.sh")"
if [ ! -s "$sandbox/guard.sh" ] ||
   [ "$guard_lines" -gt 12 ] ||
   ! grep -q 'DISPATCH_REF' "$sandbox/guard.sh"; then
  fail "could not extract the branch guard's run block from $workflow (got $guard_lines lines)"
  echo "$fails test(s) failed."
  exit 1
fi

run_guard() {
  DISPATCH_REF="$1" DEFAULT_BRANCH="$2" \
    bash -c 'set -euo pipefail; . "$0"' "$sandbox/guard.sh" 2>&1
}

# The permitted case has to stay permitted, or the guard is just an outage.
run_guard refs/heads/main main >/dev/null
[ $? -eq 0 ] \
  && pass "a dispatch from the default branch is allowed through" \
  || fail "the guard rejects a dispatch from the default branch"

# ...and a repository whose default branch is not `main` is the case a
# hard-coded comparison would silently break.
run_guard refs/heads/trunk trunk >/dev/null
[ $? -eq 0 ] \
  && pass "the default branch is read from the repository, not hard-coded" \
  || fail "the guard assumes a default branch name instead of reading it"

for ref in refs/heads/feature/x refs/heads/release refs/tags/v1.2.3 refs/pull/7/merge; do
  output="$(run_guard "$ref" main)"
  status=$?
  if [ "$status" -eq 0 ]; then
    fail "a dispatch from $ref is allowed to release"
  elif ! grep -qF "$ref" <<<"$output"; then
    fail "the rejection of $ref does not name the offending ref"
  else
    pass "a dispatch from $ref is rejected and named"
  fi
done

# A near-miss that string containment alone would wave through.
run_guard refs/heads/main-backup main >/dev/null 2>&1
[ $? -ne 0 ] \
  && pass "a ref that merely starts with the default branch is rejected" \
  || fail "the comparison is a prefix match, so main-backup can release"

# --------------------------------------------------------------------------
# Being on the default branch is not the same as being on the commit the caller
# verified. Resolving the checkout by branch *name* re-reads the branch head at
# snapshot time, which is a different commit whenever anything merged since the
# dispatch — and a caller's verify job can hold the gap open for its whole
# timeout, or the `changelog-release-${{ github.repository }}` concurrency group
# (cancel-in-progress: false) can hold it open behind another release. The
# window is not theoretical: it releases and publishes a tree nothing checked.
#
# github.sha is the caller's dispatch commit. The release push guard
# compares the target branch to that SHA and requires the new commit to be
# its direct child before atomically publishing the branch and tag.
# --------------------------------------------------------------------------
checkout_ref="$(awk '
  /^      - name: Check out release repository$/ { instep = 1; next }
  instep && /^      - /                          { exit }
  instep && /^          ref:/                    { print; exit }
' "$workflow" | sed 's/^ *ref: *//')"

case "$checkout_ref" in
  '${{ github.sha }}')
    pass "the snapshot checks out the dispatch commit, not the branch head" ;;
  '')
    fail "could not find the release checkout's ref: in $workflow" ;;
  *)
    fail "the release checks out '$checkout_ref', which re-resolves at snapshot time; a merge during verify is tagged unverified" ;;
esac

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
