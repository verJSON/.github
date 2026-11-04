#!/usr/bin/env bash
# Unit tests for scripts/fleet-watchdog.sh against a stubbed `gh`.
#
# The watchdog cancels other people's CI runs, so the tests that matter are the
# ones proving it does NOT: an unreadable fleet, spare capacity, an empty queue,
# and young jobs must all leave everything alone. Plain bash + jq, no framework.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/fleet-watchdog.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$script" ] || { echo "FAIL - watchdog script not found"; exit 1; }

mkdir -p "$tmp/bin"
# Stub `gh`. Fixtures are files so each case can reshape one facet at a time.
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
# Emulate the real `gh api --jq`: the filter is applied client-side, so a stub
# that ignores it hands the script raw JSON where it expects a number or a TSV
# stream — every guard then reads as "0 / nothing found" and the tests pass for
# the wrong reason.
filter=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  [ "${args[$i]}" = "--jq" ] && filter="${args[$((i + 1))]}"
done
emit() {
  if [ -n "$filter" ]; then jq -r "$filter" "$1"; else cat "$1"; fi
}
case "$*" in
  *"actions/runners"*)
    [ "${RUNNERS_FAIL:-false}" = true ] && exit 1
    emit "$RUNNERS_FILE"; exit 0 ;;
  *"/cancel"*)
    echo "CANCELLED $*" >>"$ACTIONLOG"; exit 0 ;;
  *"orgs/"*"/repos"*)
    printf '%s\n' "${REPOS:-alpha}"; exit 0 ;;
  *"status=queued"*)   emit "$QUEUED_FILE"; exit 0 ;;
  *"status=in_progress"*) emit "$INPROGRESS_FILE"; exit 0 ;;
  # The queued run and the in-progress run have separate job fixtures, so a case
  # can make the queue want hosted capacity while the candidate holds the pool.
  *"/runs/${QUEUED_RUN_ID:-9}/jobs"*) emit "$QUEUED_JOBS_FILE"; exit 0 ;;
  *"/jobs"*)           emit "$JOBS_FILE"; exit 0 ;;
esac
exit 0
GH
chmod +x "$tmp/bin/gh"

# jq is invoked with --jq by the real gh; our stub ignores it, so the script's
# own `jq` calls do the filtering. Provide well-formed payloads.
busy_pool()  { printf '{"runners":[{"status":"online","busy":true,"labels":[{"name":"general"}]},{"status":"online","busy":true,"labels":[{"name":"general"}]}]}\n'; }
idle_pool()  { printf '{"runners":[{"status":"online","busy":false,"labels":[{"name":"general"}]}]}\n'; }
# `gh api --paginate` emits one document per page. Two busy pages must aggregate
# to one saturated pool, not to two per-page counts (#355).
busy_pool_2pages() {
  printf '{"runners":[{"status":"online","busy":true,"labels":[{"name":"general"}]}]}\n'
  printf '{"runners":[{"status":"online","busy":true,"labels":[{"name":"general"}]}]}\n'
}
# Same two pages, but the second one holds the only idle runner. A per-page
# reader sees page 1 and cancels; a correct reader sees spare capacity.
mixed_pool_2pages() {
  printf '{"runners":[{"status":"online","busy":true,"labels":[{"name":"general"}]}]}\n'
  printf '{"runners":[{"status":"online","busy":false,"labels":[{"name":"general"}]}]}\n'
}
# A page without its `.runners` array is an unusable response, not an empty pool.
malformed_pool_2pages() {
  printf '{"runners":[{"status":"online","busy":true,"labels":[{"name":"general"}]}]}\n'
  printf '{"message":"Bad credentials"}\n'
}

