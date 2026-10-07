#!/usr/bin/env bash
# Unit tests for scripts/required-checks-audit.sh against a stubbed `gh`.
#
# The audit is read-only, so the risk is not that it breaks something — it is
# that it reports "conformant" for a repository that would in fact be wedged the
# moment the rule is written. Every test below is therefore about the audit
# REFUSING to say yes: unclassified repositories, absent contexts, and the one
# shape that looks absent but is fine (a skipped check run).
# shellcheck disable=SC2015  # Compact assertions intentionally use A && pass || fail.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/required-checks-audit.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$script" ] || { echo "FAIL - audit script not found"; exit 1; }
contract="$here/../.github/required-check-contract.json"

if grep -qE 'gh api .*(-X|--method)|gh (repo|api) (edit|delete)' "$script"; then
  fail "read-only audit contains a GitHub mutation path"
else
  pass "audit contains no GitHub mutation command"
fi

jq -e '
  .mode == "staged" and
  .mutation_authorized == true and
  .ruleset_plan.requested_enforcement == "active" and
  .ruleset_plan.human_gate_required == true and
  .ruleset_plan.rollout == {
    issue: 731,
    organization: "Verjson",
    ruleset_id: 20515817,
    ruleset_name: "core-checks-node",
    required_context: "changelog-contract",
    apply_acknowledgement: "apply-issue-731-core-checks-node",
    rollback_acknowledgement: "rollback-issue-731-core-checks-node"
  } and
  (has("universal_contexts") | not) and
  .stacks.node.contexts == ["ci / build-test", "ci / eligibility", "changelog-contract", "changelog / validate"] and
  .stacks.ui.contexts == ["ci / build-test", "changelog / validate"] and
  .stacks.helm.contexts == ["ci / lint-template", "changelog / validate"] and
  .caller_job_names.changelog == "changelog" and
  (.caller_job_names | has("generated_artifacts") | not) and
  .stacks.pulumi.contexts == ["ci / validate", "ci / preview"] and
  .stacks.actions.contexts == ["shell-tests"] and
  ([.ruleset_plan.rulesets[].name] | sort) == ([
    "changelog-contract-required", "core-checks-actions", "core-checks-helm",
    "core-checks-node", "core-checks-node-floor", "core-checks-pulumi", "core-checks-ui"
  ] | sort) and
  (.ruleset_plan.rulesets[] | select(.name == "core-checks-node-floor") | .contexts) ==
    ["ci-node22 / build-test", "ci-node22 / eligibility"] and
  (.ruleset_plan.rulesets[] | select(.name == "core-checks-node-floor")
    | [.repository_properties[].name]) ==
    ["verjson-stack", "verjson-core-checks", "verjson-node-floor"] and
  .property_schemas["verjson-node-floor"] == ["disabled", "node22"] and
  (.ruleset_plan.rulesets[] | select(.name == "core-checks-node") | .contexts) ==
    ["ci / build-test", "ci / eligibility", "changelog-contract"] and
  (.ruleset_plan.rulesets[] | select(.name == "changelog-contract-required") | .contexts) ==
    ["changelog / validate"]
' "$contract" >/dev/null \
  && pass "declared contract pins every context and a human-gated staged rollout" \
  || fail "required-check declaration or staged rollout drifted"

mkdir -p "$tmp/bin" "$tmp/checks"
content_root="$tmp/content"
mkdir -p "$content_root/.github/workflows" "$content_root/scripts"
contract_pin="$(git -C "$here/.." rev-parse HEAD)"
generator_source="$tmp/gen-changelog-caller.sh"
if ! git -C "$here/.." show "$contract_pin:scripts/gen-changelog-caller.sh" >"$generator_source"; then
  fail "the pinned changelog generator could not be loaded"
  exit 1
fi
run_generator() {
  (cd "$here" && bash -s -- "$@" <"$generator_source")
}
generated_pr_gate_state="$(
  run_generator pr-gate "$contract_pin" \
    | python3 -I "$here/required-checks-workflow.py" changelog-contract
)"
jq -e '.changelog_contract == "valid" and .pull_request == true and .path_filter == false' \
  <<<"$generated_pr_gate_state" >/dev/null \
  && pass "the exact generated pr-gate satisfies the workflow classifier" \
  || fail "the generated pr-gate and workflow classifier contract drifted"
run_generator generated-artifacts "$contract_pin" >"$content_root/.github/workflows/changelog.yml"
run_generator renderer "$contract_pin" >"$content_root/scripts/render-next.sh"
run_generator contract-test "$contract_pin" --only-package-dir packages/cli-schema \
  --release-caller-package-dirs .github/workflows/release-extra.yml=compat \
  >"$content_root/scripts/changelog-contract.test.sh"
run_generator codeowners "$contract_pin" >"$content_root/.github/CODEOWNERS"
run_generator release-node "$contract_pin" --only-package-dir packages/cli-schema >"$content_root/.github/workflows/release.yml"
run_generator release-node "$contract_pin" --only-package-dir compat >"$content_root/.github/workflows/release-extra.yml"
run_generator pr-gate "$contract_pin" >"$content_root/.github/workflows/changelog-contract.yml"
mkdir -p "$tmp/artifact-baseline/.github/workflows" "$tmp/artifact-baseline/scripts"
cp "$content_root/.github/workflows/changelog.yml" "$tmp/artifact-baseline/.github/workflows/changelog.yml"
cp "$content_root/.github/workflows/release.yml" "$tmp/artifact-baseline/.github/workflows/release.yml"
cp "$content_root/.github/workflows/changelog-contract.yml" "$tmp/artifact-baseline/.github/workflows/changelog-contract.yml"
cp "$content_root/scripts/render-next.sh" "$tmp/artifact-baseline/scripts/render-next.sh"
cp "$content_root/scripts/changelog-contract.test.sh" "$tmp/artifact-baseline/scripts/changelog-contract.test.sh"
# Stub `gh`. The real one applies `--jq` client-side; a stub that ignored it
# would hand the script raw JSON where it expects a stream, and every lookup
# would read as "nothing found" — the audit would then report every context
# missing and the tests would pass for the wrong reason.
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
filter=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  [ "${args[$i]}" = "--jq" ] && filter="${args[$((i + 1))]}"
done
emit() { if [ -n "$filter" ]; then jq -r "$filter" "$1"; else cat "$1"; fi; }
sha_of() { for a in "$@"; do case "$a" in *commits/*) s="${a#*commits/}"; printf '%s' "${s%%/*}"; return;; esac; done; }
case "$*" in
  *"orgs/"*"/repos"*)
    [ "${REPOS_FAIL:-false}" = true ] && exit 1
    [[ " $* " == *" --paginate "* ]] || exit 65
    emit "$REPOS_FILE"; exit 0 ;;
  *"/properties/values"*)
    [ "${PROPS_FAIL:-false}" = true ] && exit 1
    # Properties are fetched per repository, so a mixed-stack run needs a
    # per-repository answer. A test that only calls `stack` gets the single
    # shared file, exactly as before.
    #
    # #1225: the repo name used to fall out of `${args[1]}` unconditionally,
    # trusting that position to hold this exact endpoint shape. A caller (or a
    # future stub change) that put anything else there would still parse to
    # *some* string and silently answer with the wrong repository's (or the
    # shared) properties file instead of failing — the real script would then
    # see one repository's classification for another's. Assert the shape
    # before trusting it.
    endpoint="${args[1]}"
    case "$endpoint" in
      repos/*/*/properties/values) : ;;
      *) printf 'unexpected properties endpoint: %s\n' "$endpoint" >&2; exit 65 ;;
    esac
    repo="${endpoint#repos/*/}"; repo="${repo%%/*}"
    if [ -f "$STUB_TMP/props-$repo.json" ]; then
      emit "$STUB_TMP/props-$repo.json"
    else
      emit "$PROPS_FILE"
    fi
    exit 0 ;;
  *"repos/Verjson/.github/contents/scripts/gen-changelog-caller.sh"*)
    printf 'fetch\n' >>"$GENERATOR_FETCHES"
    git -C "$REPO_ROOT" show "$CONTRACT_PIN:scripts/gen-changelog-caller.sh"; exit 0 ;;
  *"commits?per_page=1"*)
