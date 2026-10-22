#!/usr/bin/env bash
# Pins ui-ci's side of the unified untrusted-execution boundary (ADR 0239): package,
# Git, and submodule credentials are job-wide only on a trusted ref; elsewhere they are
# step-scoped to the install steps, or withheld entirely with credentialless-untrusted.
# Executes the real classifier step and checks it against pulumi-ci's admission.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="${UI_CI_WORKFLOW:-$repo_root/.github/workflows/ui-ci.yml}"
pulumi_wf="${PULUMI_CI_WORKFLOW:-$repo_root/.github/workflows/pulumi-ci.yml}"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

step_run() { # <workflow> <step name>
  awk -v name="      - name: $2" '
    $0 == name { seen = 1; next }
    seen && /^        run: \|$/ { capture = 1; next }
    capture && /^      - / { exit }
    capture && /^[a-z]/ { exit }
    capture { sub(/^          /, ""); print }
  ' "$1"
}
step_block() { # <workflow> <step name>
  awk -v name="      - name: $2" '
    $0 == name { capture = 1 }
    capture && $0 != name && /^      - / { exit }
    capture { print }
  ' "$1"
}

classify_run="$(step_run "$wf" 'Classify event trust')"
[ -n "$classify_run" ] || { echo "FAIL - no 'Classify event trust' run block in $wf"; exit 1; }
admission_run="$(step_run "$pulumi_wf" 'Decide trusted preview admission')"

run_classify() { # <event> <repo> <head-repo> <ref> <default> <credentialless-untrusted>
  local out; out="$(mktemp)"
  EVENT_NAME="$1" REPOSITORY="$2" HEAD_REPOSITORY="$3" REF="$4" DEFAULT_BRANCH="$5" \
    CREDENTIALLESS_UNTRUSTED="$6" GITHUB_OUTPUT="$out" bash -c "$classify_run" >/dev/null 2>&1 \
    || { rm -f "$out"; printf 'ERROR'; return; }
  awk -F= '$1=="mode"{v=$2} END{print v}' "$out"; rm -f "$out"
}
check() { # <want> <label> <args...>
  local want="$1" label="$2"; shift 2
  local got; got="$(run_classify "$@")"
  [ "$got" = "$want" ] && pass "$label" || fail "$label (want $want got $got)"
}
M=refs/heads/main; R=Verjson/ui
check job "push to the default branch keeps job-wide credentials" push $R '' $M main false
check job "default-branch push stays job-wide even with credentialless-untrusted" push $R '' $M main true
check job "workflow_dispatch on the default branch keeps job-wide credentials" workflow_dispatch $R '' $M main false
check install "same-repository PR scopes credentials to install by default" pull_request $R $R refs/pull/3/merge main false
check none "same-repository PR gets no credentials with credentialless-untrusted" pull_request $R $R refs/pull/3/merge main true
check install "push to a feature branch is untrusted (install-scoped)" push $R '' refs/heads/feat main false
check none "push to a feature branch is credentialless when opted in" push $R '' refs/heads/feat main true
check install "missing default branch fails closed to install scope" push $R '' $M '' false
check install "a tag named like the default branch is untrusted" push $R '' refs/tags/main main false
check none "fork PR never receives credentials, opt-in or not" pull_request $R contributor/ui refs/pull/3/merge main false
check none "pull_request_target from a fork never receives credentials" pull_request_target $R contributor/ui refs/heads/main main false
check none "merge_group can carry a fork PR and is credentialless" merge_group $R '' refs/heads/gh-readonly-queue/main/x main false
check none "a pull request with no readable head repository is treated as a fork" pull_request $R '' refs/pull/3/merge main false
check install "an unlisted event is untrusted (install-scoped)" schedule $R '' $M main false