# A subshell, so a `WATCHDOG_DRY_RUN=true run_watchdog` prefix cannot leak into
# the cases that follow — bash keeps prefix assignments on a function call, which
# would silently disarm every later "it SHOULD cancel" assertion.
run_watchdog() (
  export PATH="$tmp/bin:$PATH"
  export WATCHDOG_ORG=TestOrg WATCHDOG_MIN_AGE_MINUTES=35 WATCHDOG_MIN_POLL_MINUTES=10
  export WATCHDOG_DRY_RUN="${WATCHDOG_DRY_RUN:-false}"
  export WATCHDOG_POLL_STEP_DRY_RUN="${WATCHDOG_POLL_STEP_DRY_RUN:-false}"
  export WATCHDOG_POLL_WORKFLOWS="${WATCHDOG_POLL_WORKFLOWS:-AI review + auto-merge|AI privileged merge}"
  export WATCHDOG_POLL_STEPS="${WATCHDOG_POLL_STEPS:-AI review + auto-merge=Wait once for the rest of CI to be green}"
  export ACTIONLOG="$tmp/act.log"; : >"$ACTIONLOG"
  bash "$script" >"$tmp/out.txt" 2>&1
  printf 'rc=%s' "$?"
)
cancelled() { grep -qF CANCELLED "$tmp/act.log"; }

# Fixtures: one stale poll job, one queued run.
old_iso="$(date -u -d '90 minutes ago' +%Y-%m-%dT%H:%M:%SZ)"
mid_iso="$(date -u -d '20 minutes ago' +%Y-%m-%dT%H:%M:%SZ)"
new_iso="$(date -u -d '2 minutes ago'  +%Y-%m-%dT%H:%M:%SZ)"

# The gate names the step that polls; the watchdog reads that step's state
# instead of inferring "waiting" from age (#343).
POLL_STEP='Wait once for the rest of CI to be green'
# A job's `.labels` is its requested `runs-on`. Only a job that actually holds a
# runner from the saturated pool is worth preempting, so every fixture standing
# for a pool-holding job carries the pool's labels.
POOL_LABELS='"self-hosted","general"'
poll_job() { # $1 = started_at — a workflow with no separable poll step
  printf '{"jobs":[{"status":"in_progress","started_at":"%s","labels":[%s]}]}\n' \
    "$1" "$POOL_LABELS"
}
gate_job() { # $1 = job started_at, $2 = poll step status, $3 = step started_at (default $1)
  printf '{"jobs":[{"status":"in_progress","started_at":"%s","labels":[%s],"steps":[{"name":"%s","status":"%s","started_at":"%s"}]}]}\n' \
    "$1" "$POOL_LABELS" "$POLL_STEP" "$2" "${3:-$1}"
}

queued_pool_job()   { printf '{"jobs":[{"status":"queued","labels":[%s]}]}\n' "$POOL_LABELS"; }
queued_hosted_job() { printf '{"jobs":[{"status":"queued","labels":["ubuntu-24.04"]}]}\n'; }

export RUNNERS_FILE="$tmp/runners.json" QUEUED_FILE="$tmp/queued.json"
export INPROGRESS_FILE="$tmp/inprog.json" JOBS_FILE="$tmp/jobs.json"
export QUEUED_JOBS_FILE="$tmp/queued-jobs.json"
printf '{"workflow_runs":[{"id":1,"name":"AI privileged merge"}]}\n' >"$INPROGRESS_FILE"
printf '{"workflow_runs":[{"id":9,"name":"CI"}]}\n' >"$QUEUED_FILE"
queued_pool_job >"$QUEUED_JOBS_FILE"
poll_job "$old_iso" >"$JOBS_FILE"
busy_pool >"$RUNNERS_FILE"

# --- the one case where it SHOULD act ---------------------------------------
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && cancelled \
  && pass "preempts a stale poll job when the pool is full and work is queued" \
  || { fail "did not preempt a stale poll job ($rc)"; sed 's/^/diag - /' "$tmp/out.txt"; }

# --- every case where it must NOT ------------------------------------------
idle_pool >"$RUNNERS_FILE"
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && ! cancelled && pass "spare capacity — cancels nothing" || fail "cancelled despite an idle runner ($rc)"
busy_pool >"$RUNNERS_FILE"

