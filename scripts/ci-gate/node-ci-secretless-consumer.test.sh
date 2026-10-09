#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/node-ci.yml"
failures=0
tmp="$(mktemp -d)"
runner_temp="$tmp/runner-temp"
tool_cache="$tmp/tool-cache"
toolchain="$tool_cache/node/24.19.0"
mkdir -p "$tmp/home" "$runner_temp" "$toolchain/bin"
export RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux BWRAP_BINARY="${BWRAP_BINARY:-/usr/bin/bwrap}" HOME="$tmp/home" RUNNER_TEMP="$runner_temp" RUNNER_TOOL_CACHE="$tool_cache"
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
assert "sandbox_entrypoint" in rebuild["run"]
assert "os.closerange(3, max_fd)" in rebuild["run"]
assert "os.execvpe(sys.argv[1], sys.argv[1:], os.environ)" in rebuild["run"]
assert 'os.execve(str(bubblewrap), arguments,' in rebuild["run"]
assert 'subprocess.run([*npm_command, "run", name]' in plan["run"]
assert "env=script_env" in plan["run"]
for command_file in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE", "GITHUB_STEP_SUMMARY"):
    assert f'"{command_file}"' in plan["run"]
compatibility = next(
    step for step in build["steps"]
    if step.get("name") == "Run runtime-resolved compatibility lanes without credentials"
)
assert "unset -v GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT GITHUB_STATE GITHUB_STEP_SUMMARY" in compatibility["run"]
assert "exec /usr/bin/python3 - <<'PY'" in compatibility["run"]
for command_file in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE", "GITHUB_STEP_SUMMARY"):
    assert f'"{command_file}"' in compatibility["run"]
secretless_bash_env = "${{ (inputs.secretless-pr || inputs.secretless-trusted-ref) && '/dev/null' || env.BASH_ENV }}"
secretless_mode = "${{ inputs.secretless-pr || inputs.secretless-trusted-ref }}"
command_files = (
    "GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE",
    "GITHUB_STEP_SUMMARY", "BASH_ENV",
)
command_file_scrub = "unset -v " + " ".join(command_files)
default_plans = []
for workflow_path in (
    Path(sys.argv[1]),
    Path(sys.argv[1]).with_name("node-ci-protected.yml"),
):
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    workflow_build = workflow["jobs"]["build-test"]
    steps = workflow_build["steps"]
    if workflow_path.name == "node-ci.yml":
        defaults = [
            step for step in steps
            if step.get("env", {}).get("SECRETLESS_MODE") == secretless_mode
        ]
        assert len(defaults) == 4
    else:
        defaults = [
            step for step in steps
            if step.get("name") == "Run default build, typecheck, test, and lint plan"
        ]
        assert len(defaults) == 1
    for step in defaults:
        assert step["env"]["BASH_ENV"] == secretless_bash_env
        assert step["env"]["SECRETLESS_MODE"] == secretless_mode
        assert command_file_scrub in step["run"]
        assert "secretless-ci-script-plan" in step["if"]
    default_plans.append((workflow_path.name, defaults))

