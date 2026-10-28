#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/gate-rearm.yml"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fails=0
pass(){ printf 'ok   - %s\n' "$1"; }
fail(){ printf 'FAIL - %s\n' "$1"; fails=$((fails+1)); }

awk '$0=="        id: arm"{f=1} f&&$0=="        run: |"{r=1;next} r{if($0~/^      - name:/)exit;sub(/^          /,"");print}' \
  "$workflow" >"$tmp/arm.sh"
[ -s "$tmp/arm.sh" ] || { echo "FAIL - arm block missing"; exit 1; }
python3 - "$workflow" "$tmp/preauthorize.sh" "$tmp/event-policy.sh" "$root/.github/workflows/ai-review-merge.yml" "$tmp/review-title-policy.sh" <<'PY'
import sys
from pathlib import Path
import yaml

lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
workflow = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
Path(sys.argv[3]).write_text(workflow["jobs"]["event-policy"]["steps"][0]["run"] + "\n", encoding="utf-8")
review = yaml.safe_load(Path(sys.argv[4]).read_text(encoding="utf-8"))
Path(sys.argv[5]).write_text(review["jobs"]["title-policy"]["steps"][0]["run"], encoding="utf-8")
step_name = "- name: Authorize hold-clearing actor before AI review admission"
start = next(i for i, line in enumerate(lines) if line.strip() == step_name)
step_indent = len(lines[start]) - len(lines[start].lstrip())
run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
body_indent = len(lines[run]) - len(lines[run].lstrip()) + 2
end = next((i for i in range(run + 1, len(lines)) if lines[i].strip().startswith("- name:") and len(lines[i]) - len(lines[i].lstrip()) == step_indent), len(lines))
Path(sys.argv[2]).write_text("\n".join(line[body_indent:] for line in lines[run + 1:end]) + "\n", encoding="utf-8")
PY
[ -s "$tmp/preauthorize.sh" ] || { echo "FAIL - hold-clearing authorization step missing"; exit 1; }
awk '$0=="      - name: Complete the authorization when no review was dispatched"{f=1} f&&$0=="        run: |"{r=1;next} r{if($0~/^      - name:/)exit;sub(/^          /,"");print}' \
  "$workflow" >"$tmp/terminalize.sh"
[ -s "$tmp/terminalize.sh" ] || { echo "FAIL - authorization terminalizer missing"; exit 1; }

mkdir "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CALLS"
case "$*" in
  "api /installation")
    # Installation tokens receive 404 from this endpoint in production.
    exit 22 ;;
  "pr view "*)
    [ "${GH_VIEW_FAIL:-false}" != true ] || exit 1
    count="$(grep -c '^pr view ' "$CALLS")"
    if [ "$count" -eq 1 ]; then cat "$META_FILE"; else cat "$DISABLED_META_FILE"; fi ;;
  "api graphql "*) cat "$GRAPHQL_FILE" ;;
  *"commits/"*"/check-runs "*) cat "$LATEST_FILE" ;;
  *"actions/runs/7001 --jq"*) printf '2\n' ;;
  *"actions/runs/7001") printf '{"event":"pull_request_target","path":".github/workflows/gate-rearm.yml","head_repository":{"full_name":"verJSON/example"},"run_attempt":2}\n' ;;
  *"actions/runs/8000") jq -nc --arg path "${SOURCE_RUN_PATH:-.github/workflows/ai-review-label-rearm.yml}" --arg actor "${SOURCE_RUN_ACTOR:-maintainer}" --arg branch "${SOURCE_RUN_BRANCH:-main}" --argjson run_id "${SOURCE_RUN_ID:-8000}" --argjson repo_id "${SOURCE_REPO_ID:-1234}" --arg head_repo "${SOURCE_HEAD_REPO:-Verjson/example}" --arg repo_full "${SOURCE_REPO_FULL:-Verjson/example}" --argjson workflow_id "${SOURCE_WORKFLOW_ID:-77}" '{id:$run_id,event:"pull_request_target",path:$path,workflow_id:$workflow_id,run_attempt:1,head_sha:"0123456789abcdef0123456789abcdef01234567",head_branch:$branch,head_repository:{full_name:$head_repo},repository:{id:$repo_id,full_name:$repo_full},actor:{login:$actor}}' ;;
  *"actions/workflows/ai-review-lifecycle-rearm.yml"*"--jq"*) printf '88\n' ;;
  *"contents/.github/workflows/ai-review-label-rearm.yml?ref="*) printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n' ;;
  *"contents/.github/workflows/ai-review-lifecycle-rearm.yml?ref="*) printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n' ;;

  *"actions/workflows/ai-review-label-rearm.yml"*"--jq"*) printf '77\n' ;;
  *"contents/.github/workflows/ai-review-lifecycle-rearm.yml?ref=main"*) printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n' ;;
  *"contents/.github/workflows/ai-review-lifecycle-rearm.yml?ref="*) printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n' ;;

  *"actions/runs/7001/artifacts?per_page=100 --jq"*) printf '%s\n' "${RECEIPT_COUNT:-1}" ;;
  "run download 7001 "*)
    for arg in "$@"; do destination="$arg"; done
    mkdir -p "$destination"
    printf '{"review_policy":"%s"}\n' "$RECEIPT_POLICY" >"$destination/receipt.json" ;;
  *"collaborators/"*"/permission"*)
    [ "${GH_PERMISSION_FAIL:-false}" != true ] || exit 1
    queried_actor="${2#*/collaborators/}"
    queried_actor="${queried_actor%/permission}"
    if [ "$queried_actor" = "${PRIVILEGED_ACTOR:-other-admin}" ]; then
      role_name="${PRIVILEGED_ACTOR_ROLE_NAME:-admin}"
      base_permission="${PRIVILEGED_ACTOR_BASE_PERMISSION:-admin}"
    elif [ "$queried_actor" = "$REQUEST_ACTOR" ]; then
      role_name="${ACTOR_ROLE_NAME:-${ACTOR_PERMISSION:-triage}}"
      case "$role_name" in
        admin) base_permission=admin ;;
        maintain|write|push) base_permission=write ;;
        triage|read) base_permission=read ;;
        *) base_permission=none ;;
      esac
      base_permission="${ACTOR_BASE_PERMISSION:-$base_permission}"
    else
      role_name=none
      base_permission=none
    fi
    printf 'PERMISSION_LOOKUP %s permission=%s role_name=%s\n' \
      "$queried_actor" "$base_permission" "$role_name" >>"$CALLS"
    if [ "${3:-}" = --jq ]; then
      case "${4:-}" in
        '.permission // ""') printf '%s\n' "$base_permission" ;;
        '.role_name // ""') printf '%s\n' "$role_name" ;;
        *) echo "unexpected permission projection: ${4:-}" >&2; exit 2 ;;
      esac
    else
      jq -cn --arg permission "$base_permission" --arg role_name "$role_name" \
        '{permission:$permission,role_name:$role_name}'
    fi ;;
  *"issues/7/events?per_page=100"*) printf '[{"id":1,"event":"labeled","label":{"name":"ai-review"},"actor":{"login":"maintainer"}},{"id":2,"event":"labeled","label":{"name":"re-review"},"actor":{"login":"maintainer"}}]\n' ;;
  *"contents/.github/workflows/ai-review-merge.yml?ref=main"*) cat "$CALLER_FILE" ;;
  *"--method POST repos/Verjson/example/check-runs --input -"*)
    response="$(jq --argjson app_id "${CREATED_CHECK_APP_ID:-15368}" \
      '. + {id:9100,app:{id:$app_id,slug:"github-actions"}}')"
    printf '%s\n' "$response" | tee "$CHECK_FILE"
    if [ "${CREATED_CHECK_APP_ID:-15368}" != 15368 ]; then
      jq '.app.id=15368 | .app.slug="github-actions"' "$CHECK_FILE" >"$TMP_TEST/check.actual"
      mv "$TMP_TEST/check.actual" "$CHECK_FILE"
    fi ;;
  *"--method PATCH repos/Verjson/example/check-runs/9100 "*)
    jq '.status="completed" | .conclusion="failure"' "$CHECK_FILE" >"$TMP_TEST/check.updated" && mv "$TMP_TEST/check.updated" "$CHECK_FILE"
    printf '{}\n' ;;
  *"repos/Verjson/example/check-runs/9100"*) cat "$CHECK_FILE" ;;
  "workflow run "*) printf 'DISPATCH %s\n' "$*" >>"$CALLS" ;;
  "pr comment "*) printf 'COMMENT %s\n' "$*" >>"$CALLS" ;;
  "pr edit "*) [ "${PR_EDIT_FAIL:-false}" != true ] ;;
  *) echo "unexpected gh call: $*" >&2; exit 2 ;;
