#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/node-ci.yml"
failures=0
tmp="$(mktemp -d -p /var/tmp verjson-node-ci-secretless-consumer.XXXXXX)"
runner_temp="$tmp/runner-temp"
tool_cache="$tmp/tool-cache"
toolchain="$tool_cache/node/24.19.0"
mkdir -p "$tmp/home" "$runner_temp" "$toolchain/bin"
export RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux BWRAP_BINARY="${BWRAP_BINARY:-/usr/bin/bwrap}" HOME="$tmp/home" RUNNER_TEMP="$runner_temp" RUNNER_TOOL_CACHE="$tool_cache" GITHUB_ENV="$runner_temp/_runner_file_commands/GITHUB_ENV"
cat > "$toolchain/bin/node" <<'NODE'
#!/usr/bin/env bash
exit 0
NODE
cat > "$toolchain/bin/npm" <<'NPM'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$GITHUB_WORKSPACE/node_modules/.npm-stub.log"
NPM
chmod +x "$toolchain/bin/node" "$toolchain/bin/npm"
trap 'rm -rf "$tmp"' EXIT

pass() { printf 'ok - %s\n' "$1"; }
fail() { printf 'not ok - %s\n' "$1" >&2; failures=$((failures + 1)); }

python3 - "$workflow" "$tmp" <<'PY' \
  && pass "consumer extensions remain validated, credentialless, and canonical" \
  || fail "consumer extension workflow structure violates the secretless contract"
import os
import subprocess
import sys
from pathlib import Path

import yaml

doc = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
inputs = doc[True]["workflow_call"]["inputs"]
assert inputs["approved-internal-scopes"]["default"] == "@verjson"
assert inputs["secretless-auxiliary-source"]["default"] == ""
assert inputs["secretless-rebuild-packages"]["default"] == ""
assert inputs["secretless-ci-script-plan"]["default"] == ""

acquire = doc["jobs"]["acquire-secretless-dependencies"]
steps = acquire["steps"]
validator_index = next(i for i, step in enumerate(steps) if step.get("name") == "Validate approved internal dependency lock")
resolve_index = next(i for i, step in enumerate(steps) if step.get("name") == "Resolve immutable auxiliary source")
checkout_index = next(i for i, step in enumerate(steps) if step.get("name") == "Acquire immutable auxiliary source")
install_index = next(i for i, step in enumerate(steps) if step.get("name") == "Populate verified private dependency cache")
assert validator_index < resolve_index < checkout_index < install_index
checkout = steps[checkout_index]
assert checkout["uses"] == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
assert checkout["with"]["ref"] == "${{ steps.resolve-auxiliary-source.outputs.commit }}"
assert checkout["with"]["token"] == "${{ secrets.NODE_AUTH_TOKEN }}"
assert checkout["with"]["persist-credentials"] is False
resolver = steps[resolve_index]
assert resolver["env"]["TRUSTED_AUXILIARY_POLICY"] == "${{ vars.CI_SECRETLESS_AUXILIARY_POLICY }}"
assert "source != policy" in resolver["run"]
cleanup = next(step for step in steps if step.get("name") == "Remove local acquisition and transfer state")
assert cleanup["env"]["AUXILIARY_CHECKOUT_PATH"] == "${{ steps.resolve-auxiliary-source.outputs.checkout-path }}"
assert 'find "$AUXILIARY_CHECKOUT_PATH" -depth -delete' in cleanup["run"]

