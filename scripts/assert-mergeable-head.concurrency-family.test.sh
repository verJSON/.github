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
APP_ID=15368
WF_GATE=381766279
WF_LIFECYCLE=381766276
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
  OUT="$("$script" Verjson/verjson-agents 432 2>&1)"
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

reset_env() {
  unset FAIL_JOBS
  rm -f "$JOBS_DIR"/*.json
  export JOBS_REQUEST_LOG="$tmp/jobs-requests"
  : >"$JOBS_REQUEST_LOG"
  export PR_JSON_FIXTURE
  PR_JSON_FIXTURE="$(printf '{"headRefOid":"%s","baseRefName":"main"}' "$HEAD_SHA")"
  export RULES_FIXTURE="[{\"type\":\"required_status_checks\",\"parameters\":{\"required_status_checks\":[{\"context\":\"ci / build-test\",\"integration_id\":$APP_ID},{\"context\":\"changelog / validate\",\"integration_id\":$APP_ID}]}}]"
  export CHECK_RUNS_FIXTURE
  CHECK_RUNS_FIXTURE="$(runs "$green_required,$cancelled_gate_check,$superseder_checks")"
  export RUNS_FIXTURE
  RUNS_FIXTURE="$(wf_runs "$(gate_run),$(lifecycle_run)")"
  jobs_for 9201 1 "$(job 31 9201 'rearm / event-policy')"
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

if [ "$fails" -ne 0 ]; then
  printf '\n%d assertion(s) failed\n' "$fails" >&2
  exit 1
fi
printf '\nall assertions passed\n'