printf '[{"sha":"%s"}]\n' "$CONTRACT_PIN" | jq -r "$filter"; exit 0 ;;
 *"repos/Verjson/.github/compare/"*) printf '{"status":"%s"}\n' "${PIN_ANCESTRY_STATUS:-identical}" | { if [ -n "$filter" ]; then jq -r "$filter"; else cat; fi; }; exit 0 ;;
  *"repos/Verjson/.github/branches/main"*) printf '{"commit":{"sha":"%s"}}\n' "$CONTRACT_PIN" | { if [ -n "$filter" ]; then jq -r "$filter"; else cat; fi; }; exit 0 ;;
  *"repos/Verjson/.github"*) printf '{"default_branch":"main"}\n' | { if [ -n "$filter" ]; then jq -r "$filter"; else cat; fi; }; exit 0 ;;
  *"/contents/.github/workflows"|*"/contents/.github/workflows?ref="*)
    [ "${WORKFLOWS_FAIL:-false}" = true ] && exit 1
    emit "$WORKFLOW_LIST_FILE"; exit 0 ;;
  *"/contents/"*"application/vnd.github.raw+json"*)
    endpoint="${args[1]}"
    path="${endpoint#*contents/}"; path="${path%%\?*}"
    printf '%s\n' "$endpoint" >>"$STUB_TMP/content-api-requests"
    case "$path" in *%*) path="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.unquote(sys.argv[1]))' "$path")" ;; esac
    printf '%s\n' "$path" >>"$STUB_TMP/raw-content-fetches"
    cat "$CONTENT_ROOT/$path"
    exit 0 ;;
  *"/contents/"*)
    [ "${WORKFLOWS_FAIL:-false}" = true ] && exit 1
    endpoint="${args[1]}"
    printf '%s\n' "$endpoint" >>"$STUB_TMP/content-api-requests"
    path="${endpoint#*contents/}"; path="${path%%\?*}"
    case "$path" in *%*) path="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.unquote(sys.argv[1]))' "$path")" ;; esac
    source="$CONTENT_ROOT/$path"
    [ -f "$source" ] || exit 1
    response="$STUB_TMP/content-response.json"
    if [ "${CONTENT_ENCODING_NONE:-false}" = true ]; then
    content_size="$(wc -c <"$source")"
    [ -z "${CONTENT_SIZE_OVERRIDE:-}" ] || content_size="$CONTENT_SIZE_OVERRIDE"
    jq -n --argjson size "$content_size" \
        '{content:"",encoding:"none",size:$size}' >"$response"
      emit "$response"; exit 0
    fi
    # Through a file, not argv. A single argument is capped at MAX_ARG_STRLEN
    # (128 KiB on Linux), and base64 is 4/3 of the source -- so this stub used
    # to start failing with E2BIG once a generated artifact passed ~96 KiB.
    # `jq` then wrote nothing, `base64 --decode` succeeded on the empty stream,
    # and the audit reported invalid PARAMETERS three checks later instead of an
    # unreadable artifact. The generated contract test is 96 KiB and growing.
    base64 -w0 "$source" | tr -d '\n' >"$STUB_TMP/content-b64"
    jq -n --rawfile content "$STUB_TMP/content-b64" --argjson size "$(wc -c <"$source")" \
      '{content:$content,encoding:"base64",size:$size}' >"$response"
    emit "$response"; exit 0 ;;
  *"/pulls?"*)
    [ "${PULLS_FAIL:-false}" = true ] && exit 1
    emit "$PULLS_FILE"; exit 0 ;;
  *"/check-runs"*)
    [ "${CHECKS_FAIL:-false}" = true ] && exit 1
    [[ " $* " == *" --paginate "* ]] || exit 65
    emit "$CHECKDIR/$(sha_of "$@").json"; exit 0 ;;
  *"/status?"*)
    [ "${STATUSES_FAIL:-false}" = true ] && exit 1
    [[ " $* " == *" --paginate "* ]] || exit 65
    f="$CHECKDIR/$(sha_of "$@").status.json"
    [ -f "$f" ] || printf '{"statuses":[]}\n' >"$f"
    emit "$f"; exit 0 ;;
esac
echo "unexpected gh call: $*" >&2
exit 64
GH
chmod +x "$tmp/bin/gh"

cat >"$tmp/bin/curl" <<'CURL'
#!/usr/bin/env bash
url="${*: -1}"
case "$url" in
  https://raw.githubusercontent.com/[Vv]er[Jj][Ss][Oo][Nn]/.github/*/scripts/changelog.py)
    ref="${url#*/.github/}"; ref="${ref%%/*}"
    git -C "$REPO_ROOT" show "$ref:scripts/changelog.py" ;;
  https://raw.githubusercontent.com/[Vv]er[Jj][Ss][Oo][Nn]/.github/*/scripts/gen-adr-index.sh)
    ref="${url#*/.github/}"; ref="${ref%%/*}"
    git -C "$REPO_ROOT" show "$ref:scripts/gen-adr-index.sh" ;;
  https://raw.githubusercontent.com/[Vv]er[Jj][Ss][Oo][Nn]/.github/*/scripts/ci-gate/gen-adr-index.test.sh)
    ref="${url#*/.github/}"; ref="${ref%%/*}"
    git -C "$REPO_ROOT" show "$ref:scripts/ci-gate/gen-adr-index.test.sh" ;;
  *) exit 1 ;;
esac
CURL
chmod +x "$tmp/bin/curl"

export PATH="$tmp/bin:$PATH"
export CHECKDIR="$tmp/checks"
export PROPS_FILE="$tmp/props.json"
export PULLS_FILE="$tmp/pulls.json"
export REPOS_FILE="$tmp/repos.json"
export WORKFLOW_LIST_FILE="$tmp/workflows.json"
export WORKFLOW_CONTENT_FILE="$tmp/workflow-content.json"
export CONTENT_ROOT="$content_root"
export CONTRACT_PIN="$contract_pin"
export GENERATOR_FETCHES="$tmp/generator-fetches.log"
export REPO_ROOT="$here/.."
export STUB_TMP="$tmp"
export RCA_ORG=Verjson
export RCA_SAMPLE_PRS=2
export RCA_REPOS=alpha
: >"$GENERATOR_FETCHES"

workflow_for() {
  local stack="$1" stack_workflow=''
  cp "$tmp/artifact-baseline/.github/workflows/changelog.yml" "$content_root/.github/workflows/changelog.yml"
  cp "$tmp/artifact-baseline/.github/workflows/release.yml" "$content_root/.github/workflows/release.yml"
  cp "$tmp/artifact-baseline/.github/workflows/changelog-contract.yml" "$content_root/.github/workflows/changelog-contract.yml"
  cp "$tmp/artifact-baseline/scripts/render-next.sh" "$content_root/scripts/render-next.sh"
  cp "$tmp/artifact-baseline/scripts/changelog-contract.test.sh" "$content_root/scripts/changelog-contract.test.sh"
  case "$stack" in
    node) stack_workflow=node-ci.yml ;;
    ui) stack_workflow=ui-ci.yml ;;
    helm) stack_workflow=helm-ci.yml ;;
    pulumi) stack_workflow=pulumi-ci.yml ;;
    actions|none) stack_workflow='' ;;
  esac
  {
    printf 'name: ci\non:\n  pull_request:\npermissions:\n  contents: read\njobs:\n'
    if [ -n "$stack_workflow" ]; then
      printf '  ci:\n    uses: Verjson/.github/.github/workflows/%s@0123456789abcdef0123456789abcdef01234567\n' "$stack_workflow"
    fi
    if [ "$stack" = node ]; then
      printf '  changelog-contract:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n        with:\n          persist-credentials: false\n      - run: echo "VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools" >> "$GITHUB_ENV"\n      - run: bash scripts/changelog-contract.test.sh\n'
    fi
  } >"$tmp/workflow.yml"
  encode_workflow
  printf '[{"type":"file","path":".github/workflows/ci.yml"},{"type":"file","path":".github/workflows/changelog.yml"},{"type":"file","path":".github/workflows/release.yml"}]\n' >"$WORKFLOW_LIST_FILE"
}

encode_workflow() {
  cp "$tmp/workflow.yml" "$content_root/.github/workflows/ci.yml"
}

stack() {
  rm -f "$tmp"/props-*.json
  jq -n --arg v "$1" '[{property_name:"verjson-stack", value:$v}]' >"$PROPS_FILE"
  workflow_for "$1"
}
# Per-repository classification, for a run that spans more than one stack. The
# shared content root still answers every workflow lookup, which is sound
# precisely because a skipped repository returns before it makes one.
stack_for() { # $1 = repo, $2 = stack
  jq -n --arg v "$2" '[{property_name:"verjson-stack", value:$v}]' >"$tmp/props-$1.json"
}
no_stack() { rm -f "$tmp"/props-*.json; printf '[]\n' >"$PROPS_FILE"; }
pulls() { jq -n --args '$ARGS.positional | map({merged_at:"2026-08-05T00:00:00Z", head:{sha:.}})' "$@" >"$PULLS_FILE"; }
# Named check runs, all concluded `success` unless a test overrides the file.
head_with() { local sha="$1"; shift; jq -n --args '{check_runs: ($ARGS.positional | map({name:., conclusion:"success"}))}' "$@" >"$CHECKDIR/$sha.json"; }

printf '[{"name":"alpha","archived":false}]\n' >"$REPOS_FILE"
workflow_for none

run_audit() { ( bash "$script" "$@" >"$tmp/out.txt" 2>&1; echo "rc=$?" ); }
out() { cat "$tmp/out.txt"; }

# ADR 0128 removed universal authorization contexts from this deterministic
# ruleset contract. A substituted declaration must not be able to restore that
# retired key while the audit silently ignores it.
hostile_contract="$tmp/contract-with-universal-contexts.json"
jq '.universal_contexts = ["gate"]' "$contract" >"$hostile_contract"
rc="$(RCA_CONTRACT_FILE="$hostile_contract" run_audit)"
{ [ "$rc" = "rc=2" ] && grep -q 'phase=contract result=declaration-invalid' "$tmp/out.txt"; } \
  && pass "a reintroduced universal context fails contract validation closed" \
  || { fail "a universal context was silently ignored ($rc)"; out | sed 's/^/diag - /'; }

# --- an unclassified repository is never called conformant -------------------
no_stack; pulls s1 s2; head_with s1 gate; head_with s2 gate
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'result=unclassified' "$tmp/out.txt"; } \
  && pass "a repository with no verjson-stack property is reported, never guessed" \
  || { fail "an unclassified repository was not flagged ($rc)"; out | sed 's/^/diag - /'; }

# --- a stack with no declared contract is a fault, not an empty contract -----
# An unknown stack whose contract silently resolved to "nothing" would report
# conformant for a repository nobody has thought about.
stack bogus; pulls s1 s2; head_with s1 gate; head_with s2 gate
rc="$(run_audit)"
{ [ "$rc" = "rc=2" ] && grep -q 'unknown-stack' "$tmp/out.txt"; } \
  && pass "an unknown stack is a fault, not an empty core contract" \
  || { fail "an unknown stack did not fault ($rc)"; out | sed 's/^/diag - /'; }

