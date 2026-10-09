#!/usr/bin/env bash
# Verjson/.github#1229 — secretless acquisition must cover nested manifests.
#
# The credentialless execution job never receives a package-read credential, so
# every private dependency it installs has to arrive through the credentialed
# acquisition job's bounded, digest-verified transfer. Before this contract the
# acquisition, packaging, and install paths all hardcoded the root lockfile, so
# a consumer with a nested manifest (an example directory with its own
# package.json/package-lock.json) had no credentialless way to obtain its
# private dependencies at all.
#
# The load-bearing invariant these tests protect is per-manifest authorization:
# a manifest is validated against its OWN approvedPackages only, so no manifest
# can smuggle in a package that was authorized for a different one.
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/node-ci.yml"
failures=0
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

pass() { printf 'ok - %s\n' "$1"; }
fail() { printf 'not ok - %s\n' "$1" >&2; failures=$((failures + 1)); }

python3 - "$workflow" <<'PY' \
  && pass "the nested-manifest input is declared and wired into every acquisition stage" \
  || fail "the nested-manifest input contract is absent or unwired"
import sys
import yaml

doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
inputs = doc[True]["workflow_call"]["inputs"]
jobs = doc["jobs"]

declared = inputs["secretless-nested-manifests"]
assert declared["type"] == "string"
assert declared["default"] == ""
assert declared["required"] is False
description = declared["description"]
for phrase in ("path", "approvedPackages", "scriptPlan", "never inherits"):
    assert phrase in description, phrase

acquire = jobs["acquire-secretless-dependencies"]["steps"]
build = jobs["build-test"]["steps"]
wiring = "${{ inputs.secretless-nested-manifests }}"

validate = next(step for step in acquire
                if step.get("name") == "Validate approved internal dependency lock")
package = next(step for step in acquire
               if step.get("name") == "Package bounded credential-free npm cache")
download_acquire = next(
    step for step in acquire
    if step.get("name") == "Download pinned secretless dependency transfer implementation"
)
download_build = next(
    step for step in build
    if step.get("name") == "Download pinned secretless dependency transfer implementation"
)
decrypt = next(step for step in build
               if step.get("name") == "Decrypt run-scoped secretless dependency payload")
install = next(step for step in build
               if step.get("name") == "Install from verified secretless npm cache")
plan = next(step for step in build
            if step.get("name") == "Run exact credentialless consumer script plan")

for step in (validate, package, install, plan):
    assert step["env"]["NESTED_MANIFESTS"] == wiring, step["name"]

def assert_step_order(job_steps, *names):
    positions = [next(i for i, step in enumerate(job_steps)
                      if step.get("name") == name) for name in names]
    assert positions == sorted(positions), (names, positions)

assert_step_order(
    acquire,
    "Download pinned secretless dependency transfer implementation",
    "Package bounded credential-free npm cache",
)
assert_step_order(
    build,
    "Download pinned secretless dependency transfer implementation",
    "Decrypt run-scoped secretless dependency payload",
    "Install from verified secretless npm cache",
)
workflow_sha = "${{ fromJSON(toJSON(job)).workflow_sha }}"
for download in (download_acquire, download_build):
    assert download["env"]["JOB_WORKFLOW_SHA"] == workflow_sha
    assert "${JOB_WORKFLOW_SHA}/scripts/container_dependency_transfer.py" in download["run"]
assert "container_dependency_transfer.py" in package["run"]
assert "container_dependency_transfer.py" in decrypt["run"]
assert decrypt["env"]["TRANSFER_KEY"] == (
    "${{ needs.acquire-secretless-dependencies.outputs.transfer-encryption-key }}"
)
assert jobs["acquire-secretless-dependencies"]["outputs"]["transfer-encryption-key"] == (
    "${{ steps.package-secretless-transfer.outputs.encryption-key }}"
)

# The protected script runner also carries the default command plan when no root
# plan is supplied, so it runs for every admitted secretless lane.
assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in plan["if"]
assert "RUN_DEFAULTS" not in plan["env"]
assert "run_defaults = not plan_source" in plan["run"]
assert "if run_defaults:" in plan["run"]

# The credentialless job still receives no package-read credential.
for credential in ("GH_TOKEN", "GITHUB_TOKEN", "NODE_AUTH_TOKEN", "NPM_TOKEN"):
    assert install["env"][credential] == ""
assert "secrets." not in str(install.get("env", {}))
assert "secrets." not in str(plan.get("env", {}))
PY

