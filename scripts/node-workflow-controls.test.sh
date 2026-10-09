#!/usr/bin/env bash
# Guards the bounded-runtime and runner-aware npm-download-cache contracts from
# #152 and #166.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
ci="$root/.github/workflows/node-ci.yml"
release="$root/.github/workflows/node-release.yml"
composite="$root/.github/actions/setup-verjson-node/action.yml"
actions_ci="$root/scripts/actions-ci-groups.tsv"
actions_ci_workflow="$root/.github/workflows/actions-ci.yml"
docs="$root/docs/node-workflows.md"
fails=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

workflow_input() {
  local workflow="$1" input="$2"
  awk -v input="$input" '
    $0 == "      " input ":" { capture = 1; print; next }
    capture && /^      [a-zA-Z0-9_-]+:$/ { exit }
    capture { print }
  ' "$workflow"
}

composite_input() {
  local input="$1"
  awk -v input="$input" '
    $0 == "  " input ":" { capture = 1; print; next }
    capture && /^  [a-zA-Z0-9_-]+:$/ { exit }
    capture { print }
  ' "$composite"
}

for workflow in "$ci" "$release"; do
  name="$(basename "$workflow")"

  timeout_input="$(workflow_input "$workflow" timeout-minutes)"
  { grep -qF 'type: number' <<<"$timeout_input" \
    && grep -qF 'default: 30' <<<"$timeout_input"; } \
    && pass "$name exposes a numeric 30-minute default bound" \
    || fail "$name does not expose the expected numeric timeout-minutes input"

  cache_input="$(workflow_input "$workflow" cache)"
  { grep -qF 'type: boolean' <<<"$cache_input" \
    && grep -qF 'default: false' <<<"$cache_input"; } \
    && pass "$name defaults Actions npm caching off for persistent runners" \
    || fail "$name does not expose a default-off boolean cache input"

  cache_max_input="$(workflow_input "$workflow" cache-max-mb)"
  { grep -qF 'type: number' <<<"$cache_max_input" \
    && grep -qF 'default: 1024' <<<"$cache_max_input"; } \
    && pass "$name bounds explicitly enabled cache uploads at 1024 MB by default" \
    || fail "$name does not expose the expected cache-max-mb guard"

  dependency_input="$(workflow_input "$workflow" cache-dependency-path)"
  { grep -qF 'type: string' <<<"$dependency_input" \
    && grep -qF 'default: package-lock.json' <<<"$dependency_input"; } \
    && pass "$name defaults its cache key to the root npm lockfile" \
    || fail "$name does not expose cache-dependency-path with the expected default"

  scope_input="$(workflow_input "$workflow" scope)"
  if [ "$workflow" = "$release" ]; then
    { grep -qF 'Required lowercase npm scope' <<<"$scope_input" \
      && grep -qF 'type: string' <<<"$scope_input" \
      && grep -qF "default: '@verjson'" <<<"$scope_input"; } \
      && pass "$name documents its required GitHub Packages scope" \
      || fail "$name does not expose the required publication scope contract"
  else
    { grep -qF 'empty to skip registry auth' <<<"$scope_input" \
      && grep -qF 'type: string' <<<"$scope_input" \
      && grep -qF "default: '@verjson'" <<<"$scope_input"; } \
      && pass "$name documents public-only mode and preserves the private @verjson default" \
      || fail "$name does not expose the expected public/private scope contract"
  fi

  cache_guard="cache: \${{ steps.npm-cache-policy.outputs.enabled == 'true' && 'npm' || '' }}"
  [ "$workflow" = "$ci" ] \
    && cache_guard="cache: \${{ !(inputs.secretless-pr || inputs.secretless-trusted-ref) && inputs.cache && hashFiles(inputs.cache-dependency-path) != '' && 'npm' || '' }}"
  grep -qF "$cache_guard" "$workflow" \
    && pass "$name enables setup-node caching only through its allowed cache policy" \
    || fail "$name does not condition npm caching on its allowed cache policy"
  grep -qF 'cache-dependency-path: ${{ inputs.cache-dependency-path }}' "$workflow" \
    && pass "$name keys setup-node caching by the caller-selected lockfile" \
    || fail "$name does not pass cache-dependency-path to setup-node"
  grep -qF 'package-manager-cache: false' "$workflow" \
    && pass "$name disables setup-node automatic package-manager caching" \
    || fail "$name can bypass the explicit cache/lockfile controls via setup-node auto-caching"
  if [ "$workflow" = "$release" ]; then
    grep -qF "printf 'npm_config_cache=%s" "$workflow"
  else
    grep -qF 'echo "npm_config_cache=$RUNNER_TEMP/verjson-npm-cache" >> "$GITHUB_ENV"' "$workflow"
  fi
  cache_export_status=$?
  { [ "$cache_export_status" -eq 0 ] \
    && grep -qF 'cache_dir="$RUNNER_TEMP/verjson-npm-cache"' "$workflow" \
    && grep -qF 'find "$cache_dir" -mindepth 1 -delete' "$workflow" \
    && grep -qF 'CACHE_MAX_MB: ${{ inputs.cache-max-mb }}' "$workflow"; } \
    && pass "$name scopes and bounds explicitly enabled cache uploads" \
    || fail "$name can archive an accumulated or unbounded persistent-runner npm cache"
  if [ "$workflow" = "$release" ]; then
    grep -qF 'registry-url: https://npm.pkg.github.com' "$workflow" \
      && grep -qF 'scope: ${{ inputs.scope }}' "$workflow" \
      && pass "$name always configures the validated GitHub Packages registry" \
      || fail "$name does not bind publication to GitHub Packages"
  else
    grep -qF "registry-url: \${{ inputs.scope != '' && 'https://npm.pkg.github.com' || '' }}" "$workflow" \
      && grep -qF 'scope: ${{ inputs.scope }}' "$workflow" \
      && pass "$name leaves setup-node registry unset for public-only installs" \
      || fail "$name does not gate GitHub Packages registry setup on a non-empty scope"
  fi
