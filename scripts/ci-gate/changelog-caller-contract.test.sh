#!/usr/bin/env bash
# Contract tests for scripts/gen-changelog-caller.sh (#286).
#
# The generated pair fails silently when wrong — a renderer and a workflow
# pinned to different commits both keep working while local output stops
# predicting CI — so the agreement between them is asserted here rather than
# left to reviewers.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
# shellcheck source=scripts/changelog-caller-contract-shard.sh
source "$repo_root/scripts/changelog-caller-contract-shard.sh"
if ! changelog_caller_shard_name_ok "${CHANGELOG_CALLER_CONTRACT_SHARD:-all}"; then
  printf 'FAIL - CHANGELOG_CALLER_CONTRACT_SHARD must be all or 1..233\n' >&2
  exit 1
fi
case "${CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY:-0}" in
  0|1) ;;
  *) printf 'FAIL - CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY must be 0 or 1\n' >&2; exit 1 ;;
esac
caller_case_count=0
caller_case_ran=0
caller_case_reserved_count=0
caller_adopter_cases_seen=0
caller_assertions_suspended=0
gen="$repo_root/scripts/gen-changelog-caller.sh"
# A ref that actually resolves. The generator pins the SHA-256 of the engine at
# the contract commit (#304), so it must be able to read that commit's content —
# a fictional SHA now fails generation by design. The assertions below are about
# interpolation fidelity ("the ref passed is the ref emitted"), which a real SHA
# exercises identically. `unresolvable_sha` keeps the old value for the case that
# asserts the new fail-closed behaviour.
sha="$(git -C "$repo_root" rev-parse HEAD)"
unresolvable_sha="0123456789abcdef0123456789abcdef01234567"
fails=0

pass() {
  [ "${caller_assertions_suspended:-}" = 1 ] && return 0
  printf 'ok   - %s\n' "$1"
}
fail() {
  [ "${caller_assertions_suspended:-}" = 1 ] && return 0
  printf 'FAIL - %s\n' "$1"
  fails=$((fails + 1))
}
# A case whose FIXTURE cannot be built in this environment. It is not a pass:
# reporting "ok" for an assertion that never ran is the defect class this suite
# exists to close, and a silent omission is worse. Use it only where the reason
# is a property of the host, and say which property.
skip() { printf 'skip - %s\n' "$1"; }

[ -x "$gen" ] || { echo "FAIL - $gen is not executable"; exit 1; }
[ ! -e "$repo_root/.github/workflows/tag-major.yml" ] \
  && pass "release publication has no moving-major tag workflow (#1644)" \
  || fail "release publication still exposes the moving-major tag workflow"

# `var="$(bash "$gen" mode "$sha")"` discards the generator's status, so a mode
# that refuses leaves an empty capture and the assertions downstream report that
# emptiness as a defect in what they test: a refusing `adr-index-test` once read
# as "adr-index-test did not rewrite the repository root for the adopter layout"
# (#1427). Take the status here, where the mode's own stderr still names the
# cause, and keep the emptiness and its explanation in one verdict. Status is
# taken on the failure branch itself — `if ! bash ...` would invert the very
# status being read and turn a refusal into a silent empty capture again.
workflow='' generated_artifacts='' generated_artifacts_with_adr=''
canonical_codeowners=''
adr_index_generator='' pr_gate='' release_node_workflow=''
renovate_attribution='' default_release='' custom_release='' generated_contract=''
component_release='' custom_contract='' adr_index_test=''
mode_capture_failures=0
capture_mode() {
  local target="$1"
  shift
  local mode="$1" out err status=0 cache_dir cache_file cache_tmp cache_key failure_reason=''
  local cache_lock_fd cache_locked=0
  # `printf -v` writes into the nearest scope holding the name, so a target naming one of
  # these locals would assign here and leave the caller reading an empty global -- the
  # #1427 misdiagnosis again, arriving through this function rather than through the
  # generator. No current call site collides; this is what keeps that true.
  case "$target" in
    target | mode | out | err | status)
      fail "capture_mode target '$target' shadows one of its own locals"
      mode_capture_failures=$((mode_capture_failures + 1))
      return ;;
  esac
  # An unchecked mktemp redirects the mode's stderr to the empty filename, and bash's own
  # complaint about that is then reported as the MODE having refused. Same misattribution.
  err="$(mktemp)" || {
    fail "capture_mode could not allocate a scratch file for mode '$mode'"
    mode_capture_failures=$((mode_capture_failures + 1))
    return
  }
  if [ -n "${CHANGELOG_CALLER_CONTRACT_CACHE:-}" ]; then
    if ! command -v flock >/dev/null 2>&1; then
      fail "capture_mode requires flock when a shared output cache is enabled"
      mode_capture_failures=$((mode_capture_failures + 1))
      rm -f "$err"
      return
    fi
    cache_key="$(printf '%s\0' "$@" | sha256sum | cut -d' ' -f1)" || {
      status=$?
      failure_reason="could not calculate the cached output key"
    }
    cache_dir="$CHANGELOG_CALLER_CONTRACT_CACHE/$sha"
    if [ "$status" -eq 0 ]; then
      mkdir -p "$cache_dir" || {
        status=$?
        failure_reason="could not create the cached output directory"
      }
    fi
    cache_file="$cache_dir/$cache_key"
    if [ "$status" -eq 0 ]; then
      exec {cache_lock_fd}>"$cache_file.lock" || {
        status=$?
        failure_reason="could not open the cached output lock"
      }
      if [ "$status" -eq 0 ]; then
        cache_locked=1
        flock "$cache_lock_fd" || {
          status=$?
          failure_reason="could not lock the cached output"
        }
      fi
    fi
    if [ "$status" -eq 0 ] && [ -f "$cache_file" ]; then
      out="$(cat -- "$cache_file")" || {
        status=$?
        failure_reason="could not read the cached output"
      }
    elif [ "$status" -eq 0 ]; then
      out="$(bash "$gen" "$@" 2>"$err")" || status=$?
      if [ "$status" -eq 0 ]; then
        cache_tmp="$(mktemp "$cache_dir/.${cache_key}.XXXXXX")" || {
          status=$?
          failure_reason="could not create a temporary cached output file"
        }
        if [ "$status" -eq 0 ]; then
          if printf '%s' "$out" >"$cache_tmp" \
            && mv -f -- "$cache_tmp" "$cache_file"; then
            :
          else
            status=$?
            failure_reason="could not store the cached output"
            rm -f "$cache_tmp"
          fi
        fi
      fi
    fi
    if [ "$cache_locked" -eq 1 ]; then
      exec {cache_lock_fd}>&-
    fi
  else
    out="$(bash "$gen" "$@" 2>"$err")" || status=$?
  fi
  if [ "$status" -ne 0 ]; then
    # Bounded: a verbose mode's stderr is unbounded, and one verdict line that scrolls the
    # named cause off the top defeats the point of naming it.
    if [ -n "$failure_reason" ]; then
      fail "capture_mode $failure_reason for generator mode '$mode' (exit $status)"
    else
      fail "generator mode '$mode' exited $status: $(tr '\n' ' ' <"$err" | sed 's/  */ /g; s/ *$//' | cut -c1-500)"
    fi
    mode_capture_failures=$((mode_capture_failures + 1))
  fi
  rm -f "$err"
  printf -v "$target" '%s' "$out"
}

tmproot="$(mktemp -d)"
trap 'rm -rf "$tmproot"' EXIT
: >"$tmproot/run.out"
if [ "${CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY:-0}" = 1 ] \
  || [ "${CHANGELOG_CALLER_CONTRACT_SHARD:-all}" = all ]; then
capture_mode workflow workflow "$sha"
capture_mode renderer renderer "$sha"
capture_mode default_release release-node "$sha"
capture_mode custom_release release-node "$sha" --scope @acme --node-version 22.23.1 --package-dir compat --release-asset contract/schema.graphql --release-asset contract/schema.sha256
capture_mode component_release release-node "$sha" --only-package-dir packages/cli-schema --default-prefix schema-v --default-component cli-schema
capture_mode custom_contract contract-test "$sha" --scope @acme --node-version 22.23.1 --package-dir compat --release-asset contract/schema.graphql --release-asset contract/schema.sha256
capture_mode generated_artifacts generated-artifacts "$sha"
capture_mode generated_artifacts_with_adr generated-artifacts-with-adr-index "$sha"
capture_mode renovate_attribution renovate-attribution "$sha"
capture_mode adr_index_generator adr-index-generator "$sha"
capture_mode adr_index_test adr-index-test "$sha"
capture_mode pr_gate pr-gate "$sha"
capture_mode canonical_codeowners codeowners "$sha"
# Every assertion from here on consumes one of those captures, so a mode that
# refused would otherwise produce a page of unrelated verdicts about empty
# strings. One named cause is the whole diagnosis; stop rather than bury it.
[ "$mode_capture_failures" -eq 0 ] || exit 1
grep -qF 'uses: verJSON/.github/.github/workflows/generated-artifacts.yml@' <<<"$workflow" \
  && grep -qF 'raw.githubusercontent.com/verJSON/.github/' <<<"$renderer" \
  && grep -qF '* @verJSON/devs' <<<"$canonical_codeowners" \
  && grep -qF "github.repository_owner == 'verJSON'" <<<"$default_release" \
  && pass "generated artifacts use the canonical organization spelling" \
  || fail "generated artifacts retain an outdated organization spelling"
# The usage text is the one deliberate nonzero: the generator refuses a missing
# mode and prints usage on stderr, which is what this capture is for.
usage_text="$(bash "$gen" 2>&1 || :)"

# 1. The workflow pins its `uses:` and its contract_ref to the same commit.
uses_ref="$(printf '%s\n' "$workflow" | sed -n 's#.*generated-artifacts\.yml@\([0-9a-f]\{40\}\).*#\1#p')"
input_ref="$(printf '%s\n' "$workflow" | sed -n 's/^ *contract_ref: \([0-9a-f]\{40\}\) *$/\1/p')"
[ "$uses_ref" = "$sha" ] && pass "workflow pins uses: to the requested commit" \
  || fail "workflow uses: is '$uses_ref', expected $sha"
[ "$input_ref" = "$sha" ] && pass "workflow passes contract_ref as the same commit" \
  || fail "workflow contract_ref is '$input_ref', expected $sha"
grep -qE '^  changelog:$' <<<"$workflow" \
  && pass "workflow compatibility mode publishes the canonical required-check prefix" \
  || fail "workflow compatibility mode does not publish changelog / validate"
grep -qE '^ +changelog: true$' <<<"$workflow" \
  && pass "workflow compatibility mode enables changelog validation" \
  || fail "workflow compatibility mode does not enable changelog validation"

# 2. The renderer pins the same commit the workflow validates with.
script_ref="$(printf '%s\n' "$renderer" | sed -n 's/^CONTRACT_REF="\([0-9a-f]\{40\}\)"$/\1/p')"
[ "$script_ref" = "$uses_ref" ] \
  && pass "renderer and workflow share one contract commit" \
  || fail "renderer pins '$script_ref' but workflow pins '$uses_ref'"

# 3. The renderer is valid bash and renders nothing on its own.
printf '%s\n' "$renderer" | bash -n \
  && pass "generated renderer parses as bash" || fail "generated renderer is not valid bash"

# 4. Least privilege: the workflow requests no write scope.
grep -q 'contents: read' <<<"$workflow" \
  && pass "workflow declares contents: read" || fail "workflow does not declare contents: read"
grep -qE '\bwrite\b' <<<"$workflow" \
  && fail "workflow requests a write permission" || pass "workflow requests no write permission"

# 4a. The shared generated-artifacts caller is generated at the same immutable
# pin. Changelog-only stays the safe default; ADR checking is a separate mode
# because opting in before acquiring the generator is a counted failure.
grep -q "generated-artifacts.yml@$sha" <<<"$generated_artifacts" \
  && pass "generated-artifacts caller pins the requested workflow commit" \
  || fail "generated-artifacts caller does not pin $sha"
grep -qE '^  changelog:$' <<<"$generated_artifacts" \
  && pass "generated-artifacts caller publishes the canonical required-check prefix" \
  || fail "generated-artifacts caller does not publish changelog / validate"
grep -qE '^ +changelog: true$' <<<"$generated_artifacts" \
  && grep -qE "^ +contract_ref: $sha$" <<<"$generated_artifacts" \
  && pass "generated-artifacts caller enables changelog validation at the pin" \
  || fail "generated-artifacts caller does not enable pinned changelog validation"
grep -qE '^ +adr-index: true$' <<<"$generated_artifacts" \
  && fail "changelog-only generated-artifacts caller enables ADR checking without its generator" \
  || pass "changelog-only generated-artifacts caller does not opt into ADR checking"
grep -qE '^ +adr-index: true$' <<<"$generated_artifacts_with_adr" \
  && grep -q "generated-artifacts.yml@$sha" <<<"$generated_artifacts_with_adr" \
  && pass "ADR-index caller explicitly enables ADR checking at the pin" \
  || fail "ADR-index caller does not enable ADR checking"
printf '%s\n' "$adr_index_generator" | sed '2d' | cmp -s - "$repo_root/scripts/gen-adr-index.sh" \
  && grep -qF "Generated by verJSON/.github scripts/gen-changelog-caller.sh adr-index-generator $sha" <<<"$adr_index_generator" \
  && pass "adr-index-generator emits the canonical pinned generator" \
  || fail "adr-index-generator does not emit the pinned gen-adr-index.sh"
grep -qF 'Generated by verJSON/.github scripts/gen-changelog-caller.sh — do not edit by hand.' <<<"$adr_index_generator" \
  && grep -qF 'adr-index-generator <contract-sha> > scripts/gen-adr-index.sh' <<<"$adr_index_generator" \
  && pass "the pinned ADR artifact identifies its generated contract and regeneration command" \
  || fail "the ADR artifact lacks its generated-file warning or regeneration command"

printf '%s\n' "$adr_index_generator" | bash -n \
  && pass "generated ADR index generator parses as bash" \
  || fail "generated ADR index generator is not valid bash"
grep -qF 'required check: changelog / validate' <<<"$usage_text" \
  && pass "usage names the check required by the active organization ruleset" \
  || fail "usage does not name the active changelog / validate requirement"

grep -qE '^  changelog-contract:$' <<<"$pr_gate" \
  && grep -qF 'VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools' <<<"$pr_gate" \
  && ! grep -qF '/opt/verjson/changelog-tools' <<<"$pr_gate" \
  && pass "generated PR gate prepares a job-writable changelog cache (#822)" \
  || fail "generated PR gate retains runner-global changelog cache state"
cache_line="$(grep -nF 'VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools' <<<"$pr_gate" | cut -d: -f1)"
test_line="$(grep -nF 'bash scripts/changelog-contract.test.sh' <<<"$pr_gate" | cut -d: -f1)"
[ -n "$cache_line" ] && [ -n "$test_line" ] && [ "$cache_line" -lt "$test_line" ] \
  && pass "generated PR gate prepares its cache before contract validation" \
  || fail "generated PR gate prepares its cache after contract validation"

# #959. `actions/checkout` persists the job's GITHUB_TOKEN into `.git/config` by
# default, and every generated caller then runs repository code out of that same
# workspace — PR-authored code, in the pr-gate case. A release state lookup may
# use a step-scoped token for its remote read, but no checkout persists credentials.
# Audited across every workflow-emitting mode rather than only the reported one.
audit_checkout_credentials() {
  MODE="$1" WORKFLOW="$2" python3 - <<'PY'
import os

import yaml

mode = os.environ["MODE"]
# (mode, checkout step name) -> the later step in the same job that may use
# read-only remote Git access through process-scoped config. The checkout itself
# must still set persist-credentials: false. #971: checked structurally below,
# not just matched by name, so renaming or deleting the consumer re-flags it.
remote_reads = {
    (mode, checkout_name): {
        "consumer": "Resolve restart-safe release state",
        "requires": ("git_with_release_token ls-remote", "git_with_release_token fetch"),
    }
    for mode, checkout_name in (
        ("release-node", "Check out the tree that will be released"),
        ("release-artifact", "Check out the tree that will be released"),
        ("release-snapshot", "Check out the tree that will be released"),
    )
}
workflow = yaml.safe_load(os.environ["WORKFLOW"])
checkouts = 0
violations = []
for job_name, job in (workflow.get("jobs") or {}).items():
    steps = job.get("steps") or []
    steps_by_name = {step.get("name"): (i, step) for i, step in enumerate(steps)}
    for index, step in enumerate(steps):
        if not str(step.get("uses") or "").startswith("actions/checkout@"):
            continue
        checkouts += 1
        name = step.get("name")
        remote_read = remote_reads.get((mode, name))
        if remote_read is not None:
            consumer_index, consumer = steps_by_name.get(
                remote_read["consumer"], (None, None)
            )
            consumer_run = str((consumer or {}).get("run") or "")
            consumer_env = (consumer or {}).get("env") or {}
            if (
                consumer is not None
                and consumer_index is not None
                and consumer_index > index
                and all(needle in consumer_run for needle in remote_read["requires"])
                and consumer_env.get("GITHUB_TOKEN") == "${{ github.token }}"
                and "GIT_CONFIG_COUNT=1" in consumer_run
                and "GIT_CONFIG_VALUE_0" in consumer_run
            ):
                pass
            else:
                violations.append(
                    f"{name} (exemption stale: consumer {remote_read['consumer']!r} "
                    "missing, not after this checkout, lacks process-scoped auth, "
                    "or no longer performs the remote operation)"
                )
        if ((step.get("with") or {}).get("persist-credentials")) is not False:
            violations.append(name or f"{job_name}[{index}] (unnamed)")
print(f"{checkouts} checkout(s)", end="")
if violations:
    print("; persists credentials: " + ", ".join(violations))
    raise SystemExit(1)
print()
PY
}
for audited_mode in pr-gate release-node release-artifact release-snapshot release-propose workflow \
  generated-artifacts generated-artifacts-with-adr-index renovate-attribution; do
  audit_args=("$audited_mode" "$sha")
  [ "$audited_mode" != release-artifact ] || audit_args+=(--build-runner ubuntu-24.04)
  [ "$audited_mode" != release-propose ] || audit_args+=(--autonomy propose)
  audit_report="$(audit_checkout_credentials "$audited_mode" \
    "$(bash "$gen" "${audit_args[@]}")" 2>&1)" \
    && pass "generated $audited_mode keeps no job credential in the checked-out tree, #959 ($audit_report)" \
    || fail "generated $audited_mode persists the job credential into checked-out code (#959): $audit_report"
done

# #971: the release-node exemption above is only as good as the consuming step
# it names. Tamper with a genuine release-node workflow two ways and confirm
# the audit re-flags the checkout instead of trusting the step-name match alone.
capture_mode release_node_workflow release-node "$sha"
[ "$mode_capture_failures" -eq 0 ] || exit 1
tampered_renamed="$(WORKFLOW="$release_node_workflow" python3 - <<'PY'
import os

import yaml

workflow = yaml.safe_load(os.environ["WORKFLOW"])
for job in (workflow.get("jobs") or {}).values():
    for step in job.get("steps") or []:
        if step.get("name") == "Resolve restart-safe release state":
            step["name"] = "Renamed"
print(yaml.safe_dump(workflow, sort_keys=False))
PY
)"
audit_report="$(audit_checkout_credentials release-node "$tampered_renamed" 2>&1)" \
  && fail "audit did not notice the consuming step was renamed away (#971)" \
  || { grep -q "exemption stale" <<<"$audit_report" \
    && pass "renaming the consuming step re-flags the checkout (#971)" \
    || fail "audit failed for the wrong reason: $audit_report"; }

tampered_defanged="$(WORKFLOW="$release_node_workflow" python3 - <<'PY'
import os

import yaml

workflow = yaml.safe_load(os.environ["WORKFLOW"])
for job in (workflow.get("jobs") or {}).values():
    for step in job.get("steps") or []:
        if step.get("name") == "Resolve restart-safe release state":
            step["run"] = "echo no longer touches origin"
print(yaml.safe_dump(workflow, sort_keys=False))
PY
)"
audit_report="$(audit_checkout_credentials release-node "$tampered_defanged" 2>&1)" \
  && fail "audit did not notice the consuming step stopped using the credential (#971)" \
  || { grep -q "exemption stale" <<<"$audit_report" \
    && pass "a consuming step that drops the remote git operation re-flags the checkout (#971)" \
    || fail "audit failed for the wrong reason: $audit_report"; }

tampered_reordered="$(WORKFLOW="$release_node_workflow" python3 - <<'PY'
import os

import yaml

workflow = yaml.safe_load(os.environ["WORKFLOW"])
for job in (workflow.get("jobs") or {}).values():
    steps = job.get("steps") or []
    names = [step.get("name") for step in steps]
    if "Resolve restart-safe release state" not in names:
        continue
    checkout_index = next(
        i for i, step in enumerate(steps)
        if str(step.get("uses") or "").startswith("actions/checkout@")
    )
    consumer_index = names.index("Resolve restart-safe release state")
    steps.insert(checkout_index, steps.pop(consumer_index))
    job["steps"] = steps
print(yaml.safe_dump(workflow, sort_keys=False))
PY
)"
audit_report="$(audit_checkout_credentials release-node "$tampered_reordered" 2>&1)" \
  && fail "audit did not notice the consuming step now runs before the checkout (#974)" \
  || { grep -q "exemption stale" <<<"$audit_report" \
    && pass "moving the consuming step before the checkout re-flags it (#974)" \
    || fail "audit failed for the wrong reason: $audit_report"; }

grep -q "renovate-changelog.yml@$sha" <<<"$renovate_attribution" \
  && grep -qE "^ +contract_ref: $sha$" <<<"$renovate_attribution" \
  && pass "Renovate attribution caller binds reusable workflow and helper contract pin" \
  || fail "Renovate attribution caller does not bind its immutable pin"
grep -qE '^  pull_request_target:$' <<<"$renovate_attribution" \
  && grep -qE '^    types: \[opened, reopened, synchronize\]$' <<<"$renovate_attribution" \
  && ! grep -qE '^  (pull_request|push|workflow_dispatch|workflow_run|schedule):' <<<"$renovate_attribution" \
  && pass "Renovate attribution caller is limited to reviewed pull_request_target events" \
  || fail "Renovate attribution caller exposes an unsafe event"
grep -qF "github.event.pull_request.head.repo.full_name == github.repository" <<<"$renovate_attribution" \
  && grep -qF "github.event.pull_request.user.login == 'app/renovate'" <<<"$renovate_attribution" \
  && grep -qF "github.event.pull_request.user.login == 'renovate[bot]'" <<<"$renovate_attribution" \
  && grep -qF "startsWith(github.event.pull_request.head.ref, 'renovate/')" <<<"$renovate_attribution" \
  && pass "Renovate attribution caller rejects forks, other authors, and other branches" \
  || fail "Renovate attribution caller lacks an admission predicate"
grep -qF 'release_app_client_id: ${{ vars.RELEASE_APP_CLIENT_ID }}' <<<"$renovate_attribution" \
  && grep -qF 'release_environment: release-app' <<<"$renovate_attribution" \
  && grep -qE '^  pull-requests: read$' <<<"$renovate_attribution" \
  && grep -qE '^    secrets: inherit$' <<<"$renovate_attribution" \
  && ! grep -qE 'ORG_ADMIN_TOKEN|contents: write' <<<"$renovate_attribution" \
  && pass "Renovate attribution caller selects the dedicated App environment and exact read scopes" \
  || fail "Renovate attribution caller broadens credentials or lacks PR-read permission"

# 5. A ref that is not a bare commit is rejected, not quoted and passed through.
# An earlier sibling generator accepted a ref and let YAML be injected through
# it; the guard is asserted, not assumed.
for bad in 'main' "$(printf 'main\n    if: false')" '../../evil' "${sha^^}" "${sha}0" ''; do
  if bash "$gen" workflow "$bad" >/dev/null 2>&1; then
    fail "generator accepted a non-commit ref: '$bad'"
  else
    pass "generator rejects non-commit ref: '${bad//$'\n'/\\n}'"
  fi
done
for mode in generated-artifacts generated-artifacts-with-adr-index renovate-attribution adr-index-generator pr-gate; do
  if bash "$gen" "$mode" main >/dev/null 2>&1; then
    fail "$mode accepted a mutable ref"
  else
    pass "$mode rejects a mutable ref"
  fi
done

# 6. An unknown mode fails rather than emitting an empty file.
bash "$gen" bogus "$sha" >/dev/null 2>&1 \
  && fail "generator accepted an unknown mode" || pass "generator rejects an unknown mode"

grep -qF "node-version: \${{ '24' }}" <<<"$default_release" \
  && grep -q "scope: '@verjson'" <<<"$default_release" \
  && pass "release-node keeps the verJSON and Node 24 defaults" \
  || fail "release-node changed its backward-compatible defaults"
grep -qF '# Legacy compatibility input; all Node release jobs use fresh ubuntu-24.04 runners.' <<<"$default_release" \
  && pass "release-node documents its hosted-runner contract" \
  || fail "release-node runner input no longer documents its compatibility scope"
assert_explicit_release_version() {
  local mode="$1" workflow="$2" version_input first_verify_step
  version_input="$(sed -n '/^      version:$/,/^      prefix:$/p' <<<"$workflow" | sed '$d')"
  first_verify_step="$(awk '/^  verify:$/ { in_verify=1 } in_verify && /^    steps:$/ { getline; print; exit }' <<<"$workflow")"
  grep -qF 'description: Exact SemVer tag to release' <<<"$version_input" \
    && grep -qF 'required: true' <<<"$version_input" \
    && ! grep -qF 'default:' <<<"$version_input" \
    && [ "$first_verify_step" = '      - name: Require an explicit release version' ] \
    && pass "$mode requires a version before any release work" \
    || fail "$mode permits a blank version or starts release work before validating it"
}
assert_explicit_release_version release-node "$default_release"
version_guard="$(python3 -c 'import sys, yaml; print(yaml.safe_load(sys.stdin)["jobs"]["verify"]["steps"][0]["run"])' <<<"$default_release")"
guard_utf8="$(python3 -c 'import sys, yaml; print(yaml.safe_load(sys.stdin)["jobs"]["verify"]["steps"][0]["env"].get("PYTHONUTF8", ""))' <<<"$default_release")"
# Bash's \u escape is locale-dependent; exercise UTF-8 bytes with the emitted step's decoder.
[ -n "$version_guard" ] && [ "$guard_utf8" = 1 ] \
  && ! env LC_ALL=C PYTHONUTF8="$guard_utf8" INPUT_VERSION='' bash -c "$version_guard" >/dev/null 2>&1 \
  && ! env LC_ALL=C PYTHONUTF8="$guard_utf8" INPUT_VERSION='  ' bash -c "$version_guard" >/dev/null 2>&1 \
  && ! env LC_ALL=C PYTHONUTF8="$guard_utf8" INPUT_VERSION=$'\xc2\xa0' bash -c "$version_guard" >/dev/null 2>&1 \
  && env LC_ALL=C PYTHONUTF8="$guard_utf8" INPUT_VERSION='v1.2.3' bash -c "$version_guard" >/dev/null 2>&1 \
  && pass "release dispatch rejects blank versions before resolving a plan" \
  || fail "release dispatch version guard accepts blank input or rejects an explicit version"
component_trigger="$(sed -n '/^on:$/,/^permissions:$/p' <<<"$component_release" | sed '$d')"
expected_component_trigger="$(cat <<'YAML'
on:
  workflow_dispatch:
    inputs:
      version:
        description: Exact SemVer tag to release
        required: true
        type: string
      prefix:
        description: Exact version namespace prefix; independent from component
        required: false
        type: string
        default: 'schema-v'
      expected_head:
        description: Optional exact default-branch head derived by release-propose
        required: false
        type: string
        default: ''
      selector_digest:
        description: Optional canonical selection digest derived by release-propose
        required: false
        type: string
        default: ''
      fragments:
        description: Newline-separated NEXT fragment filenames; empty selects the requested component stream
        required: false
        type: string
        default: ''
      component:
        description: Optional component stream; empty selects only unscoped fragments
        required: false
        type: string
        default: 'cli-schema'

YAML
)"
[ "$component_trigger" = "$expected_component_trigger" ] \
  && grep -qF 'release-node '"$sha"' --default-prefix schema-v --default-component cli-schema --only-package-dir packages/cli-schema' <<<"$component_release" \
  && pass "release-node emits byte-exact component workflow defaults (#1565)" \
  || fail "release-node component workflow defaults or provenance differ from the canonical bytes"
yaml_keyword_release="$(bash "$gen" release-node "$sha" --default-prefix null-v --default-component null)"
[ "$(grep -cF "default: 'null-v'" <<<"$yaml_keyword_release")" -eq 1 ] \
  && [ "$(grep -cF "default: 'null'" <<<"$yaml_keyword_release")" -eq 1 ] \
  && pass "component defaults remain strings when their names are YAML keywords (#1565)" \
  || fail "component defaults permit YAML scalar coercion"
for component_mode in release-snapshot release-artifact; do
  if [ "$component_mode" = release-artifact ]; then
    component_mode_release="$(bash "$gen" "$component_mode" "$sha" --build-runner ubuntu-24.04 --default-prefix schema-v --default-component cli-schema)"
  else
    component_mode_release="$(bash "$gen" "$component_mode" "$sha" --default-prefix schema-v --default-component cli-schema)"
  fi
  component_mode_trigger="$(sed -n '/^on:$/,/^permissions:$/p' <<<"$component_mode_release" | sed '$d')"
  assert_explicit_release_version "$component_mode" "$component_mode_release"
  [ "$component_mode_trigger" = "$expected_component_trigger" ] \
    && pass "$component_mode emits the same byte-exact component workflow defaults (#1565)" \
    || fail "$component_mode component workflow defaults differ from release-node"
done
if bash "$gen" contract-test "$sha" --default-prefix schema-v --default-component cli-schema >/dev/null 2>&1; then
  fail "contract-test accepted release-caller-only default options"
else
  pass "non-release modes reject release-caller default options (#1565)"
fi
grep -qF '# The verification suite runs after package.json has been stamped to the' <<<"$default_release" \
  && grep -qF '# dispatched version. Its expected version must be read dynamically from' <<<"$default_release" \
  && grep -qF '# package.json; never assert a hardcoded version literal.' <<<"$default_release" \
  && pass "release-node warns adopters to derive version expectations from stamped package metadata (#862)" \
  || fail "release-node omits the stamped-version warning from its generated header"
grep -qF 'PACKAGE_VERSION: ${{ steps.release-version.outputs.package-version }}' <<<"$default_release" \
  && grep -qF 'Release verification failed against stamped dispatch version $PACKAGE_VERSION. Check for the hardcoded-version footgun:' <<<"$default_release" \
  && pass "release-node diagnoses the stamped version and hardcoded-version footgun (#862)" \
  || fail "release-node omits the stamped-version verification diagnostic"
grep -qF "node-version: \${{ '22.23.1' }}" <<<"$custom_release" \
  && grep -q "scope: '@acme'" <<<"$custom_release" \
  && grep -qF "release-assets: '[\"contract/schema.graphql\",\"contract/schema.sha256\"]'" <<<"$custom_release" \
  && pass "release-node emits validated adopter parameters" \
  || fail "release-node ignored custom scope or Node version"

renovate_inert_node_versions="$(printf '%s\n' "$default_release" | grep -cF "node-version: \${{ '24' }}")"
[ "$renovate_inert_node_versions" -eq 2 ] \
  && pass "both generated Node-version fields are Renovate-inert" \
  || fail "release-node emitted $renovate_inert_node_versions of 2 Node-version fields as Renovate-inert expressions"

stamp_command="$(
  awk '
    /^      - name: Stamp the dispatched package versions$/ { found = 1; next }
    found && /^        run: \|$/ { in_run = 1; next }
    in_run && /^      - name:/ { exit }
    in_run { sub(/^          /, ""); print }
  ' <<<"$custom_release"
)"
stamp_root="$(mktemp -d)"
printf '{"name":"same-version-fixture","version":"0.1.0"}\n' >"$stamp_root/package.json"
mkdir "$stamp_root/compat"
printf '{"name":"same-version-compat-fixture","version":"0.1.0"}\n' >"$stamp_root/compat/package.json"
if (
  cd "$stamp_root" &&
  PACKAGE_VERSION=0.1.0 eval "$stamp_command" >/dev/null &&
  PACKAGE_VERSION=0.2.0 eval "$stamp_command" >/dev/null &&
  [ "$(node -p "require('./package.json').version")" = "0.2.0" ] &&
  [ "$(node -p "require('./compat/package.json').version")" = "0.2.0" ]
); then
  pass "generated version stamp updates every published package and accepts same-version releases (#557, #579)"
else
  fail "generated version stamp does not update every published package"
fi
rm -rf "$stamp_root"

grep -q 'EXPECTED_RELEASE_NODE_VERSION="22.23.1"' <<<"$custom_contract" \
  && grep -q 'EXPECTED_RELEASE_SCOPE="@acme"' <<<"$custom_contract" \
  && grep -qF "GENERATOR_RELEASE_PACKAGE_DIRS_JSON='[\".\",\"compat\"]'" <<<"$custom_contract" \
  && grep -qF 'RELEASE_CALLER_PACKAGE_DIRS_JSON="$workflow_package_dirs_json"' <<<"$custom_contract" \
  && grep -qF "GENERATOR_RELEASE_PACKAGE_DIR_FLAG='--package-dir'" <<<"$custom_contract" \
  && grep -qF "EXPECTED_RELEASE_CALLER_PACKAGE_DIRS_JSON='{\".github/workflows/release.yml\":[\".\",\"compat\"]}'" <<<"$custom_contract" \
  && grep -qF "EXPECTED_RELEASE_ASSETS_JSON='[\"contract/schema.graphql\",\"contract/schema.sha256\"]'" <<<"$custom_contract" \
  && grep -qF "package-dirs: '[\".\",\"compat\"]'" <<<"$custom_release" \
  && pass "contract-test preserves generator parameters and checks caller-specific directories" \
  || fail "contract-test does not bind the selected release parameters"

for bad_args in \
  "--scope Acme" \
  "--scope @Acme" \
  "--scope @acme/other" \
  "--scope @acme --scope @other" \
  "--node-version lts/*" \
  "--node-version 022" \
  "--node-version 22.0.0.1" \
  "--node-version 24 --node-version 22" \
  "--default-prefix schema-v" \
  "--default-component cli-schema" \
  "--default-prefix v --default-component cli-schema" \
  "--default-prefix Schema-v --default-component cli-schema" \
  "--default-prefix schema --default-component cli-schema" \
  "--default-prefix schema-v --default-component CLI-schema" \
  "--default-prefix schema-v --default-component -cli-schema" \
  "--default-prefix schema-v --default-component aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
  "--default-prefix schema-v --default-prefix other-v --default-component cli-schema" \
  "--default-prefix schema-v --default-component cli-schema --default-component other" \
  "--package-dir ../compat" \
  "--package-dir /tmp/compat" \
  "--package-dir -compat" \
  "--package-dir ~compat" \
  "--package-dir compat --package-dir compat" \
  "--package-dir ."; do
  # Intentional word splitting: each fixture is a complete argument sequence.
  # shellcheck disable=SC2086
  if bash "$gen" release-node "$sha" $bad_args >/dev/null 2>&1; then
    fail "release-node accepted invalid or duplicate parameters: $bad_args"
  else
    pass "release-node rejects invalid or duplicate parameters: $bad_args"
  fi