printf '{"workflow_runs":[]}\n' >"$QUEUED_FILE"
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && ! cancelled && pass "empty queue — a long poll harms nobody, cancels nothing" || fail "cancelled with nothing queued ($rc)"
printf '{"workflow_runs":[{"id":9,"name":"CI"}]}\n' >"$QUEUED_FILE"

poll_job "$new_iso" >"$JOBS_FILE"
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && ! cancelled && pass "a young poll job is left alone" || fail "cancelled a job under the age threshold ($rc)"
poll_job "$old_iso" >"$JOBS_FILE"

# A CI run must never be a candidate, however long it has been going.
printf '{"workflow_runs":[{"id":1,"name":"CI"}]}\n' >"$INPROGRESS_FILE"
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && ! cancelled && pass "never preempts a non-poll workflow (CI)" || fail "cancelled a CI run — it would lose real work ($rc)"
printf '{"workflow_runs":[{"id":1,"name":"AI privileged merge"}]}\n' >"$INPROGRESS_FILE"

# Unknown fleet state is inconclusive, and inconclusive must not authorise a
# cancel. Exit 2 (fault), not 0 (all clear).
export RUNNERS_FAIL=true
rc="$(run_watchdog)"
{ [ "$rc" = "rc=2" ] && ! cancelled; } \
  && pass "an unreadable runner list is a fault, never a licence to cancel" \
  || fail "unreadable fleet did not fail closed ($rc)"
unset RUNNERS_FAIL; export RUNNERS_FAIL=false

# Dry run reports without acting.
rc="$(WATCHDOG_DRY_RUN=true run_watchdog)"
{ [ "$rc" = rc=0 ] && ! cancelled && grep -q 'DRY RUN' "$tmp/out.txt"; } \
  && pass "dry run reports candidates without cancelling" \
  || fail "dry run either cancelled or reported nothing ($rc)"

# --- #342: the arm/disarm switch has no implicit default --------------------
# #336 shipped armed because two layers each carried a default and the running
# log described the other one. Pin the DEFAULT, not only the guarded value.
run_bare() { # run without WATCHDOG_DRY_RUN in the environment at all
  ( export PATH="$tmp/bin:$PATH"
    export WATCHDOG_ORG=TestOrg WATCHDOG_MIN_AGE_MINUTES=35 WATCHDOG_MIN_POLL_MINUTES=10
    export ACTIONLOG="$tmp/act.log"; : >"$ACTIONLOG"
    unset WATCHDOG_DRY_RUN
    [ -n "${1:-}" ] && export WATCHDOG_DRY_RUN="$1"
    bash "$script" >"$tmp/out.txt" 2>&1
    printf 'rc=%s' "$?" )
}

rc="$(run_bare)"
{ [ "$rc" = "rc=2" ] && ! cancelled; } \
  && pass "an unset WATCHDOG_DRY_RUN is a fault, not an implicit licence to cancel (#342)" \
  || fail "unset WATCHDOG_DRY_RUN did not fail closed ($rc)"

rc="$(run_bare yes)"
{ [ "$rc" = "rc=2" ] && ! cancelled; } \
  && pass "an unparseable WATCHDOG_DRY_RUN is a fault, never treated as armed (#342)" \
  || fail "WATCHDOG_DRY_RUN=yes did not fail closed ($rc)"

rc="$(run_bare false)"
{ [ "$rc" = "rc=0" ] && ! cancelled; } \
  && pass "an explicit arm cannot cancel after the production allowlist is retired" \
  || fail "an inert production watchdog still cancelled a run ($rc)"

# --- #343: preemptability is a state, not an age ----------------------------
# The AI lane's own poll window is 30 minutes, shorter than MIN_AGE_MINUTES, so
# an age rule could only ever reach gates that had already left `ci_wait` and
# started spending model-review budget.
printf '{"workflow_runs":[{"id":1,"name":"AI review + auto-merge"}]}\n' >"$INPROGRESS_FILE"

gate_job "$mid_iso" in_progress >"$JOBS_FILE"
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && cancelled \
  && pass "a gate still inside ci_wait at 20 minutes is a candidate (#343)" \
  || { fail "a provably polling gate was not preempted ($rc)"; sed 's/^/diag - /' "$tmp/out.txt"; }