build = doc["jobs"]["build-test"]
rebuild = next(step for step in build["steps"] if step.get("name") == "Rebuild exact approved lifecycle packages without credentials")
plan = next(step for step in build["steps"] if step.get("name") == "Run exact credentialless consumer script plan")
assert "inputs.secretless-pr" in rebuild["if"] and "secrets." not in str(rebuild.get("env", {}))
assert "inputs.secretless-pr" in plan["if"] and "secrets." not in str(plan.get("env", {}))
assert '"--tmpfs", "/"' in plan["run"]
assert '"--ro-bind", "/", "/"' not in plan["run"]
assert "sandbox_entrypoint" in rebuild["run"]
assert "os.closerange(3, max_fd)" in rebuild["run"]
assert "os.execvpe(sys.argv[1], sys.argv[1:], os.environ)" in rebuild["run"]
assert 'os.execve(str(bubblewrap), arguments,' in rebuild["run"]
assert 'str(bubblewrap)' in plan["run"]
assert '"GITHUB_WORKSPACE": sandbox_workspace' in plan["run"]
assert '"RUNNER_TEMP": "/tmp"' in plan["run"]
assert 'bind_source(workspace, sandbox_workspace, writable=True)' in plan["run"]
assert 'bind_source(git_metadata, f"{sandbox_workspace}/.git")' in plan["run"]
assert '"--file", str(global_config_file.fileno()), "/dev/shm/npm-globalconfig"' in plan["run"]
assert "global_config_file.fileno()" in plan["run"]
assert '"PATH": f"{node_toolchain_bin}:/usr/bin:/bin"' in plan["run"]
assert "secretless candidate scripts require a GitHub-hosted Linux runner" in plan["run"]
assert "env=script_env" in plan["run"]
assert plan["env"]["BASH_ENV"] == "/dev/null"
for command_file in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE", "GITHUB_STEP_SUMMARY"):
    assert f'"{command_file}"' in plan["run"]
compatibility = next(
    step for step in build["steps"]
    if step.get("name") == "Run runtime-resolved compatibility lanes without credentials"
)
assert '"--tmpfs", "/"' in compatibility["run"]
assert '"--ro-bind", "/", "/"' not in compatibility["run"]
assert '"--ro-bind", "/usr", "/usr"' in compatibility["run"]
assert "unset -v GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT GITHUB_STATE GITHUB_STEP_SUMMARY" in compatibility["run"]
assert "exec /usr/bin/python3 - <<'PY'" in compatibility["run"]
for command_file in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE", "GITHUB_STEP_SUMMARY"):
    assert f'"{command_file}"' in compatibility["run"]
assert plan["env"]["RUN_DEFAULTS"] == "${{ inputs.secretless-ci-script-plan == '' }}"
assert "inputs.secretless-pr" in plan["if"] and "inputs.secretless-trusted-ref" in plan["if"]
assert "subprocess.run(" in plan["run"]
assert '"--chdir", str(sandbox_directory), "--",' in plan["run"]
assert '"/usr/bin/python3", "-c", sandbox_entrypoint' in plan["run"]
assert "pass_fds=tuple(" in plan["run"]
assert "global_config_file.fileno()" in plan["run"]
assert "env=script_env" in plan["run"]
for command_file in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE", "GITHUB_STEP_SUMMARY"):
    assert f'"{command_file}"' in plan["run"]
protected_doc = yaml.safe_load(
    Path(sys.argv[1]).with_name("node-ci-protected.yml").read_text(encoding="utf-8")
)
protected_build = protected_doc["jobs"]["build-test"]
protected_plan = next(
    step for step in protected_build["steps"]
    if step.get("name") == "Run exact credentialless consumer script plan"
)
assert '"--tmpfs", "/"' in protected_plan["run"]
assert '"--ro-bind", "/", "/"' not in protected_plan["run"]
assert '*([] if requires_services else ["--unshare-net"])' in protected_plan["run"]
assert "if requires_services:" in protected_plan["run"]
assert "script_env.update(candidate_service_env)" in protected_plan["run"]
assert "candidate scripts requiring services need an isolated GitHub-hosted runner" in protected_plan["run"]
assert '"--unshare-net"' in protected_plan["run"]
assert 'script_env = {' in protected_plan["run"]
assert "candidate_service_env" in protected_plan["run"]
assert "credential_environment_name_pattern" in protected_plan["run"]
assert "credential_parameter_name_pattern" in protected_plan["run"]
assert "candidate service URL contains credential-bearing data" in protected_plan["run"]
assert "credentialed candidate service URLs must target the local service" in protected_plan["run"]
assert '"COREPACK_"' in protected_plan["run"]
assert "git_metadata" in protected_plan["run"] and "git_mount_args" in protected_plan["run"]
assert '"COREPACK_HOME": str(corepack_home) if corepack_home is not None' in protected_plan["run"]
assert '"COREPACK_ENABLE_NETWORK": "0"' in protected_plan["run"]
assert '"--bind" if tool_prefix == browser_cache else "--ro-bind"' in protected_plan["run"]
assert 'from urllib.parse import parse_qsl, unquote, urlsplit' in protected_plan["run"]
assert '"/usr/bin/python3", "-I", "-c", sandbox_entrypoint' in protected_plan["run"]
assert "os.closerange(3, max_fd)" in protected_plan["run"]
assert "os.execvpe(sys.argv[1], sys.argv[1:], os.environ)" in protected_plan["run"]
assert "BASH_ENV" in protected_plan["run"]
assert "env.BASH_ENV" not in protected_plan["run"]
assert '"--unshare-net"' in rebuild["run"]
source_defaults = [
    step for step in build["steps"]
    if step.get("run") in (
        "npm run build", "npm run typecheck --if-present", "npm test", "npm run lint --if-present"
    )
]
assert len(source_defaults) == 4
assert all("!(inputs.secretless-pr || inputs.secretless-trusted-ref)" in step["if"]
           for step in source_defaults)
