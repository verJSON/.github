#!/usr/bin/env bash
# Pins pulumi-ci's credential boundary and trusted-event admission. The tests
# extract the exact workflow jobs/run block so assertions cannot drift into a
# second implementation.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="${PULUMI_CI_WORKFLOW:-$repo_root/.github/workflows/pulumi-ci.yml}"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() {
  printf 'FAIL - %s\n' "$1"
  fails=$((fails + 1))
}
contains_literal() {
  grep -qF -- "$2" <<<"$1"
}
contains_pattern() {
  grep -qE -- "$2" <<<"$1"
}

job_block() {
  awk -v job="$1" '
    $0 == "  " job ":" { inside = 1 }
    inside && /^  [A-Za-z0-9_-]+:/ && $0 != "  " job ":" { exit }
    inside { print }
  ' "$wf"
}

validate_job="$(job_block validate)"
admission_job="$(job_block preview-admission)"
preview_job="$(job_block preview)"

# Validation is its own credential-free job. No caller-supplied command or
# checkout may inherit write, OIDC, package, cloud, or Git credentials.
# Routing follows ADR 0034's temporary general-pool exception,
# but the property THIS test owns is narrower and unchanged: validation's pool is
# fixed by policy, never chosen by the caller. `inputs.runner` must stay absent.
contains_literal "$validate_job" "github.repository_owner != 'Verjson'" \
  && contains_literal "$validate_job" 'vars.CI_LANE_FALLBACK' \
  && contains_literal "$validate_job" "'ubuntu-24.04'" \
  && ! contains_literal "$validate_job" 'inputs.runner' \
  && pass "validation keeps a policy-fixed pool the caller cannot redirect" \
  || fail "validation lost its fixed organization-aware runner boundary"
contains_literal "$validate_job" 'contents: read' \
  && ! contains_pattern "$validate_job" 'pull-requests:|id-token:|packages:|contents: write|secrets\.' \
  && pass "validation has only contents: read and no secret references" \
  || fail "validation has permissions or secrets beyond contents: read"
contains_literal "$validate_job" 'persist-credentials: false' \
  && pass "validation checkout does not persist GITHUB_TOKEN" \
  || fail "validation checkout persists credentials"
contains_literal "$validate_job" 'INSTALL_COMMAND: ${{ inputs.install-command }}' \
  && contains_literal "$validate_job" 'VALIDATE_COMMAND: ${{ inputs.validate-command }}' \
  && ! contains_pattern "$preview_job" 'inputs\.(install|validate)-command' \
  && pass "caller install/validation commands exist only in validation" \
  || fail "caller install/validation commands crossed into the preview job"
contains_literal "$validate_job" 'GIT_CONFIG_GLOBAL: /dev/null' \
  && contains_literal "$validate_job" 'NPM_CONFIG_USERCONFIG: ${{ runner.temp }}/pulumi-validation.npmrc' \
  && contains_literal "$validate_job" 'unset ACTIONS_ID_TOKEN_REQUEST_TOKEN ACTIONS_ID_TOKEN_REQUEST_URL' \
  && contains_literal "$validate_job" 'unset PULUMI_ACCESS_TOKEN VERJSON_GIT_TOKEN' \
  && pass "validation scrubs inherited Git, npm, OIDC, and cloud credential paths" \
  || fail "validation credential scrubbing is incomplete"

# Extract and execute the admission step's exact shell body.
admission_run="$(awk '
  /^      - name: Decide trusted preview admission$/ { seen = 1 }
  seen && /^        run: \|$/ { capture = 1; next }
  capture && /^      - / { exit }
  capture && /^  [A-Za-z0-9_-]+:/ { exit }
  capture {
    sub(/^          /, "")
    print
  }
' "$wf")"

if [ -z "$admission_run" ]; then
  echo "FAIL - could not extract trusted preview admission run block from $wf"
  exit 1
fi