done
for bad_assets in \
  "--release-asset ../schema.graphql" \
  "--release-asset /tmp/schema.graphql" \
  "--release-asset contract/./schema.graphql" \
  "--release-asset contract/schema.graphql --release-asset contract/schema.graphql" \
  "--release-asset a/schema.graphql --release-asset b/schema.graphql"; do
  # Intentional word splitting: fixtures contain no whitespace-bearing paths.
  # shellcheck disable=SC2086
  bash "$gen" release-node "$sha" $bad_assets >/dev/null 2>&1 \
    && fail "release-node accepted invalid release assets: $bad_assets" \
    || pass "release-node rejects invalid release assets: $bad_assets"
done
bash "$gen" workflow "$sha" --release-asset contract/schema.graphql >/dev/null 2>&1 \
  && fail "workflow mode accepted release assets" \
  || pass "non-release-node modes reject release assets"
bash "$gen" workflow "$sha" --scope @acme >/dev/null 2>&1 \
  && fail "workflow mode accepted release-only parameters" \
  || pass "non-release generator modes reject release-only parameters"

# 7. The emitted renderer fails closed when the contract cannot be fetched, and
# leaves no partial file behind for the next run to exec as if it were the
# contract. Exercised with a stubbed curl so no network is required.
# Redefined once the adopter scheduler exists. Early exits only remove the scratch tree.
drain_adopter_jobs() { :; }
trap 'drain_adopter_jobs; rm -rf "$tmproot"' EXIT
mkdir -p "$tmproot/repo/scripts" "$tmproot/repo/NEXT" "$tmproot/bin" "$tmproot/cache"
printf '%s\n' '#!/usr/bin/env bash' 'exit 1' > "$tmproot/bin/curl"
chmod +x "$tmproot/bin/curl"
printf '%s\n' "$renderer" > "$tmproot/repo/scripts/render-next.sh"

set +e
PATH="$tmproot/bin:$PATH" XDG_CACHE_HOME="$tmproot/cache" \
  bash "$tmproot/repo/scripts/render-next.sh" >/dev/null 2>"$tmproot/err"
rc=$?
# Back to the file's own mode, not -e: this suite reports every failure and
# summarizes at the end, so leaving -e on would abort at the first one and skip
# the summary — silently, and increasingly so as assertions are appended below.
set +e

[ "$rc" -ne 0 ] \
  && pass "generated renderer exits non-zero when the contract cannot be fetched" \
  || fail "generated renderer exited 0 despite a failed fetch"
grep -q 'cannot fetch the changelog contract' "$tmproot/err" \
  && pass "generated renderer reports why the fetch failed" \
  || fail "generated renderer gave no fetch-failure diagnostic"
[ -z "$(find "$tmproot/cache" -name '.changelog.*' 2>/dev/null)" ] \
  && pass "generated renderer leaves no partial download behind" \
  || fail "generated renderer left a partial download in the cache"
[ ! -f "$tmproot/cache/verjson-changelog/$sha/changelog.py" ] \
  && pass "generated renderer does not create the contract on failure" \
  || fail "generated renderer created a contract file from a failed fetch"

# 8. The generated contract test must survive the thing it protects.
#
# Three repositories hand-copied a contract test asserting a PRE-RELEASE tree:
# named fragment titles, hashed released entries, "no CHANGELOG.md yet",
# "render-released is empty". `release` consumes NEXT/, writes
# CHANGELOG/<version>.md and generates the root CHANGELOG.md, so every one of
# those is false the moment the contract works as intended. Adopters wire the
# suite into `npm test`, which release workflows run before publishing, so the
# first dispatched release pushed its tag and then died in the publish job:
# orphaned tag, nothing published, main red thereafter (#309).
#
# The load-bearing assertion is therefore not "it emits valid bash" but "it
# exits 0 against an adopter BOTH before and after a real release".

contract_src="$repo_root/scripts/changelog.py"
[ -f "$contract_src" ] || { echo "FAIL - missing $contract_src"; exit 1; }

emitted="$tmproot/contract-test.sh"
if bash "$gen" contract-test "$sha" >"$emitted" 2>"$tmproot/err"; then
  pass "contract-test mode emits a file"
else
  fail "contract-test mode failed: $(cat "$tmproot/err")"
  echo "$fails failed"
  exit 1
fi

# 4b. The generator ships the suite that covers it (#1380). Nothing generated
# the ADR-index test, so ~95 adopters each hand-wrote one, and a hand-written
# copy rots without saying so: one built every fixture in a shape the generator
# rejects and stayed green for months, because every case ahead of the first
# successful run asserted a rejection and duly got one, for the wrong reason.
#
# Assert the emitted suite IS the canonical one, rewritten only where the
# adopter layout requires, rather than a second body maintained here.
expected_adr_index_test="$(
  printf '%s\n' "$(git -C "$repo_root" show "$sha:scripts/ci-gate/gen-adr-index.test.sh")" \
    | tail -n +2 \
    | sed 's|^repo_root="\$(cd "\$here/\.\./\.\." && pwd)"$|repo_root="$(cd "$here/.." \&\& pwd)"|'
)"
printf '%s\n' "$adr_index_test" | grep -F 'repo_root="$(cd "$here/.." && pwd)"' >/dev/null \
  && ! printf '%s\n' "$adr_index_test" | grep -F 'repo_root="$(cd "$here/../.." && pwd)"' >/dev/null \
  && pass "adr-index-test resolves the repository root from the adopter's scripts/ layout" \
  || fail "adr-index-test did not rewrite the repository root for the adopter layout"
cmp -s <(printf '%s\n' "$adr_index_test" | sed '1,/^# CONTRACT_REF=/d') <(printf '%s\n' "$expected_adr_index_test") \
  && pass "adr-index-test emits the canonical suite, not a second hand-kept body" \
  || fail "adr-index-test body diverges from the canonical scripts/ci-gate/gen-adr-index.test.sh"
printf '%s\n' "$adr_index_test" | grep -E "^# CONTRACT_REF=$sha\$" >/dev/null \
  && grep -qF 'Generated by verJSON/.github scripts/gen-changelog-caller.sh' <<<"$adr_index_test" \
  && pass "adr-index-test records the pin and its generated provenance" \
  || fail "adr-index-test does not record the pin it was generated at"
printf '%s\n' "$adr_index_test" | bash -n 2>/dev/null \
  && pass "adr-index-test emits valid bash" || fail "adr-index-test emits invalid bash"

# The suite has to actually exercise the generator from the adopter's layout,
# not merely parse there. Run the two generated files together the way an
# adopter runs them.
adr_suite_adopter="$tmproot/adr-suite-adopter"
mkdir -p "$adr_suite_adopter/scripts" "$adr_suite_adopter/docs/decisions"
printf '%s\n' "$adr_index_generator" >"$adr_suite_adopter/scripts/gen-adr-index.sh"
printf '%s\n' "$adr_index_test" >"$adr_suite_adopter/scripts/gen-adr-index.test.sh"
chmod +x "$adr_suite_adopter/scripts/gen-adr-index.sh"
printf '# Decisions\n\n<!-- BEGIN ADR INDEX -->\n<!-- END ADR INDEX -->\n' \
  >"$adr_suite_adopter/docs/decisions/README.md"
if bash "$adr_suite_adopter/scripts/gen-adr-index.test.sh" >"$tmproot/adr-adopter.log" 2>&1; then
  pass "the generated suite passes against the generated generator in an adopter layout"
else
  fail "the generated suite failed in an adopter layout: $(tail -3 "$tmproot/adr-adopter.log")"
fi
# A suite that cannot fail is the defect #1380 reports, so prove this one still
# detects a broken generator rather than only that it exits 0.
sed -i 's/^  \[ "\$day" -le "\$max_day" \]$/  return 0/' "$adr_suite_adopter/scripts/gen-adr-index.sh"
bash "$adr_suite_adopter/scripts/gen-adr-index.test.sh" >"$tmproot/adr-adopter-mutant.log" 2>&1 \
  && fail "the generated suite passed against a generator that accepts impossible dates" \
  || pass "the generated suite detects a broken generator instead of passing vacuously"

# The suite is a member of the generated set, so the contract test pins its
# digest exactly as it pins the generator's — a partial regeneration fails there.
adr_index_test_digest="$(printf '%s\n' "$adr_index_test" | sha256sum | cut -d' ' -f1)"
grep -qE "^ADR_INDEX_TEST_SHA256=\"$adr_index_test_digest\"\$" "$emitted" \
  && pass "the contract test pins the digest of the emitted ADR-index suite" \
  || fail "the contract test does not pin the emitted ADR-index suite's digest"
grep -qF 'Generate the canonical ADR-index test at $CONTRACT_REF in a clean temporary checkout' "$emitted" \
  && ! grep -qF '> scripts/gen-adr-index.test.sh' "$emitted" \
  && pass "the contract test names how to acquire the pinned ADR-index suite" \
  || fail "ADR-index test guidance is missing or includes a truncating redirection"
# Only an adopter that opted into ADR checking owes the suite. A repository with
# no docs/decisions/ must not be made to carry it.
adr_test_gate="$(sed -n '/^validate_adr_generator()/,/^}/p' "$emitted")"
grep -qF 'scripts/gen-adr-index.test.sh' <<<"$adr_test_gate" \
  && pass "the ADR-index suite requirement is gated on adr-index: true" \
  || fail "the ADR-index suite requirement is not confined to the adr-index gate"

# Both ADR pins must digest what their mode writes, not the resolver's raw
# bytes: the two agree only while the canonical file ends in exactly one
# newline, and the day one did not, every adopter would fail a contract test
# against a file it had just regenerated correctly.
empty_digest="$(printf '' | sha256sum | cut -d' ' -f1)"
# A mode that emits nothing makes this comparison agree with itself: the pin and
# the mode would both digest an empty stream, so the assertion has to establish
# that bytes were written before it can read anything into them matching.
for pinned in "ADR_INDEX_SHA256:adr-index-generator" "ADR_INDEX_TEST_SHA256:adr-index-test"; do
  pin_name="${pinned%%:*}"
  pin_mode="${pinned#*:}"
  pin_recorded="$(grep -m1 "^$pin_name=" "$emitted" | cut -d'"' -f2)"
  pin_bytes="$tmproot/pin-$pin_mode.out"
  pin_mode_status=0
  bash "$gen" "$pin_mode" "$sha" >"$pin_bytes" 2>/dev/null || pin_mode_status=$?
  pin_emitted="$(sha256sum <"$pin_bytes" | cut -d' ' -f1)"
  if [ "$pin_mode_status" != 0 ] || [ ! -s "$pin_bytes" ]; then
    # Bytes on disk are what makes the rest of this comparison mean anything: an
    # empty file digests to the empty-string hash on both sides and agrees with
    # itself, so `-s` is the assertion, not a redundant guard in front of one.
    fail "$pin_mode emitted nothing, so $pin_name would match it only vacuously"
  elif [ -n "$pin_recorded" ] && [ "$pin_recorded" = "$pin_emitted" ]; then
    pass "$pin_name digests the bytes $pin_mode writes to disk"
  else
    fail "$pin_name does not match what $pin_mode emits ($pin_recorded vs $pin_emitted)"
  fi
  rm -f "$pin_bytes"
done
# A resolver that produces nothing must pin nothing. `sha256sum` digests an empty
# stream without complaint, and that empty-string digest is a real-looking pin no
# adopter file can ever match — it would turn a refusal to emit into a contract
# test that simply cannot be satisfied, and make the emitted `[ -n ... ]` guards
# vacuously true. Build a ref whose canonical suite has lost the line the rewrite
# anchors on, and require an empty pin rather than a digest.
# The fixture commit is written into a scratch object store: `hash-object -w`
# and `commit-tree` would otherwise leave dangling objects in the real
# repository for a test that only needs them for the length of this block.
mutated_objects="$tmproot/mutated-objects"
staged_fixture=true
mkdir -p "$mutated_objects" || {
  fail "could not create the scratch object store for the ADR-index refusal fixture"
  staged_fixture=false
}
# Absolute: a relative alternate resolves against each child process's cwd, and
# the generator runs git from its own directory. Assigning through `export` would
# report export's own status rather than rev-parse's, leaving the bare path
# "/objects" behind and degrading this block into a silent skip.
common_git_dir=""
git_dir_status=0
common_git_dir="$(git -C "$repo_root" rev-parse --path-format=absolute --git-common-dir)" || git_dir_status=$?
if [ "$git_dir_status" -ne 0 ] || [ -z "$common_git_dir" ]; then
  fail "could not resolve the object store for the ADR-index refusal fixture"
  staged_fixture=false
else
  # Exported only once the path is known good: the empty-generator block below
  # is not gated on $staged_fixture, so publishing a bare "/objects" here would
  # starve it of every object rather than skip it.
  export GIT_ALTERNATE_OBJECT_DIRECTORIES="$common_git_dir/objects"
  export GIT_OBJECT_DIRECTORY="$mutated_objects"
fi
# A zero-length mktemp file is a deliberately empty index for read-tree to fill;
# were read-tree to fail, the tree below would carry one path and quietly make
# this whole block vacuous, so its status is checked.
mutated_index="$(mktemp)"
if [ "$staged_fixture" = true ]; then
  GIT_INDEX_FILE="$mutated_index" git -C "$repo_root" read-tree "$sha" \
    || { fail "could not stage the pinned tree for the ADR-index refusal fixture"; staged_fixture=false; }
fi
# Every assertion below consumes this fixture. When staging fails they receive an
# empty ref and report their own unrelated failures, which is exactly how a wrong
# alternates path surfaced as "adr-index-test did not refuse as documented"
# instead of as the staging failure it was. Skip them rather than let one cause
# produce three misleading verdicts; the suite still runs its ~1800 sibling lines.
if [ "$staged_fixture" = true ]; then
  mutated_suite="$(mktemp)"
  # An unchecked `show | sed` is the worst of the construction faults: a path
  # that no longer exists leaves an empty file, `hash-object` writes an empty
  # blob, and the generator then refuses it with the very message this block
  # asserts — a green run that tested nothing.
  git -C "$repo_root" show "$sha:scripts/ci-gate/gen-adr-index.test.sh" \
    | sed 's|^repo_root=.*|repo_root="$(git rev-parse --show-toplevel)"|' >"$mutated_suite"
  show_status=${PIPESTATUS[0]}
  mutated_tree=""
  if [ "$show_status" -ne 0 ] || [ ! -s "$mutated_suite" ]; then
    fail "could not read the canonical ADR-index suite for the refusal fixture"
    staged_fixture=false
  elif ! mutated_blob="$(git -C "$repo_root" hash-object -w "$mutated_suite")" \
    || [ -z "$mutated_blob" ]; then
    fail "could not write the mutated ADR-index suite blob"
    staged_fixture=false
  elif ! GIT_INDEX_FILE="$mutated_index" git -C "$repo_root" update-index \
    --cacheinfo 100644,"$mutated_blob",scripts/ci-gate/gen-adr-index.test.sh; then
    # Left unchecked this does not fail — it leaves the original blob in the
    # index, so the fixture is simply not mutated and the assertions below
    # report the branch under test as misbehaving.
    fail "could not replace the ADR-index suite in the refusal fixture"
    staged_fixture=false
  elif ! mutated_tree="$(GIT_INDEX_FILE="$mutated_index" git -C "$repo_root" write-tree)" \
    || [ -z "$mutated_tree" ]; then
    fail "could not write the ADR-index refusal fixture tree"
    staged_fixture=false
  fi
fi

if [ "$staged_fixture" = true ]; then
  # commit-tree refuses without a committer identity, and a CI runner has none
  # configured: it dies with "unable to auto-detect email address". Supply one
  # through the environment rather than writing git config, so the fixture needs
  # nothing of the host and leaves nothing behind.
  mutated_sha="$(
    GIT_AUTHOR_NAME='changelog-caller-contract' \
    GIT_AUTHOR_EMAIL='changelog-caller-contract@invalid' \
    GIT_COMMITTER_NAME='changelog-caller-contract' \
    GIT_COMMITTER_EMAIL='changelog-caller-contract@invalid' \
    git -C "$repo_root" commit-tree "$mutated_tree" -p "$sha" -m 'anchor removed'
  )"
  # Without this the block continues with an empty ref, the generator refuses it
  # on ref validation, and the assertions below report that refusal as though the
  # branch under test had misbehaved. Every verdict past here needs a real ref.
  [ -n "$mutated_sha" ] || {
    fail "could not build the ADR-index refusal fixture commit"
    staged_fixture=false
  }
fi
rm -f "$mutated_index" "${mutated_suite:-}"

if [ "$staged_fixture" = true ]; then
  # The scratch store stays exported until the last assertion below: the fixture
  # commit lives only there, so a child generator that cannot read it fails at
  # digest resolution instead of reaching the refusal branch being asserted.

  refusal_err="$tmproot/adr-index-test-refusal.err"
  refusal_status=0
  bash "$gen" adr-index-test "$mutated_sha" >/dev/null 2>"$refusal_err" \
    || refusal_status=$?
  # Status 3 and the stated reason, not merely nonzero: distinguishing a refusal
  # from a failure to resolve is the whole point of the branch being asserted.
  if [ "$refusal_status" = 3 ] \
    && grep -q 'no longer resolves its repository root as expected' "$refusal_err"; then
    pass "adr-index-test refuses with status 3 once its rewrite anchor is gone"
  else
    fail "adr-index-test did not refuse as documented (status=$refusal_status): $(tail -1 "$refusal_err")"
  fi

  mutated_contract="$(bash "$gen" contract-test "$mutated_sha" 2>/dev/null)"
  mutated_pin="$(grep -m1 '^ADR_INDEX_TEST_SHA256=' <<<"$mutated_contract" | cut -d'"' -f2)"
  # An empty pin is only evidence if a contract test was emitted at all: a mode
  # that failed wholesale also yields an empty pin, and would otherwise report the
  # pass this assertion is supposed to earn.
  if ! grep -q "^CONTRACT_REF=\"$mutated_sha\"$" <<<"$mutated_contract"; then
    fail "contract-test emitted nothing for a ref whose ADR-index suite cannot be rewritten"
  elif [ -z "$mutated_pin" ]; then
    pass "an unresolvable ADR-index suite pins nothing rather than the empty digest"
  elif [ "$mutated_pin" = "$empty_digest" ]; then
    fail "an unresolvable ADR-index suite pinned the empty-string digest, which no adopter file can match"
  else
    fail "an unresolvable ADR-index suite pinned an unexpected digest: $mutated_pin"
  fi
fi
unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

# The other end of the same fault: a resolver can succeed and yield nothing — an
# empty blob at the ref, or a 200 with an empty body. `printf` would turn that
# into a lone newline, non-empty enough to satisfy every downstream guard while
# pinning a real-looking digest over a one-byte generator.
#
# This fixture deliberately writes into the real object store rather than the
# scratch one above. The assertions below require the generator to *emit* at the
# fixture ref, not merely to refuse it, so the child process has to be able to
# read the fixture back — and a refusal is indistinguishable from an unreadable
# ref, which is how an assertion of this shape goes vacuously green. A handful of
# unreferenced loose objects in a test checkout is the cheaper trade.
newline_digest="$(printf '\n' | sha256sum | cut -d' ' -f1)"
empty_sha=""
empty_tree=""
empty_index="$(mktemp)"
# Every step here is checked: a fixture built by several plumbing commands that
# reports only the first one's status turns a later failure into an apparent
# misbehavior of the branch under test.
if ! GIT_INDEX_FILE="$empty_index" git -C "$repo_root" read-tree "$sha"; then
  fail "could not stage the pinned tree for the empty-generator fixture"
elif ! empty_blob="$(git -C "$repo_root" hash-object -w -t blob /dev/null)" \
  || [ -z "$empty_blob" ]; then
  fail "could not write the empty generator blob"
elif ! GIT_INDEX_FILE="$empty_index" git -C "$repo_root" update-index \
  --cacheinfo 100644,"$empty_blob",scripts/gen-adr-index.sh; then
  fail "could not replace gen-adr-index.sh in the empty-generator fixture"
elif ! empty_tree="$(GIT_INDEX_FILE="$empty_index" git -C "$repo_root" write-tree)" \
  || [ -z "$empty_tree" ]; then
  fail "could not write the empty-generator fixture tree"
else
  empty_sha="$(
    GIT_AUTHOR_NAME='changelog-caller-contract' \
    GIT_AUTHOR_EMAIL='changelog-caller-contract@invalid' \
    GIT_COMMITTER_NAME='changelog-caller-contract' \
    GIT_COMMITTER_EMAIL='changelog-caller-contract@invalid' \
    git -C "$repo_root" commit-tree "$empty_tree" -p "$sha" -m 'generator emptied'
  )"
  [ -n "$empty_sha" ] || fail "could not build the empty-generator fixture commit"
fi
rm -f "$empty_index"

if [ -n "$empty_sha" ]; then
  # Any nonzero status would otherwise read as the refusal under test, and an
  # unresolvable ref exits 1 from a much earlier guard — the very regression the
  # object-store fix above repaired. Anchor on a sibling mode that resolves the
  # same ref through the same plumbing, then require the refusal's own reason.
  anchor_err="$tmproot/empty-generator-anchor.err"
  if ! bash "$gen" adr-index-test "$empty_sha" 2>"$anchor_err" | grep . >/dev/null; then
    # Carry the reason out: this fixture has now failed for three host-specific
    # causes (a linked worktree's git dir, a runner's missing committer identity,
    # a scratch object store the child could not read), and each time the verdict
    # alone said nothing about which.
    fail "the empty-generator fixture is not resolvable, so its refusal proves nothing: $(head -3 "$anchor_err")"
  else
    empty_refusal="$tmproot/empty-generator.err"
    if bash "$gen" adr-index-generator "$empty_sha" >/dev/null 2>"$empty_refusal"; then
      fail "adr-index-generator emitted a lone newline for an empty canonical generator"
    elif grep -q 'the canonical scripts/gen-adr-index.sh is empty at' "$empty_refusal"; then
      pass "adr-index-generator refuses an empty canonical generator rather than emitting a newline"
    else
      fail "adr-index-generator refused the empty canonical generator for another reason: $(cat "$empty_refusal")"
    fi
    rm -f "$empty_refusal"
  fi
  rm -f "$anchor_err"

  empty_contract="$(bash "$gen" contract-test "$empty_sha" 2>/dev/null)"
  empty_pin="$(grep -m1 '^ADR_INDEX_SHA256=' <<<"$empty_contract" | cut -d'"' -f2)"
  if ! grep -q "^CONTRACT_REF=\"$empty_sha\"$" <<<"$empty_contract"; then
    fail "contract-test emitted nothing for a ref whose ADR-index generator is empty"
  elif [ -z "$empty_pin" ]; then
    pass "an empty canonical generator pins nothing rather than the digest of a newline"
  elif [ "$empty_pin" = "$newline_digest" ]; then
    fail "an empty canonical generator pinned the digest of a lone newline"
  else
    fail "an empty canonical generator pinned an unexpected digest: $empty_pin"
  fi
fi

# The pin is taken from the mode's emitted form, not the resolver's raw bytes.
# Those two agree for as long as the canonical generator ends in exactly one
# trailing newline, which it does today — so at $sha the loop above passes
# whichever source the pin came from, and the unification it exists to protect
# is asserted only in prose. Build the day it stops agreeing: a canonical
# generator carrying one extra trailing newline, where the emitted form (which
# normalizes to exactly one) and the raw bytes have different digests. Under a
# resolver-sourced pin the adopter's contract test would fail against a file it
# had just regenerated correctly, and this fixture is what catches that.
#
# Both fixtures below are pinned to a temporary ref for the length of the block.
# They are otherwise unreferenced loose objects in the shared store, and a
# concurrent `git gc --auto` between construction and read-back makes them
# unresolvable — which fails closed, but as noise rather than a verdict.
newline_ref="refs/tmp/changelog-caller-contract/$$-empty-generator"
[ -n "${empty_sha:-}" ] && git -C "$repo_root" update-ref "$newline_ref" "$empty_sha"

padded_ref="refs/tmp/changelog-caller-contract/$$-padded-generator"
padded_sha=""
padded_index="$(mktemp)"
padded_blob_file="$tmproot/padded-generator.sh"
# Every plumbing step is checked separately: a fixture built by a chain that
# reports only the first status turns a later construction failure into an
# apparent misbehavior of the branch under test.
if ! git -C "$repo_root" show "$sha:scripts/gen-adr-index.sh" >"$padded_blob_file"; then
  fail "could not read the canonical generator for the padded-newline fixture"
elif ! printf '\n' >>"$padded_blob_file"; then
  fail "could not pad the canonical generator with a second trailing newline"
elif ! GIT_INDEX_FILE="$padded_index" git -C "$repo_root" read-tree "$sha"; then
  fail "could not stage the pinned tree for the padded-newline fixture"
elif ! padded_blob="$(git -C "$repo_root" hash-object -w -t blob "$padded_blob_file")" \
  || [ -z "$padded_blob" ]; then
  fail "could not write the padded generator blob"
elif ! GIT_INDEX_FILE="$padded_index" git -C "$repo_root" update-index \
  --cacheinfo 100644,"$padded_blob",scripts/gen-adr-index.sh; then
  fail "could not replace gen-adr-index.sh in the padded-newline fixture"
elif ! padded_tree="$(GIT_INDEX_FILE="$padded_index" git -C "$repo_root" write-tree)" \
  || [ -z "$padded_tree" ]; then
  fail "could not write the padded-newline fixture tree"
else
  padded_sha="$(
    GIT_AUTHOR_NAME='changelog-caller-contract' \
    GIT_AUTHOR_EMAIL='changelog-caller-contract@invalid' \
    GIT_COMMITTER_NAME='changelog-caller-contract' \
    GIT_COMMITTER_EMAIL='changelog-caller-contract@invalid' \
    git -C "$repo_root" commit-tree "$padded_tree" -p "$sha" -m 'generator padded with a second trailing newline'
  )"
  [ -n "$padded_sha" ] || fail "could not build the padded-newline fixture commit"
fi
rm -f "$padded_index"
[ -n "$padded_sha" ] && git -C "$repo_root" update-ref "$padded_ref" "$padded_sha"

if [ -n "$padded_sha" ]; then
  padded_raw_digest="$(sha256sum <"$padded_blob_file" | cut -d' ' -f1)"
  padded_mode="$tmproot/padded-generator.out"
  padded_mode_status=0
  bash "$gen" adr-index-generator "$padded_sha" >"$padded_mode" 2>/dev/null \
    || padded_mode_status=$?
  padded_emitted_digest="$(sha256sum <"$padded_mode" | cut -d' ' -f1)"
  padded_contract="$(bash "$gen" contract-test "$padded_sha" 2>/dev/null)"
  padded_pin="$(grep -m1 '^ADR_INDEX_SHA256=' <<<"$padded_contract" | cut -d'"' -f2)"

  # The fixture only proves anything if the two candidate sources actually
  # disagree at this ref. If padding failed to change the emitted form, the pin
  # would match both and the assertion below would pass vacuously.
  if [ "$padded_mode_status" != 0 ] || [ ! -s "$padded_mode" ]; then
    fail "adr-index-generator emitted nothing at the padded-newline fixture (status=$padded_mode_status)"
  elif [ "$padded_raw_digest" = "$padded_emitted_digest" ]; then
    fail "the padded-newline fixture does not separate the resolver's bytes from the emitted form"
  elif ! grep -q "^CONTRACT_REF=\"$padded_sha\"$" <<<"$padded_contract"; then
    fail "contract-test emitted nothing for the padded-newline fixture"
  elif [ "$padded_pin" = "$padded_raw_digest" ]; then
    fail "ADR_INDEX_SHA256 digests the resolver's raw bytes, which no adopter regenerating the file can match"
  elif [ "$padded_pin" = "$padded_emitted_digest" ]; then
    pass "ADR_INDEX_SHA256 digests the emitted form even when the canonical bytes do not end in one newline"
  else
    fail "ADR_INDEX_SHA256 matched neither source at the padded-newline fixture: $padded_pin"
  fi
  rm -f "$padded_mode"
fi
rm -f "$padded_blob_file"
git -C "$repo_root" update-ref -d "$padded_ref" 2>/dev/null || true
git -C "$repo_root" update-ref -d "$newline_ref" 2>/dev/null || true


contract_validation="$(sed -n '/^  contract-test)/,/^    ;;/p' "$gen")"
if grep -q 'bash -n <"$syntax_input"' <<<"$contract_validation" \
   && ! grep -qE "printf .*\\|[[:space:]]*bash -n" <<<"$contract_validation"; then
  pass "contract-test syntax validation reads a completed file instead of a pipe"
else
  fail "contract-test syntax validation can still race a producer SIGPIPE"
fi

# Captured workflow blocks can be much larger than a pipe buffer. A validator
# such as grep -q or an awk program that exits after its first match may close
# stdin while printf is still writing, making pipefail replace the policy error
# with a producer-side Broken pipe. Feed every captured value by redirection so
# validation status never depends on consumer read-ahead.
emitted_validation="$(sed -n '/^emit_contract_test()/,/^}$/p' "$gen")"
if grep -qE 'printf .*\$\{?(snapshot_job|verify_job|publish_job|first_verify_step|job)' \
    <<<"$emitted_validation"; then
  fail "emitted contract validation still pipes a captured value into an early-exit consumer"
else
  pass "emitted contract validation redirects every captured value without a SIGPIPE producer"
fi

bash -n "$emitted" 2>/dev/null \
  && pass "emitted contract test is valid bash" \
  || fail "emitted contract test does not parse"

for bad in 'main' "$(printf 'main\n  if: false')" '../../evil' "${sha^^}" "${sha}0" ''; do
  if bash "$gen" contract-test "$bad" >/dev/null 2>&1; then
    fail "contract-test mode accepted a non-commit ref: '$bad'"
  else
    pass "contract-test mode rejects a non-commit ref: '${bad//$'\n'/\\n}'"
  fi
done

grep -q "CONTRACT_REF=\"$sha\"" "$emitted" \
  && pass "emitted contract test pins the requested commit" \
  || fail "emitted contract test does not carry the requested pin"

# Each grep below is one of the four shapes that made a hand-copied test a
# release time bomb. None may reappear via the generator.
# Implementation digests are exempt, and only they: CONTRACT_SHA256,
# ADR_INDEX_SHA256 and ADR_INDEX_TEST_SHA256 pin code that is executed, and
# EXPECTED_CODEOWNERS_SHA256 pins a generated member of the adopter set
# (ADR 0210), and #1712 pins the restart-safe release-state script per mode.
# None of those code hashes changes with a release. The shape this guards
# against is an assertion pinned to repository CONTENT — a released entry's
# hash — which every release
# invalidates (#304, #309).
grep -vE '^(CONTRACT_SHA256|ADR_INDEX_SHA256|ADR_INDEX_TEST_SHA256|EXPECTED_CODEOWNERS_SHA256)="[0-9a-f]{64}"$' "$emitted" \
  | grep -vE '^[[:space:]]+"release-(node|artifact|snapshot)": "[0-9a-f]{64}",$' \
  | grep -E '[0-9a-f]{64}' >/dev/null \
  && fail "emitted test hardcodes a content hash of a released entry" \
  || pass "no hashed released entries (a release adds sections)"
grep -qF '[ ! -e "$root/CHANGELOG.md" ]' "$emitted" \
  && fail "emitted test asserts CHANGELOG.md is absent (a release generates it)" \
  || pass "no assertion that the aggregate changelog is absent"
grep -qF 'render-released --repo-root "$root")" ]' "$emitted" \
  && fail "emitted test asserts released history is empty (a release writes it)" \
  || pass "no assertion that released history is empty"
# A bare `^## ` grep is fine — it matches any heading. What must never reappear
# is a heading grep that *names* something, because the only thing an adopter's
# test could name is a fragment title, and a release deletes it. The generator's
# own Newer/Older fixtures are titles it creates itself, so they are exempt.
stray_titles="$(grep -oE "\^## [A-Za-z][^']*" "$emitted" | grep -vxE '\^## (Newer|Older)\$')"
[ -z "$stray_titles" ] \
  && pass "fragment assertions are derived from the tree, not named inline" \
  || fail "emitted test greps for literal fragment titles: $(tr '\n' ' ' <<<"$stray_titles")"

# Content-addressed by ref, so seeding that cache path with THIS repository's
# changelog.py makes the run hermetic and exercises the contract as it stands in
# this pull request. $sha cannot exist upstream, so a seeding bug fails loudly
# with a 404 rather than quietly passing against whatever is published.
export XDG_CACHE_HOME="$tmproot/adopter-cache"
mkdir -p "$XDG_CACHE_HOME/verjson-changelog/$sha"
cp "$contract_src" "$XDG_CACHE_HOME/verjson-changelog/$sha/changelog.py"
else
  contract_src="$repo_root/scripts/changelog.py"
  capture_mode generated_contract contract-test "$sha"
  emitted="$tmproot/contract-test.sh"
  printf '%s\n' "$generated_contract" >"$emitted"
  capture_mode custom_release release-node "$sha" --scope @acme --node-version 22.23.1 --package-dir compat --release-asset contract/schema.graphql --release-asset contract/schema.sha256
  capture_mode release_node_workflow release-node "$sha"
  export XDG_CACHE_HOME="$tmproot/adopter-cache"
  mkdir -p "$XDG_CACHE_HOME/verjson-changelog/$sha"
  cp "$contract_src" "$XDG_CACHE_HOME/verjson-changelog/$sha/changelog.py"
fi
if [ "${CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY:-0}" = 1 ]; then
  [ "$fails" -eq 0 ] || exit 1
  echo "All generator contract assertions passed."
  exit 0
fi