esac
GH
chmod +x "$tmp/bin/gh"

export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" META_FILE="$tmp/meta.json" TMP_TEST="$tmp"
export PRIVILEGED_ACTOR=other-admin PRIVILEGED_ACTOR_ROLE_NAME=admin PRIVILEGED_ACTOR_BASE_PERMISSION=admin
export DISABLED_META_FILE="$tmp/disabled.json" GRAPHQL_FILE="$tmp/graphql.json" LATEST_FILE="$tmp/latest.json" CHECK_FILE="$tmp/check.json"
export TARGET_REPO=Verjson/example PR_NUMBER=7 APP_ID=4242 APP_SLUG=verjson-ai-review
export AI_REVIEW_DISPATCH_WORKFLOW=ai-review-merge.yml
export DEFAULT_BRANCH=main EVENT_LABEL=hold EVENT_OLD_TITLE_HELD=false GITHUB_REPOSITORY_OWNER=Verjson
export EVENT_NAME=pull_request_target REPOSITORY_ID=1234
export REQUEST_ACTOR=maintainer
export WORKFLOW_REF=Verjson/example/.github/workflows/ai-review-label-rearm.yml@refs/heads/main
export WORKFLOW_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export EVENT_HEAD_SHA=0123456789abcdef0123456789abcdef01234567
export EVENT_NEW_TITLE_HELD=false HOLD_CLEAR_ACTOR_PERMISSION=''
export GITHUB_ENV="$tmp/github-env"
export ACTIONS_TOKEN=actions-token GH_TOKEN=actions-token GITHUB_SERVER_URL=https://github.com
export APP_KEY_POLICY_RESULT=success AI_REVIEW_KEY_VALIDATION_RESULT=success AI_REVIEW_ENVIRONMENT=ai-review-app
export GITHUB_RUN_ID=8000 GITHUB_RUN_ATTEMPT=1 RUNNER_TEMP="$tmp"
export GITHUB_OUTPUT="$tmp/github-output"
CALLER_FILE="$tmp/current-caller.yml"
cp "$root/scripts/ci-gate/fixtures/ai-review-caller-a6b3ccc.yml" "$CALLER_FILE"
printf '# current schema caller\n' >>"$CALLER_FILE"
export CALLER_FILE
receipt_policy='eyJhY3RvciI6Im1haW50YWluZXIiLCJhY3Rvcl9wZXJtaXNzaW9uIjoibWFpbnRhaW4iLCJidWRnZXRfdXNkIjoiMS4wMCIsIm1vZGVsIjoiZ3B0LTUuNi1sdW5hIiwicHJpY2luZ192ZXJzaW9uIjoib3BlbmFpLWx1bmEtbG9uZy1jb250ZXh0LTIwMjYtMDgtMDgiLCJwcm92aWRlciI6Im9wZW5haSJ9'
substitute_policy='eyJhY3RvciI6InRydXN0ZWQtYXJtIiwiYWN0b3JfcGVybWlzc2lvbiI6ImF1dG9tYXRpb24iLCJidWRnZXRfdXNkIjoiYXV0byIsIm1vZGVsIjoiYXV0byIsInByaWNpbmdfdmVyc2lvbiI6ImFudGhyb3BpYy1uYXRpdmUtdjEiLCJwcm92aWRlciI6ImFudGhyb3BpYyJ9'
export RECEIPT_POLICY="$receipt_policy"
head_sha=0123456789abcdef0123456789abcdef01234567

write_hold() {
  : >"$CALLS"
  : >"$GITHUB_OUTPUT"
  printf '{}\n' >"$LATEST_FILE"
  jq -nc --arg head "$head_sha" '{id:"PR_id",state:"OPEN",isDraft:false,title:"change",labels:[{name:"hold"}],headRefOid:$head,headRepositoryOwner:{login:"Verjson"},autoMergeRequest:{enabledAt:"now"}}' >"$META_FILE"
  printf '{"data":{"disablePullRequestAutoMerge":{"pullRequest":{"id":"PR_id"}}}}\n' >"$GRAPHQL_FILE"
  printf '{"id":"PR_id","autoMergeRequest":null}\n' >"$DISABLED_META_FILE"
  export EVENT_NAME=pull_request_target EVENT_ACTION=synchronize
}
run_arm(){
  local caller=ai-review-label-rearm.yml
  case "$EVENT_ACTION" in
    opened|reopened|synchronize) caller=gate-rearm.yml ;;
    ready_for_review|converted_to_draft|edited|unlabeled) caller=ai-review-lifecycle-rearm.yml ;;
  esac
  local workflow_id=77
  [ "$caller" = ai-review-lifecycle-rearm.yml ] && workflow_id=88
  (cd "$tmp" && EVENT_ACTION="$EVENT_ACTION" EVENT_LABEL="$EVENT_LABEL" \
    SOURCE_RUN_PATH="${SOURCE_RUN_PATH_OVERRIDE:-.github/workflows/$caller}" \
    SOURCE_WORKFLOW_ID="${SOURCE_WORKFLOW_ID_OVERRIDE:-$workflow_id}" \
    SOURCE_RUN_ID="${SOURCE_RUN_ID_OVERRIDE:-8000}" \
    SOURCE_REPO_ID="${SOURCE_REPO_ID_OVERRIDE:-1234}" \
    SOURCE_HEAD_REPO="${SOURCE_HEAD_REPO_OVERRIDE:-verJSON/example}" \
    SOURCE_REPO_FULL="${SOURCE_REPO_FULL_OVERRIDE:-verJSON/example}" \
    SOURCE_RUN_ACTOR="${SOURCE_RUN_ACTOR_OVERRIDE:-maintainer}" \
    WORKFLOW_REF="${WORKFLOW_REF_OVERRIDE:-Verjson/example/.github/workflows/$caller@refs/heads/main}" \
    bash "${ARM_SCRIPT:-$tmp/arm.sh}")
}


expect_fail(){ label="$1"; if run_arm >"$tmp/out" 2>&1; then fail "$label"; else pass "$label"; fi; }
run_preauthorize(){ bash "$tmp/preauthorize.sh"; }

if python3 - "$workflow" <<'PY'
import sys
from pathlib import Path

import yaml

