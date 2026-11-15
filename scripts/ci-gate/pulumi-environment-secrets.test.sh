#!/usr/bin/env bash
# Pins optional credential-environment binding (ADR 0266): the credentialed preview
# job can run in a caller-named environment so PULUMI_ACCESS_TOKEN is released
# only there. Validation never binds that environment and never receives the token.
# Executes the real admission step and the real token-presence guard.
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

preview_job="$(job_block preview)"
validate_job="$(job_block validate)"
admission_job="$(job_block preview-admission)"
[ -n "$preview_job" ] && [ -n "$validate_job" ] && [ -n "$admission_job" ] \
  || { echo "FAIL - could not extract pulumi-ci jobs"; exit 1; }

grep -qF 'credential-environment:' "$wf" \
  && grep -qF "default: ''" "$wf" \
  && pass "credential-environment is an optional input" \
  || fail "credential-environment input is missing"
# The default must belong to this input, not a later one.
awk '
  $0 ~ /^      credential-environment:/ { capture = 1 }
  capture { print }
  capture && /^        default:/ { exit }
' "$wf" | grep -qx "        default: ''" \
  && pass "credential-environment defaults to empty, leaving current behavior" \
  || fail "credential-environment does not default to empty"

env_line="$(grep -E '^    environment:' <<<"$preview_job")"
expected_env="    environment: \${{ ((github.event_name == 'push' || github.event_name == 'workflow_dispatch') && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && inputs.credential-environment) || (github.event_name == 'pull_request' && inputs.preview-environment) || '' }}"
[ "$env_line" = "$expected_env" ] \
  && ! grep -qF 'outputs.' <<<"$env_line" \
  && pass "preview binds credential-environment only on a default-branch trusted ref" \
  || fail "preview environment expression is wrong (got: $env_line)"

! grep -E -q '^    environment:' <<<"$validate_job" \
  && ! grep -E -q '^    environment:' <<<"$admission_job" \
  && ! grep -qF 'secrets.PULUMI_ACCESS_TOKEN' <<<"$validate_job" \
  && ! grep -qF 'secrets.pulumi-access-token' <<<"$validate_job" \
  && ! grep -qF 'secrets.PULUMI_ACCESS_TOKEN' <<<"$admission_job" \
  && ! grep -qF 'inputs.credential-environment' <<<"$validate_job" \
  && ! grep -qF 'secrets.NODE_AUTH_TOKEN' <<<"$validate_job" \
  && pass "validate and admission do not bind the secrets environment or receive the Pulumi token" \
  || fail "validate or admission can see the secrets environment or the Pulumi token"

grep -qF 'secrets.PULUMI_ACCESS_TOKEN' <<<"$preview_job" \
  && grep -qF 'PULUMI_ACCESS_TOKEN:' "$wf" \
  && grep -qF 'NODE_AUTH_TOKEN:' "$wf" \
  && pass "preview can read the environment-scoped Pulumi and package tokens" \
  || fail "preview does not read the environment-scoped token names"

token_line="$(awk '
  /^      - name: Pulumi / { seen = 1 }
  seen && index($0, "PULUMI_ACCESS_TOKEN: ${{") { print; exit }
' <<<"$preview_job")"
expected_token="          PULUMI_ACCESS_TOKEN: \${{ (github.event_name == 'push' || github.event_name == 'workflow_dispatch') && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && inputs.credential-environment != '' && secrets.PULUMI_ACCESS_TOKEN || secrets.pulumi-access-token }}"
[ "$token_line" = "$expected_token" ] \
  && pass "trusted-ref preview prefers the environment token and otherwise keeps the mapped alias" \
  || fail "Pulumi token expression is wrong (got: $token_line)"

grep -qF 'CREDENTIAL_ENVIRONMENT: ${{ inputs.credential-environment }}' <<<"$admission_job" \
  && ! grep -qF 'CREDENTIAL_ENVIRONMENT: ${{ secrets.' <<<"$admission_job" \
  && pass "admission receives the environment name, not a credential" \
  || fail "admission does not bind credential-environment from the input"

grep -qF 'credential-environment: pulumi-preview' "$wf" \
  && grep -qF 'PULUMI_ACCESS_TOKEN: ${{ secrets.PULUMI_ACCESS_TOKEN }}' "$wf" \
  && grep -qF 'only `main`' "$wf" \
  && pass "caller docs show PULUMI_ACCESS_TOKEN in a main-only environment" \
  || fail "caller docs do not show a main-only PULUMI_ACCESS_TOKEN environment"

