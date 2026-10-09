#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/node-ci.yml"
protected_workflow="$root/.github/workflows/node-ci-protected.yml"
documentation="$root/docs/node-workflows.md"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
failures=0
tool_cache="$tmp/tool-cache"
tool_bin="$tool_cache/node/test/x64/bin"
toolchain_root="${tool_bin%/bin}"
host_npm="${VERJSON_TEST_HOST_NPM:-$(command -v npm)}"
export VERJSON_TEST_HOST_NPM="$host_npm"
mkdir -p "$tool_bin"
cp -- "$(command -v node)" "$tool_bin/node"
python3 - "$toolchain_root" <<'PY'
import os
import shutil
import sys
from pathlib import Path

npm_cli = Path(os.environ["VERJSON_TEST_HOST_NPM"]).resolve(strict=True)
shutil.copytree(npm_cli.parents[1], Path(sys.argv[1]) / "real-npm", symlinks=True)
PY
cat > "$toolchain_root/real-npm-exec" <<'NPM_EXEC'
#!/usr/bin/env bash
set -euo pipefail
exec node "${0%/*}/real-npm/bin/npm-cli.js" "$@"
NPM_EXEC
cat > "$tool_bin/npm" <<'NPM'
#!/usr/bin/env bash
set -euo pipefail
exec node "${0%/*}/../real-npm/bin/npm-cli.js" "$@"
NPM
chmod +x "$toolchain_root/real-npm-exec" "$tool_bin/node" "$tool_bin/npm"
export PATH="$tool_bin:$PATH" RUNNER_TOOL_CACHE="$tool_cache" RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
pass() { printf 'ok - %s\n' "$1"; }
fail() { printf 'not ok - %s\n' "$1" >&2; failures=$((failures + 1)); }

if grep -qF 'Each top-level checkout entry other than `node_modules` is a bind mountpoint' "$documentation" \
    && grep -qF 'Linux reports that operation as `EBUSY`' "$documentation"; then
  pass "adopter guidance explains the top-level bind mountpoint EBUSY constraint"
else
  fail "adopter guidance omits the top-level bind mountpoint EBUSY constraint"
fi
emit_failure_diagnostic() {
  local label="$1" status="$2" stderr_path="$3"
  python3 - "$label" "$status" "$stderr_path" <<'PY' >&2
import sys
from pathlib import Path

label, status, stderr_path = sys.argv[1:]
labels = {
    "resolved compatibility consumer",
    "cold-cache compatibility consumer",
    "verified public cache compatibility consumer",
    "absent public cache compatibility consumer",
    "probe",
}
if label not in labels:
    label = "compatibility consumer"
if (
    not status.isascii()
    or not status.isdecimal()
    or len(status) > 3
    or int(status) > 255
):
    status = "unknown"
path = Path(stderr_path)
lines = []
if path.is_file():
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 16384))
        text = stream.read(16384).decode("utf-8", errors="replace")
    lines = text.splitlines()[-40:]

unavailable = "trusted bubblewrap compatibility sandbox is unavailable"
unsafe = "trusted bubblewrap compatibility sandbox is unsafe"
namespace_denials = {
    "bwrap: Creating new namespace failed: Operation not permitted",
    "bwrap: Creating new namespace failed: Permission denied",
    "bwrap: setting up uid map: Operation not permitted",
    "bwrap: setting up uid map: Permission denied",
    "bwrap: setting up gid map: Operation not permitted",
    "bwrap: setting up gid map: Permission denied",
    (
        "bwrap: No permissions to create new namespace, likely because the kernel "
        "does not allow non-privileged user namespaces. See <https://deb.li/bubblewrap> "
        "or <file:///usr/share/doc/bubblewrap/README.Debian.gz>."
    ),
}
runtime_denials = {
    "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted",
    "bwrap: Can't mount proc on /newroot/proc: Operation not permitted",
    "bwrap: Can't mount proc on /newroot/proc: Permission denied",
}
if unavailable in lines:
    category = "bubblewrap-unavailable"
elif unsafe in lines:
    category = "bubblewrap-unsafe"
elif any(line in namespace_denials for line in lines):
    category = "bubblewrap-namespace-denied"
elif any(line in runtime_denials for line in lines):
    category = "bubblewrap-runtime-denied"
else:
    category = "stderr-suppressed"
print(f"diagnostic - {label} return-code={status} stderr-category={category}")
PY
}

printf '%s\n' \
  'DEPLOY_KEY=deploy-secret-value' \
  'OPENAI_API_KEY=openai-secret-value' \
  '{"token":"json-secret-value"}' \
  'bare-secret-value' \
  'consumer-controlled-sentinel' \
  >"$tmp/diagnostic-unknown.log"
unknown_probe="$(emit_failure_diagnostic probe 23 "$tmp/diagnostic-unknown.log" 2>&1)"
printf '%s\n' 'trusted bubblewrap compatibility sandbox is unavailable' \
  >"$tmp/diagnostic-unavailable.log"
unavailable_probe="$(emit_failure_diagnostic probe 1 "$tmp/diagnostic-unavailable.log" 2>&1)"
printf '%s\n' 'trusted bubblewrap compatibility sandbox is unsafe' \
  >"$tmp/diagnostic-unsafe.log"
unsafe_probe="$(emit_failure_diagnostic probe 1 "$tmp/diagnostic-unsafe.log" 2>&1)"
{
  cat "$tmp/diagnostic-unknown.log"
  printf '%s\n' 'bwrap: Creating new namespace failed: Operation not permitted'
} >"$tmp/diagnostic-namespace.log"
namespace_probe="$(emit_failure_diagnostic probe 1 "$tmp/diagnostic-namespace.log" 2>&1)"
printf '%s\n' 'bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted' \
  >"$tmp/diagnostic-runtime.log"
runtime_probe="$(emit_failure_diagnostic probe 1 "$tmp/diagnostic-runtime.log" 2>&1)"
if [ "$unknown_probe" = 'diagnostic - probe return-code=23 stderr-category=stderr-suppressed' ] \
  && [ "$unavailable_probe" = 'diagnostic - probe return-code=1 stderr-category=bubblewrap-unavailable' ] \
  && [ "$unsafe_probe" = 'diagnostic - probe return-code=1 stderr-category=bubblewrap-unsafe' ] \
  && [ "$namespace_probe" = 'diagnostic - probe return-code=1 stderr-category=bubblewrap-namespace-denied' ] \
  && [ "$runtime_probe" = 'diagnostic - probe return-code=1 stderr-category=bubblewrap-runtime-denied' ] \
  && [[ "$unknown_probe" != *'deploy-secret-value'* ]] \
  && [[ "$unknown_probe" != *'openai-secret-value'* ]] \
  && [[ "$unknown_probe" != *'json-secret-value'* ]] \
  && [[ "$unknown_probe" != *'bare-secret-value'* ]] \
  && [[ "$unknown_probe" != *'consumer-controlled-sentinel'* ]]; then
  pass "positive-failure diagnostics expose only exact allowlisted cause categories"
else
  fail "positive-failure diagnostics expose unknown stderr or lose exact cause categories"
fi

python3 - "$workflow" "$tmp" "$protected_workflow" <<'PY'
import sys
import os
import subprocess
from pathlib import Path
import yaml
doc = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
inputs = doc[True]["workflow_call"]["inputs"]
assert inputs["secretless-compatibility-ranges"]["default"] == ""
jobs = doc["jobs"]
steps = {step.get("name"): step for job in jobs.values()
         for step in job.get("steps", []) if step.get("name")}
acquire_steps = jobs["acquire-secretless-dependencies"]["steps"]
build_steps = jobs["build-test"]["steps"]
acquire_download = next(
    step for step in acquire_steps
    if step.get("name") == "Download pinned secretless dependency transfer implementation"
)
package = next(step for step in acquire_steps
               if step.get("name") == "Package bounded credential-free npm cache")
build_download = next(
    step for step in build_steps
    if step.get("name") == "Download pinned secretless dependency transfer implementation"
)
decrypt = next(step for step in build_steps
               if step.get("name") == "Decrypt run-scoped secretless dependency payload")
install = next(step for step in build_steps
               if step.get("name") == "Install from verified secretless npm cache")
def assert_step_order(job_steps, *names):
    positions = [next(i for i, step in enumerate(job_steps)
                      if step.get("name") == name) for name in names]
    assert positions == sorted(positions), (names, positions)

assert_step_order(
    acquire_steps,
    "Download pinned secretless dependency transfer implementation",
    "Package bounded credential-free npm cache",
)
assert_step_order(
    build_steps,
    "Download pinned secretless dependency transfer implementation",
    "Decrypt run-scoped secretless dependency payload",
    "Install from verified secretless npm cache",
)
workflow_sha = "${{ fromJSON(toJSON(job)).workflow_sha }}"
for download in (acquire_download, build_download):
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
protected_doc = yaml.safe_load(Path(sys.argv[3]).read_text(encoding="utf-8"))
protected_jobs = protected_doc["jobs"]
protected_acquire = protected_jobs["acquire-secretless-dependencies"]
protected_build = protected_jobs["build-test"]
protected_acquire_steps = protected_acquire["steps"]
protected_build_steps = protected_build["steps"]
protected_package = next(
    step for step in protected_acquire_steps
    if step.get("name") == "Package bounded credential-free npm cache"
)
identity_guard_index = next(
    index for index, step in enumerate(protected_acquire_steps)
    if step.get("name") == "Revalidate protected pull-request identity"
)
transfer_download_index = next(
    index for index, step in enumerate(protected_acquire_steps)
    if step.get("name") == "Download pinned secretless dependency transfer implementation"
)
assert protected_acquire_steps[identity_guard_index - 1].get("uses", "").startswith(
    "actions/checkout@"
), "protected identity guard must immediately follow the admitted checkout"
assert identity_guard_index < transfer_download_index, (
    "protected identity guard must run before downloading transfer code"
)
protected_decrypt = next(
    step for step in protected_build_steps
    if step.get("name") == "Decrypt run-scoped secretless dependency payload"
)
assert_step_order(
    protected_build_steps,
    "Download pinned secretless dependency transfer implementation",
    "Decrypt run-scoped secretless dependency payload",
    "Install from verified secretless npm cache",
)
assert protected_package["run"] == package["run"]
assert protected_decrypt["run"] == decrypt["run"]
assert protected_decrypt["env"]["TRANSFER_KEY"] == (
    "${{ needs.acquire-secretless-dependencies.outputs.transfer-encryption-key }}"
)
assert protected_acquire["outputs"]["transfer-encryption-key"] == (
    "${{ steps.package-secretless-transfer.outputs.encryption-key }}"
)
assert "npm-private-cache.tar.enc" in protected_package["run"]
assert 'TRANSFER_KEY="$encryption_key" python3 "$RUNNER_TEMP/container_dependency_transfer.py" encrypt' in protected_package["run"]
assert '--destination "$TRANSFER_DIR/npm-private-cache.tar.enc"' in protected_package["run"]
assert 'rm -f "$TRANSFER_DIR/npm-private-cache.tar"' in protected_package["run"]
assert protected_package["run"].index("npm-private-cache.tar.enc") < protected_package["run"].index(
    'rm -f "$TRANSFER_DIR/npm-private-cache.tar"'
)
transfer_cache_saves = [
    (index, step) for index, step in enumerate(protected_acquire_steps)
    if step.get("uses", "").startswith("actions/cache/save@")
    and step.get("with", {}).get("path") == ".verjson-secretless-transfer-${{ github.run_id }}"
]
assert len(transfer_cache_saves) == 1, "protected transfer cache must have one run-scoped save"
assert protected_acquire_steps.index(protected_package) < transfer_cache_saves[0][0], (
    "encrypted package step must remove its plaintext archive before caching the transfer directory"
)
for name, filename in {
    "Validate approved internal dependency lock": "validate.sh",
    "Resolve approved compatibility ranges without lifecycle execution": "resolve.sh",
    "Package bounded credential-free npm cache": "package.sh",
    "Decrypt run-scoped secretless dependency payload": "decrypt.sh",
    "Install from verified secretless npm cache": "install.sh",
    "Run runtime-resolved compatibility lanes without credentials": "run-lanes.sh",
}.items():
    Path(sys.argv[2], filename).write_text(steps[name]["run"], encoding="utf-8")