workflow = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
jobs = workflow["jobs"]
steps = jobs["arm"]["steps"]
preauthorize = next(i for i, step in enumerate(steps) if step.get("name") == "Authorize hold-clearing actor before AI review admission")
assert "admin|maintain" in steps[preauthorize]["run"]
assert "role_name" in steps[preauthorize]["run"]
assert "github.event.action == 'unlabeled'" in steps[preauthorize]["if"]
assert "github.event.action == 'labeled'" in steps[preauthorize]["if"]
create = next(step for step in steps if step.get("name") == "Create exact-head authorization receipt")
assert jobs["arm"]["permissions"].get("checks") == "write"
assert create["env"].get("GH_TOKEN") == "${{ github.token }}"
assert not any(step.get("name") == "Mint dedicated authorization App token" for step in steps)
assert "--argjson app_id 15368" in create["run"] and ".app.id == $app_id" in create["run"]
assert jobs["arm"]["needs"] == ["event-policy", "app-key-policy", "validate-ai-review-key"]
assert create["env"]["APP_KEY_POLICY_RESULT"] == "${{ needs.app-key-policy.result }}"
assert create["env"]["AI_REVIEW_KEY_VALIDATION_RESULT"] == "${{ needs.validate-ai-review-key.result }}"
assert "AI_REVIEW_APP_PRIVATE_KEY" not in create["env"]
policy_guard = 'if [ "$APP_KEY_POLICY_RESULT" != success ] || [ "$AI_REVIEW_KEY_VALIDATION_RESULT" != success ]; then'
assert policy_guard in create["run"]
receipt = next(step for step in steps if step.get("name") == "Upload immutable arm receipt")
dispatch = next(step for step in steps if step.get("name") == "Dispatch trusted review after receipt publication")
assert create["run"].index("gh api --method POST") < create["run"].index(policy_guard)
assert steps.index(receipt) < steps.index(dispatch)

event_policy = jobs["event-policy"]
assert event_policy["permissions"] == {}
assert "vars.CI_LANE_UNTRUSTED" in event_policy["runs-on"]
assert 'gsub("[ _-]+";" ")' in event_policy["steps"][0]["run"]
assert 'gsub("[ _-]+";"-")' in event_policy["steps"][0]["run"]
assert jobs["app-key-policy"]["needs"] == "event-policy"
assert "needs.event-policy.outputs.run_control_plane == 'true'" in jobs["app-key-policy"]["if"]
assert jobs["arm"]["needs"] == ["event-policy", "app-key-policy", "validate-ai-review-key"]
assert "needs.event-policy.outputs.run_control_plane == 'true'" in jobs["arm"]["if"]

assert jobs["app-key-policy"]["if"] == "${{ needs.event-policy.outputs.run_control_plane == 'true' }}"
assert jobs["arm"]["if"] == "${{ always() && needs.event-policy.outputs.run_control_plane == 'true' }}"
assert "old_title_held" in jobs["event-policy"]["outputs"]
assert "new_title_held" in jobs["event-policy"]["outputs"]
assert "EVENT_TITLE_CHANGED" in jobs["event-policy"]["steps"][0]["env"]
marker = r"(^|[^ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_])DO\ NOT\ MERGE([^ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_]|$)"
assert marker in jobs["event-policy"]["steps"][0]["run"]
review_jobs = yaml.safe_load(Path(sys.argv[1]).with_name("ai-review-merge.yml").read_text())["jobs"]
assert review_jobs["title-policy"]["permissions"] == {}
assert review_jobs["preflight"]["needs"] == "title-policy"
assert "needs.title-policy.outputs.title_held != 'true'" in review_jobs["preflight"]["if"]
assert marker in review_jobs["title-policy"]["steps"][0]["run"]
for filename in ("ai-review-merge.yml", "ai-privileged-merge.yml", "gate-rearm.yml"):
    source = Path(sys.argv[1]).with_name(filename).read_text()
    assert 'ascii_upcase | test("(^|[^ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_])DO NOT MERGE([^ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_]|$)")' in source
print("workflow title-hold policy and authorization ordering pass")
PY
then
  pass "only actual title-hold transitions reach the trusted arm and token steps"
else
  fail "workflow hold-removal entry gates are incomplete"
fi

classify_control_plane() {
  : >"$GITHUB_OUTPUT"
  EVENT_ACTION="$1" EVENT_LABEL="$2" EVENT_TITLE_CHANGED="${3:-false}" \
    EVENT_OLD_TITLE="${4:-}" EVENT_NEW_TITLE="${5:-}" GITHUB_OUTPUT="$GITHUB_OUTPUT" \
    bash "$tmp/event-policy.sh"
  sed -n 's/^run_control_plane=//p' "$GITHUB_OUTPUT"
}
for label in hold HOLD 'Do__Not--Merge'; do
  if [ "$(classify_control_plane unlabeled "$label")" = true ]; then
    pass "recognized removed hold label $label keeps the trusted control plane eligible"
  else
    fail "recognized removed hold label $label was filtered out"
  fi
done
if [ "$(classify_control_plane unlabeled documentation)" = false ] \
  && [ "$(classify_control_plane edited documentation)" = false ] \
  && [ "$(classify_control_plane opened documentation)" = true ]; then
  pass "unrelated unlabeled and body-only edits skip protected jobs while normal actions remain eligible"
else
  fail "event policy did not isolate unrelated labels and title-free edits"
fi
if [ "$(classify_control_plane edited '' true 'REDO NOT MERGE: QA' 'ordinary title')" = false ] \
  && [ "$(classify_control_plane edited '' true 'DO NOT MERGER: QA' 'ordinary title')" = false ] \
  && [ "$(classify_control_plane edited '' true 'ordinary title' 'prefix DO NOT MERGE: hold')" = true ] \
  && [ "$(classify_control_plane edited '' true 'ordinary title' 'DO NOT MERGEK')" = true ] \
  && [ "$(classify_control_plane edited '' true 'DO NOT MERGE: hold' 'ordinary title')" = true ]; then
  pass "event policy detects only whole-phrase title hold transitions"
else
  fail "event policy title marker boundaries are incorrect"
fi

review_title_held() {
  : >"$GITHUB_OUTPUT"
  PR_TITLE="$1" GITHUB_OUTPUT="$GITHUB_OUTPUT" bash "$tmp/review-title-policy.sh"
  sed -n 's/^title_held=//p' "$GITHUB_OUTPUT"
}
if [ "$(review_title_held 'chore: dO nOt mErGe: QA')" = true ] \
  && [ "$(review_title_held 'REDO NOT MERGE: QA')" = false ] \
  && [ "$(review_title_held 'DO NOT MERGER: QA')" = false ] \
  && [ "$(review_title_held 'DO_NOT_MERGE')" = false ] \
  && [ "$(review_title_held 'DO NOT MERGEK')" = true ]; then
  pass "review preflight treats the hold marker as a whole phrase"
else
  fail "review preflight title marker boundaries are incorrect"
fi
for label in ai-review re-review hold 'Do__Not--Merge'; do
  if [ "$(classify_control_plane labeled "$label")" = true ]; then
    pass "recognized added label $label retains its existing workflow path"
  else
    fail "recognized added label $label was filtered out"
  fi
done
if [ "$(classify_control_plane labeled documentation)" = false ]; then
  pass "unrelated label additions skip protected jobs before App-token mint"
else
  fail "unrelated label addition reached protected jobs"
fi

export EVENT_ACTION=ready_for_review EVENT_OLD_TITLE_HELD=false EVENT_NEW_TITLE_HELD=false REQUEST_ACTOR=maintainer
: >"$CALLS"
for permission in read triage write push; do
  : >"$GITHUB_ENV"
  export ACTOR_PERMISSION="$permission"
  if run_preauthorize >"$tmp/out" 2>&1; then
    fail "$permission actor cleared a draft hold"
  elif grep -q 'workflow run ai-privileged-merge.yml\|workflow run ai-review-merge.yml' "$CALLS"; then
    fail "$permission actor reached privileged dispatch"
  else
    pass "$permission actor is rejected before App-token mint"
  fi
