#!/usr/bin/env bash
# Exercises Gate W in scripts/assert-mergeable-head.sh (ADR 0255, #1905) against a
# stubbed `gh`.
#
# A ruleset `workflows` rule blocks a merge unless the required workflow ran and
# succeeded on the head. The org-required `gate-rearm.yml` shares the
# `ai-review-arm-<PR>` concurrency group with the repository-local `gate re-arm`
# caller, so the local run can displace the required one from the pending slot.
# Gates A and C then set the displaced run aside (ADR 0226, 2026-10-15 amendment)
# and the assertion printed `true` while GitHub refused the merge. The live case is
# verJSON/verjson-identity-contracts#121 head 3b001575: required run 41703191233
# cancelled 2 s after creation; local run 41703192236 succeeded.
#
# Every negative case asserts the gate, the exit code, and the specific reason.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/assert-mergeable-head.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

HEAD_SHA=3b001575b09549217775577a20b9091be6de44a2
OTHER_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
APP_ID=15368
REPO=verJSON/verjson-identity-contracts
HEAD_BRANCH=renovate/actionssetup-node
RULESET_ID=20722935
WF_REQUIRED=332379840
WF_LOCAL=365716790
GATE_PATH=.github/workflows/gate-rearm.yml
PINNED_SHA=24db445fe410df7bfbd740d8766b2d9ad8e73915
LOCAL_REFS='[{"path":"Verjson/.github/.github/workflows/gate-rearm.yml@7ec2cf81fcdeda0209c3f4b2cb8774cb28b49de5","sha":"7ec2cf81fcdeda0209c3f4b2cb8774cb28b49de5"}]'

mkdir -p "$tmp/bin" "$tmp/jobs"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
set -uo pipefail
args="$*"
case "$args" in
  *"pr view "*"--json headRefOid,baseRefName"*) printf '%s\n' "$PR_JSON_FIXTURE" ;;
  *"/rules/branches/"*) printf '%s\n' "$RULES_FIXTURE" ;;
  *"/rulesets/"*)
    [ "${FAIL_RULESET:-0}" -eq 0 ] || { echo "gh: Resource not accessible by integration (HTTP 403)" >&2; exit 1; }
    printf '%s\n' "$RULESET_FIXTURE" ;;
  *"/check-runs?per_page"*) printf '%s\n' "$CHECK_RUNS_FIXTURE" ;;
  *"/commits/"*"/status"*) printf '{"total_count":0,"statuses":[]}\n' ;;
  *"/actions/runs?head_sha="*) printf '%s\n' "$RUNS_FIXTURE" ;;
  *"/actions/runs/"*"/attempts/"*"/jobs"*)
    run_id="${args#*/actions/runs/}"; run_id="${run_id%%/*}"
    attempt="${args#*/attempts/}"; attempt="${attempt%%/*}"
    fixture="$JOBS_DIR/$run_id-$attempt.json"
    [ -r "$fixture" ] || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
    cat "$fixture" ;;
  *"/annotations?per_page"*) printf '[]\n' ;;
  # The repository is promotion-enabled, so Gate R (ADR 0257) also reads its
  # independent-review receipt; every case here carries a valid one for the head.
  *"/pulls/121/reviews?per_page=100"*) printf '[%s]\n' "$RECEIPT_FIXTURE" ;;
  *"/pulls/121/reviews/"*) printf '%s\n' "$RECEIPT_FIXTURE" ;;
  *"/collaborators/independent-reviewer/permission"*) printf 'maintain\n' ;;
  *) echo "gh: unstubbed call: $args" >&2; exit 1 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH"
export JOBS_DIR="$tmp/jobs"
RECEIPT_FIXTURE="$(jq -nc --arg head "$HEAD_SHA" '{id: 82, state: "COMMENTED", commit_id: $head,
  user: {login: "independent-reviewer", type: "User"},
  body: "<!-- independent-review:v1 pr:121 head:\($head) verdict:approved -->"}')"
export RECEIPT_FIXTURE

check_run() { # id name conclusion check-suite-id
  printf '{"id":%s,"name":"%s","status":"completed","conclusion":"%s","app":{"id":%s},"check_suite":{"id":%s}}' \
    "$1" "$2" "$3" "$APP_ID" "$4"
}
check_runs() { printf '{"total_count":%s,"check_runs":[%s]}' "$(jq 'length' <<<"[$1]")" "$1"; }