for workflow_name, defaults in default_plans:
    fixture = Path(sys.argv[2]) / f"default-plan-{workflow_name}"
    bin_dir = fixture / "bin"
    bin_dir.mkdir(parents=True)
    command_file = fixture / "runner-env"
    command_file.write_text("", encoding="utf-8")
    marker = fixture / "injected-bash-env-ran"
    attack = fixture / "injected-bash-env"
    attack.write_text(f"touch {marker}\n", encoding="utf-8")
    command_log = fixture / "npm-commands"
    environment_log = fixture / "npm-command-file-env"
    npm = bin_dir / "npm"
    npm.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$*" >> "$NPM_COMMAND_LOG"\n'
        'for name in GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT GITHUB_STATE '
        'GITHUB_STEP_SUMMARY BASH_ENV; do\n'
        '  if [[ -v "$name" ]]; then printf "%s\\n" "$name" >> "$NPM_ENV_LOG"; fi\n'
        "done\n"
        'if [[ -v GITHUB_ENV ]]; then printf "BASH_ENV=%s\\n" "$BASH_ENV_ATTACK" >> "$GITHUB_ENV"; fi\n',
        encoding="utf-8",
    )
    npm.chmod(0o755)
    runner_environment = os.environ.copy()
    runner_environment.update({
        "PATH": f"{bin_dir}:{runner_environment['PATH']}",
        "BASH_ENV": str(attack),
        "BASH_ENV_ATTACK": str(attack),
        "GITHUB_ENV": str(command_file),
        "GITHUB_PATH": str(fixture / "runner-path"),
        "GITHUB_OUTPUT": str(fixture / "runner-output"),
        "GITHUB_STATE": str(fixture / "runner-state"),
        "GITHUB_STEP_SUMMARY": str(fixture / "runner-summary"),
        "NPM_COMMAND_LOG": str(command_log),
        "NPM_ENV_LOG": str(environment_log),
        "SECRETLESS_MODE": "true",
    })
    # GitHub expands the step-level expression to this value for secretless mode.
    runner_environment["BASH_ENV"] = "/dev/null"
    for step in defaults:
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", step["run"]],
            cwd=fixture,
            env=runner_environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert not marker.exists(), f"{workflow_name} sourced an injected BASH_ENV"
        assert command_file.read_text(encoding="utf-8") == ""
        assert not environment_log.exists(), "npm received a runner command-file path"
    expected_commands = sum(
        line.strip().startswith("npm ")
        for step in defaults
        for line in step["run"].splitlines()
    )
    assert len(command_log.read_text(encoding="utf-8").splitlines()) == expected_commands
print("secretless default plans cannot write runner command files or inject BASH_ENV")
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

mkdir -p "$tmp/bin" "$tmp/commands"
cat > "$tmp/bin/npm" <<'NPM'
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
chmod +x "$tmp/bin/npm"
printf '%s\n' '{"scripts":{"verify:worker-schema":"fixture","build":"fixture","audit:deps":"fixture","lint":"fixture","test":"fixture","typecheck:smoke":"fixture","smoke:otel":"fixture"}}' \
  > "$tmp/commands/package.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/argon2":{"name":"argon2","hasInstallScript":true},"node_modules/esbuild":{"name":"esbuild","hasInstallScript":true},"node_modules/leftpad":{"name":"leftpad"},"node_modules/@esbuild/linux-x64":{"name":"@esbuild/linux-x64","optional":true,"os":["linux"],"hasInstallScript":true}}}' \
  > "$tmp/commands/package-lock.json"
mkdir -p "$tmp/commands/node_modules/argon2" "$tmp/commands/node_modules/esbuild" "$tmp/commands/node_modules/leftpad"

