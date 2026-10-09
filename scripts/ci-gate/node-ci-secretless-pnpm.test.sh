#!/usr/bin/env bash
set -uo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/node-ci.yml"
failures=0
pass() { printf 'ok - %s\n' "$1"; }
fail() { printf 'not ok - %s\n' "$1" >&2; failures=$((failures + 1)); }
validator="$(mktemp)"
trap 'rm -f "$validator"; rm -rf "$fixture"' EXIT
awk '/- name: Validate approved internal dependency lock/ { found = 1; next }
  found && /python3 - <<'"'"'PY'"'"'/ { copying = 1; next }
  copying && /^          PY$/ { exit }
  copying { sub(/^          /, ""); print }' "$workflow" > "$validator"
fixture="$(mktemp -d)"
integrity="sha512-$(printf 'private pnpm package\n' | openssl dgst -sha512 -binary | base64 -w0)"
pin="$(printf '0%.0s' {1..128})"
write_fixture() {
  local dir="$1" package="$2" tarball="$3" locked_integrity="$4"
  mkdir -p "$dir"
  printf '{"name":"fixture","packageManager":"pnpm@11.20.0+sha512.%s"}\n' "$pin" > "$dir/package.json"
  printf "lockfileVersion: '9.0'\npackages:\n  '%s@1.2.3':\n    resolution:\n      integrity: %s\n      tarball: %s\n" \
    "$package" "$locked_integrity" "$tarball" > "$dir/pnpm-lock.yaml"
}
run_validator() {
  local dir="$1" approved="$2" entries="$1/private-entries"
  rm -f "$entries"
  (cd "$dir" && PACKAGE_MANAGER=pnpm APPROVED_INTERNAL_PACKAGES="$approved" \
    APPROVED_INTERNAL_SCOPES='@verjson' PRIVATE_CACHE_ENTRIES="$entries" \
    TRUSTED_PACKAGE_POLICY='' python3 "$validator")
}
valid="$fixture/valid"
write_fixture "$valid" '@verjson/contracts' 'https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/abc' "$integrity"
sed -i "s|'@verjson/contracts@1.2.3':|'@verjson/contracts@1.2.3(@scope/peer@2.0.0)':|" "$valid/pnpm-lock.yaml"
cat >> "$valid/pnpm-lock.yaml" <<'EOF'
  'react-dom@19.0.0(react@19.0.0)':
    resolution: {integrity: sha512-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==}
snapshots:
  '@verjson/contracts@1.2.3(@scope/peer@2.0.0)': {}
  'react-dom@19.0.0(react@19.0.0)': {}
EOF
if run_validator "$valid" '@verjson/contracts' && grep -qF 'https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/abc' "$valid/private-entries"; then
  pass "pnpm lock admits one exact allowlisted private package"