assert jobs["acquire-secretless-dependencies"]["permissions"] == {"contents": "read", "packages": "read"}
assert jobs["build-test"]["permissions"] == {"contents": "read"}
runner_guard = next(
 step for step in jobs["eligibility"]["steps"]
 if step.get("name") == "Reject incompatible secretless compatibility runner"
)
assert runner_guard["if"] == (
 "inputs.secretless-trusted-ref && "
 "inputs.secretless-compatibility-ranges != '' && "
 "runner.environment != 'github-hosted'"
)
guard_result = subprocess.run(
 ["/usr/bin/bash", "-c", runner_guard["run"]], capture_output=True, text=True
)
assert guard_result.returncode == 1
assert guard_result.stdout.strip() == (
 "::error::secretless-compatibility-ranges requires a GitHub-hosted runner "
 "for secretless-trusted-ref"
)
runner = steps["Run runtime-resolved compatibility lanes without credentials"]
protected_doc = yaml.safe_load(Path(sys.argv[3]).read_text(encoding="utf-8"))
protected_steps = {
    step.get("name"): step
    for job in protected_doc["jobs"].values()
    for step in job.get("steps", [])
    if step.get("name")
}
protected_runner = protected_steps[
    "Run runtime-resolved compatibility lanes without credentials"
]
Path(sys.argv[2], "protected-run-lanes.sh").write_text(
    protected_runner["run"], encoding="utf-8"
)
for name in ("GH_TOKEN", "GITHUB_TOKEN", "NODE_AUTH_TOKEN", "NPM_TOKEN",
             "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL"):
    assert runner["env"][name] == ""
# The sandbox resolves its verified public cache bind from these keys alone, so
# deleting them reproduces Verjson/.github#1372 with every consumer-facing case
# still green: the harness injects its own values and never reads the workflow's.
runtime_cache_keys = {
    "RESTORE_PERSISTED_PUBLIC_CACHE": (
        "${{ inputs.cache && inputs.package-manager == 'npm' }}"
    ),
    "RUNTIME_CACHE_DIR": (
        "${{ runner.temp }}/secretless-runtime-cache-"
        "${{ github.run_id }}-${{ github.run_attempt }}"
    ),
    "RUN_ATTEMPT": "${{ github.run_attempt }}",
    "RUN_ID": "${{ github.run_id }}",
    "SECRETLESS_RUNTIME_PUBLIC_CACHE": "${{ inputs.secretless-runtime-public-cache }}",
}
for current_runner in (runner, protected_runner):
    for name, expression in runtime_cache_keys.items():
        assert current_runner["env"][name] == expression, name
    assert sorted(current_runner["env"]) == list(current_runner["env"]), (
        "compatibility step env keys are no longer alphabetically ordered"
    )
assert "tarfile.open" in runner["run"] and "O_NOFOLLOW" in runner["run"]
assert 'subprocess.run(["npm", "install"' not in runner["run"]
assert "artifact.read_bytes" not in runner["run"]
assert "os.fstat(descriptor)" in runner["run"] and "io.BytesIO(content)" in runner["run"]
assert "extractall" not in runner["run"]
for current_runner in (runner, protected_runner):
    if os.environ.get("VERJSON_CACHE_MUTATION_CHILD") != "true":
        assert '"--dir", "/dev/shm/npm-cache"' in current_runner["run"]
        assert '"NPM_CONFIG_CACHE": "/dev/shm/npm-cache"' in current_runner["run"]
        assert '"NPM_CONFIG_GLOBALCONFIG": "/dev/shm/npm-globalconfig"' in current_runner["run"]
        assert '"NPM_CONFIG_USERCONFIG": "/dev/shm/npm-userconfig"' in current_runner["run"]
        assert '"npm_config_cache": "/dev/shm/npm-cache"' in current_runner["run"]
        assert '"npm_config_globalconfig": "/dev/shm/npm-globalconfig"' in current_runner["run"]
        assert '"npm_config_userconfig": "/dev/shm/npm-userconfig"' in current_runner["run"]
    assert "del output_tail[:-16384]" in current_runner["run"]
    assert "subprocess.CalledProcessError(" in current_runner["run"]
    assert "command={failure.cmd!r}" in current_runner["run"]
    assert "exit={failure.returncode}" in current_runner["run"]
    if os.environ.get("VERJSON_AMBIENT_MASK_MUTATION_CHILD") != "true":
        assert "ambient_masks = {" in current_runner["run"]
        assert 'arguments.extend(("--tmpfs", str(candidate_path)))' in current_runner["run"]
        assert 'arguments.extend(("--ro-bind", "/dev/null", str(candidate_path)))' in current_runner["run"]
        if os.environ.get("VERJSON_SYMLINK_GUARD_MUTATION_CHILD") != "true":
            assert "compatibility workspace top-level symlink escapes workspace" in current_runner["run"]
PY
[ "$?" -eq 0 ] && pass "compatibility lanes retain the canonical two-job credential boundary" \
  || fail "compatibility lanes do not retain the canonical two-job credential boundary"

request='{"package":"@verjson/identity-contracts","ranges":["^0.2.0"],"script":"test:compat"}'
policy='{"scopes":["@verjson"],"packages":["@verjson/identity-contracts"],"compatibility":{"@verjson/identity-contracts":["0.2.2","^0.2.0","~0.2.0",">=0.2.2 <0.4.0"]}}'
mkdir -p "$tmp/validate"
printf '%s\n' '{"name":"consumer","version":"1.0.0","lockfileVersion":3,"packages":{"":{"name":"consumer","version":"1.0.0"}}}' > "$tmp/validate/package-lock.json"
run_validator() {
  rm -f "$tmp/validate/private-entries"
  (cd "$tmp/validate" && APPROVED_INTERNAL_PACKAGES="$1" APPROVED_INTERNAL_SCOPES=@verjson \
    COMPATIBILITY_RANGES="$2" PACKAGE_MANAGER=npm PRIVATE_CACHE_ENTRIES="$tmp/validate/private-entries" \
    TRUSTED_PACKAGE_POLICY="${3-}" bash "$tmp/validate.sh")
}
if run_validator '@verjson/identity-contracts' "$request" "$policy" >/dev/null 2>&1; then
  pass "an exact approved compatibility package may be absent from the pinned lock"
else
  fail "an exact approved compatibility package was rejected"
fi

two_call_root="$tmp/two-call-policy"
mkdir -p "$two_call_root/general" "$two_call_root/type-surface" "$two_call_root/package-expansion" \
  "$two_call_root/scope-omission"
two_call_integrity="sha512-$(printf 'two-call policy fixture\n' | openssl dgst -sha512 -binary | base64 -w0)"
two_call_lock="{\"name\":\"authn-consumer\",\"version\":\"1.0.0\",\"lockfileVersion\":3,\"packages\":{\"\":{\"name\":\"authn-consumer\",\"version\":\"1.0.0\"},\"node_modules/@verjson/identity-contracts\":{\"name\":\"@verjson/identity-contracts\",\"resolved\":\"https://npm.pkg.github.com/download/@verjson/identity-contracts/0.3.0/archive\",\"integrity\":\"$two_call_integrity\"},\"node_modules/@verjson/tsconfig\":{\"name\":\"@verjson/tsconfig\",\"resolved\":\"https://npm.pkg.github.com/download/@verjson/tsconfig/0.1.3/archive\",\"integrity\":\"$two_call_integrity\"}}}"
printf '%s\n' "$two_call_lock" > "$two_call_root/general/package-lock.json"
printf '%s\n' "$two_call_lock" > "$two_call_root/type-surface/package-lock.json"
printf '%s\n' "{\"name\":\"authn-consumer\",\"version\":\"1.0.0\",\"lockfileVersion\":3,\"packages\":{\"\":{\"name\":\"authn-consumer\",\"version\":\"1.0.0\"},\"node_modules/@verjson/identity-contracts\":{\"name\":\"@verjson/identity-contracts\",\"resolved\":\"https://npm.pkg.github.com/download/@verjson/identity-contracts/0.3.0/archive\",\"integrity\":\"$two_call_integrity\"},\"node_modules/@verjson/private-target\":{\"name\":\"@verjson/private-target\",\"resolved\":\"https://npm.pkg.github.com/download/@verjson/private-target/1.0.0/archive\",\"integrity\":\"$two_call_integrity\"},\"node_modules/@verjson/tsconfig\":{\"name\":\"@verjson/tsconfig\",\"resolved\":\"https://npm.pkg.github.com/download/@verjson/tsconfig/0.1.3/archive\",\"integrity\":\"$two_call_integrity\"}}}" > "$two_call_root/package-expansion/package-lock.json"
two_call_policy='{"scopes":["@verjson"],"packages":["@verjson/authn","@verjson/identity-contracts","@verjson/tsconfig"],"compatibility":{"@verjson/authn":["1.0.3"],"@verjson/identity-contracts":[">=0.2.2 <0.3.0",">=0.3.0 <0.4.0"]}}'
general_request='{"package":"@verjson/identity-contracts","ranges":[">=0.2.2 <0.3.0",">=0.3.0 <0.4.0"],"script":"test:identity-contracts-compatibility"}'

confusion_integrity="sha512-$(printf 'scope omission confusion fixture\n' | openssl dgst -sha512 -binary | base64 -w0)"
multi_scope_policy='{"scopes":["@verjson","@tequityapp"],"packages":["@verjson/identity-contracts","@tequityapp/other"]}'
printf '%s\n' "{\"name\":\"authn-consumer\",\"version\":\"1.0.0\",\"lockfileVersion\":3,\"packages\":{\"\":{\"name\":\"authn-consumer\",\"version\":\"1.0.0\"},\"node_modules/@verjson/identity-contracts\":{\"name\":\"@verjson/identity-contracts\",\"resolved\":\"https://npm.pkg.github.com/download/@verjson/identity-contracts/0.3.0/archive\",\"integrity\":\"$two_call_integrity\"},\"node_modules/@tequityapp/other\":{\"name\":\"@tequityapp/other\",\"resolved\":\"https://registry.npmjs.org/@tequityapp/other/-/other-1.0.0.tgz\",\"integrity\":\"$confusion_integrity\"}}}" \
  > "$two_call_root/scope-omission/package-lock.json"
type_surface_request='{"package":"@verjson/authn","ranges":["1.0.3"],"script":"test:type-surface-compatibility"}'

run_policy_validator() {
  local fixture="$1" approved="$2" compatibility="$3" trusted_policy="$4"
  local scopes="${5:-@verjson}" validator_script="${6:-$tmp/validate.sh}"
  rm -f "$fixture/private-entries"
  (cd "$fixture" && APPROVED_INTERNAL_PACKAGES="$approved" APPROVED_INTERNAL_SCOPES="$scopes" \
    COMPATIBILITY_RANGES="$compatibility" PACKAGE_MANAGER=npm \
    PRIVATE_CACHE_ENTRIES="$fixture/private-entries" TRUSTED_PACKAGE_POLICY="$trusted_policy" \
    bash "$validator_script")
}

if run_policy_validator "$two_call_root/general" \
    $'@verjson/identity-contracts\n@verjson/tsconfig' "$general_request" "$two_call_policy" >/dev/null 2>&1 \
    && run_policy_validator "$two_call_root/type-surface" \
      $'@verjson/authn\n@verjson/identity-contracts\n@verjson/tsconfig' \
      "$type_surface_request" "$two_call_policy" >/dev/null 2>&1; then
  pass "one protected repository policy authorizes two exact per-call package subsets"
else
  fail "the authn two-call policy shape cannot authorize both exact dependency graphs"
fi

if run_policy_validator "$two_call_root/package-expansion" \
    $'@verjson/identity-contracts\n@verjson/private-target\n@verjson/tsconfig' \
    "$general_request" "$two_call_policy" >/dev/null 2>&1; then
  fail "a per-call package subset expanded beyond protected repository policy"
else
  pass "a per-call package subset cannot expand protected repository policy"
fi

if run_policy_validator "$two_call_root/general" \
    $'@verjson/authn\n@verjson/identity-contracts\n@verjson/tsconfig' \
    '' "$two_call_policy" >/dev/null 2>&1; then
  fail "a protected but unused per-call package approval was accepted"
else
  pass "protected policy membership does not exempt unused per-call approvals"
fi

if run_policy_validator "$two_call_root/general" \
    $'@verjson/identity-contracts\n@verjson/tsconfig' "$general_request" "$two_call_policy" \
    $'@tequityapp\n@verjson' >/dev/null 2>&1; then
  fail "a per-call scope subset expanded beyond protected repository policy"
else
  pass "a per-call scope subset cannot expand protected repository policy"
fi

if run_policy_validator "$two_call_root/scope-omission" \
    '@verjson/identity-contracts' '' "$multi_scope_policy" >/dev/null 2>&1; then
  fail "a per-call scope omission let a protected-policy scope's package resolve unrouted from the public registry"
