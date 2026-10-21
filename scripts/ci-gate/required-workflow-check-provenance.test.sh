#!/usr/bin/env bash
# Terminal promotion accepts a trusted check produced by an organization
# ruleset-required workflow only from provenance it reads from GitHub (#1861,
# ADR 0081 2026-10-21 correction): the run is a required-workflow run, the live
# organization ruleset pins exactly one revision of the declared path in
# Verjson/.github, GitHub reports that the run executed exactly that file at that
# revision, and the check is on the PR's exact head. Executes the real
# "Authorize terminal merge from trusted metadata" step and the real helper
# against a stubbed `gh`; every stub answers only the exact request the guard
# under test should make, so a guard that reads the wrong resource fails loudly.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
workflow="${REQUIRED_WORKFLOW_PROVENANCE_WORKFLOW:-$root/.github/workflows/ai-privileged-merge.yml}"
helper="${REQUIRED_WORKFLOW_PROVENANCE_HELPER:-$root/scripts/ci-gate/required-workflow-provenance.sh}"
generator="$root/scripts/gen-privileged-merge-caller.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

python3 - "$workflow" >"$tmp/authorize.sh" <<'PY'
import sys
import yaml

document = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
steps = document["jobs"]["privileged_merge"]["steps"]
name = "Authorize terminal merge from trusted metadata"
matches = [step for step in steps if step.get("name") == name]
if len(matches) != 1 or not matches[0].get("run"):
    raise SystemExit(f"missing unique non-empty step: {name}")
checkout = [step for step in steps if step.get("name") == "Check out immutable arm verifier"]
if len(checkout) != 1:
    raise SystemExit("missing unique trusted verifier checkout")
sparse = checkout[0]["with"]["sparse-checkout"].split()
if "scripts/ci-gate/required-workflow-provenance.sh" not in sparse:
    raise SystemExit("trusted verifier checkout omits the required-workflow provenance helper")
print(matches[0]["run"])
PY
[ -s "$tmp/authorize.sh" ] || { echo "FAIL - authorize step or trusted helper checkout missing"; exit 1; }

mkdir -p "$tmp/bin" "$tmp/run/.gate-trust/scripts/ci-gate" "$tmp/fx"
cat >"$tmp/run/.gate-trust/scripts/ci-gate/verify-arm-receipt.sh" <<'SH'
#!/usr/bin/env bash
printf '8001\n' >"$ARM_RECEIPT_ARTIFACT_ID_FILE"
SH
cp "$root/scripts/ci-gate/review-policy-envelope.py" "$tmp/run/.gate-trust/scripts/ci-gate/"
[ -r "$helper" ] && cp "$helper" "$tmp/run/.gate-trust/scripts/ci-gate/required-workflow-provenance.sh"

# The required workflow and the consumer workflow deliberately use different run,
# suite, and job identities, so a guard that consults the wrong one cannot pass.
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CALLS"
fixture() {
  local rc_var="RC_${1//-/_}"
  [ "${!rc_var:-0}" = 0 ] || { echo "gh: stubbed failure for $1 (HTTP 502)" >&2; exit "${!rc_var}"; }
  cat "$FX/$1.json"
}
if [ "${1-}" = api ] && [ "${2-}" = graphql ]; then
  node=""
  for argument in "$@"; do
    case "$argument" in id=*) node="${argument#id=}" ;; esac
  done
  [ "$node" = "$REQUIRED_RUN_NODE_ID" ] || { printf '%s\n' '{"data":{"node":null}}'; exit 0; }
  fixture graphql
  exit 0
