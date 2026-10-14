#!/usr/bin/env bash
# Exercises scripts/assert-mergeable-head.sh against a stubbed `gh`, per house
# convention. Every negative case asserts the SPECIFIC gate and exit code, so a
# harness regression cannot pass by failing somewhere else — and so that a
# future change which collapses the three gates back into one bare boolean
# cannot land green.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/assert-mergeable-head.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

HEAD_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
APP_ID=15368
OTHER_APP_ID=99999

mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
set -uo pipefail
args="$*"
case "$args" in
  *"pr view "*"--json headRefOid,baseRefName"*)
    [ "${FAIL_PR_VIEW:-0}" -eq 0 ] || { echo "gh: some transient API error (HTTP 502)" >&2; exit 1; }
    printf '%s\n' "$PR_JSON_FIXTURE" ;;
  *"/rules/branches/"*)
    # Record the exact requested path so a test can pin the ref spelling (#1470).
    [ -z "${RULES_REQUEST_LOG:-}" ] || printf '%s\n' "$args" >>"$RULES_REQUEST_LOG"
    # `--paginate` walks several requests through one invocation, so stderr can carry
    # earlier lines before the one that ended the walk. This models that: a note emitted
    # ahead of whatever the request itself does, success or failure.
    [ -z "${RULES_STDERR_NOTE:-}" ] || printf '%s\n' "$RULES_STDERR_NOTE" >&2
    [ "${FAIL_RULES:-0}" -eq 0 ] || { echo "gh: some transient API error (HTTP 502)" >&2; exit 1; }
    # Real `gh api` reports an authorization refusal on stderr in this shape and
    # exits 1 -- the same exit as a 502, which is why the script has to read the
    # status rather than the exit code to tell the two causes apart.
    [ -z "${FAIL_RULES_HTTP:-}" ] || {
      case "$FAIL_RULES_HTTP" in
        401) echo "gh: Bad credentials (HTTP 401)" >&2 ;;
        403) echo "gh: Resource not accessible by integration (HTTP 403)" >&2 ;;
        *) echo "gh: Not Found (HTTP $FAIL_RULES_HTTP)" >&2 ;;
      esac
      exit 1; }
    printf '%s\n' "$RULES_FIXTURE" ;;
  *"/check-runs?per_page"*)
    [ "${FAIL_CHECK_RUNS:-0}" -eq 0 ] || { echo "gh: some transient API error (HTTP 502)" >&2; exit 1; }
    printf '%s\n' "$CHECK_RUNS_FIXTURE" ;;
  *"/commits/"*"/status"*)
    [ "${FAIL_STATUS:-0}" -eq 0 ] || { echo "gh: some transient API error (HTTP 502)" >&2; exit 1; }
    printf '%s\n' "$STATUS_FIXTURE" ;;
  *"/actions/runs?head_sha="*)
    # Logged so a test can prove the workflow-run read happens only when a cancelled
    # check run makes it necessary (#1754): callers without Actions read keep working.
    [ -z "${RUNS_REQUEST_LOG:-}" ] || printf '%s\n' "$args" >>"$RUNS_REQUEST_LOG"
    [ "${FAIL_RUNS:-0}" -eq 0 ] || { echo "gh: some transient API error (HTTP 502)" >&2; exit 1; }
    printf '%s\n' "$RUNS_FIXTURE" ;;
  *"/annotations?per_page"*)
    [ "${FAIL_ANNOTATIONS:-0}" -eq 0 ] || { echo "gh: some transient API error (HTTP 502)" >&2; exit 1; }
    printf '%s\n' "$ANNOTATIONS_FIXTURE" ;;
  *)
    echo "gh: unstubbed call: $args" >&2; exit 1 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH"

rules_bound="[{\"type\":\"required_status_checks\",\"parameters\":{\"required_status_checks\":[{\"context\":\"ci / build-test\",\"integration_id\":$APP_ID},{\"context\":\"changelog / validate\",\"integration_id\":$APP_ID}]}}]"
rules_unbound='[{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"ci / build-test"},{"context":"changelog / validate"}]}}]'
rules_none='[{"type":"pull_request","parameters":{}}]'

check_run() { # id name status conclusion app-id [check-suite-id]
  printf '{"id":%s,"name":"%s","status":"%s","conclusion":%s,"app":{"id":%s}%s}' \
    "$1" "$2" "$3" "$(if [ -z "$4" ]; then echo null; else echo "\"$4\""; fi)" "$5" \
    "$(if [ -n "${6:-}" ]; then printf ',"check_suite":{"id":%s}' "$6"; fi)"
}
# One workflow run as `actions/runs?head_sha=` reports it. `run_number` is the
# per-workflow event sequence, so a higher number is the later event.
wf_run() { # id check-suite-id workflow-id run-number event status conclusion [head-sha]
  printf '{"id":%s,"check_suite_id":%s,"workflow_id":%s,"run_number":%s,"event":"%s","status":"%s","conclusion":%s,"head_sha":"%s"}' \
    "$1" "$2" "$3" "$4" "$5" "$6" "$(if [ -z "$7" ]; then echo null; else echo "\"$7\""; fi)" "${8:-$HEAD_SHA}"
}
wf_runs() { printf '{"total_count":%s,"workflow_runs":[%s]}' "$(entry_count "$1")" "$1"; }
# Both fixture builders stamp `total_count`, because both real endpoints always
# do -- verified against a live commit, including the empty-status case, which
# returns `total_count: 0` rather than omitting the field. A fixture without it
# would model a response GitHub does not send and would quietly exempt itself
# from the page-walk reconciliation.
runs() { printf '{"total_count":%s,"check_runs":[%s]}' "$(entry_count "$1")" "$1"; }
stat_page() { printf '{"total_count":%s,"statuses":[%s]}' "$(entry_count "$1")" "$1"; }
entry_count() { jq 'length' <<<"[$1]"; }