protected_defaults = next(
    step for step in protected_build["steps"]
    if step.get("name") == "Run default build, typecheck, test, and lint plan"
)
assert "!(inputs.secretless-pr || inputs.secretless-trusted-ref)" in protected_defaults["if"]
assert "env.BASH_ENV" not in protected_defaults["run"]

fixture = Path(sys.argv[2]) / "default-plan"
fixture.mkdir()
tool_cache = Path(sys.argv[2]) / "tool-cache"
toolchain = tool_cache / "node" / "24.19.0" / "x64"
bin_dir = toolchain / "bin"
bin_dir.mkdir(parents=True)
(fixture / ".git").mkdir()
(fixture / "node_modules").mkdir()
(fixture / "package.json").write_text(
    '{"scripts":{"build":"fixture","typecheck":"fixture","test":"fixture","lint":"fixture"}}',
    encoding="utf-8",
)
command_log = fixture / "node_modules" / ".npm-commands"
service_log = fixture / "node_modules" / ".npm-services"
command_files_dir = Path(os.environ["RUNNER_TEMP"]) / "_runner_file_commands"
command_files_dir.mkdir(parents=True)
command_file = command_files_dir / "set_env_probe"
command_file.write_text("BASELINE=preserved\n", encoding="utf-8")
node = bin_dir / "node"
host_home_sentinel = Path(os.environ["HOME"]) / ".host-runner-sentinel"
host_home_sentinel.write_text("runner home must stay hidden\n", encoding="utf-8")
node.write_text(
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    'for host_path in "$HOST_HOME_SENTINEL" /run/docker.sock /var/run/docker.sock; do\n'
    '  [[ ! -e "$host_path" ]] || exit 23\n'
    'done\n'
    'printf "hidden\\n" >> "$HOST_PATH_PROBE_LOG"\n'
    "shift\n"
    'printf "%s\\n" "$*" >> "$NPM_COMMAND_LOG"\n'
    'printf "%s|%s|%s|%s|%s\\n" "$*" "${DATABASE_URL-unset}" '
    '"${OPENAI_API_KEY-unset}" "${POSTGRES_PASSWORD-unset}" "${DB_ENV-unset}" '
    '>> "$NPM_SERVICE_LOG"\n'
    'for name in GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT GITHUB_STATE GITHUB_STEP_SUMMARY; do\n'
    '  if [[ -v "$name" ]]; then exit 9; fi\n'
    "done\n"
    'mkdir -p "$RUNNER_TEMP/_runner_file_commands"\n'
    'printf "BASH_ENV=/tmp/hostile-candidate.sh\\n" >> "$RUNNER_TEMP/_runner_file_commands/set_env_probe"\n',
    encoding="utf-8",
)
node.chmod(0o755)
npm = bin_dir / "npm"
npm.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
npm.chmod(0o755)
npm_cli = toolchain / "lib/node_modules/npm/bin/npm-cli.js"
npm_cli.parent.mkdir(parents=True)
npm_cli.write_text("// fixture npm cli\n", encoding="utf-8")
runner_environment = os.environ.copy()
runner_environment.update({
    "PATH": f"{bin_dir}:/usr/bin:/bin",
    "RUNNER_TOOL_CACHE": str(tool_cache),
    "HOME": str(fixture),
    "NPM_COMMAND_LOG": "/workspace/node_modules/.npm-commands",
    "NPM_SERVICE_LOG": "/workspace/node_modules/.npm-services",
    "HOST_HOME_SENTINEL": str(host_home_sentinel),
    "HOST_PATH_PROBE_LOG": "/workspace/node_modules/.host-path-probes",
    "CI_SCRIPT_PLAN": "",
    "NESTED_MANIFESTS": "",
    "RUN_DEFAULTS": "true",
    "RUNNER_ENVIRONMENT": "github-hosted",
    "RUNNER_OS": "Linux",
    "GITHUB_WORKSPACE": str(fixture),
    "GITHUB_ENV": str(command_file),
    "GITHUB_OUTPUT": str(fixture / "runner-output"),
    "DB_ENV": "POSTGRES_PASSWORD=postgres\nDATABASE_URL=postgres://app:pw@127.0.0.1:5432/app\nOPENAI_API_KEY=ci-dummy-key",
    "POSTGRES_PASSWORD": "postgres",
    "DATABASE_URL": "postgres://app:pw@127.0.0.1:5432/app",
    "OPENAI_API_KEY": "ci-dummy-key",
})
hostile_bash_env = fixture / "hostile-bash-env"
startup_marker = fixture / "bash-env-sourced"
hostile_bash_env.write_text(f"printf sourced > {startup_marker}\n", encoding="utf-8")
hostile_environment = runner_environment.copy()
hostile_environment["BASH_ENV"] = str(hostile_bash_env)
control = subprocess.run(
    ["bash", "--noprofile", "--norc", "-c", "true"],
    cwd=fixture,
    env=hostile_environment,
    check=False,
    capture_output=True,
    text=True,
)
assert control.returncode == 0 and startup_marker.exists(), "hostile BASH_ENV control did not execute"
startup_marker.unlink()
runner_environment["BASH_ENV"] = plan["env"]["BASH_ENV"]
result = subprocess.run(
    ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", plan["run"]],
    cwd=fixture,
    env=runner_environment,
    check=False,
    capture_output=True,
    text=True,
)
assert result.returncode == 0, result.stderr
assert not startup_marker.exists(), "step-level BASH_ENV guard allowed hostile startup code"
assert command_file.read_text(encoding="utf-8") == "BASELINE=preserved\n", (
    "candidate script changed the host runner command file"
)
assert command_log.read_text(encoding="utf-8").splitlines() == [
    "run build", "run typecheck", "run test", "run lint"
]
assert service_log.read_text(encoding="utf-8").splitlines() == [
    "run build|unset|unset|unset|unset",
    "run typecheck|unset|unset|unset|unset",
    "run test|postgres://app:pw@127.0.0.1:5432/app|ci-dummy-key|unset|unset",
    "run lint|unset|unset|unset|unset",
]
assert (fixture / "node_modules/.host-path-probes").read_text(encoding="utf-8").splitlines() == [
    "hidden",
    "hidden",
    "hidden",
    "hidden",
]
print("consumer sandbox hides runner home and Docker socket paths")
print("secretless default plan scopes service variables to the test script")
assert inputs["db-image"]["default"] == ""
assert inputs["cache-image"]["default"] == ""
assert next(step for step in build["steps"] if step.get("name") == "Start database service")
assert next(step for step in build["steps"] if step.get("name") == "Start cache service")