fi
case "$*" in
  "pr view "*) fixture meta ;;
  "api repos/$TARGET_REPO --jq .default_branch // \"\"") printf '%s\n' main ;;
  "api repos/Verjson/.github/commits/main --jq .sha // \"\"") printf '%s\n' "$CANONICAL_MAIN" ;;
  "api repos/Verjson/.github") fixture canonical-repository ;;
  "api repos/Verjson/.github/compare/$RULESET_PIN...$CANONICAL_MAIN --jq .status // \"\"") printf '%s\n' "${PIN_RELATION:-ahead}" ;;
  "api repos/$TARGET_REPO/check-runs/$AUTHORIZATION_CHECK_ID") fixture authorization ;;
  "api --paginate repos/$TARGET_REPO/pulls/$PR_NUMBER/reviews?per_page=100") fixture reviews ;;
  "api --paginate repos/$TARGET_REPO/commits/$EXPECTED_HEAD_SHA/check-runs?per_page=100") fixture checks ;;
  "api --paginate repos/$TARGET_REPO/rules/branches/main?per_page=100") fixture rules ;;
  "api --paginate repos/$TARGET_REPO/actions/runs?head_sha=$EXPECTED_HEAD_SHA&event=pull_request&per_page=100") fixture head-runs ;;
  "api repos/$TARGET_REPO/actions/workflows/325334018") fixture consumer-workflow ;;
  "api --paginate repos/$TARGET_REPO/actions/workflows/325334018/runs?head_sha=$EXPECTED_HEAD_SHA&event=pull_request&per_page=100") fixture consumer-workflow-runs ;;
  "api repos/$TARGET_REPO/actions/runs/7102") fixture required-run ;;
  "api --paginate repos/$TARGET_REPO/actions/runs/7102/jobs?per_page=100") fixture required-jobs ;;
  "api repos/$TARGET_REPO/actions/runs/7002") fixture consumer-run ;;
  "api --paginate repos/$TARGET_REPO/actions/runs/7002/jobs?per_page=100") fixture consumer-jobs ;;
  "api repos/$TARGET_REPO/contents/.github/workflows/changelog.yml?ref=$EXPECTED_HEAD_SHA --jq .sha // \"\"") printf '%s\n' "$CONSUMER_BLOB" ;;
  "api repos/$TARGET_REPO/contents/.github/workflows/changelog.yml?ref=main --jq .sha // \"\"") printf '%s\n' "$CONSUMER_BLOB" ;;
  "api repos/$TARGET_REPO/pulls/$PR_NUMBER") fixture base-meta ;;
  "api repos/$TARGET_REPO/git/ref/heads/main --jq select(.object.type == \"commit\") | .object.sha // \"\"") printf '%s\n' "$AUTHORIZED_BASE_SHA" ;;
  *) echo "unexpected gh call: $*" >&2; exit 2 ;;
esac
GH
chmod +x "$tmp/bin/gh"

export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" FX="$tmp/fx"
export TARGET_REPO=Verjson/example PR_NUMBER=159 AUTHORIZATION_CHECK_ID=9001
export EXPECTED_HEAD_SHA=f5595f1a09c66ff613e99f8ca4c854cb326624a7
export AUTHORIZED_BASE_SHA=44a0dc4955c741d0d79d712986953cd91060948d
export CANONICAL_MAIN=3d5770fb482220ba0ce1f35a3ee95301143f09af
export RULESET_PIN=aa5644220469ed6263444c996179cb8f6d0aa168
export CANONICAL_REPOSITORY_ID=1269388380 REQUIRED_WORKFLOW_ID=345626628
export REQUIRED_WORKFLOW_PATH=.github/workflows/cli-projects-package-surface-required.yml
export REQUIRED_RUN_NODE_ID=WFR_kwLOTCRhks8AAAAJOE6tdQ
export CONSUMER_BLOB=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
export EXPECTED_APP_SLUG=verjson-ai-review GH_TOKEN=read-only-token GITHUB_REPOSITORY_OWNER=Verjson
export CALLER_REF=refs/heads/main EXECUTING_WORKFLOW_REPOSITORY=Verjson/.github EXECUTING_WORKFLOW_SHA="$CANONICAL_MAIN"
export GITHUB_SERVER_URL=https://github.com GITHUB_API_URL=https://api.github.com
export ARM_RUN_ID=7001 ARM_RUN_ATTEMPT=1
mkdir -p "$tmp/runner-temp"
export RUNNER_TEMP="$tmp/runner-temp" GITHUB_OUTPUT="$tmp/github-output"
REVIEW_POLICY="$(python3 "$root/scripts/ci-gate/review-policy-envelope.py" encode '{"actor":"trusted-arm","actor_permission":"automation","authority":"ai-merge","budget_usd":"5.00","fallback_budget_usd":"5.00","fallback_model":"deepseek-v4-flash","model":"deepseek-v4-pro","pricing_version":"deepseek-v4-2026-08-10","provider":"deepseek"}')"
export REVIEW_POLICY