# --- a declared stack with no required contexts is skipped, not a fault ------
# `none` is a real classification: it says the audit has nothing to require of
# this repository. Faulting on it aborts the whole unscoped run at whichever
# such repository happens to sort first, so an operator asking "is the
# organization conformant?" gets no per-repository results at all (#1213).
stack none; pulls s1 s2; head_with s1 gate; head_with s2 gate
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ] \
  && grep -q 'result=skipped' "$tmp/out.txt" \
  && grep -q 'phase=done .*skipped=1' "$tmp/out.txt"; } \
  && pass "a stack declaring no required contexts is skipped and tallied" \
  || { fail "a contextless stack did not skip cleanly ($rc)"; out | sed 's/^/diag - /'; }

# --- a skip does not stop the run at the skipped repository ------------------
# The #1213 symptom was that the whole unscoped run aborted, so the assertion
# that matters is that a repository sequenced *after* a skipped one is still
# audited. A single-repository run can only show that `phase=done` was reached.
stack node
stack_for none-repo none
stack_for beta-node node
pulls s1 s2
head_with s1 "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
head_with s2 "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
rc="$(RCA_REPOS="none-repo beta-node" run_audit)"
{ [ "$rc" = "rc=0" ] \
  && grep -q 'repo=none-repo .*result=skipped' "$tmp/out.txt" \
  && grep -q 'repo=beta-node .*result=conformant' "$tmp/out.txt" \
  && grep -q 'phase=done conformant=1 nonconformant=0 unclassified=0 unaudited=0 skipped=1' "$tmp/out.txt"; } \
  && pass "a skipped repository does not stop the repositories after it" \
  || { fail "a skip did not leave the rest of the run intact ($rc)"; out | sed 's/^/diag - /'; }

# --- a skip is not a pass for a caller that is about to apply a rule ---------
# `required-checks-rollout.sh` selects repositories by the ruleset's own
# properties, not by `verjson-stack`, so a contextless stack can sit inside the
# set it is about to gate. "Nothing was verified" must not read to that caller as
# "verified fine", or the rollout requires a context nobody confirmed the
# repository emits — the permanent-pending wedge this audit exists to prevent.
stack none; pulls s1 s2; head_with s1 gate; head_with s2 gate
rc="$(run_audit --require-verified)"
{ [ "$rc" != "rc=0" ] \
  && grep -q 'result=unverifiable' "$tmp/out.txt" \
  && grep -q '::error::' "$tmp/out.txt"; } \
  && pass "a skip fails closed for a caller that requires positive verification" \
  || { fail "a skip passed a caller that is about to apply a rule ($rc)"; out | sed 's/^/diag - /'; }

# Every other clause of the exit gate counts what went wrong, so an audit that
# examined nothing satisfies all of them. For the org-wide report that is a fair
# answer; for a caller about to write a required check it is the same fail-open
# in its purest form — nothing was verified, and nothing complained.
rc="$(RCA_REPOS=" " run_audit --require-verified)"
{ [ "$rc" != "rc=0" ] && grep -q 'conformant=0' "$tmp/out.txt"; } \
  && pass "an audit that verified no repository is not a pass for an applying caller" \
  || { fail "an empty audit passed a caller that is about to apply a rule ($rc)"; out | sed 's/^/diag - /'; }

rc="$(RCA_REPOS=" " run_audit)"
[ "$rc" = "rc=0" ] \
  && pass "the read-only report still tolerates an empty repository set" \
  || { fail "the read-only report regressed on an empty set ($rc)"; out | sed 's/^/diag - /'; }

rc="$(run_audit --require-verified=sometimes)"
{ [ "$rc" = "rc=2" ] && grep -q 'unrecognized-argument' "$tmp/out.txt"; } \
  && pass "a malformed --require-verified argument fails closed rather than defaulting to lax" \
  || { fail "a malformed --require-verified argument was accepted ($rc)"; out | sed 's/^/diag - /'; }

# --- a malformed context list is a contract fault, with an annotation --------
# Fail-closed is not enough on its own: an unannotated `jq: error ... Cannot
# iterate over null` gives Actions nothing to surface and makes the rollout
# report a contract defect as repository nonconformance.
#
# The whitespace shapes are the sharp ones. A context of "   " is non-empty to
# jq and empty to the shell `read` that consumes it, so a `length > 0` schema
# admits it, the contract resolves NON-empty, every context is skipped, and the
# repository is reported conformant having been checked against nothing — the
# fail-open of #1221 reappearing one level down. " ci / build-test " is the
# quieter form: it passes, but against a different string than the one declared.
stack none; pulls s1 s2; head_with s1 gate; head_with s2 gate
for shape in 'null' '{}' '[""]' '["", ""]' '["ok", 3]' '["   "]' '["\t"]' \
  '[" gate "]' '["gate\ngate"]' '{"contexts": ["gate"]}'; do
  malformed="$tmp/contract-contexts-$(printf '%s' "$shape" | md5sum | cut -c1-8).json"
  jq --argjson v "$shape" '.stacks.none.contexts = $v' "$contract" >"$malformed"
  rc="$(RCA_CONTRACT_FILE="$malformed" run_audit)"
  { [ "$rc" = "rc=2" ] && grep -q 'phase=contract result=declaration-invalid' "$tmp/out.txt"; } \
    && pass "a contexts list of $shape is an annotated declaration fault" \
    || { fail "contexts=$shape was not rejected at the declaration boundary ($rc)"; out | sed 's/^/diag - /'; }
done

# The other arms of the same schema: a stack that is not an object, and one with
# no contexts key at all. Both previously reached the audit as "requires
# nothing".
for shape in 'null' '[]' '"node"' '{"other": []}'; do
  malformed="$tmp/contract-stack-$(printf '%s' "$shape" | md5sum | cut -c1-8).json"
  jq --argjson v "$shape" '.stacks.none = $v' "$contract" >"$malformed"
  rc="$(RCA_CONTRACT_FILE="$malformed" run_audit)"
  { [ "$rc" = "rc=2" ] && grep -q 'phase=contract result=declaration-invalid' "$tmp/out.txt"; } \
    && pass "a stack declared as $shape is an annotated declaration fault" \
    || { fail "stack=$shape was not rejected at the declaration boundary ($rc)"; out | sed 's/^/diag - /'; }
done

# --- an ambient environment variable of the same name has no effect ---------
# #1223: RCA_REQUIRE_VERIFIED used to be read as a plain env var, so a value
# exported by an unrelated parent process (or inherited across a CI step
# boundary) could silently flip the read-only org-wide report to fail-closed
# with no caller ever asking for that. It is now a positional flag only.
stack none; pulls s1 s2; head_with s1 gate; head_with s2 gate
rc="$(RCA_REQUIRE_VERIFIED=true run_audit)"
[ "$rc" = "rc=0" ] \
  && pass "an ambient RCA_REQUIRE_VERIFIED env var does not affect the read-only report" \
  || { fail "an ambient env var still flips require-verified ($rc)"; out | sed 's/^/diag - /'; }

rc="$(run_audit --bogus-flag)"
{ [ "$rc" = "rc=2" ] && grep -q 'unrecognized-argument' "$tmp/out.txt"; } \
  && pass "an unrecognized argument faults rather than being silently ignored" \
  || { fail "an unrecognized argument was silently accepted ($rc)"; out | sed 's/^/diag - /'; }

# --- the happy path ----------------------------------------------------------
stack node
pulls s1 s2
head_with s1 "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
head_with s2 "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ] && grep -q 'result=conformant' "$tmp/out.txt"; } \
  && pass "a node repository emitting its full core set is conformant" \
  || { fail "a conformant node repository was not recognised ($rc)"; out | sed 's/^/diag - /'; }

mkdir -p "$tmp/hostile-python"
printf 'raise SystemExit("ambient yaml module executed")\n' >"$tmp/hostile-python/yaml.py"
rc="$(PYTHONPATH="$tmp/hostile-python" run_audit)"
{ [ "$rc" = "rc=0" ] && grep -q 'result=conformant' "$tmp/out.txt"; } \
  && pass "workflow inspection ignores ambient Python packages" \
  || { fail "an ambient Python package changed audit behavior ($rc)"; out | sed 's/^/diag - /'; }

sed -i '1i---' "$content_root/.github/workflows/ci.yml"
printf '\n...\n' >>"$content_root/.github/workflows/ci.yml"
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ] && grep -q 'result=conformant' "$tmp/out.txt"; } \
  && pass "valid YAML document markers do not fault workflow inspection" \
  || { fail "YAML document markers changed audit behavior ($rc)"; out | sed 's/^/diag - /'; }

cp "$content_root/.github/workflows/ci.yml" "$tmp/canonical-ci.yml"
for unsupported_yaml in \
  'jobs: &shared-jobs' \
  '  <<: *shared-job' \
  'on: !canonical pull_request' \
  'on: ! {pull_request: {}}' \
  'jobs: {base: &base {runs-on: ubuntu-24.04}, copy: *base}' \
  'jobs: {base: !<tag:example.com,2026:job> {runs-on: ubuntu-24.04}}' \
  'jobs: {base: &base !canonical {runs-on: ubuntu-24.04}}'; do
  cp "$tmp/canonical-ci.yml" "$content_root/.github/workflows/ci.yml"
  printf '\n%s\n' "$unsupported_yaml" >>"$content_root/.github/workflows/ci.yml"
  rc="$(run_audit)"
  { [ "$rc" != "rc=0" ] && ! grep -q 'result=conformant' "$tmp/out.txt"; } \
    && pass "unsupported YAML syntax fails workflow inspection closed: $unsupported_yaml" \
    || { fail "unsupported YAML syntax was accepted: $unsupported_yaml ($rc)"; out | sed 's/^/diag - /'; }