else fail "pnpm lock rejected an exact allowlisted private package"; fi
missing="$fixture/missing-integrity"
write_fixture "$missing" '@verjson/contracts' 'https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/abc' ''
if run_validator "$missing" '@verjson/contracts' >/dev/null 2>&1; then fail "pnpm lock admitted missing integrity"; else pass "pnpm lock rejects missing private-package integrity"; fi
unapproved="$fixture/unapproved"
write_fixture "$unapproved" '@verjson/other' 'https://npm.pkg.github.com/download/@verjson/other/1.2.3/abc' "$integrity"
if run_validator "$unapproved" '@verjson/contracts' >/dev/null 2>&1; then fail "pnpm lock admitted an unapproved package"; else pass "pnpm lock rejects an unapproved private package"; fi
sed -i 's/pnpm@11.20.0+sha512\.[0-9a-f]*/pnpm@11.20.0/' "$valid/package.json"
if run_validator "$valid" '@verjson/contracts' >/dev/null 2>&1; then fail "pnpm admitted an unpinned Corepack version"; else pass "pnpm rejects package-manager pin drift"; fi
duplicate="$fixture/duplicate"
write_fixture "$duplicate" '@verjson/contracts' 'https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/abc' "$integrity"
printf "  '@verjson/contracts@1.2.3':\n    resolution: {integrity: %s, tarball: https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/abc}\n" "$integrity" >> "$duplicate/pnpm-lock.yaml"
if run_validator "$duplicate" '@verjson/contracts' >/dev/null 2>&1; then fail "pnpm lock admitted duplicate YAML keys"; else pass "pnpm lock rejects duplicate-key ambiguity"; fi
alias="$fixture/alias"
write_fixture "$alias" '@verjson/contracts' 'https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/abc' "$integrity"
sed -i "s|'@verjson/contracts@1.2.3':|'@verjson/contracts@npm:@verjson/other@1.2.3':|" "$alias/pnpm-lock.yaml"
if run_validator "$alias" '@verjson/contracts' >/dev/null 2>&1; then fail "pnpm lock admitted an alias as exact package identity"; else pass "pnpm lock rejects package aliases before authorization"; fi
malformed="$fixture/malformed-peer"
write_fixture "$malformed" '@verjson/contracts' 'https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/abc' "$integrity"
sed -i "s|'@verjson/contracts@1.2.3':|'@verjson/contracts@1.2.3(peer@2.0.0':|" "$malformed/pnpm-lock.yaml"
if run_validator "$malformed" '@verjson/contracts' >/dev/null 2>&1; then fail "pnpm lock admitted an unbalanced peer context"; else pass "pnpm lock rejects malformed peer-context near misses"; fi

rebuild="$fixture/rebuild.sh"
python3 - "$workflow" "$rebuild" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
step = next(step for step in doc["jobs"]["build-test"]["steps"]
            if step.get("name") == "Rebuild exact approved lifecycle packages without credentials")
open(sys.argv[2], "w", encoding="utf-8").write(step["run"])
PY
test_root="$fixture/rebuild"
rebuild_fixture="$test_root/workspace"
mkdir -p "$rebuild_fixture" "$test_root/bin"
tool_cache="$fixture/tool-cache"
tool_bin="$tool_cache/node/test/x64/bin"
mkdir -p "$tool_bin" "$test_root/runner-temp" "$test_root/home/.cache/node/corepack"
cat > "$rebuild_fixture/pnpm-lock.yaml" <<'EOF'
lockfileVersion: '9.0'
packages:
  '@verjson/native@1.2.3(peer-lib@2.0.0)': {resolution: {integrity: sha512-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==}}
snapshots:
  'native-helper@3.4.5(@scope/peer@6.7.8)': {requiresBuild: true}
  '@verjson/native@1.2.3(peer-lib@2.0.0)': {requiresBuild: true}
  'leftpad@1.0.0': {}
  '@rollup/rollup-linux-x64-gnu@4.0.0': {requiresBuild: true}
EOF
cat > "$tool_bin/corepack" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "@LOG_FILE@"
EOF
sed -i "s|@LOG_FILE@|$rebuild_fixture/corepack.log|" "$tool_bin/corepack"
chmod +x "$tool_bin/corepack"
cat > "$tool_bin/node" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$tool_bin/node"
cat > "$test_root/bin/bwrap" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
while (($#)); do
  if [ "$1" = -- ]; then
    shift
    break
  fi
  shift
done
[ "$#" -gt 0 ] || { echo 'test bubblewrap found no command separator' >&2; exit 1; }
case "$1" in
  /usr/bin/python3) ;;
  *) echo "test bubblewrap received unexpected bootstrap: $1" >&2; exit 1 ;;
esac
shift
[ "${1-}" = "-c" ] || { echo 'test bubblewrap found no trusted bootstrap source' >&2; exit 1; }
shift
bootstrap_source="${1-}"
[[ "$bootstrap_source" == *"os.closerange(3, max_fd)"* ]] || {
  echo 'test bubblewrap found no inherited-descriptor closure' >&2
  exit 1
}
[[ "$bootstrap_source" == *"os.execvpe(sys.argv[1], sys.argv[1:], os.environ)"* ]] || {
  echo 'test bubblewrap found no trusted lifecycle handoff' >&2
  exit 1
}
shift
case "$1" in
  /opt/verjson-node-toolchain/bin/corepack|corepack) ;;
  *) echo "test bubblewrap received unexpected lifecycle command: $1" >&2; exit 1 ;;