required_entry() { # required_entry <check-name>
  jq -nc --arg name "$1" --arg path "$REQUIRED_WORKFLOW_PATH" --argjson id "$REQUIRED_WORKFLOW_ID" \
    '{app_id:15368,name:$name,workflow_id:$id,workflow_path:$path,workflow_repository:"Verjson/.github"}'
}
consumer_entry='{"app_id":15368,"name":"changelog / validate","workflow_id":325334018,"workflow_path":".github/workflows/changelog.yml"}'
admission_policy="[$(required_entry admission)]"
consumer_policy="[$consumer_entry]"
# The verjson-cli-projects rebind (#1861): CI and admission come from the organization
# required workflow; changelog checks stay bound to consumer workflows.
cli_projects_policy="$(jq -cn --argjson build "$(required_entry 'ci / build-test')" \
  --argjson eligibility "$(required_entry 'ci / eligibility')" \
  --argjson admission "$(required_entry admission)" --argjson changelog "$consumer_entry" \
  '[$build, $eligibility, $admission, $changelog]')"

edit() { # edit <fixture> <jq-filter>
  jq -c "$2" "$FX/$1.json" >"$tmp/edit" && mv "$tmp/edit" "$FX/$1.json"
}

write_base() {
  : >"$CALLS"
  : >"$GITHUB_OUTPUT"
  unset PIN_RELATION RC_rules RC_graphql
  export REQUIRED_CHECK_POLICY="$admission_policy"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" \
    '{state:"OPEN",isDraft:false,title:"change",labels:[],headRefOid:$head,headRepositoryOwner:{login:"Verjson"}}' >"$FX/meta.json"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" --arg base "$AUTHORIZED_BASE_SHA" \
    '{state:"open",head:{sha:$head},base:{ref:"main",sha:$base}}' >"$FX/base-meta.json"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" \
    '{id:9001,name:"AI review authorization",head_sha:$head,status:"completed",conclusion:"success",app:{id:15368,slug:"github-actions"}}' >"$FX/authorization.json"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" --arg login "${EXPECTED_APP_SLUG}[bot]" \
    '[{id:81,state:"APPROVED",commit_id:$head,user:{login:$login},body:"<!-- ai-review-authorization:9001 -->"}]' >"$FX/reviews.json"
  jq -nc --argjson id "$CANONICAL_REPOSITORY_ID" '{id:$id,full_name:"verJSON/.github"}' >"$FX/canonical-repository.json"
  # Payload shapes recorded from verjson-cli-projects#159 on 2026-10-21: `rules/branches`
  # reports organization `workflows` rules with repository_id/path/ref/sha, and
  # required-workflow runs carry `/actions/required_workflows/<id>` workflow URLs.
  jq -nc --argjson repo_id "$CANONICAL_REPOSITORY_ID" --arg path "$REQUIRED_WORKFLOW_PATH" --arg sha "$RULESET_PIN" '[
    {type:"workflows",ruleset_id:20722935,ruleset_source:"verJSON",ruleset_source_type:"Organization",
     parameters:{do_not_enforce_on_create:true,workflows:[{path:".github/workflows/gate-rearm.yml",ref:"refs/heads/main",repository_id:$repo_id,sha:"180b8dbb6ef7687e2a48e92df7de79fa5e56c4d2"}]}},
    {type:"required_status_checks",ruleset_id:21830367,ruleset_source:"verJSON",ruleset_source_type:"Organization",
     parameters:{required_status_checks:[{context:"admission",integration_id:15368}]}},
    {type:"workflows",ruleset_id:21830367,ruleset_source:"verJSON",ruleset_source_type:"Organization",
     parameters:{do_not_enforce_on_create:false,workflows:[{path:$path,ref:"refs/heads/main",repository_id:$repo_id,sha:$sha}]}}
  ]' >"$FX/rules.json"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" '{check_runs:[
    {id:201,name:"admission",head_sha:$head,status:"completed",conclusion:"success",details_url:"https://github.com/Verjson/example/actions/runs/7102/job/8102",app:{id:15368,slug:"github-actions"},check_suite:{id:6102}},
    {id:202,name:"ci / build-test",head_sha:$head,status:"completed",conclusion:"success",details_url:"https://github.com/Verjson/example/actions/runs/7102/job/8103",app:{id:15368,slug:"github-actions"},check_suite:{id:6102}},
    {id:203,name:"ci / eligibility",head_sha:$head,status:"completed",conclusion:"success",details_url:"https://github.com/Verjson/example/actions/runs/7102/job/8104",app:{id:15368,slug:"github-actions"},check_suite:{id:6102}},
    {id:101,name:"changelog / validate",head_sha:$head,status:"completed",conclusion:"success",details_url:"https://github.com/Verjson/example/actions/runs/7002/job/8002",app:{id:15368,slug:"github-actions"},check_suite:{id:6002}}
  ]}' >"$FX/checks.json"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" --arg path "$REQUIRED_WORKFLOW_PATH" --arg node "$REQUIRED_RUN_NODE_ID" --argjson id "$REQUIRED_WORKFLOW_ID" \
    '{id:7102,node_id:$node,check_suite_id:6102,workflow_id:$id,path:$path,event:"pull_request",head_sha:$head,
      head_repository:{full_name:"Verjson/example"},status:"completed",conclusion:"success",
      workflow_url:("https://api.github.com/repos/Verjson/example/actions/required_workflows/" + ($id | tostring))}' >"$FX/required-run.json"
  jq -nc '{jobs:[
    {id:8102,name:"admission",check_run_url:"https://api.github.com/repos/Verjson/example/check-runs/201"},
    {id:8103,name:"ci / build-test",check_run_url:"https://api.github.com/repos/Verjson/example/check-runs/202"},
    {id:8104,name:"ci / eligibility",check_run_url:"https://api.github.com/repos/Verjson/example/check-runs/203"}
  ]}' >"$FX/required-jobs.json"
  # GraphQL WorkflowRun.file is the only read that names the executed source revision.
  jq -nc --arg path "$REQUIRED_WORKFLOW_PATH" --arg sha "$RULESET_PIN" --argjson id "$REQUIRED_WORKFLOW_ID" \
    '{data:{node:{databaseId:7102,workflow:{databaseId:$id},file:{path:$path,repositoryName:"verJSON/.github",
      repositoryFileUrl:("https://github.com/verJSON/.github/blob/" + $sha + "/" + $path)}}}}' >"$FX/graphql.json"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" --arg path "$REQUIRED_WORKFLOW_PATH" --argjson id "$REQUIRED_WORKFLOW_ID" \
    '{workflow_runs:[
      {id:7102,workflow_id:$id,path:$path,event:"pull_request",head_sha:$head,status:"completed",conclusion:"success",
       workflow_url:("https://api.github.com/repos/Verjson/example/actions/required_workflows/" + ($id | tostring))},
      {id:7002,workflow_id:325334018,path:".github/workflows/changelog.yml",event:"pull_request",head_sha:$head,status:"completed",conclusion:"success",
       workflow_url:"https://api.github.com/repos/Verjson/example/actions/workflows/325334018"}]}' >"$FX/head-runs.json"
  jq -nc '{id:325334018,path:".github/workflows/changelog.yml",state:"active"}' >"$FX/consumer-workflow.json"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" \
    '{workflow_runs:[{id:7002,workflow_id:325334018,path:".github/workflows/changelog.yml",event:"pull_request",head_sha:$head,status:"completed",conclusion:"success"}]}' >"$FX/consumer-workflow-runs.json"
  jq -nc --arg head "$EXPECTED_HEAD_SHA" \
    '{id:7002,node_id:"WFR_consumer",check_suite_id:6002,workflow_id:325334018,path:".github/workflows/changelog.yml",event:"pull_request",head_sha:$head,
      head_repository:{full_name:"Verjson/example"},status:"completed",conclusion:"success",
      workflow_url:"https://api.github.com/repos/Verjson/example/actions/workflows/325334018"}' >"$FX/consumer-run.json"
  jq -nc '{jobs:[{id:8002,name:"changelog / validate",check_run_url:"https://api.github.com/repos/Verjson/example/check-runs/101"}]}' >"$FX/consumer-jobs.json"
}