green_runs="$(check_run 1 'ci / build-test' completed success "$APP_ID"),$(check_run 2 'changelog / validate' completed success "$APP_ID")"

run() {
  OUT="$("$script" "${REPO:-Verjson/verjson-ci}" "${PR:-185}" 2>&1)"
  RC=$?
}

expect() { # label expected-rc needle
  if [ "$RC" -eq "$2" ] && grep -qF -- "$3" <<<"$OUT"; then
    pass "$1"
  else
    fail "$1 (rc=$RC, output: $OUT)"
  fi
}

expect_true() { # label
  if [ "$RC" -eq 0 ] && [ "$OUT" = "true" ]; then pass "$1"
  else fail "$1 (rc=$RC, output: $OUT)"; fi
}

# A pass that degraded to display-name matching must SAY so. Asserting the
# warning keeps the degradation from becoming invisible again.
expect_true_unbound() { # label
  if [ "$RC" -eq 0 ] \
    && grep -q '^true$' <<<"$OUT" \
    && grep -q '::warning::Gate A matched these required contexts on display name alone' <<<"$OUT"
  then pass "$1"
  else fail "$1 (rc=$RC, output: $OUT)"; fi
}

reset_env() {
  unset FAIL_PR_VIEW FAIL_RULES FAIL_RULES_HTTP RULES_STDERR_NOTE \
    FAIL_CHECK_RUNS FAIL_STATUS FAIL_ANNOTATIONS FAIL_RUNS
  export RULES_FIXTURE="$rules_bound"
  export RUNS_REQUEST_LOG="$tmp/runs-requests"
  : >"$RUNS_REQUEST_LOG"
  export RUNS_FIXTURE
  RUNS_FIXTURE="$(wf_runs '')"
  export RULES_REQUEST_LOG="$tmp/rules-requests"
  : >"$RULES_REQUEST_LOG"
  export PR_JSON_FIXTURE
  PR_JSON_FIXTURE="$(printf '{"headRefOid":"%s","baseRefName":"main"}' "$HEAD_SHA")"
  export CHECK_RUNS_FIXTURE
  CHECK_RUNS_FIXTURE="$(runs "$green_runs")"
  STATUS_FIXTURE="$(stat_page '')"
  export STATUS_FIXTURE
  export ANNOTATIONS_FIXTURE='[]'
}

# --- the only accepting case ------------------------------------------------
reset_env; run
expect_true "a head whose required contexts all passed, from the bound app, with no deferral, prints true"

# --- Gate A -----------------------------------------------------------------
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 1 'ci / build-test' completed success "$APP_ID")")"
run
expect "an absent required context fails closed rather than being satisfied by its absence" 3 "absent: changelog / validate"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 1 'ci / build-test' in_progress '' "$APP_ID"),$(check_run 2 'changelog / validate' completed success "$APP_ID")")"
run
expect "a still-running required context is not mistaken for a passing one" 3 "pending: ci / build-test"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 1 'ci / build-test' completed failure "$APP_ID"),$(check_run 2 'changelog / validate' completed success "$APP_ID")")"
run
expect "a failing required context is refused by name" 3 "not passing: ci / build-test"

# The impostor case: a DIFFERENT app publishes a check with the required
# context's exact display name while the genuine one is absent. Matching on the
# name alone would report this head as fully verified.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 1 'ci / build-test' completed success "$OTHER_APP_ID"),$(check_run 2 'changelog / validate' completed success "$APP_ID")")"
run
expect "a required context published by an app the ruleset does not bind is refused" 3 "wrong producer: ci / build-test"

# A legacy commit status carries no app id, so it cannot satisfy a bound context.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 2 'changelog / validate' completed success "$APP_ID")")"
STATUS_FIXTURE="$(stat_page '{"context":"ci / build-test","state":"success"}')"
run
expect "an app-bound required context cannot be satisfied by a provenance-less commit status" 3 "wrong producer: ci / build-test"

reset_env
RULES_FIXTURE="$rules_unbound"
CHECK_RUNS_FIXTURE="$(runs '')"
STATUS_FIXTURE="$(stat_page '{"context":"ci / build-test","state":"success"},{"context":"changelog / validate","state":"success"}')"
run
expect_true_unbound "an unbound required context is satisfied by a legacy commit status, and says it matched on name alone"

reset_env
RULES_FIXTURE="$rules_unbound"
CHECK_RUNS_FIXTURE="$(runs '')"
STATUS_FIXTURE="$(stat_page '{"context":"ci / build-test","state":"error"},{"context":"changelog / validate","state":"success"}')"
run
expect "a commit status in the error state is not passing" 3 "not passing: ci / build-test"

reset_env
RULES_FIXTURE="$rules_none"
run
expect "a base ref governed by no required checks cannot be gated by a green rollup" 3 "declares no required status checks"
# The other side of the #1437 discriminator: rules WERE returned, so the ref is genuinely
# ungoverned by status checks and the remedy names the ruleset that already governs it.
# Without this the two arms could be collapsed back into one sentence and stay green.
expect "a populated rule list names the ruleset that already exists rather than an empty response" 3 "1 rule(s) of type: pull_request"

# An EMPTY rule list and a populated list with no required_status_checks rule are
# different conditions with opposite remedies (#1437). The empty list must not be
# reported with the ungoverned-ref sentence, because the remedy that sentence implies --
# add a ruleset -- is wrong for the causes that actually produce an empty list.
reset_env
RULES_FIXTURE='[]'
run
expect "an empty rules response is refused without being attributed to an ungoverned ref" 3 "returned an EMPTY rule list"