done

for permission in maintain admin; do
  : >"$GITHUB_ENV"
  : >"$CALLS"
  export ACTOR_PERMISSION="$permission"
  if [ "$permission" = maintain ]; then base_permission='write'; else base_permission='admin'; fi
  if run_preauthorize >"$tmp/out" 2>&1 \
    && grep -q "^HOLD_CLEAR_ACTOR_PERMISSION=$permission$" "$GITHUB_ENV" \
    && grep -q "^PERMISSION_LOOKUP maintainer permission=$base_permission role_name=$permission$" "$CALLS"; then
    pass "$permission actor is authorized to clear a draft hold"
  else
    fail "$permission actor could not clear a draft hold"
  fi
done

export ACTOR_PERMISSION=maintain GH_PERMISSION_FAIL=true
if run_preauthorize >"$tmp/out" 2>&1; then
  fail "permission API failure cleared a draft hold"
elif grep -q 'workflow run ai-privileged-merge.yml\|workflow run ai-review-merge.yml' "$CALLS"; then
  fail "permission API failure reached privileged dispatch"
else
  pass "permission API failure remains fail-closed before App-token mint"
fi
unset GH_PERMISSION_FAIL

export EVENT_ACTION=unlabeled EVENT_LABEL=hold ACTOR_PERMISSION=triage
: >"$GITHUB_ENV"
: >"$CALLS"
if run_preauthorize >"$tmp/out" 2>&1; then
  fail "triage actor cleared a hold label"
elif grep -q 'workflow run ai-privileged-merge.yml\|workflow run ai-review-merge.yml' "$CALLS"; then
  fail "triage actor reached privileged dispatch after removing a hold label"
else
  pass "triage actor is denied hold-label removal before App-token mint"
fi

for label in hold 'Do__Not--Merge'; do
  for permission in maintain admin; do
    : >"$GITHUB_ENV"
    export ACTOR_PERMISSION="$permission" EVENT_LABEL="$label"
    if run_preauthorize >"$tmp/out" 2>&1 && grep -q "^HOLD_CLEAR_ACTOR_PERMISSION=$permission$" "$GITHUB_ENV"; then
      pass "$permission actor can clear recognized hold label $label"
    else
      fail "$permission actor could not clear recognized hold label $label"
    fi
  done
done

export EVENT_LABEL=documentation ACTOR_PERMISSION=triage
: >"$GITHUB_ENV"
: >"$CALLS"
if run_preauthorize >"$tmp/out" 2>&1 && [ ! -s "$GITHUB_ENV" ] && ! grep -q 'collaborators/' "$CALLS"; then
  pass "non-hold label removal skips hold authorization lookup"
else
  fail "non-hold label removal changed its authorization behavior"
fi

export EVENT_LABEL=hold ACTOR_PERMISSION=maintain GH_PERMISSION_FAIL=true
if run_preauthorize >"$tmp/out" 2>&1; then
  fail "permission API failure allowed hold-label removal"
elif grep -q 'workflow run ai-privileged-merge.yml\|workflow run ai-review-merge.yml' "$CALLS"; then
  fail "permission API failure reached privileged dispatch for hold-label removal"
else
  pass "hold-label permission API failure fails closed before App-token mint"
fi
unset GH_PERMISSION_FAIL

export EVENT_ACTION=edited EVENT_OLD_TITLE_HELD=true EVENT_NEW_TITLE_HELD=false
export REQUEST_ACTOR=pr-author PRIVILEGED_ACTOR=other-admin ACTOR_PERMISSION=triage
for permission in triage write; do
  : >"$GITHUB_ENV"
  : >"$CALLS"
  export ACTOR_PERMISSION="$permission"
  if run_preauthorize >"$tmp/out" 2>&1; then
    fail "$permission actor removed a title hold"
  elif ! grep -q "^PERMISSION_LOOKUP pr-author permission=" "$CALLS" \
      || grep -q '^PERMISSION_LOOKUP other-admin ' "$CALLS" \
      || grep -q 'workflow run ai-privileged-merge.yml\|workflow run ai-review-merge.yml' "$CALLS" \
      || [ -s "$GITHUB_ENV" ]; then
    fail "$permission title-hold removal did not fail before token mint for the event actor"
  else
    pass "$permission event actor is checked by login and denied title-hold removal before App-token mint"
  fi
done

export REQUEST_ACTOR=maintainer ACTOR_PERMISSION=maintain
: >"$GITHUB_ENV"
: >"$CALLS"
if run_preauthorize >"$tmp/out" 2>&1 && grep -q '^HOLD_CLEAR_ACTOR_PERMISSION=maintain$' "$GITHUB_ENV"; then
  pass "a maintainer can clear an existing title hold"
else
  fail "a real title-hold removal was not authorized"
fi

for label in ai-review re-review; do
  export EVENT_ACTION=labeled EVENT_LABEL="$label" REQUEST_ACTOR=pr-author
  for permission in triage write; do
    : >"$GITHUB_ENV"
    : >"$CALLS"
    export ACTOR_PERMISSION="$permission"
    if run_preauthorize >"$tmp/out" 2>&1; then
      fail "$permission actor added $label label"
    elif ! grep -q "^PERMISSION_LOOKUP pr-author permission=" "$CALLS" \
        || grep -q '^PERMISSION_LOOKUP other-admin ' "$CALLS" \
        || grep -q 'workflow run ai-privileged-merge.yml\|workflow run ai-review-merge.yml' "$CALLS" \
        || [ -s "$GITHUB_ENV" ]; then
      fail "$permission $label actor was not rejected before App-token mint"
    else
      pass "$permission actor rejected for $label before App-token mint"
    fi
  done
  export REQUEST_ACTOR=maintainer
  for permission in maintain admin; do
    : >"$GITHUB_ENV"
    : >"$CALLS"
    export ACTOR_PERMISSION="$permission"
    if run_preauthorize >"$tmp/out" 2>&1 \
      && grep -q "^PERMISSION_LOOKUP maintainer permission=" "$CALLS" \
      && grep -q "role_name=$permission$" "$CALLS"; then
      pass "$permission actor authorized for $label before App-token mint"
    else
      fail "$permission actor could not authorize $label before App-token mint"
    fi
  done
done

export EVENT_ACTION=edited REQUEST_ACTOR=maintainer ACTOR_PERMISSION=maintain
title_is_held() {
  [[ "${1^^}" =~ (^|[^ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_])DO\ NOT\ MERGE([^ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_]|$) ]]
}
for old_title in 'chore: DO NOT MERGE QA' 'chore: QA' 'REDO NOT MERGE QA' 'DO NOT MERGER QA'; do
  for new_title in 'chore: DO NOT MERGE QA' 'chore: new title' 'REDO NOT MERGE QA' 'DO NOT MERGER QA'; do
    old_held=false; new_held=false
    title_is_held "$old_title" && old_held=true
    title_is_held "$new_title" && new_held=true
    [ "$old_held" = "$new_held" ] || continue
    export EVENT_OLD_TITLE_HELD="$old_held" EVENT_NEW_TITLE_HELD="$new_held"
    if run_preauthorize >"$tmp/out" 2>&1; then
      fail "non-clearing title edit was authorized: $old_title -> $new_title"
    else
      pass "title edit without a hold-state transition is rejected before App-token mint"
    fi
  done
done

export EVENT_OLD_TITLE_HELD=false EVENT_NEW_TITLE_HELD=true
: >"$GITHUB_ENV"
: >"$CALLS"
if run_preauthorize >"$tmp/out" 2>&1 && [ ! -s "$GITHUB_ENV" ] && ! grep -q 'collaborators/' "$CALLS"; then
  pass "adding a mixed-case title hold skips hold-removal authorization"