build_adopter() {
  # build_adopter <dir> [with-release-workflow: yes|no|legacy] [caller]
  #
  # `yes` installs the GENERATED release caller, which is what an adopter is now
  # told to commit. `legacy` reproduces the hand-copied verjson-payments shape
  # every migrated repository carried before #463/#464/#465: it verifies nothing
  # before the irreversible snapshot, installs with GITHUB_TOKEN, and lets the
  # two halves of one release route onto two runner pools.
  local dir="$1" with_release="${2:-yes}" caller="${3:-workflow}" release_mode="${4:-release-node}"
  # Default adopters are identical. Later cases copy the pristine snapshot instead of
  # regenerating the caller set and committing it again (#1733).
  if [ "$with_release" = yes ] && [ "$caller" = workflow ] && [ "$release_mode" = release-node ] \
    && [ -n "${adopter_template:-}" ] && [ -d "$adopter_template" ]; then
    rm -rf "$dir"
    cp -a "$adopter_template" "$dir"
    return 0
  fi
  mkdir -p "$dir/NEXT" "$dir/scripts" "$dir/.github/workflows"
  bash "$gen" codeowners "$sha" >"$dir/.github/CODEOWNERS"
  bash "$gen" renderer "$sha" >"$dir/scripts/render-next.sh"
  bash "$gen" "$caller" "$sha" >"$dir/.github/workflows/changelog.yml"
  bash "$gen" pr-gate "$sha" >"$dir/.github/workflows/changelog-contract.yml"
  bash "$gen" renovate-attribution "$sha" >"$dir/.github/workflows/renovate-changelog.yml"
  if [ "$caller" = generated-artifacts-with-adr-index ]; then
    bash "$gen" adr-index-generator "$sha" >"$dir/scripts/gen-adr-index.sh"
    chmod +x "$dir/scripts/gen-adr-index.sh"
    # The generator and the suite that covers it are one set (#1380).
    bash "$gen" adr-index-test "$sha" >"$dir/scripts/gen-adr-index.test.sh"
  fi
  cp "$emitted" "$dir/scripts/changelog-contract.test.sh"
  chmod +x "$dir/scripts/render-next.sh" "$dir/scripts/changelog-contract.test.sh"
  if [ "$with_release" = yes ]; then
    if [ "$release_mode" = release-artifact ]; then
      bash "$gen" "$release_mode" "$sha" --build-runner ubuntu-24.04 >"$dir/.github/workflows/release.yml"
    else
      bash "$gen" "$release_mode" "$sha" >"$dir/.github/workflows/release.yml"
    fi
    bash "$gen" release-propose "$sha" --autonomy propose \
      >"$dir/.github/workflows/release-propose.yml"
  elif [ "$with_release" = legacy ]; then
    cat >"$dir/.github/workflows/release.yml" <<YAML
name: release
on:
  workflow_dispatch:
    inputs:
      version:
        required: true
        type: string
jobs:
  snapshot:
    uses: verJSON/.github/.github/workflows/changelog-release.yml@$sha
    with:
      contract_ref: $sha
      version: \${{ inputs.version }}
    secrets:
      push_token: \${{ secrets.ORG_ADMIN_TOKEN }}
  publish:
    needs: snapshot
    runs-on: ubuntu-24.04
    steps:
      - run: npm ci
        env:
          NODE_AUTH_TOKEN: \${{ secrets.GITHUB_TOKEN }}
      - run: npm test
YAML
  fi
  # Quoted, because that is what adopters actually write: YAML requires a quoted
  # scalar wherever a value contains `: `, which is every conventional-commit
  # title. An unquoted fixture let the emitted suite ship a front-matter parser
  # that kept the quotes as literal text and then reported the correctly-written
  # title as missing, so the fixture carries the real spelling.
  cat >"$dir/NEXT/2026-08-01-issue-7-first.md" <<'FRAGMENT'
---
date: 2026-08-01
issue: 7
title: 'fix(caller): first entry'
---

Body.
FRAGMENT
  cat >"$dir/NEXT/2026-07-31-issue-20260731T120000Z-second.md" <<'FRAGMENT'
---
date: 2026-07-31
id: 20260731T120000Z
title: Second entry
---

Body.
FRAGMENT
  git -C "$dir" init -q
  git -C "$dir" config user.name Test
  git -C "$dir" config user.email test@example.com
  git -C "$dir" add -A
  git -C "$dir" commit -qm initial
}

build_split_adopter() {
  local dir="$1"
  build_adopter "$dir" no workflow
  bash "$gen" generated-artifacts-with-adr-index "$sha" \
    >"$dir/.github/workflows/generated-artifacts.yml"
  bash "$gen" adr-index-generator "$sha" >"$dir/scripts/gen-adr-index.sh"
  chmod +x "$dir/scripts/gen-adr-index.sh"
  git -C "$dir" add -A
  git -C "$dir" commit -qm 'add generated artifacts caller'
}

run_adopter() {
  local status=0 adopter_log cache_env=() case_id
  case_id="$(basename "$1")"
  if [ "${caller_case_nested:-}" != 1 ]; then
    if ! changelog_caller_case_selected "$case_id"; then
      caller_assertions_suspended=1
      return 0
    fi
    caller_assertions_suspended=0
    printf 'running caller contract case: %s\n' "$case_id" >&2
  fi
  # A synchronous suite keeps running beside scheduled snapshots. It does not
  # wait for them: isolation already keeps their logs off this run.out (#1733).
  adopter_log="$1.contract-out"
  if [ "${ADOPTER_ISOLATED_LOG:-}" = 1 ]; then
    mkdir -p "$1.cache/verjson-changelog/$sha"
    cp -a "$XDG_CACHE_HOME/verjson-changelog/$sha/." "$1.cache/verjson-changelog/$sha/"
    cache_env=("XDG_CACHE_HOME=$1.cache")
  fi
  (
    cd "$1" && env "${cache_env[@]}" ./scripts/changelog-contract.test.sh
  ) >"$adopter_log" 2>&1 || status=$?
  if [ "${ADOPTER_ISOLATED_LOG:-}" != 1 ]; then
    last_rejection_pid=
    cp "$adopter_log" "$tmproot/run.out"
  fi
  return "$status"
}

# Independent adopter mutations share no directory. Run several at once on this
# job's CPUs instead of occupying the runner for one suite at a time (#1733).
ADOPTER_SLOTS="${ADOPTER_SLOTS:-$(nproc 2>/dev/null || echo 2)}"
[ "$ADOPTER_SLOTS" -gt 16 ] && ADOPTER_SLOTS=16
[ "$ADOPTER_SLOTS" -ge 1 ] || ADOPTER_SLOTS=1
adopter_job_pids=()
adopter_job_results=()
reject_seq=0

record_adopter_job() {
  local result="$1" status="$2" count=0
  if [ -f "$result" ]; then
    count="$(tr -cd '0-9' <"$result")"
    [ -n "$count" ] || count=1
    fails=$((fails + count))
  else
    fails=$((fails + 1))
    printf 'FAIL - adopter job exited %s without a result\n' "$status"
  fi
}

reap_adopter_pid() {
  local target="$1" i pid result status
  local -a kept_pids=() kept_results=()
  for i in "${!adopter_job_pids[@]}"; do
    pid="${adopter_job_pids[$i]}"
    result="${adopter_job_results[$i]}"
    if [ "$pid" != "$target" ]; then
      kept_pids+=("$pid")
      kept_results+=("$result")
      continue
    fi
    status=0
    wait "$pid" || status=$?
    record_adopter_job "$result" "$status"
  done
  if [ "${#kept_pids[@]}" -eq 0 ]; then
    adopter_job_pids=()
    adopter_job_results=()
  else
    adopter_job_pids=("${kept_pids[@]}")
    adopter_job_results=("${kept_results[@]}")
  fi
}

sync_last_rejection() {
  if [ -n "${last_rejection_pid:-}" ]; then
    reap_adopter_pid "$last_rejection_pid"
    if [ -n "${last_rejection_log:-}" ] && [ -f "$last_rejection_log" ]; then
      cp "$last_rejection_log" "$tmproot/run.out"
      # Suites print the tree they ran in. Scheduled jobs run on a snapshot so
      # the caller can keep mutating the original; rewrite that snapshot path
      # back before assertions that name the caller's directory.
      if [ -n "${last_rejection_copy:-}" ] && [ -n "${last_rejection_origin:-}" ]; then
        sed -i "s|$last_rejection_copy|$last_rejection_origin|g" "$tmproot/run.out"
      fi
    fi
    last_rejection_pid=
    last_rejection_copy=
    last_rejection_origin=
  fi
}

pump_adopter_jobs() {
  local pid result status
  while [ "${#adopter_job_pids[@]}" -ge "$ADOPTER_SLOTS" ]; do
    pid="${adopter_job_pids[0]}"
    result="${adopter_job_results[0]}"
    status=0
    wait "$pid" || status=$?
    record_adopter_job "$result" "$status"
    if [ "${#adopter_job_pids[@]}" -gt 1 ]; then
      adopter_job_pids=("${adopter_job_pids[@]:1}")
      adopter_job_results=("${adopter_job_results[@]:1}")
    else
      adopter_job_pids=()
      adopter_job_results=()
    fi
  done
}

drain_adopter_jobs() {
  local pid result status
  while [ "${#adopter_job_pids[@]}" -gt 0 ]; do
    pid="${adopter_job_pids[0]}"
    result="${adopter_job_results[0]}"
    status=0
    wait "$pid" || status=$?
    record_adopter_job "$result" "$status"
    if [ "${#adopter_job_pids[@]}" -gt 1 ]; then
      adopter_job_pids=("${adopter_job_pids[@]:1}")
      adopter_job_results=("${adopter_job_results[@]:1}")
    else
      adopter_job_pids=()
      adopter_job_results=()
    fi
  done
}


schedule_adopter_script() {
  local dir="$1" script="$2" seq result copy case_id
  case_id="$(basename "$dir")"
  if ! changelog_caller_case_selected "$case_id"; then
    caller_assertions_suspended=1
    return 0
  fi
  caller_assertions_suspended=0
  printf 'running caller contract case: %s\n' "$case_id" >&2
  reject_seq=$((reject_seq + 1))
  seq="$reject_seq"
  result="$tmproot/sched-result-$seq"
  copy="$tmproot/sched-$seq"
  rm -rf "$copy"
  cp -a "$dir" "$copy"
  (
    fails=0
    status=0
    caller_case_nested=1
    ADOPTER_ISOLATED_LOG=1
    run_adopter "$copy" || status=$?
    # shellcheck disable=SC2034 # read by the assertion script evaluated below
    ADOPTER_LOG="$copy.contract-out"
    eval "$script" || true
    printf '%s\n' "$fails" >"$result"
  ) &
  last_rejection_pid="$!"
  last_rejection_log="$copy.contract-out"
  last_rejection_copy="$copy"
  last_rejection_origin="$dir"
  adopter_job_pids+=("$last_rejection_pid")
  adopter_job_results+=("$result")
  pump_adopter_jobs
}

adopter="$tmproot/adopter"
build_adopter "$adopter"
adopter_template="$tmproot/adopter-template"
cp -a "$adopter" "$adopter_template"
[ -f "$adopter/.github/workflows/release.yml" ] \
  && [ ! -e "$adopter/.github/workflows/changelog-release.yml" ] \
  && pass "generated adopter installs the canonical release caller path" \
  || fail "generated adopter does not use only .github/workflows/release.yml"
grep -q "changelog-release.yml@$sha" "$adopter/.github/workflows/release.yml" \
  && grep -qE "^ +contract_ref: $sha$" "$adopter/.github/workflows/release.yml" \
  && pass "canonical release caller pins uses and contract_ref to the same commit" \
  || fail "canonical release caller does not bind uses and contract_ref to $sha"
grep -q "release-propose.yml@$sha" "$adopter/.github/workflows/release-propose.yml" \
  && grep -qE "^ +contract_ref: $sha$" "$adopter/.github/workflows/release-propose.yml" \
  && pass "canonical release proposer pins uses and contract_ref to the same commit" \
  || fail "canonical release proposer does not bind uses and contract_ref to $sha"
schedule_adopter_script "$adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite passes against an unreleased adopter"
else
  fail "emitted suite failed before any release: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

stale_renovate_caller="$tmproot/adopter-stale-renovate-caller"
cp -a "$adopter" "$stale_renovate_caller"
sed -i "s/renovate-changelog.yml@$sha/renovate-changelog.yml@0000000000000000000000000000000000000000/" \
  "$stale_renovate_caller/.github/workflows/renovate-changelog.yml"
schedule_adopter_script "$stale_renovate_caller" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a stale Renovate attribution reusable pin"
else
  pass "emitted suite rejects a stale Renovate attribution reusable pin"
fi
SCHED_SCRIPT
)"

overprivileged_renovate_caller="$tmproot/adopter-overprivileged-renovate-caller"
cp -a "$adopter" "$overprivileged_renovate_caller"
sed -i 's/^  contents: read$/  contents: write/' \
  "$overprivileged_renovate_caller/.github/workflows/renovate-changelog.yml"
schedule_adopter_script "$overprivileged_renovate_caller" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a Contents-write Renovate attribution caller"
else
  pass "emitted suite rejects a Contents-write Renovate attribution caller"
fi
SCHED_SCRIPT
)"

wrong_event_renovate_caller="$tmproot/adopter-wrong-event-renovate-caller"
cp -a "$adopter" "$wrong_event_renovate_caller"
sed -i 's/^  pull_request_target:$/  pull_request:/' \
  "$wrong_event_renovate_caller/.github/workflows/renovate-changelog.yml"
schedule_adopter_script "$wrong_event_renovate_caller" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted an event that cannot write the bot head"
else
  pass "emitted suite rejects a Renovate attribution caller on the wrong event"
fi
SCHED_SCRIPT
)"

missing_gate_renovate_caller="$tmproot/adopter-missing-renovate-gate"
cp -a "$adopter" "$missing_gate_renovate_caller"
sed -i '/^    if: >-$/,/^    uses:/ { /^    uses:/!d; }' \
  "$missing_gate_renovate_caller/.github/workflows/renovate-changelog.yml"
schedule_adopter_script "$missing_gate_renovate_caller" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a Renovate caller with no admission gate"
else
  pass "emitted suite rejects a Renovate caller with no admission gate (#1014)"
fi
SCHED_SCRIPT
)"

for mutation in fork actor branch duplicate-if; do
  mutated_renovate_caller="$tmproot/adopter-mutated-renovate-$mutation"
  cp -a "$adopter" "$mutated_renovate_caller"
  case "$mutation" in
    fork)
      sed -i 's/head.repo.full_name == github.repository/head.repo.full_name != github.repository/' \
        "$mutated_renovate_caller/.github/workflows/renovate-changelog.yml"
      ;;
    actor)
      sed -i "s/'renovate\[bot\]'/'dependabot[bot]'/" \
        "$mutated_renovate_caller/.github/workflows/renovate-changelog.yml"
      ;;
    branch)
      sed -i "s/head.ref, 'renovate\/'/head.ref, 'deps\/'/" \
        "$mutated_renovate_caller/.github/workflows/renovate-changelog.yml"
      ;;
    duplicate-if)
      sed -i '/^    uses:/a\    if: true' \
        "$mutated_renovate_caller/.github/workflows/renovate-changelog.yml"
      ;;
  esac
  run_adopter "$mutated_renovate_caller" \
    && fail "emitted suite accepted a Renovate caller with a mutated $mutation predicate" \
    || pass "emitted suite rejects a Renovate caller with a mutated $mutation predicate (#1014)"
done

stale_proposer="$tmproot/adopter-stale-proposer"
cp -a "$adopter" "$stale_proposer"
sed -i "s/release-propose.yml@$sha/release-propose.yml@0000000000000000000000000000000000000000/" \
  "$stale_proposer/.github/workflows/release-propose.yml"
schedule_adopter_script "$stale_proposer" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release proposer on another pin"
else
  pass "emitted suite rejects a release proposer on another pin"
fi
SCHED_SCRIPT
)"

overprivileged_proposer="$tmproot/adopter-overprivileged-proposer"
cp -a "$adopter" "$overprivileged_proposer"
sed -i '/^      issues: write$/a\      actions: write' \
  "$overprivileged_proposer/.github/workflows/release-propose.yml"
schedule_adopter_script "$overprivileged_proposer" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted both issue and dispatch authority in propose mode"
else
  pass "emitted suite rejects mixed release-proposer write authority"
fi
SCHED_SCRIPT
)"

event_selected_proposer="$tmproot/adopter-event-selected-proposer"
cp -a "$adopter" "$event_selected_proposer"
sed -i '/^      contract_ref:/a\      autonomy: ${{ inputs.autonomy }}' \
  "$event_selected_proposer/.github/workflows/release-propose.yml"
schedule_adopter_script "$event_selected_proposer" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted event-selected release autonomy"
else
  pass "emitted suite rejects event-selected release autonomy"
fi
SCHED_SCRIPT
)"

nested_adopter="$tmproot/adopter-nested-only-release"
build_adopter "$nested_adopter"
bash "$gen" release-node "$sha" --only-package-dir packages/cli-schema \
  --default-prefix schema-v --default-component cli-schema \
  >"$nested_adopter/.github/workflows/release.yml"
bash "$gen" contract-test "$sha" --only-package-dir packages/cli-schema >"$nested_adopter/scripts/changelog-contract.test.sh"
schedule_adopter_script "$nested_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "nested-only release and contract agree on component defaults without selecting root (#1286, #1565)"
else
  fail "nested-only generated contract failed: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
sed -i "s/default: 'schema-v'/default: v/" \
  "$nested_adopter/.github/workflows/release.yml"
schedule_adopter_script "$nested_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "component release contract accepted a mutated default prefix"
else
  pass "component release contract rejects default-prefix byte drift (#1565)"
fi
SCHED_SCRIPT
)"
bash "$gen" release-node "$sha" --only-package-dir packages/cli-schema \
  --default-prefix schema-v --default-component cli-schema \
  >"$nested_adopter/.github/workflows/release.yml"
sed -i "s/default: 'cli-schema'/default: ''/" \
  "$nested_adopter/.github/workflows/release.yml"
schedule_adopter_script "$nested_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "component release contract accepted a mutated default component"
else
  pass "component release contract rejects default-component byte drift (#1565)"
fi
SCHED_SCRIPT
)"
bash "$gen" release-node "$sha" --only-package-dir packages/cli-schema \
  --default-prefix schema-v --default-component cli-schema \
  >"$nested_adopter/.github/workflows/release.yml"
sed -i 's/package_dirs=(packages\/cli-schema)/package_dirs=(. packages\/cli-schema)/' "$nested_adopter/.github/workflows/release.yml"
schedule_adopter_script "$nested_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "nested-only contract accepted an extra root verification stamp"
else
  pass "nested-only contract rejects extra root verification stamp (#1286)"
fi
SCHED_SCRIPT
)"
bash "$gen" release-node "$sha" --only-package-dir packages/cli-schema \
  --default-prefix schema-v --default-component cli-schema \
  >"$nested_adopter/.github/workflows/release.yml"
sed -i 's/\["packages\/cli-schema"\]/[".","packages\/cli-schema"]/' "$nested_adopter/.github/workflows/release.yml"
schedule_adopter_script "$nested_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "nested-only contract accepted root publication"
else
  pass "nested-only contract rejects extra root publication (#1286)"
fi
SCHED_SCRIPT
)"

multi_release_adopter="$tmproot/adopter-multi-release"
build_adopter "$multi_release_adopter"
bash "$gen" release-node "$sha" >"$multi_release_adopter/.github/workflows/release.yml"
bash "$gen" release-node "$sha" --only-package-dir packages/cli-schema \
  >"$multi_release_adopter/.github/workflows/release-cli-schema.yml"
bash "$gen" contract-test "$sha" \
  --release-caller-package-dirs .github/workflows/release-cli-schema.yml=packages/cli-schema \
  >"$multi_release_adopter/scripts/changelog-contract.test.sh"
chmod +x "$multi_release_adopter/scripts/changelog-contract.test.sh"
schedule_adopter_script "$multi_release_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "generated contract validates multiple release callers with distinct package selections"
else
  fail "generated contract rejects multiple release callers with distinct package selections"
fi
SCHED_SCRIPT
)"
sed -i '0,/^  workflow_dispatch:$/s//  push:/' \
  "$multi_release_adopter/.github/workflows/release-cli-schema.yml"
schedule_adopter_script "$multi_release_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "generated contract accepted a push trigger on the first of multiple release callers"
else
  pass "generated contract checks the trigger on every release caller (#1488)"
fi
SCHED_SCRIPT
)"
sync_last_rejection
grep -qF "$multi_release_adopter/.github/workflows/release-cli-schema.yml declares no readable top-level" \
  "$tmproot/run.out" \
  && pass "multi-release trigger failure names the non-final caller" \
  || fail "multi-release trigger failure did not identify the non-final caller: $(tail -2 "$tmproot/run.out")"

custom_adopter="$tmproot/adopter-custom-release"
build_adopter "$custom_adopter"
printf '%s\n' "$custom_release" >"$custom_adopter/.github/workflows/release.yml"
printf '%s\n' "$custom_contract" >"$custom_adopter/scripts/changelog-contract.test.sh"
chmod +x "$custom_adopter/scripts/changelog-contract.test.sh"
schedule_adopter_script "$custom_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "custom release caller and contract test accept the same parameters (#520)"
else
  fail "matching custom release parameters were rejected: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
sed -i 's/--package-dir compat/--package-dir ignored --only-package-dir . --only-package-dir compat/' \
  "$custom_adopter/.github/workflows/release.yml"
schedule_adopter_script "$custom_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "custom contract accepted mixed additive and exact package selection flags"
else
    grep -qF 'does not declare valid package directories in generator provenance (#1717)' "$ADOPTER_LOG" \
      && pass "custom contract rejects mixed package selection modes" \
      || fail "mixed package selection was rejected for another reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
printf '%s\n' "$custom_release" >"$custom_adopter/.github/workflows/release.yml"
sed -i 's/compat/other/g' "$custom_adopter/.github/workflows/release.yml"
schedule_adopter_script "$custom_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "custom contract accepted a coordinated package-selection change"
else
    grep -qF 'does not stamp every package directory selected for publication (#557)' "$ADOPTER_LOG" \
      && pass "custom contract pins package selection independently of workflow provenance" \
      || fail "coordinated package selection was rejected for another reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
printf '%s\n' "$custom_release" >"$custom_adopter/.github/workflows/release.yml"
sed -i "s/scope: '@acme'/scope: '@other'/" \
  "$custom_adopter/.github/workflows/release.yml"
schedule_adopter_script "$custom_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "custom contract accepted a release scope that drifted after generation"
else
  pass "custom contract rejects release parameter drift (#520)"
fi
SCHED_SCRIPT
)"

omitted_stamp_adopter="$tmproot/adopter-omitted-secondary-stamp"
build_adopter "$omitted_stamp_adopter"
printf '%s\n' "$custom_release" >"$omitted_stamp_adopter/.github/workflows/release.yml"
printf '%s\n' "$custom_contract" >"$omitted_stamp_adopter/scripts/changelog-contract.test.sh"
chmod +x "$omitted_stamp_adopter/scripts/changelog-contract.test.sh"
sed -i 's/package_dirs=(. compat)/package_dirs=(.)/' \
  "$omitted_stamp_adopter/.github/workflows/release.yml"
schedule_adopter_script "$omitted_stamp_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "custom contract accepted a verification stamp that omitted a published package"
else
  pass "custom contract rejects a verification stamp that omits a published package (#557)"
fi
SCHED_SCRIPT
)"

generated_adopter="$tmproot/adopter-generated-artifacts"
build_adopter "$generated_adopter" no generated-artifacts
schedule_adopter_script "$generated_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite accepts the generated-artifacts caller"
else
  fail "emitted suite rejects the generated-artifacts caller: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

retired_adopter="$tmproot/adopter-retired-changelog-workflow"
build_adopter "$retired_adopter" no workflow
sed -i \
  -e "s#generated-artifacts.yml@$sha#changelog-validate.yml@$sha#" \
  -e '/^      changelog: true$/d' \
  "$retired_adopter/.github/workflows/changelog.yml"
schedule_adopter_script "$retired_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts the retired changelog-validate workflow"
else
  pass "emitted suite requires the generated workflow behind the required context (#835)"
fi
SCHED_SCRIPT
)"

cross_job_adopter="$tmproot/adopter-cross-job-generated-artifacts-caller"
build_adopter "$cross_job_adopter" no workflow
sed -i 's/^  changelog:$/  generated-artifacts:/' \
  "$cross_job_adopter/.github/workflows/changelog.yml"
cat >>"$cross_job_adopter/.github/workflows/changelog.yml" <<'YAML'
  changelog:
    runs-on: ubuntu-24.04
    steps:
      - run: 'true'
YAML
schedule_adopter_script "$cross_job_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts canonical fields spread across different jobs"
else
  pass "emitted suite binds the canonical caller fields to the changelog job (#835)"
fi
SCHED_SCRIPT
)"

named_job_adopter="$tmproot/adopter-named-changelog-job"
cp -a "$generated_adopter" "$named_job_adopter"
sed -i '/^  changelog:$/a\    name: renamed required check' \
  "$named_job_adopter/.github/workflows/changelog.yml"
schedule_adopter_script "$named_job_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts a changelog job with a check-name override"
else
  pass "emitted suite rejects a job-level name that changes the required context (#835)"
fi
SCHED_SCRIPT
)"

matrix_job_adopter="$tmproot/adopter-matrix-changelog-job"
cp -a "$generated_adopter" "$matrix_job_adopter"
sed -i '/^  changelog:$/a\    strategy:\n      matrix:\n        shard: [one, two]' \
  "$matrix_job_adopter/.github/workflows/changelog.yml"
schedule_adopter_script "$matrix_job_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts a matrixed changelog job"
else
  pass "emitted suite rejects strategy fields that suffix the required context (#835)"
fi
SCHED_SCRIPT
)"

secrets_job_adopter="$tmproot/adopter-secrets-changelog-job"
cp -a "$generated_adopter" "$secrets_job_adopter"
sed -i 's/^    with:$/    secrets:/' \
  "$secrets_job_adopter/.github/workflows/changelog.yml"
schedule_adopter_script "$secrets_job_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts changelog inputs nested under secrets"
else
  pass "emitted suite binds changelog inputs to the canonical with mapping (#835)"
fi
SCHED_SCRIPT
)"

typo_job_adopter="$tmproot/adopter-typo-changelog-job"
cp -a "$generated_adopter" "$typo_job_adopter"
sed -i 's/^    with:$/    wiht:/' \
  "$typo_job_adopter/.github/workflows/changelog.yml"
schedule_adopter_script "$typo_job_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts changelog inputs nested under a typo mapping"
else
  pass "emitted suite rejects a typo in the canonical with mapping (#835)"
fi
SCHED_SCRIPT
)"

extra_input_adopter="$tmproot/adopter-extra-changelog-input"
cp -a "$generated_adopter" "$extra_input_adopter"
sed -i '/^      contract_ref:/a\      unexpected_input: true' \
  "$extra_input_adopter/.github/workflows/changelog.yml"
schedule_adopter_script "$extra_input_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts an additional changelog caller input"
else
  pass "emitted suite enforces the exact changelog caller input set (#835)"
fi
SCHED_SCRIPT
)"

split_adopter="$tmproot/adopter-split-generated-artifacts"
build_split_adopter "$split_adopter"
schedule_adopter_script "$split_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts duplicate changelog callers at two paths"
else
  pass "emitted suite retires the ambiguous split caller topology (#835)"
fi
SCHED_SCRIPT
)"

renamed_duplicate_adopter="$tmproot/adopter-renamed-duplicate-changelog"
cp -a "$generated_adopter" "$renamed_duplicate_adopter"
cp "$renamed_duplicate_adopter/.github/workflows/changelog.yml" \
  "$renamed_duplicate_adopter/.github/workflows/docs-validation.yml"
schedule_adopter_script "$renamed_duplicate_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts a duplicate caller hidden behind another filename"
else
  pass "emitted suite scans every workflow for renamed duplicate callers (#835)"
fi
SCHED_SCRIPT
)"

legacy_duplicate_adopter="$tmproot/adopter-legacy-duplicate-changelog"
cp -a "$generated_adopter" "$legacy_duplicate_adopter"
cat >"$legacy_duplicate_adopter/.github/workflows/legacy-validation.yml" <<YAML
name: legacy validation
on:
  pull_request:
jobs:
  legacy:
    uses: verJSON/.github/.github/workflows/changelog-validate.yml@$sha
    with:
      contract_ref: $sha
YAML
schedule_adopter_script "$legacy_duplicate_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts a legacy caller beside the canonical caller"
else
  pass "emitted suite rejects an additional changelog-validate caller (#835)"
fi
SCHED_SCRIPT
)"

adr_adopter="$tmproot/adopter-generated-artifacts-adr"
build_adopter "$adr_adopter" no generated-artifacts-with-adr-index
schedule_adopter_script "$adr_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite accepts ADR checking with the acquired pinned generator"
else
  fail "emitted suite rejects the acquired ADR generator: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

# A repository that acquired the generator but kept a hand-written suite is the
# state #1380 found across the fleet. It must not read as conformant.
missing_adr_test_adopter="$tmproot/adopter-adr-without-suite"
cp -a "$adr_adopter" "$missing_adr_test_adopter"
rm -f "$missing_adr_test_adopter/scripts/gen-adr-index.test.sh"
schedule_adopter_script "$missing_adr_test_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts ADR checking without the pinned generator suite"
else
  pass "emitted suite rejects ADR checking without its pinned generator suite (#1380)"
fi
SCHED_SCRIPT
)"

handwritten_adr_test_adopter="$tmproot/adopter-handwritten-adr-suite"
cp -a "$adr_adopter" "$handwritten_adr_test_adopter"
printf '#!/usr/bin/env bash\necho "ok - looks like a test"\n' \
  >"$handwritten_adr_test_adopter/scripts/gen-adr-index.test.sh"
schedule_adopter_script "$handwritten_adr_test_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts a hand-written ADR generator suite"
else
  pass "emitted suite rejects a hand-written ADR generator suite (#1380)"
fi
SCHED_SCRIPT
)"
rm -f "$adr_adopter/scripts/gen-adr-index.sh"
schedule_adopter_script "$adr_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts adr-index: true without scripts/gen-adr-index.sh"
else
  grep -q 'adr-index: true requires the pinned scripts/gen-adr-index.sh' "$ADOPTER_LOG" \
    && pass "emitted suite rejects ADR checking without its pinned generator" \
    || fail "missing ADR generator fails without an acquisition remedy: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
bash "$gen" adr-index-generator "$sha" >"$adr_adopter/scripts/gen-adr-index.sh"
chmod +x "$adr_adopter/scripts/gen-adr-index.sh"
printf '\n# local drift\n' >>"$adr_adopter/scripts/gen-adr-index.sh"
schedule_adopter_script "$adr_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts a divergent ADR index generator"
else
  grep -q 'is not the generator pinned at' "$ADOPTER_LOG" \
    && pass "emitted suite rejects a divergent ADR index generator" \
    || fail "divergent ADR generator fails without a regeneration remedy: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

optional_generator_adopter="$tmproot/adopter-optional-adr-generator"
build_adopter "$optional_generator_adopter" yes generated-artifacts
bash "$gen" adr-index-generator "$sha" >"$optional_generator_adopter/scripts/gen-adr-index.sh"
chmod +x "$optional_generator_adopter/scripts/gen-adr-index.sh"
printf '\n# local payload drift with current pin\n' >>"$optional_generator_adopter/scripts/gen-adr-index.sh"
schedule_adopter_script "$optional_generator_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "changelog-only caller accepts stale optional ADR generator payload with current pin"
else
  grep -qF 'is not the generator pinned at' "$ADOPTER_LOG" \
    && pass "changelog-only caller verifies optional ADR generator payload, not just its pin marker" \
    || fail "stale optional ADR generator failed without a content-digest diagnosis: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
optional_adr_test_adopter="$tmproot/adopter-optional-adr-test"
build_adopter "$optional_adr_test_adopter" yes generated-artifacts
bash "$gen" adr-index-test "$sha" >"$optional_adr_test_adopter/scripts/gen-adr-index.test.sh"
printf '\n# local payload drift with current pin\n' >>"$optional_adr_test_adopter/scripts/gen-adr-index.test.sh"
schedule_adopter_script "$optional_adr_test_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "changelog-only caller accepts stale optional ADR test payload with current pin"
else
  grep -qF 'is not the test pinned at' "$ADOPTER_LOG" \
    && pass "changelog-only caller verifies optional ADR test payload, not just its pin marker" \
    || fail "stale optional ADR test failed without content-digest diagnosis: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

python3 "$contract_src" release --repo-root "$adopter" --version v1.0.0 >/dev/null 2>&1
{ [ -f "$adopter/CHANGELOG/v1.0.0.md" ] && [ -e "$adopter/CHANGELOG.md" ]; } \
  && pass "fixture release really consumed NEXT/ and wrote released history" \
  || fail "fixture release produced no released tree; the next check would be vacuous"

# The regression. Nothing in the hand-copied shape survived this step.
schedule_adopter_script "$adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite still passes AFTER a real release (#309)"
else
  fail "emitted suite breaks on the first release: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

# --------------------------------------------------------------------------
# #399 (duplicate #419): the render guard must tolerate ONLY an emptied NEXT/.
#
# The guard exists because `render-next` exits non-zero once a release has
# consumed NEXT/. Keyed on the exit status alone it reported
# `ok - no unreleased fragments to render` for EVERY renderer failure — an
# unreachable contract fetch, a digest mismatch, a malformed fragment, a missing
# python3, the #398 argv ceiling — and `2>/dev/null` discarded the only sentence
# that said which. A broken adopter announced a clean release.
#
# The two cases below are the same renderer failure distinguished only by whether
# fragments remain, which is why the tree and not the status has to decide.
# --------------------------------------------------------------------------
break_renderer() { # break_renderer <dir>
  # Fail the way a real adopter fails — the renderer exits non-zero with a
  # diagnostic on stderr — rather than by deleting it, which would trip the
  # earlier "is not executable" check and pass for the wrong reason. The
  # gen-changelog-caller.sh marker is kept so the "delegates to the contract"
  # check still passes and this fixture isolates the render guard alone.
  cat >"$1/scripts/render-next.sh" <<BROKEN
#!/usr/bin/env sh
# Generated by verJSON/.github scripts/gen-changelog-caller.sh renderer $sha
# gen-changelog-caller.sh
# The pin line is required: the emitted suite checks the renderer carries the
# same CONTRACT_REF before it ever renders, so a stub without it dies early and
# the render guard is never reached — which is how this fixture first passed for
# the wrong reason.
CONTRACT_REF="$sha"
echo "render-next: could not fetch the pinned contract (simulated)" >&2
exit 7
BROKEN
  chmod +x "$1/scripts/render-next.sh"
}

# A. Fragments present and the renderer broken: this must FAIL. It is the whole
#    defect — before the fix the suite reported success here.
broken="$tmproot/adopter-broken-renderer"
build_adopter "$broken"
break_renderer "$broken"
schedule_adopter_script "$broken" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "#399: a broken renderer with fragments still in NEXT/ reported success"
else
  pass "#399: a broken renderer with fragments present fails the suite"
  # The cause must reach the operator. Swallowing stderr is half the defect: a
  # failure that names nothing sends the adopter to the wrong file.
  grep -q 'could not fetch the pinned contract' "$ADOPTER_LOG" \
    && pass "#399: the renderer's own stderr is surfaced, not discarded" \
    || fail "#399: the failure hid the renderer's diagnostic: $(tail -3 "$ADOPTER_LOG")"
  grep -qE 'unreleased fragment\(s\) still in NEXT/' "$ADOPTER_LOG" \
    && pass "#399: the failure says why this is not the post-release case" \
    || fail "#399: the failure does not distinguish itself from an emptied NEXT/"