run_authorize() { (cd "$tmp/run" && bash "$tmp/authorize.sh"); }
authorized() { grep -qx 'authorized=true' "$GITHUB_OUTPUT"; }
expect_authorized() { # expect_authorized <label>
  if run_authorize >"$tmp/out" 2>&1 && authorized; then pass "$1"; else fail "$1: $(tail -1 "$tmp/out")"; fi
}
expect_refused() { # expect_refused <label> <expected-error-fragment>
  if run_authorize >"$tmp/out" 2>&1; then
    fail "$1: exited 0"
  elif authorized; then
    fail "$1: authorized despite failing"
  elif ! grep -qF -- "$2" "$tmp/out"; then
    fail "$1: refused for another reason: $(grep -m1 '::error::' "$tmp/out" || tail -1 "$tmp/out")"
  else
    pass "$1"
  fi
}
expect_deferred() { # expect_deferred <label> <expected-message-fragment>
  if run_authorize >"$tmp/out" 2>&1 && ! authorized && grep -qF -- "$2" "$tmp/out"; then
    pass "$1"
  else
    fail "$1: $(tail -1 "$tmp/out")"
  fi
}

pin_error="does not pin exactly one trusted revision"
# A malformed pin must be refused before it is interpolated into an API path.
pin_never_reaches_api() { # pin_never_reaches_api <label>
  if grep -q '/compare/' "$CALLS"; then
    fail "$1 reached the compare API path"
  else
    pass "$1 is refused before it reaches an API path"
  fi
}
revision_error="executed a required-workflow revision other than the exact live ruleset pin"
identity_error="is not backed by the trusted exact-head workflow run"

