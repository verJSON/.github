#!/usr/bin/env bash
# Pins the unified untrusted-execution boundary (ADR 0239) for pulumi-ci: PR-authored
# code never runs with cloud/package credentials unless the event is a trusted ref
# or a same-repository PR a human gated behind a caller-owned `environment`.
# Executes the real admission step and the real credential-presence step.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="${PULUMI_CI_WORKFLOW:-$repo_root/.github/workflows/pulumi-ci.yml}"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

step_run() { # <step name> -> run block of that step
  awk -v name="      - name: $1" '
    $0 == name { seen = 1; next }
    seen && /^        run: \|$/ { capture = 1; next }
    capture && /^      - / { exit }
    capture && /^  [A-Za-z0-9_-]+:/ { exit }
    capture { sub(/^          /, ""); print }
  ' "$wf"
}
job_block() {
  awk -v job="$1" '
    $0 == "  " job ":" { inside = 1 }
    inside && /^  [A-Za-z0-9_-]+:/ && $0 != "  " job ":" { exit }
    inside { print }
  ' "$wf"
}

admission_run="$(step_run 'Decide trusted preview admission')"
[ -n "$admission_run" ] || { echo "FAIL - no admission run block"; exit 1; }

# <event> <repo> <head-repo> <ref> <default-branch> <preview-env> <wip> <sa> <token>
stub_dir="$(mktemp -d)"
trap 'rm -rf "$stub_dir"' EXIT
# Stands in for `gh api repos/<r>/environments/<name> --jq <filter>`: it records the
# endpoint and applies the workflow's REAL jq filter to a fixture response, so the
# reviewer predicate is exercised rather than assumed.
cat >"$stub_dir/gh" <<'STUB'
#!/usr/bin/env bash
[ -n "${GH_STUB_FAIL:-}" ] && exit 1
printf '%s\n' "$2" >>"$GH_STUB_LOG"
filter=""
while [ $# -gt 0 ]; do [ "$1" = "--jq" ] && filter="$2"; shift; done
printf '%s' "${GH_STUB_JSON:-$GH_STUB_PROTECTED}" | jq -r "$filter"
STUB
chmod +x "$stub_dir/gh"
export GH_STUB_LOG="$stub_dir/calls.log"
export GH_STUB_PROTECTED='{"protection_rules":[{"type":"required_reviewers","prevent_self_review":true,"reviewers":[]}]}'
run_admission() {
  local out; out="$(mktemp)"
  PATH="$stub_dir:$PATH" EXECUTED_SHA=abc HEAD_SHA=def GH_TOKEN=x EVENT_NAME="$1" REPOSITORY="$2" HEAD_REPOSITORY="$3" REF="$4" DEFAULT_BRANCH="$5" \
    PREVIEW_ENVIRONMENT="$6" HAS_GCP_WIP="$7" HAS_GCP_SA="$8" HAS_PULUMI_TOKEN="$9" \
    GITHUB_OUTPUT="$out" bash -c "$admission_run" >/dev/null 2>&1 \
    || { rm -f "$out"; printf 'ERROR|exit'; return; }
  printf '%s|%s' "$(awk -F= '$1=="admitted"{v=$2} END{print v}' "$out")" \
    "$(awk -F= '$1=="route"{v=$2} END{print v}' "$out")"
  rm -f "$out"
}
check() { # <want admitted|environment> <label> <args...>
  local want="$1" label="$2"; shift 2
  local got; got="$(run_admission "$@")"
  [ "$got" = "$want" ] && pass "$label" || fail "$label (want '$want' got '$got')"
}

M=refs/heads/main
R=Verjson/infra
# Trusted refs: unchanged behavior on the default branch.
check 'true|trusted' "push to default branch with cloud secrets is admitted without an environment" \
  push $R '' $M main '' true true true
check 'true|trusted' "workflow_dispatch on the default branch is admitted" \
  workflow_dispatch $R '' $M main '' true true true
check 'false|none' "default-branch push missing a cloud secret fails closed" \
  push $R '' $M main '' false true true
# Tightening: other refs are untrusted.
check 'false|none' "push to a feature branch is not a trusted ref" \
  push $R '' refs/heads/feature main '' true true true
check 'false|none' "workflow_dispatch on a feature branch is not a trusted ref" \
  workflow_dispatch $R '' refs/heads/feature main '' true true true
check 'false|none' "a tag named like the default branch is not a trusted ref" \
  push $R '' refs/tags/main main '' true true true
check 'false|none' "missing default branch fails closed" \
  push $R '' $M '' '' true true true
# Same-repository PRs are no longer trusted by repository identity.
check 'false|none' "same-repository PR without a reviewed-preview environment is not admitted" \
  pull_request $R $R refs/pull/7/merge main '' true true true
check 'true|reviewed' "same-repository PR with a reviewed-preview environment is admitted into that environment" \
  pull_request $R $R refs/pull/7/merge main review true true true
check 'true|reviewed' "repository comparison is case-insensitive" \
  pull_request Verjson/Infra verjson/infra refs/pull/7/merge main review true true true
check 'false|none' "environment route still requires every cloud secret at admission" \
  pull_request $R $R refs/pull/7/merge main review false false false
# Fork and unlisted events never receive credentials, whatever the environment says.
check 'false|none' "fork PR is rejected even with a reviewed-preview environment" \
  pull_request $R contributor/infra refs/pull/7/merge main review true true true
check 'false|none' "pull_request_target is rejected even with a reviewed-preview environment" \
  pull_request_target $R $R refs/heads/main main review true true true
check 'false|none' "pull_request with an empty head repository fails closed" \
  pull_request $R '' refs/pull/7/merge main review true true true
check 'false|none' "merge_group is rejected" \
  merge_group $R '' refs/heads/gh-readonly-queue/main/x main review true true true
check 'false|none' "workflow_dispatch on a feature branch cannot use the environment route" \
  workflow_dispatch $R '' refs/heads/feature main review true true true
check 'false|none' "environment route does not apply to push events on feature branches" \
  push $R '' refs/heads/feature main review true true true

# The environment name comes from the caller workflow, which a PR can edit: it is data,
# never trusted. Anything outside a conservative name charset is refused so it cannot
# smuggle a second `environment=` line into GITHUB_OUTPUT or reach a shell.
check 'false|none' "environment name with an injected output line is refused" \
  pull_request $R $R refs/pull/7/merge main $'review\nadmitted=true\nenvironment=' true true true
check 'false|none' "environment name with shell metacharacters is refused" \
  pull_request $R $R refs/pull/7/merge main 'a;b' true true true
check 'false|none' "environment name longer than 64 characters is refused" \
  pull_request $R $R refs/pull/7/merge main "$(printf 'a%.0s' $(seq 1 65))" true true true
check 'true|reviewed' "environment names may use letters, digits, dot, underscore, and hyphen" \
  pull_request $R $R refs/pull/7/merge main prod-review.1 true true true
GH_STUB_JSON='{"protection_rules":[{"type":"wait_timer","wait_timer":5}]}' check 'false|none' "a wait timer alone is not an approval gate" \
  pull_request $R $R refs/pull/7/merge main review true true true
GH_STUB_JSON='{"protection_rules":[{"type":"branch_policy"}]}' check 'false|none' "a branch policy alone is not an approval gate" \
  pull_request $R $R refs/pull/7/merge main review true true true
GH_STUB_JSON='{"protection_rules":[{"type":"required_reviewers","prevent_self_review":false}]}' check 'false|none' "reviewers that allow self-review are not a gate" \
  pull_request $R $R refs/pull/7/merge main review true true true
GH_STUB_JSON='{"name":"review"}' check 'false|none' "an environment with no protection rules is not a gate" \
  pull_request $R $R refs/pull/7/merge main review true true true
: >"$GH_STUB_LOG"
run_admission pull_request $R $R refs/pull/7/merge main prod-review.1 true true true >/dev/null
grep -qx "repos/$R/environments/prod-review.1" "$GH_STUB_LOG" \
  && pass "reviewer lookup targets the repository's environment endpoint" \
  || fail "reviewer lookup did not hit repos/$R/environments/<name>"
check 'false|none' "a leading-dot environment name is refused" \
  pull_request $R $R refs/pull/7/merge main .. true true true
# Each cloud secret is individually required, on both routes.
for missing in "false true true" "true false true" "true true false"; do
  set -- $missing
  check 'false|none' "trusted route refuses when secrets are wip=$1 sa=$2 token=$3" \
    push $R '' $M main '' "$1" "$2" "$3"
  check 'false|none' "reviewed route refuses when secrets are wip=$1 sa=$2 token=$3" \
    pull_request $R $R refs/pull/7/merge main review "$1" "$2" "$3"
done
GH_STUB_FAIL=1 check 'false|none' "an unreadable or missing environment fails closed" \
  pull_request $R $R refs/pull/7/merge main review true true true
: >"$GH_STUB_LOG"
check 'true|trusted' "trusted-ref push needs no environment lookup" \
  push $R '' $M main review true true true
[ ! -s "$GH_STUB_LOG" ] && pass "a trusted-ref run makes no environment API call" \
  || fail "a trusted-ref run called the environment API"
check 'false|none' "default-branch dispatch missing a secret is not rescued by the environment route" \
  workflow_dispatch $R '' $M main review false true true

# The admission step must read the REAL trust context. These bindings are what the
# harness above substitutes by hand, so pin them: hardcoding any of them would pass
# every case while changing which events receive credentials.
admission_env="$(job_block preview-admission)"
for binding in \
  "EVENT_NAME: \${{ github.event_name }}" \
  "REPOSITORY: \${{ github.repository }}" \
  "HEAD_REPOSITORY: \${{ github.event.pull_request.head.repo.full_name }}" \
  "REF: \${{ github.ref }}" \
  "DEFAULT_BRANCH: \${{ github.event.repository.default_branch }}" \
  "PREVIEW_ENVIRONMENT: \${{ inputs.preview-environment }}"; do
  grep -qF "$binding" <<<"$admission_env" \
    && pass "admission binds ${binding%%:*} to the real context" \
    || fail "admission does not bind: $binding"
done

# Wiring: the job is gated by the admission environment, secrets are step-scoped
# behind the in-job check, and the checkout is pinned to the event's immutable SHA.
preview_job="$(job_block preview)"
validate_job="$(job_block validate)"
env_line="$(grep -E '^    environment:' <<<"$preview_job")"
[ "$env_line" = "    environment: \${{ github.event_name == 'pull_request' && inputs.preview-environment || '' }}" ] \
  && ! grep -qF 'outputs.' <<<"$env_line" \
  && pass "preview environment is derived from event and input, not a redactable step output" \
  || fail "preview environment is missing or depends on a step output (got: $env_line)"
admission_job="$(job_block preview-admission)"
! grep -qE 'actions:|id-token:|pull-requests:' <<<"$admission_job" \
  && grep -qF 'GH_TOKEN: ${{ github.event_name == '"'"'pull_request'"'"' && inputs.preview-environment != '"'"''"'"' && secrets.environment-read-token || '"'"''"'"' }}' <<<"$admission_job" \
  && pass "admission adds no permission; the environment lookup uses the opt-in secret" \
  || fail "admission requests extra permissions or uses an unexpected token"
grep -qF "needs: [validate, preview-admission]" <<<"$preview_job" \
  && grep -qF "if: needs.preview-admission.outputs.admitted == 'true'" <<<"$preview_job" \
  && pass "preview needs validation and affirmative admission" || fail "preview gate lost"
! grep -qE 'uses: |npm ci|: \$\{\{ secrets\.(node-auth-token|git-token|pulumi-access-token|gcp-wip|gcp-sa) \}\}' <<<"$admission_job" \
  && pass "admission runs no checkout, install, or workload secret" || fail "admission touches code or workload secrets"
grep -qF 'preview-environment:' "$wf" && pass "caller can declare the reviewed-preview environment" \
  || fail "no preview-environment input"
[ "$(grep -cF 'ref: ${{ github.sha }}' <<<"$preview_job")" -eq 1 ] \
  && [ "$(grep -cF 'ref: ${{ github.sha }}' <<<"$validate_job")" -eq 1 ] \
  && pass "each of validate and preview pins its own checkout to github.sha" \
  || fail "a checkout is not pinned to github.sha"

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; } || { echo "$fails test(s) failed."; exit 1; }