fi
SCHED_SCRIPT
)"

# B. The tolerated case, still tolerated. Without this, the fix could satisfy A
#    by failing on every non-zero exit — which would break every adopter the
#    moment they released, the exact regression the guard was added to avoid.
released_broken="$tmproot/adopter-released-broken"
build_adopter "$released_broken"
python3 "$contract_src" release --repo-root "$released_broken" --version v1.0.0 >/dev/null 2>&1
[ -z "$(find "$released_broken/NEXT" -maxdepth 1 -type f -name '*.md' \
    ! -name 'README.md' ! -name '0000-archive.md' 2>/dev/null)" ] \
  && pass "#399 fixture: the release really emptied NEXT/, so case B is not vacuous" \
  || fail "#399 fixture: NEXT/ still holds fragments; case B would prove nothing"
break_renderer "$released_broken"
schedule_adopter_script "$released_broken" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "#399: an emptied NEXT/ still tolerates a non-zero renderer exit"
else
  fail "#399: the fix broke the post-release case it exists to allow: $(tail -3 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

inject_render_failure() { # inject_render_failure <dir> <renderer|download|digest|python>
  local renderer="$1/scripts/render-next.sh"
  case "$2" in
    renderer)
      break_renderer "$1"
      ;;
    download)
      python3 - "$renderer" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = 'root="$(cd "$(dirname "$0")/.." && pwd)"\n'
replacement = needle + '''
XDG_CACHE_HOME="$root/.download-failure-cache"
curl() { echo "simulated contract download failure" >&2; return 22; }
'''
if text.count(needle) != 1:
    raise SystemExit("cannot locate generated renderer root")
open(path, "w", encoding="utf-8").write(text.replace(needle, replacement))
PY
      ;;
    digest)
      sed -i \
        -e 's/^CONTRACT_SHA256="[0-9a-f]\{64\}"$/CONTRACT_SHA256="0000000000000000000000000000000000000000000000000000000000000000"/' \
        -e "/^root=/a XDG_CACHE_HOME=\"\$root/.digest-failure-cache\"" \
        "$renderer"
      ;;
    python)
      sed -i 's/^exec python3 /exec verjson-missing-python3 /' "$renderer"
      ;;
    *)
      return 2
      ;;
  esac
}

for failure in renderer download digest python; do
  failing="$tmproot/adopter-$failure-failure"
  build_adopter "$failing"
  inject_render_failure "$failing" "$failure"
  if run_adopter "$failing"; then
    fail "a $failure failure with renderable NEXT/ fragments reported success"
  else
    pass "a $failure failure with renderable NEXT/ fragments fails closed"
  fi

  emptied="$tmproot/adopter-$failure-empty"
  build_adopter "$emptied"
  python3 "$contract_src" release --repo-root "$emptied" --version v1.0.0 >/dev/null 2>&1
  inject_render_failure "$emptied" "$failure"
sync_last_rejection
  run_adopter "$emptied" \
    && pass "a genuinely emptied NEXT/ remains distinct from a $failure failure" \
    || fail "an emptied NEXT/ is mistaken for a $failure failure: $(tail -3 "$tmproot/run.out")"
done

# An adopter with nothing to publish has no release.yml; `agents` and
# `github-runner` are in exactly that shape and must not be forced to invent one.
build_adopter "$tmproot/adopter-norelease" no
schedule_adopter_script "$tmproot/adopter-norelease" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite tolerates an adopter with no release workflow"
else
  fail "emitted suite requires a release workflow: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

# An unreleased NEXT/ has no upper bound: fragments are per-change, never
# batched, and only a release consumes them. Crossing 128 KiB of rendered output
# — MAX_ARG_STRLEN, the per-string execve ceiling, not the far larger ARG_MAX —
# killed the emitted suite with a bare "Argument list too long" and exit 126,
# naming neither the changelog nor the fragment count. Nothing could be released
# past it either, because the release path runs this suite (#398).
oversize="$tmproot/adopter-oversize"
build_adopter "$oversize"
filler="$(head -c 20000 </dev/zero | tr '\0' x)"
for day in 01 02 03 04 05 06 07 08; do
  issue=$((100 + 10#$day))
  cat >"$oversize/NEXT/2026-06-$day-issue-$issue-bulk.md" <<FRAGMENT
---
date: 2026-06-$day
issue: $issue
title: Bulk entry $day
---

$filler
FRAGMENT
done
git -C "$oversize" add -A
git -C "$oversize" commit -qm bulk

# Asserted, not assumed: a fixture that quietly renders under the ceiling would
# leave the case below passing for the wrong reason.
rendered_bytes="$( (cd "$oversize" && ./scripts/render-next.sh) | wc -c )"
[ "$rendered_bytes" -gt 131072 ] \
  && pass "oversize fixture renders past MAX_ARG_STRLEN ($rendered_bytes bytes)" \
  || fail "oversize fixture renders only $rendered_bytes bytes; the next check is vacuous"

# Exit 0 alone is not enough. The render block is guarded, and its else branch
# reports "no unreleased fragments" and exits 0 for *any* renderer failure — so a
# ceiling that migrated into the renderer would leave this case green with the
# render assertions never executed. Require the positive line.
schedule_adopter_script "$oversize" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ] && grep -q 'every unreleased fragment renders with its metadata linkage' "$ADOPTER_LOG"; then
  pass "emitted suite survives a NEXT/ larger than MAX_ARG_STRLEN (#398)"
else
  fail "emitted suite dies on or skips a large unreleased log: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

# A suite that passes everywhere is worthless. Each case below breaks exactly one
# invariant in a fresh adopter and requires a non-zero exit.
drain_adopter_jobs
reject_seq=0
# Mode as well as content: one of the mutations below only clears the executable
# bit, and a content-only fingerprint reports that as "changed nothing".
fingerprint() {
  ( cd "$1" && find . -type f -printf '%m %p\n' -exec sha256sum {} + | sort )
}

expect_rejection_body() {
  # expect_rejection_body <seq> <label> <mutator-fn> [mutator-args...]
  local seq="$1" label="$2" mutator="$3" dir
  shift 3
  dir="$tmproot/reject-$seq"
  build_adopter "$dir"
  # A mutation that edits nothing is rejected by nothing, and the case still
  # reads green — which is how a guard that cannot fail survives a review. The
  # fixture is fingerprinted before and after so a silently no-op mutator is a
  # failure of this file, not an endorsement of the emitted suite.
  fingerprint "$dir" >"$tmproot/before-$seq"
  "$mutator" "$dir" "$@"
  fingerprint "$dir" >"$tmproot/after-$seq"
  if cmp -s "$tmproot/before-$seq" "$tmproot/after-$seq"; then
    fail "mutation for '$label' changed nothing; the case is vacuous"
    return
  fi
  ADOPTER_ISOLATED_LOG=1 run_adopter "$dir" \
    && fail "emitted suite accepted $label" \
    || pass "emitted suite rejects $label"
}

expect_rejection() {
  # expect_rejection <label> <mutator-fn> [mutator-args...]
  local label="$1" mutator="$2" seq result
  shift 2
  if ! changelog_caller_case_selected "$label"; then
    caller_assertions_suspended=1
    return 0
  fi
  caller_assertions_suspended=0
  reject_seq=$((reject_seq + 1))
  seq="$reject_seq"
  result="$tmproot/reject-result-$seq"
  (
    fails=0
    caller_case_nested=1
    ADOPTER_ISOLATED_LOG=1
    expect_rejection_body "$seq" "$label" "$mutator" "$@"
    printf '%s\n' "$fails" >"$result"
  ) &
  last_rejection_pid="$!"
  last_rejection_log="$tmproot/reject-$seq.contract-out"
  last_rejection_copy=
  last_rejection_origin=
  adopter_job_pids+=("$last_rejection_pid")
  adopter_job_results+=("$result")
  pump_adopter_jobs
}

expect_release_mode_rejection_body() {
  local seq="$1" release_mode="$2" label="$3" mutator="$4" expected="$5" dir
  dir="$tmproot/reject-$seq"
  build_adopter "$dir" yes workflow "$release_mode"
  fingerprint "$dir" >"$tmproot/before-$seq"
  "$mutator" "$dir"
  fingerprint "$dir" >"$tmproot/after-$seq"
  if cmp -s "$tmproot/before-$seq" "$tmproot/after-$seq"; then
    fail "mutation '$label' changed nothing; vacuous"
    return
  fi
  if ADOPTER_ISOLATED_LOG=1 run_adopter "$dir"; then
    fail "emitted suite accepted $label"
  elif grep -qF "$expected" "$dir.contract-out"; then
    pass "emitted suite rejects $label for the expected reason"
  else
    fail "emitted suite rejected $label for another reason: $(tail -2 "$dir.contract-out")"
  fi
}

expect_release_mode_rejection() {
  local release_mode="$1" label="$2" mutator="$3" expected="$4" seq result
  if ! changelog_caller_case_selected "$label"; then
    caller_assertions_suspended=1
    return 0
  fi
  caller_assertions_suspended=0
  reject_seq=$((reject_seq + 1))
  seq="$reject_seq"
  result="$tmproot/reject-result-$seq"
  (
    fails=0
    caller_case_nested=1
    ADOPTER_ISOLATED_LOG=1
    expect_release_mode_rejection_body "$seq" "$release_mode" "$label" "$mutator" "$expected"
    printf '%s\n' "$fails" >"$result"
  ) &
  last_rejection_pid="$!"
  last_rejection_log="$tmproot/reject-$seq.contract-out"
  last_rejection_copy=
  last_rejection_origin=
  adopter_job_pids+=("$last_rejection_pid")
  adopter_job_results+=("$result")
  pump_adopter_jobs
}

break_pin() {
  sed -i 's/^CONTRACT_REF=.*/CONTRACT_REF="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"/' \
    "$1/scripts/render-next.sh"
}
handwrite_renderer() {
  printf '#!/usr/bin/env bash\nCONTRACT_REF="%s"\necho hand-rolled\n' "$sha" \
    >"$1/scripts/render-next.sh"
}
add_releaserc() { printf '{"branches":["main"]}\n' >"$1/.releaserc.json"; }
add_authored_log() { printf '# NEXT\n\n## An entry\n\nBody.\n' >"$1/NEXT.md"; }
uncanonical_fragment() {
  printf -- '---\ndate: 2026-08-01\nissue: 9\ntitle: Bad name\n---\n\nBody.\n' \
    >"$1/NEXT/2026-08-01-bad-name.md"
}
strip_executable() { chmod -x "$1/scripts/render-next.sh"; }
# The generated caller supplies only the dedicated App identity material. The
# reusable workflow owns token minting and constrains it to this repository.
drop_release_app_client_id() {
  sed -i '/^      release_app_client_id: /d' "$1/.github/workflows/release.yml"
}
drop_release_environment() {
  sed -i '/^      release_environment: /d' "$1/.github/workflows/release.yml"
}
restore_org_admin_token() {
  sed -i '/^      release_environment: /c\      push_token: ${{ secrets.ORG_ADMIN_TOKEN }}' \
    "$1/.github/workflows/release.yml"
}
wire_github_token() {
  sed -i '/^      release_environment: /c\      push_token: ${{ secrets.GITHUB_TOKEN }}' \
    "$1/.github/workflows/release.yml"
}
grant_snapshot_github_token_write() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("  snapshot:")
end = text.index("  publish:", start)
snapshot = text[start:end]
needle = "      contents: read\n"
if needle not in snapshot:
    raise SystemExit("snapshot fixture no longer has a read-only GITHUB_TOKEN")
snapshot = snapshot.replace(needle, "      contents: write\n", 1)
open(path, "w", encoding="utf-8").write(text[:start] + snapshot + text[end:])
PY
}

# #463/#464/#465. Each mutation below reproduces one defect the hand-copied
# release caller shipped to every migrated repository, applied to the generated
# caller so the emitted suite is the thing under test rather than the fixture.
drop_snapshot_needs() {
  sed -i '/^    needs: verify$/d' "$1/.github/workflows/release.yml"
}
drop_snapshot_runner() {
  sed -i '/^      runner: /d' "$1/.github/workflows/release.yml"
}
drop_resume_snapshot_checkout() {
  sed -i "/^      - name: Check out the existing snapshot for resumed verification$/,+6d" \
    "$1/.github/workflows/release.yml"
}
install_with_github_token() {
  sed -i 's|NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}|NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}|g' \
    "$1/.github/workflows/release.yml"
}
drop_verify_stamp() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Stamp the dispatched package versions")
end = text.index("      - name:", start + 1)
publish = text.index("  publish:")
if start > publish:
    raise SystemExit("verify stamp fixture matched the publish job")
open(path, "w", encoding="utf-8").write(text[:start] + text[end:])
PY
}
drop_verify_prepare() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Prepare release package metadata")
end = text.index("      - name:", start + 1)
publish = text.index("  publish:")
if start > publish:
    raise SystemExit("verify prepare fixture no longer matches generated output")
open(path, "w", encoding="utf-8").write(text[:start] + text[end:])
PY
}
enable_stamp_lifecycle_scripts() {
  sed -i 's/ --ignore-scripts//g' "$1/.github/workflows/release.yml"
}
reject_same_version_stamp() {
  sed -i 's/ --allow-same-version//g' "$1/.github/workflows/release.yml"
}
expose_private_token_to_verification_suite() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Run the release verification suite")
needle = "          NODE_AUTH_TOKEN: ''\n"
token_index = text.find(needle, start)
publish_index = text.find("  publish:", start)
if token_index < 0 or (publish_index >= 0 and token_index > publish_index):
    raise SystemExit("verification credential fixture no longer matches generated output")
replacement = needle.replace(
    "NODE_AUTH_TOKEN: ''", "NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}"
)
text = text[:token_index] + replacement + text[token_index + len(needle):]
open(path, "w", encoding="utf-8").write(text)
PY
}
append_credentialed_install_command() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
command = "          npm ci --ignore-scripts\n"
if text.count(command) != 1:
    raise SystemExit("credentialed install command fixture no longer matches generated output")
open(path, "w", encoding="utf-8").write(
    text.replace(command, command + "          node ./scripts/leak-token.js\n", 1)
)
PY
}
remove_credentialed_install_npmrc_guard() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
guard = "\n".join((
    '          workspace_root="$(git rev-parse --show-toplevel)"',
    '          if [ -e "$workspace_root/.npmrc" ] || [ -L "$workspace_root/.npmrc" ]; then',
    '            echo "::error::repository-controlled .npmrc is not allowed during credentialed release installation"',
    "            exit 1",
    "          fi",
)) + "\n"
if text.count(guard) != 1:
    raise SystemExit("credentialed npm configuration guard fixture no longer matches generated output")
open(path, "w", encoding="utf-8").write(text.replace(guard, "", 1))
PY
}
add_install_working_directory() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Install dependencies\n")
condition = "        if: steps.release-version.outputs.selected == 'true'\n"
position = text.find(condition, start)
if position < 0:
    raise SystemExit("credentialed install condition fixture no longer matches generated output")
position += len(condition)
text = text[:position] + "        working-directory: packages\n" + text[position:]
open(path, "w", encoding="utf-8").write(text)
PY
}
add_escaped_defaults_explicit_key() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys
import yaml

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
anchor = "  verify:\n"
mapping = (
    '    ? "def\\u0061ults"\n'
    "    : {run: {working-directory: contracts/container-deployment-cli}}\n"
)
if text.count(anchor) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
mutated = text.replace(anchor, anchor + mapping, 1)
yaml.safe_load(mutated)
open(path, "w", encoding="utf-8").write(mutated)
PY
}
add_multiline_escaped_defaults_explicit_key() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys
import yaml

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
anchor = "  verify:\n"
mapping = (
    "    ?\n"
    '      "def\\u0061ults"\n'
    "    : {run: {working-directory: contracts/container-deployment-cli}}\n"
)
if text.count(anchor) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
mutated = text.replace(anchor, anchor + mapping, 1)
yaml.safe_load(mutated)
open(path, "w", encoding="utf-8").write(mutated)
PY
}
shadow_stamped_version_header() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
warning = """# The verification suite runs after package.json has been stamped to the
# dispatched version. Its expected version must be read dynamically from
# package.json; never assert a hardcoded version literal. This order is
# intentional: the suite verifies the exact package metadata that will ship.
"""
if text.count(warning) != 1:
    raise SystemExit("stamped-version header fixture no longer matches generated output")
open(path, "w", encoding="utf-8").write(text.replace(warning, "", 1) + "\n" + warning)
PY
}
noop_stamped_version_diagnostic() {
  sed -i 's|echo "::error::Release verification failed against stamped dispatch version|: "::error::Release verification failed against stamped dispatch version|' \
    "$1/.github/workflows/release.yml"
}
expose_private_token_to_unrelated_step() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Stamp the dispatched package versions")
end = text.index("      - name:", start + 1)
step = text[start:end]
needle = "          PACKAGE_VERSION: ${{ steps.release-version.outputs.package-version }}\n"
if needle not in step:
    raise SystemExit("stamp step fixture no longer matches generated output")
step = step.replace(
    needle,
    needle + "          NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}\n",
    1,
)
open(path, "w", encoding="utf-8").write(text[:start] + step + text[end:])
PY
}
drift_verify_stamp_package_directories() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Stamp the dispatched package versions")
end = text.index("      - name:", start + 1)
publish = text.index("  publish:")
if start > publish:
    raise SystemExit("stamp fixture matched the publish job")
step = text[start:end]
if step.count("package_dirs=(.)") != 1:
    raise SystemExit("stamp fixture no longer has its default package directory")
step = step.replace("package_dirs=(.)", "package_dirs=(compat)", 1)
open(path, "w", encoding="utf-8").write(text[:start] + step + text[end:])
PY
}
drift_generator_provenance_only() {
  python3 - "$1/.github/workflows/release.yml" "$2" <<'PY'
import sys

path, package_directory_options = sys.argv[1:]
text = open(path, encoding="utf-8").read()
prefix = "# Generated by verJSON/.github scripts/gen-changelog-caller.sh "
lines = text.splitlines(keepends=True)
matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
if len(matches) != 1:
    raise SystemExit("release fixture no longer has one generator provenance header")
index = matches[0]
lines[index] = lines[index].rstrip("\n") + " " + package_directory_options + "\n"
open(path, "w", encoding="utf-8").write("".join(lines))
PY
}
drift_verify_stamp_step() {
  python3 - "$1/.github/workflows/release.yml" "$2" <<'PY'
import sys

path, mutation = sys.argv[1:]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Stamp the dispatched package versions")
end = text.index("      - name:", start + 1)
step = text[start:end]
if mutation == "command":
    needle = "          package_dirs=(.)\n"
    replacement = needle + "          echo unapproved version-stamp command\n"
elif mutation == "condition":
    needle = "        if: steps.release-version.outputs.selected == 'true'\n"
    replacement = "        if: always()\n"
elif mutation == "environment":
    needle = "          NODE_AUTH_TOKEN: ''\n"
    replacement = needle + "          EXTRA: unapproved\n"
else:
    raise SystemExit(f"unknown stamp mutation: {mutation}")
if step.count(needle) != 1:
    raise SystemExit("stamp fixture no longer has the expected mutation point")
step = step.replace(needle, replacement, 1)
open(path, "w", encoding="utf-8").write(text[:start] + step + text[end:])
PY
}
add_push_trigger() {
  sed -i 's|^on:$|on:\n  push:\n    branches: [main]|' "$1/.github/workflows/release.yml"
}
unpin_release_ref() {
  sed -i "s|changelog-release.yml@$sha|changelog-release.yml@main|" \
    "$1/.github/workflows/release.yml"
}
drift_release_contract_ref() {
  sed -i "s|contract_ref: $sha|contract_ref: 0000000000000000000000000000000000000000|" \
    "$1/.github/workflows/release.yml"
}
expose_node_version_to_renovate() {
  sed -i "0,/node-version:/s/node-version:.*/node-version: '24'/" \
    "$1/.github/workflows/release.yml"
}
strip_release_provenance() {
  sed -i '/gen-changelog-caller.sh release-node/d' "$1/.github/workflows/release.yml"
}
# The trigger surface, written the ways a line-oriented guard cannot see. Flow
# style never matches a `^on:$` anchor, and workflow_call/release/workflow_run
# are absent from any blocklist that was written by listing what came to mind.
add_flow_style_push_trigger() {
  # Line-oriented on purpose. A regex over the whole file (`(?s)`) swallows
  # everything after `on:` and produces a mutant that is rejected for having no
  # release call at all — a case that looks like it passes and proves nothing.
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
lines = open(path).read().splitlines(True)
out = []
index = 0
while index < len(lines):
    line = lines[index]
    if line.rstrip() == "on:":
        out.append(
            "on: {workflow_dispatch: {inputs: {version: {required: true,"
            " type: string}}}, push: {branches: [main]}}\n"
        )
        index += 1
        while index < len(lines) and (
            not lines[index].strip() or lines[index][:1] in " \t"
        ):
            index += 1
        continue
    out.append(line)
    index += 1
open(path, "w").write("".join(out))
PY
}
duplicate_equivalent_trigger() {
  local spelling="$2"
  printf '\n%s: {push: {branches: [main]}}\n' "$spelling" \
    >>"$1/.github/workflows/release.yml"
}
add_trigger_alias() {
  sed -i 's|^on:$|on: *release_events|' "$1/.github/workflows/release.yml"
}
add_trigger_merge_key() {
  sed -i 's|^  workflow_dispatch:$|  <<: *release_events\n  workflow_dispatch:|' \
    "$1/.github/workflows/release.yml"
}
duplicate_dispatch_trigger() {
  sed -i 's|^  workflow_dispatch:$|  "workflow_dispatch":\n  workflow_dispatch:|' \
    "$1/.github/workflows/release.yml"
}
alias_dispatch_schema() {
  sed -i 's|^  workflow_dispatch:$|  workflow_dispatch: *release_inputs|' \
    "$1/.github/workflows/release.yml"
}
anchor_dispatch_schema() {
  sed -i 's|^  workflow_dispatch:$|  workflow_dispatch: \&release_inputs|' \
    "$1/.github/workflows/release.yml"
}
flow_dispatch_schema() {
  sed -i 's|^  workflow_dispatch:$|  workflow_dispatch: {inputs: {}}|' \
    "$1/.github/workflows/release.yml"
}
tag_dispatch_schema() {
  sed -i 's|^  workflow_dispatch:$|  workflow_dispatch: !reviewed {}|' \
    "$1/.github/workflows/release.yml"
}
duplicate_nested_input_key() {
  sed -i '0,/^        required: false$/s//        required: false\n        required: true/' \
    "$1/.github/workflows/release.yml"
}
add_unknown_dispatch_input() {
  sed -i '0,/^      version:$/s//      attacker:\n        required: false\n        type: string\n      version:/' \
    "$1/.github/workflows/release.yml"
}
change_dispatch_input_shape() {
  sed -i "0,/^        default: ''$/s//        default: v/" \
    "$1/.github/workflows/release.yml"
}
add_yaml_directive() {
  sed -i '1i%YAML 1.1' "$1/.github/workflows/release.yml"
}
add_second_yaml_document() {
  printf '\n---\non: {push: {}}\n' >>"$1/.github/workflows/release.yml"
}
malform_nested_trigger_indent() {
  sed -i 's|^    inputs:$|     inputs:|' "$1/.github/workflows/release.yml"
}
add_trigger_scalar_comment() {
  sed -i "0,/^        default: ''$/s//        default: '' # false/" \
    "$1/.github/workflows/release.yml"
}
malform_trigger_mapping() {
  sed -i 's|^on:$|on|' "$1/.github/workflows/release.yml"
}
add_workflow_call_trigger() {
  sed -i 's|^on:$|on:\n  workflow_call:|' "$1/.github/workflows/release.yml"
}
add_release_trigger() {
  sed -i 's|^on:$|on:\n  release:\n    types: [published]|' "$1/.github/workflows/release.yml"
}
# The same credential, inherited rather than written on the install step, which
# is where a step-scoped guard stops looking.
install_token_from_job_env() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys
path = sys.argv[1]
out = []
for line in open(path):
    if "NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}" in line:
        continue
    out.append(line)
    if line.startswith("  verify:"):
        out.append("    env:\n      NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n")
open(path, "w").write("".join(out))
PY
}
expose_github_token_to_verify_job() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = "  verify:\n"
if text.count(needle) != 1:
    raise SystemExit("verification job fixture no longer has one canonical job key")
text = text.replace(
    needle,
    needle + "    env:\n      GITHUB_TOKEN: ${{ github.token }}\n",
    1,
)
open(path, "w", encoding="utf-8").write(text)
PY
}
expose_wrapped_package_token_to_verification_suite() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Run the release verification suite")
needle = "          NODE_AUTH_TOKEN: ''\n"
token_index = text.find(needle, start)
publish_index = text.find("  publish:", start)
if token_index < 0 or (publish_index >= 0 and token_index > publish_index):
    raise SystemExit("wrapped verification credential fixture no longer matches generated output")
replacement = needle + "          PRIVATE_REGISTRY_CREDENTIAL: ${{ format('{{{0}}}', secrets.NODE_AUTH_TOKEN) }}\n"
text = text[:token_index] + replacement + text[token_index + len(needle):]
open(path, "w", encoding="utf-8").write(text)
PY
}
expose_folded_wrapped_package_token_to_verification_suite() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Run the release verification suite")
needle = "          NODE_AUTH_TOKEN: ''\n"
token_index = text.find(needle, start)
publish_index = text.find("  publish:", start)
if token_index < 0 or (publish_index >= 0 and token_index > publish_index):
    raise SystemExit("folded verification credential fixture no longer matches generated output")
replacement = needle + """          PRIVATE_REGISTRY_CREDENTIAL: >-
            ${{ format(
              '{{{0}}}', secrets.NODE_AUTH_TOKEN
            ) }}
"""
text = text[:token_index] + replacement + text[token_index + len(needle):]
open(path, "w", encoding="utf-8").write(text)
PY
}
expose_serialized_secrets_to_verification_suite() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Run the release verification suite")
needle = "          NODE_AUTH_TOKEN: ''\n"
token_index = text.find(needle, start)
publish_index = text.find("  publish:", start)
if token_index < 0 or (publish_index >= 0 and token_index > publish_index):
    raise SystemExit("serialized verification credential fixture no longer matches generated output")
replacement = needle + "          PRIVATE_REGISTRY_CREDENTIALS: ${{ toJSON(secrets) }}\n"
text = text[:token_index] + replacement + text[token_index + len(needle):]
open(path, "w", encoding="utf-8").write(text)
PY
}
expose_serialized_github_context_to_verification_suite() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Run the release verification suite")
needle = "          NODE_AUTH_TOKEN: ''\n"
token_index = text.find(needle, start)
publish_index = text.find("  publish:", start)
if token_index < 0 or (publish_index >= 0 and token_index > publish_index):
    raise SystemExit("serialized GitHub context fixture no longer matches generated output")
replacement = needle + "          PRIVATE_GITHUB_CONTEXT: ${{ toJSON(github) }}\n"
text = text[:token_index] + replacement + text[token_index + len(needle):]
open(path, "w", encoding="utf-8").write(text)
PY
}
expose_indexed_github_token_to_verification_suite() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Run the release verification suite")
needle = "          NODE_AUTH_TOKEN: ''\n"
token_index = text.find(needle, start)
publish_index = text.find("  publish:", start)
if token_index < 0 or (publish_index >= 0 and token_index > publish_index):
    raise SystemExit("indexed GitHub token fixture no longer matches generated output")
replacement = needle + "          PRIVATE_GITHUB_TOKEN: ${{ github['token'] }}\n"
text = text[:token_index] + replacement + text[token_index + len(needle):]
open(path, "w", encoding="utf-8").write(text)
PY
}
expose_release_state_token_through_yaml_alias() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
state_start = text.index("      - name: Resolve restart-safe release state")
state_env = text.index("        env:\n", state_start)
text = text[:state_env] + text[state_env:].replace(
    "        env:\n", "        env: &release_state_credentials\n", 1
)
verify_start = text.index("      - name: Run the release verification suite")
needle = "          NODE_AUTH_TOKEN: ''\n"
token_index = text.find(needle, verify_start)
publish_index = text.find("  publish:", verify_start)
if token_index < 0 or (publish_index >= 0 and token_index > publish_index):
    raise SystemExit("verification YAML alias fixture no longer matches generated output")
replacement = "          <<: *release_state_credentials\n" + needle
text = text[:token_index] + replacement + text[token_index + len(needle):]
open(path, "w", encoding="utf-8").write(text)
PY
}
persist_release_token_in_git_config_file() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
state_start = text.index("      - name: Resolve restart-safe release state")
needle = "        run: |\n"
run_start = text.find(needle, state_start)
next_step = text.find("      - name:", state_start + len(needle))
if run_start < 0 or (next_step >= 0 and run_start > next_step):
    raise SystemExit("release state run fixture no longer matches generated output")
insert_at = run_start + len(needle)
text = text[:insert_at] + "          printf '%s\\n' \"$GITHUB_TOKEN\" >> \"$GITHUB_WORKSPACE/.git/config\"\n" + text[insert_at:]
open(path, "w", encoding="utf-8").write(text)
PY
}
persist_release_token_to_github_env() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
state_start = text.index("      - name: Resolve restart-safe release state")
needle = "        run: |\n"
run_start = text.find(needle, state_start)
next_step = text.find("      - name:", state_start + len(needle))
if run_start < 0 or (next_step >= 0 and run_start > next_step):
    raise SystemExit("release state run fixture no longer matches generated output")
insert_at = run_start + len(needle)
text = text[:insert_at] + "          printf 'LEAKED_TOKEN=%s\\n' \"$GITHUB_TOKEN\" >> \"$GITHUB_ENV\"\n" + text[insert_at:]
open(path, "w", encoding="utf-8").write(text)
PY
}
configure_token_step_bash_env() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
state_start = text.index("      - name: Resolve restart-safe release state")
token_line = "          GITHUB_TOKEN: ${{ github.token }}\n"
token_index = text.find(token_line, state_start)
next_step = text.find("      - name:", state_start + len(token_line))
if token_index < 0 or (next_step >= 0 and token_index > next_step):
    raise SystemExit("release state environment fixture no longer matches generated output")
insert_at = token_index + len(token_line)
text = text[:insert_at] + "          \"BASH_ENV\": .bash_env\n" + text[insert_at:]
open(path, "w", encoding="utf-8").write(text)
PY
}
configure_token_step_custom_shell() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
state_start = text.index("      - name: Resolve restart-safe release state")
run_line = "        run: |\n"
run_index = text.find(run_line, state_start)
next_step = text.find("      - name:", state_start + len(run_line))
if run_index < 0 or (next_step >= 0 and run_index > next_step):
    raise SystemExit("release state shell fixture no longer matches generated output")
