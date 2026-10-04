#!/usr/bin/env bash
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
workflow="$root/.github/workflows/ai-privileged-merge.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

python3 - "$workflow" >"$tmp/promote.sh" <<'PY'
import sys
import yaml

document = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
steps = document["jobs"]["privileged_merge"]["steps"]
names = [
    "Authorize terminal merge from trusted metadata",
    "Revalidate independent review receipt",
    "Merge the authorized head",
    "Confirm terminal merge state",
    "Export terminal merge cleanup receipt",
]
for name in names:
    matches = [step for step in steps if step.get("name") == name]
    if len(matches) != 1 or not matches[0].get("run"):
        raise SystemExit(f"missing unique non-empty step: {name}")
    print(matches[0]["run"])
cleanup_steps = document["jobs"]["cleanup_arm_receipt"]["steps"]
cleanup = [
    step
    for step in cleanup_steps
    if step.get("name") == "Delete consumed arm receipt artifact"
]
if len(cleanup) != 1 or not cleanup[0].get("run"):
    raise SystemExit("missing unique non-empty cleanup deletion step")
PY
[ -s "$tmp/promote.sh" ] || { echo "FAIL - promotion block missing"; exit 1; }

mkdir -p "$tmp/bin" "$tmp/run/.gate-trust/scripts/ci-gate"
cat >"$tmp/run/.gate-trust/scripts/ci-gate/verify-arm-receipt.sh" <<'SH'
#!/usr/bin/env bash
printf 'verify-arm-receipt\n' >>"$CALLS"
printf '8001\n' >"$ARM_RECEIPT_ARTIFACT_ID_FILE"
exit "${VERIFY_RC:-0}"
SH
chmod 0644 "$tmp/run/.gate-trust/scripts/ci-gate/verify-arm-receipt.sh"
cp "$root/scripts/ci-gate/review-policy-envelope.py" "$tmp/run/.gate-trust/scripts/ci-gate/review-policy-envelope.py"
cp "$root/scripts/ci-gate/terminal-merge.sh" "$tmp/run/.gate-trust/scripts/ci-gate/terminal-merge.sh"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CALLS"
case "$*" in
  "pr view "*) cat "$META_FILE" ;;
  *"repos/$TARGET_REPO --jq"*) printf '%s\n' main ;;
  *"commits/$EXPECTED_HEAD_SHA/check-runs?per_page=100"*) cat "$CI_CHECKS_FILE" ;;
  *"actions/workflows/315894159/runs?head_sha=$EXPECTED_HEAD_SHA&event=pull_request&per_page=100"*) cat "$WORKFLOW_RUNS_FILE" ;;
  *"actions/workflows/315894159"*)
    [ "${WORKFLOW_METADATA_RC:-0}" = 0 ] || exit "$WORKFLOW_METADATA_RC"
    cat "$WORKFLOW_METADATA_FILE" ;;
  *"actions/runs/7002/jobs?per_page=100"*) cat "$CI_JOBS_FILE" ;;
  *"actions/runs/7002"*) cat "$CI_RUN_FILE" ;;
  *"contents/.github/workflows/actions-ci.yml?ref=$EXPECTED_HEAD_SHA"*) printf '%s\n' "$WORKFLOW_BLOB_HEAD" ;;
  *"contents/.github/workflows/actions-ci.yml?ref=main"*) printf '%s\n' "$WORKFLOW_BLOB_TRUSTED" ;;
  *"repos/Verjson/.github/commits/main"*) printf '%s\n' "$EXECUTING_WORKFLOW_SHA" ;;
  *"check-runs/$AUTHORIZATION_CHECK_ID"*) cat "$CHECK_FILE" ;;
  *"pulls/$PR_NUMBER/reviews/"*) cat "$REVIEW_REFETCH_FILE" ;;
  *"pulls/$PR_NUMBER/reviews?per_page=100"*)
    review_calls="$(cat "$REVIEW_CALLS_FILE")"
    review_calls=$((review_calls + 1))
    printf '%s\n' "$review_calls" >"$REVIEW_CALLS_FILE"
    if [ "$review_calls" -le 2 ]; then cat "$REVIEWS_FILE"; else cat "$LATEST_REVIEWS_FILE"; fi ;;
  *"collaborators/attacker/permission"*) printf '%s\n' "${ATTACKER_ROLE:-none}" ;;
  *"collaborators/independent-reviewer/permission"*)
    permission_calls="$(cat "$PERMISSION_CALLS_FILE")"
    permission_calls=$((permission_calls + 1))
    printf '%s\n' "$permission_calls" >"$PERMISSION_CALLS_FILE"
    if [ "$permission_calls" -eq 1 ]; then
      printf '%s\n' "${INDEPENDENT_REVIEWER_ROLE:-maintain}"
    else
      printf '%s\n' "${TERMINAL_REVIEWER_ROLE:-${INDEPENDENT_REVIEWER_ROLE:-maintain}}"
    fi ;;
  *"repos/$TARGET_REPO/pulls/$PR_NUMBER"*)
    pull_calls="$(cat "$PULL_CALLS_FILE")"
    pull_calls=$((pull_calls + 1))
    printf '%s\n' "$pull_calls" >"$PULL_CALLS_FILE"
    if [ "$pull_calls" -eq 1 ]; then cat "$BASE_META_FILE"; else cat "$TERMINAL_META_FILE"; fi ;;
  *"repos/$TARGET_REPO/git/ref/heads/$DEFAULT_BRANCH"*) printf '%s\n' "$AUTHORIZED_BASE_SHA" ;;
  "pr merge "*)
    if [ "${MERGE_CONFIRMED:-true}" = true ]; then
      jq '.state="MERGED"' "$META_FILE" >"$META_FILE.next" && mv "$META_FILE.next" "$META_FILE"
      if [ -n "${MERGE_CONFIRMED_HEAD:-}" ]; then
        jq --arg head "$MERGE_CONFIRMED_HEAD" '.headRefOid = $head' "$META_FILE" \
          >"$META_FILE.next" && mv "$META_FILE.next" "$META_FILE"
      fi
    fi ;;
  *) echo "unexpected gh call: $*" >&2; exit 2 ;;