else
  fail "adding a title hold attempted hold-removal authorization"
fi

write_hold
jq '.labels=[] | .title="DO NOT MERGE: hold"' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
export EVENT_ACTION=edited EVENT_OLD_TITLE_HELD=false EVENT_NEW_TITLE_HELD=true EVENT_LABEL=''
export REQUEST_ACTOR=maintainer ACTOR_PERMISSION=triage
: >"$CALLS"
if run_arm >"$tmp/out" 2>&1 \
  && grep -q 'disablePullRequestAutoMerge' "$CALLS" \
  && ! grep -q 'collaborators/\|workflow run' "$CALLS"; then
  pass "adding a title hold disables auto-merge without hold-clear authorization"
else
  fail "adding a title hold did not disable auto-merge safely"
fi

write_hold
export EVENT_ACTION=labeled EVENT_LABEL=hold
if run_arm >"$tmp/out" 2>&1 \
  && grep -q 'disablePullRequestAutoMerge' "$CALLS" \
  && ! grep -q 'workflow run ai-review-merge.yml' "$CALLS"; then
  pass "adding a lowercase hold label disables auto-merge without review dispatch"
else
  fail "adding a lowercase hold label did not disable auto-merge"
fi

write_hold
jq '.labels=[{"name":"ai-review"}]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
printf '{}\n' >"$LATEST_FILE"
export EVENT_ACTION=labeled EVENT_LABEL=ai-review ACTOR_PERMISSION=maintain
WORKFLOW_REF=Verjson/example/.github/workflows/ai-review-label-rearm.yml@refs/heads/main
if APP_KEY_POLICY_RESULT=failure run_arm >"$tmp/out" 2>&1; then
  fail "failed App-key policy was accepted"
elif grep -q 'AI review environment policy did not succeed' "$tmp/out" \
  && grep -q 'failure_reason=ai_review_environment_policy' "$GITHUB_OUTPUT" \
  && grep -q -- '--method POST repos/Verjson/example/check-runs' "$CALLS" \
  && ! grep -q 'workflow run ai-review-merge.yml' "$CALLS"; then
  pass "failed App-key policy completes a failure-visible check before review dispatch"
else
  fail "failed App-key policy did not fail visibly before review dispatch"
fi
write_hold; printf '{"data":null,"errors":[{"message":"denied"}]}\n' >"$GRAPHQL_FILE"
expect_fail "HTTP-200 GraphQL errors fail the hold closed"
write_hold; printf '{"data":{"disablePullRequestAutoMerge":{"pullRequest":{"id":"wrong"}}}}\n' >"$GRAPHQL_FILE"
expect_fail "a mutation response for another PR fails closed"
write_hold; printf '{"id":"PR_id","autoMergeRequest":{"enabledAt":"still-on"}}\n' >"$DISABLED_META_FILE"
expect_fail "auto-merge remaining enabled after mutation fails closed"

write_terminal_hold() {
  : >"$CALLS"
  : >"$GITHUB_OUTPUT"
  printf '{}\n' >"$LATEST_FILE"
  jq -nc --arg head "$head_sha" \
    '{id:"PR_id",state:"OPEN",isDraft:false,title:"change",labels:[],headRefOid:$head,headRepositoryOwner:{login:"Verjson"},autoMergeRequest:null}' \
    >"$META_FILE"
  export EVENT_ACTION=synchronize EVENT_LABEL='' EVENT_OLD_TITLE_HELD=false EVENT_NEW_TITLE_HELD=false
}

app_id_mismatch_is_terminalized() {
  local arm_script="$1"
  write_terminal_hold
  jq '.labels=[{"name":"ai-review"}]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
  export EVENT_ACTION=labeled EVENT_LABEL=ai-review ACTOR_PERMISSION=maintain
  if ARM_SCRIPT="$arm_script" CREATED_CHECK_APP_ID=9999 run_arm >"$tmp/out" 2>&1; then return 1; fi
  grep -q '^check_id=9100$' "$GITHUB_OUTPUT" \
    && ! grep -q 'workflow run ai-review-merge.yml' "$CALLS"
}

if app_id_mismatch_is_terminalized "$tmp/arm.sh"; then
  pass "a post-creation App-ID mismatch fails closed for the always-run finalizer"
else
  fail "a post-creation App-ID mismatch did not fail closed before dispatch"
fi

sed '/echo "check_id=\$check_id"/d' "$tmp/arm.sh" >"$tmp/arm-no-early-check-id.sh"
if app_id_mismatch_is_terminalized "$tmp/arm-no-early-check-id.sh"; then
  fail "removing the early check-ID export escaped the terminal-state mutation test"
else
  pass "the terminal-state mutation test detects removal of the early check-ID export"
fi

for signal in do-not-merge-label do_not_merge-label title draft; do
  write_terminal_hold
  case "$signal" in
    do-not-merge-label) jq '.labels=[{"name":"do-not-merge"}]' "$META_FILE" >"$tmp/x" ;;
    do_not_merge-label) jq '.labels=[{"name":"Do_Not_Merge"}]' "$META_FILE" >"$tmp/x" ;;
    title) jq '.title="chore: DO NOT MERGE until QA"' "$META_FILE" >"$tmp/x" ;;
    draft) jq '.isDraft=true' "$META_FILE" >"$tmp/x" ;;
  esac
  mv "$tmp/x" "$META_FILE"
  if run_arm >"$tmp/out" 2>&1 && ! grep -q 'check-runs\|workflow run' "$CALLS"; then
    pass "$signal remains a terminal arm no-op"
  else
    fail "$signal reached authorization or dispatch"
  fi
done

for malformed in truncated labels-not-array label-name-not-string title-not-string; do
  write_terminal_hold
  case "$malformed" in
    truncated) printf '{"labels":[{"name":"hold"}],"title":"change","isDraft":fal' >"$META_FILE" ;;
    labels-not-array) jq '.labels="hold"' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE" ;;
    label-name-not-string) jq '.labels=[{"name":{"value":"hold"}}]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE" ;;
    title-not-string) jq '.title=42' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE" ;;
  esac
  expect_fail "$malformed hold metadata fails closed before authorization"
  ! grep -q 'check-runs\|workflow run' "$CALLS" \
    && pass "$malformed hold metadata cannot authorize or dispatch" \
    || fail "$malformed hold metadata reached authorization or dispatch"
done

for unreadable in empty null missing-hold-fields; do
  write_terminal_hold
  case "$unreadable" in
    empty) : >"$META_FILE" ;;
    null) printf 'null\n' >"$META_FILE" ;;
    missing-hold-fields)
      jq 'del(.labels, .title, .isDraft)' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE" ;;
  esac
  expect_fail "$unreadable PR metadata fails closed before authorization"
  ! grep -q 'check-runs\|workflow run' "$CALLS" \
    && pass "$unreadable PR metadata cannot authorize or dispatch" \
    || fail "$unreadable PR metadata reached authorization or dispatch"
done

write_terminal_hold
export GH_VIEW_FAIL=true
expect_fail "an arm metadata API failure fails closed"
unset GH_VIEW_FAIL
! grep -q 'check-runs\|workflow run' "$CALLS" \
  && pass "an arm metadata API failure cannot authorize or dispatch" \
  || fail "an arm metadata API failure reached authorization or dispatch"

for terminal_state in CLOSED MERGED; do
  write_terminal_hold
  jq --arg state "$terminal_state" '.state=$state' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
  if run_arm >"$tmp/out" 2>&1 && ! grep -q 'check-runs\|workflow run' "$CALLS"; then
    pass "$terminal_state PR is a terminal arm no-op"
  else
    fail "$terminal_state PR reached authorization or dispatch"
  fi