run_admission() { # <event> <repository> <head-repository> <has-wip> <has-sa> <has-pulumi-token>
  local output
  output="$(mktemp)"
  EVENT_NAME="$1" REPOSITORY="$2" HEAD_REPOSITORY="$3" \
    HAS_GCP_WIP="$4" HAS_GCP_SA="$5" HAS_PULUMI_TOKEN="$6" GITHUB_OUTPUT="$output" \
    bash -c "$admission_run" >/dev/null
  awk -F= '$1 == "admitted" { value = $2 } END { print value }' "$output"
  rm -f "$output"
}

check_admission() { # <expected> <event> <repository> <head> <wip> <sa> <token> <label>
  local got
  got="$(run_admission "$2" "$3" "$4" "$5" "$6" "$7")"
  if [ "$got" = "$1" ]; then
    pass "$8"
  else
    fail "$8 (want $1 got ${got:-empty})"
  fi
}

check_admission false pull_request Verjson/infra contributor/infra true true true \
  "fork PR with cloud secrets is rejected"
check_admission true pull_request Verjson/infra Verjson/infra true true true \
  "same-repository PR with every cloud secret is admitted"
check_admission true push Verjson/infra '' true true true \
  "push with every cloud secret is admitted"
check_admission false push Verjson/infra '' false true true \
  "push missing a cloud secret fails closed"
check_admission false pull_request Verjson/infra Verjson/infra true false true \
  "same-repository PR missing a cloud secret fails closed"
check_admission false pull_request_target Verjson/infra contributor/infra true true true \
  "secret-bearing pull_request_target event fails closed"
check_admission false workflow_dispatch Verjson/infra '' true true true \
  "unlisted secret-bearing event fails closed"

contains_literal "$admission_job" 'needs: validate' \
  && contains_literal "$admission_job" 'contents: read' \
  && contains_literal "$admission_job" "HAS_GCP_WIP: \${{ secrets.gcp-wip != '' }}" \
  && contains_literal "$admission_job" "HAS_GCP_SA: \${{ secrets.gcp-sa != '' }}" \
  && contains_literal "$admission_job" "HAS_PULUMI_TOKEN: \${{ secrets.pulumi-access-token != '' }}" \
  && ! contains_pattern "$admission_job" 'GCP_WIP: \$\{\{ secrets\.gcp-wip \}\}|GCP_SA: \$\{\{ secrets\.gcp-sa \}\}|PULUMI_ACCESS_TOKEN: \$\{\{ secrets\.pulumi-access-token \}\}' \
  && ! contains_pattern "$admission_job" 'uses: actions/checkout|inputs\.(install|validate)-command|pull-requests:|id-token:' \
  && pass "admission receives boolean secret-presence flags in a fixed credential-light job" \
  || fail "admission runs before validation or receives raw credentials"

# The privileged job must depend on successful validation plus affirmative
# admission and must not expose package/Git credentials after its fixed install.
contains_literal "$preview_job" 'needs: [validate, preview-admission]' \
  && contains_literal "$preview_job" "if: needs.preview-admission.outputs.admitted == 'true'" \
  && pass "live preview requires successful validation and trusted admission" \
  || fail "live preview is not gated by validation and trusted admission"
for permission in 'contents: read' 'pull-requests: write' 'id-token: write'; do
  contains_literal "$preview_job" "$permission" \
    || fail "preview job is missing permission: $permission"
done
contains_literal "$preview_job" 'persist-credentials: false' \
  && pass "preview checkout does not persist GITHUB_TOKEN" \
  || fail "preview checkout persists credentials"
contains_literal "$preview_job" 'run: npm ci' \
  && contains_literal "$preview_job" 'NODE_AUTH_TOKEN: ${{ secrets.node-auth-token }}' \
  && contains_literal "$preview_job" 'VERJSON_GIT_TOKEN: ${{ secrets.git-token }}' \
  && contains_literal "$preview_job" 'GIT_CONFIG_GLOBAL: /dev/null' \
  && contains_literal "$preview_job" "GIT_CONFIG_COUNT: '3'" \
  && pass "preview dependency install is fixed and step-scopes package/Git secrets" \
  || fail "preview dependency install is caller-controlled or lacks scoped secrets"