# Parity with pulumi-ci: the same truth table decides "trusted ref" in both workflows.
if [ -n "$admission_run" ]; then
  stub="$(mktemp -d)"; trap 'rm -rf "$stub"' EXIT
  printf '#!/usr/bin/env bash\nexit 1\n' >"$stub/gh"; chmod +x "$stub/gh"
  pulumi_trusted() { # <event> <repo> <head> <ref> <default>
    local out; out="$(mktemp)"
    PATH="$stub:$PATH" EXECUTED_SHA=a HEAD_SHA=b GH_TOKEN="" EVENT_NAME="$1" REPOSITORY="$2" HEAD_REPOSITORY="$3" \
      REF="$4" DEFAULT_BRANCH="$5" PREVIEW_ENVIRONMENT="" HAS_GCP_WIP=true HAS_GCP_SA=true HAS_PULUMI_TOKEN=true \
      GITHUB_OUTPUT="$out" bash -c "$admission_run" >/dev/null 2>&1
    [ "$(awk -F= '$1=="route"{v=$2} END{print v}' "$out")" = trusted ] && echo true || echo false
    rm -f "$out"
  }
  while IFS='|' read -r ev head ref def; do
    ui="$(run_classify "$ev" $R "$head" "$ref" "$def" true)"
    pu="$(pulumi_trusted "$ev" $R "$head" "$ref" "$def")"
    if { [ "$ui" = job ] && [ "$pu" = true ]; } || { [ "$ui" != job ] && [ "$pu" = false ]; }; then
      pass "ui-ci and pulumi-ci agree on '$ev $ref head=${head:-none} default=${def:-none}'"
    else
      fail "ui-ci ($ui) and pulumi-ci (trusted=$pu) disagree on '$ev $ref head=${head:-none} default=${def:-none}'"
    fi
  done <<'TABLE'
push||refs/heads/main|main
workflow_dispatch||refs/heads/main|main
push||refs/heads/feat|main
push||refs/tags/main|main
push||refs/heads/main|
pull_request|Verjson/ui|refs/pull/3/merge|main
pull_request|other/ui|refs/pull/3/merge|main
pull_request_target|Verjson/ui|refs/heads/main|main
merge_group||refs/heads/x|main
TABLE
else
  fail "could not extract pulumi-ci admission step for parity"
fi

# The classifier must read the REAL context; the harness above substitutes these by hand.
classify_step="$(step_block "$wf" 'Classify event trust')"
for binding in \
  "EVENT_NAME: \${{ github.event_name }}" \
  "REPOSITORY: \${{ github.repository }}" \
  "HEAD_REPOSITORY: \${{ github.event.pull_request.head.repo.full_name }}" \
  "REF: \${{ github.ref }}" \
  "DEFAULT_BRANCH: \${{ github.event.repository.default_branch }}" \
  "CREDENTIALLESS_UNTRUSTED: \${{ inputs.credentialless-untrusted }}"; do
  grep -qF "$binding" <<<"$classify_step" \
    && pass "classifier binds ${binding%%:*} to the real context" \
    || fail "classifier does not bind: $binding"
done

# Wiring.
job="$(awk '/^  build-test:/{f=1} f' "$wf")"
checkout="$(step_block "$wf" 'Check out')"
[ -z "$checkout" ] && checkout="$(awk '/uses: actions\/checkout@/{f=1} f&&/^      - name: Set up/{exit} f' <<<"$job")"
grep -qF "persist-credentials: \${{ steps.trust.outputs.mode == 'job' }}" <<<"$checkout" \
  && pass "only a trusted ref persists the checkout token" || fail "persist-credentials is not mode-gated"
! grep -qE '^ *ref:' <<<"$checkout" \
  && pass "checkout keeps the default ref (the event commit), which the runner-isolation scan can bind" \
  || fail "checkout selects a runtime ref the isolation scan cannot bind"
grep -qF "token: \${{ steps.trust.outputs.mode != 'none' && secrets.submodules-token || github.token }}" <<<"$checkout" \
  && pass "submodule token is withheld in credentialless mode" || fail "submodule token is not mode-gated"
first_step="$(grep -m1 -E '^      - ' <<<"$job")"
[ "$first_step" = '      - name: Classify event trust' ] \
  && pass "classification is the first step" || fail "classification is not first (got: $first_step)"

with_creds="$(step_block "$wf" 'Set up Node + @verjson registry (trusted ref)')"
without="$(step_block "$wf" 'Set up Node + @verjson registry (credentials scoped to install)')"
grep -qF "if: steps.trust.outputs.mode == 'job'" <<<"$with_creds" \
  && grep -qF 'node-auth-token: ${{ secrets.node-auth-token }}' <<<"$with_creds" \
  && pass "job-wide credentials reach setup only on a trusted ref" || fail "job-wide credential step is not trusted-ref only"