done

[ "$(grep -cF 'timeout-minutes: ${{ inputs.timeout-minutes }}' "$ci")" -eq 3 ] \
  && ! grep -qF 'cleanup-secretless-transfer:' "$ci" \
  && pass "node-ci bounds eligibility, acquisition, and build-test jobs" \
  || fail "node-ci does not apply the caller bound to every job"
python3 - "$release" <<'PY'
from pathlib import Path
import sys
import yaml

def require(condition, message):
    if not condition:
        raise SystemExit(message)

workflow = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
jobs = workflow["jobs"]
expected_timeout = "${{ inputs.timeout-minutes }}"
for job_name in ("prepare", "release"):
    require(jobs[job_name].get("timeout-minutes") == expected_timeout,
            f"{job_name} must carry the caller timeout at job level")

prepare_steps = jobs["prepare"]["steps"]
def one_step(predicate, label):
    matches = [index for index, step in enumerate(prepare_steps) if predicate(step)]
    require(len(matches) == 1, f"expected one {label} step in prepare")
    return matches[0], prepare_steps[matches[0]]

cache_index, cache_step = one_step(
    lambda step: step.get("name") == "Configure the bounded npm cache", "cache configuration")
policy_index, policy_step = one_step(
    lambda step: step.get("id") == "npm-cache-policy", "npm cache policy")
setup_index, setup_step = one_step(
    lambda step: str(step.get("uses", "")).startswith("actions/setup-node@"), "setup-node")
bound_index, bound_step = one_step(
    lambda step: step.get("name") == "Bound npm cache upload", "cache bound")
cache_run = cache_step.get("run", "")
require(policy_step.get("env") == {
            "CACHE_REQUESTED": "${{ inputs.cache }}",
            "HAS_PRIVATE_PACKAGE_TOKEN": "${{ secrets.NODE_AUTH_TOKEN != '' }}",
            "LOCKFILE_MATCHED": "${{ hashFiles(inputs.cache-dependency-path) != '' }}",
        }, "cache policy may receive only the boolean private-token state")
require("HAS_PRIVATE_PACKAGE_TOKEN" in policy_step.get("run", "")
        and "enabled=false" in policy_step.get("run", ""),
        "persistent npm caching must be disabled when private credentials are supplied")
require(cache_step.get("if") == "steps.npm-cache-policy.outputs.enabled == 'true'",
        "cache configuration must depend on the safe cache-policy result")
cache_dir_assignment = cache_run.find('cache_dir="$RUNNER_TEMP/verjson-npm-cache"')
cache_export = cache_run.find("printf 'npm_config_cache=%s\\n' \"$cache_dir\" >> \"$GITHUB_ENV\"")
require(cache_dir_assignment >= 0 and cache_export > cache_dir_assignment,
        "the job-scoped cache path must be assigned before export to GITHUB_ENV")