install_line="$(printf '%s\n' "$preview_job" | grep -nF 'run: npm ci' | cut -d: -f1)"
auth_line="$(printf '%s\n' "$preview_job" | grep -nF 'uses: google-github-actions/auth@' | cut -d: -f1)"
{ [ -n "$install_line" ] && [ -n "$auth_line" ] && [ "$install_line" -lt "$auth_line" ]; } \
  && pass "package/Git install completes before cloud credentials exist" \
  || fail "cloud authentication can precede dependency install"

# Every third-party action in the changed workflow is pinned to the reviewed
# immutable commit; both checkouts explicitly disable credential persistence.
checkout='actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7'
setup_node='actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7'
auth='google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3'
pulumi='pulumi/actions@8e5e406f4007fca908480587cb9893c07090f58d # v7'
[ "$(grep -cF "uses: $checkout" "$wf")" -eq 2 ] \
  && [ "$(grep -cF 'persist-credentials: false' "$wf")" -eq 2 ] \
  && [ "$(grep -cF "uses: $setup_node" "$wf")" -eq 2 ] \
  && grep -qF "uses: $auth" "$wf" \
  && grep -qF "uses: $pulumi" "$wf" \
  && ! grep -Eq 'uses: (actions/(checkout|setup-node)|google-github-actions/auth|pulumi/actions)@v[0-9]+' "$wf" \
  && pass "all third-party actions and checkouts use immutable credential-safe pins" \
  || fail "a third-party action is mutable or a checkout persists credentials"

# Keep the pre-existing no-op-on-push comment behavior.
expr="$(awk '
  /^      - name: Pulumi / { seen = 1 }
  seen && $0 ~ /^          comment-on-pr:/ {
    line = $0
    sub(/^[^{]*\{\{[[:space:]]*/, "", line)
    sub(/[[:space:]]*\}\}.*$/, "", line)
    print line
    exit
  }
' "$wf")"

if [ -z "$expr" ]; then
  echo "FAIL - could not extract the comment-on-pr expression from $wf"
  exit 1
fi

case "$expr" in
  *"github.event_name == 'pull_request'"*)
    pass "comment-on-pr is gated on a pull_request event context" ;;
  *)
    fail "comment-on-pr lacks the github.event_name == 'pull_request' guard (got: $expr)" ;;
esac

case "$expr" in
  *"inputs.comment-on-pr"*"&&"*"github.event_name"*)
    pass "caller's comment-on-pr input is AND-gated with the PR-context guard" ;;
  *)
    fail "expected 'inputs.comment-on-pr && github.event_name ...' (got: $expr)" ;;
esac

eval_guard() { # <input-bool> <event-name> -> true|false (drives off $expr)
  local e="${expr//inputs.comment-on-pr/$1}"
  e="${e//github.event_name/\'$2\'}"        # e.g.  true && 'push' == 'pull_request'
  local out=true
  local IFS='&'
  for term in ${e//&&/&}; do                # split on && (each side ANDed)
    term="$(printf '%s' "$term" | tr -d ' ')"
    [ -z "$term" ] && continue
    case "$term" in
      *==*) [ "${term%%==*}" = "${term##*==}" ] || out=false ;;   # 'a' == 'b'
      true) ;;
      false) out=false ;;
      *) echo "FAIL - unhandled term '$term' in extracted guard"; exit 1 ;;
    esac
  done
  echo "$out"
}
check() { # <expected> <input> <event> <label>
  got="$(eval_guard "$2" "$3")"
  if [ "$got" = "$1" ]; then pass "$4"; else fail "$4 (want $1 got $got)"; fi
}
check false true  push         "push event never comments even when input is true (no-op)"
check false false push         "push event never comments when input is false"
check true  true  pull_request "PR event comments when input is true"
check false false pull_request "PR event honors caller opt-out (input false)"

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
else
  echo "$fails test(s) failed."
  exit 1
fi