done

write_repromotion() {
  : >"$CALLS"
  jq -nc --arg head "$head_sha" '{id:"PR_id",state:"OPEN",isDraft:false,title:"change",labels:[],headRefOid:$head,headRepositoryOwner:{login:"Verjson"},autoMergeRequest:null}' >"$META_FILE"
  jq -nc --arg head "$head_sha" '{id:9001,status:"completed",conclusion:"success",external_id:"ai-review:v1:Verjson/example:7:0123456789abcdef0123456789abcdef01234567:7001:2:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",details_url:"https://github.com/Verjson/example/runs/9001",head_sha:$head,app:{id:15368,slug:"github-actions"}}' >"$LATEST_FILE"
  export EVENT_ACTION=unlabeled EVENT_LABEL=hold EVENT_OLD_TITLE_HELD=false EVENT_NEW_TITLE_HELD=false HOLD_CLEAR_ACTOR_PERMISSION=maintain RECEIPT_COUNT=1
}
write_repromotion
if run_arm >"$tmp/out" 2>&1 && grep -q 'workflow run ai-privileged-merge.yml' "$CALLS" \
  && grep -qF -- "-f review_policy=$receipt_policy" "$CALLS" && ! grep -q 'workflow run ai-review-merge.yml' "$CALLS"; then
  pass "hold removal reuses a live receipt without another paid review"
else fail "hold removal did not reuse authorization: $(tail -1 "$tmp/out")"; fi

write_repromotion
jq '.external_id |= sub(":7:"; ":8:")' "$LATEST_FILE" >"$tmp/x" && mv "$tmp/x" "$LATEST_FILE"
expect_fail "re-promotion refuses an approved check whose external_id names another pull request (#1648)"
! grep -q 'workflow run ai-privileged-merge.yml' "$CALLS" \
  && pass "a foreign-identity approved check never dispatches privileged merge" \
  || fail "a foreign-identity approved check dispatched privileged merge"

write_repromotion
jq 'del(.external_id)' "$LATEST_FILE" >"$tmp/x" && mv "$tmp/x" "$LATEST_FILE"
expect_fail "re-promotion refuses an approved check that carries no arm identity (#1648)"
! grep -q 'workflow run ai-privileged-merge.yml' "$CALLS" \
  && pass "an identity-less approved check never dispatches privileged merge" \
  || fail "an identity-less approved check dispatched privileged merge"

write_repromotion
jq '.external_id |= sub("a{64}$"; "not-a-nonce")' "$LATEST_FILE" >"$tmp/x" && mv "$tmp/x" "$LATEST_FILE"
expect_fail "re-promotion refuses an approved check whose external_id carries a malformed nonce (#1648)"
! grep -q 'workflow run ai-privileged-merge.yml' "$CALLS" \
  && pass "a malformed-nonce approved check never dispatches privileged merge" \
  || fail "a malformed-nonce approved check dispatched privileged merge"

for release_case in hold-label normalized-label ready-for-review edited-title; do
  write_repromotion
  case "$release_case" in
    hold-label) export EVENT_ACTION=unlabeled EVENT_LABEL=hold EVENT_OLD_TITLE_HELD=false EVENT_NEW_TITLE_HELD=false HOLD_CLEAR_ACTOR_PERMISSION=maintain ;;
    normalized-label) export EVENT_ACTION=unlabeled EVENT_LABEL='Do__Not--Merge' EVENT_OLD_TITLE_HELD=false EVENT_NEW_TITLE_HELD=false HOLD_CLEAR_ACTOR_PERMISSION=admin ;;
    ready-for-review) export EVENT_ACTION=ready_for_review EVENT_LABEL='' EVENT_OLD_TITLE_HELD=false EVENT_NEW_TITLE_HELD=false HOLD_CLEAR_ACTOR_PERMISSION=maintain ;;
    edited-title) export EVENT_ACTION=edited EVENT_LABEL='' EVENT_OLD_TITLE_HELD=true EVENT_NEW_TITLE_HELD=false HOLD_CLEAR_ACTOR_PERMISSION=maintain ;;
  esac
  if run_arm >"$tmp/out" 2>&1 && grep -q 'workflow run ai-privileged-merge.yml' "$CALLS" \
      && ! grep -q 'workflow run ai-review-merge.yml' "$CALLS"; then
    pass "$release_case reuses exact-head authorization without another paid review"
  else
    fail "$release_case did not follow the receipt-preserving re-arm path"
  fi
done

write_repromotion
export EVENT_ACTION=unlabeled EVENT_LABEL=hold HOLD_CLEAR_ACTOR_PERMISSION=triage
if run_arm >"$tmp/out" 2>&1; then
  fail "triage actor was allowed to clear a hold label"
elif grep -q 'commits/.*/check-runs\|workflow run' "$CALLS"; then
  fail "triage actor clearing a hold label reached receipt lookup or dispatch"
else
  pass "triage actor cannot use hold-label removal for receipt promotion"
fi

write_repromotion
export EVENT_ACTION=ready_for_review EVENT_LABEL='' HOLD_CLEAR_ACTOR_PERMISSION=triage
if run_arm >"$tmp/out" 2>&1; then
  fail "unauthorized ready-for-review actor was silently accepted"
elif grep -q 'workflow run ai-privileged-merge.yml\|workflow run ai-review-merge.yml' "$CALLS"; then
  fail "unauthorized ready-for-review actor reached privileged dispatch"
else
  pass "unauthorized ready-for-review actor cannot dispatch privileged work"
fi

write_repromotion
jq '.title="chore: DO NOT MERGE QA"' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
export EVENT_ACTION=edited EVENT_OLD_TITLE_HELD=true EVENT_NEW_TITLE_HELD=false HOLD_CLEAR_ACTOR_PERMISSION=maintain
if run_arm >"$tmp/out" 2>&1 && ! grep -q 'commits/.*/check-runs\|workflow run' "$CALLS"; then
  pass "title edit that retains the authoritative hold exits before receipt lookup"
else
  fail "title edit that retains the hold reached authorization dispatch"
fi

write_repromotion
for rejected in replay stale-head actor-mismatch; do
  write_repromotion
  export EVENT_ACTION=ready_for_review EVENT_LABEL=''
  case "$rejected" in
    replay) export GITHUB_RUN_ATTEMPT=2 ;;
    stale-head) export EVENT_HEAD_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ;;
    actor-mismatch) export REQUEST_ACTOR=other ;;
  esac
  expect_fail "lifecycle delivery rejects $rejected"
  if grep -q 'workflow run\|--method POST repos/Verjson/example/check-runs' "$CALLS"; then
    fail "rejected lifecycle delivery reached authorization or dispatch"
  else pass "rejected lifecycle delivery stops before authority"; fi
  export GITHUB_RUN_ATTEMPT=1 EVENT_HEAD_SHA="$head_sha" REQUEST_ACTOR=maintainer
done
write_repromotion
export EVENT_ACTION=converted_to_draft EVENT_LABEL=''
printf '{"data":{"disablePullRequestAutoMerge":{"pullRequest":{"id":"PR_id"}}}}\n' >"$GRAPHQL_FILE"
printf '{"id":"PR_id","autoMergeRequest":null}\n' >"$DISABLED_META_FILE"
jq '.isDraft=true | .autoMergeRequest={enabledAt:"now"}' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
if run_arm >"$tmp/out" 2>&1 && grep -q 'disablePullRequestAutoMerge' "$CALLS" && ! grep -q 'workflow run' "$CALLS"; then
  pass "local converted-to-draft delivery disables auto-merge without dispatch"