reset_env
RULES_FIXTURE='"not-an-array"'
run
expect "a malformed rules response is named as a bad shape, not as an empty or ungoverned one" 3 "is not a JSON array"

# `null`, `false`, and an empty body are non-arrays too, and they are the ones a proxy or
# a cached error page actually produces. They must not be reported as an EMPTY rule list:
# that arm's remedies are "add a ruleset" and "fix the ref", and neither is the remedy for
# a body that never described the ref's governance at all.
for body in 'null' 'false' ''; do
  reset_env
  RULES_FIXTURE="$body"
  run
  expect "a ${body:-empty} rules body is named as a bad shape rather than an empty rule list" \
    3 "is not a JSON array"
done

# An array whose entries are not objects is still an array, so it lands in the populated
# arm and its sentence promises the rule types that were read. The sentence must not come
# out with that promise unfilled.
reset_env
RULES_FIXTURE='["a","b"]'
run
expect "a populated list of non-objects still names a type rather than trailing off" 3 "2 rule(s) of type: ?"

# --- superseded cancellations (#1754) ----------------------------------------
# A draft PR marked ready starts a second run of the same workflow on the same head,
# and concurrency cancels the first. Both runs' check runs stay attached to the head
# (they are different check suites, so the endpoint's default `filter=latest` keeps
# both). Suite 501 / run 10 is the cancelled draft-time run; suite 502 / run 11 is
# the ready_for_review run that passed. The cancelled `shell-test-groups` is the
# unexpanded matrix name, which the passing run reports only as expanded legs -- the
# shape #1750 actually carried, and why supersession is decided per RUN, not per name.
WF_CI=315894159
WF_OTHER=371658702
superseded_runs="$(check_run 11 'ci / build-test' completed cancelled "$APP_ID" 501),$(check_run 12 'changelog / validate' completed cancelled "$APP_ID" 501),$(check_run 13 'shell-test-groups' completed cancelled "$APP_ID" 501),$(check_run 21 'ci / build-test' completed success "$APP_ID" 502),$(check_run 22 'changelog / validate' completed success "$APP_ID" 502),$(check_run 23 'shell-test-groups (merge-gate)' completed success "$APP_ID" 502)"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$superseded_runs")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")"
run
if [ "$RC" -eq 0 ] && grep -q '^true$' <<<"$OUT" \
  && grep -qF -- '::notice::' <<<"$OUT" && grep -qF -- 'superseded' <<<"$OUT"; then
  pass "a cancelled run superseded by a later successful run of the same workflow, event, and head is admitted, and the dismissal is reported"
  if grep -qF -- "check run 13, run 9001 superseded by run 9002" <<<"$OUT"; then
    pass "a dismissal names the check run, the cancelled run, and the run that superseded it"
  else
    fail "a dismissal names the check run, the cancelled run, and the run that superseded it (output: $OUT)"
  fi
  if grep -qF -- "actions/runs?head_sha=$HEAD_SHA&" "$RUNS_REQUEST_LOG"; then
    pass "the workflow-run read is scoped to the pull request's head SHA"
  else
    fail "the workflow-run read is scoped to the pull request's head SHA (requests: $(cat "$RUNS_REQUEST_LOG"))"
  fi
else
  fail "a cancelled run superseded by a later successful run of the same workflow, event, and head is admitted, and the dismissal is reported (rc=$RC, output: $OUT)"
fi

# The reverse order must block. The later run is the later event's verification
# intent -- a draft-time run defers the heavy tiers (ADR 0223), so an EARLIER success
# can be a reduced run -- and its cancellation means that intent never completed. The
# same-head earlier success does not stand in for it; that is verjson-cli#251's
# unexercised-head class.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 11 'ci / build-test' completed success "$APP_ID" 501),$(check_run 12 'changelog / validate' completed success "$APP_ID" 501),$(check_run 21 'ci / build-test' completed cancelled "$APP_ID" 502),$(check_run 22 'changelog / validate' completed cancelled "$APP_ID" 502)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed success),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed cancelled)")"
run
expect "a cancellation AFTER an earlier success of the same workflow still blocks: only a later run supersedes" 3 "not passing: changelog / validate (SUCCESS,CANCELLED)"

# Every workflow's checks are published by the same GitHub Actions app, so app id is
# no provenance between workflows: a later successful run of a DIFFERENT workflow that
# happens to carry a same-named job must not dismiss the cancelled one. Its run number
# is deliberately higher, so only workflow identity can refuse it.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 11 'ci / build-test' completed cancelled "$APP_ID" 501),$(check_run 12 'changelog / validate' completed success "$APP_ID" 501),$(check_run 21 'ci / build-test' completed success "$APP_ID" 502)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_OTHER" 11 pull_request completed success)")"
run
expect "a later success of a DIFFERENT workflow with a same-named job does not supersede a cancelled required check" 3 "not passing: ci / build-test (CANCELLED,SUCCESS)"

# The same workflow under a different event is a different verification: a
# workflow_dispatch or push run tests the ref, a pull_request run tests the merge
# result, and their `if:` conditions differ. Only the same event supersedes.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$superseded_runs")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 workflow_dispatch completed success)")"
run
expect "a later success of the same workflow under a DIFFERENT event does not supersede a cancelled check" 3 "not passing: ci / build-test (CANCELLED,SUCCESS)"