gate_job "$old_iso" completed >"$JOBS_FILE"
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && ! cancelled \
  && pass "a gate past ci_wait is never preempted, however old — that is model review (#343)" \
  || fail "cancelled a gate that had finished polling; that discards paid review work ($rc)"

gate_job "$new_iso" in_progress >"$JOBS_FILE"
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && ! cancelled \
  && pass "a gate that has only just started polling is under the floor" \
  || fail "cancelled a gate below WATCHDOG_MIN_POLL_MINUTES ($rc)"

# The floor guards POLL age, not job age. A job that spent 88 minutes on
# checkout and 2 minutes polling has not been in anyone's way for long enough.
gate_job "$old_iso" in_progress "$new_iso" >"$JOBS_FILE"
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && ! cancelled \
  && pass "an old job whose poll step just started is under the floor" \
  || { fail "the floor read job age instead of poll-step age ($rc)"; sed 's/^/diag - /' "$tmp/out.txt"; }

# A step whose start time is missing cannot be aged, and unmeasurable is not a
# licence to cancel.
printf '{"jobs":[{"status":"in_progress","started_at":"%s","labels":[%s],"steps":[{"name":"%s","status":"in_progress"}]}]}\n' \
  "$old_iso" "$POOL_LABELS" "$POLL_STEP" >"$JOBS_FILE"
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && ! cancelled \
  && pass "a poll step with no start time is left alone, not cancelled on job age" \
  || fail "an unmeasurable poll step authorised a cancel ($rc)"

# An unrecognised job shape must not be read as "polling".
printf '{"jobs":[{"status":"in_progress","started_at":"%s","labels":[%s],"steps":[]}]}\n' "$old_iso" "$POOL_LABELS" >"$JOBS_FILE"
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && ! cancelled \
  && pass "a gate whose poll step cannot be found is left alone, not cancelled on age" \
  || fail "an unreadable step list authorised a cancel ($rc)"

printf '{"workflow_runs":[{"id":1,"name":"AI privileged merge"}]}\n' >"$INPROGRESS_FILE"
poll_job "$old_iso" >"$JOBS_FILE"
rc="$(run_watchdog)"
[ "$rc" = rc=0 ] && cancelled \
  && pass "a workflow with no separable poll step keeps the age rule" \
  || fail "the age fallback stopped selecting AI privileged merge ($rc)"

# --- a candidate must occupy the pool the jam is on -------------------------
# `gate` routes on `CI_RUNNER_OVERFLOW` (or `_FASTLANE` for public targets)
# and both are `["ubuntu-24.04"]` today, so every `gate` in the org is on hosted
# capacity. Cancelling one frees ZERO self-hosted runners: the jam survives, and
# the PR pays for a full re-run of a job that was never holding the resource.
hosted_gate_job() { # $1 = started_at, $2 = poll step status
  printf '{"jobs":[{"status":"in_progress","started_at":"%s","labels":["ubuntu-24.04"],"steps":[{"name":"%s","status":"%s","started_at":"%s"}]}]}\n' \
    "$1" "$POLL_STEP" "$2" "$1"
}
printf '{"workflow_runs":[{"id":1,"name":"AI review + auto-merge"}]}\n' >"$INPROGRESS_FILE"
hosted_gate_job "$mid_iso" in_progress >"$JOBS_FILE"
rc="$(run_watchdog)"
{ [ "$rc" = rc=0 ] && ! cancelled && grep -q 'stale_poll_jobs=0' "$tmp/out.txt"; } \
  && pass "a hosted gate is not a candidate — cancelling it frees no self-hosted runner" \
  || { fail "a hosted job was selected; cancelling it cannot clear a self-hosted jam ($rc)"; sed 's/^/diag - /' "$tmp/out.txt"; }