else
  pass "a per-call scope omission cannot exempt a protected-policy scope from internal routing"
fi

for malformed_policy in \
    '{"scopes":["@Bad","@verjson"],"packages":["@verjson/identity-contracts","@verjson/tsconfig"]}' \
    '{"scopes":["@verjson"],"packages":["@verjson/Bad","@verjson/identity-contracts","@verjson/tsconfig"]}' \
    '{"scopes":["@verjson"],"packages":["@tequityapp/schema","@verjson/identity-contracts","@verjson/tsconfig"]}' \
    '{"scopes":[["@verjson"]],"packages":["@verjson/identity-contracts","@verjson/tsconfig"]}'; do
  if run_policy_validator "$two_call_root/general" \
      $'@verjson/identity-contracts\n@verjson/tsconfig' '' "$malformed_policy" >/dev/null 2>&1; then
    fail "a malformed protected policy superset entry was accepted"
  else
    pass "malformed protected policy superset entries fail closed"
  fi
done

duplicate_request='{"package":"@verjson/identity-contracts","package":"@verjson/identity-contracts","ranges":[">=0.2.2 <0.3.0",">=0.3.0 <0.4.0"],"script":"test:identity-contracts-compatibility"}'
duplicate_outer_policy='{"scopes":["@verjson"],"scopes":["@verjson"],"packages":["@verjson/authn","@verjson/identity-contracts","@verjson/tsconfig"],"compatibility":{"@verjson/authn":["1.0.3"],"@verjson/identity-contracts":[">=0.2.2 <0.3.0",">=0.3.0 <0.4.0"]}}'
duplicate_nested_policy='{"scopes":["@verjson"],"packages":["@verjson/authn","@verjson/identity-contracts","@verjson/tsconfig"],"compatibility":{"@verjson/authn":["1.0.3"],"@verjson/identity-contracts":[">=0.2.2 <0.3.0",">=0.3.0 <0.4.0"],"@verjson/identity-contracts":[">=0.2.2 <0.3.0",">=0.3.0 <0.4.0"]}}'

if duplicate_output=$(run_policy_validator "$two_call_root/general" \
    $'@verjson/identity-contracts\n@verjson/tsconfig' "$duplicate_request" "$two_call_policy" 2>&1); then
  fail "duplicate per-call compatibility object keys were accepted"
elif [ "$duplicate_output" = "secretless-compatibility-ranges rejects duplicate JSON object keys" ]; then
  pass "duplicate per-call compatibility object keys fail with a stable reason"
else
  fail "duplicate per-call compatibility object keys failed without the stable reason"
fi
for duplicate_policy in "$duplicate_outer_policy" "$duplicate_nested_policy"; do
  if duplicate_output=$(run_policy_validator "$two_call_root/general" \
      $'@verjson/identity-contracts\n@verjson/tsconfig' "$general_request" "$duplicate_policy" 2>&1); then
    fail "duplicate protected policy object keys were accepted"
  elif [ "$duplicate_output" = "CI_SECRETLESS_PACKAGE_POLICY rejects duplicate JSON object keys" ]; then
    pass "duplicate protected policy object keys fail with a stable reason at every level"
  else
    fail "duplicate protected policy object keys failed without the stable reason"
  fi
done

python3 - "$tmp/validate.sh" "$two_call_root/equality-mutant.sh" \
    "$two_call_root/subset-disabled-mutant.sh" "$two_call_root/unused-disabled-mutant.sh" \
    "$two_call_root/duplicate-enabled-mutant.sh" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8")
subset_guard = "if scopes != policy_scopes or not approved.issubset(policy_packages):"
unused_guard = 'if unused:\n    sys.exit("approved internal dependencies absent from lock: " + ", ".join(unused))'
duplicate_hook = "object_pairs_hook=reject_duplicate_object_keys,"
# Three authorization-bearing object levels reject duplicate keys: the per-call
# compatibility request, the trusted repository policy, and the nested-manifest
# declaration that authorizes packages per manifest (#1229).
if (source.count(subset_guard) != 1 or source.count(unused_guard) != 1
        or source.count(duplicate_hook) != 3):
    raise SystemExit("expected exact secretless policy guard source once")
Path(sys.argv[2]).write_text(
    source.replace(subset_guard, "if policy_scopes != scopes or policy_packages != approved:"),
    encoding="utf-8",
)
Path(sys.argv[3]).write_text(source.replace(subset_guard, "if False:"), encoding="utf-8")
Path(sys.argv[4]).write_text(source.replace(unused_guard, 'if False:\n    pass'), encoding="utf-8")
Path(sys.argv[5]).write_text(source.replace(duplicate_hook, ""), encoding="utf-8")
PY

if run_policy_validator "$two_call_root/general" \
    $'@verjson/identity-contracts\n@verjson/tsconfig' "$general_request" "$two_call_policy" \
    '@verjson' "$two_call_root/equality-mutant.sh" >/dev/null 2>&1; then
  fail "the exact-policy equality mutation did not recreate the two-call contradiction"
else
  pass "the exact-policy equality mutation recreates the two-call contradiction"
fi

if run_policy_validator "$two_call_root/package-expansion" \
    $'@verjson/identity-contracts\n@verjson/private-target\n@verjson/tsconfig' \
    "$general_request" "$two_call_policy" '@verjson' \
    "$two_call_root/subset-disabled-mutant.sh" >/dev/null 2>&1 \
    && run_policy_validator "$two_call_root/general" \
      $'@verjson/identity-contracts\n@verjson/tsconfig' "$general_request" "$two_call_policy" \
      $'@tequityapp\n@verjson' "$two_call_root/subset-disabled-mutant.sh" >/dev/null 2>&1; then
  pass "disabling the subset guard admits both package and scope expansion mutations"
else
  fail "package or scope mutation fixture did not isolate the subset guard"
fi

if run_policy_validator "$two_call_root/general" \
    $'@verjson/authn\n@verjson/identity-contracts\n@verjson/tsconfig' \
    '' "$two_call_policy" '@verjson' "$two_call_root/unused-disabled-mutant.sh" >/dev/null 2>&1; then
  pass "disabling the unused-approval guard admits the protected but absent package mutation"
else
  fail "unused-approval mutation fixture did not isolate the lock-exactness guard"
fi

if run_policy_validator "$two_call_root/general" \
    $'@verjson/identity-contracts\n@verjson/tsconfig' "$duplicate_request" "$two_call_policy" \
    '@verjson' "$two_call_root/duplicate-enabled-mutant.sh" >/dev/null 2>&1 \
    && run_policy_validator "$two_call_root/general" \
      $'@verjson/identity-contracts\n@verjson/tsconfig' "$general_request" "$duplicate_outer_policy" \
      '@verjson' "$two_call_root/duplicate-enabled-mutant.sh" >/dev/null 2>&1 \
    && run_policy_validator "$two_call_root/general" \
      $'@verjson/identity-contracts\n@verjson/tsconfig' "$general_request" "$duplicate_nested_policy" \
      '@verjson' "$two_call_root/duplicate-enabled-mutant.sh" >/dev/null 2>&1; then
  pass "removing unique-object decoding admits duplicate request, policy, and nested authorization keys"
else
  fail "duplicate-key mutation fixtures did not isolate every authorization-bearing object level"
fi
unapproved='{"package":"@verjson/other","ranges":["^0.2.0"],"script":"test:compat"}'
if run_validator '@verjson/identity-contracts' "$unapproved" "$policy" >/dev/null 2>&1; then
  fail "an unapproved compatibility package was accepted"
else
  pass "an unapproved compatibility package fails before registry access"
fi
caller_bypass='{"package":"@verjson/private-target","ranges":["^1.0.0"],"script":"test:compat"}'
if run_validator '@verjson/private-target' "$caller_bypass" '' >/dev/null 2>&1; then
  fail "caller-controlled package and absent-lock exemption bypassed protected policy"
else
  pass "combined caller-controlled package and absent-lock exemption fails without protected policy"
fi
package_only_policy='{"scopes":["@verjson"],"packages":["@verjson/identity-contracts"]}'
if run_validator '@verjson/identity-contracts' "$request" "$package_only_policy" >/dev/null 2>&1; then
  fail "package-only protected policy authorized caller-controlled compatibility ranges"
else
  pass "compatibility ranges require explicit protected-policy authorization"
fi
range_expansion='{"package":"@verjson/identity-contracts","ranges":["^1.0.0"],"script":"test:compat"}'
if run_validator '@verjson/identity-contracts' "$range_expansion" "$policy" >/dev/null 2>&1; then
  fail "caller-controlled range expanded beyond protected policy"
else
  pass "caller-controlled range cannot expand protected policy"
fi
for range_value in 'file:../payload' '>=0' '>=0.0.0' '*' '1.2' '>=2.0.0 <1.0.0' '>=1.0.0'; do
  bad_range="{\"package\":\"@verjson/identity-contracts\",\"ranges\":[\"$range_value\"],\"script\":\"test:compat\"}"
  bad_policy="{\"scopes\":[\"@verjson\"],\"packages\":[\"@verjson/identity-contracts\"],\"compatibility\":{\"@verjson/identity-contracts\":[\"$range_value\"]}}"
  if run_validator '@verjson/identity-contracts' "$bad_range" "$bad_policy" >/dev/null 2>&1; then
    fail "unbounded or unsupported compatibility range $range_value was accepted"
  else
    pass "unbounded or unsupported compatibility range $range_value fails before registry access"
  fi
done
for range_value in '0.2.2' '^0.2.0' '~0.2.0' '>=0.2.2 <0.4.0'; do
  bounded="{\"package\":\"@verjson/identity-contracts\",\"ranges\":[\"$range_value\"],\"script\":\"test:compat\"}"
  if run_validator '@verjson/identity-contracts' "$bounded" "$policy" >/dev/null 2>&1; then
    pass "bounded compatibility range $range_value is accepted"
  else
    fail "bounded compatibility range $range_value was rejected"
  fi
done

mkdir -p "$tmp/resolve/bin" "$tmp/resolve/runner"
printf 'compatibility fixture\n' > "$tmp/resolve/package.tgz"
fixture_integrity="sha512-$(openssl dgst -sha512 -binary "$tmp/resolve/package.tgz" | openssl base64 -A)"
cat > "$tmp/resolve/bin/npm" <<'SH'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$NPM_STUB_LOG"
failure="${NPM_STUB_FAILURE:-}"
for category in auth-fail access-fail missing empty; do
  if [ "$failure" = "$category" ]; then
    case "$category" in auth-fail) code=E401;; access-fail) code=E403;; missing) code=E404;; empty) code=ETARGET;; esac
    echo "npm ERR! code $code token=$NODE_AUTH_TOKEN" >&2
    exit 1
  fi
done
if [ "$#" -eq 4 ] && { [ "${4:-}" = version ] || [ "${3:-}" = versions ]; }; then
  printf '%s\n' '["0.2.1","0.2.2"]'
else
  printf '%s\n' "{\"name\":\"@verjson/identity-contracts\",\"version\":\"0.2.2\",\"dist.integrity\":\"$NPM_STUB_INTEGRITY\",\"dist.tarball\":\"${NPM_STUB_TARBALL:-https://npm.pkg.github.com/download/@verJSON/identity-contracts/0.2.2/archive}\"}"
fi
SH
chmod +x "$tmp/resolve/bin/npm"
run_resolver() {
  rm -rf "$tmp/resolve/runner/_compatibility"
  : > "$tmp/resolve/private-entries"
  (cd "$tmp/resolve" && PATH="$tmp/resolve/bin:$PATH" NPM_STUB_LOG="$tmp/resolve/npm.log" \
    NPM_STUB_INTEGRITY="$fixture_integrity" NPM_STUB_FAILURE="${3:-}" NPM_STUB_TARBALL="${4:-https://npm.pkg.github.com/download/@verJSON/identity-contracts/0.2.2/archive}" \
    NODE_AUTH_TOKEN=runtime-package-token \
    APPROVED_INTERNAL_SCOPES=@verjson COMPATIBILITY_RANGES="$1" \
    COMPATIBILITY_PROVENANCE="$tmp/resolve/runner/_compatibility/provenance.json" \
    PRIVATE_CACHE_ENTRIES="$tmp/resolve/private-entries" NPM_CONFIG_GLOBALCONFIG="$tmp/resolve/runner/global.npmrc" \
    NPM_CONFIG_USERCONFIG="$tmp/resolve/runner/user.npmrc" bash "$tmp/resolve.sh") >"$2" 2>&1
}
if run_resolver "$request" "$tmp/resolve/success.log" && \
  python3 - "$tmp/resolve/runner/_compatibility/provenance.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1],encoding="utf-8"));assert p["lanes"][0]["version"]=="0.2.2"