done
cp "$tmp/canonical-ci.yml" "$content_root/.github/workflows/ci.yml"

block_scalar_result="$tmp/block-scalar-result.json"
printf '%s\n' \
  'jobs:' \
  '  example:' \
  '    if: ${{ !cancelled() }}' \
  '    steps:' \
  '      - run: |' \
  '          echo result: !important' \
  | python3 -I "$here/required-checks-workflow.py" changelog >"$block_scalar_result"
rc=$?
{ [ "$rc" -eq 0 ] && jq -e '.changelog_contract == "absent"' "$block_scalar_result" >/dev/null; } \
  && pass "YAML-like shell text inside a block scalar remains supported content" \
  || fail "block scalar shell content was mistaken for unsupported YAML syntax (rc=$rc)"

rc="$(RCA_WORKFLOW_INSPECTOR="$tmp/missing-workflow-inspector.py" run_audit)"
{ [ "$rc" = "rc=2" ] && grep -q 'workflow-inspector-missing' "$tmp/out.txt"; } \
  && pass "a missing hermetic workflow inspector fails at startup" \
  || { fail "the audit ran without its workflow inspector ($rc)"; out | sed 's/^/diag - /'; }

stack node; pulls s1 s2
head_with s1 gate "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
head_with s2 gate "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
run_generator workflow "$contract_pin" >"$content_root/.github/workflows/changelog.yml"
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ] && grep -q 'result=conformant' "$tmp/out.txt"; } \
  && pass "the documented workflow compatibility mode remains conformant" \
  || { fail "workflow compatibility mode failed provenance ($rc)"; out | sed 's/^/diag - /'; }

# The documented single-caller layout is one .github/workflows/changelog.yml
# generated at the shared pin. Exercise every package stack through the full
# source audit so the fixture cannot drift back to the retired file path.
for documented_stack in node ui helm; do
  stack "$documented_stack"; pulls s1 s2
  case "$documented_stack" in
    node)
      head_with s1 gate "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
      head_with s2 gate "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
      ;;
    ui)
      head_with s1 gate "ci / build-test" "changelog / validate"
      head_with s2 gate "ci / build-test" "changelog / validate"
      ;;
    helm)
      head_with s1 gate "ci / lint-template" "changelog / validate"
      head_with s2 gate "ci / lint-template" "changelog / validate"
      ;;
  esac
  rc="$(run_audit)"
  { [ "$rc" = "rc=0" ] && grep -q 'result=conformant' "$tmp/out.txt"; } \
    && pass "the documented $documented_stack changelog.yml layout is conformant" \
    || { fail "the documented $documented_stack layout was rejected ($rc)"; out | sed 's/^/diag - /'; }
done

# --- THE finding this exists for: a missing context is non-zero --------------
# A generated release contract can drift independently of fragment validation.
# Requiring only `changelog / validate` leaves the caller, renderer,
# contract test, and release caller free to disagree about their immutable pin.
stack node
pulls s1 s2
head_with s1 gate "ci / build-test" "ci / eligibility" "changelog / validate"
head_with s2 gate "ci / build-test" "ci / eligibility" "changelog / validate"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'missing-core-contexts' "$tmp/out.txt" && grep -q 'missing=changelog-contract;' "$tmp/out.txt"; } \
  && pass "a node repository omitting generated contract conformance is nonconformant" \
  || { fail "an absent changelog-contract context was not reported ($rc)"; out | sed 's/^/diag - /'; }

# `changelog / validate` is core for package repositories, and a repository not
# yet wired to the changelog contract emits nothing for it. Requiring it there
# is the permanently-pending wedge.
stack node
pulls s1 s2
head_with s1 gate "ci / build-test" "ci / eligibility" changelog-contract
head_with s2 gate "ci / build-test" "ci / eligibility" changelog-contract
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'missing-core-contexts' "$tmp/out.txt" && grep -q 'changelog / validate' "$tmp/out.txt"; } \
  && pass "a package repository not wired to the changelog contract is reported as missing it" \
  || { fail "an absent core context was not reported ($rc)"; out | sed 's/^/diag - /'; }

# --- a SKIPPED check run is conformant, not missing --------------------------
# This is the distinction the whole design rests on: a conditional job reports
# `skipped` and satisfies a required check, while a paths-filtered workflow
# reports nothing and wedges. Calling `skipped` missing would send people to fix
# the one shape that is already correct.
stack node
pulls s1 s2
jq -n '{check_runs:[{name:"gate",conclusion:"success"},
                    {name:"ci / build-test",conclusion:"success"},
                    {name:"ci / eligibility",conclusion:"skipped"},
                    {name:"changelog-contract",conclusion:"success"},
                    {name:"changelog / validate",conclusion:"success"}]}' >"$CHECKDIR/s1.json"
cp "$CHECKDIR/s1.json" "$CHECKDIR/s2.json"
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ] && ! grep -q 'missing-core-contexts' "$tmp/out.txt"; } \
  && pass "a skipped check run satisfies the contract — only an absent one wedges" \
  || { fail "a skipped conditional job was reported as missing ($rc)"; out | sed 's/^/diag - /'; }

# --- stacks carry different contracts ---------------------------------------
# A helm repository must not be judged against node's contexts; if it were, the
# audit would demand `ci / build-test` from a repository that never emits it.
stack helm
pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ]; } \
  && pass "a helm repository is judged against helm's contract, not node's" \
  || { fail "helm was judged against the wrong contract ($rc)"; out | sed 's/^/diag - /'; }

# `pulumi` is deliberately NOT a package stack, so demanding the changelog
# context from it would be a false finding.
stack pulumi
pulls s1 s2
head_with s1 gate "ci / validate" "ci / preview"
head_with s2 gate "ci / validate" "ci / preview"
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ]; } \
  && pass "a non-package stack is not required to emit the changelog context" \
  || { fail "pulumi was wrongly required to emit changelog / validate ($rc)"; out | sed 's/^/diag - /'; }

# --- authorization-arm contexts are independent -----------------------------
stack actions
pulls s1 s2
head_with s1 "shell-tests"
head_with s2 "shell-tests"
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ] && ! grep -q 'missing=gate' "$tmp/out.txt" && ! grep -q 'missing=arm' "$tmp/out.txt"; } \
  && pass "authorization-arm contexts are not conflated with deterministic stack checks" \
  || { fail "authorization-arm context leaked into the stack contract ($rc)"; out | sed 's/^/diag - /'; }

# --- commit statuses count -----------------------------------------------------
# The audit reads check-runs AND statuses; a context delivered as a commit status
# must not be reported missing just because it is not a check run.
stack actions
pulls s1 s2
head_with s1
head_with s2
printf '{"statuses":[{"context":"shell-tests","state":"success"}]}\n' >"$CHECKDIR/s1.status.json"
printf '{"statuses":[{"context":"shell-tests","state":"success"}]}\n' >"$CHECKDIR/s2.status.json"
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ]; } \
  && pass "a context delivered as a commit status counts as present" \
  || { fail "a commit status context was reported missing ($rc)"; out | sed 's/^/diag - /'; }