esac
GH
chmod +x "$tmp/bin/gh"

export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" META_FILE="$tmp/meta.json" CHECK_FILE="$tmp/check.json" REVIEWS_FILE="$tmp/reviews.json" REVIEW_REFETCH_FILE="$tmp/review-refetch.json" LATEST_REVIEWS_FILE="$tmp/latest-reviews.json" REVIEW_CALLS_FILE="$tmp/review-calls" PERMISSION_CALLS_FILE="$tmp/permission-calls" CI_CHECKS_FILE="$tmp/ci-checks.json" CI_RUN_FILE="$tmp/ci-run.json" CI_JOBS_FILE="$tmp/ci-jobs.json"
export BASE_META_FILE="$tmp/base-meta.json"
export TERMINAL_META_FILE="$tmp/terminal-meta.json" PULL_CALLS_FILE="$tmp/pull-calls"
export WORKFLOW_METADATA_FILE="$tmp/workflow-metadata.json" WORKFLOW_RUNS_FILE="$tmp/workflow-runs.json"
export TARGET_REPO=Verjson/example PR_NUMBER=7 AUTHORIZATION_CHECK_ID=9001
export EXPECTED_HEAD_SHA=0123456789abcdef0123456789abcdef01234567
export AUTHORIZED_BASE_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb DEFAULT_BRANCH=main
export ARM_RUN_ID=7001 ARM_RUN_ATTEMPT=2 EXPECTED_APP_ID=4242 EXPECTED_APP_SLUG=verjson-ai-review
export GH_TOKEN=admin-token GITHUB_REPOSITORY_OWNER=Verjson GITHUB_REF=refs/heads/main CALLER_REF=refs/heads/main
export EXECUTING_WORKFLOW_REPOSITORY=Verjson/.github EXECUTING_WORKFLOW_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export GITHUB_SERVER_URL=https://github.com GITHUB_API_URL=https://api.github.com WORKFLOW_BLOB_HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa WORKFLOW_BLOB_TRUSTED=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
mkdir -p "$tmp/runner-temp"
export RUNNER_TEMP="$tmp/runner-temp"
export GITHUB_OUTPUT="$tmp/github-output"
export REQUIRED_CHECK_POLICY='[{"name":"shell-tests","app_id":15368,"workflow_id":315894159,"workflow_path":".github/workflows/actions-ci.yml"}]'
encode_policy() { python3 "$root/scripts/ci-gate/review-policy-envelope.py" encode "$1"; }
ai_merge_policy='{"actor":"trusted-arm","actor_permission":"automation","authority":"ai-merge","budget_usd":"5.00","fallback_budget_usd":"5.00","fallback_model":"deepseek-v4-flash","model":"deepseek-v4-pro","pricing_version":"deepseek-v4-2026-08-10","provider":"deepseek"}'
ai_approve_policy='{"actor":"trusted-arm","actor_permission":"automation","authority":"ai-approve","budget_usd":"5.00","fallback_budget_usd":"5.00","fallback_model":"deepseek-v4-flash","model":"deepseek-v4-pro","pricing_version":"deepseek-v4-2026-08-10","provider":"deepseek"}'
REVIEW_POLICY="$(encode_policy "$ai_merge_policy")"
export REVIEW_POLICY