validator="$tmp/validate.sh"
awk '
  /- name: Validate approved internal dependency lock/ { found = 1; next }
  found && /python3 - <<'"'"'PY'"'"'/ { copying = 1; next }
  copying && /^          PY$/ { exit }
  copying { sub(/^          /, ""); print }
' "$workflow" > "$validator"

run_validator() {
  local fixture="$1" approved="$2" nested="$3"
  local scopes="${4:-@verjson}" policy="${5:-}" package_manager="${6:-npm}"
  rm -f "$fixture/private-cache-entries"
  (cd "$fixture" && APPROVED_INTERNAL_PACKAGES="$approved" \
    APPROVED_INTERNAL_SCOPES="$scopes" NESTED_MANIFESTS="$nested" \
    PACKAGE_MANAGER="$package_manager" \
    PRIVATE_CACHE_ENTRIES="$fixture/private-cache-entries" \
    TRUSTED_PACKAGE_POLICY="$policy" python3 "$validator")
}

blob() { printf 'content of %s\n' "$1"; }
integrity_of() { blob "$1" | openssl dgst -sha512 -binary | base64 -w0; }
digest_of() { blob "$1" | sha512sum | cut -d' ' -f1; }

root_integrity="sha512-$(integrity_of root-lib)"
nested_integrity="sha512-$(integrity_of nested-lib)"
root_url='https://npm.pkg.github.com/download/@verjson/root-lib/1.0.0/aaa'
nested_url='https://npm.pkg.github.com/download/@verjson/nested-lib/2.0.0/bbb'

write_lock() {
  # write_lock <path> [<name> <url> <integrity>]...
  local target="$1"; shift
  local dir; dir="$(dirname "$target")"
  mkdir -p "$dir"
  printf '%s\n' '{"name":"fixture","version":"1.0.0","scripts":{"verify":"true"}}' \
    > "$dir/package.json"
  local body='{"lockfileVersion":3,"packages":{"":{}'
  while [ "$#" -gt 0 ]; do
    body="$body,\"node_modules/$1\":{\"name\":\"$1\",\"resolved\":\"$2\",\"integrity\":\"$3\"}"
    shift 3
  done
  printf '%s\n' "$body}}" > "$target"
}

nested_plan='[{"path":"examples/nested","approvedPackages":["@verjson/nested-lib"],"scriptPlan":["verify"]}]'

paired="$tmp/paired"
write_lock "$paired/package-lock.json" '@verjson/root-lib' "$root_url" "$root_integrity"
write_lock "$paired/examples/nested/package-lock.json" \
  '@verjson/nested-lib' "$nested_url" "$nested_integrity"

if run_validator "$paired" '@verjson/root-lib' "$nested_plan" >/dev/null 2>&1 \
    && [ "$(wc -l < "$paired/private-cache-entries")" -eq 2 ] \
    && grep -qF "$root_url	$(digest_of root-lib)" "$paired/private-cache-entries" \
    && grep -qF "$nested_url	$(digest_of nested-lib)" "$paired/private-cache-entries"; then
  pass "a nested manifest's private downloads join the root manifest's verified acquisition set"
else
  fail "a nested manifest's private downloads were not acquired alongside the root manifest's"
fi

# The adversarial control: the root manifest approves @verjson/root-lib, so a
# nested manifest that pulls it must still be rejected — approval is scoped to
# the manifest that declared it, never to the workflow call as a whole.
smuggle="$tmp/smuggle"
write_lock "$smuggle/package-lock.json" '@verjson/root-lib' "$root_url" "$root_integrity"
write_lock "$smuggle/examples/nested/package-lock.json" \
  '@verjson/nested-lib' "$nested_url" "$nested_integrity" \
  '@verjson/root-lib' "$root_url" "$root_integrity"

if run_validator "$smuggle" '@verjson/root-lib' "$nested_plan" >/dev/null 2>&1; then
  fail "a nested manifest inherited another manifest's approved package"
else
  pass "a package approved only for the root manifest is rejected inside a nested manifest"
fi

# The mirror control: the root manifest must not inherit a nested approval.
reverse="$tmp/reverse"
write_lock "$reverse/package-lock.json" \
  '@verjson/root-lib' "$root_url" "$root_integrity" \
  '@verjson/nested-lib' "$nested_url" "$nested_integrity"
write_lock "$reverse/examples/nested/package-lock.json" \
  '@verjson/nested-lib' "$nested_url" "$nested_integrity"