plan='["verify:worker-schema","build","audit:deps","lint","test","typecheck:smoke",{"script":"smoke:otel","unsetEnv":["OTEL_SDK_DISABLED"]}]'
if (cd "$tmp/commands" && PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$tmp/plan.log" \
    NPM_STUB_ENV_LOG="$tmp/plan-env.log" OTEL_SDK_DISABLED=true \
    GITHUB_ENV="$tmp/runner-env" GITHUB_PATH="$tmp/runner-path" \
    GITHUB_OUTPUT="$tmp/runner-output" GITHUB_STATE="$tmp/runner-state" \
    GITHUB_STEP_SUMMARY="$tmp/runner-summary" \
    CI_SCRIPT_PLAN="$plan" bash "$tmp/plan.sh") \
    && [ "$(wc -l < "$tmp/plan.log")" -eq 7 ] \
    && [ "$(head -1 "$tmp/plan.log")" = 'run verify:worker-schema' ] \
    && [ "$(tail -1 "$tmp/plan.log")" = 'run smoke:otel' ] \
    && grep -qFx 'smoke:otel=unset' "$tmp/plan-env.log"; then
  pass "the exact consumer npm script plan runs in order with a bounded per-script environment clear"
else
  fail "the exact consumer npm script plan did not run in order"
fi
for command_file in GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT GITHUB_STATE GITHUB_STEP_SUMMARY; do
  grep -qFx "$command_file=unset" "$tmp/plan-env.log" \
    && pass "untrusted consumer cannot access $command_file" \
    || fail "untrusted consumer received $command_file"
done

# Node 26's validated toolcache layout keeps the launcher in bin/ but the npm
# package in lib/node_modules/npm. The launcher fixture resolves npm-prefix.js
# relative to bin/, reproducing the broken lookup without copying tool files.
node26="$tmp/node26"
mkdir -p "$node26/bin" "$node26/lib/node_modules/npm/bin"
ln -s "$(command -v node)" "$node26/bin/node"
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
: > "$tmp/node26-plan.log"
node26_plan_status=0
(cd "$tmp/commands" && PATH="$node26/bin:/usr/bin:/bin" \
    NPM_STUB_LOG="$tmp/node26-plan.log" NODE26_TOOLCACHE="$node26" \
    CI_SCRIPT_PLAN='["build"]' bash "$tmp/plan.sh") \
    > "$tmp/node26-plan-output.log" 2>&1 || node26_plan_status=$?
if [ "$node26_plan_status" -eq 0 ] && [ "$(cat "$tmp/node26-plan.log")" = 'run build' ]; then
  pass "the consumer script plan resolves npm from the Node 26 toolcache package layout"
elif grep -Fq "$node26/bin/node_modules/npm/bin/npm-prefix.js" "$tmp/node26-plan-output.log"; then
  fail "the Node 26 npm launcher looked for missing $node26/bin/node_modules/npm/bin/npm-prefix.js"
else
  fail "the Node 26 npm launcher layout failed: $(tail -1 "$tmp/node26-plan-output.log")"
fi

# Node 24 can expose npm itself as a symlink to npm-cli.js. The raw
# credentialless path must derive and deduplicate the resolved package root.
node24="$tmp/node24"
mkdir -p "$node24/bin" "$node24/lib/node_modules/npm/bin"
ln -s "$(command -v node)" "$node24/bin/node"
cat > "$node24/lib/node_modules/npm/bin/npm-cli.js" <<'JS'
#!/usr/bin/env node
const fs = require("node:fs");
fs.appendFileSync(process.env.NPM_STUB_LOG, `${process.argv.slice(2).join(" ")}\n`);
JS
chmod +x "$node24/lib/node_modules/npm/bin/npm-cli.js"
ln -s ../lib/node_modules/npm/bin/npm-cli.js "$node24/bin/npm"
: > "$tmp/node24-plan.log"
node24_plan_status=0
(cd "$tmp/commands" && PATH="$node24/bin:/usr/bin:/bin" \
    NPM_STUB_LOG="$tmp/node24-plan.log" \
    CI_SCRIPT_PLAN='["build"]' bash "$tmp/plan.sh") \
    > "$tmp/node24-plan-output.log" 2>&1 || node24_plan_status=$?
if [ "$node24_plan_status" -eq 0 ] && [ "$(cat "$tmp/node24-plan.log")" = 'run build' ]; then
  pass "the credentialless consumer plan resolves a Node 24 npm CLI symlink"
else
  fail "the Node 24 npm CLI symlink layout failed: $(tail -1 "$tmp/node24-plan-output.log")"
fi

for bad_plan in '["build","build"]' '["--help"]' '["missing"]' '{"script":"build"}' \
  '[{"script":"build","unsetEnv":["NODE_AUTH_TOKEN"]}]'; do
  : > "$tmp/bad-plan.log"
  if (cd "$tmp/commands" && PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$tmp/bad-plan.log" \
      CI_SCRIPT_PLAN="$bad_plan" bash "$tmp/plan.sh") >/dev/null 2>&1 \
      || [ -s "$tmp/bad-plan.log" ]; then
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