write_base() {
  : >"$CALLS"
  unset VERIFY_RC
  unset MERGE_CONFIRMED
  unset WORKFLOW_METADATA_RC
  unset INDEPENDENT_REVIEWER_ROLE
  unset TERMINAL_REVIEWER_ROLE
  printf '0\n' >"$REVIEW_CALLS_FILE"
  printf '0\n' >"$PERMISSION_CALLS_FILE"
  printf '0\n' >"$PULL_CALLS_FILE"
  export WORKFLOW_BLOB_HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa WORKFLOW_BLOB_TRUSTED=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  jq -nc --arg head "$EXPECTED_HEAD_SHA" '{state:"OPEN",isDraft:false,title:"change",labels:[],headRefOid:$head,headRepositoryOwner:{login:"Verjson"}}' >"$META_FILE"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" --arg base "$AUTHORIZED_BASE_SHA" \
    '{state:"open",draft:false,title:"change",labels:[],head:{sha:$head},base:{ref:"main",sha:$base}}' >"$BASE_META_FILE"
  cp "$BASE_META_FILE" "$TERMINAL_META_FILE"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" \
    '{id:9001,name:"AI review authorization",head_sha:$head,status:"completed",conclusion:"success",app:{id:15368,slug:"github-actions"}}' >"$CHECK_FILE"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" --arg login "${EXPECTED_APP_SLUG}[bot]" --arg check "$AUTHORIZATION_CHECK_ID" \
    --arg marker "<!-- independent-review:v1 pr:${PR_NUMBER} head:${EXPECTED_HEAD_SHA} verdict:approved -->" \
    '[
      {id:81,state:"APPROVED",commit_id:$head,user:{login:$login,type:"Bot"},body:("<!-- ai-review-authorization:"+$check+" -->")},
      {id:82,state:"COMMENTED",commit_id:$head,user:{login:"independent-reviewer",type:"User"},author_association:"MEMBER",body:$marker}
    ]' >"$REVIEWS_FILE"
  jq '.[1]' "$REVIEWS_FILE" >"$REVIEW_REFETCH_FILE"
  cp "$REVIEWS_FILE" "$LATEST_REVIEWS_FILE"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" '{check_runs:[{id:101,name:"shell-tests",head_sha:$head,status:"completed",conclusion:"success",details_url:"https://github.com/Verjson/example/actions/runs/7002/job/8002",app:{id:15368,slug:"github-actions"},check_suite:{id:6002}}]}' >"$CI_CHECKS_FILE"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" '{id:7002,check_suite_id:6002,workflow_id:315894159,path:".github/workflows/actions-ci.yml",event:"pull_request",head_sha:$head,head_repository:{full_name:"Verjson/example"},status:"completed",conclusion:"success"}' >"$CI_RUN_FILE"
  jq -nc '{jobs:[{id:8002,name:"shell-tests",check_run_url:"https://api.github.com/repos/Verjson/example/check-runs/101"}]}' >"$CI_JOBS_FILE"
  jq -nc '{id:315894159,path:".github/workflows/actions-ci.yml",state:"active"}' >"$WORKFLOW_METADATA_FILE"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" '{workflow_runs:[{id:7002,workflow_id:315894159,path:".github/workflows/actions-ci.yml",event:"pull_request",head_sha:$head,status:"completed",conclusion:"success"}]}' >"$WORKFLOW_RUNS_FILE"
}
run_promote() { (cd "$tmp/run" && bash "$tmp/promote.sh"); }
expect_pass() { label="$1"; shift; if "$@" >"$tmp/out" 2>&1; then pass "$label"; else fail "$label: $(tail -1 "$tmp/out")"; fi; }
expect_fail() { label="$1"; shift; if "$@" >"$tmp/out" 2>&1; then fail "$label"; else pass "$label"; fi; }