# Supersession explains a run that was cancelled AS A RUN, which is what concurrency
# does. A cancelled job inside a run that concluded otherwise (a fail-fast matrix, a
# timeout) is evidence about that run's own outcome, and a later run does not erase it.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$superseded_runs")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed failure),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")"
run
expect "a cancelled job inside a run that concluded FAILURE is not superseded by a later success" 3 "not passing: ci / build-test (CANCELLED,SUCCESS)"

# Malformed run records fail closed, by TYPE and never by truthiness. These are the
# shapes that would otherwise fail OPEN: jq orders every string above every number,
# so a string run number `"9"` compares greater than 10; and two absent workflow ids
# or events compare equal (`null == null`), forging a same-workflow match.
for mutation in '.workflow_runs[1].run_number = "9"' \
  '.workflow_runs[].workflow_id = null' \
  '.workflow_runs[] |= del(.event)' \
  '.workflow_runs[1].conclusion = 1' \
  '.workflow_runs[0].status = null' \
  '.workflow_runs[1].check_suite_id = "502"' \
  '.workflow_runs[1].id = null' \
  '.workflow_runs[1] = "not-a-run"'; do
  reset_env
  CHECK_RUNS_FIXTURE="$(runs "$superseded_runs")"
  RUNS_FIXTURE="$(jq -c "$mutation" <<<"$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")")"
  run
  expect "a workflow-run inventory with a malformed record ($mutation) is a fault, not a supersession" 1 "workflow-run inventory for"
done

# A superseder has to have FINISHED. A record that is still running cannot have
# verified anything yet, whatever conclusion field it happens to carry; and a
# cancelled run that is not itself completed (a re-run in flight) is not settled.
for shape in 'superseder in_progress:in_progress:completed' 'cancelled run re-running:completed:in_progress'; do
  label="${shape%%:*}"; rest="${shape#*:}"; new_status="${rest%%:*}"; old_status="${rest#*:}"
  reset_env
  CHECK_RUNS_FIXTURE="$(runs "$superseded_runs")"
  RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request "$old_status" cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request "$new_status" success)")"
  run
  expect "an unfinished run on either side ($label) supersedes nothing" 3 "not passing: ci / build-test (CANCELLED,SUCCESS)"
done

# The read is filtered by head SHA, so a record for any other commit means the
# response is not answering the question asked. A success on a different commit
# verified different code and must never supersede anything on this one.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$superseded_runs")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb)")"
run
expect "a workflow run reported for a different head SHA is a fault, never a superseder" 1 "workflow-run inventory for"

# A check suite belongs to exactly one workflow run. Two records claiming one suite
# make the cancelled check's own run ambiguous, and picking either is a guess.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$superseded_runs")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9003 501 "$WF_CI" 10 pull_request completed failure),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")"
run
expect "two workflow runs claiming one check suite are a fault, not a guess at which one was cancelled" 1 "workflow-run inventory for"

# The workflow-run walk is an inventory like the other two, reconciled on the same
# terms: a short walk, a body that states no count, and an empty body are faults.
for body in '{"total_count":7,"workflow_runs":[]}' '{"workflow_runs":[]}' ''; do
  reset_env
  CHECK_RUNS_FIXTURE="$(runs "$superseded_runs")"
  if [ "$body" = '{"total_count":7,"workflow_runs":[]}' ]; then
    RUNS_FIXTURE="$(jq -c '. + {total_count: 7}' <<<"$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")")"
  else
    RUNS_FIXTURE="$body"
  fi
  run
  expect "an incomplete or unattested workflow-run walk (${body:-empty body}) is a fault" 1 "workflow-run inventory for"
done

reset_env
CHECK_RUNS_FIXTURE="$(runs "$superseded_runs")"
FAIL_RUNS=1 run
expect "a workflow-run read failure is a fault, not a pass" 1 "failed to fetch workflow runs"
expect "a workflow-run read failure names its required read grant" 1 "Actions (read) or Actions permissions.actions: read"

# Only a FINISHED cancellation is dismissible. A record still in progress that
# carries a cancelled conclusion is inconsistent, and dismissing it would erase a
# pending check -- the state Gate A exists to refuse.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 11 'ci / build-test' in_progress cancelled "$APP_ID" 501),$(check_run 12 'changelog / validate' completed success "$APP_ID" 502),$(check_run 21 'ci / build-test' completed success "$APP_ID" 502)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")"
run
expect "an in-progress check run carrying a cancelled conclusion is never dismissed" 3 "pending: ci / build-test"

# A cancellation nothing later superseded is still a cancellation: it blocks.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 11 'ci / build-test' completed cancelled "$APP_ID" 501),$(check_run 12 'changelog / validate' completed success "$APP_ID" 501)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled)")"
run
expect "a cancelled-only required context blocks" 3 "not passing: ci / build-test (CANCELLED)"

# Supersession dismisses CANCELLED and nothing else. A failure blocks whether it came
# before or after a success of the same workflow: a later failure is the newest
# verdict, and an earlier one is genuine evidence about this same commit.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 11 'ci / build-test' completed success "$APP_ID" 501),$(check_run 12 'changelog / validate' completed success "$APP_ID" 501),$(check_run 21 'ci / build-test' completed failure "$APP_ID" 502),$(check_run 22 'changelog / validate' completed success "$APP_ID" 502)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed success),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed failure)")"
run
expect "a success followed by a later failure of the same workflow blocks" 3 "not passing: ci / build-test (SUCCESS,FAILURE)"
if [ ! -s "$RUNS_REQUEST_LOG" ]; then
  pass "a head carrying no cancelled check run never reads workflow runs, so callers without Actions read are unaffected"
else
  fail "a head carrying no cancelled check run never reads workflow runs (requests: $(cat "$RUNS_REQUEST_LOG"))"