PY
then pass "trusted acquisition records runtime-resolved version, integrity, and provenance"; else fail "trusted acquisition did not record runtime-resolved provenance"; fi
confusable_tarball='https://npm.pkg.github.com/download/@vеrjson/identity-contracts/0.2.2/archive'
if run_resolver "$request" "$tmp/resolve/confusable.log" "" "$confusable_tarball"; then
  fail "registry provenance accepted a Unicode lookalike package path"
elif grep -qF 'invalid GitHub Packages URL' "$tmp/resolve/confusable.log"; then
  pass "registry provenance accepts API casing variants and rejects Unicode lookalikes"
else
  fail "registry provenance returned an unexpected error for a Unicode lookalike"
fi

outside_range='{"package":"@verjson/identity-contracts","ranges":["^9.0.0"],"script":"test:compat"}'
if run_resolver "$outside_range" "$tmp/resolve/outside-range.log"; then
  fail "registry-selected version outside the declared bounded range was accepted"
else
  pass "registry-selected version is rechecked against the declared bounded range"
fi
grep -Eq 'install|pack|run|exec' "$tmp/resolve/npm.log" \
  && fail "compatibility resolution invoked a lifecycle-capable npm command" \
  || pass "compatibility resolution uses metadata reads only"
for case_name in auth-fail access-fail missing empty; do
  bad="{\"package\":\"@verjson/identity-contracts\",\"ranges\":[\"${case_name}1.0.0\"],\"script\":\"test:compat\"}"
  if run_resolver "$bad" "$tmp/resolve/$case_name.log" "$case_name"; then
    fail "$case_name registry failure was accepted"
  elif grep -qF runtime-package-token "$tmp/resolve/$case_name.log"; then
    fail "$case_name registry diagnostic leaked the package token"
  else
    case "$case_name" in auth-fail) expected='authentication failed';; access-fail) expected='package access denied';; missing) expected='package is absent or unreadable';; empty) expected='compatibility range has no readable version';; esac
    grep -qF "$expected" "$tmp/resolve/$case_name.log" && pass "$case_name registry failure is classified and scrubbed" || fail "$case_name registry failure was not classified"
  fi
done

mkdir -p "$tmp/e2e/acquire/cache/_cacache/content-v2/sha512" "$tmp/e2e/acquire/compat/_compatibility" "$tmp/e2e/acquire/work" "$tmp/e2e/acquire/runner-temp"
cp "$root/scripts/container_dependency_transfer.py" "$tmp/e2e/acquire/runner-temp/container_dependency_transfer.py"
printf '%s\n' '{"name":"@verjson/identity-contracts","version":"0.2.2"}' > "$tmp/e2e/acquire/work/package.json"
tar -C "$tmp/e2e/acquire/work" --transform='s#^#package/#' -czf "$tmp/e2e/acquire/lane.tgz" package.json
lane_digest="$(sha512sum "$tmp/e2e/acquire/lane.tgz" | cut -d' ' -f1)"
lane_integrity="sha512-$(openssl dgst -sha512 -binary "$tmp/e2e/acquire/lane.tgz" | openssl base64 -A)"
lane_content="$tmp/e2e/acquire/cache/_cacache/content-v2/sha512/${lane_digest:0:2}/${lane_digest:2:2}/${lane_digest:4}"
mkdir -p "$(dirname "$lane_content")" && cp "$tmp/e2e/acquire/lane.tgz" "$lane_content"
printf '%s\n' '{"name":"consumer","version":"1.0.0","lockfileVersion":3,"packages":{"":{"name":"consumer","version":"1.0.0"}}}' > "$tmp/e2e/acquire/package-lock.json"
python3 - "$tmp/e2e/acquire/compat/_compatibility/provenance.json" "$lane_integrity" "$lane_digest" <<'PY'
import json,sys
r={"package":"@verjson/identity-contracts","ranges":["^0.2.0"],"script":"test:compat"}
l={"index":0,"package":r["package"],"range":"^0.2.0","script":"test:compat","version":"0.2.2","integrity":sys.argv[2],"tarball":"https://npm.pkg.github.com/download/@verjson/identity-contracts/0.2.2/archive","sha512":sys.argv[3]}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps({"schemaVersion":1,"request":r,"lanes":[l]},sort_keys=True,separators=(",",":"))+"\n")
PY
(cd "$tmp/e2e/acquire" && AUXILIARY_COMMIT='' AUXILIARY_CONTENT_PATH='' AUXILIARY_REPOSITORY='' \
  CACHE_DIR="$tmp/e2e/acquire/cache" COMPATIBILITY_ROOT="$tmp/e2e/acquire/compat" COMPATIBILITY_RANGES="$request" \
  GITHUB_OUTPUT="$tmp/e2e/acquire/package.output" GITHUB_WORKSPACE="$tmp/e2e/acquire" MAX_PAYLOAD_BYTES=83886080 \
  PACKAGE_MANAGER=npm RUN_ATTEMPT=1 RUN_ID=1103 RUNNER_TEMP="$tmp/e2e/acquire/runner-temp" TRANSFER_DIR="$tmp/e2e/acquire/transfer" bash "$tmp/package.sh")
[ "$?" -eq 0 ] \
  && [ -f "$tmp/e2e/acquire/transfer/npm-private-cache.tar.enc" ] \
  && [ ! -e "$tmp/e2e/acquire/transfer/npm-private-cache.tar" ] \
  && grep -q '^compatibility_provenance_sha256=' "$tmp/e2e/acquire/transfer/manifest" \
  && pass "compatibility provenance shares an encrypted bounded transfer" \
  || fail "compatibility provenance was not packaged as encrypted canonical transfer"

mkdir -p "$tmp/e2e/build/bin"
cp "$tmp/e2e/acquire/package-lock.json" "$tmp/e2e/build/package-lock.json" && cp -R "$tmp/e2e/acquire/transfer" "$tmp/e2e/build/transfer"
printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" "$*" >> "$NPM_STUB_LOG"' '[ "${1:-}" = ci ]' > "$tmp/e2e/build/bin/npm"
chmod +x "$tmp/e2e/build/bin/npm"
provenance_sha="$(sed -n 's/^compatibility_provenance_sha256=//p' "$tmp/e2e/build/transfer/manifest")"
decrypt_transfer() {
  local fixture="$1"
  local runner_temp="$fixture/runner-temp"
  local transfer_key
  transfer_key="$(sed -n 's/^encryption-key=//p' "$tmp/e2e/acquire/package.output")"
  [ -f "$fixture/transfer/npm-private-cache.tar.enc" ] \
    && [ ! -e "$fixture/transfer/npm-private-cache.tar" ] || return 1
  mkdir -p "$runner_temp"
  cp "$root/scripts/container_dependency_transfer.py" "$runner_temp/container_dependency_transfer.py"
  (cd "$fixture" && RUNNER_TEMP="$runner_temp" TRANSFER_DIR="$fixture/transfer" \
    TRANSFER_KEY="$transfer_key" bash "$tmp/decrypt.sh") || return 1
  [ -f "$fixture/transfer/npm-private-cache.tar" ] \
    && [ ! -e "$runner_temp/container_dependency_transfer.py" ]
}

run_install() {
  local fixture="$1"
  local runner_temp="$fixture/runner-temp"
  (cd "$fixture" && RUNNER_TEMP="$runner_temp" PATH="$tmp/e2e/build/bin:$PATH" NPM_STUB_LOG="$fixture/npm.log" APPROVED_INTERNAL_SCOPES=@verjson \
    COMPATIBILITY_RANGES="$request" COMPATIBILITY_ARTIFACT_DIR="$fixture/artifacts" EXPECTED_AUXILIARY_COMMIT='' \
    EXPECTED_AUXILIARY_CONTENT_PATH='' EXPECTED_AUXILIARY_REPOSITORY='' EXPECTED_COMPATIBILITY_PROVENANCE_SHA256="$provenance_sha" \
    EXPECTED_PAYLOAD_BYTES="$(sed -n 's/^payload_bytes=//p' "$fixture/transfer/manifest")" EXPECTED_PAYLOAD_SHA256="$(sed -n 's/^payload_sha256=//p' "$fixture/transfer/manifest")" \
    GITHUB_ENV="$fixture/github.env" GITHUB_WORKSPACE="$fixture" NPM_CONFIG_CACHE="$fixture/runtime-cache" NPM_CONFIG_GLOBALCONFIG="$fixture/global.npmrc" \
    NPM_CONFIG_USERCONFIG="$fixture/user.npmrc" PACKAGE_MANAGER=npm SECRETLESS_CACHE_DIR="$fixture/secretless-cache" TRANSFER_DIR="$fixture/transfer" \
    MAX_PAYLOAD_BYTES=83886080 RUN_ATTEMPT=1 RUN_ID=1103 bash "$tmp/install.sh")
}
if decrypt_transfer "$tmp/e2e/build" && run_install "$tmp/e2e/build" \
    && [ -f "$tmp/e2e/build/artifacts/lane-0.tgz" ]; then
  pass "credentialless restore decrypts before reconstructing the exact compatibility tarball"
else
  fail "credentialless restore did not decrypt and reconstruct the compatibility tarball"
fi

for mutation in provenance payload; do
  fixture="$tmp/e2e/tampered-$mutation"; mkdir -p "$fixture"; cp "$tmp/e2e/acquire/package-lock.json" "$fixture/package-lock.json"; cp -R "$tmp/e2e/acquire/transfer" "$fixture/transfer"
  if ! decrypt_transfer "$fixture"; then
    fail "the $mutation fixture could not decrypt the bounded transfer"
    continue
  fi
  python3 - "$fixture/transfer/npm-private-cache.tar" "$mutation" <<'PY'
import json,pathlib,tarfile,tempfile,sys
p=pathlib.Path(sys.argv[1]);m=sys.argv[2]
with tempfile.TemporaryDirectory() as d:
 r=pathlib.Path(d)
 with tarfile.open(p,"r:") as a:a.extractall(r,filter="data")
 if m=="provenance":
  q=r/"_compatibility"/"provenance.json";v=json.loads(q.read_text());v["lanes"][0]["range"]="^9.0.0";q.write_text(json.dumps(v)+"\n")
 else:next((r/"_cacache"/"content-v2"/"sha512").glob("*/*/*")).write_bytes(b"tampered")
 with tarfile.open(p,"w:") as a:a.add(r/"_cacache",arcname="_cacache");a.add(r/"_compatibility",arcname="_compatibility")
PY
  p="$fixture/transfer/npm-private-cache.tar"; bytes="$(stat -c %s "$p")"; sha="$(sha256sum "$p" | cut -d' ' -f1)"; sed -i "s/^payload_bytes=.*/payload_bytes=$bytes/;s/^payload_sha256=.*/payload_sha256=$sha/" "$fixture/transfer/manifest"
  run_install "$fixture" >/dev/null 2>&1 && fail "tampered compatibility $mutation passed verification" || pass "tampered compatibility $mutation fails before consumer code"
done

mkdir -p "$tmp/e2e/consumer/artifacts" "$tmp/e2e/consumer/compat-results" \
  "$tmp/e2e/consumer/.git" \
  "$tmp/e2e/consumer/node_modules/@verjson/identity-contracts"
cp "$tmp/e2e/build/artifacts/"* "$tmp/e2e/consumer/artifacts/"
printf '%s\n' '{"name":"@verjson/identity-contracts","version":"0.1.0"}' \
  > "$tmp/e2e/consumer/node_modules/@verjson/identity-contracts/package.json"