# --- acceptance --------------------------------------------------------------------
write_base
expect_authorized "required-workflow check bound to the exact live ruleset pin is accepted"
grep -qxF "api --paginate repos/$TARGET_REPO/rules/branches/main?per_page=100" "$CALLS" \
  && pass "the pin is read from the live effective branch rules" \
  || fail "the pin was not read from the live effective branch rules"
if grep -q "actions/workflows/$REQUIRED_WORKFLOW_ID" "$CALLS"; then
  fail "a required-workflow check consulted the consumer workflow entity that cannot exist"
else
  pass "a required-workflow check never consults the consumer workflow entity"
fi

write_base; export REQUIRED_CHECK_POLICY="$cli_projects_policy"
expect_authorized "the verjson-cli-projects rebind (required CI + admission, consumer changelog) is accepted"

write_base; export REQUIRED_CHECK_POLICY="$consumer_policy"
expect_authorized "a consumer-workflow check still verifies through its default-branch blob"
if grep -q -e 'graphql' -e 'rules/branches' "$CALLS"; then
  fail "a consumer-workflow check consulted required-workflow provenance"
else
  pass "a consumer-workflow check does not depend on required-workflow provenance"
fi

# --- source repository ------------------------------------------------------------
write_base; edit graphql '.data.node.file.repositoryName="attacker/.github" | .data.node.file.repositoryFileUrl |= sub("verJSON/.github"; "attacker/.github")'
expect_refused "a run executed from another source repository is refused" "$revision_error"
write_base; edit graphql '.data.node.file.repositoryFileUrl |= sub("verJSON/.github"; "attacker/.github")'
expect_refused "a file URL naming another repository than the reported source is refused" "$revision_error"
write_base; edit graphql '.data.node.file.repositoryName="verJSON/.gіthub" | .data.node.file.repositoryFileUrl |= sub("verJSON/.github"; "verJSON/.gіthub")'
expect_refused "a Unicode lookalike source repository is refused" "$revision_error"
write_base; edit rules '(.[] | select(.ruleset_id == 21830367 and .type == "workflows") | .parameters.workflows[0].repository_id) = 999'
expect_refused "a ruleset pin for the same path in another repository is not a pin of Verjson/.github" "$pin_error"
write_base; edit canonical-repository '.full_name="attacker/.github"'
expect_refused "a canonical repository identity that is not Verjson/.github is refused" "$pin_error"