# `self-hosted` alone is not the pool. The org also runs `hostinger`
# (`self-hosted,Linux,X64,manish`), so a job can hold a self-hosted runner and
# still free nothing when the `general` pool is the one that is jammed —
# cancelling it destroys someone else's merge for no capacity. Each half of the
# two-label guard needs its own fixture: the hosted case above fails BOTH
# checks, so it cannot tell which one is doing the work.
other_pool_job() { # $1 = started_at — self-hosted, but a DIFFERENT pool
  printf '{"jobs":[{"status":"in_progress","started_at":"%s","labels":["self-hosted","manish"]}]}\n' "$1"
}
printf '{"workflow_runs":[{"id":1,"name":"AI privileged merge"}]}\n' >"$INPROGRESS_FILE"
other_pool_job "$old_iso" >"$JOBS_FILE"
rc="$(run_watchdog)"
{ [ "$rc" = rc=0 ] && ! cancelled && grep -q 'stale_poll_jobs=0' "$tmp/out.txt"; } \
  && pass "a self-hosted job in a different pool is not a candidate — it frees no runner here" \
  || { fail "a job on another self-hosted pool was selected; cancelling it cannot clear this pool's jam ($rc)"; sed 's/^/diag - /' "$tmp/out.txt"; }

poll_job "$old_iso" >"$JOBS_FILE"

# --- starvation has to be starvation OF THIS POOL ---------------------------
# `status=queued` counts every queued run in the org, including runs queued for
# hosted capacity. Those are not waiting on this pool and no cancellation here
# can release them, so they are not evidence that a poll job is in anyone's way.
queued_hosted_job >"$QUEUED_JOBS_FILE"
rc="$(run_watchdog)"
{ [ "$rc" = rc=0 ] && ! cancelled && grep -q 'queued_runs=0' "$tmp/out.txt"; } \
  && pass "a run queued for hosted capacity is not work starved by this pool" \
  || { fail "a hosted queue was read as starvation of the self-hosted pool ($rc)"; sed 's/^/diag - /' "$tmp/out.txt"; }

# ...and the same for the other half of the label pair: work queued for another
# self-hosted pool is not waiting on this one either, so it is not the evidence
# that authorises a cancel.
printf '{"jobs":[{"status":"queued","labels":["self-hosted","manish"]}]}\n' >"$QUEUED_JOBS_FILE"
rc="$(run_watchdog)"
{ [ "$rc" = rc=0 ] && ! cancelled && grep -q 'queued_runs=0' "$tmp/out.txt"; } \
  && pass "a run queued for a different self-hosted pool is not starvation of this one" \
  || { fail "another pool's queue was read as starvation of this pool ($rc)"; sed 's/^/diag - /' "$tmp/out.txt"; }
queued_pool_job >"$QUEUED_JOBS_FILE"

# --- the two selection rules carry different evidence, so they arm apart -----
# The 35-minute age rule preempted four runs correctly in production. The #343
# poll-step rule has never fired at all. Arming the proven one must not arm the
# unproven one, and disarming the unproven one must not disarm the proven one.
printf '{"workflow_runs":[{"id":1,"name":"AI review + auto-merge"}]}\n' >"$INPROGRESS_FILE"
gate_job "$mid_iso" in_progress >"$JOBS_FILE"
rc="$(WATCHDOG_POLL_STEP_DRY_RUN=true run_watchdog)"
{ [ "$rc" = rc=0 ] && ! cancelled && grep -q 'DRY RUN would cancel' "$tmp/out.txt"; } \
  && pass "a poll-step candidate is reported, not cancelled, while its own rule is unarmed" \
  || { fail "the unproven poll-step rule cancelled under WATCHDOG_POLL_STEP_DRY_RUN=true ($rc)"; sed 's/^/diag - /' "$tmp/out.txt"; }

printf '{"workflow_runs":[{"id":1,"name":"AI privileged merge"}]}\n' >"$INPROGRESS_FILE"
poll_job "$old_iso" >"$JOBS_FILE"
rc="$(WATCHDOG_POLL_STEP_DRY_RUN=true run_watchdog)"
[ "$rc" = rc=0 ] && cancelled \
  && pass "the proven age rule still cancels while the poll-step rule is unarmed" \
  || { fail "disarming the poll-step rule also disarmed the age rule — the only mitigation for a privileged_merge jam ($rc)"; sed 's/^/diag - /' "$tmp/out.txt"; }