# One workflow run as `actions/runs?head_sha=` reports it.
#   wf_run id suite workflow-id run-number name workflow-url created conclusion referenced [overrides]
wf_run() {
  jq -nc --arg head "$HEAD_SHA" --arg branch "$HEAD_BRANCH" --arg repo "$REPO" --arg path "$GATE_PATH" \
    --argjson id "$1" --argjson suite "$2" --argjson wf "$3" --argjson num "$4" \
    --arg name "$5" --arg url "$6" --arg created "$7" --arg conclusion "$8" \
    --argjson refs "$9" --argjson over "${10:-{\}}" '
    { id: $id, check_suite_id: $suite, workflow_id: $wf, run_number: $num,
      event: "pull_request_target", status: "completed", conclusion: $conclusion,
      head_sha: $head, name: $name, path: $path, run_attempt: 1,
      created_at: $created, run_started_at: $created,
      head_branch: $branch, head_repository: { full_name: $repo },
      pull_requests: [], workflow_url: $url, referenced_workflows: $refs } + $over'
}
wf_runs() { printf '{"total_count":%s,"workflow_runs":[%s]}' "$(jq 'length' <<<"[$1]")" "$1"; }

# The org-required arm, which GitHub names by a required-workflow URL the repository
# cannot forge, and the repository-local `gate re-arm` caller.
#   required_run id suite run-number created conclusion [overrides]
required_run() {
  wf_run "$1" "$2" "$WF_REQUIRED" "$3" 'AI review authorization arm' \
    "https://api.github.com/repos/$REPO/actions/required_workflows/$WF_REQUIRED" "$4" "$5" '[]' "${6:-{\}}"
}
local_run() { # id suite run-number created conclusion [overrides]
  wf_run "$1" "$2" "$WF_LOCAL" "$3" 'gate re-arm' \
    "https://api.github.com/repos/$REPO/actions/workflows/$WF_LOCAL" "$4" "$5" "$LOCAL_REFS" "${6:-{\}}"
}

workflow_entry="{\"repository_id\":1269388380,\"path\":\"$GATE_PATH\",\"sha\":\"$PINNED_SHA\",\"ref\":\"refs/heads/main\"}"
workflows_rule() { # entries-json [ruleset-id]
  printf '{"type":"workflows","parameters":{"do_not_enforce_on_create":true,"workflows":%s},"ruleset_source_type":"Organization","ruleset_source":"verJSON","ruleset_id":%s}' \
    "$1" "${2:-$RULESET_ID}"
}
ruleset() { # updated-at entries-json [ruleset-id]
  printf '{"id":%s,"name":"ai-authorization-arm-required","source_type":"Organization","source":"verJSON","enforcement":"active","updated_at":"%s","rules":[{"type":"workflows","parameters":{"do_not_enforce_on_create":true,"workflows":%s}}]}' \
    "${3:-$RULESET_ID}" "$1" "$2"
}
status_rule="{\"type\":\"required_status_checks\",\"parameters\":{\"required_status_checks\":[{\"context\":\"CI / test\",\"integration_id\":$APP_ID}]}}"

run() {
  OUT="$("$script" "$REPO" 121 2>&1)"
  RC=$?
}
expect() { # label expected-rc needle
  if [ "$RC" -eq "$2" ] && grep -qF -- "$3" <<<"$OUT"; then pass "$1"
  else fail "$1 (rc=$RC, output: $OUT)"; fi
}
# A reason whose evidence cannot be attributed gets the runbook's no-re-arm recovery,
# and nothing in the refusal advises any way to re-arm: a re-arm itself, a reopen, a
# new head, a re-run of the run, the interim recovery, or the sweep. The prohibition
# itself and the required workflow's path (gate-rearm.yml) are not advice.
expect_no_rearm() { # label reason
  local advice
  advice="${OUT//Do not re-arm./}"
  advice="${advice//"$GATE_PATH"/}"
  if [ "$RC" -eq 3 ] && grep -qF -- "[$2] Do not re-arm. The evidence cannot be attributed. Re-run the read-only assertion. If the refusal persists, escalate on #1905 with the message." <<<"$OUT" \
     && ! grep -qiE 're-?arm|reopen|push a new head|re-?run (this|the) (run|arm|required (arm|workflow))|re-?trigger|interim recovery|sweep' <<<"$advice"; then pass "$1"
  else fail "$1 (rc=$RC, output: $OUT)"; fi
}