# --- revision ---------------------------------------------------------------------
write_base; edit graphql '.data.node.file.repositoryFileUrl |= sub("aa5644220469ed6263444c996179cb8f6d0aa168"; "c78fcd4ef9ab83b6775ae2ede94ebce475b3943c")'
expect_refused "a run at the previous pin after the ruleset rotated is refused" "$revision_error"
write_base; edit rules '(.[] | select(.ruleset_id == 21830367 and .type == "workflows") | .parameters.workflows[0].sha) = "c78fcd4ef9ab83b6775ae2ede94ebce475b3943c"'
export RULESET_PIN=c78fcd4ef9ab83b6775ae2ede94ebce475b3943c
expect_refused "a ruleset that pins another revision than the run executed is refused" "$revision_error"
export RULESET_PIN=aa5644220469ed6263444c996179cb8f6d0aa168
write_base; edit rules '(.[] | select(.ruleset_id == 21830367 and .type == "workflows") | .parameters.workflows[0]) |= del(.sha)'
expect_refused "a ref-only ruleset entry without an exact SHA pin is refused" "$pin_error"
pin_never_reaches_api "a ref-only pin"
write_base; edit rules '(.[] | select(.ruleset_id == 21830367 and .type == "workflows") | .parameters.workflows[0].sha) = "AA5644220469ED6263444C996179CB8F6D0AA168"'
expect_refused "a non-canonical SHA spelling is not an exact pin" "$pin_error"
pin_never_reaches_api "a non-canonical SHA spelling"
write_base; edit rules '(.[] | select(.ruleset_id == 21830367 and .type == "workflows") | .parameters.workflows[0].sha) = "../../../repos/attacker/x/compare/a"'
expect_refused "a path-shaped pin is not an exact pin" "$pin_error"
pin_never_reaches_api "a path-shaped pin"
write_base; edit rules '. + [(.[2] | .ruleset_id = 31000000 | .parameters.workflows[0].sha = "c78fcd4ef9ab83b6775ae2ede94ebce475b3943c")]'
expect_refused "two active rulesets pinning different revisions are ambiguous" "$pin_error"
write_base; edit rules '. + [(.[2] | .ruleset_id = 31000000)]'
expect_authorized "two active rulesets agreeing on one exact revision are not ambiguous"
write_base; edit rules '(.[] | select(.ruleset_id == 21830367 and .type == "workflows") | .ruleset_source_type) = "Repository"'
expect_refused "a repository-level ruleset cannot supply the trusted pin" "$pin_error"
write_base; edit rules '[.[] | select(.ruleset_id != 21830367 or .type != "workflows")]'
expect_refused "a required-workflow check with no live ruleset pin is refused" "$pin_error"
write_base; export PIN_RELATION=diverged
expect_refused "a pinned revision unreachable from canonical main is refused" "$pin_error"

# --- path ---------------------------------------------------------------------------
write_base; edit graphql '.data.node.file.path=".github/workflows/other-required.yml" | .data.node.file.repositoryFileUrl |= sub("cli-projects-package-surface-required"; "other-required")'
expect_refused "a run that executed another source file is refused" "$revision_error"
write_base; edit graphql '.data.node.file.repositoryFileUrl |= sub("cli-projects-package-surface-required"; "other-required")'
expect_refused "a file URL naming another path than the reported file is refused" "$revision_error"
write_base; edit required-run '.path=".github/workflows/other-required.yml"'
expect_refused "a run whose REST path differs from the declared path is refused" "$identity_error"
write_base; edit rules '(.[] | select(.ruleset_id == 21830367 and .type == "workflows") | .parameters.workflows[0].path) = ".github/workflows/other-required.yml"'
expect_refused "a ruleset that pins only another path is refused" "$pin_error"