setup_inputs = setup_step.get("with", {})
require(setup_inputs.get("cache") == "${{ steps.npm-cache-policy.outputs.enabled == 'true' && 'npm' || '' }}",
        "setup-node cache must stay opt-in, lockfile-gated, and disabled with private credentials")
require(setup_inputs.get("cache-dependency-path") == "${{ inputs.cache-dependency-path }}",
        "setup-node cache must use the caller-selected dependency lock")
require(setup_inputs.get("package-manager-cache") is False,
        "setup-node automatic package-manager caching must stay disabled")
require(policy_index < cache_index < setup_index < bound_index,
        "cache policy must precede setup-node and bounded cleanup must follow it")
require(bound_step.get("if") == "always() && steps.npm-cache-policy.outputs.enabled == 'true'",
        "cache cleanup must run after failures only when safe opt-in caching is enabled")
require(bound_step.get("env", {}).get("CACHE_MAX_MB") == "${{ inputs.cache-max-mb }}",
        "cache cleanup must receive the caller size limit")
require('find "$cache_dir" -mindepth 1 -delete' in bound_step.get("run", ""),
        "oversized cache cleanup must remove entries before the setup-node post action")
PY
release_contract_status=$?
if [ "$release_contract_status" -eq 0 ]; then
  pass "node-release scopes bounded cache export and caller timeouts to prepare/publish"
else
  fail "node-release cache export or job-level timeout contract is invalid"
fi

{ grep -qF 'submodules: ${{ (inputs.secretless-pr || inputs.secretless-trusted-ref) && '\''false'\'' || '\''recursive'\'' }}' "$ci" \
  && grep -qF "inputs.schema-dir != ''" "$ci" \
  && grep -qF 'working-directory: ${{ inputs.schema-dir }}' "$ci" \
  && [ "$(grep -cF 'NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}' "$ci")" -ge 2 ]; } \
  && pass "node-ci preserves schema checkout/install and private-package auth" \
  || fail "node-ci regressed schema-submodule or NODE_AUTH_TOKEN wiring"
grep -qF 'run: npm run typecheck --if-present' "$ci" \
  && pass "node-ci runs a declared consumer typecheck without requiring the script" \
  || fail "node-ci does not conditionally enforce the consumer typecheck"
grep -qF 'cache_root="$(mktemp -d "$GITHUB_WORKSPACE/.verjson-changelog-tools.XXXXXX")"' "$ci" \
  && pass "node-ci gives changelog tooling a job-writable cache" \
  || fail "node-ci does not allocate the changelog tool cache beneath the workspace"
grep -qF 'cache_root="$(mktemp -d "$GITHUB_WORKSPACE/.verjson-changelog-tools.XXXXXX")"' "$release" \
  && pass "node-release gives publish builds a job-writable changelog tool cache" \
  || fail "node-release does not allocate the changelog tool cache beneath the workspace"
python3 - "$release" <<'PY' \
  && pass "node-release prepares the changelog cache after checkout cleanup and before release consumers" \
  || fail "node-release changelog cache is exposed to checkout cleanup or prepared too late"
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
steps = doc["jobs"]["release"]["steps"]
checkout = next(i for i, step in enumerate(steps) if str(step.get("uses", "")).startswith("actions/checkout@"))
cache = next(i for i, step in enumerate(steps) if step.get("name") == "Prepare job-scoped changelog tool cache")
verify = next(i for i, step in enumerate(steps) if step.get("name") == "Verify the checked-out tag and immutable release note")
assert checkout < cache < verify
PY
grep -qF 'echo "VERJSON_CHANGELOG_TOOL_CACHE=" >> "$GITHUB_ENV"' "$actions_ci_workflow" \
  && pass "actions-ci clears the persistent runner changelog cache override" \
  || fail "actions-ci does not restore per-fixture changelog cache isolation"
{ grep -qF 'NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}' "$release" \
  && grep -qF 'NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}' "$release"; } \
  && pass "node-release preserves private install and publish authentication" \
  || fail "node-release regressed NODE_AUTH_TOKEN wiring"