printf '%s\n' '# compatibility sandbox mount target' > "$tmp/e2e/consumer/.git/config"
printf '%s\n' '{"name":"consumer","version":"1.0.0","scripts":{"test:compat":"node test-compat.js"}}' > "$tmp/e2e/consumer/package.json"
cat > "$tmp/e2e/consumer/test-compat.js" <<'JS'
const fs = require('node:fs');
const version = require('./node_modules/@verjson/identity-contracts/package.json').version;
fs.writeFileSync('compat-results/observed-version', version);
fs.writeFileSync('compat-results/service-env.json', JSON.stringify({
  DB_HOST: process.env.DB_HOST ?? null,
  DB_PORT: process.env.DB_PORT ?? null,
  CACHE_PORT: process.env.CACHE_PORT ?? null,
  DATABASE_URL: process.env.DATABASE_URL ?? null,
  CACHE_URL: process.env.CACHE_URL ?? null,
  DB_ENV: process.env.DB_ENV ?? null,
  CACHE_ENV: process.env.CACHE_ENV ?? null,
  CI_SCRIPT_PLAN: process.env.CI_SCRIPT_PLAN ?? null,
}));
try {
  fs.appendFileSync('.git/config', '\n# consumer write probe\n');
  fs.writeFileSync('compat-results/git-write-allowed', 'true');
} catch (error) {
  if (!['EROFS', 'EACCES'].includes(error.code)) throw error;
  fs.writeFileSync('compat-results/git-write-denied', error.code);
}
if (process.env.REJECT_COMPATIBILITY === 'true') {
  console.error('bounded-consumer-failure');
  process.exit(42);
}
JS
consumer_stderr="$tmp/e2e/consumer/run.stderr"
if (cd "$tmp/e2e/consumer" && CI_SCRIPT_PLAN='[{"script":"test:compat","requiresServices":false}]' DB_ENV='DATABASE_URL=postgres://localhost:5432/app_test' CACHE_ENV='CACHE_URL=redis://localhost:6379/0' DB_HOST=127.0.0.1 DB_PORT=5432 CACHE_PORT=6379 DATABASE_URL=postgres://localhost:5432/app_test CACHE_URL=redis://localhost:6379/0 COMPATIBILITY_ARTIFACT_DIR="$tmp/e2e/consumer/artifacts" COMPATIBILITY_RANGES="$request" EXPECTED_COMPATIBILITY_PROVENANCE_SHA256="$provenance_sha" REJECT_COMPATIBILITY=false bash "$tmp/run-lanes.sh") >"$tmp/e2e/consumer/run.stdout" 2>"$consumer_stderr"; then
  consumer_status=0
else
  consumer_status=$?
fi
if [ "$consumer_status" -eq 0 ] \
  && grep -qFx 0.2.2 "$tmp/e2e/consumer/compat-results/observed-version" \
  && grep -qFx '{"DB_HOST":null,"DB_PORT":null,"CACHE_PORT":null,"DATABASE_URL":null,"CACHE_URL":null,"DB_ENV":null,"CACHE_ENV":null,"CI_SCRIPT_PLAN":null}' "$tmp/e2e/consumer/compat-results/service-env.json"; then
  pass "the resolved in-range artifact reaches the declared consumer test"
else
  fail "the resolved artifact did not reach the declared consumer test"
  emit_failure_diagnostic "resolved compatibility consumer" "$consumer_status" "$consumer_stderr"
fi
if [ "${VERJSON_DIAGNOSTIC_MUTATION_CHILD:-false}" != true ]; then
  if [ ! -e "$tmp/e2e/consumer/compat-results/git-write-allowed" ] \
    && grep -qFx 'EROFS' "$tmp/e2e/consumer/compat-results/git-write-denied" \
    && grep -qFx '# compatibility sandbox mount target' "$tmp/e2e/consumer/.git/config"; then
    pass "compatibility consumer cannot write the host checkout's .git configuration"
  else
    fail "compatibility sandbox exposed a writable host .git configuration"
  fi
  if (cd "$tmp/e2e/consumer" && CI_SCRIPT_PLAN='[{"script":"test:compat","requiresServices":true}]' DB_ENV='DATABASE_URL=postgres://localhost:5432/app_test' CACHE_ENV='CACHE_URL=redis://localhost:6379/0' DB_HOST=127.0.0.1 DB_PORT=5432 CACHE_PORT=6379 DATABASE_URL=postgres://localhost:5432/app_test CACHE_URL=redis://localhost:6379/0 COMPATIBILITY_ARTIFACT_DIR="$tmp/e2e/consumer/artifacts" COMPATIBILITY_RANGES="$request" EXPECTED_COMPATIBILITY_PROVENANCE_SHA256="$provenance_sha" REJECT_COMPATIBILITY=false bash "$tmp/run-lanes.sh") >"$tmp/e2e/authorized-services.log" 2>&1; then
    if grep -qFx '{"DB_HOST":"127.0.0.1","DB_PORT":"5432","CACHE_PORT":"6379","DATABASE_URL":"postgres://localhost:5432/app_test","CACHE_URL":"redis://localhost:6379/0","DB_ENV":null,"CACHE_ENV":null,"CI_SCRIPT_PLAN":null}' "$tmp/e2e/consumer/compat-results/service-env.json"; then
      pass "only the declared service-enabled compatibility script receives service values"
    else
      fail "declared service-enabled compatibility script received an incorrect environment"
    fi
  else
    fail "declared service-enabled compatibility script could not run"
  fi
fi
if (cd "$tmp/e2e/consumer" && COMPATIBILITY_ARTIFACT_DIR="$tmp/e2e/consumer/artifacts" COMPATIBILITY_RANGES="$request" EXPECTED_COMPATIBILITY_PROVENANCE_SHA256="$provenance_sha" REJECT_COMPATIBILITY=true bash "$tmp/run-lanes.sh") >"$tmp/e2e/rejected.log" 2>&1; then
  fail "an in-range incompatible artifact did not fail consumer tests"
elif grep -qF "command=['npm', 'run', 'test:compat'] exit=42" "$tmp/e2e/rejected.log" \
    && grep -qF "bounded-consumer-failure" "$tmp/e2e/rejected.log" \
    && [ "$(wc -c < "$tmp/e2e/rejected.log")" -lt 18000 ]; then
  pass "consumer failure reports command, exit status, and bounded output"
else
  fail "consumer failure omitted its bounded command context"
fi
if (cd "$tmp/e2e/consumer" && COMPATIBILITY_ARTIFACT_DIR="$tmp/e2e/consumer/artifacts" COMPATIBILITY_RANGES="$request" EXPECTED_COMPATIBILITY_PROVENANCE_SHA256="$provenance_sha" NODE_AUTH_TOKEN=leaked bash "$tmp/run-lanes.sh") >"$tmp/e2e/token.log" 2>&1; then fail "a package credential reached compatibility consumer execution"; elif grep -qF 'credential reached compatibility consumer execution' "$tmp/e2e/token.log"; then pass "consumer execution fails closed on a credential leak"; else fail "token-leak mutation failed for the wrong reason"; fi

real_npm="$host_npm"
mkdir -p "$tmp/archive-cases/bin"
runtime_cache_run_id=4102
runtime_cache_run_attempt=1
runtime_cache_name="secretless-runtime-cache-$runtime_cache_run_id-$runtime_cache_run_attempt"
public_cache_sentinel=sha512/aa/bb/sentinel
ambient_root="$tmp/archive-cases/ambient"
mkdir -p "$ambient_root/uppercase-cache" "$ambient_root/lowercase-cache" \
  "$ambient_root/unlisted-cache" \
  "$ambient_root/home/.npm"
printf '%s\n' ambient-uppercase-secret > "$ambient_root/uppercase-cache/secret"
printf '%s\n' ambient-lowercase-secret > "$ambient_root/lowercase-cache/secret"
printf '%s\n' ambient-default-cache-secret > "$ambient_root/home/.npm/secret"
printf '%s\n' ambient-unlisted-secret > "$ambient_root/unlisted-cache/secret"
printf '%s\n' '//registry.example/:_authToken=ambient-home-token' > "$ambient_root/home/.npmrc"
printf '%s\n' '//registry.example/:_authToken=ambient-global-token' > "$ambient_root/global.npmrc"
printf '%s\n' '//registry.example/:_authToken=ambient-user-token' > "$ambient_root/user.npmrc"
printf '%s\n' '//registry.example/:_authToken=ambient-lower-global-token' > "$ambient_root/lower-global.npmrc"
printf '%s\n' '//registry.example/:_authToken=ambient-lower-user-token' > "$ambient_root/lower-user.npmrc"
cat > "$tmp/archive-cases/bin/npm" <<'SH'
#!/usr/bin/env bash
set -eu
if [ "${1:-}" = run ] || [ "${1:-}" = pack ]; then
  if [ -x "$REAL_NPM" ]; then
    exec "$REAL_NPM" "$@"
  fi
  exec node "$REAL_NPM_SANDBOX" "$@"
fi
printf 'unexpected graph resolution: %s\n' "$*" > "$NPM_GRAPH_RESOLUTION_MARKER"
exit 97
SH
chmod +x "$tmp/archive-cases/bin/npm"
cp "$tmp/archive-cases/bin/npm" "$tool_bin/npm"

prepare_archive_case() {
  local mutation="$1"
  local fixture="$tmp/archive-cases/$mutation"
  rm -rf "$fixture"
  mkdir -p "$fixture/artifacts" \
    "$fixture/compat-results" \
    "$fixture/node_modules/@verjson/identity-contracts" \
    "$fixture/node_modules/cold-cache-public" \
    "$fixture/cold-cache/_cacache/content-v2/sha512"
  rm -rf "$tmp/archive-cases/runner-temps/$mutation"
  mkdir -p "$tmp/archive-cases/runner-temps/$mutation"
  if [ "$mutation" = public-cache ] || [ "$mutation" = public-cache-masked ] \
    || [ "$mutation" = public-cache-residue ] \
    || [ "$mutation" = public-cache-file-link ]; then
    local content_root="$tmp/archive-cases/runner-temps/$mutation/$runtime_cache_name/_cacache/content-v2"
    mkdir -p "$content_root/${public_cache_sentinel%/*}"
    printf '%s\n' verified-public-blob > "$content_root/$public_cache_sentinel"
    if [ "$mutation" = public-cache-file-link ]; then
      printf '%s\n' host-content-must-not-cross-boundary > "$fixture/outside-cache"
      ln -s "$fixture/outside-cache" "$content_root/top-level-file-link"
    fi
  fi
  printf '%s\n' '{"name":"@verjson/identity-contracts","version":"0.1.0"}' \
    > "$fixture/node_modules/@verjson/identity-contracts/package.json"
  printf '%s\n' old > "$fixture/node_modules/@verjson/identity-contracts/old-sentinel"
  printf '%s\n' '{"name":"cold-cache-public","version":"1.4.0","main":"index.js"}' \
    > "$fixture/node_modules/cold-cache-public/package.json"
  printf '%s\n' 'module.exports = "cold-cache-public-1.4.0";' \
    > "$fixture/node_modules/cold-cache-public/index.js"
  printf '%s\n' \
    '{"name":"consumer","version":"1.0.0","dependencies":{"cold-cache-public":"^1.0.0"},"scripts":{"test:compat":"node test-compat.cjs"}}' \
    > "$fixture/package.json"
  mkdir -p "$fixture/.cjs-build"
  cat > "$fixture/test-compat.cjs" <<'JS'
const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');

assert.equal(require('./node_modules/@verjson/identity-contracts/package.json').version, '0.2.2');
assert.equal(require('cold-cache-public'), 'cold-cache-public-1.4.0');
assert.equal(process.env.NPM_CONFIG_CACHE, '/dev/shm/npm-cache');
assert.equal(process.env.npm_config_cache, '/dev/shm/npm-cache');
assert.equal(process.env.NPM_CONFIG_GLOBALCONFIG, '/dev/shm/npm-globalconfig');
assert.equal(process.env.NPM_CONFIG_USERCONFIG, '/dev/shm/npm-userconfig');
assert.equal(process.env.npm_config_globalconfig, '/dev/shm/npm-globalconfig');
assert.equal(process.env.npm_config_userconfig, '/dev/shm/npm-userconfig');
fs.mkdirSync(`${process.env.npm_config_cache}/consumer`, {recursive: true});
fs.writeFileSync(`${process.env.npm_config_cache}/consumer/write-proof`, 'writable');
childProcess.execFileSync('npm', ['pack', '--silent', '--dry-run'], {stdio: 'pipe'});
for (const path of process.env.AMBIENT_DIRECTORY_PROBES.split(':')) {
  assert.throws(() => fs.readFileSync(`${path}/secret`, 'utf8'));
}
for (const path of process.env.AMBIENT_FILE_PROBES.split(':')) {
  try {
    assert.equal(fs.readFileSync(path, 'utf8'), '');
  } catch (error) {
    assert.ok(error && ['EACCES', 'ENOENT'].includes(error.code));
  }
}
if (fs.existsSync('ambient-cache-link')) {
  assert.throws(() => fs.readFileSync('ambient-cache-link/secret', 'utf8'));
}
if (process.env.PUBLIC_CACHE_SENTINEL) {
  const contentRoot = `${process.env.npm_config_cache}/_cacache/content-v2`;
  assert.equal(
    fs.readFileSync(`${contentRoot}/${process.env.PUBLIC_CACHE_SENTINEL}`, 'utf8'),
    'verified-public-blob\n',
  );
  fs.writeFileSync(`${contentRoot}/${process.env.PUBLIC_CACHE_SENTINEL}`, 'rewritten\n');
  fs.writeFileSync(`${contentRoot}/intruder`, 'consumer-write');
  if (process.env.PUBLIC_CACHE_RESIDUE) {
    fs.mkdirSync(`${contentRoot}/sealed`, {recursive: true});
    fs.chmodSync(`${contentRoot}/sealed`, 0o000);
  }
}
const mountRemoval = childProcess.spawnSync('rmdir', ['.cjs-build'], {encoding: 'utf8'});
assert.notEqual(mountRemoval.status, 0);
assert.match(mountRemoval.stderr, /busy/i);
fs.writeFileSync('compat-results/consumer-ran', 'yes');
JS
  python3 - "$fixture" "$mutation" "$request" <<'PY'
import base64
import gzip
import hashlib
import io
import json
import pathlib
import sys
import tarfile

fixture = pathlib.Path(sys.argv[1])
mutation = sys.argv[2]
request = json.loads(sys.argv[3])
artifact = fixture / "artifacts" / "lane-0.tgz"
manifest = {
    "name": "@verjson/identity-contracts",
    "version": "0.2.2",
    "scripts": {"postinstall": "node -e process.exit(99)"},
}
if mutation == "wrong-name":
    manifest["name"] = "@verjson/other"
if mutation == "wrong-version":
    manifest["version"] = "9.9.9"

def add_bytes(archive, name, content, mode=0o644):
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.size = len(content)
    archive.addfile(info, io.BytesIO(content))

if mutation == "oversize":
    with gzip.open(artifact, "wb") as stream:
        info = tarfile.TarInfo("package/oversize.bin")
        info.size = 64 * 1024 * 1024 + 1
        stream.write(info.tobuf())
        stream.write(b"\0" * 1024)
else:
    with tarfile.open(artifact, "w:gz") as archive:
        add_bytes(archive, "package/package.json", json.dumps(manifest).encode())
        add_bytes(archive, "package/index.js", b"export const compatible = true;\n")
        if mutation == "traversal":
            add_bytes(archive, "package/../../escape", b"escaped")
        elif mutation == "absolute":
            add_bytes(archive, "/package/escape", b"escaped")
        elif mutation == "multi-root":
            add_bytes(archive, "other/escape", b"escaped")
        elif mutation in {"symlink", "hardlink", "special", "pax"}:
            info = tarfile.TarInfo("package/unsafe")
            if mutation == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "../../escape"
            elif mutation == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = "package/package.json"
            elif mutation == "special":
                info.type = tarfile.CHRTYPE
                info.devmajor = 1
                info.devminor = 3
            else:
                info.type = tarfile.XHDTYPE
                content = b"path=package/unsafe\n"
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
                info = None
            if info is not None:
                archive.addfile(info)
        elif mutation == "count":
            for index in range(4096):
                info = tarfile.TarInfo(f"package/members/{index}")
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
        elif mutation == "duplicate":
            add_bytes(archive, "package/package.json", json.dumps(manifest).encode())

artifact_bytes = artifact.read_bytes()
digest = hashlib.sha512(artifact_bytes).digest()
lane = {
    "index": 0,
    "package": request["package"],
    "range": request["ranges"][0],
    "script": request["script"],
    "version": "0.2.2",
    "integrity": "sha512-" + base64.b64encode(digest).decode(),
    "tarball": "https://npm.pkg.github.com/download/@verjson/identity-contracts/0.2.2/archive",
    "sha512": digest.hex(),
}
provenance = json.dumps(
    {"schemaVersion": 1, "request": request, "lanes": [lane]},
    sort_keys=True,
    separators=(",", ":"),
) + "\n"
(fixture / "artifacts" / "provenance.json").write_text(provenance)
(fixture / "provenance.sha256").write_text(hashlib.sha256(provenance.encode()).hexdigest())

public_tarball = io.BytesIO()
with tarfile.open(fileobj=public_tarball, mode="w:gz") as archive:
    add_bytes(archive, "package/package.json", b'{"name":"cold-cache-public","version":"1.4.0"}')
public_bytes = public_tarball.getvalue()
public_digest = hashlib.sha512(public_bytes).digest()
public_hex = public_digest.hex()
cache_entry = fixture / "cold-cache" / "_cacache" / "content-v2" / "sha512" / public_hex[:2] / public_hex[2:4] / public_hex[4:]
cache_entry.parent.mkdir(parents=True)
cache_entry.write_bytes(public_bytes)
lock = {
    "name": "consumer",
    "version": "1.0.0",
    "lockfileVersion": 3,
    "packages": {
        "": {"name": "consumer", "version": "1.0.0", "dependencies": {"cold-cache-public": "^1.0.0"}},
        "node_modules/cold-cache-public": {
            "version": "1.4.0",
            "resolved": "https://registry.npmjs.org/cold-cache-public/-/cold-cache-public-1.4.0.tgz",
            "integrity": "sha512-" + base64.b64encode(public_digest).decode(),
        },
    },
}
(fixture / "package-lock.json").write_text(json.dumps(lock) + "\n")
PY
  case "$mutation" in
    relative-workspace-link)
      ln -s ../ambient/unlisted-cache "$fixture/ambient-cache-link"
      ;;
    absolute-workspace-link)
      ln -s "$ambient_root/unlisted-cache" "$fixture/ambient-cache-link"
      ;;
  esac
}