# --- exact head ---------------------------------------------------------------------
write_base; edit checks '.check_runs[0].head_sha="0000000000000000000000000000000000000000"'
expect_refused "a required check reported on another head is refused" "is not on the exact authorized head"
write_base; edit required-run '.head_sha="0000000000000000000000000000000000000000"'
expect_refused "a required-workflow run for another head is refused" "$identity_error"

# --- required-workflow provenance field ---------------------------------------------
write_base; edit required-run '.workflow_url="https://api.github.com/repos/Verjson/example/actions/workflows/345626628"'
expect_refused "a run without the required-workflow URL cannot satisfy a required-workflow declaration" "$identity_error"
write_base; edit required-run 'del(.workflow_url)'
expect_refused "a run with no workflow URL at all is refused" "$identity_error"
write_base; edit required-run '.workflow_url="https://api.github.com/repos/Verjson/other/actions/required_workflows/345626628"'
expect_refused "a required-workflow URL under another repository is refused" "$identity_error"
write_base; export REQUIRED_CHECK_POLICY="$consumer_policy"; edit consumer-run '.workflow_url="https://api.github.com/repos/Verjson/example/actions/required_workflows/325334018"'
expect_refused "a required-workflow run cannot satisfy a consumer-workflow declaration" "$identity_error"
write_base; edit required-run 'del(.node_id)'
expect_refused "a run without a GraphQL node identity is refused" "$revision_error"
write_base; edit graphql '.data.node.databaseId=7103'
expect_refused "GraphQL provenance for another run is refused" "$revision_error"
write_base; edit graphql '.data.node.workflow.databaseId=1'
expect_refused "GraphQL provenance for another workflow is refused" "$revision_error"
write_base; edit graphql 'del(.data.node.file)'
expect_refused "GraphQL provenance without a workflow file is refused" "$revision_error"
for policy in \
  '[{"app_id":15368,"name":"admission","workflow_id":345626628,"workflow_path":".github/workflows/cli-projects-package-surface-required.yml","workflow_repository":"Verjson/other"}]' \
  '[{"app_id":15368,"name":"admission","workflow_id":345626628,"workflow_path":".github/workflows/cli-projects-package-surface-required.yml","workflow_repository":"verjson/.github"}]' \
  '[{"app_id":15368,"name":"admission","workflow_id":345626628,"workflow_path":".github/workflows/cli-projects-package-surface-required.yml","workflow_repository":null}]' \
  '[{"app_id":15368,"name":"admission","workflow_id":345626628,"workflow_path":".github/workflows/cli-projects-package-surface-required.yml","workflow_repository":"Verjson/.github","sha":"aa5644220469ed6263444c996179cb8f6d0aa168"}]'; do
  write_base; export REQUIRED_CHECK_POLICY="$policy"
  expect_refused "the gate rejects a malformed source declaration: $(jq -c '.[0] | del(.app_id, .name, .workflow_id, .workflow_path)' <<<"$policy")" "required_checks must declare trusted"
done