text = text[:run_index] + "        shell: bash --noprofile {0}\n" + text[run_index:]
open(path, "w", encoding="utf-8").write(text)
PY
}
configure_verify_job_default_shell() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = "  verify:\n"
if text.count(needle) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
text = text.replace(
    needle,
    needle + "    \"defaults\":\n      run:\n        shell: bash --noprofile {0}\n",
    1,
)
open(path, "w", encoding="utf-8").write(text)
PY
}
configure_workflow_shelopts() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
marker = "  verify:" + chr(10)
if text.count(marker) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
addition = chr(10).join(("    env:", '      "SHELLOPTS": xtrace', ""))
text = text.replace(marker, marker + addition, 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
configure_workflow_loader_env() {
  python3 - "$1/.github/workflows/release.yml" "$2" <<'PY'
import sys

path, key = sys.argv[1:]
text = open(path, encoding="utf-8").read()
marker = "  verify:" + chr(10)
if text.count(marker) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
addition = chr(10).join(("    env:", f'      "{key}": /tmp/credential-boundary-hostile.so', ""))
text = text.replace(marker, marker + addition, 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
configure_workflow_git_trace() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
marker = "  verify:" + chr(10)
if text.count(marker) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
addition = chr(10).join((
    "    env:",
    "      GIT_TRACE_CURL: '1'",
    "      GIT_CURL_VERBOSE: '1'",
    "      GIT_TRACE_REDACT: '0'",
    "      GIT_TRACE2: '1'",
    "      GIT_TRACE2_ENV_VARS: GIT_CONFIG_VALUE_0",
    "",
))
text = text.replace(marker, marker + addition, 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
configure_workflow_git_config_sources() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
marker = "  verify:" + chr(10)
if text.count(marker) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
addition = chr(10).join((
    "    env:",
    "      GIT_CONFIG_GLOBAL: /tmp/attacker.gitconfig",
    "      GIT_CONFIG_SYSTEM: /tmp/attacker-system.gitconfig",
    "",
))
text = text.replace(marker, marker + addition, 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
configure_workflow_config_parameters() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
marker = "  verify:" + chr(10)
if text.count(marker) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
addition = chr(10).join((
    "    env:",
    "      GIT_CONFIG_PARAMETERS: --global=credential.helper=",
    "",
))
text = text.replace(marker, marker + addition, 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
make_lifecycle_step_print_instead_of_rebuild() {
  sed -i 's/^        run: npm rebuild$/        run: echo npm rebuild/' \
    "$1/.github/workflows/release.yml"
}
grant_verify_job_write_permissions() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import re
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").readlines()
start = next(index for index, line in enumerate(lines) if line.rstrip("\n") == "  verify:")
end = next(
    (index for index in range(start + 1, len(lines)) if re.match(r"^  [A-Za-z0-9_.-]+:\s*$", lines[index])),
    len(lines),
)
section = "".join(lines[start:end])
needle = "      contents: read\n"
if section.count(needle) != 1:
    raise SystemExit("verify permissions fixture no longer has one contents-read grant")
section = section.replace(needle, "      contents: write\n", 1)
open(path, "w", encoding="utf-8").write("".join(lines[:start]) + section + "".join(lines[end:]))
PY
}
allow_verify_job_to_continue_on_error() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = "  verify:\n"
if text.count(needle) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
text = text.replace(needle, needle + "    continue-on-error: true\n", 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
allow_verification_step_to_continue_on_error() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = "      - name: Run the release verification suite\n"
if text.count(needle) != 1:
    raise SystemExit("verification step fixture no longer matches generated output")
text = text.replace(needle, needle + "        continue-on-error: true\n", 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
skip_release_verification() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Run the release verification suite\n")
needle = "        if: steps.release-version.outputs.selected == 'true'\n"
index = text.find(needle, start)
if index < 0:
    raise SystemExit("verification step condition fixture no longer matches generated output")
text = text[:index] + "        if: false\n" + text[index + len(needle):]
open(path, "w", encoding="utf-8").write(text)
PY
}
replace_release_verification_with_noop() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
step = text.index("      - name: Run the release verification suite\n")
run = text.index("        run: |\n", step)
body = run + len("        run: |\n")
snapshot = text.index("\n  snapshot:\n", body)
text = text[:body] + '          echo "verification bypassed"\n' + text[snapshot:]
open(path, "w", encoding="utf-8").write(text)
PY
}
remove_release_verification_failure_handler() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").readlines()
starts = [i for i, line in enumerate(lines) if line.strip() == 'if [ "$verification_status" -ne 0 ]; then']
if len(starts) != 1:
    raise SystemExit(f"expected one verification failure handler, found {len(starts)}")
start = starts[0]
indent = lines[start][:len(lines[start]) - len(lines[start].lstrip())]
ends = [i for i in range(start + 1, len(lines)) if lines[i] == indent + "fi\n"]
if len(ends) != 1:
    raise SystemExit(f"expected one matching verification failure handler end, found {len(ends)}")
del lines[start:ends[0] + 1]
open(path, "w", encoding="utf-8").writelines(lines)
PY
}
quote_checkout_reference() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Check out the tree that will be released\n")
uses = text.index("        uses: actions/checkout@", start)
end = text.index("\n", uses)
value = text[uses + len("        uses: "):end]
ref, separator, comment = value.partition(" #")
text = text[:uses] + f'        uses: "{ref}"{separator}{comment}' + text[end:]
open(path, "w", encoding="utf-8").write(text)
PY
}
drift_canonical_selection_contract_checkout_ref() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
step = text.index("- name: Check out the canonical selection contract\n")
ref = text.index("ref:", step)
line_start = text.rfind("\n", 0, ref) + 1
line_end = text.index("\n", ref)
if not text[line_start:line_end].lstrip().startswith("ref:"):
    raise SystemExit("canonical contract checkout ref fixture no longer matches generated output")
text = text[:ref] + "main" + text[line_end:]
open(path, "w", encoding="utf-8").write(text)
PY
}
inline_checkout_reference() {
  python3 - "$1/.github/workflows/release.yml" "$2" <<'PY'
import re
import sys

path, credentials = sys.argv[1:]
text = open(path, encoding="utf-8").read()
start = text.index("      - name: Check out the tree that will be released\n")
end = text.index("\n      - name:", start + 1)
step = text[start:end]
uses_prefix = "        uses: actions/checkout@"
uses_start = step.index(uses_prefix)
uses_end = step.index("\n", uses_start)
value = step[uses_start + len("        uses: "):uses_end]
ref, separator, comment = value.partition(" #")
step = step[:uses_start] + step[uses_end + 1:]
step = step.replace(
    "      - name: Check out the tree that will be released\n",
    f"      - uses: '{ref}'{separator}{comment}\n        name: Check out the tree that will be released\n",
    1,
)
if credentials == "missing":
    step, removed = re.subn(r"(?m)^[ ]*persist-credentials: false[ ]*(?:\n|$)", "", step)
    if removed != 1:
        raise SystemExit(f"expected one checkout credential setting, removed {removed}")

text = text[:start] + step + text[end:]
open(path, "w", encoding="utf-8").write(text)
PY
}
inline_checkout_with_persist_credentials() {
  inline_checkout_reference "$1" present
}
inline_checkout_without_persist_credentials() {
  inline_checkout_reference "$1" missing
}
# Keying the checks on one filename let any other name collect none of them.
uppercase_checkout_without_disabled_credentials() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").readlines()
uses_index = next(i for i, line in enumerate(lines) if "uses: actions/checkout@" in line)
indent = len(lines[uses_index]) - len(lines[uses_index].lstrip())
lines[uses_index] = lines[uses_index].replace("actions/checkout@", "ACTIONS/CHECKOUT@", 1)
end = next(
    (i for i in range(uses_index + 1, len(lines))
     if lines[i].lstrip().startswith("-") and len(lines[i]) - len(lines[i].lstrip()) < indent),
    len(lines),
)
credentials = [i for i in range(uses_index + 1, end) if lines[i].strip() == "persist-credentials: false"]
if len(credentials) != 1:
    raise SystemExit(f"expected one checkout credential setting, found {len(credentials)}")
del lines[credentials[0]]
open(path, "w", encoding="utf-8").writelines(lines)
PY
}
folded_checkout_reference_without_disabled_credentials() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").readlines()
uses_index = next(i for i, line in enumerate(lines) if "uses: actions/checkout@" in line)
indent = len(lines[uses_index]) - len(lines[uses_index].lstrip())
reference = lines[uses_index].split("uses:", 1)[1].partition(" #")[0].strip()
lines[uses_index] = " " * indent + "uses: >-\n"
lines.insert(uses_index + 1, " " * (indent + 2) + reference + "\n")
end = next(
    (i for i in range(uses_index + 2, len(lines))
     if lines[i].lstrip().startswith("-") and len(lines[i]) - len(lines[i].lstrip()) < indent),
    len(lines),
)
credentials = [i for i in range(uses_index + 2, end) if lines[i].strip() == "persist-credentials: false"]
if len(credentials) != 1:
    raise SystemExit(f"expected one checkout credential setting, found {len(credentials)}")
del lines[credentials[0]]
open(path, "w", encoding="utf-8").writelines(lines)
PY
}
add_flow_style_checkout_step() {
  cat >> "$1/.github/workflows/release.yml" <<'YAML'
  injected-checkout:
    runs-on: ubuntu-latest
    steps: [{uses: actions/checkout@v4}]
YAML
}
reindent_release_job_fields() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").readlines()
jobs = [i for i, line in enumerate(lines) if line.rstrip("\n") == "jobs:"]
if len(jobs) != 1:
    raise SystemExit(f"expected one top-level jobs mapping, found {len(jobs)}")
for index in range(jobs[0] + 1, len(lines)):
    line = lines[index]
    if line.strip() and not line[0].isspace():
        break
    if line.strip() and len(line) - len(line.lstrip()) > 2:
        lines[index] = "  " + line
open(path, "w", encoding="utf-8").writelines(lines)
PY
}
use_mutable_path_for_release_verification() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").readlines()
expected = 'PATH="$RELEASE_VERIFICATION_PATH" ' + chr(92)
matches = [index for index, line in enumerate(lines) if line.strip() == expected]
if len(matches) != 1:
    raise SystemExit("expected one verifier PATH sourced captured output")
index = matches[0]
lines[index] = lines[index].replace('PATH="$RELEASE_VERIFICATION_PATH"', 'PATH="$PATH"', 1)
open(path, "w", encoding="utf-8").writelines(lines)
PY
}
assert_mutable_verification_path_rejected() {
  local source label slug dir
  source="$1"
  label="$2"
  slug="$3"
  if ! changelog_caller_case_selected "$slug"; then
    caller_assertions_suspended=1
    return 0
  fi
  caller_assertions_suspended=0
  dir="$tmproot/$slug"
  cp -a "$source" "$dir"
  use_mutable_path_for_release_verification "$dir"
  if caller_case_nested=1 run_adopter "$dir"; then
    fail "emitted suite accepted a mutable verification PATH in $label mode"
  elif grep -qF 'does not match the approved release verification script (#1717)' "$tmproot/run.out"; then
    pass "the generated suite rejects mutable verification PATH in $label mode"
  else
    fail "mutable verification PATH in $label mode was rejected for another reason: $(tail -2 "$tmproot/run.out")"
  fi
}
assert_release_path_mutation_rejected() {
  local source label slug mutator expected dir
  source="$1"
  label="$2"
  slug="$3"
  mutator="$4"
  expected="$5"
  if ! changelog_caller_case_selected "$slug"; then
    caller_assertions_suspended=1
    return 0
  fi
  caller_assertions_suspended=0
  dir="$tmproot/$slug"
  cp -a "$source" "$dir"
  if ! "$mutator" "$dir"; then
    fail "$label path mutation fixture did not apply"
  elif caller_case_nested=1 run_adopter "$dir"; then
    fail "the generated suite accepted $label path mutation"
  elif grep -qF "$expected" "$tmproot/run.out"; then
    pass "the generated suite rejects $label path mutation"
  else
    fail "$label path mutation was rejected for another reason: $(tail -2 "$tmproot/run.out")"
  fi
}
add_capture_path_override() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
step = text.index("      - name: Capture trusted release verification runtime\n")
shell = text.index("        shell:", step)
text = text[:shell] + "        env:\n          PATH: /tmp/attacker-bin\n" + text[shell:]
open(path, "w", encoding="utf-8").write(text)
PY
}
add_verify_job_path_override() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = "  verify:\n"
if text.count(needle) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
text = text.replace(
    needle,
    needle + "    env:\n      PATH: /tmp/attacker-bin\n",
    1,
)
open(path, "w", encoding="utf-8").write(text)
PY
}
add_escaped_flow_path_environment() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = "  verify:\n"
if text.count(needle) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
escaped_path_key = "P" + chr(92) + "u0041TH"
environment = '    env: {"' + escaped_path_key + '": "/tmp/attacker-bin"}\n'
text = text.replace(needle, needle + environment, 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
add_tagged_flow_bash_env() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = "  verify:\n"
if text.count(needle) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
escaped_key = "B" + chr(92) + "u0041SH_ENV"
environment = '    env: !!map {"' + escaped_key + '": ".github/evil.sh"}\n'
text = text.replace(needle, needle + environment, 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
add_explicit_bash_env_key() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = "  verify:\n"
if text.count(needle) != 1:
    raise SystemExit("verify job fixture no longer matches generated output")
environment = '    env:\n      ? "BASH_ENV"\n      : ".github/evil.sh"\n'
text = text.replace(needle, needle + environment, 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
add_pre_capture_environment_writer() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = "      - uses: actions/setup-node@"
if text.count(needle) != 1:
    raise SystemExit("setup-node step fixture no longer matches generated output")
step = (
    '      - name: Inject Bash startup script through runner environment\n'
    '        run: echo "BASH_ENV=.github/evil.sh" >> "$GITHUB_ENV"\n'
)
text = text.replace(needle, step + needle, 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
add_in_place_precredential_environment_writer() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
step = text.index("      - name: Resolve the release selection and version\n")
needle = "        run: |\n          set -euo pipefail\n"
run_body = text.index(needle, step)
replacement = needle + '          echo "BASH_ENV=.github/evil.sh" >> "$GITHUB_ENV"\n'
text = text[:run_body] + text[run_body:].replace(needle, replacement, 1)
open(path, "w", encoding="utf-8").write(text)
PY
}
add_precredential_pythonpath() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
step = text.index("      - name: Resolve the release selection and version\n")
needle = "        env:\n"
environment = text.index(needle, step)
text = text[:environment] + text[environment:].replace(
    needle, needle + "          PYTHONPATH: .github\n", 1
)
open(path, "w", encoding="utf-8").write(text)
PY
}
add_install_node_options() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
step = text.index("      - name: Install dependencies\n")
needle = "        env:\n"
environment = text.index(needle, step)
text = text[:environment] + text[environment:].replace(
    needle,
    needle + "          NODE_OPTIONS: --require=./.github/exfil.cjs\n",
    1,
)
open(path, "w", encoding="utf-8").write(text)
PY
}
move_capture_after_lifecycle_scripts() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").readlines()
capture = next(i for i, line in enumerate(lines)
               if line == "      - name: Capture trusted release verification runtime\n")
capture_end = next(i for i in range(capture + 1, len(lines))
                   if lines[i].startswith("      - "))
block = lines[capture:capture_end]
del lines[capture:capture_end]
lifecycle = next(i for i, line in enumerate(lines)
                 if line == "      - name: Run dependency lifecycle scripts without credentials\n")
target = next(i for i in range(lifecycle + 1, len(lines))
              if lines[i].startswith("      - "))
lines[target:target] = block
open(path, "w", encoding="utf-8").writelines(lines)
PY
}
replace_verification_script_shell() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
step = text.index("      - name: Run the release verification suite\n")
env = text.index("        env:\n", step) + len("        env:\n")
text = text[:env] + "          npm_config_script_shell: /bin/true\n" + text[env:]
open(path, "w", encoding="utf-8").write(text)
PY
}
replace_verification_bash_env() {
  python3 - "$1/.github/workflows/release.yml" <<'PY'
import re
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
step = text.index("      - name: Run the release verification suite\n")
boundaries = [position for marker in ("\n      - ",) if (position := text.find(marker, step + 1)) >= 0]
next_job = re.search(r"(?m)^  [A-Za-z0-9_.-]+:", text[step + 1:])
if next_job:
    boundaries.append(step + 1 + next_job.start())
end = min(boundaries, default=len(text))
verification_step = text[step:end]
needle = "          BASH_ENV: ''\n"
if verification_step.count(needle) != 1:
    raise SystemExit("release verification BASH_ENV setting fixture is stale")
verification_step = verification_step.replace(needle, "          BASH_ENV: /tmp/release-bypass\n", 1)
text = text[:step] + verification_step + text[end:]
open(path, "w", encoding="utf-8").write(text)
PY
}
rename_release_caller() {
  mv "$1/.github/workflows/release.yml" "$1/.github/workflows/publish-package.yml"
  rm "$1/.github/workflows/release-propose.yml"
  sed -i '/^    needs: verify$/d' "$1/.github/workflows/publish-package.yml"
}

expect_rejection "a renderer pinned to a different commit" break_pin
# .github/CODEOWNERS is a required member of the adopter set (ADR 0210, #1272):
# absent, hand-edited, symlinked, or shadowed by a competing location all fail closed.
drop_codeowners() { rm -f "$1/.github/CODEOWNERS"; }
drift_codeowners() { printf '* @Verjson/someone-else\n' >>"$1/.github/CODEOWNERS"; }
shadow_codeowners() { cp "$1/.github/CODEOWNERS" "$1/CODEOWNERS"; }
symlink_codeowners() { mv "$1/.github/CODEOWNERS" "$1/CODEOWNERS.real" && ln -s ../CODEOWNERS.real "$1/.github/CODEOWNERS"; }
expect_rejection "an adopter without .github/CODEOWNERS" drop_codeowners
expect_rejection "a hand-edited .github/CODEOWNERS" drift_codeowners
expect_rejection "a competing root CODEOWNERS beside the canonical file" shadow_codeowners
expect_rejection "a symlinked .github/CODEOWNERS" symlink_codeowners
symlink_github_dir() { mv "$1/.github" "$1/.github.real" && ln -s .github.real "$1/.github"; }
docs_shadow_codeowners() { mkdir -p "$1/docs" && cp "$1/.github/CODEOWNERS" "$1/docs/CODEOWNERS"; }
directory_codeowners() { rm -f "$1/.github/CODEOWNERS" && mkdir "$1/.github/CODEOWNERS"; }
expect_rejection "a symlinked .github directory in front of CODEOWNERS" symlink_github_dir
expect_rejection "a competing docs/CODEOWNERS beside the canonical file" docs_shadow_codeowners
expect_rejection "a directory where .github/CODEOWNERS must be a file" directory_codeowners
expect_rejection "a hand-written renderer that bypasses the contract" handwrite_renderer
expect_rejection "a .releaserc.json that reintroduces release-on-merge" add_releaserc
expect_rejection "a second authored running log in NEXT.md" add_authored_log
expect_rejection "a fragment whose filename is not canonical" uncanonical_fragment
expect_rejection "a non-executable renderer" strip_executable
expect_rejection "a release caller without RELEASE_APP_CLIENT_ID" drop_release_app_client_id
expect_rejection "a release caller without its release environment" drop_release_environment
expect_rejection "a release caller restoring ORG_ADMIN_TOKEN" restore_org_admin_token
expect_rejection "a release caller wiring GITHUB_TOKEN" wire_github_token

# Rejected for the stated reason, not incidentally. expect_rejection only asserts
# a non-zero exit, so without this the guard could rot while its case stays green.
# It reads the LAST run, so it has to sit immediately after the credential cases.
sync_last_rejection
grep -qE 'snapshot forwards credentials|release-app environment' "$tmproot/run.out" \
  && pass "the broad-token rejection names the dedicated release App remedy" \
  || fail "the last credential case failed for some other reason: $(tail -2 "$tmproot/run.out")"

expect_rejection "a snapshot caller granting GITHUB_TOKEN contents-write (#784)" grant_snapshot_github_token_write
expect_rejection "a snapshot job that verifies nothing first (#463, #464)" drop_snapshot_needs
expect_rejection "a snapshot job with no explicit runner (#465)" drop_snapshot_runner
expect_rejection "a resumed release that verifies the later dispatch tree instead of its tagged snapshot (#591)" drop_resume_snapshot_checkout
expect_rejection "an npm ci installing with GITHUB_TOKEN (#465)" install_with_github_token
expect_rejection "a verification job without package metadata preparation (#550)" drop_verify_prepare
expect_rejection "a verification suite with no dispatched version stamp (#519)" drop_verify_stamp
expect_rejection "version stamps that can run package lifecycle scripts (#519)" enable_stamp_lifecycle_scripts
expect_rejection "a first release whose scaffold version already matches the dispatch (#579)" reject_same_version_stamp
expect_rejection "a release verification suite receiving package credentials (#1712)" expose_private_token_to_verification_suite
expect_rejection "a credentialed install step with an extra command (#1712)" append_credentialed_install_command
sync_last_rejection
grep -qF 'runs an unexpected credentialed acquisition command (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects extra commands beside credentialed npm ci" \
  || fail "the generated suite rejected an extra credentialed command for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a credentialed install step without a repository npm configuration guard (#1717)" \
  remove_credentialed_install_npmrc_guard
sync_last_rejection
grep -qF 'does not reject repository-controlled npm configuration before credentialed install (#1717)' \
  "$tmproot/run.out" \
  && pass "the generated suite requires a repository npm configuration guard before credentialed install" \
  || fail "a missing credentialed npm configuration guard was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a credentialed install working-directory override (#1717)" \
  add_install_working_directory