fi

reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 11 'ci / build-test' completed failure "$APP_ID" 501),$(check_run 12 'changelog / validate' completed cancelled "$APP_ID" 501),$(check_run 21 'ci / build-test' completed success "$APP_ID" 502),$(check_run 22 'changelog / validate' completed success "$APP_ID" 502)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")"
run
expect "a failure inside a superseded run is not dismissed with its cancelled siblings" 3 "not passing: ci / build-test (FAILURE,SUCCESS)"

# A newer run still in flight supersedes nothing, and its own pending check blocks.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 11 'ci / build-test' completed cancelled "$APP_ID" 501),$(check_run 12 'changelog / validate' completed success "$APP_ID" 501),$(check_run 21 'ci / build-test' in_progress '' "$APP_ID" 502)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request in_progress '')")"
run
expect "a cancelled check whose newer sibling run is still pending blocks as pending" 3 "pending: ci / build-test"

# Dismissal removes evidence; it never manufactures it. A required context whose only
# report was a superseded cancellation is absent, and an absent context fails closed.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 11 'ci / build-test' completed cancelled "$APP_ID" 501),$(check_run 12 'changelog / validate' completed success "$APP_ID" 502)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")"
run
expect "a required context reported only by a superseded cancellation is absent, not satisfied" 3 "absent: ci / build-test"

# A cancelled check run that cannot be placed in a workflow run -- another app's
# check suite, or a record with no suite -- has no provenance to supersede it.
for suite in 777 ''; do
  reset_env
  CHECK_RUNS_FIXTURE="$(runs "$(check_run 11 'ci / build-test' completed cancelled "$APP_ID" $suite),$(check_run 12 'changelog / validate' completed success "$APP_ID" 502),$(check_run 21 'ci / build-test' completed success "$APP_ID" 502)")"
  RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")"
  run
  expect "a cancelled check run in no known workflow run (suite '${suite:-none}') is never superseded" 3 "not passing: ci / build-test (CANCELLED,SUCCESS)"
done

# Gate C equivalents. The first replays #1750's lifecycle re-arm shape: two earlier
# runs of a second workflow cancelled, superseded by its third run.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 31 'rearm / event-policy' completed cancelled "$APP_ID" 601),$(check_run 32 'rearm / event-policy' completed cancelled "$APP_ID" 602),$(check_run 33 'rearm / event-policy' completed success "$APP_ID" 603)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9101 601 "$WF_OTHER" 73 pull_request_target completed cancelled),$(wf_run 9102 602 "$WF_OTHER" 74 pull_request_target completed cancelled),$(wf_run 9103 603 "$WF_OTHER" 75 pull_request_target completed success)")"
run
if [ "$RC" -eq 0 ] && grep -q '^true$' <<<"$OUT" && grep -qF -- 'rearm / event-policy' <<<"$OUT"; then
  pass "Gate C admits advisory checks cancelled by a later successful run of their own workflow (#1750 re-arm shape)"
else
  fail "Gate C admits advisory checks cancelled by a later successful run of their own workflow (#1750 re-arm shape) (rc=$RC, output: $OUT)"
fi

reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 31 'rearm / event-policy' completed cancelled "$APP_ID" 601)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9101 601 "$WF_OTHER" 73 pull_request_target completed cancelled)")"
run
expect "Gate C still refuses a cancelled-only advisory check" 5 "rearm / event-policy=CANCELLED"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 31 'rearm / event-policy' completed cancelled "$APP_ID" 601),$(check_run 33 'rearm / event-policy' completed success "$APP_ID" 603)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9101 601 "$WF_OTHER" 73 pull_request_target completed cancelled),$(wf_run 9103 603 "$WF_CI" 75 pull_request_target completed success)")"
run
expect "Gate C refuses an advisory cancellation 'superseded' only by another workflow" 5 "rearm / event-policy=CANCELLED"

# A later run supersedes only by SUCCEEDING. One that ended any other way -- here a
# startup failure that never created a job, so it carries no check run of its own to
# block on -- verified nothing, and the earlier cancellation stays the newest word.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 31 'rearm / event-policy' completed cancelled "$APP_ID" 601)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9101 601 "$WF_OTHER" 73 pull_request_target completed cancelled),$(wf_run 9102 602 "$WF_OTHER" 74 pull_request_target completed startup_failure)")"
run
expect "a later run of the same workflow that did not SUCCEED supersedes nothing" 5 "rearm / event-policy=CANCELLED"

# Review attack A1. `event` cannot tell a draft-time run from a ready-time one: both
# are `pull_request`, and a run whose guarded jobs were all SKIPPED still concludes
# `success` (ready -> converted_to_draft -> ready, ADR 0223; or an adopter guarding
# heavy jobs off `labeled`). A later run that reports the SAME check as anything but
# SUCCESS did not do that check's work, so the earlier cancellation keeps blocking.
for conclusion in skipped neutral; do
  reset_env
  CHECK_RUNS_FIXTURE="$(runs "$(check_run 11 'ci / build-test' completed cancelled "$APP_ID" 501),$(check_run 12 'changelog / validate' completed cancelled "$APP_ID" 501),$(check_run 21 'ci / build-test' completed "$conclusion" "$APP_ID" 502),$(check_run 22 'changelog / validate' completed "$conclusion" "$APP_ID" 502)")"
  RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")"
  run
  upper="$(tr '[:lower:]' '[:upper:]' <<<"$conclusion")"
  expect "A1: a later successful run that reports the required check as $upper does not supersede its cancellation" 3 "not passing: ci / build-test (CANCELLED,$upper)"

  reset_env
  CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 31 'heavy-suite' completed cancelled "$APP_ID" 601),$(check_run 32 'heavy-suite' completed "$conclusion" "$APP_ID" 602)")"
  RUNS_FIXTURE="$(wf_runs "$(wf_run 9101 601 "$WF_OTHER" 73 pull_request completed cancelled),$(wf_run 9102 602 "$WF_OTHER" 74 pull_request completed success)")"
  run
  expect "A1 under Gate C: a later successful run that reports the advisory check as $upper does not supersede its cancellation" 5 "heavy-suite=CANCELLED"