archive_case_env=()
run_archive_case() {
  local mutation="$1"
  local fixture="$tmp/archive-cases/$mutation"
  local runner="${2:-$tmp/run-lanes.sh}"
  prepare_archive_case "$mutation"
  (
    cd "$fixture" || exit 1
    PATH="$tool_bin:$tmp/archive-cases/bin:$PATH" \
    REAL_NPM="$real_npm" \
    REAL_NPM_SANDBOX=/opt/verjson-node-toolchain/real-npm/bin/npm-cli.js \
    NPM_GRAPH_RESOLUTION_MARKER="$fixture/npm-graph-resolution" \
    HOME="$ambient_root/home" \
    NPM_CONFIG_CACHE="$ambient_root/uppercase-cache" \
    npm_config_cache="$ambient_root/lowercase-cache" \
    NPM_CONFIG_GLOBALCONFIG="$ambient_root/global.npmrc" \
    NPM_CONFIG_USERCONFIG="$ambient_root/user.npmrc" \
    npm_config_globalconfig="$ambient_root/lower-global.npmrc" \
    npm_config_userconfig="$ambient_root/lower-user.npmrc" \
    AMBIENT_DIRECTORY_PROBES="$ambient_root/uppercase-cache:$ambient_root/lowercase-cache:$ambient_root/home/.npm" \
    AMBIENT_FILE_PROBES="$ambient_root/global.npmrc:$ambient_root/user.npmrc:$ambient_root/lower-global.npmrc:$ambient_root/lower-user.npmrc:$ambient_root/home/.npmrc" \
    npm_config_offline=true \
    COMPATIBILITY_ARTIFACT_DIR="$fixture/artifacts" \
    COMPATIBILITY_RANGES="$request" \
    EXPECTED_COMPATIBILITY_PROVENANCE_SHA256="$(<"$fixture/provenance.sha256")" \
    env "${archive_case_env[@]}" bash "$runner"
  ) >"$fixture/run.stdout" 2>"$fixture/run.stderr"
}

run_public_cache_case() {
  local mutation="$1" runtime_cache="$2" requested="${3-}"
  archive_case_env=(
    "RUNNER_TEMP=$tmp/archive-cases/runner-temps/$mutation"
    "RUN_ATTEMPT=$runtime_cache_run_attempt"
    "RUN_ID=$runtime_cache_run_id"
  )
  if [ -n "$runtime_cache" ]; then
    archive_case_env+=("RUNTIME_CACHE_DIR=$runtime_cache")
  fi
  if [ -n "$requested" ]; then
    archive_case_env+=("SECRETLESS_RUNTIME_PUBLIC_CACHE=$requested")
  fi
  shift 3 2>/dev/null || shift "$#"
  if [ "$#" -gt 0 ]; then
    archive_case_env+=("$@")
  fi
  if [ "$mutation" = public-cache ] || [ "$mutation" = public-cache-residue ]; then
    archive_case_env+=("PUBLIC_CACHE_SENTINEL=$public_cache_sentinel")
  fi
  run_archive_case "$mutation"
  local status=$?
  archive_case_env=()
  return "$status"
}

if run_archive_case good; then
  archive_status=0
else
  archive_status=$?
fi
if [ "$archive_status" -eq 0 ] \
  && [ -f "$tmp/archive-cases/good/compat-results/consumer-ran" ] \
  && [ ! -e "$tmp/archive-cases/good/npm-graph-resolution" ] \
  && [ ! -e "$tmp/archive-cases/good/node_modules/@verjson/identity-contracts/old-sentinel" ] \
  && [ "$(find "$tmp/archive-cases/good/node_modules/@verjson" -maxdepth 1 -name '.identity-contracts.*' -print -quit)" = '' ]; then
  pass "cold-cache caret consumer swaps one verified package without resolving its graph"
else
  fail "cold-cache caret consumer did not preserve its installed dependency graph"
  emit_failure_diagnostic \
    "cold-cache compatibility consumer" \
    "$archive_status" \
    "$tmp/archive-cases/good/run.stderr"
fi

if run_public_cache_case public-cache \
  "$tmp/archive-cases/runner-temps/public-cache/$runtime_cache_name"; then
  public_cache_status=0
else
  public_cache_status=$?
fi
public_cache_content="$tmp/archive-cases/runner-temps/public-cache/$runtime_cache_name/_cacache/content-v2"
if [ "$public_cache_status" -eq 0 ] \
  && [ -f "$tmp/archive-cases/public-cache/compat-results/consumer-ran" ] \
  && [ "$(<"$public_cache_content/$public_cache_sentinel")" = verified-public-blob ] \
  && [ ! -e "$public_cache_content/intruder" ]; then
  pass "verified public cache content reaches the sandbox without exposing the job cache"
else
  fail "verified public cache content did not reach the sandbox, or exposed the job cache"
  emit_failure_diagnostic \
    "verified public cache compatibility consumer" \
    "$public_cache_status" \
    "$tmp/archive-cases/public-cache/run.stderr"
fi

if run_public_cache_case public-cache-file-link \
  "$tmp/archive-cases/runner-temps/public-cache-file-link/$runtime_cache_name"; then
  fail "a top-level public-cache file symlink reached the compatibility sandbox"
elif [ -e "$tmp/archive-cases/public-cache-file-link/compat-results/consumer-ran" ]; then
  fail "a top-level public-cache file symlink ran consumer code before rejection"
elif grep -qF 'compatibility public cache blob is not a regular file' \
  "$tmp/archive-cases/public-cache-file-link/run.stderr" \
  && ! grep -qF 'host-content-must-not-cross-boundary' \
  "$tmp/archive-cases/public-cache-file-link/run.stderr"; then
  pass "a top-level public-cache file symlink is rejected by lstat before copying"
else
  fail "a top-level public-cache file symlink failed without the confinement reason"
fi

if run_public_cache_case public-cache-absent \
  "$tmp/archive-cases/runner-temps/public-cache-absent/$runtime_cache_name" false; then
  absent_cache_status=0
else
  absent_cache_status=$?
fi
if [ "$absent_cache_status" -eq 0 ] \
  && [ -f "$tmp/archive-cases/public-cache-absent/compat-results/consumer-ran" ]; then
  pass "a caller without a runtime public cache still starts the compatibility sandbox"
else
  fail "a caller without a runtime public cache could not start the compatibility sandbox"
  emit_failure_diagnostic \
    "absent public cache compatibility consumer" \
    "$absent_cache_status" \
    "$tmp/archive-cases/public-cache-absent/run.stderr"
fi

if run_public_cache_case public-cache-requested-unset '' true; then
  fail "a requested public cache without its workflow env key started the sandbox"
elif [ -e "$tmp/archive-cases/public-cache-requested-unset/compat-results/consumer-ran" ]; then
  fail "a requested public cache without its workflow env key ran consumer code"
elif grep -qF 'compatibility public cache is requested without a runtime cache path' \
  "$tmp/archive-cases/public-cache-requested-unset/run.stderr"; then
  pass "a requested public cache without its workflow env key fails closed, not open"
else
  fail "a requested public cache without its workflow env key failed without naming its reason"
fi

if run_public_cache_case public-cache-requested-missing \
  "$tmp/archive-cases/runner-temps/public-cache-requested-missing/$runtime_cache_name" true; then
  fail "a requested public cache the population step never wrote started the sandbox"