names = {
    "Resolve immutable auxiliary source": "resolve.sh",
    "Rebuild exact approved lifecycle packages without credentials": "rebuild.sh",
    "Run exact credentialless consumer script plan": "plan.sh",
}
for job in doc["jobs"].values():
    for step in job.get("steps", []):
        if step.get("name") in names:
            Path(sys.argv[2], names[step["name"]]).write_text(step["run"], encoding="utf-8")
PY

commit=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
mkdir -p "$tmp/resolver/config"
printf '%s\n' "{\"repository\":\"tequityapp/tequity-worker\",\"commit\":\"$commit\"}" \
  > "$tmp/resolver/config/worker-schema-pin.json"
valid_source='{"repository":"tequityapp/tequity-worker","pinFile":"config/worker-schema-pin.json","checkoutPath":".worker-schema","sparsePath":"migrations"}'

run_resolver() {
  local fixture="$1" source="$2" policy="${3:-$2}"
  : > "$fixture/output"
  (cd "$fixture" && AUXILIARY_SOURCE="$source" TRUSTED_AUXILIARY_POLICY="$policy" \
    GITHUB_OUTPUT="$fixture/output" bash "$tmp/resolve.sh")
}

if run_resolver "$tmp/resolver" "$valid_source" \
    && grep -qFx 'repository=tequityapp/tequity-worker' "$tmp/resolver/output" \
    && grep -qFx "commit=$commit" "$tmp/resolver/output" \
    && grep -qFx 'content-path=.worker-schema/migrations' "$tmp/resolver/output"; then
  pass "an exact repository and immutable pin resolve to one isolated sparse content path"