rm -f "$CHECKDIR"/*.status.json

# --- unreadable APIs do not read as conformant -------------------------------
stack node; pulls s1 s2; head_with s1 gate; head_with s2 gate
rc="$(PULLS_FAIL=true run_audit)"
{ grep -q 'pulls-unreadable' "$tmp/out.txt" && ! grep -q 'result=conformant' "$tmp/out.txt"; } \
  && pass "an unreadable pulls API is reported, never counted as conformant" \
  || { fail "an API failure was absorbed ($rc)"; out | sed 's/^/diag - /'; }

rc="$(CHECKS_FAIL=true run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'checks-unreadable' "$tmp/out.txt" && ! grep -q 'result=conformant' "$tmp/out.txt"; } \
  && pass "an unreadable check-runs API is reported, never counted as conformant" \
  || { fail "an unreadable check API was absorbed ($rc)"; out | sed 's/^/diag - /'; }

rc="$(STATUSES_FAIL=true run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'checks-unreadable' "$tmp/out.txt" && ! grep -q 'result=conformant' "$tmp/out.txt"; } \
  && pass "a rate-limited status page is reported, never counted as conformant" \
  || { fail "a status pagination/rate failure was absorbed ($rc)"; out | sed 's/^/diag - /'; }

rc="$(PROPS_FAIL=true run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'properties-unreadable' "$tmp/out.txt"; } \
  && pass "an unreadable custom-property value fails closed" \
  || { fail "a property API failure was treated as classification ($rc)"; out | sed 's/^/diag - /'; }

# --- the stub's own endpoint-shape assumption is enforced, not implicit ------
# #1225: the repo name used to fall out of `${args[1]}` unconditionally. The
# production script only ever emits `repos/<org>/<repo>/properties/values`, so
# this is a self-test of the fixture rather than of the script: it asserts
# that a shape the script does not currently send would be caught loudly, not
# silently misparsed into the wrong (or shared) properties file.
gh_out=$("$tmp/bin/gh" api "repos/properties/values" --jq '.' 2>&1); gh_rc=$?
{ [ "$gh_rc" -eq 65 ] && grep -q 'unexpected properties endpoint' <<<"$gh_out"; } \
  && pass "the gh stub refuses a properties endpoint missing a path segment" \
  || { fail "the gh stub silently accepted a malformed properties endpoint (rc=$gh_rc)"; printf 'diag - %s\n' "$gh_out"; }

rc="$(WORKFLOWS_FAIL=true run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'workflow-source-unreadable' "$tmp/out.txt"; } \
  && pass "unreadable workflow source fails closed" \
  || { fail "a workflow-source API failure was absorbed ($rc)"; out | sed 's/^/diag - /'; }

# A successful Contents API response may contain a zero-byte workflow.
# Its empty source means no wiring; the separate WORKFLOWS_FAIL case above
# proves API failure still stops the audit as unreadable.
inspector_on_empty="$(python3 -I "$REPO_ROOT/scripts/required-checks-workflow.py" changelog </dev/null)"
inspector_on_empty_rc=$?
{ [ "$inspector_on_empty_rc" -eq 0 ] \
  && [ "$(jq -r '.changelog_contract' <<<"$inspector_on_empty")" = absent ] \
  && [ "$(jq -r '.generated_changelog' <<<"$inspector_on_empty")" = absent ] \
  && [ "$(jq -r '.path_filter' <<<"$inspector_on_empty")" = false ]; } \
  && pass "the workflow inspector reports an empty source as absent changelog wiring" \
  || { fail "the inspector no longer classifies an empty workflow as missing changelog wiring (rc=$inspector_on_empty_rc)"; printf 'diag - %s\n' "$inspector_on_empty"; }

stack node
workflow_for node
cp "$content_root/.github/workflows/ci.yml" "$content_root/.github/workflows/inert.txt"
printf '[{"type":"file","path":".github/workflows/inert.txt"}]\n' >"$WORKFLOW_LIST_FILE"
: >"$tmp/content-api-requests"
rc="$(run_audit)"
{
  [ "$rc" = "rc=1" ] &&
    grep -q 'result=stack-caller-missing' "$tmp/out.txt" &&
    ! grep -q 'workflow-source-unreadable' "$tmp/out.txt" &&
    ! grep -q 'inert.txt' "$tmp/content-api-requests" &&
    grep -q 'unaudited=0' "$tmp/out.txt"
} && pass "non-workflow files cannot satisfy the workflow audit" \
  || { fail "inert workflow-directory files were counted"; out | sed 's/^/diag - /'; }

# A newline in a GitHub path must not split the path stream.
stack node
workflow_for node
python3 - "$WORKFLOW_LIST_FILE" <<'PY'
import json, sys
with open(sys.argv[1], "w") as listing:
    json.dump([{"type": "file", "path": ".github/workflows/ci\n.yml"}], listing)
PY
: >"$tmp/content-api-requests"
rc="$(run_audit)"
{
 [ "$rc" = "rc=1" ] && grep -q 'result=stack-caller-missing' "$tmp/out.txt" &&
 ! grep -q 'workflow-source-unreadable' "$tmp/out.txt" &&
 [ ! -s "$tmp/content-api-requests" ] && grep -q 'unaudited=0' "$tmp/out.txt"
} && pass "control characters cannot split workflow paths" \
 || { fail "a control-character workflow path reached Contents API ($rc)"; out | sed 's/^/diag - /'; }

# Bound request count as well as response bytes; empty files still cost API calls.
stack node
workflow_for node
python3 - "$WORKFLOW_LIST_FILE" <<'PY'
import json, sys
with open(sys.argv[1], "w") as listing:
    json.dump([{"type": "file", "path": f".github/workflows/ci-{i}.yml"} for i in range(101)], listing)
PY
: >"$tmp/content-api-requests"
rc="$(run_audit)"
{
 [ "$rc" = "rc=1" ] && grep -q 'workflow-source-unreadable' "$tmp/out.txt" &&
 [ ! -s "$tmp/content-api-requests" ] && grep -q 'unaudited=1' "$tmp/out.txt"
} && pass "workflow file count is bounded before Contents API calls" \
 || { fail "an oversized workflow listing was not rejected before fetch ($rc)"; out | sed 's/^/diag - /'; }

stack node
workflow_for node
# The Contents API truncates directory listings at 1,000 entries; a full page may hide workflows.
stack node
workflow_for node
python3 - "$WORKFLOW_LIST_FILE" <<'PY'
import json, sys
entries = [{"type": "file", "path": ".github/workflows/ci.yml"}]
entries.extend({"type": "file", "path": f".github/workflows/inert-{i}.txt"} for i in range(999))
with open(sys.argv[1], "w") as listing:
    json.dump(entries, listing)
PY
: >"$tmp/content-api-requests"
rc="$(run_audit)"
{
 [ "$rc" = "rc=1" ] && grep -q 'workflow-source-unreadable' "$tmp/out.txt" &&
 [ ! -s "$tmp/content-api-requests" ] && grep -q 'unaudited=1' "$tmp/out.txt"
} && pass "a full Contents API directory page fails closed" \
 || { fail "a possibly truncated workflow listing was scanned as complete ($rc)"; out | sed 's/^/diag - /'; }

query_path='.github/workflows/ci?ref=wrong#frag.yml'
cp "$content_root/.github/workflows/ci.yml" "$content_root/$query_path"
printf '[{"type":"file","path":"%s"}]\n' "$query_path" >"$WORKFLOW_LIST_FILE"
printf 'alpha\tmain\t%s\n' "$contract_pin" >"$tmp/query-heads.tsv"
: >"$tmp/content-api-requests"
rc="$(RCA_HEADS_FILE="$tmp/query-heads.tsv" run_audit)"
{
  [ "$rc" = "rc=1" ] &&
    grep -q 'result=changelog-caller-missing' "$tmp/out.txt" &&
    ! grep -q 'workflow-source-unreadable' "$tmp/out.txt" &&
    grep -Fq "repos/Verjson/alpha/contents/.github/workflows/ci%3Fref%3Dwrong%23frag.yml?ref=$contract_pin" \
      "$tmp/content-api-requests" &&
    grep -q 'unaudited=0' "$tmp/out.txt"
} && pass "workflow paths are encoded before audited-ref queries" \
  || { fail "special workflow path altered the audited Contents API request ($rc)"; out | sed 's/^/diag - /'; }

stack node
workflow_for node
: >"$tmp/raw-content-fetches"
rc="$(CONTENT_ENCODING_NONE=true CONTENT_SIZE_OVERRIDE=$((5 * 1024 * 1024 + 1)) run_audit)"
{
  [ "$rc" = "rc=1" ] &&
    grep -q 'workflow-source-unreadable' "$tmp/out.txt" &&
    [ ! -s "$tmp/raw-content-fetches" ]
} && pass "an over-limit workflow is rejected before raw download" \
  || { fail "an over-limit workflow was downloaded or not reported unreadable ($rc)"; out | sed 's/^/diag - /'; }

stack node
workflow_for node
python3 - "$content_root/.github/workflows/ci.yml" "$content_root/.github/workflows" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_bytes()
root = Path(sys.argv[2])
for index in range(1, 4):
    (root / f"budget-{index}.yml").write_bytes(source + b"\n# " + b"x" * (4 * 1024 * 1024))
PY
printf '[{"type":"file","path":".github/workflows/budget-1.yml"},{"type":"file","path":".github/workflows/budget-2.yml"},{"type":"file","path":".github/workflows/budget-3.yml"}]\n' >"$WORKFLOW_LIST_FILE"
: >"$tmp/raw-content-fetches"
rc="$(CONTENT_ENCODING_NONE=true run_audit)"
{
  [ "$rc" = "rc=1" ] &&
    grep -q 'workflow-source-unreadable' "$tmp/out.txt" &&
    [ "$(wc -l <"$tmp/raw-content-fetches")" -eq 2 ] &&
 [ "$(grep -Fc ".github/workflows/budget-1.yml?ref=$contract_pin" "$tmp/content-api-requests")" -eq 2 ] &&
 [ "$(grep -Fc ".github/workflows/budget-2.yml?ref=$contract_pin" "$tmp/content-api-requests")" -eq 2 ] &&
    ! grep -q 'budget-3.yml' "$tmp/raw-content-fetches"
} && pass "per-repository workflow byte budget stops further downloads" \
  || { fail "aggregate workflow download budget was not enforced ($rc)"; out | sed 's/^/diag - /'; }

stack node
workflow_for node
cp "$content_root/.github/workflows/ci.yml" "$tmp/large-ci-original.yml"
python3 - "$content_root/.github/workflows/ci.yml" <<'PY'
import sys
from pathlib import Path

with Path(sys.argv[1]).open("ab") as workflow:
    workflow.write(b"\n# " + b"x" * 1_000_100 + b"\n")
PY
printf '[{"type":"file","path":".github/workflows/ci.yml"}]\n' >"$WORKFLOW_LIST_FILE"
: >"$tmp/content-api-requests"
: >"$tmp/raw-content-fetches"
rc="$(CONTENT_ENCODING_NONE=true run_audit)"
cp "$tmp/large-ci-original.yml" "$content_root/.github/workflows/ci.yml"
{
  [ "$rc" = "rc=1" ] &&
    grep -q 'result=changelog-caller-missing' "$tmp/out.txt" &&
    ! grep -q 'workflow-source-unreadable' "$tmp/out.txt" &&
    grep -Fxq '.github/workflows/ci.yml' "$tmp/raw-content-fetches" &&
    grep -q 'unaudited=0' "$tmp/out.txt"
} && pass "an oversized workflow uses the raw contents response" \
  || { fail "an oversized workflow was not audited from raw contents ($rc)"; out | sed 's/^/diag - /'; }

stack node
workflow_for node
: >"$content_root/.github/workflows/ci.yml"
rc="$(run_audit)"
{ [ "$rc" = "rc=1" ] &&
  grep -q 'result=stack-caller-missing' "$tmp/out.txt" &&
  ! grep -q 'workflow-source-unreadable' "$tmp/out.txt" &&
  grep -q 'phase=done' "$tmp/out.txt" &&
  grep -q 'unaudited=0' "$tmp/out.txt"; } &&
pass "a fetched zero-byte workflow is nonconformant and the audit completes" \
|| { fail "a fetched zero-byte workflow was treated as a fetch fault ($rc)"; out | sed 's/^/diag - /'; }

stack node
printf 'on: [unterminated\n' >"$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'workflow-source-unreadable' "$tmp/out.txt"; } \
  && pass "malformed workflow YAML fails closed" \
  || { fail "malformed caller source was accepted ($rc)"; out | sed 's/^/diag - /'; }

for duplicate_top_level in permissions jobs on true on-true true-on; do
  stack node
  case "$duplicate_top_level" in
    permissions)
      sed -i '/^jobs:$/i\permissions: {}' "$tmp/workflow.yml"
      ;;
    jobs)
      printf '\njobs: {}\n' >>"$tmp/workflow.yml"
      ;;
    on)
      sed -i '/^permissions:$/i\on: pull_request' "$tmp/workflow.yml"
      ;;
    true)
      sed -i 's/^on:$/true:/' "$tmp/workflow.yml"
      sed -i '/^permissions:$/i\true: pull_request' "$tmp/workflow.yml"
      ;;
    on-true)
      sed -i '/^permissions:$/i\true: pull_request' "$tmp/workflow.yml"
      ;;
    true-on)
      sed -i 's/^on:$/true:/' "$tmp/workflow.yml"
      sed -i '/^permissions:$/i\on: pull_request' "$tmp/workflow.yml"
      ;;
  esac
  encode_workflow
  rc="$(run_audit)"
  { [ "$rc" != "rc=0" ] && grep -q 'workflow-source-unreadable' "$tmp/out.txt"; } \
    && pass "duplicate top-level YAML keys fail closed: $duplicate_top_level" \
    || { fail "duplicate top-level YAML keys were accepted: $duplicate_top_level ($rc)"; out | sed 's/^/diag - /'; }
done

# --- a repository with no merged PRs is not conformant by default ------------
stack node; printf '[]\n' >"$PULLS_FILE"
rc="$(run_audit)"
{ grep -q 'no-merged-prs' "$tmp/out.txt" && ! grep -q 'result=conformant' "$tmp/out.txt"; } \
  && pass "a repository with no merged PRs is reported, not silently conformant" \
  || { fail "an unaudited repository was counted as conformant ($rc)"; out | sed 's/^/diag - /'; }

# --- source contract: callers must be unconditional and canonically named ---
stack node; pulls s1 s2
head_with s1 gate "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
head_with s2 gate "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
sed -i '/^  pull_request:$/a\    paths:\n      - "src/**"' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'workflow-path-filter' "$tmp/out.txt"; } \
  && pass "a workflow-level paths filter is nonconformant even when sampled checks exist" \
  || { fail "a paths-filtered stack caller was called safe ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i 's#^on:$#on: {pull_request: {paths: ["src/**"]}}#; /^  pull_request:$/d' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'workflow-path-filter' "$tmp/out.txt"; } \
  && pass "an inline workflow-level paths filter is also nonconformant" \
  || { fail "an inline paths filter bypassed source inspection ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i 's/^  ci:$/  build:/' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'caller-job-name expected=ci actual=build' "$tmp/out.txt"; } \
  && pass "a thin stack caller with the wrong job name is nonconformant" \
  || { fail "a noncanonical stack caller name was accepted ($rc)"; out | sed 's/^/diag - /'; }

# A repository may call the same reusable workflow again under another name —
# a Node floor matrix, a downstream compatibility lane. Those publish their own
# prefixes, satisfy no rule, and are outside this contract. Reading them as a
# naming violation reported five conformant repositories as nonconformant.
stack node
pulls s1 s2
head_with s1 "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
head_with s2 "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
sed -i '/^  changelog-contract:$/i\  ci-node-floor:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@0123456789abcdef0123456789abcdef01234567' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ] && grep -q 'result=conformant' "$tmp/out.txt" \
  && ! grep -q 'caller-job-name' "$tmp/out.txt"; } \
  && pass "an extra stack caller under another job name does not fail the canonical one" \
  || { fail "a second reusable-workflow caller was read as a naming violation ($rc)"; out | sed 's/^/diag - /'; }

# The name being right twice is still wrong: two check runs would publish one
# required context, and which of them satisfies the rule is undefined.
stack node
cat >"$content_root/.github/workflows/ci-extra.yml" <<'EXTRA'
name: extra
on:
  pull_request:
permissions:
  contents: read
jobs:
  ci:
    uses: Verjson/.github/.github/workflows/node-ci.yml@0123456789abcdef0123456789abcdef01234567
EXTRA
printf '[{"type":"file","path":".github/workflows/ci.yml"},{"type":"file","path":".github/workflows/ci-extra.yml"},{"type":"file","path":".github/workflows/changelog.yml"},{"type":"file","path":".github/workflows/release.yml"}]\n' >"$WORKFLOW_LIST_FILE"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'stack-caller-duplicate expected=1 actual=2' "$tmp/out.txt"; } \
  && pass "two jobs named ci publishing one required context is nonconformant" \
  || { fail "a duplicated canonical stack caller was accepted ($rc)"; out | sed 's/^/diag - /'; }
rm -f "$content_root/.github/workflows/ci-extra.yml"

stack node
sed -i 's/^  changelog:$/  generated-docs:/' "$content_root/.github/workflows/changelog.yml"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-invalid' "$tmp/out.txt"; } \
  && pass "the generated-artifacts caller must publish changelog / validate" \
  || { fail "a noncanonical changelog caller name was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack helm; pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
sed -i '/^  changelog:$/a\    name: renamed required check' "$content_root/.github/workflows/changelog.yml"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-invalid' "$tmp/out.txt"; } \
  && pass "a job-level name cannot disguise a changed changelog context" \
  || { fail "a named changelog caller was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack helm; pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
sed -i '/^  changelog:$/a\    strategy:\n      matrix:\n        shard: [one, two]' "$content_root/.github/workflows/changelog.yml"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-invalid' "$tmp/out.txt"; } \
  && pass "a matrix cannot suffix the generated changelog context" \
  || { fail "a matrixed changelog caller was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack helm; pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
sed -i 's/^      changelog: true$/      changelog: false/' "$content_root/.github/workflows/changelog.yml"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-invalid' "$tmp/out.txt"; } \
  && pass "the source audit requires changelog validation to be enabled" \
  || { fail "a changelog-disabled generated caller was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack helm; pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
sed -i 's/generated-artifacts\.yml@/GENERATED-ARTIFACTS.yml@/' "$content_root/.github/workflows/changelog.yml"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-missing' "$tmp/out.txt"; } \
  && pass "workflow path case cannot satisfy the generated changelog caller" \
  || { fail "a caller with the wrong workflow path case was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack helm; pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
sed -i 's|^    uses: verJSON/|    # uses: verJSON/|' "$content_root/.github/workflows/changelog.yml"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-missing' "$tmp/out.txt"; } \
  && pass "a commented uses lookalike cannot satisfy source inspection" \
  || { fail "a comment-only generated caller was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack helm; pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
cp "$content_root/.github/workflows/changelog.yml" "$content_root/.github/workflows/generated-artifacts.yml"
printf '[{"type":"file","path":".github/workflows/ci.yml"},{"type":"file","path":".github/workflows/changelog.yml"},{"type":"file","path":".github/workflows/generated-artifacts.yml"},{"type":"file","path":".github/workflows/release.yml"}]\n' >"$WORKFLOW_LIST_FILE"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-missing expected=1 actual=2' "$tmp/out.txt"; } \
  && pass "duplicate generated changelog callers fail as ambiguous" \
  || { fail "duplicate caller files were accepted ($rc)"; out | sed 's/^/diag - /'; }

stack helm; pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
cat >"$content_root/.github/workflows/alternate-indentation.yml" <<YAML
name: alternate indentation
on: pull_request
jobs:
    changelog:
        uses: Verjson/.github/.github/workflows/generated-artifacts.yml@$contract_pin
        with:
            changelog: true
            contract_ref: $contract_pin
YAML
printf '[{"type":"file","path":".github/workflows/alternate-indentation.yml"},{"type":"file","path":".github/workflows/ci.yml"},{"type":"file","path":".github/workflows/changelog.yml"},{"type":"file","path":".github/workflows/release.yml"}]\n' >"$WORKFLOW_LIST_FILE"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-missing expected=1 actual=2' "$tmp/out.txt"; } \
  && pass "alternate YAML indentation cannot hide a duplicate caller" \
  || { fail "an alternately indented duplicate caller was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack helm; pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
cp "$content_root/.github/workflows/changelog.yml" "$content_root/.github/workflows/docs-validation.yml"
printf '[{"type":"file","path":".github/workflows/ci.yml"},{"type":"file","path":".github/workflows/changelog.yml"},{"type":"file","path":".github/workflows/docs-validation.yml"},{"type":"file","path":".github/workflows/release.yml"}]\n' >"$WORKFLOW_LIST_FILE"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-missing expected=1 actual=2' "$tmp/out.txt"; } \
  && pass "a renamed duplicate generated caller fails as ambiguous" \
  || { fail "a renamed duplicate caller was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack helm; pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
cat >"$content_root/.github/workflows/legacy-validation.yml" <<YAML
name: legacy validation
on:
  pull_request:
jobs:
  legacy:
    uses: Verjson/.github/.github/workflows/changelog-validate.yml@$contract_pin
    with:
      contract_ref: $contract_pin
YAML
printf '[{"type":"file","path":".github/workflows/ci.yml"},{"type":"file","path":".github/workflows/changelog.yml"},{"type":"file","path":".github/workflows/legacy-validation.yml"},{"type":"file","path":".github/workflows/release.yml"}]\n' >"$WORKFLOW_LIST_FILE"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-invalid path=.github/workflows/legacy-validation.yml' "$tmp/out.txt"; } \
  && pass "a legacy changelog-validate caller cannot coexist with the canonical caller" \
  || { fail "an additional legacy caller was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack helm; pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
sed -i '/^      contract_ref:/a\      unexpected_input: true' "$content_root/.github/workflows/changelog.yml"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-invalid' "$tmp/out.txt"; } \
  && pass "the generated caller rejects additional nested inputs" \
  || { fail "an additional generated caller input was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack helm; pulls s1 s2
head_with s1 gate "ci / lint-template" "changelog / validate"
head_with s2 gate "ci / lint-template" "changelog / validate"
cat >>"$content_root/.github/workflows/changelog.yml" <<YAML
  duplicate-changelog:
    uses: Verjson/.github/.github/workflows/generated-artifacts.yml@$contract_pin
    with:
      changelog: true
      contract_ref: $contract_pin
YAML
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-caller-invalid' "$tmp/out.txt"; } \
  && pass "multiple generated changelog jobs in one workflow fail as ambiguous" \
  || { fail "ambiguous caller jobs were accepted ($rc)"; out | sed 's/^/diag - /'; }

stack node
unmerged_pin=ffffffffffffffffffffffffffffffffffffffff
for artifact in \
  "$content_root/.github/workflows/changelog.yml" \
  "$content_root/.github/workflows/release.yml" \
  "$content_root/scripts/render-next.sh" \
  "$content_root/scripts/changelog-contract.test.sh"; do
  sed -i "s/$contract_pin/$unmerged_pin/g" "$artifact"
done
: >"$GENERATOR_FETCHES"
export PIN_ANCESTRY_STATUS=diverged
rc="$(run_audit)"
unset PIN_ANCESTRY_STATUS
{ [ "$rc" != "rc=0" ] && grep -q 'generated-contract-pin-not-on-default' "$tmp/out.txt" && [ ! -s "$GENERATOR_FETCHES" ]; } \
  && pass "an unmerged consumer-selected generator pin is rejected before fetch or execution" \
  || { fail "an unmerged generator pin reached trusted execution ($rc)"; out | sed 's/^/diag - /'; }

stack node
printf '\n# handwritten drift\n' >>"$content_root/scripts/render-next.sh"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'generated-contract-byte-drift artifact=render-next.sh' "$tmp/out.txt"; } \
  && pass "a handwritten renderer lookalike cannot satisfy generated provenance" \
  || { fail "renderer byte drift was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack node
printf '#!/usr/bin/env bash\nexit 0\n' >"$content_root/scripts/changelog-contract.test.sh"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -qE 'generated-contract-(parameters-invalid|byte-drift)' "$tmp/out.txt"; } \
  && pass "a trivial replacement contract test cannot satisfy generated provenance" \
  || { fail "a trivial generated-test escape was accepted ($rc)"; out | sed 's/^/diag - /'; }

# --- #1212: changelog-contract.yml (the pr-gate artifact) is byte-compared ---
# The shape checker only asks whether SOME unconditional job runs the contract
# test; a hand-written job satisfying that shape used to pass even though the
# canonical generated file is what carries the runner pin and the
# persist-credentials:false hardening from #959.
stack node
printf '\n# handwritten drift\n' >>"$content_root/.github/workflows/changelog-contract.yml"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'generated-contract-byte-drift artifact=changelog-contract.yml' "$tmp/out.txt"; } \
  && pass "a handwritten changelog-contract.yml lookalike cannot satisfy generated provenance" \
  || { fail "changelog-contract.yml byte drift was accepted ($rc)"; out | sed 's/^/diag - /'; }

# `--untrusted-runner` is the one argument that changes pr-gate's output, and
# it is not recorded anywhere else the audit reads — it must be recovered from
# the artifact's own `runs-on:` line. An unrecognised shape there must fault,
# not fall back to a best-effort parse that would let a hand-edited `runs-on:`
# regenerate to match itself.
stack node
sed -i 's/^\( *\)runs-on: .*/\1runs-on: something-unparseable/' "$content_root/.github/workflows/changelog-contract.yml"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'generated-contract-runner-shape-invalid' "$tmp/out.txt"; } \
  && pass "an unrecognised pr-gate runs-on shape faults rather than guessing" \
  || { fail "a malformed pr-gate runs-on shape was accepted ($rc)"; out | sed 's/^/diag - /'; }