elif [ -e "$tmp/archive-cases/public-cache-requested-missing/compat-results/consumer-ran" ]; then
  fail "a requested public cache the population step never wrote ran consumer code"
elif grep -qF 'compatibility public cache is requested but was never populated' \
  "$tmp/archive-cases/public-cache-requested-missing/run.stderr"; then
  pass "a requested public cache the population step never wrote fails closed, not open"
else
  fail "a requested public cache the population step never wrote failed without naming its reason"
fi

# The install step populates the runtime cache when SECRETLESS_RUNTIME_PUBLIC_CACHE
# is true *or* when RESTORE_PERSISTED_PUBLIC_CACHE is, the latter being
# `inputs.cache && inputs.package-manager == 'npm'`. Reading only the former as
# "a cache was expected" leaves an ordinary `cache: true` npm caller resolving to
# a silent None with the cache populated and bound -- Verjson/.github#1372 intact
# for the larger share of adopters.
if run_public_cache_case public-cache-persisted-missing \
  "$tmp/archive-cases/runner-temps/public-cache-persisted-missing/$runtime_cache_name" false \
  RESTORE_PERSISTED_PUBLIC_CACHE=true; then
  fail "a persisted public cache the population step never wrote started the sandbox"
elif [ -e "$tmp/archive-cases/public-cache-persisted-missing/compat-results/consumer-ran" ]; then
  fail "a persisted public cache the population step never wrote ran consumer code"
elif grep -qF 'compatibility public cache is requested but was never populated' \
  "$tmp/archive-cases/public-cache-persisted-missing/run.stderr"; then
  pass "a persisted-cache caller whose runtime cache is absent fails closed, not open"
else
  fail "a persisted-cache caller whose runtime cache is absent failed without naming its reason"
fi

if run_public_cache_case public-cache-persisted-unset '' false \
  RESTORE_PERSISTED_PUBLIC_CACHE=true; then
  fail "a persisted public cache without its workflow env key started the sandbox"
elif [ -e "$tmp/archive-cases/public-cache-persisted-unset/compat-results/consumer-ran" ]; then
  fail "a persisted public cache without its workflow env key ran consumer code"
elif grep -qF 'compatibility public cache is requested without a runtime cache path' \
  "$tmp/archive-cases/public-cache-persisted-unset/run.stderr"; then
  pass "a persisted-cache caller without its workflow env key fails closed, not open"
else
  fail "a persisted-cache caller without its workflow env key failed without naming its reason"
fi

# An ambient mask equal to the sandbox bind target would append its --tmpfs
# after the bind and shadow it, failing open to Verjson/.github#1372.
# Consumer code runs as the runner's uid and can seal a staged directory, so
# the staging copy's cleanup must report residue rather than swallow it.
if run_public_cache_case public-cache-residue \
  "$tmp/archive-cases/runner-temps/public-cache-residue/$runtime_cache_name" true \
  PUBLIC_CACHE_RESIDUE=1; then
  residue_status=0
else
  residue_status=$?
fi
if [ "$residue_status" -ne 0 ]; then
  fail "sealing a staged directory broke the compatibility run instead of leaving residue"
elif [ ! -f "$tmp/archive-cases/public-cache-residue/compat-results/consumer-ran" ]; then
  fail "the staging-residue case never ran consumer code"
elif grep -qF '::warning::compatibility public cache staging was not removed' \
  "$tmp/archive-cases/public-cache-residue/run.stderr"; then
  pass "staging residue a consumer sealed is reported rather than silently left behind"
else
  fail "staging residue a consumer sealed was swallowed instead of reported"
fi
chmod -R u+rwX "$tmp/archive-cases/runner-temps/public-cache-residue" 2>/dev/null || true

if run_public_cache_case public-cache-masked \
  "$tmp/archive-cases/runner-temps/public-cache-masked/$runtime_cache_name" true \
  NPM_CONFIG_CACHE=/dev/shm/npm-cache; then
  fail "an ambient mask shadowing the public cache bind reached consumer execution"
elif [ -e "$tmp/archive-cases/public-cache-masked/compat-results/consumer-ran" ]; then
  fail "an ambient mask shadowing the public cache bind ran consumer code"
elif grep -qF 'ambient npm path overlaps compatibility public cache bind' \
  "$tmp/archive-cases/public-cache-masked/run.stderr"; then
  pass "an ambient mask shadowing the public cache bind is refused, not layered over it"
else
  fail "an ambient mask shadowing the public cache bind failed without naming its reason"
fi

if run_public_cache_case public-cache-foreign "$tmp/archive-cases/foreign-cache"; then
  fail "a runtime public cache outside the run's own path reached the sandbox"
elif [ -e "$tmp/archive-cases/public-cache-foreign/compat-results/consumer-ran" ]; then
  fail "a runtime public cache outside the run's own path ran consumer code"
elif grep -qF 'compatibility public cache is not the workflow runtime cache' \
  "$tmp/archive-cases/public-cache-foreign/run.stderr"; then
  pass "a runtime public cache outside the run's own path is refused, not guessed at"
else
  fail "a foreign runtime public cache was refused without naming its reason"
fi

# The ambient-mask overlap guard has to cover every mountpoint the public-cache
# arguments create. Deriving those paths by positional slice holds only while
# every element is exactly a `--bind src dst` triple: one argument of different
# arity re-indexes the slice onto a source path or a flag, and the guard stops
# covering the real target without failing. Exercise the derivation directly
# with a differently-shaped list rather than trusting the comment.
python3 - "$tmp/run-lanes.sh" "$tmp/protected-run-lanes.sh" <<'PY'
import ast
import re
import subprocess
import sys
from pathlib import Path

MARKER = "python3 - <<'PY'\n"
TARGET = "/dev/shm/npm-cache/_cacache/content-v2"
STAGING = "/runner-temp/verjson-compatibility-public-cache-x/content-v2"
EXPECTED_MOUNT_OPTIONS = {
    "--bind": ("SRC", "DEST"),
    "--dir": ("DEST",),
    "--ro-bind": ("SRC", "DEST"),
    "--ro-bind-try": ("SRC", "DEST"),
    "--tmpfs": ("DEST",),
}


def bwrap_option_operands():
    completed = subprocess.run(
        ["/usr/bin/bwrap", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    options = {}
    for line in (completed.stdout + completed.stderr).splitlines():
        synopsis = re.split(r"\s{2,}", line.strip(), maxsplit=1)[0]
        fields = synopsis.split()
        if fields and fields[0].startswith("--"):
            options[fields[0]] = tuple(fields[1:])
    return options


def assert_mount_table_matches_bwrap(table, bwrap_options):
    assert set(table) == set(EXPECTED_MOUNT_OPTIONS), (
        "PUBLIC_CACHE_MOUNT_ARITY changed without an explicit supported-flag decision"
    )
    for option, expected_operands in EXPECTED_MOUNT_OPTIONS.items():
        assert bwrap_options.get(option) == expected_operands, (
            f"installed bwrap synopsis for {option} changed: "
            f"{bwrap_options.get(option)!r}"
        )
        arity, destination = table[option]
        assert arity == len(expected_operands), option
        assert destination == expected_operands.index("DEST") + 1, option


bwrap_options = bwrap_option_operands()

for lane_path in sys.argv[1:]:
    source = Path(lane_path).read_text(encoding="utf-8")
    start = source.find(MARKER)
    end = source.find("\nPY\n", start)
    assert start >= 0 and end > start, f"{lane_path}: embedded consumer source missing"
    module = ast.parse(source[start + len(MARKER):end + 1])
    definitions = [
        node
        for node in module.body
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "public_cache_bind_targets"
        )
        or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "PUBLIC_CACHE_MOUNT_ARITY"
                for target in node.targets
            )
        )
    ]
    assert len(definitions) == 2, (
        f"{lane_path}: guarded paths are not derived from the bind arguments' own shape"
    )
    namespace = {"Path": Path}
    exec(compile(ast.Module(definitions, type_ignores=[]), "<lanes>", "exec"), namespace)
    derive = namespace["public_cache_bind_targets"]
    mount_table = namespace["PUBLIC_CACHE_MOUNT_ARITY"]
    assert_mount_table_matches_bwrap(mount_table, bwrap_options)

    # Negative controls prove both arity drift and an unreviewed expansion of
    # the accepted subset are rejected by the bwrap synopsis contract.
    malformed_tables = []
    wrong_arity = dict(mount_table)
    wrong_arity["--tmpfs"] = (2, 1)
    malformed_tables.append(wrong_arity)
    expanded_subset = dict(mount_table)
    expanded_subset["--bind-try"] = (2, 2)
    malformed_tables.append(expanded_subset)
    for malformed_table in malformed_tables:
        try:
            assert_mount_table_matches_bwrap(malformed_table, bwrap_options)
        except AssertionError:
            continue
        raise AssertionError(f"{lane_path}: malformed bwrap mount table was accepted")

    assert derive(["--bind", STAGING, TARGET]) == [Path(TARGET)], lane_path
    assert derive([]) == [], lane_path
    # A leading argument of a different arity must not move the guard.
    reshaped = ["--tmpfs", "/dev/shm/npm-cache/_cacache/index-v5",
                "--bind", STAGING, TARGET]
    assert Path(TARGET) in derive(reshaped), (
        f"{lane_path}: a differently-shaped argument list silently moved the guard"
    )
    # Every mountpoint the arguments create is guarded, not only --bind targets.
    assert derive(reshaped)[0] == Path("/dev/shm/npm-cache/_cacache/index-v5"), lane_path
    for malformed in (
        ["--bind", STAGING],
        ["--tmpfs"],
        ["--unsupported-flag", STAGING, TARGET],
        [STAGING, TARGET],
    ):
        try:
            derive(malformed)
        except SystemExit:
            continue
        raise AssertionError(f"{lane_path}: {malformed!r} was accepted without a guarded target")
PY
[ "$?" -eq 0 ] \
  && pass "the ambient-mask guard derives its paths from the bind arguments' own shape" \
  || fail "the ambient-mask guard re-indexes when a bind argument of different arity is added"

if run_archive_case protected-good "$tmp/protected-run-lanes.sh" \
  && [ -f "$tmp/archive-cases/protected-good/compat-results/consumer-ran" ]; then
  pass "protected compatibility workflow masks ambient npm cache and config paths"
else
  fail "protected compatibility workflow exposed an ambient npm cache or config path"
fi

for link_kind in relative absolute; do
  fixture="$tmp/archive-cases/${link_kind}-workspace-link"
  if run_archive_case "${link_kind}-workspace-link"; then
    fail "$link_kind top-level workspace symlink reached consumer execution"
  elif [ -e "$fixture/compat-results/consumer-ran" ]; then
    fail "$link_kind top-level workspace symlink ran consumer code before rejection"
  elif grep -qF "compatibility workspace top-level symlink" "$fixture/run.stderr"; then
    pass "$link_kind top-level workspace symlink cannot reach ambient npm data"
  else
    fail "$link_kind top-level workspace symlink failed without confinement reason"
  fi
done

for mutation in traversal absolute multi-root symlink hardlink special pax oversize count duplicate wrong-name wrong-version; do
  if run_archive_case "$mutation"; then
    fail "$mutation compatibility archive reached consumer execution"
  elif [ -e "$tmp/archive-cases/$mutation/compat-results/consumer-ran" ] \
      || [ -e "$tmp/archive-cases/$mutation/npm-graph-resolution" ] \
      || [ -e "$tmp/archive-cases/$mutation/escape" ]; then
    fail "$mutation compatibility archive caused work before rejection"
  elif [ "$(node -p "require('$tmp/archive-cases/$mutation/node_modules/@verjson/identity-contracts/package.json').version")" != 0.1.0 ] \
    || [ ! -f "$tmp/archive-cases/$mutation/node_modules/@verjson/identity-contracts/old-sentinel" ]; then
    fail "$mutation compatibility archive disturbed the pre-existing package target"
  elif [ -n "$(find "$tmp/archive-cases/$mutation/node_modules/@verjson" -maxdepth 1 -name '.identity-contracts.*' -print -quit)" ]; then
    fail "$mutation compatibility archive left a staging or backup directory"
  else
    pass "$mutation compatibility archive fails before target swap or consumer code"
  fi
done