composite_cache="$(composite_input cache)"
composite_dependency="$(composite_input cache-dependency-path)"
composite_scope="$(composite_input scope)"
composite_registry="$(composite_input registry-url)"
{ grep -qF "default: 'false'" <<<"$composite_cache" \
  && grep -qF 'default: package-lock.json' <<<"$composite_dependency" \
  && grep -qF "cache: \${{ inputs.cache == 'true' && hashFiles(inputs.cache-dependency-path) != '' && 'npm' || '' }}" "$composite" \
  && grep -qF 'cache-dependency-path: ${{ inputs.cache-dependency-path }}' "$composite" \
  && grep -qF 'package-manager-cache: false' "$composite" \
  && grep -qF 'echo "npm_config_cache=$RUNNER_TEMP/verjson-npm-cache" >> "$GITHUB_ENV"' "$composite"; } \
  && pass "setup-verjson-node implements the same default-off, job-scoped cache contract" \
  || fail "setup-verjson-node cache inputs or setup-node wiring are incomplete"
{ grep -qF "default: '@verjson'" <<<"$composite_scope" \
  && grep -qF "default: 'https://npm.pkg.github.com'" <<<"$composite_registry" \
  && grep -qF "registry-url: \${{ inputs.scope != '' && inputs.registry-url || '' }}" "$composite" \
  && grep -qF 'NODE_AUTH_TOKEN: ${{ inputs.node-auth-token }}' "$composite"; } \
  && pass "setup-verjson-node gates public installs while preserving private registry auth" \
  || fail "setup-verjson-node regressed empty-scope gating or private registry auth"

for test_command in \
  'bash scripts/node-workflow-controls.test.sh' \
  'bash scripts/ci-gate/node-ci-db-service.test.sh' \
  'bash scripts/ci-gate/node-ci-cache-service.test.sh' \
  'bash scripts/ci-gate/ci-eligibility.test.sh' \
  'bash scripts/node-workflow-pins.test.sh' \
  'bash scripts/node-release-publish.test.sh' \
  'bash scripts/retired-release-tooling.test.sh'; do
  grep -qF "$(printf '\t%s' "$test_command")" "$actions_ci" \
    && pass "actions-ci runs $test_command" \
    || fail "actions-ci does not run $test_command"
done

changelog_cache_script="$(mktemp)"
python3 - "$ci" > "$changelog_cache_script" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for step in doc["jobs"]["build-test"]["steps"]:
    if step.get("name") == "Prepare job-scoped changelog tool cache":
        print(step["run"])
        break
else:
    raise AssertionError("missing changelog cache preparation step")
PY
changelog_runner_temp="/proc/verjson-unwritable-runner-temp"
changelog_workspace="$(mktemp -d)"
changelog_github_env="$(mktemp)"
if RUNNER_TEMP="$changelog_runner_temp" GITHUB_WORKSPACE="$changelog_workspace" GITHUB_ENV="$changelog_github_env" bash "$changelog_cache_script" \
    && changelog_cache="$(sed -n 's/^VERJSON_CHANGELOG_TOOL_CACHE=//p' "$changelog_github_env")" \
    && [ -d "$changelog_cache" ] \
    && [ "${changelog_cache#"$changelog_workspace/.verjson-changelog-tools."}" != "$changelog_cache" ]; then
  pass "node-ci exports a cold-cache location independently of runner.temp"
else
  fail "node-ci cannot prepare a writable cold-cache location with unwritable runner.temp"
fi
rm -f "$changelog_cache_script" "$changelog_github_env"
rm -rf "$changelog_workspace"

publish_runner_temp="/proc/verjson-unwritable-runner-temp"
publish_workspace="$(mktemp -d)"
ambient_publish_cache="/proc/verjson-persistent-changelog-cache"
export VERJSON_CHANGELOG_TOOL_CACHE="$ambient_publish_cache"
publish_contract_sha="0123456789abcdef0123456789abcdef01234567"
publish_cache_step="$(mktemp)"
python3 - "$release" >"$publish_cache_step" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for step in doc["jobs"]["release"]["steps"]:
    if step.get("name") == "Prepare job-scoped changelog tool cache":
        print(step["run"])
PY
publish_github_env="$(mktemp)"
RUNNER_TEMP="$publish_runner_temp" GITHUB_WORKSPACE="$publish_workspace" GITHUB_ENV="$publish_github_env" \
  bash -eo pipefail "$publish_cache_step"