# --- API errors and malformed payloads ------------------------------------------------
write_base; export RC_rules=1
expect_refused "an unreadable ruleset fails closed" "$pin_error"
write_base; printf 'not json\n' >"$FX/rules.json"
expect_refused "a malformed ruleset response fails closed" "$pin_error"
write_base; printf '{"message":"Not Found"}\n' >"$FX/rules.json"
expect_refused "a ruleset error object instead of an array fails closed" "$pin_error"
write_base; : >"$FX/rules.json"
expect_refused "an empty ruleset response fails closed" "$pin_error"
write_base; edit rules '(.[] | select(.ruleset_id == 21830367 and .type == "workflows") | .parameters.workflows) = "malformed"'
expect_refused "a malformed workflows rule fails closed instead of being skipped" "$pin_error"
write_base; export RC_graphql=1
expect_refused "an unreadable GraphQL provenance read fails closed" "$revision_error"
write_base; printf 'not json\n' >"$FX/graphql.json"
expect_refused "malformed GraphQL provenance fails closed" "$revision_error"
write_base; edit graphql '. + {errors:[{message:"Resource not accessible by integration"}]}'
expect_refused "GraphQL provenance carrying errors fails closed" "$revision_error"
write_base; printf '%s\n%s\n' "$(cat "$FX/graphql.json")" "$(cat "$FX/graphql.json")" >"$FX/graphql.next" && mv "$FX/graphql.next" "$FX/graphql.json"
expect_refused "two concatenated GraphQL documents are not one provenance answer" "$revision_error"
write_base; printf 'not json\n' >"$FX/canonical-repository.json"
expect_refused "a malformed canonical repository identity fails closed" "$pin_error"

# --- readiness of an unreported required-workflow check -------------------------------
write_base; edit checks '.check_runs |= map(select(.name != "admission"))'
edit head-runs '.workflow_runs[0].status="in_progress" | .workflow_runs[0].conclusion=null'
expect_deferred "an active required-workflow run without its check yet defers" "is pending in trusted workflow run 7102"
write_base; edit checks '.check_runs |= map(select(.name != "admission"))'; edit head-runs '.workflow_runs |= map(select(.id != 7102))'
expect_deferred "a required workflow that has not started defers" "has not been reported because its trusted workflow has not started"
write_base; edit checks '.check_runs |= map(select(.name != "admission"))'; edit head-runs '.workflow_runs[0].workflow_url="https://api.github.com/repos/Verjson/example/actions/workflows/345626628"'
expect_deferred "a run without the required-workflow URL is not evidence that the required workflow started" "has not been reported because its trusted workflow has not started"
write_base; edit checks '.check_runs |= map(select(.name != "admission"))'
expect_refused "a completed required-workflow run that never published the check is permanent drift" "missing or renamed"

# --- generator ------------------------------------------------------------------------
contract_sha=0123456789abcdef0123456789abcdef01234567
if bash "$generator" "$contract_sha" "$cli_projects_policy" >"$tmp/generated.yml" 2>"$tmp/gen-err" &&
  python3 - "$cli_projects_policy" "$tmp/generated.yml" <<'PY'
import json, sys, yaml
document = yaml.safe_load(open(sys.argv[2], encoding="utf-8"))
policy = json.loads(document["jobs"]["privileged_merge"]["with"]["required_checks"])
assert policy == json.loads(sys.argv[1]), policy
PY
then
  pass "the generator emits the verjson-cli-projects required-workflow rebind verbatim"
else
  fail "the generator rejected or altered the verjson-cli-projects rebind: $(cat "$tmp/gen-err")"
fi
if bash "$generator" "$contract_sha" --retry '["generated artifacts"]' "$cli_projects_policy" >/dev/null 2>&1; then
  pass "the retry generator accepts the same required-workflow policy"
else
  fail "the retry generator rejected the required-workflow policy"
fi
for policy in \
  '[{"app_id":15368,"name":"admission","workflow_id":345626628,"workflow_path":".github/workflows/cli-projects-package-surface-required.yml","workflow_repository":"Verjson/other"}]' \
  '[{"app_id":15368,"name":"admission","workflow_id":345626628,"workflow_path":".github/workflows/cli-projects-package-surface-required.yml","workflow_repository":"Verjson/.github","sha":"aa5644220469ed6263444c996179cb8f6d0aa168"}]'; do
  if bash "$generator" "$contract_sha" "$policy" >/dev/null 2>&1; then
    fail "the generator accepted a malformed source declaration: $policy"
  else
    pass "the generator rejects a malformed source declaration: $(jq -c '.[0] | del(.app_id, .name, .workflow_id, .workflow_path)' <<<"$policy")"
  fi
done

[ "$fails" -eq 0 ] || { printf '%d failure(s)\n' "$fails"; exit 1; }
echo "all required-workflow check provenance tests passed"