done

# The superseder may OMIT the check -- #1750's cancelled matrix parent
# `shell-test-groups` has no same-named successor -- but then nothing in that run
# vouches for the context. A required context whose cancellation was set aside must
# still be backed by a SUCCESS of its own; a SKIPPED from another suite does not do.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 11 'ci / build-test' completed cancelled "$APP_ID" 501),$(check_run 22 'changelog / validate' completed success "$APP_ID" 502),$(check_run 31 'ci / build-test' completed skipped "$APP_ID" 503)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")"
run
expect "a required context whose cancellation was set aside must still have a SUCCESS, not only a SKIPPED" 3 "not passing: ci / build-test (SKIPPED; a superseded cancellation needs a SUCCESS)"

# Every other same-named report in the superseder keeps the cancellation too. These
# already block on their own; the guard keeps them from being reasoned away later.
for shape in 'cancelled:completed' 'failure:completed' ':completed' ':in_progress' 'success:in_progress'; do
  conclusion="${shape%%:*}"; status="${shape#*:}"
  reset_env
  CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 31 'heavy-suite' completed cancelled "$APP_ID" 601),$(check_run 32 'heavy-suite' "$status" "$conclusion" "$APP_ID" 602)")"
  RUNS_FIXTURE="$(wf_runs "$(wf_run 9101 601 "$WF_OTHER" 73 pull_request completed cancelled),$(wf_run 9102 602 "$WF_OTHER" 74 pull_request completed success)")"
  run
  expect "a superseder reporting the same check as '${conclusion:-no conclusion}' ($status) supersedes nothing" 5 "heavy-suite=CANCELLED"
done

# The NEWEST successful later run decides. An older full run cannot be cherry-picked
# over a newer run that skipped the work (ready, draft again: the head is back in the
# reduced tier, and the next ready run is what has to vouch for it).
reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 31 'heavy-suite' completed cancelled "$APP_ID" 601),$(check_run 32 'heavy-suite' completed success "$APP_ID" 602),$(check_run 33 'heavy-suite' completed skipped "$APP_ID" 603)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9101 601 "$WF_OTHER" 73 pull_request completed cancelled),$(wf_run 9102 602 "$WF_OTHER" 74 pull_request completed success),$(wf_run 9103 603 "$WF_OTHER" 75 pull_request completed success)")"
run
expect "only the newest successful later run may supersede; an older full run is not cherry-picked over a newer skip" 5 "heavy-suite=CANCELLED"

# "Later" is strict. Two distinct runs of one workflow never share a run number, so
# a record that does is not later -- and must not supersede.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$superseded_runs")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 10 pull_request completed success)")"
run
expect "a run with the SAME run number is not later and supersedes nothing" 3 "not passing: ci / build-test (CANCELLED,SUCCESS)"

# Gate B reads the raw inventory. Supersession is a rule for the pass/fail gates; a
# deferral that ran -- cancelled or not -- stays execution evidence it never touches.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$superseded_runs,$(check_run 14 'ci / deferred-ci' completed cancelled "$APP_ID" 501),$(check_run 24 'ci / deferred-ci' completed skipped "$APP_ID" 502)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 9001 501 "$WF_CI" 10 pull_request completed cancelled),$(wf_run 9002 502 "$WF_CI" 11 pull_request completed success)")"
run
expect "a superseded cancellation of deferred-ci still trips Gate B: supersession never reaches the execution-evidence gate" 4 "ci / deferred-ci=CANCELLED"

# --- Gate B -----------------------------------------------------------------
for name in 'build-test / deferred-ci' 'deferred-ci' 'deferred-ci (push)' 'ci / deferred-ci (ubuntu-latest)' 'Deferred-CI'; do
  reset_env
  CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 9 "$name" completed failure "$APP_ID")")"
  run
  expect "a deferral named '$name' is caught by the execution-evidence gate" 4 "was DEFERRED, not verified"
done

reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 9 'build-test / deferred-ci' completed success "$APP_ID")")"
run
expect "a deferred-ci that somehow concluded SUCCESS still proves the head was deferred" 4 "was DEFERRED, not verified"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 9 'build-test / deferred-ci' completed skipped "$APP_ID")")"
run
expect_true "a skipped deferred-ci is the normal exercised path and does not block"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 9 'x / deferred-cindy' completed success "$APP_ID")")"
run
expect_true "a longer name sharing the prefix after a separator does not falsely trip the gate"

# Gate B must not be relaxable from the environment: an earlier draft exposed
# these as tunable patterns, which made disabling it a one-variable edit.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 9 'build-test / deferred-ci' completed failure "$APP_ID")")"
# These are the script's OWN constant names. An earlier version of this test
# used the sibling script's names, which the gate never reads, so it would have
# passed against a build where the gate really was overridable.
OUT="$(DEFERRED_JOB_PATTERN='$x^' DEFERRED_ANNOTATION_PATTERN='$x^' "$script" Verjson/verjson-ci 185 2>&1)"; RC=$?
expect "the deferral gate cannot be switched off from the environment" 4 "was DEFERRED, not verified"