publish_cache="$(sed -n 's/^VERJSON_CHANGELOG_TOOL_CACHE=//p' "$publish_github_env")"
populate_publish_cache() {
  mkdir -p "$VERJSON_CHANGELOG_TOOL_CACHE/$publish_contract_sha" \
    && printf published >"$VERJSON_CHANGELOG_TOOL_CACHE/$publish_contract_sha/changelog.py"
}
if VERJSON_CHANGELOG_TOOL_CACHE="$publish_cache" populate_publish_cache \
  && [ "$(cat "$publish_cache/$publish_contract_sha/changelog.py")" = published ] \
  && [ "$VERJSON_CHANGELOG_TOOL_CACHE" = "$ambient_publish_cache" ] \
  && [ ! -e "$ambient_publish_cache" ]; then
  pass "node-release publish builds override a hostile persistent cache and populate a cold SHA beneath runner.temp"
else
  fail "node-release publish builds still depend on a persistent runner cache"
fi

release_without_cache="$(mktemp)"
cp "$release" "$release_without_cache"
sed -i '/^      - name: Prepare job-scoped changelog tool cache$/,+3d' "$release_without_cache"
missing_publish_step="$(mktemp)"
python3 - "$release_without_cache" >"$missing_publish_step" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for step in doc["jobs"]["release"]["steps"]:
    if step.get("name") == "Prepare job-scoped changelog tool cache":
        print(step["run"])
PY
[ ! -s "$missing_publish_step" ] || fail "the publish-cache removal mutation left an override step"
if VERJSON_CHANGELOG_TOOL_CACHE="$ambient_publish_cache" populate_publish_cache 2>/dev/null; then
  fail "the publish build survived removal of the runner.temp cache override"
else
  pass "removing the publish override reproduces the hostile persistent-cache failure (#630)"
fi
unset VERJSON_CHANGELOG_TOOL_CACHE
rm -f "$release_without_cache" "$publish_cache_step" "$publish_github_env" "$missing_publish_step"
rm -rf "$publish_workspace"

{ grep -qF '`timeout-minutes`' "$docs" \
  && grep -qF '`cache-dependency-path`' "$docs" \
  && grep -qF '`cache: false`' "$docs" \
  && grep -qF '`NODE_AUTH_TOKEN`' "$docs" \
  && grep -qF 'cancel-in-progress: true' "$docs" \
  && grep -qF '`cancel-in-progress: false`' "$docs"; } \
  && pass "usage docs cover timeout, cache, auth, and caller concurrency decisions" \
  || fail "usage docs omit a required timeout/cache/concurrency contract"

# Execute the workflow-owned guard, not a copied test implementation. This
# proves an oversized job-scoped cache is emptied while an in-bound cache is
# preserved, and confines deletion beneath RUNNER_TEMP.
guard_script="$(mktemp)"
guard_root="$(mktemp -d)"
trap 'rm -f "$guard_script"; rm -rf "$guard_root"' EXIT
awk '
  /- name: Bound npm cache upload/ { found = 1; next }
  found && /^        run: \|$/ { capture = 1; next }
  capture && /^          / { sub(/^          /, ""); print; next }
  capture { exit }
' "$ci" > "$guard_script"
summary="$guard_root/summary.md"
cache_dir="$guard_root/verjson-npm-cache"
mkdir -p "$cache_dir"
dd if=/dev/zero of="$cache_dir/oversized" bs=1048576 count=2 status=none
if RUNNER_TEMP="$guard_root" CACHE_MAX_MB=1 GITHUB_STEP_SUMMARY="$summary" \
    bash "$guard_script" >/dev/null \
    && [ -d "$cache_dir" ] \
    && [ -z "$(find "$cache_dir" -mindepth 1 -print -quit)" ] \
    && grep -qF -- '- size:' "$summary" \
    && grep -qF -- '- upload limit: 1 MB' "$summary"; then
  pass "cache guard reports and clears an oversized job-scoped cache"
else
  fail "cache guard did not safely clear an oversized job-scoped cache"
fi

: > "$summary"
printf 'keep' > "$cache_dir/in-bound"
if RUNNER_TEMP="$guard_root" CACHE_MAX_MB=1 GITHUB_STEP_SUMMARY="$summary" \
    bash "$guard_script" >/dev/null \
    && [ -f "$cache_dir/in-bound" ]; then
  pass "cache guard preserves an in-bound job-scoped cache"
else
  fail "cache guard removed an in-bound job-scoped cache"
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