if run_validator "$reverse" '@verjson/root-lib' "$nested_plan" >/dev/null 2>&1; then
  fail "the root manifest inherited a nested manifest's approved package"
else
  pass "a package approved only for a nested manifest is rejected inside the root manifest"
fi

# A nested manifest path names a directory inside the checkout. A PR-authored
# symlink passes every syntactic segment check, so containment has to be proved
# against the resolved path or the credentialed job reads a lock from outside
# the workspace entirely.
escape="$tmp/escape"
outside="$tmp/outside-checkout"
write_lock "$escape/package-lock.json" '@verjson/root-lib' "$root_url" "$root_integrity"
write_lock "$outside/package-lock.json" '@verjson/nested-lib' "$nested_url" "$nested_integrity"
mkdir -p "$escape/examples"
ln -s "$outside" "$escape/examples/nested"

if run_validator "$escape" '@verjson/root-lib' "$nested_plan" >/dev/null 2>&1; then
  fail "a nested manifest symlinked outside the checkout was accepted"
else
  pass "a nested manifest path resolving outside the checkout is rejected"
fi

reject_nested() {
  local label="$1" nested="$2" package_manager="${3:-npm}"
  if run_validator "$paired" '@verjson/root-lib' "$nested" \
      '@verjson' '' "$package_manager" >/dev/null 2>&1; then
    fail "$label"
  else
    pass "$label"
  fi
}

reject_nested "a parent-traversal nested manifest path is rejected" \
  '[{"path":"../elsewhere","approvedPackages":["@verjson/nested-lib"],"scriptPlan":[]}]'
reject_nested "an absolute nested manifest path is rejected" \
  '[{"path":"/etc/nested","approvedPackages":["@verjson/nested-lib"],"scriptPlan":[]}]'
reject_nested "a repeated nested manifest path is rejected" \
  '[{"path":"examples/nested","approvedPackages":["@verjson/nested-lib"],"scriptPlan":[]},{"path":"examples/nested","approvedPackages":["@verjson/nested-lib"],"scriptPlan":[]}]'
reject_nested "an unknown nested manifest key is rejected" \
  '[{"path":"examples/nested","approvedPackages":["@verjson/nested-lib"],"scriptPlan":[],"registry":"https://evil.example"}]'
reject_nested "a nested approvedPackages entry outside the approved scopes is rejected" \
  '[{"path":"examples/nested","approvedPackages":["@attacker/nested-lib"],"scriptPlan":[]}]'

# Bounds and shapes. Each of these would otherwise widen either the fan-out the
# credentialed job performs or the set of packages one manifest can authorize.
reject_nested "an empty nested manifest list is rejected" '[]'
reject_nested "a nested manifest list is not an object" \
  '{"path":"examples/nested","approvedPackages":[],"scriptPlan":[]}'
reject_nested "more than eight nested manifests are rejected" \
  "$(python3 -c 'import json;print(json.dumps([{"path":f"examples/n{i}","approvedPackages":[],"scriptPlan":[]} for i in range(9)]))')"
reject_nested "malformed nested manifest JSON is rejected" \
  '[{"path":"examples/nested",'
reject_nested "a duplicate key inside a nested manifest object is rejected" \
  '[{"path":"examples/nested","approvedPackages":["@verjson/nested-lib"],"scriptPlan":[],"approvedPackages":["@attacker/x"]}]'
reject_nested "a non-list nested approvedPackages is rejected" \
  '[{"path":"examples/nested","approvedPackages":"@verjson/nested-lib","scriptPlan":[]}]'
reject_nested "a repeated package within one nested approvedPackages is rejected" \
  '[{"path":"examples/nested","approvedPackages":["@verjson/nested-lib","@verjson/nested-lib"],"scriptPlan":[]}]'
reject_nested "more than eight nested script plan entries are rejected" \
  '[{"path":"examples/nested","approvedPackages":["@verjson/nested-lib"],"scriptPlan":["a","b","c","d","e","f","g","h","i"]}]'

# A declared directory that carries no lockfile of its own has nothing the
# credentialed job could verify, so it must be refused rather than silently
# resolved against the root lock.
mkdir -p "$paired/examples/bare"
printf '%s\n' '{"name":"bare","version":"1.0.0","scripts":{"verify":"true"}}' \
  > "$paired/examples/bare/package.json"
reject_nested "a nested manifest without its own package-lock.json is rejected" \
  '[{"path":"examples/bare","approvedPackages":["@verjson/nested-lib"],"scriptPlan":[]}]'