# The forward case: a repository that legitimately opted into
# `--untrusted-runner` still regenerates byte-identical and is conformant.
stack node
run_generator pr-gate "$contract_pin" --untrusted-runner self-hosted,general \
  >"$content_root/.github/workflows/changelog-contract.yml"
pulls s1 s2
head_with s1 "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
head_with s2 "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ] && grep -q 'result=conformant' "$tmp/out.txt"; } \
  && pass "a legitimate --untrusted-runner pr-gate artifact regenerates byte-identical" \
  || { fail "a legitimate --untrusted-runner pr-gate artifact was rejected ($rc)"; out | sed 's/^/diag - /'; }

# A repository that hasn't adopted `pr-gate` mode at all has no
# changelog-contract.yml file — the fetch itself must fault rather than treat
# a 404 as anything else.
stack node
rm -f "$content_root/.github/workflows/changelog-contract.yml"
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'generated-contract-artifact-unreadable path=.github/workflows/changelog-contract.yml' "$tmp/out.txt"; } \
  && pass "a missing changelog-contract.yml faults rather than skipping byte identity" \
  || { fail "a missing changelog-contract.yml was not detected ($rc)"; out | sed 's/^/diag - /'; }

# A truncated artifact is not a readable one. `base64 --decode` exits 0 on an
# empty stream, so an artifact that arrives as zero bytes used to pass the
# unreadable guard and surface three checks later as invalid PARAMETERS -- a
# diagnosis pointing at the adopter's scope/node/package-dirs when nothing had
# been fetched at all. Absence of an error signal is not evidence the artifact
# is there.
# Only the artifacts this fetch loop is the first observer of. An empty
# changelog.yml is caught earlier, by the workflow enumeration, and reddens
# there with its own accurate reason.
for empty_artifact in scripts/changelog-contract.test.sh scripts/render-next.sh; do
  stack node
  : >"$content_root/$empty_artifact"
  rc="$(run_audit)"
  { [ "$rc" != "rc=0" ] && grep -q "generated-contract-artifact-empty path=$empty_artifact" "$tmp/out.txt"; } \
    && pass "a zero-byte $empty_artifact faults as empty, not as invalid parameters" \
    || { fail "a zero-byte $empty_artifact was read as a fetched artifact ($rc)"; out | sed 's/^/diag - /'; }