if (cd "$tmp/run" && .gate-trust/scripts/ci-gate/verify-arm-receipt.sh) >"$tmp/out" 2>&1; then
  fail "non-executable sparse-checkout fixture unexpectedly supports direct execution"
else
  pass "non-executable sparse-checkout fixture rejects direct execution"
fi
write_base; expect_pass "explicit bash invocation supports a non-executable sparse-checkout verifier" run_promote
grep -q -- '--admin --squash --match-head-commit' "$CALLS" \
  && pass "all-success promotion merges the exact authorized head" || fail "terminal promotion did not use exact-head admin squash merge"
write_base
for draft_state in '.draft=true' 'del(.draft)' '.draft=null' '.draft="false"'; do
  write_base
  jq "$draft_state" "$TERMINAL_META_FILE" >"$tmp/x" && mv "$tmp/x" "$TERMINAL_META_FILE"
  expect_fail "terminal REST draft guard rejects $draft_state" run_promote
  ! grep -q 'pr merge' "$CALLS" || fail "terminal REST draft guard allowed $draft_state"
done
write_base
# #1615: the reviewing token cannot always resolve author_association correctly (e.g. a
# default GITHUB_TOKEN without org-membership read visibility reports a real org MEMBER as
# "NONE"). The independent-review revalidation must not gate on that unreliable field; the
# live admin/maintain collaborator-permission check is the actual, already-authoritative
# eligibility test.
jq '.[1].author_association="NONE"' "$REVIEWS_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEWS_FILE"
jq '.[1]' "$REVIEWS_FILE" >"$REVIEW_REFETCH_FILE"
cp "$REVIEWS_FILE" "$LATEST_REVIEWS_FILE"
expect_pass "unresolvable author_association still promotes a real admin/maintain reviewer (#1615)" run_promote
grep -q -- '--admin --squash --match-head-commit' "$CALLS" \
  && pass "author_association-blind promotion still merges the exact authorized head" \
  || fail "author_association-blind promotion did not use exact-head admin squash merge"

write_base
# Selection can no longer rely on author_association to keep candidates scoped to
# org-associated accounts (#1615), and this is a PUBLIC repository: anyone can leave a
# review at the exact head SHA. A higher-numbered review ID from an account that is not
# actually a collaborator must not block or supersede a real approval — selection has to
# walk candidates from the highest ID down and accept the first that still holds live
# admin/maintain, not trust the highest ID outright.
jq '.[1] as $base | . + [($base | .id=90 | .user.login="attacker" | .body="unrelated comment")]' "$REVIEWS_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEWS_FILE"
cp "$REVIEWS_FILE" "$LATEST_REVIEWS_FILE"
expect_pass "a higher-ID review from a non-privileged public account cannot block or supersede a real approval" run_promote
grep -q -- '--admin --squash --match-head-commit' "$CALLS" \
  && pass "griefing-resistant selection still merges the real approver's exact-head receipt" \
  || fail "griefing-resistant selection did not reach terminal merge"

write_base
# The walk-from-highest-ID search that closes the griefing gap above must itself be
# bounded: without a cap, a flood of junk reviews posted above the real approval would
# force an unbounded number of live permission lookups. More non-privileged candidates
# than the search bound allows must fail closed rather than search indefinitely.
jq --argjson base "$(jq '.[1]' "$REVIEWS_FILE")" '
    . + [range(100; 125) as $n | $base | .id=$n | .user.login="attacker" | .body="unrelated comment"]
  ' "$REVIEWS_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEWS_FILE"
cp "$REVIEWS_FILE" "$LATEST_REVIEWS_FILE"
if run_promote >"$tmp/out" 2>&1; then
  fail "flooding the exact head with more junk reviews than the search bound still promoted"
elif [ "$(grep -c 'collaborators/attacker/permission' "$CALLS")" -le 20 ] &&
    [ "$(grep -c 'collaborators/independent-reviewer/permission' "$CALLS")" = 0 ]; then
  pass "a junk-review flood past the search bound fails closed with a bounded number of lookups"
else
  fail "a junk-review flood past the search bound did not stay within its bounded lookup cost"