else
  fail "a valid auxiliary source did not resolve"
fi

resolver_rejects() {
  local name="$1" source="$2" fixture="$tmp/reject-$1"
  mkdir -p "$fixture/config"
  cp "$tmp/resolver/config/worker-schema-pin.json" "$fixture/config/worker-schema-pin.json"
  if run_resolver "$fixture" "$source" >/dev/null 2>&1; then
    fail "$name auxiliary source was accepted"
  else
    pass "$name auxiliary source fails closed"
  fi
}

resolver_rejects "repository-mismatch" '{"repository":"attacker/other","pinFile":"config/worker-schema-pin.json","checkoutPath":".worker-schema","sparsePath":"migrations"}'
if run_resolver "$tmp/resolver" \
    '{"repository":"attacker/other","pinFile":"config/worker-schema-pin.json","checkoutPath":".worker-schema","sparsePath":"secrets"}' \
    "$valid_source" >/dev/null 2>&1; then
  fail "a PR-controlled token-reachable auxiliary source was accepted"
else
  pass "a PR-controlled auxiliary source cannot override trusted repository policy"
fi
resolver_rejects "repository-dot-component" '{"repository":"./other","pinFile":"config/worker-schema-pin.json","checkoutPath":".worker-schema","sparsePath":"migrations"}'
resolver_rejects "checkout-traversal" '{"repository":"tequityapp/tequity-worker","pinFile":"config/worker-schema-pin.json","checkoutPath":"../worker","sparsePath":"migrations"}'
resolver_rejects "normalized-sparse-path" '{"repository":"tequityapp/tequity-worker","pinFile":"config/worker-schema-pin.json","checkoutPath":".worker-schema","sparsePath":"migrations//reviewed"}'
resolver_rejects "output-injection-path" '{"repository":"tequityapp/tequity-worker","pinFile":"config/worker-schema-pin.json","checkoutPath":".worker-schema","sparsePath":"migrations\ncommit=attacker"}'
resolver_rejects "git-pin-path" '{"repository":"tequityapp/tequity-worker","pinFile":".git/pin.json","checkoutPath":".worker-schema","sparsePath":"migrations"}'
resolver_rejects "git-sparse-path" '{"repository":"tequityapp/tequity-worker","pinFile":"config/worker-schema-pin.json","checkoutPath":".worker-schema","sparsePath":"migrations/.git"}'
resolver_rejects "sparse-glob" '{"repository":"tequityapp/tequity-worker","pinFile":"config/worker-schema-pin.json","checkoutPath":".worker-schema","sparsePath":"migrations/**"}'
resolver_rejects "unknown-field" '{"repository":"tequityapp/tequity-worker","pinFile":"config/worker-schema-pin.json","checkoutPath":".worker-schema","sparsePath":"migrations","ref":"main"}'

mkdir -p "$tmp/mutable/config"
printf '%s\n' '{"repository":"tequityapp/tequity-worker","commit":"main"}' > "$tmp/mutable/config/pin.json"
mutable_source='{"repository":"tequityapp/tequity-worker","pinFile":"config/pin.json","checkoutPath":".worker-schema","sparsePath":"migrations"}'
if run_resolver "$tmp/mutable" "$mutable_source" >/dev/null 2>&1; then
  fail "a mutable auxiliary ref was accepted"
else
  pass "a mutable auxiliary ref fails closed before credentialed checkout"
fi