esac
shift
cd "@WORKSPACE@"
exec "@COREPACK@" "$@"
EOF
sed -i "s|@WORKSPACE@|$rebuild_fixture|; s|@COREPACK@|$tool_bin/corepack|" "$test_root/bin/bwrap"
chmod 755 "$test_root/bin/bwrap"
run_rebuild() {
  export HOME="$test_root/home"
  export RUNNER_TEMP="$test_root/runner-temp"
  export RUNNER_TOOL_CACHE="$tool_cache"
  export COREPACK_HOME="$test_root/home/.cache/node/corepack"
  export BWRAP_BINARY="$test_root/bin/bwrap"
  export PATH="$tool_bin:$PATH"
  bash "$rebuild"
}
mkdir -p "$rebuild_fixture/node_modules/@verjson/native" "$rebuild_fixture/node_modules/native-helper"
if (cd "$rebuild_fixture" && PATH="$rebuild_fixture/bin:$PATH" PACKAGE_MANAGER=pnpm RUNNER_ENVIRONMENT=github-hosted \
    REBUILD_PACKAGES=$'@verjson/native\nnative-helper' COREPACK_LOG="$rebuild_fixture/corepack.log" \
    run_rebuild) \
    && grep -qFx 'pnpm rebuild @verjson/native native-helper' "$rebuild_fixture/corepack.log"; then
  pass "pnpm rebuild accepts exact scoped and unscoped peer-context package identities from packages and snapshots"
else
  fail "pnpm rebuild misparsed valid package or snapshot identities"
fi

# #932: secretless-rebuild-packages must exactly match the lock's requiresBuild
# surface, not merely name packages present in the lock.
: > "$rebuild_fixture/corepack.log"
if (cd "$rebuild_fixture" && PATH="$rebuild_fixture/bin:$PATH" PACKAGE_MANAGER=pnpm RUNNER_ENVIRONMENT=github-hosted \
    REBUILD_PACKAGES='native-helper' COREPACK_LOG="$rebuild_fixture/corepack.log" run_rebuild) >/dev/null 2>&1 \
    || [ -s "$rebuild_fixture/corepack.log" ]; then
  fail "a lock-declared requiresBuild package absent from the allowlist reached pnpm rebuild (#932)"
else
  pass "the lock's requiresBuild surface must be fully named in the allowlist (#932)"
fi
: > "$rebuild_fixture/corepack.log"
if (cd "$rebuild_fixture" && PATH="$rebuild_fixture/bin:$PATH" PACKAGE_MANAGER=pnpm RUNNER_ENVIRONMENT=github-hosted \
    REBUILD_PACKAGES=$'@verjson/native\nnative-helper\nleftpad' COREPACK_LOG="$rebuild_fixture/corepack.log" \
    run_rebuild) >/dev/null 2>&1 \
    || [ -s "$rebuild_fixture/corepack.log" ]; then
  fail "an allowlisted package the lock does not mark requiresBuild reached pnpm rebuild (#932)"
else
  pass "the allowlist may not name a package the lock does not mark requiresBuild (#932)"
fi

# #941: a requiresBuild package for a platform other than this runner's (never
# installed into node_modules/) must not be forced into the allowlist.
: > "$rebuild_fixture/corepack.log"
if (cd "$rebuild_fixture" && PATH="$rebuild_fixture/bin:$PATH" PACKAGE_MANAGER=pnpm RUNNER_ENVIRONMENT=github-hosted \
    REBUILD_PACKAGES=$'@verjson/native\nnative-helper' COREPACK_LOG="$rebuild_fixture/corepack.log" \
    run_rebuild) \
    && grep -qFx 'pnpm rebuild @verjson/native native-helper' "$rebuild_fixture/corepack.log"; then
  pass "a requiresBuild package absent from node_modules/ is not forced into the allowlist (#941)"