# The live #121 shape. The required run was cancelled before it created a job, so it
# left no check run for Gates A or C to see, and the local caller succeeded.
local_checks="$(check_run 21 'rearm / event-policy' success 702),$(check_run 22 'rearm / arm' success 702)"
# The defaults are built once; each case starts from them.
default_rules="[$status_rule,$(workflows_rule "[$workflow_entry]")]"
default_ruleset="$(ruleset 2026-11-03T11:39:01.138-05:00 "[$workflow_entry]")"
default_check_runs="$(check_runs "$(check_run 1 'CI / test' success 500),$local_checks")"
default_runs="$(wf_runs "$(required_run 41703191233 701 198 2026-11-03T19:25:55Z cancelled),$(local_run 41703192236 702 113 2026-11-03T19:25:55Z success)")"
reset_env() {
  rm -f "$JOBS_DIR"/*.json
  unset FAIL_RULESET
  export PR_JSON_FIXTURE="{\"headRefOid\":\"$HEAD_SHA\",\"baseRefName\":\"main\",\"headRefName\":\"$HEAD_BRANCH\",\"headRepository\":{\"nameWithOwner\":\"$REPO\"}}"
  export RULES_FIXTURE="$default_rules"
  export RULESET_FIXTURE="$default_ruleset"
  export CHECK_RUNS_FIXTURE="$default_check_runs"
  export RUNS_FIXTURE="$default_runs"
}

# --- the #1905 shape ----------------------------------------------------------
reset_env; run
expect "a cancelled required run beside a successful local caller fails Gate W (verjson-identity-contracts#121)" 3 \
  "Gate W: $REPO#121 head $HEAD_SHA"
expect "the refusal names the cancelled required run and that nothing of the same required workflow succeeded after it" 3 \
  "cancelled: required run 41703191233 of $GATE_PATH (ruleset $RULESET_ID) was cancelled and no later run of the same required workflow on this head succeeded"
expect "a cancelled refusal carries the runbook's cancelled recovery" 3 \
  "[cancelled] Interim recovery (docs/arm-recovery-runbook.md#interim-recovery-close-and-reopen), or push a new head."

# The same shape where the cancelled required run DID leave a never-started check and
# the local caller was created after it: Gates A and C set that check aside as a
# family displacement (ADR 0226, 2026-10-15 amendment, #1905's exact gap). That
# set-aside never satisfies Gate W; only the same required workflow can.
reset_env
CHECK_RUNS_FIXTURE="$(check_runs "$(check_run 1 'CI / test' success 500),$(check_run 11 'event-policy' cancelled 701),$local_checks")"
RUNS_FIXTURE="$(wf_runs "$(required_run 41703191233 701 198 2026-11-03T19:25:55Z cancelled),$(local_run 41703192236 702 113 2026-11-03T19:25:56Z success)")"
printf '{"total_count":1,"jobs":[{"id":11,"run_id":41703191233,"run_attempt":1,"head_sha":"%s","name":"event-policy","status":"completed","conclusion":"cancelled","steps":[],"runner_id":0}]}\n' \
  "$HEAD_SHA" >"$JOBS_DIR/41703191233-1.json"
run
expect "the family set-aside dismissed the displaced required run's check before Gate W ran" 3 \
  "run 41703191233 displaced by run 41703192236 within declared concurrency family"
expect "a family-displaced required run never satisfies Gate W" 3 \
  "cancelled: required run 41703191233 of $GATE_PATH (ruleset $RULESET_ID) was cancelled"

# --- F-1R: a guard-skipped local run (ADR 0255 amendment 2026-11-06) -----------------
# ARB-G1-2026-11-06 precondition 5. A label re-arm run on this head is displaced while
# pending (its one job never started), and the required run then arms the head. The
# regenerated local `gate re-arm` caller, created after both, yields: `guard` SUCCESS,
# `rearm` SKIPPED, run `success`. It never entered the group, but it still matches the
# family's caller list, so it is the newest family run the ADR 0226 rule consults, and
# that rule sets a displaced run aside only for a SUCCESS-only suite. The recorded
# verdict is a Gate C refusal (exit 5) on the displaced check -- a liveness cost, never
# a pass -- while Gate W is satisfied by the required run.
reset_env
label_refs='[{"path":"Verjson/.github/.github/workflows/gate-rearm.yml@7ec2cf81fcdeda0209c3f4b2cb8774cb28b49de5"}]'
CHECK_RUNS_FIXTURE="$(check_runs "$(check_run 1 'CI / test' success 500),$(check_run 11 'event-policy' success 701),$(check_run 12 'arm' success 701),$(check_run 41 'rearm / event-policy' cancelled 704),$(check_run 51 'guard' success 705),$(check_run 52 'rearm' skipped 705)")"
RUNS_FIXTURE="$(wf_runs "$(wf_run 41703200001 704 365716791 9 'AI review label re-arm' "https://api.github.com/repos/$REPO/actions/workflows/365716791" 2026-11-06T19:26:10Z cancelled "$label_refs" '{"path":".github/workflows/ai-review-label-rearm.yml"}'),$(required_run 41703200002 701 199 2026-11-06T19:26:19Z success),$(local_run 41703200003 705 114 2026-11-06T19:26:20Z success)")"
printf '{"total_count":1,"jobs":[{"id":41,"run_id":41703200001,"run_attempt":1,"head_sha":"%s","name":"rearm / event-policy","status":"completed","conclusion":"cancelled","steps":[],"runner_id":0}]}\n' \
  "$HEAD_SHA" >"$JOBS_DIR/41703200001-1.json"
# Gate W runs before Gate C and would exit 3, so exit 5 also shows Gate W passed.
run
expect "a displaced family run is not set aside by a later guard-skipped local run, so Gate C refuses" 5 \
  "rearm / event-policy=CANCELLED"

# --- supersession: only a later run of the same required workflow ---------------------
# The runner-acquisition watchdog cancels an arm whose job never gets a runner (its own
# cancellation, an owner-ratified exception, ADR 0255). A later run of the SAME
# required workflow on the same head, here the close-and-reopen recovery, supersedes
# it, and ADR 0226's same-workflow rule sets the cancelled check aside for Gate C.
reset_env
CHECK_RUNS_FIXTURE="$(check_runs "$(check_run 1 'CI / test' success 500),$(check_run 11 'event-policy' success 701),$(check_run 12 'arm' cancelled 701),$(check_run 31 'event-policy' success 703),$(check_run 32 'arm' success 703)")"
RUNS_FIXTURE="$(wf_runs "$(required_run 41703191233 701 198 2026-11-03T19:25:55Z cancelled),$(required_run 41711448295 703 211 2026-11-03T20:19:05Z success)")"
printf '{"total_count":2,"jobs":[%s,%s]}\n' \
  "{\"id\":11,\"run_id\":41703191233,\"run_attempt\":1,\"head_sha\":\"$HEAD_SHA\",\"name\":\"event-policy\",\"status\":\"completed\",\"conclusion\":\"success\",\"steps\":[{\"name\":\"Set up job\"}],\"runner_id\":7}" \
  "{\"id\":12,\"run_id\":41703191233,\"run_attempt\":1,\"head_sha\":\"$HEAD_SHA\",\"name\":\"arm\",\"status\":\"completed\",\"conclusion\":\"cancelled\",\"steps\":[],\"runner_id\":0}" \
  >"$JOBS_DIR/41703191233-1.json"
run
if [ "$RC" -eq 0 ] && grep -q '^true$' <<<"$OUT"; then
  pass "a watchdog-cancelled required run replaced by a later successful run of the same required workflow passes"
else
  fail "a watchdog-cancelled required run replaced by a later successful run of the same required workflow passes (rc=$RC, output: $OUT)"
fi

# The newest run is the verdict: a later cancelled attempt (a re-arm that was itself
# cancelled) is not rescued by an older success.
reset_env
RUNS_FIXTURE="$(wf_runs "$(required_run 41703191233 701 198 2026-11-03T19:25:55Z success),$(required_run 41711448295 703 211 2026-11-03T20:19:05Z cancelled)")"
run
expect "an older successful required run does not rescue a newer cancelled one" 3 \
  "cancelled: required run 41711448295 of $GATE_PATH (ruleset $RULESET_ID) was cancelled"

# A required run that concluded `skipped` ran nothing; only `success` is evidence.
reset_env
RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T20:19:05Z skipped)")"
run
expect "a skipped required run fails Gate W" 3 \
  "not successful: required run 41711448295 of $GATE_PATH (ruleset $RULESET_ID) is completed/skipped"
expect "a not-successful refusal carries the runbook's fix-the-cause-first recovery" 3 \
  "[not successful] The required run failed or skipped. Read its log and fix the cause before re-arming."

# --- stale head and retargeting ----------------------------------------------------
# A required run recorded on another head (the head before a push) is no evidence for
# this one. The inventory admits only exact-head runs, so it faults rather than
# letting Gate W read it.
reset_env
RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T20:19:05Z success "{\"head_sha\":\"$OTHER_SHA\"}"),$(local_run 41703192236 702 113 2026-11-03T19:25:55Z success)")"
run
expect "a successful required run on the previous head faults instead of satisfying Gate W" 1 \
  "the workflow-run inventory for $REPO@$HEAD_SHA carries a malformed record"

# A retargeted pull request is judged by its CURRENT base's workflows rules. Here the
# new base requires a different workflow, and the head's arm run is not a run of it.
reset_env
PR_JSON_FIXTURE="$(jq -c '.baseRefName = "release/1.x"' <<<"$PR_JSON_FIXTURE")"
release_entry='{"repository_id":1269388380,"path":".github/workflows/release-arm.yml","sha":"2222222222222222222222222222222222222222","ref":"refs/heads/main"}'
RULES_FIXTURE="[$status_rule,$(workflows_rule "[$release_entry]")]"
RULESET_FIXTURE="$(ruleset 2026-11-03T11:39:01.138-05:00 "[$release_entry]")"
RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T20:19:05Z success)")"
run
expect "a retargeted pull request needs the required workflow of its new base" 3 \
  "absent: no run of required workflow .github/workflows/release-arm.yml (ruleset $RULESET_ID) is recorded on this head"

# --- missing -------------------------------------------------------------------
# No required run reached the head at all: an unenrolled event, or one GitHub never
# scheduled. The local caller's success is no substitute.
reset_env
RUNS_FIXTURE="$(wf_runs "$(local_run 41703192236 702 113 2026-11-03T19:25:55Z success)")"
run
expect "a head with no run of the required workflow fails Gate W as absent" 3 \
  "absent: no run of required workflow $GATE_PATH (ruleset $RULESET_ID) is recorded on this head"
expect "an absent refusal carries the runbook's absent recovery, enrollment first" 3 \
  "[absent] The head has no required run. Check that the repository is enrolled and the base ref carries the rule. Then use the interim recovery."

# Several refusals each carry their own recovery, in the order the refusals appear,
# and a reason refused for two required workflows states its recovery once.
reset_env
third_entry='{"repository_id":1269388380,"path":".github/workflows/third-arm.yml","sha":"3333333333333333333333333333333333333333","ref":"refs/heads/main"}'
RULES_FIXTURE="[$status_rule,$(workflows_rule "[$workflow_entry,$release_entry,$third_entry]")]"
RULESET_FIXTURE="$(ruleset 2026-11-03T11:39:01.138-05:00 "[$workflow_entry,$release_entry,$third_entry]")"
run
expect "a refusal for several reasons pairs each reason with its own recovery" 3 \
  "[cancelled] Interim recovery (docs/arm-recovery-runbook.md#interim-recovery-close-and-reopen), or push a new head. [absent] The head has no required run. Check that the repository is enrolled and the base ref carries the rule. Then use the interim recovery."
if [ "$(grep -oF '[absent]' <<<"$OUT" | wc -l)" -eq 1 ]; then
  pass "a reason refused for two required workflows states its recovery once"
else
  fail "a reason refused for two required workflows states its recovery once (output: $OUT)"
fi

# --- binding: provider fields only --------------------------------------------------
# A run binds to the rule only through fields GitHub records: the path, the event, a
# required-workflow URL naming the run's own workflow id (a repository cannot forge
# one), and this pull request's head branch and repository. The last two are the
# closest PR binding exposed (`pull_requests` is empty on these runs), so a run of a
# reused branch name or another fork sharing the SHA binds nothing. The run name is
# never consulted: a caller chooses it.
# --- required pull_request event ---------------------------------------------------
# Authn#356's required type-surface run is a GitHub-named required workflow on the
# exact head and ruleset, but uses `pull_request` rather than `pull_request_target`.
saved_repo=$REPO
saved_head_sha=$HEAD_SHA
saved_head_branch=$HEAD_BRANCH
saved_ruleset_id=$RULESET_ID
REPO=verJSON/verjson-authn
HEAD_SHA=b724569a9a4ec2f36004829917dfe4ae23c96507
HEAD_BRANCH=ci/355-canonical-adoption
RULESET_ID=21750617
type_surface_path=.github/workflows/authn-type-surface-required.yml
type_surface_entry="{\"repository_id\":1269388380,\"path\":\"$type_surface_path\",\"sha\":\"c78fcd4ef9ab83b6775ae2ede94ebce475b3943c\",\"ref\":\"refs/heads/main\"}"
reset_env
RULES_FIXTURE="[$status_rule,$(workflows_rule "[$type_surface_entry]" "$RULESET_ID")]"
RULESET_FIXTURE="$(ruleset 2026-11-10T00:40:41.641Z "[$type_surface_entry]")"
type_surface_run="$(wf_run 42731662927 712 346741024 74 'Authn required type surface' \
  "https://api.github.com/repos/$REPO/actions/required_workflows/346741024" \
  2026-11-10T00:52:55Z success '[]' \
  '{"event":"pull_request","path":".github/workflows/authn-type-surface-required.yml"}')"
RUNS_FIXTURE="$(wf_runs "$(required_run 41703191233 701 198 2026-11-03T19:25:55Z cancelled),$(local_run 41703192236 702 113 2026-11-03T19:25:55Z success),$type_surface_run")"
run
if [ "$RC" -eq 0 ] && grep -q '^true$' <<<"$OUT"; then
  pass "a fresh pull_request run with GitHub required-workflow provenance satisfies Gate W"
else
  fail "a fresh pull_request run with GitHub required-workflow provenance satisfies Gate W (rc=$RC, output: $OUT)"
fi
REPO=$saved_repo
HEAD_SHA=$saved_head_sha
HEAD_BRANCH=$saved_head_branch
RULESET_ID=$saved_ruleset_id

for field in \
  '{"head_branch":"renovate/reused-branch"}' \
  '{"head_repository":{"full_name":"someone/verjson-identity-contracts"}}' \
  '{"head_repository":null}' \
  '{"event":"push"}' \
  '{"event":"merge_group"}' \
  "{\"workflow_url\":\"https://api.github.com/repos/$REPO/actions/required_workflows/1$WF_REQUIRED\"}"; do
  reset_env
  RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T20:19:05Z success "$field")")"
  run
  expect "a successful required-path run carrying $field is unbound and fails Gate W" 3 \
    "unbound: run 41711448295 at $GATE_PATH is not bound to ruleset $RULESET_ID's required workflow for this pull request"
  expect_no_rearm "an unbound refusal carrying $field never advises a re-arm" unbound
done

# A run whose own creation time cannot be read cannot be ordered against the rule
# change; that is not the same finding as a run known to predate it.
for created in null '"yesterday"' '"2026-11-03 20:19:05Z"'; do
  reset_env
  RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T20:19:05Z success "{\"created_at\":$created}")")"
  run
  expect "a bound required run whose creation time reads $created fails Gate W as undated" 3 \
    "undated: run 41711448295 of required workflow $GATE_PATH reports no readable creation time"
  expect_no_rearm "an undated refusal for a run created at $created never advises a re-arm" undated
done

# --- ambiguity -----------------------------------------------------------------------
# Two required workflows at one path (another source repository's, or a re-registered
# one) cannot be told apart by any field the rule carries, so neither is evidence.
reset_env
other_required="$(wf_run 41711448400 704 332379999 5 'AI review authorization arm' \
  "https://api.github.com/repos/$REPO/actions/required_workflows/332379999" 2026-11-03T20:19:06Z success '[]')"
RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T20:19:05Z success),$other_required")"
run
expect "bound runs of two required-workflow ids at the rule's path fail Gate W as ambiguous" 3 \
  "ambiguous: runs of 2 required workflows (332379840, 332379999) at $GATE_PATH bind to ruleset $RULESET_ID"
expect_no_rearm "an ambiguous refusal never advises a re-arm" ambiguous

# A run carries its path but not its source repository, so two required workflows the
# rules name at one path could each be satisfied by the other's run.
reset_env
second_entry="{\"repository_id\":999,\"path\":\"$GATE_PATH\",\"sha\":\"$PINNED_SHA\",\"ref\":\"refs/heads/main\"}"
RULES_FIXTURE="[$status_rule,$(workflows_rule "[$workflow_entry,$second_entry]")]"
RULESET_FIXTURE="$(ruleset 2026-11-03T11:39:01.138-05:00 "[$workflow_entry,$second_entry]")"
RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T20:19:05Z success)")"
run
expect "two required workflows the rules name at one path fail Gate W as ambiguous" 3 \
  "Gate W: the workflows rules of main name 2 required workflows at $GATE_PATH"
expect_no_rearm "a duplicate-paths refusal never advises a re-arm" "duplicate paths"

# Freshness alone narrows ambiguity: a stale run of another required-workflow id (the
# registration before a re-registration) beside a fresh successful run of the current
# one is no candidate for the current rule, so the head passes.
reset_env
stale_other="$(wf_run 41600000002 705 332379999 4 'AI review authorization arm' \
  "https://api.github.com/repos/$REPO/actions/required_workflows/332379999" 2026-11-03T16:00:00Z success '[]')"
RUNS_FIXTURE="$(wf_runs "$stale_other,$(required_run 41711448295 703 211 2026-11-03T20:19:05Z success)")"
run
if [ "$RC" -eq 0 ] && grep -q '^true$' <<<"$OUT"; then
  pass "a stale run of another required-workflow id beside a fresh success of the current one is not ambiguous"
else
  fail "a stale run of another required-workflow id beside a fresh success of the current one is not ambiguous (rc=$RC, output: $OUT)"
fi

# --- rule change ------------------------------------------------------------------
# No provider field binds a run to the rule's pinned SHA (a required run reports
# referenced_workflows []), so a run created before the ruleset last changed is stale
# under the owner's timing rule (ARB-F-RUN-F-2026-11-03). The ruleset reports its
# change time with an offset; 11:39:01.138-05:00 is 16:39:01.138Z.
reset_env
RUNS_FIXTURE="$(wf_runs "$(required_run 41600000001 701 190 2026-11-03T16:00:00Z success)")"
run
expect "a successful required run created before the ruleset last changed fails Gate W as stale" 3 \
  "stale: every run of required workflow $GATE_PATH (ruleset $RULESET_ID) on this head was created at or before the ruleset last changed at 2026-11-03T11:39:01.138-05:00"
expect "a stale refusal carries the runbook's stale recovery" 3 \
  "[stale] Re-arm sweep (docs/arm-recovery-runbook.md#re-arm-sweep-after-a-ruleset-edit): one close-and-reopen under the interim rules."

# Run times are second-granular and the change time is not: a run created in the
# change's own second may precede it, so it is stale too. The UTC spelling the
# endpoint also returns is read the same way.
reset_env
RULESET_FIXTURE="$(ruleset 2026-11-03T16:39:01.138Z "[$workflow_entry]")"
RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T16:39:01Z success)")"
run
expect "a required run created in the same second as the ruleset change is stale" 3 \
  "stale: every run of required workflow $GATE_PATH (ruleset $RULESET_ID) on this head was created at or before the ruleset last changed at 2026-11-03T16:39:01.138Z"

# With no fractional part the change and the run share the exact instant. The rule is
# strictly-after, so a run created at the change instant itself is stale.
reset_env
RULESET_FIXTURE="$(ruleset 2026-11-03T16:39:01Z "[$workflow_entry]")"
RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T16:39:01Z success)")"
run
expect "a required run created at the exact instant of a whole-second ruleset change is stale" 3 \
  "stale: every run of required workflow $GATE_PATH (ruleset $RULESET_ID) on this head was created at or before the ruleset last changed at 2026-11-03T16:39:01Z"

# Where the change time cannot be read, staleness cannot be ruled out, so nothing on
# the head is evidence. jq orders every number above null, so an unread time would
# otherwise make every run look newer than the change.
for updated in '' 'yesterday' '2026-11-03 16:39:01Z' '2026-11-03T16:39:01+0500'; do
  reset_env
  RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T20:19:05Z success)")"
  RULESET_FIXTURE="$(ruleset "$updated" "[$workflow_entry]")"
  run
  expect "a ruleset whose change time reads '$updated' fails Gate W as undated" 3 \
    "undated: ruleset $RULESET_ID reports no readable change time"
  expect_no_rearm "an undated refusal for a ruleset changed at '$updated' never advises a re-arm" undated
done
reset_env
RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T20:19:05Z success)")"
RULESET_FIXTURE="$(ruleset x "[$workflow_entry]" | jq -c 'del(.updated_at)')"
run
expect "a ruleset that reports no change time at all fails Gate W as undated" 3 \
  "undated: ruleset $RULESET_ID reports no readable change time"

# The change time dates the rule only if it is the rule the branch read returned. A
# rotation between the two reads (a new pinned SHA) leaves that pairing unproven.
reset_env
RUNS_FIXTURE="$(wf_runs "$(required_run 41711448295 703 211 2026-11-03T20:19:05Z success)")"
RULESET_FIXTURE="$(ruleset 2026-11-03T11:39:01.138-05:00 "[$(jq -c '.sha = "1111111111111111111111111111111111111111"' <<<"$workflow_entry")]")"
run
expect "a ruleset read that no longer carries the branch rule's required workflow fails Gate W" 3 \
  "changed: ruleset $RULESET_ID no longer reports the required workflow $GATE_PATH@$PINNED_SHA that the branch rules returned"
expect_no_rearm "a changed refusal never advises a re-arm" changed

# --- a workflows rule Gate W cannot read ------------------------------------------
# A shape change in the rule (a renamed key, a missing ruleset id) must not read as a
# ref with no required workflow: that is the vacuous pass, and it is reserved for a
# ref whose rules carry no workflows rule at all.
for mutation in 'del(.ruleset_id)' '.ruleset_id = "20722935"' '.ruleset_id = 0' \
                'del(.parameters.workflows)' '.parameters.workflows = {}' '.parameters.workflows = []' \
                '.parameters.workflows[0] |= del(.path)' '.parameters.workflows[0].path = ""' \
                '.parameters.workflows[0] = "gate-rearm.yml"'; do
  reset_env
  RULES_FIXTURE="[$status_rule,$(workflows_rule "[$workflow_entry]" | jq -c "$mutation")]"
  run
  expect "a workflows rule mutated by '$mutation' fails Gate W as unreadable" 3 \
    "Gate W: the workflows rules of main carry a rule it cannot read"
  expect_no_rearm "an unreadable-rule refusal for '$mutation' never advises a re-arm" "unreadable rule"
done

# --- unreadable -------------------------------------------------------------------
reset_env
export FAIL_RULESET=1
run
expect "a refused ruleset read faults and names the repository Metadata grant" 1 \
  "Gate W failed to read ruleset $RULESET_ID of $REPO, which dates its workflows rule; if authorization failed, this endpoint needs fine-grained Metadata (read)"

# A body that is not the ruleset requested (a proxy page, a cached error, a different
# id) dates nothing.
for body in "$(ruleset 2026-11-03T11:39:01.138-05:00 "[$workflow_entry]" 1)" \
            "$(ruleset 2026-11-03T11:39:01.138-05:00 "[$workflow_entry]" "\"$RULESET_ID\"")" \
            '[]' 'null' '"ruleset"'; do
  reset_env
  RULESET_FIXTURE="$body"
  run
  expect "a ruleset response '${body:0:40}' that is not ruleset $RULESET_ID faults" 1 \
    "the response for ruleset $RULESET_ID of $REPO is not that ruleset"
done

# --- a reason with no recorded recovery -------------------------------------------
# Exit 1 tells a caller to fix access and retry. A refusal whose reason the table
# lacks is still a Gate W refusal, so it keeps exit 3 and its reasons, and escalates
# instead of guessing a recovery. Exercised on a copy whose `cancelled` key is renamed.
renamed="$tmp/assert-mergeable-head.renamed-key.sh"
sed 's/"cancelled":"Interim recovery/"cancelled-renamed":"Interim recovery/' "$script" >"$renamed"
chmod +x "$renamed"
if cmp -s "$script" "$renamed"; then
  fail "the renamed-key copy differs from the script (the cancelled key was not found)"
else
  reset_env
  OUT="$("$renamed" "$REPO" 121 2>&1)"; RC=$?
  expect "a refusal whose reason has no recorded recovery keeps exit 3 and its reasons" 3 \
    "Gate W: $REPO#121 head $HEAD_SHA does not carry a successful run of every required workflow of main — cancelled: required run 41703191233"
  expect "a refusal whose reason has no recorded recovery escalates instead of guessing one" 3 \
    "[cancelled] no runbook recovery recorded for this reason; escalate on #1905"
fi

# --- recovery agrees with the runbook ---------------------------------------------
# docs/arm-recovery-runbook.md's "Recovery by refusal reason" table is the source of
# truth for what each refusal tells its reader to do. The script's table must carry
# exactly the same reasons with the same recovery, the runbook's in-page links
# rendered as "label (docs/arm-recovery-runbook.md#anchor)".
script_remedies="$(sed -n "s/^readonly GATE_W_REMEDIES='\(.*\)'\$/\1/p" "$script" | jq -S .)"
runbook_remedies="$(awk '/^## Recovery by refusal reason$/ { section = 1; next }
                          section && /^## / { exit }
                          section && /^\| / && !/^\| (---|Gate W reason) / { print }' \
                      "$here/../docs/arm-recovery-runbook.md" \
  | jq -RnS '[ inputs | capture("^\\| (?<reasons>.+) \\| (?<recovery>.+) \\|$")
              | (.recovery | gsub("\\[(?<label>[^]]+)\\]\\(#(?<anchor>[^)]+)\\)";
                                  "\(.label) (docs/arm-recovery-runbook.md#\(.anchor))")) as $recovery
              | .reasons | split(", ")[] | gsub("`"; "") | { (.): $recovery } ] | add')"
if [ -n "$script_remedies" ] && [ "$script_remedies" != null ] && [ "$script_remedies" = "$runbook_remedies" ]; then
  pass "every Gate W refusal reason carries exactly the runbook's recovery for it"
else
  fail "every Gate W refusal reason carries exactly the runbook's recovery for it ($(diff <(printf '%s\n' "$script_remedies") <(printf '%s\n' "$runbook_remedies") | tr '\n' ' '))"
fi

if [ "$fails" -ne 0 ]; then
  printf '\n%d assertion(s) failed\n' "$fails" >&2
  exit 1
fi
printf '\nall assertions passed\n'