mkdir -p "$tmp/symlink/config" "$tmp/symlink/real"
cp "$tmp/resolver/config/worker-schema-pin.json" "$tmp/symlink/real/pin.json"
ln -s ../real/pin.json "$tmp/symlink/config/pin.json"
symlink_source='{"repository":"tequityapp/tequity-worker","pinFile":"config/pin.json","checkoutPath":".worker-schema","sparsePath":"migrations"}'
if run_resolver "$tmp/symlink" "$symlink_source" >/dev/null 2>&1; then
  fail "a symlinked auxiliary pin file was accepted"
else
  pass "a symlinked auxiliary pin file fails closed"
fi

plan_toolchain="$tool_cache/node/22.0.0/x64"
mkdir -p "$plan_toolchain/bin" "$tmp/commands"
cat > "$plan_toolchain/bin/node" <<'NODE'
#!/usr/bin/env bash
exit 0
NODE
cat > "$plan_toolchain/bin/npm" <<'NPM'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$NPM_STUB_LOG"
if [ -n "${NPM_STUB_ENV_LOG:-}" ]; then
  printf '%s=%s\n' "${*: -1}" "${OTEL_SDK_DISABLED-unset}" >> "$NPM_STUB_ENV_LOG"
  for name in GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT GITHUB_STATE GITHUB_STEP_SUMMARY; do
    if [[ -v $name ]]; then
      printf '%s=%s\n' "$name" "${!name}" >> "$NPM_STUB_ENV_LOG"
    else
      printf '%s=unset\n' "$name" >> "$NPM_STUB_ENV_LOG"
    fi
  done
fi
NPM
chmod +x "$plan_toolchain/bin/node" "$plan_toolchain/bin/npm"
printf '%s\n' '{"scripts":{"verify:worker-schema":"fixture","build":"fixture","audit:deps":"fixture","lint":"fixture","test":"fixture","typecheck:smoke":"fixture","smoke:otel":"fixture"}}' \
  > "$tmp/commands/package.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/argon2":{"name":"argon2","hasInstallScript":true},"node_modules/esbuild":{"name":"esbuild","hasInstallScript":true},"node_modules/leftpad":{"name":"leftpad"},"node_modules/@esbuild/linux-x64":{"name":"@esbuild/linux-x64","optional":true,"os":["linux"],"hasInstallScript":true}}}' \
  > "$tmp/commands/package-lock.json"
mkdir -p "$tmp/commands/.git" "$tmp/commands/node_modules/argon2" "$tmp/commands/node_modules/esbuild" "$tmp/commands/node_modules/leftpad"

plan='["verify:worker-schema","build","audit:deps","lint","test","typecheck:smoke",{"script":"smoke:otel","unsetEnv":["OTEL_SDK_DISABLED"]}]'
if (cd "$tmp/commands" && PATH="$plan_toolchain/bin:$PATH" \
    NPM_STUB_LOG=/workspace/node_modules/.plan.log \
    NPM_STUB_ENV_LOG=/workspace/node_modules/.plan-env.log OTEL_SDK_DISABLED=true \
        GITHUB_ENV="$runner_temp/_runner_file_commands/GITHUB_ENV" GITHUB_PATH="$tmp/runner-path" \
    GITHUB_OUTPUT="$tmp/runner-output" GITHUB_STATE="$tmp/runner-state" \
    GITHUB_STEP_SUMMARY="$tmp/runner-summary" \
    CI_SCRIPT_PLAN="$plan" bash "$tmp/plan.sh") \
    && [ "$(wc -l < "$tmp/commands/node_modules/.plan.log")" -eq 7 ] \
    && [ "$(head -1 "$tmp/commands/node_modules/.plan.log")" = 'run verify:worker-schema' ] \
    && [ "$(tail -1 "$tmp/commands/node_modules/.plan.log")" = 'run smoke:otel' ] \
    && grep -qFx 'smoke:otel=unset' "$tmp/commands/node_modules/.plan-env.log"; then
  pass "the exact consumer npm script plan runs in order with a bounded per-script environment clear"
else
  fail "the exact consumer npm script plan did not run in order"