# A commit-status context uses `prefix/deferred-ci` with no space, and a check
# run may carry a trailing matrix separator. Both are deferrals.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs")"
STATUS_FIXTURE="$(stat_page '{"context":"ci / build-test","state":"success"},{"context":"changelog / validate","state":"success"},{"context":"continuous-integration/deferred-ci","state":"success"}')"
run
expect "a slash-separated commit-status deferral blocks" 4 "was DEFERRED, not verified"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 9 'deferred-ci / verify' completed success "$APP_ID")")"
run
expect "a deferral naming a sub-step still blocks" 4 "was DEFERRED, not verified"

reset_env
ANNOTATIONS_FIXTURE='[{"title":"CI deferred","message":"nothing ran"}]'
run
expect "an ADR 0156 deferral annotation still blocks the composite-action path" 4 "carries a deferral annotation"

# --- Gate C -----------------------------------------------------------------
reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 3 'mirror-contract' completed failure "$APP_ID")")"
run
expect "a failing advisory check blocks under its own gate, naming the check" 5 "mirror-contract=FAILURE"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_runs,$(check_run 3 'mirror-contract' queued '' "$APP_ID")")"
run
expect "a queued advisory check blocks rather than being read as absent" 5 "mirror-contract=QUEUED"

# --- unevaluable states fail closed, never green ----------------------------
reset_env; FAIL_PR_VIEW=1 run
expect "a pull-request metadata failure is a fault, not a pass" 1 "failed to fetch pull request metadata"
expect "a pull-request metadata failure names its required read grant" 1 "Pull requests (read) or Actions permissions.pull-requests: read"

reset_env; FAIL_RULES=1 run
expect "an unreadable ruleset is a fault: the required set must never be inferred from the head" 1 "cannot establish the required-check set"

# Gate A is evaluable ONLY from the base ref's ruleset, so a caller whose token
# cannot read that endpoint has not failed a gate -- it has failed to acquire
# the gate's input. `gh api` exits 1 for an authorization refusal and for a 502
# alike, so an implementation that reads only the exit code cannot tell an
# operator whether to retry or to fix a token. Each authorization status must
# name the permission as the cause and state what the caller needs.
for code in 401 403; do
  reset_env
  FAIL_RULES_HTTP="$code" run
  expect "an HTTP $code on the ruleset read names the token permission as the cause, not a generic API failure" \
    1 "the token presented cannot read it"
  expect "an HTTP $code on the ruleset read states the read access Gate A requires" \
    1 "Metadata (read)"
  expect "an HTTP $code on the ruleset read states pull-request metadata access" \
    1 "Pull requests (read)"
  expect "an HTTP $code on the ruleset read states check-run access" \
    1 "Checks (read)"
  expect "an HTTP $code on the ruleset read states commit-status access" \
    1 "Commit statuses (read)"
  expect "an HTTP $code on the ruleset read states Actions grants" \
    1 "permissions.pull-requests: read, permissions.checks: read, permissions.statuses: read"
  expect "an HTTP $code on the ruleset read states the conditional Actions read grant" \
    1 "Actions (read) when the head carries a cancelled check run"
  expect "an HTTP $code on the ruleset read states the Actions workflow permission" \
    1 "permissions.statuses: read, permissions.actions: read."
done

# GitHub masks an unauthorized read of a private repository as 404, so a 404
# must not be reported as a definitively missing ref: an operator who trusts
# that goes looking for a deleted branch instead of at their token.
reset_env
FAIL_RULES_HTTP=404 run
expect "an HTTP 404 on the ruleset read says it may be a masked authorization failure" \
  1 "the token presented cannot read it"

# The discrimination that makes the above worth having: a genuinely transient
# failure must NOT be reported as a permission problem, or the new message is
# just the old opaque one with more words.
reset_env; FAIL_RULES=1 run
if [ "$RC" -eq 1 ] && ! grep -qF -- "the token presented cannot read it" <<<"$OUT"; then
  pass "a transient ruleset API failure is not misreported as a permission failure"
else
  fail "a transient ruleset API failure is not misreported as a permission failure (rc=$RC, output: $OUT)"
fi

# ...and on that transient path the replayed `gh` text is the ONLY thing that separates a
# 502 from a 500 from a connection reset: the script's own message is identical for all
# three, and the status is deliberately not matched. Asserting the literal diagnostic is
# what makes dropping the replay a visible regression rather than a silent one.
reset_env; FAIL_RULES=1 run
expect "a transient ruleset failure replays gh's own diagnostic, the only thing distinguishing a 502 from a 500" \
  1 "gh: some transient API error (HTTP 502)"

# A `--paginate` walk emits one stderr line per request it makes, so the refusal that
# ENDED the walk is the last status in the stream, not the first. A note from an earlier
# page carrying its own status must not be mistaken for the fault: reading the first one
# here would see the 301 and report the generic transient message, sending an operator to
# retry a request their token will never be allowed to make.
reset_env
export RULES_STDERR_NOTE='gh: warning: repos/Verjson/verjson-ci/rules/branches/main was redirected (HTTP 301); following'
FAIL_RULES_HTTP=403 run
expect "the LAST status in a multi-line paginated stderr stream is the one that explains the refusal" \
  1 "the token presented cannot read it"
# Needled on the script's OWN message rather than on `(HTTP 403)` alone, which the
# replayed stderr would satisfy by itself and so would assert nothing about the match.
expect "a multi-line paginated stderr stream reports the refusing status, not an earlier page's" \
  1 "(HTTP 403): the token presented cannot read it"

