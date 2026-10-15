#!/usr/bin/env bash
# Exercises the concurrency-family supersession in scripts/assert-mergeable-head.sh
# (ADR 0226, amendment 2026-10-15) against a stubbed `gh`.
#
# GitHub keeps at most ONE pending run per concurrency group: a newer pending run
# replaces the older pending one, which concludes `cancelled` without a runner or a
# step. `gate-rearm.yml` declares `ai-review-arm-<PR>` and every generated re-arm
# caller inherits it, so a `gate re-arm` run can be displaced by a later
# `AI review lifecycle re-arm` run -- a DIFFERENT workflow, which ADR 0226's
# same-workflow rule never sets aside. Verjson/verjson-agents#432 (head ae5f8326) is
# the live case: run 38787495945 (`gate re-arm`, created 18:39:57) cancelled with
# zero steps, displaced by 38787540131 and then 38787543084 (`AI review lifecycle
# re-arm`, created 18:40:16, success); Gate C exited 5 on
# `rearm / event-policy=CANCELLED`.
#
# Every negative case asserts the specific gate and exit code.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/assert-mergeable-head.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

HEAD_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
OTHER_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
APP_ID=15368
WF_GATE=381766279
WF_LIFECYCLE=381766276
WF_UNRELATED=315894159
WF_REQUIRED=360793393
CANONICAL_REF="Verjson/.github/.github/workflows/gate-rearm.yml@ee656fdb181eeb9ec9ae3765497930968cc544b7"

mkdir -p "$tmp/bin" "$tmp/jobs"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
set -uo pipefail
args="$*"
case "$args" in
  *"pr view "*"--json headRefOid,baseRefName"*) printf '%s\n' "$PR_JSON_FIXTURE" ;;
  *"/rules/branches/"*) printf '%s\n' "$RULES_FIXTURE" ;;
  *"/check-runs?per_page"*) printf '%s\n' "$CHECK_RUNS_FIXTURE" ;;
  *"/commits/"*"/status"*) printf '{"total_count":0,"statuses":[]}\n' ;;
  *"/actions/runs?head_sha="*) printf '%s\n' "$RUNS_FIXTURE" ;;
  *"/actions/runs/"*"/attempts/"*"/jobs"*)
    [ -z "${JOBS_REQUEST_LOG:-}" ] || printf '%s\n' "$args" >>"$JOBS_REQUEST_LOG"
    [ "${FAIL_JOBS:-0}" -eq 0 ] || { echo "gh: Resource not accessible by integration (HTTP 403)" >&2; exit 1; }
    run_id="${args#*/actions/runs/}"; run_id="${run_id%%/*}"
    attempt="${args#*/attempts/}"; attempt="${attempt%%/*}"
    fixture="$JOBS_DIR/$run_id-$attempt.json"
    [ -r "$fixture" ] || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
    cat "$fixture" ;;
  *"/annotations?per_page"*) printf '[]\n' ;;
  *) echo "gh: unstubbed call: $args" >&2; exit 1 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH"
export JOBS_DIR="$tmp/jobs"

check_run() { # id name conclusion check-suite-id [status]
  printf '{"id":%s,"name":"%s","status":"%s","conclusion":%s,"app":{"id":%s},"check_suite":{"id":%s}}' \
    "$1" "$2" "${5:-completed}" "$(if [ -z "$3" ]; then echo null; else echo "\"$3\""; fi)" "$APP_ID" "$4"
}
runs() { printf '{"total_count":%s,"check_runs":[%s]}' "$(jq 'length' <<<"[$1]")" "$1"; }