else fail "local draft transition did not disable auto-merge"; fi
write_repromotion
export EVENT_ACTION=unlabeled EVENT_LABEL=documentation EVENT_OLD_TITLE_HELD=false EVENT_NEW_TITLE_HELD=false
if run_arm >"$tmp/out" 2>&1 && ! grep -q 'commits/.*/check-runs\|workflow run' "$CALLS"; then
  pass "unrelated label removal exits before authorization lookup or dispatch"
else
  fail "unrelated label removal reached authorization lookup or dispatch"
fi
sed "s|review_policy=\"\$(jq -er .*|review_policy=\"$substitute_policy\"|" "$tmp/arm.sh" >"$tmp/arm-substituted.sh"
write_repromotion
if ARM_SCRIPT="$tmp/arm-substituted.sh" run_arm >"$tmp/out" 2>&1 \
  && grep -qF -- "-f review_policy=$substitute_policy" "$CALLS" \
  && ! grep -qF -- "-f review_policy=$receipt_policy" "$CALLS"; then
  pass "valid constant substitution is detected instead of matching the recovered receipt policy"
else fail "constant substitution mutation escaped the exact-value assertion"; fi
write_repromotion; export RECEIPT_COUNT=0
expect_fail "expired receipt requires explicit admin recovery"
! grep -q 'workflow run ai-review-merge.yml' "$CALLS" \
  && pass "expired receipt never automatically dispatches another paid review" \
  || fail "expired receipt dispatched paid review"

write_repromotion
jq '.status="completed" | .conclusion="failure"' "$LATEST_FILE" >"$tmp/x" && mv "$tmp/x" "$LATEST_FILE"
if run_arm >"$tmp/out" 2>&1 && grep -q 're-review' "$CALLS" && ! grep -q 'workflow run ai-review-merge.yml' "$CALLS"; then
  pass "failed authorization requires an explicit paid re-review decision"
else fail "failed authorization hold-clear guidance is missing"; fi

write_repromotion
jq '.status="in_progress" | .conclusion=null' "$LATEST_FILE" >"$tmp/x" && mv "$tmp/x" "$LATEST_FILE"
if run_arm >"$tmp/out" 2>&1 && grep -q 'still in progress' "$CALLS" && ! grep -q 'workflow run ai-review-merge.yml' "$CALLS"; then
  pass "receipt-proven pending authorization tells maintainers to wait without redispatch"
else fail "pending authorization was not handled spend-safely"; fi

write_repromotion
jq '.status="in_progress" | .conclusion=null' "$LATEST_FILE" >"$tmp/x" && mv "$tmp/x" "$LATEST_FILE"
export RECEIPT_COUNT=0
expect_fail "unproven pending authorization requires admin recovery"
grep -q 'administrator must recover' "$CALLS" \
  && pass "unproven pending authorization emits recovery guidance" \
  || fail "unproven pending authorization guidance missing"
! grep -q 'workflow run ai-review-merge.yml' "$CALLS" \
  && pass "pending recovery never automatically dispatches another paid review" \
  || fail "pending recovery dispatched a paid review"

write_repromotion
jq '.status="in_progress" | .conclusion=null | .external_id |= sub(":7:"; ":8:")' "$LATEST_FILE" >"$tmp/x" && mv "$tmp/x" "$LATEST_FILE"
expect_fail "a pending check whose external_id names another pull request is not receipt-proven (#1648)"
grep -q 'administrator must recover' "$CALLS" && ! grep -q 'workflow run ai-review-merge.yml' "$CALLS" \
  && pass "a foreign-identity pending check routes to admin recovery without a paid review" \
  || fail "a foreign-identity pending check was not routed to admin recovery"

write_repromotion
jq '.status="in_progress" | .conclusion=null | .app={id:4528902,slug:"ai-review-authorization"}' "$LATEST_FILE" >"$tmp/x" && mv "$tmp/x" "$LATEST_FILE"
export RECEIPT_COUNT=0
run_arm >"$tmp/out" 2>&1 || true
if ! grep -q -- '--method PATCH repos/Verjson/example/check-runs/9001' "$CALLS" \
  && ! grep -q -- '--method POST repos/Verjson/example/check-runs' "$CALLS" \
  && ! grep -q 'workflow run ai-review-merge.yml' "$CALLS"; then
  pass "legacy review-App-owned check cannot be mutated or trigger duplicate review dispatch"
else
  fail "legacy review-App-owned check reached mutation or duplicate review dispatch"
fi

: >"$CALLS"
jq -nc --arg head "$head_sha" '{id:"PR_id",state:"OPEN",isDraft:false,title:"change",labels:[{name:"re-review"}],headRefOid:$head,headRepositoryOwner:{login:"Verjson"},autoMergeRequest:null}' >"$META_FILE"
export EVENT_ACTION=labeled EVENT_LABEL=re-review REQUEST_ACTOR=maintainer ACTOR_PERMISSION=triage
export EVENT_NAME=pull_request_target
export REREVIEW_PROVIDER=openai REREVIEW_MODEL=gpt-5.6-luna REREVIEW_BUDGET_USD=1.00
expect_fail "triage actor cannot authorize a paid re-review"
! grep -q 'check-runs' "$CALLS" && pass "unauthorized actor fails before receipt or paid dispatch" || fail "unauthorized actor reached authorization creation"

: >"$CALLS"
jq '.labels=[]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
export ACTOR_PERMISSION=maintain
expect_fail "withdrawn re-review label fails authoritative current-state validation"
! grep -q -- '--method POST repos/Verjson/example/check-runs\|workflow run ai-review-merge.yml' "$CALLS" \
  && pass "withdrawn re-review cannot create a receipt check or dispatch" \
  || fail "withdrawn re-review reached authorization or paid dispatch"

# An `ai-review` label may be added after the ordinary human-path authorization
# already completed for this head. That explicit, maintainer-authorized opt-in
# must create a fresh receipt instead of being mistaken for a duplicate event.
: >"$CALLS"; : >"$GITHUB_OUTPUT"
jq -nc --arg head "$head_sha" '{id:"PR_id",state:"OPEN",isDraft:false,title:"change",labels:[{name:"ai-review"}],headRefOid:$head,headRepositoryOwner:{login:"Verjson"},autoMergeRequest:null}' >"$META_FILE"
jq -nc --arg head "$head_sha" '{id:9001,status:"completed",conclusion:"success",external_id:"ai-review:v1:Verjson/example:7:0123456789abcdef0123456789abcdef01234567:7001:2:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",details_url:"https://github.com/Verjson/example/runs/9001",head_sha:$head}' >"$LATEST_FILE"
export EVENT_ACTION=labeled EVENT_LABEL=ai-review REQUEST_ACTOR=maintainer ACTOR_PERMISSION=maintain
export EVENT_NAME=pull_request_target
export REVIEW_AUTHORITY=human PRIMARY_PROVIDER=deepseek PRIMARY_MODEL=deepseek-v4-pro PRIMARY_BUDGET_USD=5.00
export PRIMARY_FALLBACK_MODEL=deepseek-v4-flash PRIMARY_FALLBACK_BUDGET_USD=5.00
if run_arm >"$tmp/out" 2>&1 && grep -q -- '--method POST repos/Verjson/example/check-runs --input -' "$CALLS" \
  && grep -q '^check_id=9100$' "$GITHUB_OUTPUT" \
  && policy_envelope="$(sed -n 's/^review_policy=//p' "$GITHUB_OUTPUT")" \
  && policy_json="$(python3 "$root/scripts/ci-gate/review-policy-envelope.py" decode "$policy_envelope")" \
  && [ "$(jq -r '.actor + ":" + .actor_permission' <<<"$policy_json")" = maintainer:maintain ]; then
  pass "post-open ai-review opt-in bypasses the existing human-path authorization"