# Capturing the ruleset read's stderr to a file redirects it away from the caller on the
# SUCCESS path too, not only on the failure path that inspects it. A 200 that carries a
# rate-limit or deprecation warning is exactly the signal an operator needs BEFORE the
# limit is reached, and it reached them before the capture existed. Replaying it has to be
# unconditional on the file being non-empty, not a consequence of having failed.
reset_env
export RULES_STDERR_NOTE='gh: warning: API rate limit remaining 12 of 5000'
run
if [ "$RC" -eq 0 ] \
  && grep -q '^true$' <<<"$OUT" \
  && grep -qF -- 'gh: warning: API rate limit remaining 12 of 5000' <<<"$OUT"
then pass "a warning on a SUCCESSFUL ruleset read still reaches the caller rather than being swallowed by the capture"
else fail "a warning on a SUCCESSFUL ruleset read still reaches the caller rather than being swallowed by the capture (rc=$RC, output: $OUT)"
fi

# A page walk that returns fewer runs than the endpoint claims exist leaves the
# gate reasoning over a short inventory -- the same shape as a `failure` the
# gate never sees. Reconciliation is only real if a short page is a fault.
reset_env
CHECK_RUNS_FIXTURE="$(jq -c --argjson runs "$(runs "$green_runs")" \
  '$runs + {total_count: 7}' <<<'{}')"
run
expect "a truncated check-run page walk is a fault, not a short green inventory" \
  1 "check-run inventory for"

# Commit statuses are the other half of the same inventory, and the endpoint
# Gate C reads a `failure` from. A short walk there hides exactly what Gate C
# exists to see, so it is a fault on the same terms.
reset_env
STATUS_FIXTURE="$(jq -c '. + {total_count: 9}' \
  <<<"$(stat_page '{"context":"ci / build-test","state":"success"}')")"
run
expect "a truncated commit-status page walk is a fault, not a short green inventory" \
  1 "commit-status inventory for"

# Defaulting the claim to the observation would make the reconciliation vacuous
# precisely where it matters: a body that states no count reconciles nothing,
# and an empty body reconciles zero against zero while the gate goes on to
# reason over an inventory in which no check can be seen at all.
reset_env
CHECK_RUNS_FIXTURE='{"check_runs":[]}'
run
expect "a check-run response that states no total_count is unattested, not empty" \
  1 "check-run inventory for"

reset_env
CHECK_RUNS_FIXTURE=''
run
expect "an empty check-run body is unattested, not a zero-length inventory" \
  1 "check-run inventory for"

reset_env
STATUS_FIXTURE='{"statuses":[]}'
run
expect "a commit-status response that states no total_count is unattested, not empty" \
  1 "commit-status inventory for"

reset_env; FAIL_CHECK_RUNS=1 run
expect "a check-runs failure is a fault, not a pass" 1 "failed to fetch check runs"
expect "a check-runs failure names its required read grant" 1 "Checks (read) or Actions permissions.checks: read"

reset_env; FAIL_STATUS=1 run
expect "a commit-status failure is a fault, not a pass" 1 "failed to fetch commit statuses"
expect "a commit-status failure names its required read grant" 1 "Commit statuses (read) or Actions permissions.statuses: read"

reset_env; FAIL_ANNOTATIONS=1 run
expect "an annotations failure is a fault, not a pass" 1 "failed to fetch annotations"
expect "an annotations failure names its required read grant" 1 "Checks (read) or Actions permissions.checks: read"

reset_env
PR_JSON_FIXTURE='{"headRefOid":"","baseRefName":"main"}'
run
expect "a missing head SHA is a fault rather than an empty comparison" 1 "could not resolve a head SHA"

reset_env
PR_JSON_FIXTURE="$(printf '{"headRefOid":"%s","baseRefName":""}' "$HEAD_SHA")"
run
expect "a missing base ref is a fault, since it names the governing ruleset" 1 "could not resolve a base ref"

# --- ref spelling on the rules/branches/ path segment (#1470) --------------------
reset_env
run
if [ "$RC" -eq 0 ] && grep -qE -- "repos/Verjson/verjson-ci/rules/branches/main$" "$RULES_REQUEST_LOG"; then
  pass "a plain base ref is spent unchanged as the rules/branches/ path segment"
else
  fail "a plain base ref was not requested as rules/branches/main (rc=$RC, requests: $(cat "$RULES_REQUEST_LOG"))"
fi

reset_env
PR_JSON_FIXTURE="$(printf '{"headRefOid":"%s","baseRefName":"release/1.x"}' "$HEAD_SHA")"
run
if [ "$RC" -eq 0 ] && grep -qE -- "repos/Verjson/verjson-ci/rules/branches/release%2F1\.x$" "$RULES_REQUEST_LOG" \
    && ! grep -qF -- "rules/branches/release/1.x" "$RULES_REQUEST_LOG"; then
  pass "a slash-bearing base ref is spent as ONE percent-encoded path segment (release%2F1.x), never with a literal slash (#1470)"
else
  fail "a slash-bearing base ref was not requested as rules/branches/release%2F1.x (rc=$RC, requests: $(cat "$RULES_REQUEST_LOG"))"
fi

# --- usage ------------------------------------------------------------------
OUT="$("$script" 2>&1)"; RC=$?
expect "no arguments is a usage error" 2 "usage:"
OUT="$("$script" not-a-repo 185 2>&1)"; RC=$?
expect "a malformed repository is a usage error" 2 "usage:"
OUT="$("$script" Verjson/verjson-ci not-a-number 2>&1)"; RC=$?
expect "a non-numeric pull-request number is a usage error" 2 "usage:"

if [ "$fails" -ne 0 ]; then
  printf '\n%d assertion(s) failed\n' "$fails" >&2
  exit 1
fi
printf '\nall assertions passed\n'