# One workflow run as `actions/runs?head_sha=` reports it, with the identity fields the
# family rule reads. Overrides are applied as a jq object merged over the defaults.
#   wf_run id suite workflow-id run-number name path created conclusion referenced-json [overrides-json]
wf_run() {
  jq -nc --arg head "$HEAD_SHA" \
    --argjson id "$1" --argjson suite "$2" --argjson wf "$3" --argjson num "$4" \
    --arg name "$5" --arg path "$6" --arg created "$7" --arg conclusion "$8" \
    --argjson refs "$9" --argjson over "${10:-{\}}" '
    { id: $id, check_suite_id: $suite, workflow_id: $wf, run_number: $num,
      event: "pull_request_target", status: "completed", conclusion: $conclusion,
      head_sha: $head, name: $name, path: $path, run_attempt: 1,
      created_at: $created, run_started_at: $created,
      workflow_url: "https://api.github.com/repos/Verjson/verjson-agents/actions/workflows/\($wf)",
      referenced_workflows: $refs } + $over'
}
wf_runs() { printf '{"total_count":%s,"workflow_runs":[%s]}' "$(jq 'length' <<<"[$1]")" "$1"; }

# One attempt's job listing, served for `actions/runs/<id>/attempts/<n>/jobs`. A
# displaced job never acquired a runner (runner_id 0, empty name) and ran no step.
#   jobs_for run-id attempt job-json...
jobs_for() {
  local run_id="$1" attempt="$2"
  shift 2
  local body
  body="$(IFS=,; printf '%s' "$*")"
  printf '{"total_count":%s,"jobs":[%s]}\n' "$(jq 'length' <<<"[$body]")" "$body" \
    >"$JOBS_DIR/$run_id-$attempt.json"
}
job() { # id run-id name [steps-json] [runner-id] [overrides-json]
  jq -nc --arg head "$HEAD_SHA" --argjson id "$1" --argjson run "$2" --arg name "$3" \
    --argjson steps "${4:-[]}" --argjson runner "${5:-0}" --argjson over "${6:-{\}}" '
    { id: $id, run_id: $run, run_attempt: 1, head_sha: $head, name: $name,
      status: "completed", conclusion: "cancelled", steps: $steps,
      runner_id: $runner, runner_name: (if $runner == 0 then "" else "runner-\($runner)" end) } + $over'
}

GATE_REFS="[{\"path\":\"$CANONICAL_REF\",\"sha\":\"ee656fdb181eeb9ec9ae3765497930968cc544b7\"}]"
LIFECYCLE_REFS="[{\"path\":\"verJSON/verjson-agents/.github/workflows/gate-rearm.yml@19d4214bf5a0a7f97429a12f4b32856335a9acfb\",\"ref\":\"refs/heads/main\"},{\"path\":\"$CANONICAL_REF\"}]"
GATE_PATH=.github/workflows/gate-rearm.yml
LIFECYCLE_PATH=.github/workflows/ai-review-lifecycle-rearm.yml

green_required="$(check_run 1 'ci / build-test' success 500),$(check_run 2 'changelog / validate' success 500)"
# The superseder's own check suite: every gate-rearm job completed SUCCESS.
superseder_checks="$(check_run 41 'rearm / rearm / event-policy' success 602),$(check_run 42 'rearm / rearm / arm' success 602)"
cancelled_gate_check="$(check_run 31 'rearm / event-policy' cancelled 601)"

gate_run() { wf_run 9201 601 "$WF_GATE" 32 'gate re-arm' "$GATE_PATH" 2026-10-15T18:39:57Z cancelled "$GATE_REFS" "${1:-{\}}"; }
lifecycle_run() { wf_run 9202 602 "$WF_LIFECYCLE" 21 'AI review lifecycle re-arm' "$LIFECYCLE_PATH" 2026-10-15T18:40:16Z success "$LIFECYCLE_REFS" "${1:-{\}}"; }

run() {
  OUT="$("$script" "${REPO:-Verjson/verjson-agents}" 432 2>&1)"
  RC=$?
}
expect() { # label expected-rc needle
  if [ "$RC" -eq "$2" ] && grep -qF -- "$3" <<<"$OUT"; then pass "$1"
  else fail "$1 (rc=$RC, output: $OUT)"; fi
}
expect_true() { # label needle
  if [ "$RC" -eq 0 ] && grep -q '^true$' <<<"$OUT" && grep -qF -- "$2" <<<"$OUT"; then pass "$1"
  else fail "$1 (rc=$RC, output: $OUT)"; fi
}