reject_nested "a non-ASCII nested path segment is rejected" \
  '[{"path":"examples/nestеd","approvedPackages":["@verjson/nested-lib"],"scriptPlan":[]}]'
reject_nested "an over-long nested path segment is rejected" \
  "$(python3 -c 'print("[{\"path\":\"examples/" + "n" * 65 + "\",\"approvedPackages\":[],\"scriptPlan\":[]}]")')"

# Two declarations can name the same physical directory through a symlink. Each
# is still verified against its own approvedPackages, so the union of the two
# approvals never becomes usable: exact-set equality rejects the shared lock.
alias_fixture="$tmp/alias"
write_lock "$alias_fixture/package-lock.json" '@verjson/root-lib' "$root_url" "$root_integrity"
other_integrity="sha512-$(integrity_of other-lib)"
other_url='https://npm.pkg.github.com/download/@verjson/other-lib/3.0.0/ccc'
write_lock "$alias_fixture/examples/nested/package-lock.json" \
  '@verjson/nested-lib' "$nested_url" "$nested_integrity" \
  '@verjson/other-lib' "$other_url" "$other_integrity"
ln -s nested "$alias_fixture/examples/aliased"

if run_validator "$alias_fixture" '@verjson/root-lib' \
    '[{"path":"examples/nested","approvedPackages":["@verjson/nested-lib"],"scriptPlan":[]},{"path":"examples/aliased","approvedPackages":["@verjson/other-lib"],"scriptPlan":[]}]' \
    >/dev/null 2>&1; then
  fail "two aliased declarations combined their approvals over one shared lock"
else
  pass "aliased nested declarations cannot union their approvals over one lock"
fi

# pnpm has to be refused for the declaration itself, before any lock is read —
# otherwise the guard is indistinguishable from an unrelated parse failure.
pnpm_output="$(run_validator "$paired" '@verjson/root-lib' "$nested_plan" '@verjson' '' pnpm 2>&1)"
if [ "$pnpm_output" = "secretless-nested-manifests currently requires package-manager npm" ]; then
  pass "nested manifests under pnpm are rejected before any lock is parsed"
else
  fail "nested manifests under pnpm failed without the stable declaration reason"
fi

python3 - "$workflow" "$tmp" <<'PY'
import sys
import yaml

doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
root = sys.argv[2]
names = {
    "Package bounded credential-free npm cache": "package.sh",
    "Decrypt run-scoped secretless dependency payload": "decrypt.sh",
    "Install from verified secretless npm cache": "install.sh",
}
for job in doc["jobs"].values():
    for step in job.get("steps", []):
        if step.get("name") in names:
            with open(f"{root}/{names[step['name']]}", "w", encoding="utf-8") as stream:
                stream.write(step["run"])
PY

mkdir -p "$tmp/bin"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'printf "%s\t%s\n" "$PWD" "$*" >> "$NPM_STUB_LOG"' \
  > "$tmp/bin/npm"
chmod +x "$tmp/bin/npm"

# One end-to-end fixture: a root manifest and a nested manifest, each with a
# private package only it is authorized for, packaged into the single bounded
# transfer the credentialless job installs from.
e2e="$tmp/e2e"
write_lock "$e2e/package-lock.json" '@verjson/root-lib' "$root_url" "$root_integrity"
write_lock "$e2e/examples/nested/package-lock.json" \
  '@verjson/nested-lib' "$nested_url" "$nested_integrity"
for name in root-lib nested-lib; do
  digest="$(digest_of "$name")"
  content="$e2e/package-cache/_cacache/content-v2/sha512/${digest:0:2}/${digest:2:2}/${digest:4}"
  mkdir -p "$(dirname "$content")"
  blob "$name" > "$content"
done

run_package() {
  local fixture="$1" nested="$2"
  mkdir -p "$fixture/runner-temp"
  cp "$root/scripts/container_dependency_transfer.py" "$fixture/runner-temp/container_dependency_transfer.py"
  (cd "$fixture" && RUNNER_TEMP="$fixture/runner-temp" PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$fixture/npm.log" \
    CACHE_DIR="$fixture/package-cache" TRANSFER_DIR="$fixture/transfer" \
    AUXILIARY_COMMIT='' AUXILIARY_CONTENT_PATH='' AUXILIARY_REPOSITORY='' \
    NESTED_MANIFESTS="$nested" GITHUB_WORKSPACE="$fixture" \
    GITHUB_OUTPUT="$fixture/package.outputs" \
    NPM_CONFIG_GLOBALCONFIG="$fixture/empty-global.npmrc" \
    NPM_CONFIG_USERCONFIG="$fixture/empty-user.npmrc" \
    MAX_PAYLOAD_BYTES=83886080 RUN_ID=7001 RUN_ATTEMPT=3 bash "$tmp/package.sh") \
    && [ -f "$fixture/transfer/npm-private-cache.tar.enc" ] \
    && [ ! -e "$fixture/transfer/npm-private-cache.tar" ]
}

run_nested_install() {
  local fixture="$1" nested="$2"
  local expected_payload_sha256 expected_payload_bytes
  expected_payload_sha256="$(sed -n 's/^payload_sha256=//p' "$fixture/transfer/manifest")"
  expected_payload_bytes="$(sed -n 's/^payload_bytes=//p' "$fixture/transfer/manifest")"
  [ -f "$fixture/transfer/npm-private-cache.tar.enc" ] \
    && [ ! -e "$fixture/transfer/npm-private-cache.tar" ] || return 1
  mkdir -p "$fixture/runner-temp"
  cp "$root/scripts/container_dependency_transfer.py" "$fixture/runner-temp/container_dependency_transfer.py"
  (cd "$fixture" && RUNNER_TEMP="$fixture/runner-temp" TRANSFER_DIR="$fixture/transfer" \
    TRANSFER_KEY="$(sed -n 's/^encryption-key=//p' "$fixture/package.outputs")" bash "$tmp/decrypt.sh") || return 1
  [ -f "$fixture/transfer/npm-private-cache.tar" ] \
    && [ ! -e "$fixture/runner-temp/container_dependency_transfer.py" ] || return 1
  (cd "$fixture" && RUNNER_TEMP="$fixture/runner-temp" PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$fixture/npm.log" \
    GITHUB_ENV="$fixture/github.env" NPM_CONFIG_USERCONFIG="$fixture/empty.npmrc" \
    NPM_CONFIG_CACHE="$fixture/runtime-cache" \
    NPM_CONFIG_GLOBALCONFIG="$fixture/empty-global.npmrc" \
    APPROVED_INTERNAL_SCOPES=@verjson NESTED_MANIFESTS="$nested" \
    EXPECTED_AUXILIARY_COMMIT='' EXPECTED_AUXILIARY_CONTENT_PATH='' \
    EXPECTED_AUXILIARY_REPOSITORY='' GITHUB_WORKSPACE="$fixture" \
    EXPECTED_PAYLOAD_BYTES="$expected_payload_bytes" \
    EXPECTED_PAYLOAD_SHA256="$expected_payload_sha256" \
    MAX_PUBLIC_RUNTIME_CACHE_BLOBS=4096 MAX_PUBLIC_RUNTIME_CACHE_BYTES=268435456 \
    SECRETLESS_RUNTIME_PUBLIC_CACHE=false RUNNER_TEMP="$fixture/runner-temp" \
    PACKAGE_MANAGER=npm NESTED_PATHS_FILE="$fixture/runner-temp/nested-paths" \
    SECRETLESS_CACHE_DIR="$fixture/build-cache" TRANSFER_DIR="$fixture/transfer" \
    MAX_PAYLOAD_BYTES=83886080 RUN_ID=7001 RUN_ATTEMPT=3 bash "$tmp/install.sh")
}

install_flags='ci --ignore-scripts --prefer-offline --no-audit --no-fund --cache '
if run_package "$e2e" "$nested_plan" >/dev/null 2>&1 \
    && [ "$(wc -l < "$e2e/transfer/manifest")" -eq 11 ] \
    && grep -qE '^nested_manifests_sha256=[0-9a-f]{64}$' "$e2e/transfer/manifest" \
    && run_nested_install "$e2e" "$nested_plan" >/dev/null 2>&1 \
    && grep -qF "$e2e	$install_flags" "$e2e/npm.log" \
    && grep -qF "$e2e/examples/nested	$install_flags" "$e2e/npm.log"; then
  pass "two manifests share one bounded transfer and each installs credentiallessly from it"
else
  fail "the packaged cache or credentialless install did not cover both manifests"
fi

# A nested lockfile is PR-authored, so the acquisition-time digest binding has
# to cover it: swapping it after acquisition must fail before npm runs.
tamper="$tmp/tamper"
cp -a "$e2e" "$tamper"
rm -rf "$tamper/build-cache" "$tamper/runner-temp" "$tamper/npm.log" "$tamper/transfer"
if run_package "$tamper" "$nested_plan" >/dev/null 2>&1; then
  write_lock "$tamper/examples/nested/package-lock.json" \
    '@verjson/nested-lib' "$nested_url" "$root_integrity"
  if run_nested_install "$tamper" "$nested_plan" >/dev/null 2>&1; then
    fail "a nested lockfile mutated after acquisition was installed anyway"
  elif [ ! -s "$tamper/npm.log" ]; then
    pass "a nested lockfile mutated after acquisition fails before npm"
  else
    fail "a mutated nested lockfile reached npm before failing"
  fi
else
  fail "the tamper fixture could not be packaged"
fi

python3 - "$workflow" "$tmp/plan.sh" <<'PY'
import sys
import yaml

doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
step = next(step for job in doc["jobs"].values() for step in job.get("steps", [])
            if step.get("name") == "Run exact credentialless consumer script plan")
open(sys.argv[2], "w", encoding="utf-8").write(step["run"])
PY

plan_fixture="$tmp/plan-fixture"
mkdir -p "$plan_fixture/examples/nested" "$plan_fixture/.git" "$plan_fixture/node_modules" \
  "$plan_fixture/runner-temp/_runner_file_commands"
plan_toolchain="$tmp/plan-tool-cache/node/22.0.0/x64"
mkdir -p "$plan_toolchain/bin"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$plan_toolchain/bin/node"
cp "$tmp/bin/npm" "$plan_toolchain/bin/npm"
chmod +x "$plan_toolchain/bin/node" "$plan_toolchain/bin/npm"
printf '%s\n' '{"name":"root","version":"1.0.0","scripts":{"root-verify":"true","build":"true","typecheck":"true","test":"true","lint":"true"}}' \
  > "$plan_fixture/package.json"
printf '%s\n' '{"name":"nested","version":"1.0.0","scripts":{"verify":"true"}}' \
  > "$plan_fixture/examples/nested/package.json"

run_plan() {
  local root_plan="$1" nested="$2"
  rm -f "$plan_fixture/node_modules/.npm.log"
  (cd "$plan_fixture" && PATH="$plan_toolchain/bin:$PATH" \
    RUNNER_TOOL_CACHE="$tmp/plan-tool-cache" NPM_STUB_LOG=/workspace/node_modules/.npm.log \
    CI_SCRIPT_PLAN="$root_plan" NESTED_MANIFESTS="$nested" \
    RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux \
    RUNNER_TEMP="$plan_fixture/runner-temp" \
    GITHUB_ENV="$plan_fixture/runner-temp/_runner_file_commands/GITHUB_ENV" \
    bash "$tmp/plan.sh")
}

both_plan='[{"path":"examples/nested","approvedPackages":["@verjson/nested-lib"],"scriptPlan":["verify"]}]'
if run_plan '["root-verify"]' "$both_plan" >/dev/null \
    && grep -qFx '/workspace	run root-verify' "$plan_fixture/node_modules/.npm.log" \
    && grep -qFx '/workspace/examples/nested	run verify' "$plan_fixture/node_modules/.npm.log"; then
  pass "each manifest's script plan runs in that manifest's own directory"
else
  fail "the nested script plan did not run in its own manifest directory"
fi

# An empty root plan selects the default build/typecheck/test/lint scripts.
# The nested manifest's own plan still runs beside those defaults.
if run_plan '' "$both_plan" >/dev/null \
    && [ "$(wc -l < "$plan_fixture/node_modules/.npm.log")" -eq 5 ] \
    && grep -qFx '/workspace	run build' "$plan_fixture/node_modules/.npm.log" \
    && grep -qFx '/workspace	run typecheck' "$plan_fixture/node_modules/.npm.log" \
    && grep -qFx '/workspace	run test' "$plan_fixture/node_modules/.npm.log" \
    && grep -qFx '/workspace	run lint' "$plan_fixture/node_modules/.npm.log" \
    && grep -qFx '/workspace/examples/nested	run verify' "$plan_fixture/node_modules/.npm.log"; then
  pass "an empty root plan runs defaults and the nested manifest plan"
else
  fail "an empty root plan did not run defaults beside the nested manifest plan"
fi

# The root package.json declares root-verify; the nested one does not. Script
# existence is checked against the manifest that will run it.
if run_plan '["root-verify"]' \
    '[{"path":"examples/nested","approvedPackages":["@verjson/nested-lib"],"scriptPlan":["root-verify"]}]' \
    >/dev/null 2>&1; then
  fail "a nested plan ran a script declared only by the root package.json"
elif [ ! -s "$plan_fixture/node_modules/.npm.log" ]; then
  pass "a nested plan naming a script absent from its own package.json fails before npm"
else
  fail "a nested plan naming a foreign script reached npm before failing"
fi

reject_plan() {
  local label="$1" root_plan="$2" nested="$3"
  if run_plan "$root_plan" "$nested" >/dev/null 2>&1; then
    fail "$label"
  else
    pass "$label"
  fi
}

# unsetEnv can strip environment from a script, but never the variables the
# credentialless job relies on to stay credential-free and cache-bound.
reject_plan "a nested plan unsetting a credential variable is rejected" '' \
  '[{"path":"examples/nested","approvedPackages":[],"scriptPlan":[{"script":"verify","unsetEnv":["NODE_AUTH_TOKEN"]}]}]'
reject_plan "a nested plan unsetting the npm cache override is rejected" '' \
  '[{"path":"examples/nested","approvedPackages":[],"scriptPlan":[{"script":"verify","unsetEnv":["NPM_CONFIG_CACHE"]}]}]'
reject_plan "a nested plan repeating a script name is rejected" '' \
  '[{"path":"examples/nested","approvedPackages":[],"scriptPlan":["verify","verify"]}]'
reject_plan "a nested plan with an invalid script name is rejected" '' \
  '[{"path":"examples/nested","approvedPackages":[],"scriptPlan":["verify; rm -rf /"]}]'
reject_plan "a parent-traversal nested path is rejected before any script runs" '' \
  '[{"path":"../elsewhere","approvedPackages":[],"scriptPlan":["verify"]}]'

# The execution job runs on a PR-authored checkout, so it re-proves containment
# itself rather than trusting the credentialed job's earlier check.
mkdir -p "$plan_fixture/examples" "$tmp/outside-plan"
printf '%s\n' '{"name":"outside","version":"1.0.0","scripts":{"verify":"true"}}' \
  > "$tmp/outside-plan/package.json"
ln -s "$tmp/outside-plan" "$plan_fixture/examples/linked"
reject_plan "a symlinked nested path escaping the checkout is rejected before any script runs" '' \
  '[{"path":"examples/linked","approvedPackages":[],"scriptPlan":["verify"]}]'
if [ ! -s "$plan_fixture/node_modules/.npm.log" ]; then
  pass "no script ran for any rejected nested plan"
else
  fail "a rejected nested plan still reached npm"
fi

# The protected candidate variant re-implements the same loop inside a
# bubblewrap namespace, so its chdir must track the per-manifest directory too.
python3 - "$root/.github/workflows/node-ci-protected.yml" <<'PY' \
  && pass "the protected candidate loop chdirs into each script's own manifest directory" \
  || fail "the protected candidate loop does not chdir per manifest"
import sys
import yaml

doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
step = next(step for job in doc["jobs"].values() for step in job.get("steps", [])
            if step.get("name") == "Run exact credentialless consumer script plan")
body = step["run"]
assert "for index, (script_directory, name, unset_env, requires_services) in enumerate(normalized):" in body
assert '"--chdir", str(script_directory),' in body
assert '"--chdir", str(workspace),' not in body
PY

python3 - "$validator" "$tmp" <<'PYTEST' \
  && pass "contained npm workspace links and empty approvals preserve the registry boundary" \
  || fail "npm workspace link admission or empty-approval boundary regressed"
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

validator, temporary = sys.argv[1:]
checks = 0

def check(change=lambda root, packages: None, *, approved=(), success=True, version=3, downloads=0):
    global checks
    with tempfile.TemporaryDirectory(dir=temporary) as directory:
        root = Path(directory)
        nested = root / "examples/login"
        nested.mkdir(parents=True)
        (root / "package.json").write_text(json.dumps({"name": "@verjson/authn"}))
        (root / "package-lock.json").write_text(json.dumps({"lockfileVersion": version, "packages": {"": {}}}))
        (nested / "package.json").write_text(json.dumps({"name": "example"}))
        packages = {
            "": {},
            "../..": {"name": "@verjson/authn", "version": "1.0.0"},
            "node_modules/@verjson/authn": {"resolved": "../..", "link": True},
        }
        change(root, packages)
        (nested / "package-lock.json").write_text(json.dumps({"lockfileVersion": version, "packages": packages}))
        spec = [{"path": "examples/login", "approvedPackages": list(approved), "scriptPlan": []}]
        result = subprocess.run([sys.executable, validator], cwd=root, text=True, capture_output=True,
            env={**os.environ, "PACKAGE_MANAGER": "npm", "APPROVED_INTERNAL_SCOPES": "@verjson",
                 "APPROVED_INTERNAL_PACKAGES": "", "NESTED_MANIFESTS": json.dumps(spec),
                 "TRUSTED_PACKAGE_POLICY": "", "PRIVATE_CACHE_ENTRIES": str(root / "entries")})
        assert (result.returncode == 0) == success, result.stderr
        if success:
            assert len((root / "entries").read_text().splitlines()) == downloads, "only authorized registry downloads may enter acquisition"
        checks += 1

for version in (2, 3):
    check(version=version)
    check(lambda root, packages: packages["node_modules/@verjson/authn"].update(resolved="file:../.."), version=version)
check(lambda root, packages: packages.clear())
check(approved=("@verjson/authn",), success=False)
check(lambda root, packages: packages["node_modules/@verjson/authn"].update(name="@verjson/other"), success=False)
check(lambda root, packages: packages["../.."].update(name="@verjson/other"), success=False)
check(lambda root, packages: (root / "package.json").write_text('{"name":"@verjson/other"}'), success=False)
check(lambda root, packages: packages.pop("../.."), success=False)
check(lambda root, packages: packages["../.."].update(resolved="https://npm.pkg.github.com/download/@verjson/authn/1.0.0/aaa"), success=False)
check(lambda root, packages: packages["node_modules/@verjson/authn"].update(link="true"), success=False)
check(lambda root, packages: (root / "package.json").write_text('{"name":"@verjson/other","name":"@verjson/authn"}'), success=False)
for path in ("../node_modules/@verjson/authn", "/node_modules/@verjson/authn", "node_modules/../node_modules/@verjson/authn"):
    check(lambda root, packages, path=path: packages.update({path: packages.pop("node_modules/@verjson/authn")}), success=False)
for target in ("../../../", "/tmp", "file:/tmp", "https://evil.invalid/source", "..\\..", "%2e%2e/%2e%2e", "../..?query", "../..#fragment"):
    def mutate(root, packages, target=target):
        packages[target.removeprefix("file:")] = {"name": "@verjson/authn"}
        packages["node_modules/@verjson/authn"]["resolved"] = target
    check(mutate, success=False)

with tempfile.TemporaryDirectory(dir=temporary) as outside:
    external = Path(outside)
    (external / "package.json").write_text('{"name":"@verjson/authn"}')
    def directory_escape(root, packages):
        (root / "escaped").symlink_to(external, target_is_directory=True)
        packages["../../escaped"] = packages.pop("../..")
        packages["node_modules/@verjson/authn"]["resolved"] = "../../escaped"
    check(directory_escape, success=False)
    def manifest_escape(root, packages):
        (root / "package.json").unlink()
        (root / "package.json").symlink_to(external / "package.json")
    check(manifest_escape, success=False)

def registry_substitution(root, packages):
    packages.clear()
    packages["node_modules/@verjson/authn"] = {"name": "@verjson/authn", "resolved": "https://registry.npmjs.org/@verjson/authn/-/authn-1.0.0.tgz"}
check(registry_substitution, approved=("@verjson/authn",), success=False)
def unapproved_private(root, packages):
    packages.clear()
    packages["node_modules/@verjson/authn"] = {"resolved": "https://npm.pkg.github.com/download/@verjson/authn/1.0.0/aaa", "integrity": "sha512-" + "A" * 86 + "=="}
check(unapproved_private, success=False)
def contained_symlink(root, packages):
    (root / "source-alias").symlink_to(root, target_is_directory=True)
    packages["../../source-alias"] = packages.pop("../..")
    packages["node_modules/@verjson/authn"]["resolved"] = "../../source-alias"
check(contained_symlink)
check(lambda root, packages: packages["../.."].pop("name"))
def mixed_source_and_registry(root, packages):
    packages["node_modules/@verjson/private-lib"] = {"resolved": "https://npm.pkg.github.com/download/@verjson/private-lib/1.0.0/aaa", "integrity": "sha512-" + "A" * 86 + "=="}
check(mixed_source_and_registry, success=False)
check(mixed_source_and_registry, approved=("@verjson/private-lib",), downloads=1)
print(f"{checks} workspace/registry boundary cases passed")
PYTEST

exit $((failures > 0))
