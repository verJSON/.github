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
assert 'arguments.extend(("--", *command, *requested))' in rebuild["run"]
assert 'os.execve(str(bubblewrap), arguments,' in rebuild["run"]
assert 'subprocess.run([*npm_command, "run", name]' in plan["run"]
assert "env=script_env" in plan["run"]
for command in ("npm run build", "npm run typecheck --if-present", "npm test", "npm run lint --if-present"):
    step = next(step for step in build["steps"] if step.get("run") == command)
    assert "secretless-ci-script-plan" in step["if"]
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
printf '%s\n' '#!/usr/bin/env bash' \
  'printf '\''%s\n'\'' "$*" >> "$NPM_STUB_LOG"' \
  '[ -z "${NPM_STUB_ENV_LOG:-}" ] || printf '\''%s=%s\n'\'' "${*: -1}" "${OTEL_SDK_DISABLED-unset}" >> "$NPM_STUB_ENV_LOG"' \
  > "$tmp/bin/npm"
chmod +x "$tmp/bin/npm"
printf '%s\n' '{"scripts":{"verify:worker-schema":"fixture","build":"fixture","audit:deps":"fixture","lint":"fixture","test":"fixture","typecheck:smoke":"fixture","smoke:otel":"fixture"}}' \
  > "$tmp/commands/package.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/argon2":{"name":"argon2","hasInstallScript":true},"node_modules/esbuild":{"name":"esbuild","hasInstallScript":true},"node_modules/leftpad":{"name":"leftpad"},"node_modules/@esbuild/linux-x64":{"name":"@esbuild/linux-x64","optional":true,"os":["linux"],"hasInstallScript":true}}}' \
  > "$tmp/commands/package-lock.json"
mkdir -p "$tmp/commands/node_modules/argon2" "$tmp/commands/node_modules/esbuild" "$tmp/commands/node_modules/leftpad"

plan='["verify:worker-schema","build","audit:deps","lint","test","typecheck:smoke",{"script":"smoke:otel","unsetEnv":["OTEL_SDK_DISABLED"]}]'
if (cd "$tmp/commands" && PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$tmp/plan.log" \
    NPM_STUB_ENV_LOG="$tmp/plan-env.log" OTEL_SDK_DISABLED=true \
    CI_SCRIPT_PLAN="$plan" bash "$tmp/plan.sh") \
    && [ "$(wc -l < "$tmp/plan.log")" -eq 7 ] \
    && [ "$(head -1 "$tmp/plan.log")" = 'run verify:worker-schema' ] \
    && [ "$(tail -1 "$tmp/plan.log")" = 'run smoke:otel' ] \
    && grep -qFx 'smoke:otel=unset' "$tmp/plan-env.log"; then
  pass "the exact consumer npm script plan runs in order with a bounded per-script environment clear"
else
  fail "the exact consumer npm script plan did not run in order"
fi

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