else
  fail "a cross-platform lock entry incorrectly demanded allowlisting (#941)"
fi

sed -i "s|'native-helper@3.4.5(@scope/peer@6.7.8)'|'native-helper@npm:@scope/other@3.4.5'|" "$rebuild_fixture/pnpm-lock.yaml"
: > "$rebuild_fixture/corepack.log"
if (cd "$rebuild_fixture" && PATH="$rebuild_fixture/bin:$PATH" PACKAGE_MANAGER=pnpm RUNNER_ENVIRONMENT=github-hosted \
    REBUILD_PACKAGES='native-helper' COREPACK_LOG="$rebuild_fixture/corepack.log" run_rebuild) >/dev/null 2>&1; then
  fail "pnpm rebuild admitted an aliased snapshot identity"
elif [ -s "$rebuild_fixture/corepack.log" ]; then
  fail "pnpm rebuild invoked package code before rejecting an alias"
else
  pass "pnpm rebuild rejects aliased snapshot identities before execution"
fi
python3 - "$workflow" "$valid/pnpm-lock.yaml" <<'PY' \
  && pass "registry-addressed private pnpm packages resolve locally in a credentialless, frozen, bounded, and cleaned install" \
  || fail "pnpm execution weakened the secretless handoff"
import ast, sys, textwrap, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
inputs = doc[True]["workflow_call"]["inputs"]
assert inputs["package-manager"]["default"] == "npm"
steps = doc["jobs"]["build-test"]["steps"]
install = next(step for step in steps if step.get("name") == "Install from verified secretless npm cache")
for credential in ("GH_TOKEN", "GITHUB_TOKEN", "NODE_AUTH_TOKEN", "NPM_TOKEN"):
    assert install["env"][credential] == ""
script = install["run"]
lock = open(sys.argv[2], encoding="utf-8").read()
assert "https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/abc" in lock
assert 'corepack pnpm store add' not in script
assert '.resolve().as_uri()' not in script
assert 'parsed = urlparse(tarball.value)' in script
assert 'if parsed.hostname != "npm.pkg.github.com":' in script
assert 'lock = yaml.compose(lock_text, Loader=yaml.SafeLoader)' in script
assert '(tarball.start_mark.index, tarball.end_mark.index, json.dumps(local_url))' in script
assert 'lock_text = lock_text[:start] + replacement + lock_text[end:]' in script
assert 'yaml.safe_dump(lock' not in script
assert 'local_url = f"http://127.0.0.1:{port}/tarball/{digest}.tgz"' in script
assert 'httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)' in script
assert 'npmrc_lines = [f"{scope}:registry=http://127.0.0.1:{port}/\\n" for scope in sorted(scopes)]' in script
assert 'PNPM_CONFIG_IGNORE_PNPMFILE=true NPM_CONFIG_USERCONFIG="$MOCK_REGISTRY_NPMRC" corepack pnpm install --frozen-lockfile --ignore-scripts --prefer-offline' in script
assert 'cp -- "$PNPM_STORE_DIR/pnpm-lock.original.yaml" pnpm-lock.yaml' in script
assert 'kill "$MOCK_REGISTRY_PID" 2>/dev/null || true' in script
assert 'rm -rf "$SECRETLESS_CACHE_DIR" "$PNPM_STORE_DIR" "$TRANSFER_DIR"' in script
boundary = next(step for step in doc["jobs"]["acquire-secretless-dependencies"]["steps"] if step.get("name") == "Enforce the secretless event boundary")
assert "same-repository pull request" in boundary["run"]
validator = next(step for step in doc["jobs"]["acquire-secretless-dependencies"]["steps"]
                 if step.get("name") == "Validate approved internal dependency lock")["run"]
rebuild = next(step for step in steps
               if step.get("name") == "Rebuild exact approved lifecycle packages without credentials")["run"]
def parser_ast(script):
    source = script.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    tree = ast.parse(textwrap.dedent(source))
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == "pnpm_package_name")
    return ast.dump(function, include_attributes=False)
assert parser_ast(validator) == parser_ast(rebuild)
PY
exit "$failures"