fi
write_base; REVIEW_POLICY="$(encode_policy "$ai_approve_policy")" expect_fail "ai-approve authority never reaches terminal merge" run_promote
! grep -q 'pr merge' "$CALLS" || fail "ai-approve authority attempted a terminal merge"
write_base; jq '.conclusion="failure"' "$CHECK_FILE" >"$tmp/x" && mv "$tmp/x" "$CHECK_FILE"; expect_fail "failed authorization never promotes" run_promote
write_base; jq '.conclusion=null' "$CHECK_FILE" >"$tmp/x" && mv "$tmp/x" "$CHECK_FILE"; expect_fail "inconclusive authorization never promotes" run_promote
write_base; printf '[]\n' >"$REVIEWS_FILE"; expect_fail "missing exact App approval never promotes" run_promote
write_base; jq '.[0].user.login="attacker[bot]"' "$REVIEWS_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEWS_FILE"; expect_fail "wrong approval identity never promotes" run_promote
write_base; jq '.[0].commit_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' "$REVIEWS_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEWS_FILE"; expect_fail "stale App approval never promotes" run_promote
write_base; jq 'del(.[1])' "$REVIEWS_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEWS_FILE"; expect_fail "missing independent-review receipt never promotes" run_promote
write_base; jq '.[1].commit_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' "$REVIEWS_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEWS_FILE"; expect_fail "stale-head independent-review receipt never promotes" run_promote
write_base; jq '.body="prefix <!-- independent-review:v1 pr:7 head:0123456789abcdef0123456789abcdef01234567 verdict:approved --> suffix"' "$REVIEW_REFETCH_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEW_REFETCH_FILE"; expect_fail "surrounding context cannot impersonate independent-review receipt" run_promote
write_base; jq '.body="```\n<!-- independent-review:v1 pr:7 head:0123456789abcdef0123456789abcdef01234567 verdict:approved -->\n```"' "$REVIEW_REFETCH_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEW_REFETCH_FILE"; expect_fail "fenced marker cannot impersonate independent-review receipt" run_promote
write_base; jq '.body |= sub("pr:7"; "pr:8")' "$REVIEW_REFETCH_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEW_REFETCH_FILE"; expect_fail "foreign-PR independent-review receipt never promotes" run_promote
write_base; jq '.state="APPROVED"' "$REVIEW_REFETCH_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEW_REFETCH_FILE"; expect_fail "ordinary approval cannot impersonate independent-review receipt" run_promote
write_base; jq '.body="edited after selection"' "$REVIEW_REFETCH_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEW_REFETCH_FILE"; expect_fail "edited receipt body fails terminal revalidation" run_promote
write_base; jq '.state="DISMISSED"' "$REVIEW_REFETCH_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEW_REFETCH_FILE"; expect_fail "dismissed receipt fails terminal revalidation" run_promote
write_base; jq '.body |= sub("verdict:approved"; "verdict:withdrawn")' "$REVIEW_REFETCH_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEW_REFETCH_FILE"; expect_fail "withdrawn receipt fails terminal revalidation" run_promote
write_base; jq '.[1] as $base | . + [($base | .id=83 | .state="CHANGES_REQUESTED" | .body="changes requested")]' "$REVIEWS_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEWS_FILE"; jq '.[2]' "$REVIEWS_FILE" >"$REVIEW_REFETCH_FILE"; cp "$REVIEWS_FILE" "$LATEST_REVIEWS_FILE"; expect_fail "later changes-requested verdict supersedes approval" run_promote
write_base; jq '.[1] as $base | . + [($base | .id=83 | .body="later verdict")]' "$LATEST_REVIEWS_FILE" >"$tmp/x" && mv "$tmp/x" "$LATEST_REVIEWS_FILE"; expect_fail "later review appearing during authorization supersedes receipt" run_promote
write_base; jq '.[1].user.type="Bot"' "$REVIEWS_FILE" >"$tmp/x" && mv "$tmp/x" "$REVIEWS_FILE"; expect_fail "bot-authored independent-review receipt never promotes" run_promote
write_base; export INDEPENDENT_REVIEWER_ROLE=write; expect_fail "write-only receipt author never promotes" run_promote
write_base; export TERMINAL_REVIEWER_ROLE=write; expect_fail "permission downgrade before token mint never promotes" run_promote
write_base
jq '.labels=[{"name":"hold"}]' "$TERMINAL_META_FILE" >"$tmp/x" && mv "$tmp/x" "$TERMINAL_META_FILE"
if run_promote >"$tmp/out" 2>&1; then
  fail "hold added after authorization reached terminal merge"