sync_last_rejection
grep -qF 'does not pin credentialed install step inputs and working directory (#1717)' \
  "$tmproot/run.out" \
  && pass "the generated suite pins the credentialed install working directory" \
  || fail "a credentialed install working-directory override was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "canonical selection checkout uses an unpinned branch (#1717)" \
  drift_canonical_selection_contract_checkout_ref
sync_last_rejection
grep -qF 'does not check out the canonical selection contract at its pinned ref (#1717)' \
  "$tmproot/run.out" \
  && pass "the generated suite binds its canonical checkout to the contract ref" \
  || fail "a canonical selection checkout ref drift was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "an escaped YAML explicit key setting inherited working-directory defaults (#1717)" \
  add_escaped_defaults_explicit_key
sync_last_rejection
grep -qF 'uses unsupported explicit YAML mapping keys (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects explicit YAML mapping keys before environment checks" \
  || fail "an escaped explicit defaults key was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a multiline escaped YAML explicit key setting inherited working-directory defaults (#1717)" \
  add_multiline_escaped_defaults_explicit_key
sync_last_rejection
grep -qF 'uses unsupported explicit YAML mapping keys (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects multiline explicit YAML mapping keys" \
  || fail "a multiline escaped explicit defaults key was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a verification job inheriting GITHUB_TOKEN (#1712)" expose_github_token_to_verify_job
sync_last_rejection
grep -qF 'exposes a GitHub or package secret beyond approved acquisition and restart-safe state steps (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects Git credentials inherited by release verification" \
  || fail "the generated suite rejected an inherited Git token for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a verification suite receiving a wrapped package token (#1712)" expose_wrapped_package_token_to_verification_suite
sync_last_rejection
grep -qF 'exposes a GitHub or package secret beyond approved acquisition and restart-safe state steps (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects a wrapped package secret in verification" \
  || fail "the generated suite rejected a wrapped package token for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a verification suite receiving a folded wrapped package token (#1712)" expose_folded_wrapped_package_token_to_verification_suite
sync_last_rejection
grep -qF 'exposes a GitHub or package secret beyond approved acquisition and restart-safe state steps (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects a folded package secret in verification" \
  || fail "the generated suite rejected a folded wrapped package token for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a verification suite serializing the secrets context (#1712)" expose_serialized_secrets_to_verification_suite
sync_last_rejection
grep -qF 'exposes a GitHub or package secret beyond approved acquisition and restart-safe state steps (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects serialization of the secrets object in verification" \
  || fail "the generated suite rejected serialized secrets for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a verification suite serializing the GitHub context (#1712)" expose_serialized_github_context_to_verification_suite
sync_last_rejection
grep -qF 'exposes a GitHub or package secret beyond approved acquisition and restart-safe state steps (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects serialization of the GitHub context in verification" \
  || fail "the generated suite rejected serialized GitHub context for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a verification suite reading github['token'] (#1712)" expose_indexed_github_token_to_verification_suite
sync_last_rejection
grep -qF 'exposes a GitHub or package secret beyond approved acquisition and restart-safe state steps (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects indexed GitHub token access in verification" \
  || fail "the generated suite rejected indexed GitHub token for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a verification suite inheriting a GitHub token through YAML aliases (#1712)" expose_release_state_token_through_yaml_alias
sync_last_rejection
grep -qF 'uses YAML anchors or aliases in a release workflow (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects YAML aliases that merge release-state credentials into verification" \
  || fail "the generated suite rejected an aliased Git token for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a release state step writing its token directly into .git/config (#1712)" persist_release_token_in_git_config_file
sync_last_rejection
grep -qF 'does not match the approved restart-safe release-state script (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects unapproved credential-bearing release-state commands" \
  || fail "the generated suite rejected a modified release-state command for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a release state step forwarding its token through GITHUB_ENV (#1712)" persist_release_token_to_github_env
sync_last_rejection
grep -qF 'does not match the approved restart-safe release-state script (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects release-state token forwarding through runner command files" \
  || fail "the generated suite rejected a GITHUB_ENV mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a release state step loading repository code through quoted BASH_ENV (#1712)" configure_token_step_bash_env
sync_last_rejection
grep -qF 'does not restrict the restart-safe release-state environment (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects shell startup code in token environment" \
  || fail "the generated suite rejected BASH_ENV mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a release state step selecting custom shell (#1712)" configure_token_step_custom_shell
sync_last_rejection
grep -qF 'configures a custom shell or action in the credentialed release-state step (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects custom shells in credentialed workflows" \
  || fail "the generated suite rejected a custom shell mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a verify job setting custom shell through quoted defaults (#1712)" configure_verify_job_default_shell
sync_last_rejection
grep -qF 'configures run defaults (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects inherited custom shells in verify job" \
  || fail "the generated suite rejected a default-shell mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a workflow enabling Bash xtrace through quoted SHELLOPTS (#1712)" configure_workflow_shelopts
sync_last_rejection
grep -qF 'configures credential-sensitive environment outside the credentialed step (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects inherited Bash xtrace in token step" \
  || fail "the generated suite rejected SHELLOPTS mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a workflow preloading library in credentialed steps (#1712)" configure_workflow_loader_env LD_PRELOAD
sync_last_rejection
grep -qF 'configures credential-sensitive environment outside the credentialed step (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects inherited LD_PRELOAD in token step" \
  || fail "the generated suite rejected an LD_PRELOAD mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a workflow adding dynamic loader audit library (#1712)" configure_workflow_loader_env LD_AUDIT
sync_last_rejection
grep -qF 'configures credential-sensitive environment outside the credentialed step (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects inherited LD_AUDIT in token step" \
  || fail "the generated suite rejected an LD_AUDIT mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a workflow changing dynamic library search paths (#1712)" configure_workflow_loader_env LD_LIBRARY_PATH
sync_last_rejection
grep -qF 'inherits unapproved job-level environment in release verification (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects job-level LD_LIBRARY_PATH in release verification" \
  || fail "the generated suite rejected an LD_LIBRARY_PATH mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a workflow logging Git authorization values through curl and Trace2 (#1712)" configure_workflow_git_trace
sync_last_rejection
grep -qF 'configures credential-sensitive environment outside the credentialed step (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects Git curl and Trace2 environment logging in token step" \
  || fail "the generated suite rejected Git tracing mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a workflow redirecting Git remote helpers (#1712)" configure_workflow_loader_env GIT_EXEC_PATH
sync_last_rejection
grep -qF 'configures credential-sensitive environment outside the credentialed step (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects repository-controlled remote helper in token step" \
  || fail "the generated suite rejected a Git remote-helper mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a workflow selecting hostile Git config sources (#1712)" configure_workflow_git_config_sources
sync_last_rejection
grep -qF 'configures credential-sensitive environment outside the credentialed step (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects inherited system and global Git config paths" \
  || fail "the generated suite rejected Git config-source mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a workflow overriding Git config parameters (#1712)" configure_workflow_config_parameters
sync_last_rejection
grep -qF 'configures credential-sensitive environment outside the credentialed step (#1712)' "$tmproot/run.out" \
  && pass "the generated suite rejects inherited Git config parameters" \
  || fail "the generated suite rejected Git config-parameters mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a lifecycle step that only prints npm rebuild (#1712)" make_lifecycle_step_print_instead_of_rebuild
sync_last_rejection
grep -qF 'does not restore dependency lifecycle execution after acquisition (#1712)' "$tmproot/run.out" \
  && pass "the generated suite requires lifecycle command to execute exactly" \
  || fail "the generated suite rejected a lifecycle mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a release verification job granting contents-write (#1712)" grant_verify_job_write_permissions
sync_last_rejection
grep -qF 'requires verify-job permissions to be exactly contents: read (#1712)' "$tmproot/run.out" \
  && pass "the generated suite holds verification to read-only contents permission" \
  || fail "the generated suite rejected a write-permission mutation for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a release verification job that continues after failure (#1717)" allow_verify_job_to_continue_on_error
sync_last_rejection
grep -qF 'allows the release verification job to continue after failure (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects job-level continue-on-error for verification" \
  || fail "job-level continue-on-error was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a release verification step that continues after failure (#1717)" allow_verification_step_to_continue_on_error
sync_last_rejection
grep -qF 'allows the release verification step to continue after failure (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects step-level continue-on-error for verification" \
  || fail "step-level continue-on-error was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a release verification step skipped by its condition (#1717)" skip_release_verification
sync_last_rejection
grep -qF 'does not require a selected version before release verification (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects a skipped verification step" \
  || fail "a skipped verification step was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a release verification step replaced with a no-op (#1717)" replace_release_verification_with_noop
sync_last_rejection
grep -qF 'does not stamp the dispatched package version before the verification build or suite (#519)' "$tmproot/run.out" \
  && pass "the generated suite rejects a no-op that skips the version stamp" \
  || fail "a no-op verification script was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "release verification failure handler removed (#1717)" \
  remove_release_verification_failure_handler
sync_last_rejection
grep -qF 'does not match the approved release verification script (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects removal of the verification failure handler" \
  || fail "a removed verification failure handler was rejected for another reason: $(tail -2 "$tmproot/run.out")"
quoted_checkout="$tmproot/adopter-quoted-checkout"
cp -a "$adopter" "$quoted_checkout"
quote_checkout_reference "$quoted_checkout"
schedule_adopter_script "$quoted_checkout" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "the generated suite accepts a quoted checkout reference with credentials disabled"
else
  fail "the generated suite rejected a quoted safe checkout reference: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
inline_checkout="$tmproot/adopter-inline-checkout"
cp -a "$adopter" "$inline_checkout"
inline_checkout_with_persist_credentials "$inline_checkout"
schedule_adopter_script "$inline_checkout" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "the generated suite accepts an inline checkout mapping with credentials disabled"
else
  fail "the generated suite rejected a safe inline checkout mapping: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
expect_rejection "an inline checkout mapping without disabled credentials (#1717)" inline_checkout_without_persist_credentials
sync_last_rejection
grep -qF 'persists checkout credentials into release repository code (#1712)' "$tmproot/run.out" \
  && pass "the generated suite detects checkout credential persistence in inline YAML" \
  || fail "an inline checkout credential mutation was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "checkout reference uses case-variant owner and repository (#1717)" \
  uppercase_checkout_without_disabled_credentials
sync_last_rejection
grep -qF 'persists checkout credentials into release repository code (#1712)' "$tmproot/run.out" \
  && pass "the generated suite checks checkout references case-insensitively" \
  || fail "a case-variant checkout reference was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "checkout reference uses a folded YAML scalar (#1717)" \
  folded_checkout_reference_without_disabled_credentials
sync_last_rejection
grep -qF 'cannot safely inspect a release workflow action reference (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects unsupported folded checkout references" \
  || fail "a folded checkout reference was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "release job hides steps in a flow-style sequence (#1717)" \
  add_flow_style_checkout_step
sync_last_rejection
grep -qF 'cannot safely inspect flow-style release workflow steps (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects unparsed flow-style step collections" \
  || fail "flow-style release steps were rejected for another reason: $(tail -2 "$tmproot/run.out")"
reindented_adopter="$tmproot/adopter-reindented-jobs"
cp -a "$adopter" "$reindented_adopter"
reindent_release_job_fields "$reindented_adopter"
schedule_adopter_script "$reindented_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "the generated suite finds release steps with valid noncanonical job indentation"
else
  fail "valid job indentation was rejected: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
expect_rejection "release verifier uses PATH modified by dependency lifecycle scripts (#1717)" \
  use_mutable_path_for_release_verification
sync_last_rejection
grep -qF 'does not match the approved release verification script (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects runner PATH in release verification" \
  || fail "a mutable verifier PATH was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "release verification PATH capture overrides runner PATH (#1717)" \
  add_capture_path_override
sync_last_rejection
grep -qF 'overrides the runner-managed PATH before release verification (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects a PATH override in the trusted capture step" \
  || fail "a capture-step PATH override was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "verify job overrides runner PATH (#1717)" add_verify_job_path_override
sync_last_rejection
grep -qF 'overrides the runner-managed PATH before release verification (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects a job-level PATH override" \
  || fail "a job-level PATH override was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "release verification PATH is captured after dependency lifecycle code (#1717)" \
  move_capture_after_lifecycle_scripts
sync_last_rejection
grep -qF 'does not capture trusted release verification runtime inputs (#1717)' "$tmproot/run.out" \
  && pass "the generated suite requires PATH capture immediately after setup-node" \
  || fail "a late PATH capture was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "an escaped flow-style environment key selects runner PATH (#1717)" \
  add_escaped_flow_path_environment
sync_last_rejection
grep -qF 'uses an unsupported or ambiguous environment mapping (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects escaped flow-style PATH environment keys" \
  || fail "an escaped flow-style PATH key was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a tagged flow-style environment key selects Bash startup script (#1717)" \
  add_tagged_flow_bash_env
sync_last_rejection
grep -qF 'uses an unsupported or ambiguous environment mapping (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects tagged flow-style BASH_ENV environment keys" \
  || fail "a tagged flow-style BASH_ENV key was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "an explicit-key BASH_ENV mapping selects Bash startup script (#1717)" \
  add_explicit_bash_env_key
sync_last_rejection
grep -qF 'uses an unsupported or ambiguous environment mapping (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects explicit-key BASH_ENV mappings" \
  || fail "an explicit-key BASH_ENV mapping was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "an inserted verify step writes Bash startup script through GITHUB_ENV (#1717)" \
  add_pre_capture_environment_writer
sync_last_rejection
grep -qF 'does not use the approved verify-job step sequence (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects unapproved verify-job steps before dependency credentials" \
  || fail "an inserted environment-writing step was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "an existing release-plan step writes Bash startup script through GITHUB_ENV (#1717)" \
  add_in_place_precredential_environment_writer
sync_last_rejection
grep -qF 'contains an unapproved verify step before credentialed dependency installation (#1717)' "$tmproot/run.out" \
  && pass "the generated suite pins existing verify-job commands before dependency credentials" \
  || fail "an in-place environment-writing mutation was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "an existing release-plan step injects PYTHONPATH (#1717)" \
  add_precredential_pythonpath
sync_last_rejection
grep -qF 'contains an unapproved verify step before credentialed dependency installation (#1717)' "$tmproot/run.out" \
  && pass "the generated suite pins pre-credential step environment fields" \
  || fail "a pre-credential PYTHONPATH mutation was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "the stamped-version step changes generator-declared package directories (#1717)" \
  drift_verify_stamp_package_directories
sync_last_rejection
grep -qF 'has an unapproved package-directory assignment in Stamp the dispatched package versions (#1717)' "$tmproot/run.out" \
  && pass "the generated suite pins package directories in the version-stamp step" \
  || fail "an altered version-stamp package directory was rejected for another reason: $(tail -2 "$tmproot/run.out")"
for stamp_mutation in command condition environment; do
  expect_rejection "the version-stamp step changes its $stamp_mutation (#1717)" \
    drift_verify_stamp_step "$stamp_mutation"
sync_last_rejection
  grep -qF 'does not match the approved package-version stamp step (#1717)' "$tmproot/run.out" \
    && pass "the generated suite pins the full version-stamp step" \
    || fail "a changed version-stamp $stamp_mutation was rejected for another reason: $(tail -2 "$tmproot/run.out")"
done
expect_rejection "generator provenance changes the contract-pinned package set (#1717)" \
  drift_generator_provenance_only '--only-package-dir compat'
sync_last_rejection
grep -qF 'does not declare valid package directories in generator provenance (#1717)' "$tmproot/run.out" \
  && pass "the generated suite binds provenance to canonical package-directory inputs" \
  || fail "altered generator provenance was rejected for another reason: $(tail -2 "$tmproot/run.out")"
while IFS= read -r package_directory_options; do
  expect_rejection "generator provenance alone uses an unsafe or duplicate package directory: $package_directory_options (#1717)" \
    drift_generator_provenance_only "$package_directory_options"
sync_last_rejection
  grep -qF 'does not declare valid package directories in generator provenance (#1717)' "$tmproot/run.out" \
    && pass "the generated suite rejects unsafe provenance independently of workflow stamps" \
    || fail "unsafe provenance was rejected for another reason: $(tail -2 "$tmproot/run.out")"
done <<'EOF'
--package-dir ../compat
--only-package-dir compat --only-package-dir compat
EOF
expect_release_mode_rejection release-artifact \
  "a release-artifact release-plan step writes Bash startup script through GITHUB_ENV (#1717)" \
  add_in_place_precredential_environment_writer \
  'contains an unapproved verify step before credentialed dependency installation (#1717)'
expect_release_mode_rejection release-snapshot \
  "a release-snapshot release-plan step writes Bash startup script through GITHUB_ENV (#1717)" \
  add_in_place_precredential_environment_writer \
  'contains an unapproved verify step before credentialed dependency installation (#1717)'
expect_release_mode_rejection release-artifact \
  "a release-artifact release-plan step injects PYTHONPATH (#1717)" \
  add_precredential_pythonpath \
  'contains an unapproved verify step before credentialed dependency installation (#1717)'
expect_release_mode_rejection release-snapshot \
  "a release-snapshot release-plan step injects PYTHONPATH (#1717)" \
  add_precredential_pythonpath \
  'contains an unapproved verify step before credentialed dependency installation (#1717)'
expect_rejection "credentialed dependency install preloads repository Node code (#1717)" \
  add_install_node_options
sync_last_rejection
grep -qF 'does not allowlist the credentialed dependency installation environment (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects NODE_OPTIONS during credentialed installation" \
  || fail "a credentialed NODE_OPTIONS mutation was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "release verifier overrides npm script shell (#1717)" replace_verification_script_shell
sync_last_rejection
grep -qF 'does not isolate release verification from prior lifecycle environment (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects an npm script-shell override" \
  || fail "an npm script-shell override was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "release verifier inherits Bash startup script (#1717)" replace_verification_bash_env
sync_last_rejection
grep -qF 'does not isolate release verification from prior lifecycle environment (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects a Bash startup script override" \
  || fail "a Bash startup script override was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "stamped-version warning text shadowed outside generated header (#862)" shadow_stamped_version_header
sync_last_rejection
grep -qF 'does not carry the stamped-version warning inside the generated header before `on:` (#862)' "$tmproot/run.out" \
  && pass "the shadowed warning is rejected for leaving generated header" \
  || fail "the shadowed warning failed for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "a modified release-verification failure diagnostic (#1717)" noop_stamped_version_diagnostic
sync_last_rejection
grep -qF 'does not match the approved release verification script (#1717)' "$tmproot/run.out" \
  && pass "the generated suite rejects a changed verification script by its approved-script pin" \
  || fail "the changed verification script was rejected for another reason: $(tail -2 "$tmproot/run.out")"
expect_rejection "an unrelated release step exposed to private-package auth (#569)" expose_private_token_to_unrelated_step
expect_rejection "a release caller reachable by a push to main" add_push_trigger
expect_rejection "a release caller on a mutable reusable ref" unpin_release_ref
expect_rejection "a release caller whose contract_ref drifts from its uses pin" drift_release_contract_ref
expect_rejection "a release caller whose Node version became Renovate-visible" expose_node_version_to_renovate
expect_rejection "a hand-written release caller with no generator provenance" strip_release_provenance
expect_rejection "a push: trigger hidden in a flow-style on:" add_flow_style_push_trigger
for equivalent in on '"on"' true True TRUE yes Yes YES; do
  expect_rejection "a duplicate YAML-equivalent $equivalent trigger key (#1070)" \
    duplicate_equivalent_trigger "$equivalent"
done
expect_rejection "an aliased release trigger mapping (#1070)" add_trigger_alias
expect_rejection "a merged release trigger mapping (#1070)" add_trigger_merge_key
expect_rejection "duplicate quoted and plain workflow_dispatch keys (#1070)" duplicate_dispatch_trigger
expect_rejection "a workflow_dispatch value alias (#1070)" alias_dispatch_schema
expect_rejection "an anchored workflow_dispatch mapping (#1070)" anchor_dispatch_schema
expect_rejection "a flow-style workflow_dispatch input mapping (#1070)" flow_dispatch_schema
expect_rejection "an explicitly tagged workflow_dispatch mapping (#1070)" tag_dispatch_schema
expect_rejection "a duplicate nested dispatch input key (#1070)" duplicate_nested_input_key
expect_rejection "an unexpected dispatch input (#1070)" add_unknown_dispatch_input
expect_rejection "a changed required dispatch-input shape (#1070)" change_dispatch_input_shape
expect_rejection "a YAML directive (#1070)" add_yaml_directive
expect_rejection "a second YAML document (#1070)" add_second_yaml_document
expect_rejection "malformed nested trigger indentation (#1070)" malform_nested_trigger_indent
expect_rejection "a scalar changed through a trailing comment (#1070)" add_trigger_scalar_comment
expect_rejection "a malformed top-level release trigger mapping (#1070)" malform_trigger_mapping
expect_rejection "a release caller exposed as a reusable workflow_call" add_workflow_call_trigger
expect_rejection "a release caller fired by a release: event" add_release_trigger
expect_rejection "an install credential inherited from a job-level env:" install_token_from_job_env
expect_rejection "a release caller under any other filename (#463, #464)" rename_release_caller

# ...and the renamed caller must be rejected for its real defect, not merely for
# no longer being called release.yml. A checker that only notices the name would
# pass the identical file back under its old one.
sync_last_rejection
grep -q 'publish-package.yml' "$tmproot/run.out" \
  && pass "the renamed release caller is checked under the name it actually has" \
  || fail "the renamed caller's rejection never names it: $(tail -2 "$tmproot/run.out")"

# The shape ~21 repositories carry today. If the emitted suite accepted it,
# regenerating would change nothing an adopter could observe.
legacy_release="$tmproot/adopter-legacy-release"
build_adopter "$legacy_release" legacy
schedule_adopter_script "$legacy_release" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted the hand-copied verjson-payments release shape"
else
  pass "emitted suite rejects the hand-copied verjson-payments release shape"
fi
SCHED_SCRIPT
)"
# The set-level atomicity verdict runs before every per-member assertion and is
# fatal, so it is what a legacy release.yml now hits first: a hand-copied file
# declares no generator-mode header at all. That is the correct diagnosis --
# regenerating the member is the fix for every defect the per-member assertions
# would have found in it -- but the remedy must name all three modes that write
# release.yml. ~21 repositories carry this shape and they do not all want
# release-node: telling every one of them to run it would publish npm packages
# from repositories that publish GitHub Release assets, or nothing at all.
#
# Nothing here declares a mode, so this finding must say the mode is unresolved
# and list every mode that can write release.yml. Repair guidance is prose-only
# for every member because the checker cannot reconstruct caller-specific flags.
# Anchored on the arm's own wording rather than sliced out of a pipe with
# `head`: a hand-copied release.yml declares no generator-mode header, so this
# matches exactly one line, and a pipe-fed early-exiting consumer would kill the
# producer on SIGPIPE under pipefail (#1430/#1445).
sync_last_rejection
legacy_release_finding="$(grep -F '.github/workflows/release.yml declares' "$tmproot/run.out")"
sync_last_rejection
{ grep -qF 'possible modes are release-node, release-artifact, release-snapshot' <<<"$legacy_release_finding" \
  && grep -qF 'release-artifact publishes GitHub Release assets' "$tmproot/run.out" \
  && grep -qF 'release-snapshot publishes nothing from the release workflow' "$tmproot/run.out" \
  && ! grep -qF '>' <<<"$legacy_release_finding"; } \
  && pass "the legacy release shape names every possible mode and gives prose-only guidance" \
  || fail "the legacy release rejection names no remedy, names only one mode, or carries a truncating redirect: ${legacy_release_finding:-$(tail -2 "$tmproot/run.out")}"

# The counterpart. docs/changelog/README.md tells adopters to write exactly this
# comment next to a correct wiring, so a guard matching the raw line would break
# the build of everyone who followed the documentation.
commented="$tmproot/adopter-commented"
build_adopter "$commented"
sed -i 's|^      release_environment:|      # ORG_ADMIN_TOKEN and push_token are retired by ADR 0099.\n      release_environment:|' \
  "$commented/.github/workflows/release.yml"
schedule_adopter_script "$commented" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite ignores retired-token names in comments"
else
  fail "emitted suite treated a comment as credential wiring: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

# A quoted title is the correct spelling, not a tolerated one, so the emitted
# suite has to read it the way the engine does. Both quote styles, because the
# unquoting rule branches on which quote opened the scalar and a parser can be
# right about one of them.
quoted="$tmproot/adopter-quoted"
build_adopter "$quoted"
cat >"$quoted/NEXT/2026-08-02-issue-8-quoted.md" <<'FRAGMENT'
---
date: 2026-08-02
issue: 8
title: "feat(caller): a double-quoted title"
---

The lead paragraph, which is what a release note carries.

## Why

The argument beneath it, which a release note does not.
FRAGMENT
git -C "$quoted" add -A >/dev/null 2>&1
git -C "$quoted" -c user.email=t@t -c user.name=t commit -qm quoted >/dev/null 2>&1
schedule_adopter_script "$quoted" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite accepts the quoted titles YAML requires of conventional commits"
else
  fail "emitted suite rejected a quoted title: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

commit_fixture() {
  # commit_fixture <dir> <message>
  git -C "$1" add -A >/dev/null 2>&1
  git -C "$1" -c user.email=t@t -c user.name=t commit -qm "$2" >/dev/null 2>&1
}

# `refs:` is in KNOWN_KEYS and exists so several entries can link an issue while
# only one owns it (#316). The renderer appends `; refs #n` after the back-link,
# but the emitted assertion anchored `_$` directly after the issue number, so the
# one combination the contract validates and renders correctly was rejected by
# the generated test — which adopters wire into `npm test` and may not edit (#461).
refs_adopter="$tmproot/adopter-refs"
build_adopter "$refs_adopter"
cat >"$refs_adopter/NEXT/2026-08-03-issue-5-refs.md" <<'FRAGMENT'
---
date: 2026-08-03
issue: 5
refs: 16
title: 'fix(caller): an entry that links a second issue'
---

Body.
FRAGMENT
commit_fixture "$refs_adopter" refs
schedule_adopter_script "$refs_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite accepts an issue-form fragment carrying refs (#461)"
else
  fail "emitted suite rejected a refs: fragment: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
refs_released="$tmproot/adopter-refs-released"
cp -a "$refs_adopter" "$refs_released"
python3 "$contract_src" release --repo-root "$refs_released" --version v1.0.0 >/dev/null 2>&1
schedule_adopter_script "$refs_released" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite still accepts refs metadata after the exact release path"
else
  fail "emitted suite rejects refs metadata after release: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

# Two refs, because one leaves the repeated group in the pattern unproven.
multi_refs="$tmproot/adopter-refs-multi"
build_adopter "$multi_refs"
cat >"$multi_refs/NEXT/2026-08-04-issue-6-multi-refs.md" <<'FRAGMENT'
---
date: 2026-08-04
issue: 6
refs: 16, 22
title: 'fix(caller): an entry that links two other issues'
---

Body.
FRAGMENT
commit_fixture "$multi_refs" multi-refs
schedule_adopter_script "$multi_refs" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite accepts a fragment refs-ing several issues (#461)"
else
  fail "emitted suite rejected a multi-ref fragment: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

# Widening the pattern to accept `refs` is only safe if it can still fail. Nothing
# an adopter writes can produce a back-link that disagrees with its own fragment —
# the engine derives both — so the mutation is applied to the rendered OUTPUT: the
# generated renderer keeps its pin and its delegation, and only what it prints is
# corrupted. Without this, every case above would pass against `.*`.
corrupt_render() {
  # corrupt_render <dir> <sed-script>
  RENDERER="$1/scripts/render-next.sh" MUTATION="$2" python3 - <<'PY'
import os
import shlex

path = os.environ["RENDERER"]
tail = 'exec python3 "$contract" "${args[@]}"\n'
text = open(path, encoding="utf-8").read()
if not text.endswith(tail):
    raise SystemExit("generated renderer no longer ends with the render exec")
piped = tail.rstrip("\n") + " | sed " + shlex.quote(os.environ["MUTATION"]) + "\n"
open(path, "w", encoding="utf-8").write(text[: -len(tail)] + piped)
PY
}

expect_backlink_rejection() {
  # expect_backlink_rejection <label> <sed-script>
  local label="$1" dir
  reject_seq=$((reject_seq + 1))
  dir="$tmproot/backlink-$reject_seq"
  build_adopter "$dir"
  cp "$refs_adopter/NEXT/2026-08-03-issue-5-refs.md" "$dir/NEXT/"
  commit_fixture "$dir" backlink
  corrupt_render "$dir" "$2"
  if run_adopter "$dir"; then
    fail "emitted suite accepted $label"
  elif grep -q 'back-link missing from the rendered log' "$tmproot/run.out"; then
    pass "emitted suite rejects $label"
  else
    fail "$label failed for another reason: $(tail -2 "$tmproot/run.out")"
  fi
}

expect_backlink_rejection "a rendered back-link naming the wrong issue" 's/issue #5;/issue #55;/'
expect_backlink_rejection "a rendered back-link carrying the wrong date" 's/^_Date: 2026-08-03;/_Date: 2026-08-13;/'
expect_backlink_rejection "text appended after the back-link's closing underscore" 's/refs #16_$/refs #16_ and more/'
expect_backlink_rejection "an unrecognised suffix in place of refs" 's/; refs #16_/; notes #16_/'
expect_backlink_rejection "a declared refs linkage the render dropped" 's/; refs #16_/_/'

# A validator may reject the first malformed back-link while rendered output is
# still much larger than a pipe buffer. The generated suite must report that
# policy diagnostic, never a producer-side Broken pipe made fatal by pipefail.
large_backlink="$tmproot/backlink-large"
build_adopter "$large_backlink"
cp "$refs_adopter/NEXT/2026-08-03-issue-5-refs.md" "$large_backlink/NEXT/"
python3 - "$large_backlink/NEXT/2026-08-03-issue-5-refs.md" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(path.read_text(encoding="utf-8") + ("rendered padding\n" * 32768), encoding="utf-8")
PY
commit_fixture "$large_backlink" large-backlink
corrupt_render "$large_backlink" 's/issue #5;/issue #55;/'
for attempt in 1 2 3 4 5; do
  if run_adopter "$large_backlink"; then
    fail "large rendered back-link mutation was accepted on attempt $attempt"
sync_last_rejection
  elif grep -q 'back-link missing from the rendered log' "$tmproot/run.out" \
       && ! grep -qi 'broken pipe' "$tmproot/run.out"; then
    pass "large rendered rejection reports the intended diagnostic on attempt $attempt"
  else
sync_last_rejection
    fail "large rendered rejection raced into another failure on attempt $attempt: $(tail -2 "$tmproot/run.out")"
  fi
done

# The released form is what an author is asked to read before merge, and under
# ADR 0059 it is the form that can never be corrected afterwards. A renderer that
# cannot produce it leaves only "skip the review" or "edit a generated artifact",
# and the contract forbids the second (#443).
released_out="$tmproot/as-released.out"
if (cd "$quoted" && ./scripts/render-next.sh --as-released) >"$released_out" 2>&1; then
  pass "the generated renderer accepts --as-released"
else
  fail "the generated renderer rejected --as-released: $(head -1 "$released_out")"
fi

# Distinguishes pass-through from a flag that is merely tolerated and dropped:
# the released form omits everything after the lead paragraph.
if grep -q '^## feat(caller): a double-quoted title$' "$released_out" \
  && grep -q '^The lead paragraph, which is what a release note carries\.$' "$released_out" \
  && ! grep -q '^## Why$' "$released_out"; then
  pass "--as-released renders the release note, not the whole diary"
else
  fail "--as-released did not change the output; the flag is being swallowed"
fi

cat >"$quoted/NEXT/2026-08-07-issue-390-python-stream.md" <<'FRAGMENT'
---
date: 2026-08-07
issue: 390
component: python
title: Python stream
---

Python-only release note.
FRAGMENT
commit_fixture "$quoted" component
component_out="$tmproot/component.out"
if (cd "$quoted" && ./scripts/render-next.sh --component python) \
    >"$component_out" 2>&1 \
    && grep -q '^## Python stream$' "$component_out" \
    && ! grep -q '^## feat(caller): a double-quoted title$' "$component_out"; then
  pass "the generated renderer selects exactly one explicit component stream"
else
  fail "the generated renderer does not isolate an explicit component stream"
fi

# Still a renderer, not a front end to a pinned engine: anything else is refused
# so a caller cannot reach subcommands the contract does not sanction.
if (cd "$quoted" && ./scripts/render-next.sh release --version v9.9.9) >/dev/null 2>&1; then
  fail "the generated renderer forwarded an unsanctioned argument"
else
  pass "the generated renderer still refuses arguments outside its render flags"
fi

# Only reachable after a release, so it needs a released fixture.
edited="$tmproot/adopter-edited"
build_adopter "$edited"
python3 "$contract_src" release --repo-root "$edited" --version v1.0.0 >/dev/null 2>&1
printf '\nhand-written addition\n' >>"$edited/CHANGELOG.md"
schedule_adopter_script "$edited" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a hand-edited CHANGELOG.md"
else
  pass "emitted suite rejects a hand-edited CHANGELOG.md"
fi
SCHED_SCRIPT
)"

# Generation is fail-closed on an unresolvable ref: emitting a caller whose engine
# cannot be verified would hand adopters a contract that only looks pinned.
gen_err="$(mktemp)"
if "$gen" renderer "$unresolvable_sha" >/dev/null 2>"$gen_err"; then
  fail "generator emitted a caller for a ref whose engine it could not read"
elif grep -q "cannot resolve" "$gen_err"; then
  pass "generating for an unresolvable ref fails closed with a stated cause"
else
  fail "generation failed for an unstated reason: $(cat "$gen_err")"
fi
rm -f "$gen_err"

# --------------------------------------------------------------------------
# release-artifact (#975): a non-npm release caller for adopters that ship
# GitHub Release assets (e.g. Electron installers) instead of a package.
# --------------------------------------------------------------------------

if bash "$gen" release-artifact "$sha" >/dev/null 2>&1; then
  fail "release-artifact accepted generation with no --build-runner"
else
  pass "release-artifact requires at least one --build-runner"
fi

if bash "$gen" release-artifact "$sha" --build-runner 'not a label' >/dev/null 2>&1; then
  fail "release-artifact accepted a --build-runner value that is not a bare label"
else
  pass "release-artifact rejects a --build-runner value that is not a bare label"
fi

if bash "$gen" release-artifact "$sha" --build-runner 'vars.CI_LANE_TRUSTED_MACOS' >/dev/null 2>&1; then
  fail "release-artifact accepted a silent variable-name runner literal"
else
  pass "release-artifact rejects variable names that omit the required expression"
fi

lane_expression='${{ fromJSON(vars.CI_LANE_TRUSTED_MACOS) }}'
windows_lane_expression='${{ fromJSON(vars.CI_LANE_TRUSTED_WINDOWS) }}'
if bash "$gen" release-artifact "$sha" --build-runner "$lane_expression" >/dev/null 2>&1; then
  pass "release-artifact accepts the exact ADR 0103 macOS lane expression"
else
  fail "release-artifact rejected the exact ADR 0103 macOS lane expression"
fi
for forbidden_metered_literal in macos-14 windows-2022; do
  if bash "$gen" release-artifact "$sha" --build-runner "$forbidden_metered_literal" >/dev/null 2>&1; then
    fail "release-artifact accepted literal metered selector: $forbidden_metered_literal"
  else
    pass "release-artifact rejects literal metered selector: $forbidden_metered_literal"
  fi
done

if bash "$gen" release-artifact "$sha" --build-runner ubuntu-24.04 --node-version 22 \
    >/dev/null 2>&1; then
  pass "release-artifact accepts the same --node-version/--scope knobs as release-node"
else
  fail "release-artifact rejected a validated --node-version"
fi

if bash "$gen" release-node "$sha" --build-runner ubuntu-24.04 >/dev/null 2>&1; then
  fail "release-node accepted --build-runner, a release-artifact-only flag"
else
  pass "release-node rejects --build-runner"
fi

if bash "$gen" release-node "$sha" --approved-internal-package @verjson/ai >/dev/null 2>&1; then
  fail "release-node accepted release-artifact private acquisition policy"
else
  pass "private acquisition policy is release-artifact-only"
fi
for invalid_package in '@Verjson/ai' '@verjson/AI' '@other/ai' '@verjson/ai@latest'; do
  if bash "$gen" release-artifact "$sha" --build-runner ubuntu-24.04 \
      --approved-internal-package "$invalid_package" >/dev/null 2>&1; then
    fail "release-artifact accepted invalid approved package: $invalid_package"
  else
    pass "release-artifact rejects invalid approved package: $invalid_package"
  fi
done

artifact_release="$(bash "$gen" release-artifact "$sha" \
  --build-runner ubuntu-24.04 --build-runner self-hosted-release)"

sync_last_rejection
grep -qF "gen-changelog-caller.sh release-artifact $sha --build-runner ubuntu-24.04 --build-runner self-hosted-release" \
  <<<"$artifact_release" \
  && pass "release-artifact records its exact regeneration command, including build runners" \
  || fail "release-artifact does not record a regenerable provenance comment"
sync_last_rejection
grep -qE '^  build:$' <<<"$artifact_release" \
  && grep -qE '^  publish:$' <<<"$artifact_release" \
  && ! grep -q 'uses:.*node-release\.yml' <<<"$artifact_release" \
  && pass "release-artifact replaces node-release.yml with a build+publish pair" \
  || fail "release-artifact did not emit the expected build/publish shape"
sync_last_rejection
grep -qF -- "- os: 'ubuntu-24.04'" <<<"$artifact_release" \
  && grep -qF -- "- os: 'self-hosted-release'" <<<"$artifact_release" \
  && pass "release-artifact's build matrix carries exactly the declared runner labels" \
  || fail "release-artifact's build matrix does not match --build-runner"

private_artifact_release="$(bash "$gen" release-artifact "$sha" \
  --build-runner "$lane_expression" --build-runner "$windows_lane_expression" \
  --approved-internal-package @verjson/ai --approved-internal-package @verjson/ai-gguf)"
sync_last_rejection
grep -qF 'acquire-private-dependencies:' <<<"$private_artifact_release" \
  && grep -qF 'permissions:' <<<"$private_artifact_release" \
  && grep -qF 'packages: read' <<<"$private_artifact_release" \
  && grep -qF 'timeout-minutes: 45' <<<"$private_artifact_release" \
  && grep -qF 'npm ci --ignore-scripts --audit=false --fund=false' <<<"$private_artifact_release" \
  && grep -qF 'fail-on-cache-miss: true' <<<"$private_artifact_release" \
  && grep -qF "APPROVED_INTERNAL_PACKAGES: '@verjson/ai,@verjson/ai-gguf'" <<<"$private_artifact_release" \
  && pass "release-artifact separates approved private acquisition from credentialless build hooks" \
  || fail "release-artifact omitted the bounded private dependency handoff"
build_slice="$(awk '/^  build:/{seen=1} /^  publish:/{seen=0} seen' <<<"$private_artifact_release")"
! grep -qE 'secrets\b' <<<"$build_slice" \
  && grep -qF "NODE_AUTH_TOKEN: ''" <<<"$build_slice" \
  && pass "private release build hooks receive restored dependencies without secret context" \
  || fail "private release build hook can observe credential context"

build_artifact_adopter() {
  # A non-npm adopter: same verify/snapshot shape as build_adopter's default,
  # but release-artifact for publication and a release-build.sh hook per #975.
  local dir="$1"
  mkdir -p "$dir/NEXT" "$dir/scripts" "$dir/.github/workflows"
  bash "$gen" codeowners "$sha" >"$dir/.github/CODEOWNERS"
  bash "$gen" renderer "$sha" >"$dir/scripts/render-next.sh"
  bash "$gen" workflow "$sha" >"$dir/.github/workflows/changelog.yml"
  bash "$gen" pr-gate "$sha" >"$dir/.github/workflows/changelog-contract.yml"
  cp "$emitted" "$dir/scripts/changelog-contract.test.sh"
  chmod +x "$dir/scripts/render-next.sh" "$dir/scripts/changelog-contract.test.sh"
  bash "$gen" release-artifact "$sha" --build-runner ubuntu-24.04 --build-runner self-hosted-release \
    >"$dir/.github/workflows/release.yml"
  printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' 'mkdir -p "$2"' \
    'echo built >"$2/artifact-$1.bin"' >"$dir/scripts/release-build.sh"
  chmod +x "$dir/scripts/release-build.sh"
  cat >"$dir/NEXT/2026-08-01-issue-975-first.md" <<'FRAGMENT'
---
date: 2026-08-01
issue: 975
title: 'fix(caller): first artifact entry'
---

Body.
FRAGMENT
  git -C "$dir" init -q
  git -C "$dir" config user.name Test
  git -C "$dir" config user.email test@example.com
  git -C "$dir" add -A
  git -C "$dir" commit -qm initial
}

artifact_adopter="$tmproot/adopter-artifact"
build_artifact_adopter "$artifact_adopter"
schedule_adopter_script "$artifact_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite accepts a generated release-artifact caller"
else
  fail "emitted suite rejects a generated release-artifact caller: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
assert_mutable_verification_path_rejected "$artifact_adopter" release-artifact artifact-mutable-path
assert_release_path_mutation_rejected "$artifact_adopter" release-artifact artifact-capture-path-override \
  add_capture_path_override 'overrides the runner-managed PATH before release verification (#1717)'
assert_release_path_mutation_rejected "$artifact_adopter" release-artifact artifact-job-path-override \
  add_verify_job_path_override 'overrides the runner-managed PATH before release verification (#1717)'
assert_release_path_mutation_rejected "$artifact_adopter" release-artifact artifact-late-path-capture \
  move_capture_after_lifecycle_scripts 'does not capture trusted release verification runtime inputs (#1717)'

private_artifact_adopter="$tmproot/adopter-artifact-private"
cp -a "$artifact_adopter" "$private_artifact_adopter"
bash "$gen" release-artifact "$sha" \
  --build-runner '${{ fromJSON(vars.CI_LANE_TRUSTED_MACOS) }}' \
  --build-runner '${{ fromJSON(vars.CI_LANE_TRUSTED_WINDOWS) }}' --approved-internal-package @verjson/ai \
  >"$private_artifact_adopter/.github/workflows/release.yml"
bash "$gen" contract-test "$sha" \
  --build-runner '${{ fromJSON(vars.CI_LANE_TRUSTED_MACOS) }}' \
  --build-runner '${{ fromJSON(vars.CI_LANE_TRUSTED_WINDOWS) }}' --approved-internal-package @verjson/ai \
  >"$private_artifact_adopter/scripts/changelog-contract.test.sh"
chmod +x "$private_artifact_adopter/scripts/changelog-contract.test.sh"
cat >"$private_artifact_adopter/package-lock.json" <<'LOCK'
{"name":"private-release","lockfileVersion":3,"packages":{"":{"name":"private-release"},"node_modules/@verjson/ai":{"name":"@verjson/ai","version":"1.0.0","resolved":"https://npm.pkg.github.com/download/@verjson/ai/1.0.0/hash","integrity":"sha512-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="}}}
LOCK
git -C "$private_artifact_adopter" add .github/workflows/release.yml scripts/changelog-contract.test.sh package-lock.json
git -C "$private_artifact_adopter" commit -qm 'enable private release dependency acquisition'
schedule_adopter_script "$private_artifact_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite accepts credential-separated private release acquisition"
else
  fail "emitted suite rejects canonical private release acquisition: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

unbound_build_adopter="$tmproot/adopter-artifact-unbound-build"
cp -a "$artifact_adopter" "$unbound_build_adopter"
python3 - "$unbound_build_adopter/.github/workflows/release.yml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text()
binding = '    runs-on: ${{ matrix.os }}'
assert source.count(binding) == 1
path.write_text(source.replace(binding, "    # runs-on: ${{ matrix.os }}\n    runs-on: 'general'", 1))
PY
schedule_adopter_script "$unbound_build_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepts a build job detached from its approved OS matrix"
else
  pass "emitted suite rejects a build job detached from its approved OS matrix"
fi
SCHED_SCRIPT
)"

unbound_acquisition_adopter="$tmproot/adopter-artifact-unbound-acquisition"
cp -a "$private_artifact_adopter" "$unbound_acquisition_adopter"
python3 - "$unbound_acquisition_adopter/.github/workflows/release.yml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text()
binding = '    runs-on: ${{ matrix.os }}'
assert source.count(binding) == 2
path.write_text(source.replace(binding, "    # runs-on: ${{ matrix.os }}\n    runs-on: 'general'", 1))
PY
if run_adopter "$unbound_acquisition_adopter"; then
  fail "emitted suite accepts acquisition detached from its approved OS matrix"
else
  pass "emitted suite rejects acquisition detached from its approved OS matrix"
fi

sync_last_rejection
if python3 "$repo_root/scripts/ci-gate/hosted-selector-policy.py" \
  --consumer-policy "$private_artifact_adopter/.github/workflows" >"$tmproot/run.out" 2>&1; then
  pass "generated private artifact caller satisfies canonical runner selector policy"
else
sync_last_rejection
  fail "generated private artifact caller violates runner selector policy: $(tail -2 "$tmproot/run.out")"
fi

private_lock_validator="$tmproot/private-lock-validator.js"
sync_last_rejection
if python3 - "$private_artifact_adopter/.github/workflows/release.yml" "$private_lock_validator" >"$tmproot/run.out" 2>&1 <<'PY'
from pathlib import Path
import re
import subprocess
import sys

lines = Path(sys.argv[1]).read_text().splitlines()
blocks = []
for start, line in enumerate(lines):
    if not re.search(r"\bnode <<(?:'NODE'|NODE)\s*$", line):
        continue
    end = next((index for index in range(start + 1, len(lines))
                if re.fullmatch(r"\s*NODE", lines[index])), None)
    if end is None:
        raise SystemExit(f"unterminated generated Node block at line {start + 1}")
    indent = len(lines[end]) - len(lines[end].lstrip())
    body = "\n".join(value[indent:] for value in lines[start + 1:end]) + "\n"
    checked = subprocess.run(["node", "--check", "-"], input=body, text=True, capture_output=True)
    if checked.returncode:
        raise SystemExit(f"generated Node block at line {start + 1}: {checked.stderr}")
    blocks.append(body)
validators = [block for block in blocks if "internal dependency is not pinned" in block]
if not blocks or len(validators) != 1:
    raise SystemExit("expected one private lock validator among generated Node blocks")
Path(sys.argv[2]).write_text(validators[0])
PY
then
  pass "every generated artifact caller Node block parses"
else
sync_last_rejection
  fail "generated artifact caller contains invalid Node code: $(tail -2 "$tmproot/run.out")"
fi

upper_scope_adopter="$tmproot/adopter-artifact-private-upper-scope"
cp -a "$private_artifact_adopter" "$upper_scope_adopter"
sed -i 's|download/@verjson/ai/|download/@verJSON/ai/|g' "$upper_scope_adopter/package-lock.json"
sync_last_rejection
grep -qF 'download/@verJSON/ai/' "$upper_scope_adopter/package-lock.json" \
  || fail "upper-scope fixture did not retain its authentic registry URL"
sync_last_rejection
run_adopter "$upper_scope_adopter" \
  && (cd "$upper_scope_adopter" && APPROVED_INTERNAL_PACKAGES=@verjson/ai node "$private_lock_validator") >"$tmproot/run.out" 2>&1 \
  && pass "generated private lock checks accept the authentic organization URL spelling" \
  || fail "generated private lock checks reject the authentic organization URL spelling: $(tail -2 "$tmproot/run.out")"

wrong_package_adopter="$tmproot/adopter-artifact-private-wrong-package"
cp -a "$upper_scope_adopter" "$wrong_package_adopter"
sed -i 's|download/@verJSON/ai/|download/@verJSON/wrong/|g' "$wrong_package_adopter/package-lock.json"
sync_last_rejection
if (cd "$wrong_package_adopter" && APPROVED_INTERNAL_PACKAGES=@verjson/ai node "$private_lock_validator") >"$tmproot/run.out" 2>&1; then
  fail "generated private lock validator accepted a different package URL"
else
sync_last_rejection
  grep -qF 'internal dependency is not pinned to its exact GitHub Packages download URL' "$tmproot/run.out" \
    && pass "generated private lock validator rejects a different package URL" \
    || fail "generated private lock validator rejected the wrong package for another reason: $(tail -2 "$tmproot/run.out")"
fi

private_allowlist_adopter="$tmproot/adopter-artifact-private-allowlist"
cp -a "$private_artifact_adopter" "$private_allowlist_adopter"
sed -i "s/APPROVED_INTERNAL_PACKAGES: '@verjson\/ai'/APPROVED_INTERNAL_PACKAGES: '@verjson\/ai,@verjson\/ai-gguf'/" \
  "$private_allowlist_adopter/.github/workflows/release.yml"
node - "$private_allowlist_adopter/package-lock.json" <<'NODE'
const fs = require('fs');
const path = process.argv[2];
const lock = JSON.parse(fs.readFileSync(path, 'utf8'));
lock.packages['node_modules/@verjson/ai-gguf'] = {
  name: '@verjson/ai-gguf', version: '1.0.0',
  resolved: 'https://npm.pkg.github.com/download/@verjson/ai-gguf/1.0.0/hash',
  integrity: 'sha512-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=='
};
fs.writeFileSync(path, JSON.stringify(lock));
NODE
git -C "$private_allowlist_adopter" commit -aqm 'widen generated private package allowlist'
schedule_adopter_script "$private_allowlist_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a consumer-widened private package allowlist"
else
  grep -qF 'allowlist differs from the generated contract' "$ADOPTER_LOG" \
    && pass "emitted suite rejects consumer-widened private package allowlists" \
    || fail "emitted suite rejected allowlist widening for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

private_lock_adopter="$tmproot/adopter-artifact-private-lock"
cp -a "$private_artifact_adopter" "$private_lock_adopter"
node - "$private_lock_adopter/package-lock.json" <<'NODE'
const fs = require('fs');
const path = process.argv[2];
const lock = JSON.parse(fs.readFileSync(path, 'utf8'));
lock.packages['node_modules/@verjson/ai-gguf'] = {
  name: '@verjson/ai-gguf', version: '1.0.0',
  resolved: 'https://npm.pkg.github.com/download/@verjson/ai-gguf/1.0.0/hash',
  integrity: 'sha512-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=='
};
fs.writeFileSync(path, JSON.stringify(lock));
NODE
git -C "$private_lock_adopter" commit -aqm 'add unauthorized private lock entry'
schedule_adopter_script "$private_lock_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted an unauthorized private lock entry"
else
  grep -qF 'repository lock differs from the generated private package authorization' "$ADOPTER_LOG" \
    && pass "emitted suite rejects unauthorized private lock entries" \
    || fail "emitted suite rejected private lock widening for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

private_static_cache_adopter="$tmproot/adopter-artifact-private-static-cache"
cp -a "$private_artifact_adopter" "$private_static_cache_adopter"
sed -i 's/release-dependencies-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.dependency-index }}/release-dependencies-shared/g' \
  "$private_static_cache_adopter/.github/workflows/release.yml"
git -C "$private_static_cache_adopter" commit -aqm 'share dependency cache across runs'
schedule_adopter_script "$private_static_cache_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a shared static dependency cache key"
else
  grep -qF 'cache keys are not bound identically to run, attempt, and matrix OS index' "$ADOPTER_LOG" \
    && pass "emitted suite rejects shared static dependency cache keys" \
    || fail "emitted suite rejected static cache key for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

private_mismatched_cache_adopter="$tmproot/adopter-artifact-private-mismatched-cache"
cp -a "$private_artifact_adopter" "$private_mismatched_cache_adopter"
sed -i '/^  build:/,/^  publish:/ s/matrix.dependency-index/matrix.os/' \
  "$private_mismatched_cache_adopter/.github/workflows/release.yml"
git -C "$private_mismatched_cache_adopter" commit -aqm 'mismatch restored dependency cache key'
schedule_adopter_script "$private_mismatched_cache_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted mismatched acquisition and build cache keys"
else
  grep -qF 'cache keys are not bound identically to run, attempt, and matrix OS index' "$ADOPTER_LOG" \
    && pass "emitted suite rejects mismatched acquisition and build cache keys" \
    || fail "emitted suite rejected mismatched cache key for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

private_lane_preflight_adopter="$tmproot/adopter-artifact-private-lane-preflight"
cp -a "$private_artifact_adopter" "$private_lane_preflight_adopter"
sed -i 's/must be a non-empty JSON runner-label array/must contain labels/g' \
  "$private_lane_preflight_adopter/.github/workflows/release.yml"
git -C "$private_lane_preflight_adopter" commit -aqm 'remove fail-loud lane diagnostic'
schedule_adopter_script "$private_lane_preflight_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a weakened OS lane preflight"
else
  grep -qF 'does not fail loudly before snapshot' "$ADOPTER_LOG" \
    && pass "emitted suite rejects weakened OS lane preflight" \
    || fail "emitted suite rejected lane preflight mutation for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

private_noop_lane_preflight_adopter="$tmproot/adopter-artifact-private-noop-lane-preflight"
cp -a "$private_artifact_adopter" "$private_noop_lane_preflight_adopter"
sed -i '/for lane_name in /c\          for lane_name in; do' \
  "$private_noop_lane_preflight_adopter/.github/workflows/release.yml"
git -C "$private_noop_lane_preflight_adopter" commit -aqm 'make lane preflight a no-op'
schedule_adopter_script "$private_noop_lane_preflight_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a no-op OS lane preflight"
else
  grep -qF 'OS lane preflight logic differs from the provenance-authorized contract' "$ADOPTER_LOG" \
    && pass "emitted suite rejects a no-op OS lane preflight" \
    || fail "emitted suite rejected no-op lane preflight for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

private_timeout_adopter="$tmproot/adopter-artifact-private-timeout"
cp -a "$private_artifact_adopter" "$private_timeout_adopter"
sed -i '/^  build:/,/^  publish:/ s/timeout-minutes: 45/timeout-minutes: 60/' \
  "$private_timeout_adopter/.github/workflows/release.yml"
git -C "$private_timeout_adopter" commit -aqm 'widen metered build timeout'
schedule_adopter_script "$private_timeout_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a build timeout above ADR 0103"
else
  grep -qF "exceeds ADR 0103's 45-minute bound" "$ADOPTER_LOG" \
    && pass "emitted suite rejects build timeouts above ADR 0103" \
    || fail "emitted suite rejected timeout mutation for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

private_acquisition_timeout_adopter="$tmproot/adopter-artifact-private-acquisition-timeout"
cp -a "$private_artifact_adopter" "$private_acquisition_timeout_adopter"
sed -i '/^  acquire-private-dependencies:/,/^  build:/ s/timeout-minutes: 45/timeout-minutes: 60/' \
  "$private_acquisition_timeout_adopter/.github/workflows/release.yml"
git -C "$private_acquisition_timeout_adopter" commit -aqm 'widen metered acquisition timeout'
schedule_adopter_script "$private_acquisition_timeout_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted an acquisition timeout above ADR 0103"
else
  grep -qF "acquisition matrix exceeds ADR 0103's 45-minute bound" "$ADOPTER_LOG" \
    && pass "emitted suite rejects acquisition timeouts above ADR 0103" \
    || fail "emitted suite rejected acquisition timeout mutation for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

private_lifecycle_adopter="$tmproot/adopter-artifact-private-lifecycle"
cp -a "$private_artifact_adopter" "$private_lifecycle_adopter"
sed -i 's/npm ci --ignore-scripts --audit=false --fund=false/npm ci --audit=false --fund=false/' \
  "$private_lifecycle_adopter/.github/workflows/release.yml"
git -C "$private_lifecycle_adopter" commit -aqm 'execute lifecycle scripts beside package credential'
schedule_adopter_script "$private_lifecycle_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted lifecycle execution inside credentialed acquisition"
else
  grep -qF 'private acquisition weakened its credentialless handoff' "$ADOPTER_LOG" \
    && pass "emitted suite rejects lifecycle execution inside credentialed acquisition" \
    || fail "emitted suite rejected private lifecycle mutation for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

private_extra_secret_adopter="$tmproot/adopter-artifact-private-extra-secret"
cp -a "$private_artifact_adopter" "$private_extra_secret_adopter"
sed -i '/^  acquire-private-dependencies:/,/^  build:/ s/NODE_AUTH_TOKEN: \${{ secrets.NODE_AUTH_TOKEN }}/NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}\n          RELEASE_APP_PRIVATE_KEY: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}/' \
  "$private_extra_secret_adopter/.github/workflows/release.yml"
git -C "$private_extra_secret_adopter" commit -aqm 'expose another secret during acquisition'
schedule_adopter_script "$private_extra_secret_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a second acquisition secret"
else
  grep -qF 'another secret' "$ADOPTER_LOG" \
    && pass "emitted suite rejects additional acquisition secrets" \
    || fail "emitted suite rejected acquisition secret widening for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

acquisition_github_token_adopter="$tmproot/adopter-artifact-acquisition-github-token"
cp -a "$private_artifact_adopter" "$acquisition_github_token_adopter"
python3 - "$acquisition_github_token_adopter/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("  acquire-private-dependencies:\n")
end = text.index("\n  build:", start)
needle = "          NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}\n"
token_index = text.find(needle, start, end)
if token_index < 0:
    raise SystemExit("private acquisition token fixture no longer matches generated output")
replacement = needle + "          PRIVATE_GITHUB_TOKEN: ${{ format('{{{0}}}', github.token) }}\n"
text = text[:token_index] + replacement + text[token_index + len(needle):]
open(path, "w", encoding="utf-8").write(text)
PY
git -C "$acquisition_github_token_adopter" commit -aqm 'leak github.token into private acquisition'
schedule_adopter_script "$acquisition_github_token_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release-artifact acquisition step receiving github.token"
else
  grep -qF 'private acquisition exposes credentials or another secret context' "$ADOPTER_LOG" \
    && pass "emitted suite rejects GitHub token access during private acquisition" \
    || fail "emitted suite rejected the acquisition token leak for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

private_selector_adopter="$tmproot/adopter-artifact-private-selector"
cp -a "$private_artifact_adopter" "$private_selector_adopter"
sed -i "s/- os: \${{ fromJSON(vars.CI_LANE_TRUSTED_WINDOWS) }}/- os: 'vars.CI_LANE_TRUSTED_WINDOWS'/g" \
  "$private_selector_adopter/.github/workflows/release.yml"
git -C "$private_selector_adopter" commit -aqm 'replace runner expression with silent literal typo'
schedule_adopter_script "$private_selector_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a silent variable-name runner literal"
else
  grep -qF 'unreviewed runner selector' "$ADOPTER_LOG" \
    && pass "emitted suite rejects silent variable-name runner literals" \
    || fail "emitted suite rejected runner typo for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

broken_artifact_adopter="$tmproot/adopter-artifact-broken-build-gate"
cp -a "$artifact_adopter" "$broken_artifact_adopter"
sed -i 's/needs: \[verify, snapshot\]$/needs: verify/' \
  "$broken_artifact_adopter/.github/workflows/release.yml"
git -C "$broken_artifact_adopter" commit -aqm 'drop the build job ordering gate'
schedule_adopter_script "$broken_artifact_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release-artifact caller whose build job dropped needs: [verify, snapshot]"
else
  grep -qF 'does not gate the build matrix on both verification and snapshot state' "$ADOPTER_LOG" \
    && pass "emitted suite rejects a release-artifact caller with a broken build-job gate" \
    || fail "emitted suite rejected the broken build job, but for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

# Reproduces a real, empirically-verified exploit: the generated contract-test
# must reject a build job hand-edited to escalate permissions or to smuggle in
# the release App private key — the credential that mints main-protection-bypass
# tokens — since scripts/release-build.sh runs adopter-owned (potentially
# third-party) build tooling on caller-chosen runners (#975 review finding).
escalated_permissions_adopter="$tmproot/adopter-artifact-escalated-permissions"
cp -a "$artifact_adopter" "$escalated_permissions_adopter"
awk '
  /^  build:[[:space:]]*$/ { in_build = 1 }
  in_build && /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ && $0 !~ /^  build:/ { in_build = 0 }
  in_build && /^    permissions:[[:space:]]*$/ { print; getline; sub(/contents: read/, "contents: write"); print; next }
  { print }
' "$escalated_permissions_adopter/.github/workflows/release.yml" \
  >"$escalated_permissions_adopter/.github/workflows/release.yml.new"
mv "$escalated_permissions_adopter/.github/workflows/release.yml.new" \
  "$escalated_permissions_adopter/.github/workflows/release.yml"
awk '
  /^  build:[[:space:]]*$/ { in_build = 1 }
  in_build && /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ && $0 !~ /^  build:/ { in_build = 0 }
  in_build { print }
' "$escalated_permissions_adopter/.github/workflows/release.yml" \
  | grep -F 'contents: write' >/dev/null \
  || fail "test setup did not actually escalate the build job's permissions to contents: write"
git -C "$escalated_permissions_adopter" commit -aqm 'escalate the build job to contents: write'
schedule_adopter_script "$escalated_permissions_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release-artifact caller whose build job was escalated to contents: write"
else
  grep -qF 'build job grants more than contents-read' "$ADOPTER_LOG" \
    && pass "emitted suite rejects a release-artifact caller with an escalated build-job permission" \
    || fail "emitted suite rejected the escalated build job, but for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

leaked_secret_adopter="$tmproot/adopter-artifact-leaked-secret"
cp -a "$artifact_adopter" "$leaked_secret_adopter"
sed -i \
  's/RELEASE_VERSION: \${{ needs.verify.outputs.version }}/RELEASE_VERSION: ${{ needs.verify.outputs.version }}\n          RELEASE_APP_PRIVATE_KEY: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}/' \
  "$leaked_secret_adopter/.github/workflows/release.yml"
sync_last_rejection
grep -qF 'RELEASE_APP_PRIVATE_KEY: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}' \
  "$leaked_secret_adopter/.github/workflows/release.yml" \
  || fail "test setup did not actually inject RELEASE_APP_PRIVATE_KEY into the build step's env"
git -C "$leaked_secret_adopter" commit -aqm 'leak the release App private key into the build step env'
schedule_adopter_script "$leaked_secret_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release-artifact caller whose build step env leaked RELEASE_APP_PRIVATE_KEY"
else
  grep -qF 'build job references a secrets context' "$ADOPTER_LOG" \
    && pass "emitted suite rejects a release-artifact caller with a release-App secret leaked into the build job" \
    || fail "emitted suite rejected the leaked secret, but for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

# A second independent review found the dot-accessor-only pattern above
# (secrets\.) is itself bypassable three ways, all reproduced end-to-end
# against a real generated caller before this fix: bracket-index access,
# toJSON(secrets), and workflow-level env: indirection. Each must now be
# rejected too (#975 review finding, round 2).

bracket_secret_adopter="$tmproot/adopter-artifact-bracket-secret"
cp -a "$artifact_adopter" "$bracket_secret_adopter"
sed -i \
  "s/RELEASE_VERSION: \${{ needs.verify.outputs.version }}/RELEASE_VERSION: \${{ needs.verify.outputs.version }}\n          RELEASE_APP_PRIVATE_KEY: \${{ secrets['RELEASE_APP_PRIVATE_KEY'] }}/" \
  "$bracket_secret_adopter/.github/workflows/release.yml"
sync_last_rejection
grep -qF "RELEASE_APP_PRIVATE_KEY: \${{ secrets['RELEASE_APP_PRIVATE_KEY'] }}" \
  "$bracket_secret_adopter/.github/workflows/release.yml" \
  || fail "test setup did not actually inject a bracket-syntax secrets[...] reference into the build step's env"
git -C "$bracket_secret_adopter" commit -aqm 'leak the release App private key via secrets[...] bracket syntax'
schedule_adopter_script "$bracket_secret_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release-artifact caller whose build step env leaked a secret via bracket syntax (secrets['NAME'])"
else
  grep -qF 'build job references a secrets context' "$ADOPTER_LOG" \
    && pass "emitted suite rejects a release-artifact caller leaking a secret via bracket syntax" \
    || fail "emitted suite rejected the bracket-syntax leak, but for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

tojson_secret_adopter="$tmproot/adopter-artifact-tojson-secret"
cp -a "$artifact_adopter" "$tojson_secret_adopter"
sed -i \
  's/RELEASE_VERSION: \${{ needs.verify.outputs.version }}/RELEASE_VERSION: ${{ needs.verify.outputs.version }}\n          ALL_SECRETS: ${{ toJSON(secrets) }}/' \
  "$tojson_secret_adopter/.github/workflows/release.yml"
sync_last_rejection
grep -qF 'ALL_SECRETS: ${{ toJSON(secrets) }}' \
  "$tojson_secret_adopter/.github/workflows/release.yml" \
  || fail "test setup did not actually inject a toJSON(secrets) dump into the build step's env"
git -C "$tojson_secret_adopter" commit -aqm 'dump every secret into the build step env via toJSON(secrets)'
schedule_adopter_script "$tojson_secret_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release-artifact caller whose build step env dumped the entire secrets context via toJSON(secrets)"
else
  grep -qF 'build job references a secrets context' "$ADOPTER_LOG" \
    && pass "emitted suite rejects a release-artifact caller dumping secrets via toJSON(secrets)" \
    || fail "emitted suite rejected the toJSON(secrets) leak, but for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

github_token_build_adopter="$tmproot/adopter-artifact-github-token-build"
cp -a "$artifact_adopter" "$github_token_build_adopter"
python3 - "$github_token_build_adopter/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("  build:\n")
end = text.index("\n  publish:", start)
needle = "          RELEASE_VERSION: ${{ needs.verify.outputs.version }}\n"
token_index = text.find(needle, start, end)
if token_index < 0:
    raise SystemExit("build GitHub token fixture no longer matches generated output")
replacement = needle + "          PRIVATE_GITHUB_TOKEN: ${{ format('{{{0}}}', github.token) }}\n"
text = text[:token_index] + replacement + text[token_index + len(needle):]
open(path, "w", encoding="utf-8").write(text)
PY
sync_last_rejection
grep -qF "PRIVATE_GITHUB_TOKEN: \${{ format('{{{0}}}', github.token) }}" \
  "$github_token_build_adopter/.github/workflows/release.yml" \
  || fail "test setup did not inject a wrapped github.token into the release-artifact build job"
git -C "$github_token_build_adopter" commit -aqm 'leak wrapped github.token into build environment'
schedule_adopter_script "$github_token_build_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release-artifact caller exposing github.token to its build job"
else
  grep -qF 'build job references a secrets context' "$ADOPTER_LOG" \
    && pass "emitted suite rejects a release-artifact build job receiving github.token" \
    || fail "emitted suite rejected the build token leak, but for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

github_context_build_adopter="$tmproot/adopter-artifact-github-context-build"
cp -a "$artifact_adopter" "$github_context_build_adopter"
python3 - "$github_context_build_adopter/.github/workflows/release.yml" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
start = text.index("  build:\n")
end = text.index("\n  publish:", start)
needle = "          RELEASE_VERSION: ${{ needs.verify.outputs.version }}\n"
token_index = text.find(needle, start, end)
if token_index < 0:
    raise SystemExit("build GitHub context fixture no longer matches generated output")
replacement = needle + "          PRIVATE_GITHUB_CONTEXT: ${{ toJSON(github) }}\n"
text = text[:token_index] + replacement + text[token_index + len(needle):]
open(path, "w", encoding="utf-8").write(text)
PY
sync_last_rejection
grep -qF 'PRIVATE_GITHUB_CONTEXT: ${{ toJSON(github) }}' \
  "$github_context_build_adopter/.github/workflows/release.yml" \
  || fail "test setup did not inject a serialized github context into the release-artifact build job"
git -C "$github_context_build_adopter" commit -aqm 'leak github context into build environment'
schedule_adopter_script "$github_context_build_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release-artifact caller serializing the github context in its build job"
else
  grep -qF 'build job references a secrets context' "$ADOPTER_LOG" \
    && pass "emitted suite rejects a release-artifact build job serializing github context" \
    || fail "emitted suite rejected the serialized GitHub context, but for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

toplevel_env_secret_adopter="$tmproot/adopter-artifact-toplevel-env-secret"
cp -a "$artifact_adopter" "$toplevel_env_secret_adopter"
awk '
  /^jobs:[[:space:]]*$/ && !done {
    print "env:"
    print "  RELEASE_APP_PRIVATE_KEY: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}"
    print ""
    done = 1
  }
  { print }
' "$toplevel_env_secret_adopter/.github/workflows/release.yml" \
  >"$toplevel_env_secret_adopter/.github/workflows/release.yml.new"
mv "$toplevel_env_secret_adopter/.github/workflows/release.yml.new" \
  "$toplevel_env_secret_adopter/.github/workflows/release.yml"
sed -i \
  's/RELEASE_VERSION: \${{ needs.verify.outputs.version }}/RELEASE_VERSION: ${{ needs.verify.outputs.version }}\n          RELEASE_APP_PRIVATE_KEY: ${{ env.RELEASE_APP_PRIVATE_KEY }}/' \
  "$toplevel_env_secret_adopter/.github/workflows/release.yml"
sync_last_rejection
grep -qE '^env:[[:space:]]*$' "$toplevel_env_secret_adopter/.github/workflows/release.yml" \
  || fail "test setup did not actually add a workflow-level env: block"
sync_last_rejection
grep -qF 'RELEASE_APP_PRIVATE_KEY: ${{ env.RELEASE_APP_PRIVATE_KEY }}' \
  "$toplevel_env_secret_adopter/.github/workflows/release.yml" \
  || fail "test setup did not actually make the build step consume the workflow-level env indirection"
# The literal word "secrets" never appears inside the build: job block itself
# in this bypass — it only appears in the workflow-level env: block the build
# job indirectly reads via ${{ env.RELEASE_APP_PRIVATE_KEY }} — so this proves
# the per-job secrets scan alone cannot catch it; only the workflow-level env:
# block rejection can.
build_job_slice="$(awk '
  /^  build:[[:space:]]*$/ { in_job = 1 }
  in_job && /^  [A-Za-z0-9_.-]+:[[:space:]]*$/ && $0 !~ /^  build:/ { exit }
  in_job { print }
' "$toplevel_env_secret_adopter/.github/workflows/release.yml")"
! grep -q 'secrets' <<<"$build_job_slice" \
  || fail "test setup leaked the literal word secrets into the build job block, which would make this test pass for the wrong reason"
git -C "$toplevel_env_secret_adopter" commit -aqm 'leak the release App private key via a workflow-level env: indirection'
schedule_adopter_script "$toplevel_env_secret_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release-artifact caller smuggling a secret through a workflow-level env: block"
else
  grep -qF 'declares a workflow-level env: block' "$ADOPTER_LOG" \
    && pass "emitted suite rejects a release-artifact caller smuggling a secret through a workflow-level env: block" \
    || fail "emitted suite rejected the workflow-level env indirection, but for the wrong reason: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

# --------------------------------------------------------------------------
# release-snapshot (#1206): a snapshot-only release caller for adopters that
# publish nothing from the release workflow — concretely, a repository whose
# container images are published by a separate, independently triggered
# workflow, but which still cuts versioned releases and therefore accumulates
# NEXT/ fragments that must be consumed into CHANGELOG/<version>.md. Before this
# mode, "no release caller at all" was their only option, which left NEXT/
# permanently unconsumed.
# --------------------------------------------------------------------------

snapshot_release="$(bash "$gen" release-snapshot "$sha")"
release_auth_environment_note='# The step clears shell startup, loader, and Git settings that could alter this lookup.'
release_auth_isolation_note='# Run Git with env -i and send its auth header over stdin; do not export it.'
sync_last_rejection
grep -qF -- "$release_auth_environment_note" <<<"$release_node_workflow" \
  && grep -qF -- "$release_auth_isolation_note" <<<"$release_node_workflow" \
  && pass "release-node documents its isolated release-state Git environment (#1715)" \
  || fail "release-node omits the release-state environment explanation (#1715)"
sync_last_rejection
grep -qF -- "$release_auth_environment_note" <<<"$artifact_release" \
  && grep -qF -- "$release_auth_isolation_note" <<<"$artifact_release" \
  && pass "release-artifact documents its isolated release-state Git environment (#1715)" \
  || fail "release-artifact omits the release-state environment explanation (#1715)"
sync_last_rejection
grep -qF -- "$release_auth_environment_note" <<<"$snapshot_release" \
  && grep -qF -- "$release_auth_isolation_note" <<<"$snapshot_release" \
  && pass "release-snapshot documents its isolated release-state Git environment (#1715)" \
  || fail "release-snapshot omits the release-state environment explanation (#1715)"
sync_last_rejection
grep -qE '^  verify:$' <<<"$snapshot_release" \
  && grep -qE '^  snapshot:$' <<<"$snapshot_release" \
  && grep -qE '^  publish:$' <<<"$snapshot_release" \
  && ! grep -qE '^  build:$' <<<"$snapshot_release" \
  && ! grep -q 'uses:.*node-release\.yml' <<<"$snapshot_release" \
  && pass "release-snapshot emits verify/snapshot/publish with no build or publication stage" \
  || fail "release-snapshot did not emit the snapshot-only release shape"

# #1616: the publish job runs in its own job context, so it cannot read
# `steps.release-version` — that step ID exists only inside the `verify` job.
# Regression: publish's tag/note verification step once read the version from
# `steps.release-version.outputs.version` (always empty there), so the exact-tag
# check failed on every release.
# A snapshot-only adopter need not be a Node project (#1206): the Node steps are
# guarded on package.json and a repository without one must verify through its
# own scripts/release-verify.sh rather than falling back to npm test.
snapshot_verify_job="$(awk '/^  verify:[[:space:]]*$/{seen=1} /^  snapshot:[[:space:]]*$/{seen=0} seen' <<<"$snapshot_release")"
[ "$(grep -cF "&& hashFiles('package.json') != ''" <<<"$snapshot_verify_job")" -eq 4 ] \
  && grep -qF 'elif [ ! -f package.json ]; then' <<<"$snapshot_verify_job" \
  && grep -qF 'No package.json and no executable scripts/release-verify.sh' <<<"$snapshot_verify_job" \
  && pass "release-snapshot guards setup-node, npm ci, lifecycle execution, and version stamping on package.json and fails closed without a verify hook (#1206)" \
  || fail "release-snapshot still assumes a Node project: a non-npm adopter would fail at npm ci or verify nothing"
snapshot_publish_job="$(awk '/^  publish:[[:space:]]*$/{seen=1} seen' <<<"$snapshot_release")"
sync_last_rejection
grep -qF 'VERSION: ${{ needs.verify.outputs.version }}' <<<"$snapshot_publish_job" \
  && ! grep -qF 'steps.release-version' <<<"$snapshot_publish_job" \
  && pass "release-snapshot publish job resolves VERSION from needs.verify, not the unreachable verify-job step" \
  || fail "release-snapshot publish job references steps.release-version, which does not exist outside the verify job (#1616)"

build_snapshot_adopter() {
  # An adopter that consumes NEXT/ but publishes nothing from the release
  # workflow: no package, no GitHub Release assets, no release-build.sh hook.
  local dir="$1"
  mkdir -p "$dir/NEXT" "$dir/scripts" "$dir/.github/workflows"
  bash "$gen" codeowners "$sha" >"$dir/.github/CODEOWNERS"
  bash "$gen" renderer "$sha" >"$dir/scripts/render-next.sh"
  bash "$gen" workflow "$sha" >"$dir/.github/workflows/changelog.yml"
  bash "$gen" pr-gate "$sha" >"$dir/.github/workflows/changelog-contract.yml"
  cp "$emitted" "$dir/scripts/changelog-contract.test.sh"
  chmod +x "$dir/scripts/render-next.sh" "$dir/scripts/changelog-contract.test.sh"
  bash "$gen" release-snapshot "$sha" >"$dir/.github/workflows/release.yml"
  cat >"$dir/NEXT/2026-09-01-issue-1206-first.md" <<'FRAGMENT'
---
date: 2026-09-01
issue: 1206
title: 'fix(caller): first snapshot-only entry'
---

Body.
FRAGMENT
  git -C "$dir" init -q
  git -C "$dir" config user.name Test
  git -C "$dir" config user.email test@example.com
  git -C "$dir" add -A
  git -C "$dir" commit -qm initial
}

snapshot_adopter="$tmproot/adopter-snapshot"
build_snapshot_adopter "$snapshot_adopter"
schedule_adopter_script "$snapshot_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  pass "emitted suite accepts a generated release-snapshot caller"
else
  fail "emitted suite rejects a generated release-snapshot caller: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"
assert_mutable_verification_path_rejected "$snapshot_adopter" release-snapshot snapshot-mutable-path
assert_release_path_mutation_rejected "$snapshot_adopter" release-snapshot snapshot-capture-path-override \
  add_capture_path_override 'overrides the runner-managed PATH before release verification (#1717)'
assert_release_path_mutation_rejected "$snapshot_adopter" release-snapshot snapshot-job-path-override \
  add_verify_job_path_override 'overrides the runner-managed PATH before release verification (#1717)'
assert_release_path_mutation_rejected "$snapshot_adopter" release-snapshot snapshot-late-path-capture \
  move_capture_after_lifecycle_scripts 'does not capture trusted release verification runtime inputs (#1717)'

expected_release_fallback='    fail "$release_workflow is not a generated release caller at $CONTRACT_REF. Inspect the existing release workflow and repository configuration to determine its mode and all custom generator options. Regenerate the complete caller set at $CONTRACT_REF in a clean temporary checkout, review the full diff, then replace the committed set together. Supported release modes are release-node, release-artifact for GitHub Release assets, and release-snapshot when the release workflow publishes nothing."'
sync_last_rejection
grep -Fqx "$expected_release_fallback" "$emitted" \
  && pass "release-specific unknown-provenance fallback is prose-only" \
  || fail "release-specific unknown-provenance fallback diverged or included an unapproved command"

unknown_release="$tmproot/adopter-unrecognized-release"
build_adopter "$unknown_release" yes generated-artifacts-with-adr-index
sed -i '/^# Generated by verJSON\/\.github scripts\/gen-changelog-caller\.sh release-node /d' \
  "$unknown_release/.github/workflows/release.yml"
schedule_adopter_script "$unknown_release" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release workflow without recognized provenance"
else
  unknown_release_finding="$(grep -F 'Affected member: .github/workflows/release.yml;' "$ADOPTER_LOG")"
  unknown_release_guidance="${unknown_release_finding#*Affected member: .github/workflows/release.yml; }"
  expected_unknown_release_guidance='its mode cannot be established by this check; possible modes are release-node, release-artifact, release-snapshot. Inspect the existing caller and related generated artifacts to derive the exact mode and all custom generator options; preserve them. Generate into a clean temporary checkout, review the full diff, then replace the committed set together. This diagnostic intentionally prints no single-file command. Name the mode this repository already adopted: release-artifact publishes GitHub Release assets, and release-snapshot publishes nothing from the release workflow.'
  [ "$unknown_release_guidance" = "$expected_unknown_release_guidance" ] \
    && [ -s "$unknown_release/.github/workflows/release.yml" ] \
    && pass "unrecognized release provenance is rejected with exact prose-only guidance" \
    || fail "unrecognized release provenance guidance diverged or included a command: ${unknown_release_guidance:-<no release guidance>}"
fi
SCHED_SCRIPT
)"

# --------------------------------------------------------------------------
# release-node refuses an unpublishable package BEFORE the snapshot (#1206).
#
# node-release.yml only ever runs as `publish`, so by the time it can read
# package.json, changelog-release.yml has already consumed NEXT/, written the
# immutable CHANGELOG/<version>.md, committed, tagged and pushed. Failing there
# leaves a permanently red run over a release that completed and cannot be
# re-cut. The refusal therefore also lives in `verify`, which `snapshot`
# declares in `needs:` — the only place in the caller that still precedes the
# irreversible push. The step is executed here rather than grepped for: a
# diagnostic present as text but unreachable as code is the failure this
# assertion exists to catch.
# --------------------------------------------------------------------------

node_release="$(bash "$gen" release-node "$sha")"
private_guard="$tmproot/private-guard.sh"
awk '
  /^  snapshot:$/ { exit }
  /^      - name: Refuse a package this release can never publish$/ { found = 1; next }
  found && /^        run: \|$/ { body = 1; next }
  body && /^      - / { exit }
  body { sub(/^          /, ""); print }
' <<<"$node_release" >"$private_guard"

run_private_guard() {
  ( cd "$1" && bash -euo pipefail "$private_guard" ) >"$tmproot/guard.out" 2>&1
}

if [ -s "$private_guard" ] && bash -n "$private_guard" 2>"$tmproot/guard.syntax"; then
  guard_ok="$tmproot/guard-publishable"
  mkdir -p "$guard_ok"
  printf '%s\n' '{"name":"@verjson/thing","version":"0.0.0"}' >"$guard_ok/package.json"
  guard_private="$tmproot/guard-private"
  mkdir -p "$guard_private"
  printf '%s\n' '{"name":"@verjson/thing","version":"0.0.0","private":true}' \
    >"$guard_private/package.json"
  run_private_guard "$guard_ok" \
    && pass "release-node's pre-snapshot guard passes a publishable package" \
    || fail "release-node's pre-snapshot guard rejected a publishable package: $(cat "$tmproot/guard.out")"
  if run_private_guard "$guard_private"; then
    fail "release-node's pre-snapshot guard accepted a private:true package"
  else
    grep -qi 'private' "$tmproot/guard.out" \
      && pass "release-node refuses a private:true package before the snapshot, naming the cause" \
      || fail "release-node's pre-snapshot refusal does not name private: $(cat "$tmproot/guard.out")"
  fi
else
  fail "release-node emits no executable pre-snapshot guard against an unpublishable package"
fi

stripped_guard_adopter="$tmproot/adopter-stripped-private-guard"
build_adopter "$stripped_guard_adopter"
WORKFLOW="$stripped_guard_adopter/.github/workflows/release.yml" python3 - <<'PY'
import os

path = os.environ["WORKFLOW"]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
start = next(
    i
    for i, line in enumerate(lines)
    if line.strip() == "- name: Refuse a package this release can never publish"
)
end = next(
    i for i in range(start + 1, len(lines)) if lines[i].startswith("      - ")
)
open(path, "w", encoding="utf-8").writelines(lines[:start] + lines[end:])
PY
schedule_adopter_script "$stripped_guard_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a release-node caller with its pre-snapshot refusal deleted"
else
  pass "emitted suite rejects a release-node caller whose pre-snapshot refusal was deleted (#1206)"
fi
SCHED_SCRIPT
)"


# --------------------------------------------------------------------------
# The generated adopter set is atomic (#1369, requirement 5)
# --------------------------------------------------------------------------
#
# The set is one artifact spread across several files, all pinned to one
# contract commit. Regenerating a strict subset leaves the rest behind, and
# until now the invariant "these move together" was carried only by prose in
# the generated headers and by a Renovate grouping rule -- neither of which
# reddens when a human regenerates half the set by hand.
#
# `stale_pin` reproduces exactly that: the named member keeps the pin it had
# before the others advanced. A real older commit is not used because CI
# checks this repository out at fetch-depth 1, so HEAD~1 does not exist there;
# the comparison under test is between the pin a member CLAIMS and the pin the
# set was generated at, and a rewritten claim exercises it identically.
stale_contract_ref="00112233445566778899aabbccddeeff00112233"

stale_pin() { # stale_pin <dir> <member-path>
  # Only the two pin-declaration forms the generator emits are rewritten. A
  # blanket 40-hex substitution would also truncate CONTRACT_SHA256, and the
  # case would then pass for the wrong reason.
  sed -i -E \
    -e "/^# Generated by verJSON\\/\\.github scripts\\/gen-changelog-caller\\.sh /s/[0-9a-f]{40}/$stale_contract_ref/" \
    -e "/^# CONTRACT_REF=/s/[0-9a-f]{40}/$stale_contract_ref/" \
    -e "/^CONTRACT_REF=\"/s/[0-9a-f]{40}/$stale_contract_ref/" \
    "$1/$2"
}

# The other pin-declaration mutator, defined beside stale_pin because both
# rewrite the same two declaration forms and both are used from more than one
# section: this one leaves a member claiming two pins, which is the only arm
# that can report the suite's own copy.
duplicate_pin_line() {
  sed -i -E -e '/^CONTRACT_REF="/p' "$1"
}

set_atomicity_seq=0
expect_set_divergence_named() {
  # expect_set_divergence_named <member-path>
  local member="$1" caller="${2:-workflow}" dir
  set_atomicity_seq=$((set_atomicity_seq + 1))
  dir="$tmproot/set-atomicity-$set_atomicity_seq"
  build_adopter "$dir" yes "$caller"
  stale_pin "$dir" "$member"
  if run_adopter "$dir"; then
    fail "emitted suite accepted a set with $member left at an earlier contract commit"
    return
  fi
  grep -qF "$member" "$tmproot/run.out" \
    && grep -qF "$stale_contract_ref" "$tmproot/run.out" \
    && grep -qF "$sha" "$tmproot/run.out" \
    && pass "a subset regeneration naming $member reports both contract commits" \
    || fail "rejecting $member did not name the member and both commits: $(tr '\n' ' ' <"$tmproot/run.out" | tail -c 400)"
}

# Every member of the set, not just the one that motivated the check. A member
# the enumeration forgets is a member a subset regeneration may leave behind.
expect_set_divergence_named .github/workflows/changelog.yml
expect_set_divergence_named .github/workflows/changelog-contract.yml
expect_set_divergence_named .github/workflows/renovate-changelog.yml
expect_set_divergence_named .github/workflows/release.yml
expect_set_divergence_named .github/workflows/release-propose.yml
expect_set_divergence_named scripts/render-next.sh
expect_set_divergence_named scripts/gen-adr-index.sh generated-artifacts-with-adr-index
expect_set_divergence_named scripts/gen-adr-index.test.sh generated-artifacts-with-adr-index

# Every enumerated member is a path the generator can actually WRITE. A member
# no mode produces is dead enumeration that ships to ~95 repositories: it can
# never fire, and the remedy it would print names a mode that does not write
# that path. The generator's usage block is the authority on which mode writes
# where, so the enumeration is checked against it rather than against a second
# hand-kept list that can drift the same way.
generator_output_paths="$(awk '
  /# Usage:/ { in_usage = 1; next }
  in_usage {
    line = $0
    sub(/^[[:space:]]*#[[:space:]]*/, "", line)
    if (line !~ /^scripts\/gen-changelog-caller\.sh /) { in_usage = 0; next }
    sub(/^scripts\/gen-changelog-caller\.sh [^ ]+ /, "", line)
    if (index(line, " > ")) sub(/^.* > /, "", line)
    else sub(/^.* /, "", line)
    sub(/[[:space:]]+#.*/, "", line)
    print line
  }
' "$gen")"
enumerated_members="$(sed -nE 's|^generated_set_check +([^ ]+) .*$|\1|p' "$emitted")"
if grep -qF 'flags="${6:-}"' "$emitted"; then
  fail "the emitted generated-set checker retains an unused generator flags argument"
else
  pass "the emitted generated-set checker omits the unused generator flags argument"
fi
if grep -qF "'generated-artifacts|generated-artifacts-with-adr-index|workflow' ''" "$emitted"; then
  fail "the changelog caller retains an empty argument before its remedy note"
else
  pass "the changelog caller passes its mode-specific remedy note in the right position"
fi
if grep -qF "'release-node|release-artifact|release-snapshot' ''" "$emitted"; then
  fail "the release caller retains an empty argument before its remedy note"
else
  pass "the release caller passes its mode-specific remedy note in the right position"
fi
unwritable_members=''
while read -r member; do
  [ -n "$member" ] || continue
  grep -qxF "$member" <<<"$generator_output_paths" || unwritable_members="$unwritable_members $member"
done <<<"$enumerated_members"
[ -n "$enumerated_members" ] \
  || fail "no set members were enumerated; the extraction above stopped matching the emitted suite"
[ -z "$unwritable_members" ] \
  && pass "every enumerated set member is a path some generator mode writes" \
  || fail "the set enumerates members no generator mode writes:$unwritable_members"

# Every changelog-contract generator output is covered by the set check.
# `codeowners` is a required member (ADR 0210) but carries no pin header, so it
# is held by its own digest check in the emitted suite rather than by the
# header-pin set enumeration below.
contract_generator_output_paths="$(grep -Fvx '.github/CODEOWNERS' <<<"$generator_output_paths" | sort -u)"
unlisted_outputs=''
while read -r output; do
  [ -n "$output" ] || continue
  grep -qxF "$output" <<<"$enumerated_members" || unlisted_outputs="$unlisted_outputs $output"
done <<<"$contract_generator_output_paths"
[ -n "$contract_generator_output_paths" ] \
  || fail "no contract generator outputs discovered; extraction above stopped matching usage"
[ -z "$unlisted_outputs" ] \
  && pass "every contract generator output is enumerated for set validation" \
  || fail "contract generator outputs are missing from set validation:$unlisted_outputs"

# Repair guidance cannot safely reproduce every adopter mode and option.
# Require a verified mode hint and full-set instructions, with no command.
known_remedy="$tmproot/adopter-changelog-remedy"
build_adopter "$known_remedy" yes generated-artifacts-with-adr-index
stale_pin "$known_remedy" .github/workflows/changelog.yml
run_adopter "$known_remedy"
sync_last_rejection
known_finding="$(grep -F '.github/workflows/changelog.yml is still at' "$tmproot/run.out")"
known_guidance="${known_finding#*Affected member: .github/workflows/changelog.yml; }"
expected_known_guidance='mode hint: generated-artifacts-with-adr-index; verify it against existing artifacts. Inspect the existing caller and related generated artifacts to derive the exact mode and all custom generator options; preserve them. Generate into a clean temporary checkout, review the full diff, then replace the committed set together. This diagnostic intentionally prints no single-file command. Name the mode this repository already adopted: generated-artifacts-with-adr-index also wires adr-index: true and the pinned scripts/gen-adr-index.sh, and workflow is the compatibility alias.'
[ "$known_guidance" = "$expected_known_guidance" ] \
  && pass "known-mode repair guidance matches the approved prose exactly" \
  || fail "known-mode repair guidance diverged: $known_guidance"

unknown_remedy="$tmproot/adopter-changelog-remedy-unknown"
build_adopter "$unknown_remedy" yes generated-artifacts-with-adr-index
rm -f "$unknown_remedy/.github/workflows/changelog.yml"
run_adopter "$unknown_remedy"
sync_last_rejection
unknown_finding="$(grep -F '.github/workflows/changelog.yml is absent' "$tmproot/run.out")"
unknown_guidance="${unknown_finding#*Affected member: .github/workflows/changelog.yml; }"
expected_unknown_guidance='its mode cannot be established by this check; possible modes are generated-artifacts, generated-artifacts-with-adr-index, workflow. Inspect the existing caller and related generated artifacts to derive the exact mode and all custom generator options; preserve them. Generate into a clean temporary checkout, review the full diff, then replace the committed set together. This diagnostic intentionally prints no single-file command. Name the mode this repository already adopted: generated-artifacts-with-adr-index also wires adr-index: true and the pinned scripts/gen-adr-index.sh, and workflow is the compatibility alias.'
[ "$unknown_guidance" = "$expected_unknown_guidance" ] \
  && pass "unknown-mode repair names possible modes and exact safe guidance" \
  || fail "unknown-mode repair guidance diverged or included a command: ${unknown_guidance:-<no changelog.yml finding>}"

# --------------------------------------------------------------------------
# Generated-set repair guidance is prose only (#1369)
# --------------------------------------------------------------------------
#
# These findings cannot reconstruct every adopter's mode-specific options, and
# a shell redirection truncates a target before generation succeeds. The test
# below verifies that every affected member is named and that the diagnostic
# prints no executable generation or redirection command.
paste_safety="$tmproot/adopter-remedy-paste-safety"
build_adopter "$paste_safety" yes generated-artifacts-with-adr-index
# Emptying a member drives the arm that fires whatever pin form it declares, so
# one run collects a finding -- and therefore a remedy -- for every member at
# once. The suite's own copy is excluded because the adopter has to run it.
#
# A member the fixture does not carry is a FAILURE, not a skip. Skipping it
# drops it from $paste_safety_members, which is the very list the non-vacuity
# pin below is computed from -- so a fixture that stopped writing every member
# would satisfy that pin with an empty set and report the paste-safety scan as
# covering members it never emptied. The adopter this section builds is the
# full-set one, so every enumerated member must be present in it by
# construction; if one is not, the enumeration and the fixture have diverged
# and that is the thing worth reporting.
paste_safety_members=''
paste_safety_absent=''
while read -r member; do
  [ -n "$member" ] || continue
  [ "$member" = scripts/changelog-contract.test.sh ] && continue
  if [ ! -f "$paste_safety/$member" ]; then
    paste_safety_absent="$paste_safety_absent $member"
    continue
  fi
  : >"$paste_safety/$member"
  paste_safety_members="$paste_safety_members $member"
done <<<"$enumerated_members"
[ -z "$paste_safety_absent" ] \
  && pass "the paste-safety fixture carries every enumerated set member" \
  || fail "the paste-safety fixture is missing:$paste_safety_absent; those members were never emptied, so the non-vacuity pin below is computed from a short list"
if run_adopter "$paste_safety"; then
  fail "emitted suite accepted generated members emptied to zero bytes"
else
  pass "emitted suite rejects generated members emptied to zero bytes"
fi
paste_safety_unreported=''
for member in $paste_safety_members; do
sync_last_rejection
  grep -qF "Affected member: $member;" "$tmproot/run.out" \
    || paste_safety_unreported="$paste_safety_unreported $member"
done
[ -z "$paste_safety_unreported" ] \
  && pass "every set member emits a remedy this scan can inspect" \
  || fail "no remedy was emitted for:$paste_safety_unreported; the paste-safety scan below is vacuous for them"
# Every generated-set finding carries prose guidance only. This checker
# cannot recover every caller-specific option from the target workflow, and a
# shell redirect truncates the destination before generation succeeds.
sync_last_rejection
remedy_guidance="$(grep -F 'Regenerate the complete generated set' "$tmproot/run.out" || true)"
[ -n "$remedy_guidance" ] \
  && grep -qF 'Generate into a clean temporary checkout, review the full diff, then replace the committed set together.' <<<"$remedy_guidance" \
  && grep -qF 'This diagnostic intentionally prints no single-file command.' <<<"$remedy_guidance" \
  && ! grep -qF 'scripts/gen-changelog-caller.sh ' <<<"$remedy_guidance" \
  && ! grep -qF '>' <<<"$remedy_guidance" \
  && ! grep -qF '|' <<<"$remedy_guidance" \
  && ! grep -qF '`' <<<"$remedy_guidance" \
  && ! grep -qF '&&' <<<"$remedy_guidance" \
  && ! grep -qF '||' <<<"$remedy_guidance" \
  && ! grep -Eq '(^|[[:space:]])(rm|mv|cp|bash|sh|python[0-9]*|git|curl|wget|node)([[:space:]]|$)' <<<"$remedy_guidance" \
  && pass "generated-set findings do not print executable single-file commands or redirects" \
  || fail "generated-set guidance is missing or still prints a command that can truncate an adopter file"

paste_safety_unidentified=''
for member in $paste_safety_members; do
  grep -qF "Affected member: $member;" <<<"$remedy_guidance" \
    || paste_safety_unidentified="$paste_safety_unidentified $member"
done
[ -z "$paste_safety_unidentified" ] \
  && pass "every generated-set member receives its own repair guidance" \
  || fail "no generated-set guidance identifies:$paste_safety_unidentified"

generated_set_check_body="$(awk '/^generated_set_check\(\) \{/,/^\}/' "$gen")"
# That range ends at the first line that is `}` alone, so a dedented brace in
# the body would truncate it silently and the -gt 0 guards below would still
# hold. generated_set_note is called from nowhere else in the generator, so
# requiring the body to carry every call site in the file catches a truncation
# that drops arms, which is the truncation that matters here.
arm_findings="$(grep -c 'generated_set_note ' <<<"$generated_set_check_body" || true)"
arm_findings_in_generator="$(grep -c 'generated_set_note ' "$gen" || true)"
arm_findings_shared_remedy="$(grep -c 'generated_set_note .*\$remedy"$' <<<"$generated_set_check_body" || true)"

# Every line of the body that mentions `remedy` is classified, and a line that
# fits no class FAILS. A line either reads $remedy only, or is the local
# declaration, or is one of exactly two compositions written out here in full.
# There is no fourth class, so an append, a rewrite, a third composition or a
# form nobody has thought of all land in $remedy_unaccounted rather than having
# to be recognised. Exactly one of each composition is required: a second copy
# of one would keep any count-balancing pin happy while changing what an arm
# below it emits.
#
# The reach of this pin is the NAME, and that bound is real. The filter below
# selects lines containing the substring `remedy`, so a write that never spells
# the name escapes it entirely -- assembling the variable name in another
# parameter and writing through `printf -v`, `eval`, `declare -g`, or a
# `local -n` alias. Such a line is not merely unclassified, it is never
# examined. Closing that would mean interpreting the body rather than reading
# it, which is a different kind of check; it is recorded here as a known limit
# so a later reader does not mistake "every form is caught" for what this
# asserts. Every form that does name $remedy is caught and fails closed.
remedy_decls=0
remedy_from_mode=0
remedy_from_declared=0
remedy_unaccounted=''
while IFS= read -r remedy_line; do
  case "$remedy_line" in *remedy*) ;; *) continue ;; esac
  # Two classes of line cannot modify anything, by bash's own parse rather than
  # by this suite's judgement: a line whose first non-blank character is `#`,
  # and the function opener, whose signature comment names the remedy arguments.
  # Everything else is classified in full, `#` included, since a `#` this loop
  # decided to treat as a comment could hide an assignment behind it.
  grep -qE '^ *#' <<<"$remedy_line" && continue
  grep -qE '^generated_set_check\(\) \{ *#' <<<"$remedy_line" && continue
  remedy_rest="${remedy_line//\$remedy/}"
  remedy_rest="${remedy_rest//\$\{remedy\}/}"
  remedy_rest="${remedy_rest//generated_set_remedy/}"
  case "$remedy_rest" in *remedy*) ;; *) continue ;; esac
  if grep -qxE ' *local .* remedy .*' <<<"$remedy_line"; then
    remedy_decls=$((remedy_decls + 1))
  elif grep -qxE ' *remedy="\$\(generated_set_remedy "\$mode" "\$rel" "\$note"\)"' <<<"$remedy_line"; then
    remedy_from_mode=$((remedy_from_mode + 1))
  elif grep -qxE ' *remedy="\$\(generated_set_remedy "\$declared" "\$rel" "\$note"\)"' <<<"$remedy_line"; then
    remedy_from_declared=$((remedy_from_declared + 1))
  else
    remedy_unaccounted="$remedy_unaccounted [$remedy_line]"
  fi
done <<<"$generated_set_check_body"

# The $mode composition is the only one in scope for the first four arms, so it
# must precede the first arm: an arm reached before any composition would append
# an unset $remedy, which no count above would notice.
remedy_mode_line="$(grep -nE ' *remedy="\$\(generated_set_remedy "\$mode"' <<<"$generated_set_check_body" | sed -n '1s/:.*//p')"
remedy_first_arm_line="$(grep -n 'generated_set_note ' <<<"$generated_set_check_body" | sed -n '1s/:.*//p')"

{ [ "$arm_findings" -gt 0 ] \
  && [ "$arm_findings" = "$arm_findings_in_generator" ] \
  && [ "$arm_findings" = "$arm_findings_shared_remedy" ] \
  && [ "$remedy_decls" -eq 1 ] \
  && [ "$remedy_from_mode" -eq 1 ] \
  && [ "$remedy_from_declared" -eq 1 ] \
  && [ -z "$remedy_unaccounted" ] \
  && [ -n "$remedy_mode_line" ] && [ -n "$remedy_first_arm_line" ] \
  && [ "$remedy_mode_line" -lt "$remedy_first_arm_line" ]; } \
  && pass "every generated_set_check arm emits one unmodified composed remedy, so a driven arm stands in for the undriven ones" \
  || fail "generated_set_check no longer emits one unmodified composed remedy for all its arms, so the arms this section does not drive can emit a remedy it never runs ($arm_findings_shared_remedy of $arm_findings findings append \$remedy; the generator holds $arm_findings_in_generator arms in total; $remedy_decls declarations, $remedy_from_mode \$mode and $remedy_from_declared \$declared compositions, each wanted exactly once; first composition at body line ${remedy_mode_line:-none} against first arm at ${remedy_first_arm_line:-none}; lines touching \$remedy that fit no permitted form:${remedy_unaccounted:- none})"

remedy_scan="$tmproot/remedy-scan.out"
sync_last_rejection
cat "$tmproot/run.out" >"$remedy_scan"

custom_modes="$tmproot/adopter-remedy-custom-modes"
build_adopter "$custom_modes" yes generated-artifacts-with-adr-index
bash "$gen" release-artifact "$sha" --build-runner ubuntu-24.04 \
  >"$custom_modes/.github/workflows/release.yml"
bash "$gen" pr-gate "$sha" --untrusted-runner linux-x64 \
  >"$custom_modes/.github/workflows/changelog-contract.yml"
sync_last_rejection
grep -qF -- '--build-runner ubuntu-24.04' "$custom_modes/.github/workflows/release.yml" \
  && pass "custom release-artifact fixture records its build-runner option" \
  || fail "custom release-artifact fixture lost its build-runner option"
sync_last_rejection
grep -qF 'runs-on: [linux-x64]' "$custom_modes/.github/workflows/changelog-contract.yml" \
  && pass "custom pr-gate fixture records its untrusted-runner option" \
  || fail "custom pr-gate fixture lost its untrusted-runner option"
stale_pin "$custom_modes" .github/workflows/release.yml
stale_pin "$custom_modes" .github/workflows/changelog-contract.yml
schedule_adopter_script "$custom_modes" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted custom release callers with stale pins"
else
release_finding="$(grep -F 'Affected member: .github/workflows/release.yml;' "$ADOPTER_LOG")"
release_guidance="${release_finding#*Affected member: .github/workflows/release.yml; }"
pr_gate_finding="$(grep -F 'Affected member: .github/workflows/changelog-contract.yml;' "$ADOPTER_LOG")"
pr_gate_guidance="${pr_gate_finding#*Affected member: .github/workflows/changelog-contract.yml; }"
expected_release_guidance='mode hint: release-artifact; verify it against existing artifacts. Inspect the existing caller and related generated artifacts to derive the exact mode and all custom generator options; preserve them. Generate into a clean temporary checkout, review the full diff, then replace the committed set together. This diagnostic intentionally prints no single-file command. Name the mode this repository already adopted: release-artifact publishes GitHub Release assets, and release-snapshot publishes nothing from the release workflow.'
expected_pr_gate_guidance='mode hint: pr-gate; verify it against existing artifacts. Inspect the existing caller and related generated artifacts to derive the exact mode and all custom generator options; preserve them. Generate into a clean temporary checkout, review the full diff, then replace the committed set together. This diagnostic intentionally prints no single-file command.'
[ "$release_guidance" = "$expected_release_guidance" ] && [ "$pr_gate_guidance" = "$expected_pr_gate_guidance" ] \
  && pass "custom stale caller guidance preserves exact safe prose and mode hints" \
  || fail "custom stale caller guidance diverged or included a command: ${release_guidance:-<no release guidance>} ${pr_gate_guidance:-<no pr-gate guidance>}"
fi
SCHED_SCRIPT
)"

paste_modes="$tmproot/adopter-remedy-paste-modes"
build_adopter "$paste_modes" yes generated-artifacts-with-adr-index
stale_pin "$paste_modes" .github/workflows/changelog.yml
sed -i '/^# Generated by verJSON\/\.github scripts\/gen-changelog-caller\.sh /d' "$paste_modes/.github/workflows/changelog.yml"
schedule_adopter_script "$paste_modes" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a generated caller with unknown mode provenance"
else
  stale_guidance="$(grep -F 'Affected member: .github/workflows/changelog.yml;' "$ADOPTER_LOG")"
  expected_stale_guidance='its mode cannot be established by this check; possible modes are generated-artifacts, generated-artifacts-with-adr-index, workflow. Inspect the existing caller and related generated artifacts to derive the exact mode and all custom generator options; preserve them. Generate into a clean temporary checkout, review the full diff, then replace the committed set together. This diagnostic intentionally prints no single-file command. Name the mode this repository already adopted: generated-artifacts-with-adr-index also wires adr-index: true and the pinned scripts/gen-adr-index.sh, and workflow is the compatibility alias.'
  actual_stale_guidance="${stale_guidance#*Affected member: .github/workflows/changelog.yml; }"
  [ "$actual_stale_guidance" = "$expected_stale_guidance" ] \
    && pass "unknown-mode caller guidance matches approved prose exactly" \
    || fail "unknown-mode caller guidance diverged (actual: ${actual_stale_guidance:-<none>}; expected: $expected_stale_guidance)"
fi
SCHED_SCRIPT
)"

paste_self="$tmproot/adopter-remedy-paste-self"
build_adopter "$paste_self" yes generated-artifacts-with-adr-index
duplicate_pin_line "$paste_self/scripts/changelog-contract.test.sh"
schedule_adopter_script "$paste_self" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted its own duplicate pin"
else
    self_finding="$(grep -F 'Affected member: scripts/changelog-contract.test.sh;' "$ADOPTER_LOG")"
    self_guidance="${self_finding#*Affected member: scripts/changelog-contract.test.sh; }"
    expected_self_guidance='mode hint: contract-test; verify it against existing artifacts. Inspect the existing caller and related generated artifacts to derive the exact mode and all custom generator options; preserve them. Generate into a clean temporary checkout, review the full diff, then replace the committed set together. This diagnostic intentionally prints no single-file command.'
    [ "$self_guidance" = "$expected_self_guidance" ] \
      && pass "the contract-suite duplicate-pin remedy stays prose-only" \
      || fail "the contract-suite duplicate-pin remedy diverged (actual: ${self_guidance:-<none>}; expected: $expected_self_guidance)"
fi
SCHED_SCRIPT
)"

# The header states which MODE produced the file, and that is part of the claim.
# A `changelog.yml` carrying a `pr-gate` header at the correct pin is not the
# changelog caller, and the set-level verdict is advertised as authoritative --
# accepting it would make the block print `ok` for a file it has not identified
# and, when it did fire, print a remedy for the wrong mode. Downstream
# per-member assertions reject this adopter either way, so the assertion is on
# the SET-LEVEL verdict, not on the suite's exit status.
forged_mode="$tmproot/adopter-forged-mode-header"
build_adopter "$forged_mode"
sed -i -E \
  's|^(# Generated by verJSON/\.github scripts/gen-changelog-caller\.sh )[a-z][a-z-]*( [0-9a-f]{40})|\1pr-gate\2|' \
  "$forged_mode/.github/workflows/changelog.yml"
run_adopter "$forged_mode"
sync_last_rejection
{ ! grep -qF "every generated member of the adopter set pins $sha" "$tmproot/run.out" \
  && grep -qF 'the generated adopter set is not atomic' "$tmproot/run.out" \
  && grep -qF '.github/workflows/changelog.yml' "$tmproot/run.out"; } \
  && pass "the set-level verdict refuses a member declaring a mode that does not write it" \
  || fail "the set-level verdict accepted a changelog.yml carrying a pr-gate header at the pin: $(tr '\n' ' ' <"$tmproot/run.out" | tail -c 400)"

for member_mode in \
  'scripts/render-next.sh renderer contract-test' \
  'scripts/changelog-contract.test.sh contract-test renderer' \
  'scripts/gen-adr-index.test.sh adr-index-test renderer'; do
  read -r member expected_mode wrong_mode <<<"$member_mode"
  wrong_mode_adopter="$tmproot/adopter-wrong-mode-${member##*/}"
  caller=workflow
  [ "$member" = scripts/gen-adr-index.test.sh ] && caller=generated-artifacts-with-adr-index
  build_adopter "$wrong_mode_adopter" yes "$caller"
  sed -i -E \
    "s|^(# Generated by verJSON/\\.github scripts/gen-changelog-caller\\.sh )${expected_mode}( [0-9a-f]{40})|\\1${wrong_mode}\\2|" \
    "$wrong_mode_adopter/$member"
  run_adopter "$wrong_mode_adopter"
sync_last_rejection
  if grep -qF "$member declares generator mode '$wrong_mode'" "$tmproot/run.out"; then
    pass "the set-level verdict rejects $member when it declares the valid but wrong mode '$wrong_mode'"
  else
sync_last_rejection
    fail "the set-level verdict accepted $member with wrong generator mode '$wrong_mode': $(tr '\n' ' ' <"$tmproot/run.out" | tail -c 400)"
  fi
done

# Regenerating the WHOLE set together is the conformant case, and it has to be
# asserted positively: a check that only ever reddens proves nothing about the
# state it is supposed to accept.
atomic_adopter="$tmproot/adopter-atomic-set"
build_adopter "$atomic_adopter"
schedule_adopter_script "$atomic_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ] && grep -qF "every generated member of the adopter set pins $sha" "$ADOPTER_LOG"; then
  pass "a whole-set regeneration passes and says so"
else
  fail "a whole-set regeneration did not report the set as atomic: $(tail -2 "$ADOPTER_LOG")"
fi
SCHED_SCRIPT
)"