else
  fail "post-open ai-review opt-in lost its verified actor permission or failed before receipt creation"
fi

# Persistent label state is not a delivery and must never be reusable authority.
: >"$CALLS"; : >"$GITHUB_OUTPUT"
sync_opt_in_temp="$tmp/sync-opt-in"; mkdir "$sync_opt_in_temp"; export RUNNER_TEMP="$sync_opt_in_temp"
export EVENT_NAME=pull_request_target EVENT_ACTION=synchronize EVENT_LABEL='' REQUEST_ACTOR=pusher ACTOR_PERMISSION=maintain
if run_arm >"$tmp/out" 2>&1 \
  && ! grep -q 'collaborators/maintainer/permission\|--method POST repos/Verjson/example/check-runs' "$CALLS"; then
  pass "synchronize cannot promote persistent ai-review label state into authority"
else
  fail "synchronize reused persistent ai-review label state"
fi
export RUNNER_TEMP="$tmp"

# A repository-level provider choice overrides the inherited organization
# DeepSeek policy as a unit. Stale Pro->Flash fallback variables must not make
# Anthropic/OpenAI invalid or leak into their receipt policy.
: >"$CALLS"; : >"$GITHUB_OUTPUT"
provider_temp="$tmp/provider-anthropic"; mkdir "$provider_temp"; export RUNNER_TEMP="$provider_temp"
export EVENT_NAME=pull_request_target EVENT_ACTION=synchronize EVENT_LABEL=''
jq '.labels=[]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
printf '{}\n' >"$LATEST_FILE"
export PRIMARY_PROVIDER=anthropic PRIMARY_MODEL=auto PRIMARY_BUDGET_USD=auto
export PRIMARY_FALLBACK_MODEL=deepseek-v4-flash PRIMARY_FALLBACK_BUDGET_USD=5.00
if run_arm >"$tmp/out" 2>&1 \
  && policy_envelope="$(sed -n 's/^review_policy=//p' "$GITHUB_OUTPUT")" \
  && policy_json="$(python3 "$root/scripts/ci-gate/review-policy-envelope.py" decode "$policy_envelope")" \
  && [ "$(jq -r '.provider + ":" + .fallback_model + ":" + .fallback_budget_usd' <<<"$policy_json")" = 'anthropic::' ]; then
  pass "repository Anthropic override clears inherited DeepSeek fallback policy"
else
  fail "repository Anthropic override retained inherited DeepSeek fallback policy"
fi

: >"$CALLS"; : >"$GITHUB_OUTPUT"
provider_temp="$tmp/provider-openai"; mkdir "$provider_temp"; export RUNNER_TEMP="$provider_temp"
jq '.labels=[{"name":"re-review"}]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
export EVENT_ACTION=labeled EVENT_LABEL=re-review REQUEST_ACTOR=maintainer ACTOR_PERMISSION=maintain
export EVENT_NAME=pull_request_target
export REREVIEW_PROVIDER=openai REREVIEW_MODEL=gpt-5.6-luna REREVIEW_BUDGET_USD=1.00
export REREVIEW_FALLBACK_MODEL=deepseek-v4-flash REREVIEW_FALLBACK_BUDGET_USD=5.00
if run_arm >"$tmp/out" 2>&1 \
  && policy_envelope="$(sed -n 's/^review_policy=//p' "$GITHUB_OUTPUT")" \
  && policy_json="$(python3 "$root/scripts/ci-gate/review-policy-envelope.py" decode "$policy_envelope")" \
  && [ "$(jq -r '.provider + ":" + .fallback_model + ":" + .fallback_budget_usd' <<<"$policy_json")" = 'openai::' ]; then
  pass "repository OpenAI override clears inherited DeepSeek fallback policy"
else
  fail "repository OpenAI override retained inherited DeepSeek fallback policy"
fi
export RUNNER_TEMP="$tmp"

# GitHub can omit a label-triggered pull_request_target delivery entirely. A
# maintainer can rerun a prior exact-head arm attempt; attempt 2 must bypass
# same-head deduplication only after the arm re-reads current PR/head state, and
# the new receipt must bind that run attempt. This does not simulate or claim a
# repaired label delivery.
: >"$CALLS"; : >"$GITHUB_OUTPUT"
jq -nc --arg head "$head_sha" '{id:"PR_id",state:"OPEN",isDraft:false,title:"change",labels:[{name:"re-review"}],headRefOid:$head,headRepositoryOwner:{login:"Verjson"},autoMergeRequest:null}' >"$META_FILE"
recovery_temp="$tmp/recovery"
mkdir "$recovery_temp"
export EVENT_NAME=pull_request_target EVENT_ACTION=labeled EVENT_LABEL=re-review REQUEST_ACTOR=maintainer ACTOR_PERMISSION=maintain GITHUB_RUN_ATTEMPT=2 RUNNER_TEMP="$recovery_temp"
expect_fail "rerun cannot replay an explicit re-review delivery"
! grep -q -- '--method POST repos/Verjson/example/check-runs' "$CALLS" \
  && pass "replayed delivery fails before authorization creation" \
  || fail "replayed delivery created authorization"

: >"$CALLS"; : >"$GITHUB_OUTPUT"
jq '.labels=[]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
repeated_recovery_temp="$tmp/repeated-recovery"
mkdir "$repeated_recovery_temp"
export GITHUB_RUN_ATTEMPT=3 RUNNER_TEMP="$repeated_recovery_temp"
expect_fail "later rerun remains fail-closed after label consumption"
export GITHUB_RUN_ATTEMPT=1 RUNNER_TEMP="$tmp"

: >"$CALLS"; : >"$GITHUB_OUTPUT"
jq '.labels=[{"name":"ai-review"}]' "$META_FILE" >"$tmp/x" && mv "$tmp/x" "$META_FILE"
export EVENT_ACTION=labeled EVENT_LABEL=ai-review ACTOR_PERMISSION=triage PR_EDIT_FAIL=true
export EVENT_NAME=pull_request_target
expect_fail "unauthorized ai-review label fails even when cleanup cannot remove it"
: >"$CALLS"
export EVENT_NAME=pull_request_target EVENT_ACTION=synchronize EVENT_LABEL='' REQUEST_ACTOR=pusher
sync_retained_temp="$tmp/sync-retained"; mkdir "$sync_retained_temp"; export RUNNER_TEMP="$sync_retained_temp"
if run_arm >"$tmp/out" 2>&1 \
  && ! grep -q 'collaborators/maintainer/permission' "$CALLS" \
  && ! grep -q '^explicit_ai_review=true$' "$GITHUB_OUTPUT"; then
  pass "retained unauthorized label cannot become explicit review authority"
else
  fail "retained unauthorized label influenced the synchronized authorization policy"
fi
export RUNNER_TEMP="$tmp"
unset PR_EDIT_FAIL

if python3 - "$workflow" <<'PYTEST'
import sys
from pathlib import Path
import yaml

workflow = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
create = next(step for step in workflow["jobs"]["arm"]["steps"] if step.get("name") == "Create exact-head authorization receipt")
run = create["run"]
assert '--arg review_policy "$review_policy" --arg delivery_event "$EVENT_ACTION"' in run
assert '{schema:(if $source_bound then 2 else 1 end)' in run
assert '. + {delivery_event:$delivery_event,delivery_actor:$delivery_actor,' in run
PYTEST
then
  pass "source-bound receipts record lifecycle action metadata"
else
  fail "source-bound lifecycle receipt metadata contract missing"
fi

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."; exit 1