done
stack node

# Bracket-shaped-but-invalid inner content — the one bash regex a future edit
# is most likely to loosen by accident — must also fault closed rather than
# best-effort-parse into a false match.
for bad_runner in '[SELF-HOSTED]' '[self-hosted,general]' '[self-hosted, general, ]'; do
  stack node
  sed -i "s/^\( *\)runs-on: .*/\1runs-on: ${bad_runner//\//\\/}/" "$content_root/.github/workflows/changelog-contract.yml"
  rc="$(run_audit)"
  { [ "$rc" != "rc=0" ] && grep -q 'generated-contract-runner-shape-invalid' "$tmp/out.txt"; } \
    && pass "a bracket-shaped but invalid pr-gate runs-on faults: $bad_runner" \
    || { fail "a bracket-shaped but invalid pr-gate runs-on was accepted: $bad_runner ($rc)"; out | sed 's/^/diag - /'; }
done

stack node
pulls s1 s2
head_with s1 gate "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
head_with s2 gate "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
sed -i \
  -e 's#echo "VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools" >> "$GITHUB_ENV"#echo   "VERJSON_CHANGELOG_TOOL_CACHE=${RUNNER_TEMP}/verjson-changelog-tools"  >>  "${GITHUB_ENV}"#' \
  -e 's#bash scripts/changelog-contract.test.sh#bash "scripts/changelog-contract.test.sh"#' \
  "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ] && grep -q 'result=conformant' "$tmp/out.txt"; } \
  && pass "semantically equivalent command quoting and spacing remain conformant" \
  || { fail "equivalent shell formatting was rejected ($rc)"; out | sed 's/^/diag - /'; }

for harmless_format in trailing-comment block-scalar; do
  stack node
  case "$harmless_format" in
    trailing-comment)
      sed -i 's|bash scripts/changelog-contract.test.sh|bash scripts/changelog-contract.test.sh # generated contract entrypoint|' "$tmp/workflow.yml"
      ;;
    block-scalar)
      sed -i '/^      - run: bash scripts\/changelog-contract\.test\.sh$/c\      - run: |\
          bash scripts/changelog-contract.test.sh' "$tmp/workflow.yml"
      sed -i '/^      - run: |$/i\      - name: Run changelog contract' "$tmp/workflow.yml"
      sed -i '/^      - run: |$/d' "$tmp/workflow.yml"
      sed -i '/^          bash scripts\/changelog-contract\.test\.sh$/i\        run: |' "$tmp/workflow.yml"
      ;;
  esac
  encode_workflow
  rc="$(run_audit)"
  { [ "$rc" = "rc=0" ] && grep -q 'result=conformant' "$tmp/out.txt"; } \
    && pass "harmless command formatting remains conformant: $harmless_format" \
    || { fail "harmless command formatting was rejected: $harmless_format ($rc)"; out | sed 's/^/diag - /'; }