# Two members left behind at once. The per-member assertions further down stop
# at the first one they reach, which is exactly the reporting the set-level
# check exists to replace: a human fixing one member and re-running should not
# have to discover the second on the next iteration.
two_stale="$tmproot/adopter-two-stale-members"
build_adopter "$two_stale"
stale_pin "$two_stale" scripts/render-next.sh
stale_pin "$two_stale" .github/workflows/changelog-contract.yml
if run_adopter "$two_stale"; then
  fail "emitted suite accepted two members left at an earlier contract commit"
else
sync_last_rejection
  { grep -qF 'scripts/render-next.sh is still at' "$tmproot/run.out" \
    && grep -qF '.github/workflows/changelog-contract.yml is still at' "$tmproot/run.out"; } \
    && pass "a subset regeneration names every divergent member, not just the first" \
    || fail "only one divergent member was named: $(tr '\n' ' ' <"$tmproot/run.out" | tail -c 400)"
fi

# --------------------------------------------------------------------------
# Divergence is reported on positive evidence, never inferred from a quiet scan
# --------------------------------------------------------------------------
#
# A missing member, an unreadable one, a header that cannot be parsed and an
# extraction that yields nothing are each a state in which the check CANNOT
# establish the member was generated at the pin. Reading any of them as
# conformance is the defect class this whole effort exists to close, so each is
# exercised as its own case rather than assumed to fall out of the happy path.
malformed_seq=0
expect_unestablished_pin() {
  # expect_unestablished_pin <label> <member-path> <mutator-fn> [required-phrase]
  #
  # <required-phrase>, where given, pins the WORDING the arm under test emits.
  # Several arms report the same member against the same commit, so the generic
  # assertions below cannot tell which one fired: deleting the multiplicity arm
  # entirely leaves this section green, because the trailing pin-mismatch arm
  # picks the member up and reports it anyway. An arm that only changes the
  # message needs the message asserted or it is not covered at all.
  local label="$1" member="$2" mutator="$3" phrase="${4:-}" seq result
  if ! changelog_caller_case_selected "$label"; then
    caller_assertions_suspended=1
    return 0
  fi
  caller_assertions_suspended=0
  malformed_seq=$((malformed_seq + 1))
  seq="$malformed_seq"
  result="$tmproot/malformed-result-$seq"
  (
    fails=0
    caller_case_nested=1
    ADOPTER_ISOLATED_LOG=1
    expect_unestablished_pin_body "$seq" "$label" "$member" "$mutator" "$phrase"
    printf '%s\n' "$fails" >"$result"
  ) &
  last_rejection_pid="$!"
  last_rejection_log="$tmproot/set-malformed-$seq.contract-out"
  last_rejection_copy=
  last_rejection_origin=
  adopter_job_pids+=("$last_rejection_pid")
  adopter_job_results+=("$result")
  pump_adopter_jobs
}