elif [ "$(cat "$PULL_CALLS_FILE")" = 2 ] &&
    grep -q 'terminal merge rejected' "$tmp/out" && ! grep -q 'pr merge' "$CALLS"; then
  pass "hold added after authorization blocks at terminal merge boundary"
else
  fail "late hold did not fail at the terminal merge boundary"
fi
write_base; jq '.headRepositoryOwner.login="outsider"' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"; expect_fail "fork PR fails closed" run_promote
write_base; jq '.isDraft=true' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"; expect_pass "draft PR is a terminal no-op" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "draft merged"
write_base; jq '.labels=[{"name":"hold"}]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"; expect_pass "held PR is a terminal no-op" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "hold merged"
write_base; jq '.labels=[{"name":"DO NOT MERGE"}]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"; expect_pass "DO NOT MERGE label is a terminal no-op" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "DO NOT MERGE label merged"
write_base; jq '.labels=[{"name":"Do__Not--Merge"}]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"; expect_pass "normalized hold label is a terminal no-op" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "normalized hold label merged"
write_base; jq '.title="chore: DO NOT MERGE until QA"' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"; expect_pass "DO NOT MERGE title is a terminal no-op" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "DO NOT MERGE title merged"
write_base; jq --arg title $'DO NOT MERGE\u212a' '.title=$title' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"; expect_pass "Unicode boundary title is a terminal no-op" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "Unicode boundary title merged"
write_base; jq '.title="REDO NOT MERGEABLE: QA"' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"; expect_pass "embedded title words are not a hold" run_promote; grep -q 'pr merge' "$CALLS" || fail "embedded title words blocked a valid promotion"
for malformed in truncated labels-not-array label-name-not-string title-not-string; do
  write_base
  case "$malformed" in
    truncated) printf '{"state":"OPEN","isDraft":false,"title":"change","labels":[' >"$META_FILE" ;;
    labels-not-array) jq '.labels="hold"' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE" ;;
    label-name-not-string) jq '.labels=[{"name":{"value":"hold"}}]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE" ;;
    title-not-string) jq '.title=42' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE" ;;
  esac
  expect_fail "$malformed hold metadata fails closed" run_promote
  ! grep -q 'pr merge' "$CALLS" || fail "$malformed hold metadata merged"
done
for unreadable in empty null; do
  write_base
  case "$unreadable" in
    empty) : >"$META_FILE" ;;
    null) printf 'null\n' >"$META_FILE" ;;
  esac
  expect_pass "$unreadable PR metadata is a terminal no-op" run_promote
  ! grep -q 'pr merge' "$CALLS" || fail "$unreadable PR metadata merged"