if [ "${VERJSON_DIAGNOSTIC_MUTATION_CHILD:-false}" != true ]; then
  mutation_root="$tmp/missing-bwrap-mutation"
  mkdir -p "$mutation_root/.github/workflows" "$mutation_root/scripts/ci-gate" \
    "$mutation_root/docs"
  cp "$0" "$mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh"
  cp "$root/scripts/container_dependency_transfer.py" "$mutation_root/scripts/container_dependency_transfer.py"
  cp "$protected_workflow" "$mutation_root/.github/workflows/node-ci-protected.yml"
  cp "$documentation" "$mutation_root/docs/node-workflows.md"
  python3 - "$workflow" "$mutation_root/.github/workflows/node-ci.yml" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8")
compatibility_start = source.index("def public_cache_bind_targets")
compatibility = source[compatibility_start:]
needle = 'bubblewrap = Path("/usr/bin/bwrap")'
assert compatibility.count(needle) == 1
Path(sys.argv[2]).write_text(
    source[:compatibility_start]
    + compatibility.replace(needle, 'bubblewrap = Path("/definitely-missing/bwrap")'),
    encoding="utf-8",
)
PY
  if VERJSON_DIAGNOSTIC_MUTATION_CHILD=true \
    bash "$mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh" \
    >"$mutation_root/run.log" 2>&1; then
    mutation_status=0
  else
    mutation_status=$?
  fi
  if [ "$mutation_status" -eq 9 ] \
    && grep -qFx 'not ok - verified public cache content did not reach the sandbox, or exposed the job cache' "$mutation_root/run.log" \
    && grep -qFx 'not ok - a caller without a runtime public cache could not start the compatibility sandbox' "$mutation_root/run.log" \
    && grep -qFx 'not ok - sealing a staged directory broke the compatibility run instead of leaving residue' "$mutation_root/run.log" \
    && grep -qFx 'not ok - an ambient mask shadowing the public cache bind failed without naming its reason' "$mutation_root/run.log" \
    && grep -qFx 'diagnostic - resolved compatibility consumer return-code=1 stderr-category=bubblewrap-unavailable' "$mutation_root/run.log" \
    && grep -qFx 'diagnostic - cold-cache compatibility consumer return-code=1 stderr-category=bubblewrap-unavailable' "$mutation_root/run.log" \
    && grep -qFx 'diagnostic - verified public cache compatibility consumer return-code=1 stderr-category=bubblewrap-unavailable' "$mutation_root/run.log" \
    && grep -qFx 'diagnostic - absent public cache compatibility consumer return-code=1 stderr-category=bubblewrap-unavailable' "$mutation_root/run.log" \
    && ! grep -qF 'trusted bubblewrap compatibility sandbox is unavailable' "$mutation_root/run.log" \
    && ! grep -qF 'consumer-controlled-sentinel' "$mutation_root/run.log"; then
    pass "missing-bwrap mutation reports both positive failures with exact allowlisted categories"
  else
    fail "missing-bwrap mutation did not preserve exact allowlisted positive-failure categories"
  fi

  cache_mutation_root="$tmp/ambient-cache-mutation"
  mkdir -p "$cache_mutation_root/.github/workflows" "$cache_mutation_root/scripts/ci-gate" \
    "$cache_mutation_root/docs"
  cp "$0" "$cache_mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh"
  cp "$root/scripts/container_dependency_transfer.py" "$cache_mutation_root/scripts/container_dependency_transfer.py"
  cp "$protected_workflow" "$cache_mutation_root/.github/workflows/node-ci-protected.yml"
  cp "$documentation" "$cache_mutation_root/docs/node-workflows.md"
  python3 - "$workflow" "$cache_mutation_root/.github/workflows/node-ci.yml" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8")
compatibility_start = source.index("def public_cache_bind_targets")
prefix = source[:compatibility_start]
compatibility = source[compatibility_start:]
for needle in (
    '                  "--dir", "/dev/shm/npm-cache",\n',
    '                      "NPM_CONFIG_CACHE": "/dev/shm/npm-cache",\n',
    '                      "npm_config_cache": "/dev/shm/npm-cache",\n',
):
    assert compatibility.count(needle) == 1
    compatibility = compatibility.replace(needle, "")
Path(sys.argv[2]).write_text(prefix + compatibility, encoding="utf-8")
PY
  if VERJSON_DIAGNOSTIC_MUTATION_CHILD=true VERJSON_CACHE_MUTATION_CHILD=true \
    bash "$cache_mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh" \
    >"$cache_mutation_root/run.log" 2>&1; then
    cache_mutation_status=0
  else
    cache_mutation_status=$?
  fi
  if [ "$cache_mutation_status" -eq 4 ] \
    && grep -qFx 'not ok - cold-cache caret consumer did not preserve its installed dependency graph' "$cache_mutation_root/run.log" \
    && grep -qFx 'not ok - verified public cache content did not reach the sandbox, or exposed the job cache' "$cache_mutation_root/run.log" \
    && grep -qFx 'not ok - a caller without a runtime public cache could not start the compatibility sandbox' "$cache_mutation_root/run.log" \
    && grep -qFx 'not ok - sealing a staged directory broke the compatibility run instead of leaving residue' "$cache_mutation_root/run.log" \
    && grep -qFx 'diagnostic - cold-cache compatibility consumer return-code=1 stderr-category=stderr-suppressed' "$cache_mutation_root/run.log" \
    && grep -qFx 'diagnostic - verified public cache compatibility consumer return-code=1 stderr-category=stderr-suppressed' "$cache_mutation_root/run.log" \
    && grep -qFx 'diagnostic - absent public cache compatibility consumer return-code=1 stderr-category=stderr-suppressed' "$cache_mutation_root/run.log"; then
    pass "ambient read-only npm cache mutation reproduces the silent npm failure"
  else
    fail "ambient read-only npm cache mutation did not reproduce the silent npm failure"
  fi

  mask_mutation_root="$tmp/ambient-mask-mutation"
  mkdir -p "$mask_mutation_root/.github/workflows" "$mask_mutation_root/scripts/ci-gate" \
    "$mask_mutation_root/docs"
  cp "$0" "$mask_mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh"
  cp "$root/scripts/container_dependency_transfer.py" "$mask_mutation_root/scripts/container_dependency_transfer.py"
  cp "$protected_workflow" "$mask_mutation_root/.github/workflows/node-ci-protected.yml"
  cp "$documentation" "$mask_mutation_root/docs/node-workflows.md"
  python3 - "$workflow" "$mask_mutation_root/.github/workflows/node-ci.yml" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8")
start = source.index("              ambient_masks = {\n")
end = source.index(
    "              for entry in sorted(workspace.iterdir(), key=lambda path: path.name):\n",
    start,
)
Path(sys.argv[2]).write_text(source[:start] + source[end:], encoding="utf-8")
PY
  if VERJSON_DIAGNOSTIC_MUTATION_CHILD=true \
    VERJSON_AMBIENT_MASK_MUTATION_CHILD=true \
    bash "$mask_mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh" \
    >"$mask_mutation_root/run.log" 2>&1; then
    mask_mutation_status=0
  else
    mask_mutation_status=$?
  fi
  if [ "$mask_mutation_status" -eq 1 ] \
    && grep -qFx 'not ok - an ambient mask shadowing the public cache bind reached consumer execution' "$mask_mutation_root/run.log"; then
    pass "removing ambient npm masks exposes the real absolute-path escape probe"
  else
    fail "ambient npm mask mutation did not expose the absolute-path escape probe"
  fi

  symlink_mutation_root="$tmp/workspace-symlink-mutation"
  mkdir -p "$symlink_mutation_root/.github/workflows" \
    "$symlink_mutation_root/scripts/ci-gate" "$symlink_mutation_root/docs"
  cp "$0" "$symlink_mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh"
  cp "$root/scripts/container_dependency_transfer.py" "$symlink_mutation_root/scripts/container_dependency_transfer.py"
  cp "$protected_workflow" "$symlink_mutation_root/.github/workflows/node-ci-protected.yml"
  cp "$documentation" "$symlink_mutation_root/docs/node-workflows.md"
  python3 - "$workflow" "$symlink_mutation_root/.github/workflows/node-ci.yml" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8")
start = source.index("                  if entry.is_symlink():\n")
end = source.index("                  add_sandbox_binding(\n", start)
Path(sys.argv[2]).write_text(source[:start] + source[end:], encoding="utf-8")
PY
  if VERJSON_DIAGNOSTIC_MUTATION_CHILD=true \
    VERJSON_SYMLINK_GUARD_MUTATION_CHILD=true \
    bash "$symlink_mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh" \
    >"$symlink_mutation_root/run.log" 2>&1; then
    symlink_mutation_status=0
  else
    symlink_mutation_status=$?
  fi
  if [ "$symlink_mutation_status" -eq 2 ] \
    && grep -qFx 'not ok - relative top-level workspace symlink reached consumer execution' "$symlink_mutation_root/run.log" \
    && grep -qFx 'not ok - absolute top-level workspace symlink reached consumer execution' "$symlink_mutation_root/run.log"; then
    pass "removing workspace symlink confinement admits a link and exposes the absolute escape probe"
  else
    fail "workspace symlink confinement mutation did not expose its link escape probes"
  fi

  public_cache_link_mutation_root="$tmp/public-cache-file-link-mutation"
  mkdir -p "$public_cache_link_mutation_root/.github/workflows" \
    "$public_cache_link_mutation_root/scripts/ci-gate" "$public_cache_link_mutation_root/docs"
  cp "$0" "$public_cache_link_mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh"
  cp "$root/scripts/container_dependency_transfer.py" "$public_cache_link_mutation_root/scripts/container_dependency_transfer.py"
  cp "$protected_workflow" "$public_cache_link_mutation_root/.github/workflows/node-ci-protected.yml"
  cp "$documentation" "$public_cache_link_mutation_root/docs/node-workflows.md"
  python3 - "$workflow" "$public_cache_link_mutation_root/.github/workflows/node-ci.yml" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8")
needle = "blob_metadata = blob.lstat()"
assert source.count(needle) == 1
Path(sys.argv[2]).write_text(
    source.replace(needle, "blob_metadata = blob.stat()"), encoding="utf-8"
)
PY
  if VERJSON_DIAGNOSTIC_MUTATION_CHILD=true \
    bash "$public_cache_link_mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh" \
    >"$public_cache_link_mutation_root/run.log" 2>&1; then
    public_cache_link_mutation_status=0
  else
    public_cache_link_mutation_status=$?
  fi
  if [ "$public_cache_link_mutation_status" -eq 1 ] \
    && grep -qFx 'not ok - a top-level public-cache file symlink reached the compatibility sandbox' \
    "$public_cache_link_mutation_root/run.log" \
    && ! grep -qFx 'ok - a top-level public-cache file symlink is rejected by lstat before copying' \
    "$public_cache_link_mutation_root/run.log"; then
    pass "the malicious-cache fixture fails when lstat is weakened to stat"
  else
    fail "the malicious-cache fixture did not detect a weakened file-type check"
  fi

  absent_cache_mutation_root="$tmp/absent-public-cache-mutation"
  mkdir -p "$absent_cache_mutation_root/.github/workflows" \
    "$absent_cache_mutation_root/scripts/ci-gate" "$absent_cache_mutation_root/docs"
  cp "$0" "$absent_cache_mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh"
  cp "$root/scripts/container_dependency_transfer.py" "$absent_cache_mutation_root/scripts/container_dependency_transfer.py"
  cp "$protected_workflow" "$absent_cache_mutation_root/.github/workflows/node-ci-protected.yml"
  cp "$documentation" "$absent_cache_mutation_root/docs/node-workflows.md"
  python3 - "$workflow" "$absent_cache_mutation_root/.github/workflows/node-ci.yml" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8")
needle = (
    "                      # secretless-runtime-public-cache is off, so the runtime\n"
    "                      # cache was never created. Start the sandbox without it.\n"
    "                      return None\n"
)
assert source.count(needle) == 1
Path(sys.argv[2]).write_text(
    source.replace(needle, "                      pass\n"), encoding="utf-8"
)
PY
  if VERJSON_DIAGNOSTIC_MUTATION_CHILD=true \
    bash "$absent_cache_mutation_root/scripts/ci-gate/node-ci-secretless-compatibility.test.sh" \
    >"$absent_cache_mutation_root/run.log" 2>&1; then
    absent_cache_mutation_status=0
  else
    absent_cache_mutation_status=$?
  fi
  if [ "$absent_cache_mutation_status" -eq 1 ] \
    && grep -qFx 'not ok - a caller without a runtime public cache could not start the compatibility sandbox' "$absent_cache_mutation_root/run.log"; then
    pass "binding an absent runtime public cache would break every caller that has none"
  else
    fail "the absent runtime public cache guard is not load-bearing"
  fi
fi

exit "$failures"