expect_unestablished_pin_body() {
  local seq="$1" label="$2" member="$3" mutator="$4" phrase="${5:-}" dir
  dir="$tmproot/set-malformed-$seq"
  build_adopter "$dir"
  "$mutator" "$dir/$member"
  if ADOPTER_ISOLATED_LOG=1 run_adopter "$dir"; then
    chmod -R u+rwX "$dir" 2>/dev/null || true
    fail "emitted suite read $label as conformance"
    return
  fi
  { grep -qF 'the generated adopter set is not atomic' "$dir.contract-out" \
    && grep -qF "$member" "$dir.contract-out" \
    && grep -qF "$sha" "$dir.contract-out"; } \
    && pass "$label is reported against the pinned commit, not skipped" \
    || fail "$label reddened for some other reason: $(tr '\n' ' ' <"$dir.contract-out" | tail -c 400)"
  if [ -n "$phrase" ]; then
    grep -qF "$phrase" "$dir.contract-out" \
      && pass "$label is reported in the wording only its own arm emits" \
      || fail "$label was reported by some other arm; \"$phrase\" is absent: $(tr '\n' ' ' <"$dir.contract-out" | tail -c 400)"
  fi
  # chmod 000 would otherwise defeat this file's own cleanup.
  chmod -R u+rwX "$dir" 2>/dev/null || true
}

remove_member() { rm -f "$1"; }
make_unreadable() {
  chmod 000 "$1"
  # Belt and braces for a filesystem that does not honour the mode bits (an
  # ACL, a permissive fuse mount): if the fixture is still readable the case
  # below asserts nothing while printing "ok".
  [ ! -r "$1" ] || fail "make_unreadable left $1 readable; the unreadable-member case would assert nothing"
}
empty_member() { : >"$1"; }
replace_member_with_directory() {
  # `! -f` has no other case: make_unreadable exercises only `! -r`, and its
  # chmod is inert for uid 0, which is what a CI container usually runs as. A
  # directory is not a regular file for root either, so this half of the guard
  # is covered everywhere rather than only on a non-root developer machine.
  rm -f "$1"
  mkdir -p "$1"
}
replace_member_with_dangling_symlink() {
  rm -f "$1"
  ln -s "$1.missing-target" "$1"
}
replace_member_with_live_symlink() {
  local target="$1.target"
  cp "$1" "$target"
  rm -f "$1"
  ln -s "$(basename "$target")" "$1"
}
mangle_pin_line() {
  # The pin declaration survives as prose but stops being a parsable claim.
  sed -i -E \
    -e '/^# Generated by verJSON\/\.github scripts\/gen-changelog-caller\.sh /s/[0-9a-f]{40}/<the pinned commit>/' \
    -e '/^CONTRACT_REF="/s/[0-9a-f]{40}/<the pinned commit>/' \
    "$1"
}

expect_unestablished_pin "a required member deleted outright" scripts/render-next.sh remove_member
# `chmod 000` does not restrict uid 0, so on a container runner running as root
# this fixture is readable and the case passes having exercised nothing. Named
# as a skip rather than quietly left in: a vacuous "ok" here would be the same
# shape of failure as the fail-open it covers.
if [ "$(id -u)" -eq 0 ]; then
  skip "a required member that cannot be read: chmod 000 does not restrict uid 0, so this fixture cannot be built here"
else
  expect_unestablished_pin "a required member that cannot be read" scripts/render-next.sh make_unreadable
fi
expect_unestablished_pin "a required member replaced by a directory" scripts/render-next.sh \
  replace_member_with_directory "is present but not a readable regular file"
expect_unestablished_pin "an optional member replaced by dangling symlink" \
  .github/workflows/renovate-changelog.yml replace_member_with_dangling_symlink \
  "symlink"
expect_unestablished_pin "an optional member replaced by a live symlink" \
  .github/workflows/renovate-changelog.yml replace_member_with_live_symlink \
  "symlink"
replace_parent_with_live_symlink() {
  local target="${1}.target"
  mv "$1" "$target"
  ln -s "$(basename "$target")" "$1"
}

verify_safe_symlink_repair() { # verify_safe_symlink_repair <label> <path> <mutator> <expected-component>
  local label="$1" member="$2" mutator="$3" expected_component="$4"
  local dir="$tmproot/safe-symlink-repair-${label//[^a-zA-Z0-9]/-}"
  local target before after repair sentinel
  build_adopter "$dir"
  target="$dir/$member.target"
  "$mutator" "$dir/$member"
  if [ ! -e "$target" ]; then
    fail "$label symlink fixture did not create a target to protect"
    return
  fi
  before="$(find "$target" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
  if run_adopter "$dir"; then
    fail "$label symlink unexpectedly passed the generated-set check"
    return
  fi
sync_last_rejection
  grep -qF "symlinked path component $expected_component" "$tmproot/run.out" \
    || fail "$label symlink was not identified by its path component"
  [ -L "$dir/$member" ] \
    && pass "$label finding leaves the symlink in place until its repair command is run" \
    || fail "$label generated-set check changed the symlink while reporting it"
  repair="$(awk '
    /Safe path repair preserves the symlink target: `[^`]*`/ {
      sub(/^.*Safe path repair preserves the symlink target: `/, "")
      sub(/`.*/, "")
      print
      exit
    }
sync_last_rejection
  ' "$tmproot/run.out")"
  [ -n "$repair" ] \
    || { fail "$label finding did not provide a safe path repair command"; return; }
  ( cd "$dir" && eval "$repair" ) \
    || { fail "$label safe path repair command failed"; return; }
  after="$(find "$target" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
  [ "$before" = "$after" ] \
    && pass "$label safe path repair removes only the link and preserves its target" \
    || fail "$label safe path repair changed the symlink target"
  [ ! -L "$dir/$member" ] \
    && pass "$label safe path repair leaves no symlink at the generated member path" \
    || fail "$label safe path repair left a symlink at the generated member path"
  if [ -d "$dir/$member" ]; then
    sentinel="$dir/$member/.repair-sentinel"
  else
    sentinel="$dir/$member"
  fi
  printf 'replacement must survive\n' >"$sentinel"
  if ( cd "$dir" && eval "$repair" ); then
    fail "$label stale repair command removed or accepted a non-symlink path"
  elif [ "$(cat "$sentinel")" = 'replacement must survive' ]; then
    pass "$label stale repair command refuses to remove a replacement path"
  else
    fail "$label stale repair command changed the replacement path"
  fi
}

verify_safe_symlink_repair "leaf" .github/workflows/renovate-changelog.yml \
  replace_member_with_live_symlink .github/workflows/renovate-changelog.yml
verify_safe_symlink_repair "parent" .github/workflows \
  replace_parent_with_live_symlink .github/workflows

expect_unestablished_pin "a required member emptied to zero bytes" scripts/render-next.sh empty_member
expect_unestablished_pin "a pin declaration that no longer parses" scripts/render-next.sh mangle_pin_line
expect_unestablished_pin "a member declaring two different pins" scripts/render-next.sh \
  duplicate_pin_line "scripts/render-next.sh declares 2 contract pins"
expect_unestablished_pin "a caller header stripped of its pin" .github/workflows/changelog-contract.yml mangle_pin_line
# This suite is a member of the set it checks, so its own pin claim is read out
# of the file the comparison value was itself assigned from: the "still at"
# arm cannot fire for it. The multiplicity arm can, and it is exactly the
# partial regeneration this section is about -- a human repinning the suite by
# adding a second CONTRACT_REF line instead of regenerating the set leaves a
# file that is half-old and half-new. The self-referential member stays
# enumerated because of this arm, so the arm is asserted rather than assumed.
expect_unestablished_pin "the suite itself declaring two different pins" \
  scripts/changelog-contract.test.sh duplicate_pin_line \
  "scripts/changelog-contract.test.sh declares 2 contract pins"

# --------------------------------------------------------------------------
# A member's header is a claim, never an instruction (#1369)
# --------------------------------------------------------------------------
#
# ~95 adopter repositories run this suite, and the hub reads their generated
# headers. If a header could name what the checker executes, an adopter could
# reach the hub's context through a file it controls. The pin is therefore
# constrained to 40 lowercase hex characters and compared as a string; nothing
# read out of a member is ever evaluated. This plants a header shaped like a
# command substitution and asserts both halves: the suite refuses it, and the
# planted command leaves no trace of having run.
injection_adopter="$tmproot/adopter-header-injection"
build_adopter "$injection_adopter"
injection_witness="$tmproot/header-injection-witness"
rm -f "$injection_witness"
python3 - "$injection_adopter/scripts/render-next.sh" "$injection_witness" <<'INJECT'
import sys

path, witness = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
for i, line in enumerate(lines):
    if line.startswith("CONTRACT_REF="):
        lines[i] = 'CONTRACT_REF="$(touch %s && echo pwned)"\n' % witness
        break
else:
    raise SystemExit("fixture has no CONTRACT_REF line to replace")
open(path, "w", encoding="utf-8").writelines(lines)
INJECT
schedule_adopter_script "$injection_adopter" "$(cat <<'SCHED_SCRIPT'
if [ "$status" -eq 0 ]; then
  fail "emitted suite accepted a header whose pin is a command substitution"
else
  pass "emitted suite refuses a header whose pin is a command substitution"
fi
SCHED_SCRIPT
)"
[ ! -e "$injection_witness" ] \
  && pass "a member's header is read as a claim and never executed" \
  || fail "a command named in an adopter-controlled header was executed by the checker"

drain_adopter_jobs
if [ "$caller_case_count" -lt 1 ]; then
  fail "changelog caller contract recorded no cases"
fi
[ "$fails" -eq 0 ] || exit 1
echo "All tests passed."