rc="$(WATCHDOG_POLL_STEP_DRY_RUN=yes run_watchdog)"
{ [ "$rc" = "rc=2" ] && ! cancelled; } \
  && pass "an unparseable WATCHDOG_POLL_STEP_DRY_RUN is a fault, never treated as armed" \
  || fail "WATCHDOG_POLL_STEP_DRY_RUN=yes did not fail closed ($rc)"

# --- #355: the runner list is paginated -------------------------------------
busy_pool_2pages >"$RUNNERS_FILE"
rc="$(run_watchdog)"
{ [ "$rc" = rc=0 ] && cancelled && grep -q 'online=2 idle=0' "$tmp/out.txt"; } \
  && pass "runner pages are aggregated, not counted per page (#355)" \
  || { fail "a two-page runner list did not aggregate ($rc)"; sed 's/^/diag - /' "$tmp/out.txt"; }

mixed_pool_2pages >"$RUNNERS_FILE"
rc="$(run_watchdog)"
{ [ "$rc" = rc=0 ] && ! cancelled && grep -q 'online=2 idle=1' "$tmp/out.txt"; } \
  && pass "an idle runner on a later page still means spare capacity (#355)" \
  || { fail "idle capacity on page 2 was missed ($rc)"; sed 's/^/diag - /' "$tmp/out.txt"; }

malformed_pool_2pages >"$RUNNERS_FILE"
rc="$(run_watchdog)"
{ [ "$rc" = "rc=2" ] && ! cancelled; } \
  && pass "a malformed runner page is a fault, not an empty pool (#355)" \
  || fail "a malformed page did not fail closed ($rc)"
busy_pool >"$RUNNERS_FILE"

workflows_dir="$here/../.github/workflows"
IFS= read -r default_hit < <(grep -o 'WATCHDOG_POLL_WORKFLOWS:-[^}]*' "$script") || true
default_workflows="${default_hit#*-}"
[ "$default_workflows" = __no_poll_workflows__ ] \
  && grep -q 'POLL_STEPS="${WATCHDOG_POLL_STEPS:-}"' "$script" \
  && pass "production watchdog allowlists are inert after polling retirement" \
  || fail "retired merge-gate pollers remain cancellable by default"

# --- the arm/disarm decision is a workflow-level contract (#342) ------------
# The script deliberately carries no default, so these two expressions are the
# ONLY place that decides whether cancelling is authorised — and nothing else
# guards them. #336 shipped armed by exactly this drift. Pin them literally:
# the age rule (35 minutes, the one with production evidence) stays armed; the
# poll-step rule widened by #343 reports only until it earns the same evidence.
watchdog_wf="$workflows_dir/fleet-watchdog.yml"
if [ -f "$watchdog_wf" ]; then
  grep -qF "WATCHDOG_DRY_RUN: \${{ vars.CI_WATCHDOG_DRY_RUN || 'false' }}" "$watchdog_wf" \
    && pass "the proven age path is armed by default in fleet-watchdog.yml" \
    || fail "the WATCHDOG_DRY_RUN default in fleet-watchdog.yml is not the pinned 'false' — the arm/disarm decision has drifted with nothing else guarding it"
  grep -qF "WATCHDOG_POLL_STEP_DRY_RUN: \${{ vars.CI_WATCHDOG_POLL_STEP_DRY_RUN || 'true' }}" "$watchdog_wf" \
    && pass "the widened poll-step path is report-only by default in fleet-watchdog.yml" \
    || fail "the WATCHDOG_POLL_STEP_DRY_RUN default in fleet-watchdog.yml is not the pinned 'true' — the rule with no production evidence would cancel"
else
  fail "could not locate fleet-watchdog.yml to pin the dry-run defaults"
fi

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