done

for mutation in \
  's/${RUNNER_TEMP}/${HOME}/' \
  's/ >> / > /' \
  's/echo   /echo -n /' \
  's#bash "scripts/changelog-contract.test.sh"#bash "scripts/changelog-contract.test.sh"; echo bypass#'; do
  stack node
  sed -i \
    -e 's#echo "VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools" >> "$GITHUB_ENV"#echo   "VERJSON_CHANGELOG_TOOL_CACHE=${RUNNER_TEMP}/verjson-changelog-tools"  >>  "${GITHUB_ENV}"#' \
    -e 's#bash scripts/changelog-contract.test.sh#bash "scripts/changelog-contract.test.sh"#' \
    -e "$mutation" \
    "$tmp/workflow.yml"
  encode_workflow
  rc="$(run_audit)"
  { [ "$rc" != "rc=0" ] && grep -q 'changelog-contract-job-invalid' "$tmp/out.txt"; } \
    && pass "semantic command mutation fails workflow inspection closed: $mutation" \
    || { fail "semantic command mutation was accepted: $mutation ($rc)"; out | sed 's/^/diag - /'; }
done

for command_boundary in cache-argument cache-redirection contract-argument; do
  stack node
  case "$command_boundary" in
    cache-argument)
      sed -i 's#      - run: echo "VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools" >> "$GITHUB_ENV"#      - name: prepare cache\n        run: |\n          echo\n          "VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools" >> "$GITHUB_ENV"#' "$tmp/workflow.yml"
      ;;
    cache-redirection)
      sed -i 's#      - run: echo "VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools" >> "$GITHUB_ENV"#      - name: prepare cache\n        run: |\n          echo "VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools"\n          >> "$GITHUB_ENV"#' "$tmp/workflow.yml"
      ;;
    contract-argument)
      sed -i 's#      - run: bash scripts/changelog-contract.test.sh#      - name: run contract test\n        run: |\n          bash\n          scripts/changelog-contract.test.sh#' "$tmp/workflow.yml"
      ;;
  esac
  encode_workflow
  rc="$(run_audit)"
  { [ "$rc" != "rc=0" ] && grep -q 'changelog-contract-job-invalid' "$tmp/out.txt"; } \
    && pass "a literal newline cannot become harmless command spacing: $command_boundary" \
    || { fail "a command-boundary newline was accepted: $command_boundary ($rc)"; out | sed 's/^/diag - /'; }
done

for permission_shape in write unexpected duplicate malformed least-privilege; do
  stack node
  case "$permission_shape" in
    write)
      sed -i '/^    runs-on:/i\    permissions:\n      contents: write\n      pull-requests: write' "$tmp/workflow.yml"
      ;;
    unexpected)
      sed -i '/^    runs-on:/i\    permissions:\n      issues: read' "$tmp/workflow.yml"
      ;;
    duplicate)
      sed -i '/^    runs-on:/i\    permissions: {}\n    permissions: {}' "$tmp/workflow.yml"
      ;;
    malformed)
      sed -i '/^    runs-on:/i\    permissions: contents' "$tmp/workflow.yml"
      ;;
    least-privilege)
      sed -i '/^    runs-on:/i\    permissions:\n      contents: read' "$tmp/workflow.yml"
      ;;
  esac
  encode_workflow
  rc="$(run_audit)"
  { [ "$rc" != "rc=0" ] && ! grep -q 'result=conformant' "$tmp/out.txt"; } \
    && pass "job-level permissions outside the generated shape fail closed: $permission_shape" \
    || { fail "job-level permissions were accepted: $permission_shape ($rc)"; out | sed 's/^/diag - /'; }
done

for workflow_permission_shape in absent write unexpected empty scalar duplicate; do
  stack node
  case "$workflow_permission_shape" in
    absent)
      sed -i '/^permissions:$/,+1d' "$tmp/workflow.yml"
      ;;
    write)
      sed -i 's/^  contents: read$/  contents: write/' "$tmp/workflow.yml"
      ;;
    unexpected)
      sed -i '/^  contents: read$/a\  issues: read' "$tmp/workflow.yml"
      ;;
    empty)
      sed -i '/^permissions:$/,+1c\permissions: {}' "$tmp/workflow.yml"
      ;;
    scalar)
      sed -i '/^permissions:$/,+1c\permissions: read-all' "$tmp/workflow.yml"
      ;;
    duplicate)
      sed -i '/^  contents: read$/a\  contents: read' "$tmp/workflow.yml"
      ;;
  esac
  encode_workflow
  rc="$(run_audit)"
  { [ "$rc" != "rc=0" ] && grep -q 'changelog-contract-job-invalid' "$tmp/out.txt"; } \
    && pass "workflow-level permissions cannot alter the generated job boundary: $workflow_permission_shape" \
    || { fail "workflow-level permissions were accepted: $workflow_permission_shape ($rc)"; out | sed 's/^/diag - /'; }
done

stack node
pulls s1 s2
head_with s1 gate "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
head_with s2 gate "ci / build-test" "ci / eligibility" changelog-contract "changelog / validate"
sed -i 's/^    runs-on: ubuntu-24.04$/    runs-on:\n      - self-hosted\n      - linux/' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ] && grep -q 'result=conformant' "$tmp/out.txt"; } \
  && pass "a block-sequence runner label remains conformant" \
  || { fail "a valid block-sequence runs-on was rejected ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i 's/^  changelog-contract:$/  contract-conformance:/' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-contract-job-count expected=1 actual=0' "$tmp/out.txt"; } \
  && pass "the current workflow source must publish the literal changelog-contract job" \
  || { fail "a renamed changelog-contract job was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i '/^  changelog-contract:$/a\    if: false' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-contract-job-invalid' "$tmp/out.txt"; } \
  && pass "a conditional changelog-contract job cannot satisfy the source contract" \
  || { fail "a conditional changelog-contract job was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i '/^  changelog-contract:$/a\    name: harmless-looking-name' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-contract-job-invalid' "$tmp/out.txt"; } \
  && pass "a display name cannot replace the literal required context" \
  || { fail "a renamed check context was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i '/^  changelog-contract:$/a\    strategy:\n      matrix:\n        shard: [1, 2]' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-contract-job-invalid' "$tmp/out.txt"; } \
  && pass "a matrix cannot suffix the literal required context" \
  || { fail "a matrix check-name escape was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i '/^  changelog-contract:$/a\    continue-on-error: true' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-contract-job-invalid' "$tmp/out.txt"; } \
  && pass "continue-on-error cannot make contract failures advisory" \
  || { fail "continue-on-error was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i '/^          persist-credentials: false$/a\          ref: main' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-contract-job-invalid' "$tmp/out.txt"; } \
  && pass "the contract job cannot test a substituted checkout ref" \
  || { fail "a default-branch checkout escape was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i '/^          persist-credentials: false$/a\          repository: attacker/lookalike' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-contract-job-invalid' "$tmp/out.txt"; } \
  && pass "the contract job cannot substitute another repository" \
  || { fail "a repository checkout escape was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i 's#bash scripts/changelog-contract.test.sh#bash scripts/other.test.sh#' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'changelog-contract-job-invalid' "$tmp/out.txt"; } \
  && pass "the required job must execute the generated changelog contract test" \
  || { fail "a changelog-contract job that runs another command was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i 's/^  pull_request:$/  workflow_dispatch:/' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'workflow-trigger-missing' "$tmp/out.txt"; } \
  && pass "the required job must run for pull requests" \
  || { fail "a non-PR changelog-contract job was accepted ($rc)"; out | sed 's/^/diag - /'; }

stack node
sed -i '/^  pull_request:$/a\    types: [opened]' "$tmp/workflow.yml"
encode_workflow
rc="$(run_audit)"
{ [ "$rc" != "rc=0" ] && grep -q 'workflow-trigger-missing' "$tmp/out.txt"; } \
  && pass "pull-request type filters cannot omit synchronize events" \
  || { fail "a type-filtered pull_request trigger was accepted ($rc)"; out | sed 's/^/diag - /'; }

# --- repository enumeration is paginated, filters archives, and fails closed -
stack actions; pulls s1 s2
head_with s1 gate shell-tests
head_with s2 gate shell-tests
printf '[{"name":"alpha","archived":false},{"name":"retired","archived":true}]\n' >"$REPOS_FILE"
unset RCA_REPOS
rc="$(run_audit)"
{ [ "$rc" = "rc=0" ] && ! grep -q 'repo=retired' "$tmp/out.txt"; } \
  && pass "paginated repository discovery excludes archived repositories" \
  || { fail "archived repository was audited or discovery failed ($rc)"; out | sed 's/^/diag - /'; }

rc="$(REPOS_FAIL=true run_audit)"
{ [ "$rc" = "rc=2" ] && grep -q 'phase=repository-list result=unreadable' "$tmp/out.txt"; } \
  && pass "repository pagination/rate failure is terminal" \
  || { fail "repository pagination failure produced a partial green audit ($rc)"; out | sed 's/^/diag - /'; }
export RCA_REPOS=alpha
printf '[{"name":"alpha","archived":false}]\n' >"$REPOS_FILE"

echo
if [ "$fails" -eq 0 ]; then echo "All tests passed."; else echo "$fails test(s) failed."; exit 1; fi