fi
for command_file in GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT GITHUB_STATE GITHUB_STEP_SUMMARY; do
    grep -qFx "$command_file=unset" "$tmp/commands/node_modules/.plan-env.log" \
    && pass "untrusted consumer cannot access $command_file" \
    || fail "untrusted consumer received $command_file"
done

# Node 26's validated toolcache layout keeps the launcher in bin/ but the npm
# package in lib/node_modules/npm. The launcher fixture resolves npm-prefix.js
# relative to bin/, reproducing the broken lookup without copying tool files.
node26="$tool_cache/node/26.0.0/x64"
mkdir -p "$node26/bin" "$node26/lib/node_modules/npm/bin"
cp -- "$(command -v node)" "$node26/bin/node"
cat > "$node26/bin/npm" <<'JS'
#!/usr/bin/env node
const path = require("node:path");
const prefix = path.join(__dirname, "node_modules", "npm", "bin", "npm-prefix.js");
require(prefix);
require(path.join(__dirname, "..", "lib", "node_modules", "npm", "bin", "npm-cli.js"));
JS
cat > "$node26/lib/node_modules/npm/bin/npm-prefix.js" <<'JS'
module.exports = __dirname;
JS
cat > "$node26/lib/node_modules/npm/bin/npm-cli.js" <<'JS'
const fs = require("node:fs");
fs.appendFileSync(process.env.NPM_STUB_LOG, `${process.argv.slice(2).join(" ")}\n`);
JS
chmod +x "$node26/bin/npm"
: > "$tmp/commands/node_modules/.npm-stub.log"
node26_plan_status=0
(cd "$tmp/commands" && PATH="$node26/bin:/usr/bin:/bin" \
    GITHUB_WORKSPACE="$tmp/commands" NPM_STUB_LOG=/workspace/node_modules/.npm-stub.log \
    NODE26_TOOLCACHE="$node26" \
    CI_SCRIPT_PLAN='["build"]' bash "$tmp/plan.sh") \
    > "$tmp/node26-plan-output.log" 2>&1 || node26_plan_status=$?
if [ "$node26_plan_status" -eq 0 ] && [ "$(cat "$tmp/commands/node_modules/.npm-stub.log")" = 'run build' ]; then
  pass "the consumer script plan resolves npm from the Node 26 toolcache package layout"
elif grep -Fq "$node26/bin/node_modules/npm/bin/npm-prefix.js" "$tmp/node26-plan-output.log"; then
  fail "the Node 26 npm launcher looked for missing $node26/bin/node_modules/npm/bin/npm-prefix.js"
else
  fail "the Node 26 npm launcher layout failed: $(tail -1 "$tmp/node26-plan-output.log")"
fi

# Node 24 can expose npm itself as a symlink to npm-cli.js. The raw
# credentialless path must derive and deduplicate the resolved package root.
node24="$tool_cache/node/24.0.0/x64"
mkdir -p "$node24/bin" "$node24/lib/node_modules/npm/bin"
cp -- "$(command -v node)" "$node24/bin/node"
cat > "$node24/lib/node_modules/npm/bin/npm-cli.js" <<'JS'
#!/usr/bin/env node
const fs = require("node:fs");
fs.appendFileSync(process.env.NPM_STUB_LOG, `${process.argv.slice(2).join(" ")}\n`);
JS
chmod +x "$node24/lib/node_modules/npm/bin/npm-cli.js"
ln -s ../lib/node_modules/npm/bin/npm-cli.js "$node24/bin/npm"
: > "$tmp/commands/node_modules/.npm-stub.log"
node24_plan_status=0
(cd "$tmp/commands" && PATH="$node24/bin:/usr/bin:/bin" \
    GITHUB_WORKSPACE="$tmp/commands" NPM_STUB_LOG=/workspace/node_modules/.npm-stub.log \
    CI_SCRIPT_PLAN='["build"]' bash "$tmp/plan.sh") \
    > "$tmp/node24-plan-output.log" 2>&1 || node24_plan_status=$?
if [ "$node24_plan_status" -eq 0 ] && [ "$(cat "$tmp/commands/node_modules/.npm-stub.log")" = 'run build' ]; then
  pass "the credentialless consumer plan resolves a Node 24 npm CLI symlink"
else
  fail "the Node 24 npm CLI symlink layout failed: $(tail -1 "$tmp/node24-plan-output.log")"