done
write_base; jq '.check_runs[0].conclusion=null | .check_runs[0].status="in_progress"' "$CI_CHECKS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_CHECKS_FILE"; expect_pass "pending required CI exits immediately" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "pending CI merged"
write_base; jq '.check_runs[0].conclusion="failure"' "$CI_CHECKS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_CHECKS_FILE"; expect_fail "terminal required CI failure blocks" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "failed CI merged"
write_base; jq '.check_runs=[]' "$CI_CHECKS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_CHECKS_FILE"; printf '{"workflow_runs":[]}\n' >"$WORKFLOW_RUNS_FILE"; expect_pass "not-yet-started required CI remains pending without mutation" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "not-yet-started CI merged"
write_base; jq '.check_runs=[]' "$CI_CHECKS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_CHECKS_FILE"; expect_fail "completed workflow without the reviewed check is a permanent rename/misconfiguration" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "renamed CI merged"
write_base; jq '.check_runs=[]' "$CI_CHECKS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_CHECKS_FILE"; jq '.workflow_runs[0].status="in_progress" | .workflow_runs[0].conclusion=null' "$WORKFLOW_RUNS_FILE" >"$tmp/x" && mv "$tmp/x" "$WORKFLOW_RUNS_FILE"; expect_pass "active workflow with an unreported check remains pending" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "pending workflow merged"
write_base; export WORKFLOW_METADATA_RC=1; expect_fail "deleted required workflow fails closed before promotion" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "deleted workflow merged"
write_base; jq '.path=".github/workflows/renamed.yml"' "$WORKFLOW_METADATA_FILE" >"$tmp/x" && mv "$tmp/x" "$WORKFLOW_METADATA_FILE"; expect_fail "renamed required workflow path fails closed before promotion" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "renamed workflow merged"
write_base; jq '.check_runs += [{id:102,name:"shell-tests",status:"in_progress",conclusion:null,details_url:"https://github.com/Verjson/example/actions/runs/7002/job/8003",app:{id:15368,slug:"github-actions"}}]' "$CI_CHECKS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_CHECKS_FILE"; expect_pass "newer pending check overrides older success" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "older success bypassed newer pending"
write_base; jq '.check_runs = [{id:100,name:"shell-tests",status:"completed",conclusion:"failure",details_url:"https://github.com/Verjson/example/actions/runs/7002/job/8001",app:{id:15368,slug:"github-actions"}}, .check_runs[0]]' "$CI_CHECKS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_CHECKS_FILE"; expect_pass "newer success overrides older failure" run_promote
write_base; jq '.check_runs += [(.check_runs[0] | .id=102 | .app.id=999)]' "$CI_CHECKS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_CHECKS_FILE"; expect_fail "newest duplicate context from wrong App cannot forge required CI" run_promote
write_base; jq '.workflow_id=999' "$CI_RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_RUN_FILE"; expect_fail "wrong workflow identity cannot satisfy required CI" run_promote
# #1610: a required check's own job can finish (and satisfy $CI_CHECKS_FILE) well before
# the multi-job run backing it as a whole reaches "completed". That must retry, not
# hard-fail, as long as the run's identity still matches; only a run that finished
# unsuccessfully is a terminal block.
write_base; jq '.status="in_progress" | .conclusion=null' "$CI_RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_RUN_FILE"; expect_pass "in-progress trusted workflow run with an already-completed required-check job remains pending" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "in-progress trusted workflow run merged"
write_base; jq '.conclusion="failure"' "$CI_RUN_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_RUN_FILE"; expect_fail "terminally unsuccessful trusted workflow run blocks despite a completed required-check job" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "unsuccessful trusted workflow run merged"
write_base; jq '.check_runs[0].check_suite.id=9999' "$CI_CHECKS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_CHECKS_FILE"; expect_fail "same-App forged check cannot claim an unrelated successful workflow run" run_promote
write_base; jq '.jobs[0].check_run_url="https://api.github.com/repos/Verjson/example/check-runs/9999"' "$CI_JOBS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_JOBS_FILE"; expect_fail "same-suite forged check must be the exact job check run" run_promote
write_base; jq '.jobs += [.jobs[0]]' "$CI_JOBS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_JOBS_FILE"; expect_fail "ambiguous duplicate job association fails closed" run_promote
grep -q -- 'api --paginate repos/Verjson/example/actions/runs/7002/jobs?per_page=100' "$CALLS" \
  && pass "trusted workflow job association is read with pagination" \
  || fail "trusted workflow job association did not paginate"
write_base; jq '.check_runs[0].details_url="https://attacker.invalid/actions/runs/7002/job/8002"' "$CI_CHECKS_FILE" >"$tmp/x" && mv "$tmp/x" "$CI_CHECKS_FILE"; expect_fail "wrong details URL cannot satisfy required CI" run_promote
write_base; export WORKFLOW_BLOB_HEAD=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb; expect_fail "PR-modified workflow revision cannot satisfy required CI" run_promote
write_base
if (export REQUIRED_CHECK_POLICY='[{"name":"wrong-check","app_id":15368,"workflow_id":315894159,"workflow_path":".github/workflows/actions-ci.yml"}]'; run_promote) >"$tmp/out" 2>&1; then
  fail "renamed check satisfied required CI"
elif grep -q 'missing or renamed' "$tmp/out" && ! grep -q 'pr merge' "$CALLS"; then
  pass "renamed required check fails closed after its trusted workflow completes"
else
  fail "renamed check did not produce permanent-misconfiguration evidence"