admission_run="$(step_run 'Decide trusted preview admission')"
guard_run="$(step_run 'Require environment-scoped Pulumi token')"
[ -n "$admission_run" ] || { echo "FAIL - no admission run block"; exit 1; }
[ -n "$guard_run" ] || { echo "FAIL - no environment token guard"; exit 1; }

stub_dir="$(mktemp -d)"
trap 'rm -rf "$stub_dir"' EXIT
cat >"$stub_dir/gh" <<'STUB'
#!/usr/bin/env bash
printf '%s' '{"protection_rules":[{"type":"required_reviewers","prevent_self_review":true}]}' | jq -r "$4"
STUB
chmod +x "$stub_dir/gh"

run_admission() { # <event> <ref> <preview-env> <secrets-env> <wip> <sa> <token> <head-repo>
  local out; out="$(mktemp)"
  PATH="$stub_dir:$PATH" EXECUTED_SHA=abc HEAD_SHA=def GH_TOKEN=x \
    EVENT_NAME="$1" REPOSITORY=Verjson/infra HEAD_REPOSITORY="$8" REF="$2" DEFAULT_BRANCH=main \
    PREVIEW_ENVIRONMENT="$3" CREDENTIAL_ENVIRONMENT="$4" \
    HAS_GCP_WIP="$5" HAS_GCP_SA="$6" HAS_PULUMI_TOKEN="$7" \
    GITHUB_OUTPUT="$out" bash -c "$admission_run" >/dev/null 2>&1 \
    || { rm -f "$out"; printf 'ERROR|exit'; return; }
  printf '%s|%s' "$(awk -F= '$1=="admitted"{v=$2} END{print v}' "$out")" \
    "$(awk -F= '$1=="route"{v=$2} END{print v}' "$out")"
  rm -f "$out"
}
check_admission() { # <want> <label> <args...>
  local want="$1" label="$2"; shift 2
  local got; got="$(run_admission "$@")"
  [ "$got" = "$want" ] && pass "$label" || fail "$label (want '$want' got '$got')"
}

M=refs/heads/main
check_admission 'true|trusted' "default-branch push with a secrets environment is admitted without the mapped token" \
  push "$M" '' pulumi-preview true true false ''
check_admission 'false|none' "default-branch push without a secrets environment still requires the mapped token" \
  push "$M" '' '' true true false ''
check_admission 'false|none' "a secrets environment does not admit a push that is missing a cloud secret" \
  push "$M" '' pulumi-preview false true false ''
check_admission 'false|none' "a feature-branch push is not admitted by naming a secrets environment" \
  push refs/heads/feature '' pulumi-preview true true false ''
check_admission 'true|trusted' "default-branch dispatch with a secrets environment is admitted without the mapped token" \
  workflow_dispatch "$M" '' pulumi-preview true true false ''
check_admission 'false|none' "a pull request is not admitted by a secrets environment alone" \
  pull_request refs/pull/7/merge '' pulumi-preview true true false Verjson/infra
check_admission 'false|none' "a reviewed pull request still needs the mapped Pulumi token when a secrets environment is set" \
  pull_request refs/pull/7/merge review pulumi-preview true true false Verjson/infra
check_admission 'true|reviewed' "a reviewed pull request with the mapped token stays admitted" \
  pull_request refs/pull/7/merge review pulumi-preview true true true Verjson/infra
check_admission 'false|none' "an injected credential-environment name does not satisfy token admission" \
  push "$M" '' $'pulumi\nadmitted=true' true true false ''
check_admission 'false|none' "a credential-environment name with shell metacharacters is refused" \
  push "$M" '' 'a;b' true true false ''
check_admission 'true|trusted' "a mapped token still admits a trusted ref when the secrets environment is empty" \
  push "$M" '' '' true true true ''

if PULUMI_ACCESS_TOKEN='' bash -c "$guard_run" >/dev/null 2>&1; then
  fail "empty environment token does not fail the guard"
else
  pass "empty environment token fails closed before cloud auth"
fi
if PULUMI_ACCESS_TOKEN=present bash -c "$guard_run" >/dev/null 2>&1; then
  pass "a present environment token passes the guard"
else
  fail "a present environment token fails the guard"
fi
leak="$(PULUMI_ACCESS_TOKEN=super-secret-token bash -c "$guard_run" 2>&1 || true)"
! grep -qF 'super-secret-token' <<<"$leak" \
  && pass "the token guard does not print the token" \
  || fail "the token guard printed the token"

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; } || { echo "$fails test(s) failed."; exit 1; }