grep -qF "if: steps.trust.outputs.mode != 'job'" <<<"$without" \
  && ! grep -qE 'node-auth-token|git-token|secrets\.' <<<"$without" \
  && pass "untrusted events set up Node with no credential input" || fail "untrusted setup step carries credentials"

for step in 'Install schema submodule deps' 'Install deps'; do
  block="$(step_block "$wf" "$step")"
  grep -qF "NODE_AUTH_TOKEN: \${{ steps.trust.outputs.mode != 'none' && secrets.node-auth-token || '' }}" <<<"$block" \
    && grep -qF "VERJSON_GIT_TOKEN: \${{ steps.trust.outputs.mode != 'none' && secrets.git-token || '' }}" <<<"$block" \
    && grep -qF 'CREDENTIAL_MODE: ${{ steps.trust.outputs.mode }}' <<<"$block" \
    && grep -qF 'credential.https://github.com.helper' <<<"$block" \
    && pass "$step receives step-scoped, mode-gated credentials" \
    || fail "$step credentials are not step-scoped and mode-gated"
done
for step in Lint Typecheck Test Build; do
  block="$(step_block "$wf" "$step")"
  [ -n "$block" ] && ! grep -qE 'secrets\.|NODE_AUTH_TOKEN|VERJSON_GIT_TOKEN' <<<"$block" \
    && pass "$step step carries no credential" || fail "$step step references a credential"
done
job_settings="$(awk '/^    steps:/{exit} {print}' <<<"$job")"
! grep -qE 'secrets\.|NODE_AUTH_TOKEN|VERJSON_GIT_TOKEN' <<<"$job_settings" \
  && pass "no job-level env or setting carries a credential" || fail "a job-level setting carries a credential"
! grep -qE 'GITHUB_ENV' <(grep -v '^ *#' "$wf") \
  && pass "the workflow itself never exports to GITHUB_ENV" || fail "the workflow writes GITHUB_ENV"
grep -qF 'git-token: ${{ secrets.git-token }}' <<<"$with_creds" \
  && pass "a trusted ref still hands the Git token to the composite" || fail "trusted ref lost the Git token"
# The install wrapper: executed for real.
install_run="$(step_run "$wf" 'Install deps')"
schema_run="$(step_run "$wf" 'Install schema submodule deps')"
[ -n "$install_run" ] || fail "no Install deps run block"
git_global() { # <mode> <git token> -> GIT_CONFIG_GLOBAL as the install command sees it
  CREDENTIAL_MODE="$1" VERJSON_GIT_TOKEN="$2" \
    INSTALL_COMMAND='printf "%s" "${GIT_CONFIG_GLOBAL:-unset}"' bash -c "$install_run" 2>/dev/null
}
[ "$(git_global job tok)" = unset ] && pass "a trusted ref leaves the runner's Git configuration alone" || fail "a trusted ref overrides Git configuration"
[ "$(git_global install tok)" = /dev/null ] && pass "install mode wires a step-only Git credential helper" || fail "install mode does not wire Git credentials"
git_wiring() { # <mode> <token> -> the effective count and helper
  CREDENTIAL_MODE="$1" VERJSON_GIT_TOKEN="$2" \
    INSTALL_COMMAND='printf "%s|%s" "${GIT_CONFIG_COUNT:-0}" "${GIT_CONFIG_KEY_2:-}"' bash -c "$install_run" 2>/dev/null
}
[ "$(git_wiring install tok)" = '3|credential.https://github.com.helper' ] \
  && pass "install mode registers all three Git rewrites and the credential helper" \
  || fail "install mode Git wiring is incomplete"
[ "$(git_global install '')" = unset ] && pass "install mode without a Git token leaves Git configuration alone" || fail "an empty token still overrides Git configuration"
[ "$(git_global none '')" = unset ] && pass "credentialless mode leaves Git configuration alone" || fail "credentialless mode overrides Git configuration"
wrapper_of() { sed -n '/^set -euo pipefail$/,/^fi$/p' <<<"$1"; }
[ -n "$(wrapper_of "$schema_run")" ] && [ "$(wrapper_of "$schema_run")" = "$(wrapper_of "$install_run")" ] \
  && pass "the schema install uses the same credential wrapper" || fail "schema install wrapper differs"
grep -qE '^      credentialless-untrusted:' "$wf" \
  && pass "credentialless-untrusted input exists" || fail "credentialless-untrusted input is missing"

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; } || { echo "$fails test(s) failed."; exit 1; }