fi
# The gate must never satisfy its own readiness (#276, ADR 0039; carried onto the
# ADR 0081 topology by #1320). Under the retired poll design the gate enumerated the
# commit rollup and had to FILTER its own jobs out. The declaration is now an
# allowlist, so the same fail-open is reached by DECLARING a promotion surface as a
# required check: the gate's own successful authorization check would then satisfy
# the readiness it is supposed to gate, and a PR would merge on nothing but itself.
# Both halves of the schema clause are pinned separately because either alone
# leaves a usable spelling of the same declaration.
for self_name in "AI review authorization" "AI terminal merge promotion" "AI terminal promotion retry"; do
  write_base
  policy="$(jq -nc --arg name "$self_name" \
    '[{name:$name,app_id:15368,workflow_id:315894159,workflow_path:".github/workflows/actions-ci.yml"}]')"
  if (export REQUIRED_CHECK_POLICY="$policy"; run_promote) >"$tmp/out" 2>&1; then
    fail "gate check name satisfied its own readiness: $self_name"
  elif grep -q 'must declare trusted non-promotion Actions checks' "$tmp/out" && ! grep -q 'pr merge' "$CALLS"; then
    pass "gate check name cannot be declared a required check: $self_name"
  else
    fail "gate check name rejected without the non-promotion contract: $self_name"
  fi
done
for self_path in ai-review-merge ai-privileged-merge ai-promotion-retry; do
  write_base
  policy="$(jq -nc --arg path ".github/workflows/$self_path.yml" \
    '[{name:"shell-tests",app_id:15368,workflow_id:315894159,workflow_path:$path}]')"
  if (export REQUIRED_CHECK_POLICY="$policy"; run_promote) >"$tmp/out" 2>&1; then
    fail "promotion workflow satisfied its own readiness: $self_path"
  elif grep -q 'must declare trusted non-promotion Actions checks' "$tmp/out" && ! grep -q 'pr merge' "$CALLS"; then
    pass "promotion workflow cannot be declared a required check: $self_path"
  else
    fail "promotion workflow rejected without the non-promotion contract: $self_path"
  fi
done
write_base
: >"$GITHUB_OUTPUT"
if (export MERGE_CONFIRMED=false; run_promote) >"$tmp/out" 2>&1; then
  fail "unconfirmed merge postcondition did not fail closed"
else
  pass "unconfirmed merge postcondition fails closed"
fi
! grep -q '^terminal_merge_succeeded=true$' "$GITHUB_OUTPUT" \
  && pass "unconfirmed merge does not export cleanup eligibility" \
  || fail "unconfirmed merge exported cleanup eligibility"

write_base
: >"$GITHUB_OUTPUT"
export MERGE_CONFIRMED_HEAD=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
if run_promote >"$tmp/out" 2>&1; then
  fail "wrong-head merged postcondition did not fail closed"
else
  pass "wrong-head merged postcondition fails closed"
fi
unset MERGE_CONFIRMED_HEAD
! grep -q '^terminal_merge_succeeded=true$' "$GITHUB_OUTPUT" \
  && pass "wrong-head merged postcondition does not export cleanup eligibility" \
  || fail "wrong-head merged postcondition exported cleanup eligibility"
# A promotion that arrives after the PR left OPEN is a no-op either way, but the
# two ways are not the same event and must not read the same in the log. MERGED is
# the ordinary race: a duplicate dispatch for work already landed. CLOSED-unmerged
# means the gate authorized a head that a human then closed — nothing to merge, so
# still a no-op, but an upstream anomaly worth seeing. #1329 deleted the only test
# that distinguished them and left both on one silent `exit 0` (#1331).
write_base; jq '.state="MERGED"' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"; expect_pass "duplicate promotion after merge is idempotent" run_promote; ! grep -q 'pr merge' "$CALLS" || fail "merged PR repeated mutation"
grep -q 'already merged' "$tmp/out" \
  && pass "an already-merged no-op says so" || fail "merged no-op is silent and unattributable"
write_base; jq '.state="CLOSED"' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
expect_pass "promotion of a closed-unmerged PR is a terminal no-op" run_promote
! grep -q 'pr merge' "$CALLS" || fail "closed-unmerged PR reached a terminal merge"
! grep -q 'verify-arm-receipt' "$CALLS" || fail "closed-unmerged promotion verified a receipt it can never use"
grep -q 'closed without merging' "$tmp/out" \
  && pass "a closed-unmerged no-op is distinguishable from a completed merge" \
  || fail "closed-unmerged and merged promotions are indistinguishable silent no-ops"
write_base; jq '.headRefOid="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"; expect_pass "superseded promotion is a terminal no-op" run_promote
! grep -q 'verify-arm-receipt' "$CALLS" || fail "stale promotion verified an obsolete receipt"
! grep -q 'pr merge' "$CALLS" || fail "stale promotion attempted a merge"
write_base; GH_TOKEN='' expect_fail "missing privileged credential fails before mutation" run_promote

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