# The default (#432-shaped) fixtures are built once: each case starts from them, and
# rebuilding them per case spent a third of the suite's time spawning jq.
default_check_runs="$(runs "$green_required,$cancelled_gate_check,$superseder_checks")"
default_runs="$(wf_runs "$(gate_run),$(lifecycle_run)")"
default_jobs="$(jobs_for 9201 1 "$(job 31 9201 'rearm / event-policy')" && cat "$JOBS_DIR/9201-1.json")"
reset_env() {
  unset FAIL_JOBS
  rm -f "$JOBS_DIR"/*.json
  export JOBS_REQUEST_LOG="$tmp/jobs-requests"
  : >"$JOBS_REQUEST_LOG"
  export PR_JSON_FIXTURE="{\"headRefOid\":\"$HEAD_SHA\",\"baseRefName\":\"main\"}"
  export RULES_FIXTURE="[{\"type\":\"required_status_checks\",\"parameters\":{\"required_status_checks\":[{\"context\":\"ci / build-test\",\"integration_id\":$APP_ID},{\"context\":\"changelog / validate\",\"integration_id\":$APP_ID}]}}]"
  export CHECK_RUNS_FIXTURE="$default_check_runs"
  export RUNS_FIXTURE="$default_runs"
  printf '%s\n' "$default_jobs" >"$JOBS_DIR/9201-1.json"
}

# --- the live #432 shape ------------------------------------------------------
reset_env; run
expect_true "a gate re-arm run displaced from ai-review-arm-<PR> by a later successful lifecycle re-arm run is set aside (verjson-agents#432)" \
  "check run 31, run 9201 displaced from concurrency group ai-review-arm-432 by run 9202"

# --- only displacement-shaped cancellations ----------------------------------
# A run displaced from a concurrency group never started: no step ran. A cancelled
# run whose job ran a step did work that was interrupted -- a manual cancel, a
# timeout -- and a later run of another workflow does not vouch for it. The runner
# id stays 0 here so ONLY the step guard can refuse it.
reset_env
jobs_for 9201 1 "$(job 31 9201 'rearm / event-policy' '[{"name":"Set up job","status":"completed","conclusion":"success","number":1}]')"
run
expect "a cancelled family run whose job started a step is still refused" 5 "rearm / event-policy=CANCELLED"

# A job that acquired a runner left the queue: it was cancelled while starting, not
# displaced while pending. No step is listed, so ONLY the runner guard can refuse it.
reset_env
jobs_for 9201 1 "$(job 31 9201 'rearm / event-policy' '[]' 1000071397)"
run
expect "a cancelled family run whose job acquired a runner is still refused" 5 "rearm / event-policy=CANCELLED"

# Every job of a displaced attempt was itself cancelled: a job that concluded any
# other way, or never finished, means the attempt was not simply dropped from the
# queue. Steps and runner stay empty so ONLY the job-conclusion guard can refuse.
for shape in '.conclusion = "success"' '.conclusion = "skipped"' '.status = "queued" | .conclusion = null'; do
  reset_env
  jobs_for 9201 1 "$(job 31 9201 'rearm / event-policy')" "$(job 32 9201 'rearm / arm' | jq -c "$shape")"
  run
  expect "a displaced-looking attempt with a job where '$shape' is still refused" 5 "rearm / event-policy=CANCELLED"
done

# The cancelled check run must BE one of the attempt's jobs (a job id is its check
# run id). Otherwise the job listing describes some other attempt -- a re-run whose
# record moved on -- and says nothing about this check.
reset_env
jobs_for 9201 1 "$(job 39 9201 'rearm / event-policy')"
run
expect "a cancelled check run absent from its run's job listing is still refused" 5 "rearm / event-policy=CANCELLED"

# --- the job inventory is validated like every other inventory -------------------
# Types are checked, never truthiness: a missing `steps` measures as length 0 and a
# missing runner id compares as null, so either would read as "never started" and
# release the cancellation. A job from another run, attempt, or head is not evidence
# about this one. Every shape faults rather than answering.
for mutation in \
  'del(.steps)' '.steps = "none"' '.steps = null' \
  'del(.runner_id)' '.runner_id = "0"' \
  'del(.id)' '.id = "31"' \
  '.run_id = 9999' '.run_attempt = 2' 'del(.run_attempt)' \
  ".head_sha = \"$OTHER_SHA\"" 'del(.head_sha)' \
  'del(.status)' '.status = 1' '.conclusion = 1' \
  '"a job"'; do
  reset_env
  jobs_for 9201 1 "$(job 31 9201 'rearm / event-policy' | jq -c "$mutation")"
  run
  expect "a job record mutated by '$mutation' faults instead of answering" 1 "job inventory of workflow run 9201"
done

# --- the declaring workflow is a member ----------------------------------------
# The organization ruleset runs gate-rearm.yml itself as a required workflow (`AI
# review authorization arm`): it declares the group rather than referencing it, and
# its workflow_url names a required workflow, which a repository cannot forge.
required_run() { # run-id suite created conclusion [overrides-json]
  wf_run "$1" "$2" "$WF_REQUIRED" 104 'AI review authorization arm' "$GATE_PATH" "$3" "$4" '[]' \
    "$(jq -nc --arg url "https://api.github.com/repos/Verjson/verjson-agents/actions/required_workflows/$WF_REQUIRED" \
         --argjson over "${5:-{\}}" '{workflow_url: $url} + $over')"
}
reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_required,$cancelled_gate_check,$(check_run 51 'event-policy' success 603),$(check_run 52 'arm' success 603)")"
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(required_run 9203 603 2026-10-15T18:39:58Z success)")"
run
expect_true "a later successful required-workflow run of the declaring workflow displaces a cancelled caller run" \
  "check run 31, run 9201 displaced from concurrency group ai-review-arm-432 by run 9203"

# In the declaring repository the workflow runs directly, not as a required workflow.
# Owner and repository compare case-insensitively, as GitHub resolves them.
direct_run() { required_run "$@" "{\"workflow_url\":\"https://api.github.com/repos/verJSON/.github/actions/workflows/$WF_REQUIRED\"}"; }
reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_required,$cancelled_gate_check,$(check_run 51 'event-policy' success 603)")"
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(direct_run 9203 603 2026-10-15T18:39:58Z success)")"
REPO=verJSON/.github run
expect_true "in the declaring repository its own direct run is a family member" \
  "run 9201 displaced from concurrency group ai-review-arm-432 by run 9203"

# --- the displacer must have done the family's work --------------------------
# A run concludes `success` with its guarded jobs SKIPPED (ADR 0226's attack A1), and
# a different workflow reports no same-named check to consult. So every check in the
# displacer's own suite must be a completed SUCCESS.
for shape in skipped neutral; do
  reset_env
  CHECK_RUNS_FIXTURE="$(runs "$green_required,$cancelled_gate_check,$(check_run 41 'rearm / rearm / event-policy' success 602),$(check_run 42 'rearm / rearm / arm' "$shape" 602)")"
  run
  expect "a successful family run whose own suite reports a ${shape^^} check displaces nothing" 5 "rearm / event-policy=CANCELLED"
done

# --- membership comes from the checked-in map, never from resemblance -----------
reset_env
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(lifecycle_run '{"referenced_workflows":[]}')")"
run
expect "a listed caller name and path that does not reference the declaring workflow is no member" 5 "rearm / event-policy=CANCELLED"

reset_env
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(lifecycle_run '{"name":"AI review lifecycle re-arm (copy)"}')")"
run
expect "a run referencing the declaring workflow under an unlisted name is no member" 5 "rearm / event-policy=CANCELLED"

reset_env
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(lifecycle_run '{"path":".github/workflows/lifecycle-copy.yml"}')")"
run
expect "a run under a listed name at an unlisted path is no member" 5 "rearm / event-policy=CANCELLED"

reset_env
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(lifecycle_run '{"referenced_workflows":[{"path":"Verjson/.github/.github/workflows/ai-review-merge.yml@ee656fdb181eeb9ec9ae3765497930968cc544b7"},{"path":"Verjson/other/.github/workflows/gate-rearm.yml@ee656fdb181eeb9ec9ae3765497930968cc544b7"}]}')")"
run
expect "a reference to another workflow, or to the same path in another repository, is no membership" 5 "rearm / event-policy=CANCELLED"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_required,$cancelled_gate_check,$(check_run 51 'event-policy' success 603)")"
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(direct_run 9203 603 2026-10-15T18:39:58Z success)")"
run
expect "outside the declaring repository a run named like the declaring workflow is no member" 5 "rearm / event-policy=CANCELLED"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_required,$cancelled_gate_check,$(check_run 51 'event-policy' success 603)")"
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(required_run 9203 603 2026-10-15T18:39:58Z success '{"workflow_id":1}')")"
run
expect "a required-workflow URL naming a different workflow is no membership" 5 "rearm / event-policy=CANCELLED"

# The required-workflow URL proves only that SOME required workflow ran at that path;
# another source repository's required workflow can share the path. The declared
# name is what identifies the declaring workflow.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_required,$cancelled_gate_check,$(check_run 51 'event-policy' success 603)")"
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(required_run 9203 603 2026-10-15T18:39:58Z success '{"name":"Another required workflow"}')")"
run
expect "a required workflow at the declaring path under another name is no member" 5 "rearm / event-policy=CANCELLED"

reset_env
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(lifecycle_run '{"event":"pull_request"}')")"
run
expect "a family run under a different event displaces nothing" 5 "rearm / event-policy=CANCELLED"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_required,$(check_run 31 'rearm / event-policy' success 601),$(check_run 41 'rearm / rearm / event-policy' cancelled 602)")"
RUNS_FIXTURE="$(wf_runs "$(gate_run '{"conclusion":"success"}'),$(lifecycle_run '{"conclusion":"cancelled"}')")"
jobs_for 9202 1 "$(job 41 9202 'rearm / rearm / event-policy')"
run
expect "an EARLIER-created successful family run displaces nothing" 5 "rearm / rearm / event-policy=CANCELLED"

reset_env
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(lifecycle_run '{"created_at":"2026-10-15T18:39:57Z"}')")"
run
expect "a family run created in the same second displaces nothing: ties are not later" 5 "rearm / event-policy=CANCELLED"

# A re-run attempt starts later than the run was created. A family run created
# between the two did not displace the re-run.
reset_env
RUNS_FIXTURE="$(wf_runs "$(gate_run '{"run_attempt":2,"run_started_at":"2026-10-15T18:48:35Z"}'),$(lifecycle_run)")"
jobs_for 9201 2 "$(job 31 9201 'rearm / event-policy' '[]' 0 '{"run_attempt":2}')"
run
expect "a family run created before a re-run attempt started does not displace that attempt" 5 "rearm / event-policy=CANCELLED"
if grep -qF -- "/actions/runs/9201/attempts/2/jobs" "$JOBS_REQUEST_LOG"; then
  pass "the job read is pinned to the attempt the run record reports"
else
  fail "the job read is pinned to the attempt the run record reports (requests: $(cat "$JOBS_REQUEST_LOG"))"
fi

for conclusion in failure cancelled skipped; do
  reset_env
  RUNS_FIXTURE="$(wf_runs "$(gate_run),$(lifecycle_run "{\"conclusion\":\"$conclusion\"}")")"
  jobs_for 9202 1 "$(job 41 9202 'rearm / rearm / event-policy' '[{"name":"Set up job"}]' 7)"
  run
  expect "a later family run that concluded $conclusion displaces nothing" 5 "rearm / event-policy=CANCELLED"
done

# Only the NEWEST later successful family run is consulted: an older full run is
# not cherry-picked over a newer one that skipped the work.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_required,$cancelled_gate_check,$superseder_checks,$(check_run 61 'rearm / rearm / event-policy' success 604),$(check_run 62 'rearm / rearm / arm' skipped 604)")"
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(lifecycle_run),$(wf_run 9204 604 "$WF_LIFECYCLE" 22 'AI review lifecycle re-arm' "$LIFECYCLE_PATH" 2026-10-15T18:41:00Z success "$LIFECYCLE_REFS")")"
run
expect "an older full family run is not cherry-picked over a newer one that skipped" 5 "rearm / event-policy=CANCELLED"

reset_env
CHECK_RUNS_FIXTURE="$(runs "$green_required,$cancelled_gate_check")"
run
expect "a successful family run that reports no check of its own displaces nothing" 5 "rearm / event-policy=CANCELLED"

# --- the job read ---------------------------------------------------------------
reset_env
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(wf_run 9202 602 "$WF_UNRELATED" 21 'CI' .github/workflows/ci.yml 2026-10-15T18:40:16Z success '[]')")"
run
expect "a later success of an unrelated workflow displaces nothing" 5 "rearm / event-policy=CANCELLED"
gate_only="$(wf_runs "$(wf_run 9201 601 "$WF_UNRELATED" 32 'CI' .github/workflows/ci.yml 2026-10-15T18:39:57Z cancelled '[]')")"
reset_env
RUNS_FIXTURE="$gate_only"
run
if [ "$RC" -eq 5 ] && [ ! -s "$JOBS_REQUEST_LOG" ]; then
  pass "no job listing is read for a cancelled run outside every family"
else
  fail "no job listing is read for a cancelled run outside every family (rc=$RC, requests: $(cat "$JOBS_REQUEST_LOG"))"
fi

reset_env
export FAIL_JOBS=1
run
expect "a refused job read faults and names the Actions grant" 1 "permissions.actions: read"

reset_env
printf '{"total_count":2,"jobs":[%s]}\n' "$(job 31 9201 'rearm / event-policy')" >"$JOBS_DIR/9201-1.json"
run
expect "a truncated job walk faults" 1 "job inventory of workflow run 9201 for Verjson/verjson-agents@$HEAD_SHA is incomplete or unattested"

reset_env
printf '{"total_count":1,"jobs":"rearm / event-policy"}\n' >"$JOBS_DIR/9201-1.json"
run
expect "a job page whose collection is not an array faults" 1 "carries a malformed page"

reset_env
jobs_for 9201 1
run
expect "an attempt with no jobs at all is not displacement evidence" 5 "rearm / event-policy=CANCELLED"

for attempt in '"1"' 0 1.5 null; do
  reset_env
  RUNS_FIXTURE="$(wf_runs "$(gate_run "{\"run_attempt\":$attempt}"),$(lifecycle_run)")"
  run
  expect "a cancelled family run whose attempt is $attempt faults" 1 "without a valid attempt"
done

reset_env
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(lifecycle_run '{"created_at":"yesterday"}')")"
run
expect "a family run with an unreadable creation time faults" 1 "failed to evaluate superseded cancellations"

reset_env
RUNS_FIXTURE="$(wf_runs "$(gate_run),$(lifecycle_run "{\"head_sha\":\"$OTHER_SHA\"}")")"
run
expect "a family run on another head faults rather than displacing" 1 "carries a malformed record"

# --- Gate A -----------------------------------------------------------------------
# Dismissal removes evidence and never manufactures it: a required context reported
# only by a displaced cancellation is then absent, and Gate A refuses it.
reset_env
CHECK_RUNS_FIXTURE="$(runs "$(check_run 1 'ci / build-test' success 500),$(check_run 31 'changelog / validate' cancelled 601),$superseder_checks")"
jobs_for 9201 1 "$(job 31 9201 'changelog / validate')"
run
expect "a required context reported only by a displaced cancellation is absent under Gate A" 3 "absent: changelog / validate"

if [ "$fails" -ne 0 ]; then
  printf '\n%d assertion(s) failed\n' "$fails" >&2
  exit 1
fi
printf '\nall assertions passed\n'