fi

for bad_plan in '["build","build"]' '["--help"]' '["missing"]' '{"script":"build"}' \
  '[{"script":"build","unsetEnv":["NODE_AUTH_TOKEN"]}]'; do
  : > "$tmp/commands/bad-plan.log"
  if (cd "$tmp/commands" && PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$tmp/commands/bad-plan.log" \
      CI_SCRIPT_PLAN="$bad_plan" bash "$tmp/plan.sh") >/dev/null 2>&1 \
      || [ -s "$tmp/commands/bad-plan.log" ]; then
    fail "invalid consumer script plan reached npm: $bad_plan"
  else
    pass "invalid consumer script plan fails before npm: $bad_plan"
  fi
done

if (cd "$tmp/commands" && PATH="$toolchain/bin:/usr/bin:/bin" NPM_STUB_LOG="$tmp/rebuild.log" \
    REBUILD_PACKAGES=$'argon2\nesbuild' bash "$tmp/rebuild.sh") \
    && grep -qFx 'rebuild argon2 esbuild' "$tmp/commands/node_modules/.npm-stub.log"; then
  pass "only exact locked lifecycle packages reach npm rebuild"
else
  fail "approved exact lifecycle packages did not rebuild"
fi

for bad_rebuild in $'argon2\n--foreground-scripts' 'missing-package' $'argon2\nargon2'; do
  : > "$tmp/commands/node_modules/.npm-stub.log"
  if (cd "$tmp/commands" && PATH="$toolchain/bin:/usr/bin:/bin" NPM_STUB_LOG="$tmp/bad-rebuild.log" \
      REBUILD_PACKAGES="$bad_rebuild" bash "$tmp/rebuild.sh") >/dev/null 2>&1 \
      || [ -s "$tmp/commands/node_modules/.npm-stub.log" ]; then
    fail "invalid lifecycle rebuild list reached npm"
  else
    pass "invalid lifecycle rebuild list fails before npm"
  fi
done

# #932: secretless-rebuild-packages must exactly match the lock's install-script
# surface, not merely name packages present in the lock.
: > "$tmp/undeclared-rebuild.log"
if (cd "$tmp/commands" && PATH="$toolchain/bin:/usr/bin:/bin" NPM_STUB_LOG="$tmp/undeclared-rebuild.log" \
    REBUILD_PACKAGES=argon2 bash "$tmp/rebuild.sh") >/dev/null 2>&1 \
    || [ -s "$tmp/commands/node_modules/.npm-stub.log" ]; then
  fail "a lock-declared lifecycle package absent from the allowlist reached npm (#932)"
else
  pass "the lock's install-script surface must be fully named in the allowlist (#932)"
fi

: > "$tmp/unnecessary-rebuild.log"
if (cd "$tmp/commands" && PATH="$toolchain/bin:/usr/bin:/bin" NPM_STUB_LOG="$tmp/unnecessary-rebuild.log" \
    REBUILD_PACKAGES=$'argon2\nesbuild\nleftpad' bash "$tmp/rebuild.sh") >/dev/null 2>&1 \
    || [ -s "$tmp/commands/node_modules/.npm-stub.log" ]; then
  fail "an allowlisted package the lock does not mark as needing install scripts reached npm (#932)"
else
  pass "the allowlist may not name a package the lock does not mark as needing install scripts (#932)"
fi

# #941: a lock-declared install-script package for another platform (never
# installed into node_modules/ on this runner) must not be forced into the
# allowlist, and must not be rebuilt if it somehow were named.
: > "$tmp/cross-platform-rebuild.log"
if (cd "$tmp/commands" && PATH="$toolchain/bin:/usr/bin:/bin" NPM_STUB_LOG="$tmp/cross-platform-rebuild.log" \
    REBUILD_PACKAGES=$'argon2\nesbuild' bash "$tmp/rebuild.sh") \
    && grep -qFx 'rebuild argon2 esbuild' "$tmp/commands/node_modules/.npm-stub.log"; then
  pass "a lock-declared install-script package absent from node_modules/ is not forced into the allowlist (#941)"
else
  fail "a cross-platform lock entry incorrectly demanded allowlisting (#941)"
fi

exit "$failures"
