#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/node-ci.yml"
fixture="$(mktemp -d)"
failures=0
trap 'rm -rf "$fixture"' EXIT

pass() { printf 'ok - %s\n' "$1"; }
fail() { printf 'not ok - %s\n' "$1" >&2; failures=$((failures + 1)); }

rewrite="$fixture/rewrite.py"
server="$fixture/mock-registry-server.py"
cleanup="$fixture/cleanup.sh"
python3 - "$workflow" "$rewrite" "$server" "$cleanup" <<'PY'
import sys

document_source = sys.argv[1]
import yaml

document = yaml.safe_load(open(document_source, encoding="utf-8"))
install = next(
    step
    for step in document["jobs"]["build-test"]["steps"]
    if step.get("name") == "Install from verified secretless npm cache"
)["run"]

rewrite_marker = 'MOCK_REGISTRY_NPMRC="$MOCK_REGISTRY_NPMRC" python3 - <<\'PY\'\n'
rewrite_source = install.split(rewrite_marker, 1)[1].split("\nPY\n", 1)[0]
open(sys.argv[2], "w", encoding="utf-8").write(rewrite_source + "\n")

server_marker = 'cat > "$MOCK_REGISTRY_SERVER" <<\'PY\'\n'
server_source = install.split(server_marker, 1)[1].split("\nPY\n", 1)[0]
open(sys.argv[3], "w", encoding="utf-8").write(server_source + "\n")

cleanup_body = install.split("cleanup() {", 1)[1].split("trap cleanup EXIT", 1)[0]
open(sys.argv[4], "w", encoding="utf-8").write(
    "set -euo pipefail\ncleanup() {" + cleanup_body + "trap cleanup EXIT\nfalse\n"
)
PY

package_dir="$fixture/package"
app_dir="$fixture/app"
mock_dir="$fixture/mock-registry"
mkdir -p "$package_dir" "$app_dir" "$mock_dir/tarball"
printf '%s\n' '{"name":"@verjson/contracts","version":"1.2.3","files":["index.js"]}' > "$package_dir/package.json"
printf '%s\n' 'module.exports = "fixture";' > "$package_dir/index.js"
(cd "$package_dir" && npm pack --silent --pack-destination "$fixture" >/dev/null)
tarball="$fixture/verjson-contracts-1.2.3.tgz"
digest="$(sha512sum "$tarball" | cut -d' ' -f1)"
integrity="sha512-$(openssl dgst -sha512 -binary "$tarball" | openssl base64 -A)"

# A realistic consumer pins the private dependency to a semver range, so the
# lockfile key stays registry-shaped (`name@version`) rather than a `file:`
# or other non-semver dependency path. Fixtures that instead resolve from a
# local `file:` spec sidestep pnpm's own "registry-style dependency path
# backed by a non-registry resolution" policy entirely (#896) and cannot
# reproduce the failure this rewrite exists to avoid.
printf '%s\n' '{"name":"secretless-fixture","version":"1.0.0","packageManager":"pnpm@11.22.0","dependencies":{"@verjson/contracts":"^1.2.3"}}' > "$app_dir/package.json"
cat > "$app_dir/pnpm-lock.yaml" <<EOF
lockfileVersion: '9.0'
settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false
importers:
  .:
    dependencies:
      '@verjson/contracts':
        specifier: ^1.2.3
        version: 1.2.3
packages:
  '@verjson/contracts@1.2.3':
    resolution:
      integrity: $integrity
      tarball: https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/fixture
snapshots:
  '@verjson/contracts@1.2.3': {}
x-preservation:
  yes: no
  0123: 0123
  1:20: 1:20
  ~: ~
  2026-08-18: 2026-08-18
  1.20: 1.20
EOF

original="$fixture/original.yaml"
cp -- "$app_dir/pnpm-lock.yaml" "$original"
mapping="$fixture/import-map.tsv"
printf '%s\t%s\n' "$digest" "$tarball" > "$mapping"

run_rewrite() {
  (cd "$app_dir" && PNPM_IMPORT_MAP="$mapping" MOCK_REGISTRY_DIR="$mock_dir" \
    MOCK_REGISTRY_PORT="4873" MOCK_REGISTRY_NPMRC="$fixture/mock-registry.npmrc" \
    APPROVED_INTERNAL_SCOPES=$'@verjson' \
    python3 "$rewrite")
}

if run_rewrite \
    && grep -qF '"tarball": "http://127.0.0.1:4873/tarball/'"$digest"'.tgz"' "$mock_dir/@verjson/contracts" \
    && [ -f "$mock_dir/tarball/$digest.tgz" ] \
    && cmp -s "$mock_dir/tarball/$digest.tgz" "$tarball" \
    && grep -qFx '@verjson:registry=http://127.0.0.1:4873/' "$fixture/mock-registry.npmrc"; then
  pass "the rewrite publishes a self-consistent local packument, tarball, and npmrc scope override"
else
  fail "the rewrite did not produce a self-consistent local mock registry"
fi

if DIGEST="$digest" python3 - "$original" "$app_dir/pnpm-lock.yaml" <<'PY'
import json
import os
import sys
from pathlib import Path

before = Path(sys.argv[1]).read_text(encoding="utf-8")
after = Path(sys.argv[2]).read_text(encoding="utf-8")
registry = "https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/fixture"
replacement = json.dumps(f"http://127.0.0.1:4873/tarball/{os.environ['DIGEST']}.tgz")
assert before.count(registry) == 1
assert before.replace(registry, replacement, 1) == after
PY
then
  pass "the private tarball scalar is the rewrite's sole byte delta"
else
  fail "the rewrite changed bytes outside the private tarball scalar"
fi

cp -- "$original" "$app_dir/pnpm-lock.yaml"
rm -rf "$mock_dir"; mkdir -p "$mock_dir/tarball"

for unsafe_yaml in alias tag; do
  cp -- "$original" "$app_dir/pnpm-lock.yaml"
  if [ "$unsafe_yaml" = alias ]; then
    sed -i '1i x-anchor: &private https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/fixture' "$app_dir/pnpm-lock.yaml"
    sed -i '/tarball:/ s#https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/fixture#*private#' "$app_dir/pnpm-lock.yaml"
  else
    sed -i 's#https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/fixture#!private https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/fixture#' "$app_dir/pnpm-lock.yaml"
  fi
  cp -- "$app_dir/pnpm-lock.yaml" "$fixture/$unsafe_yaml.original"
  if run_rewrite >"$fixture/$unsafe_yaml.out" 2>&1; then
    fail "rewrite accepted a YAML $unsafe_yaml"
  elif grep -qF 'rejects YAML aliases, anchors, and tags' "$fixture/$unsafe_yaml.out" \
      && cmp -s "$fixture/$unsafe_yaml.original" "$app_dir/pnpm-lock.yaml"; then
    pass "YAML $unsafe_yaml is rejected without modifying the lockfile"
  else
    fail "YAML $unsafe_yaml rejection was not fail-closed"
  fi
done

cp -- "$original" "$app_dir/pnpm-lock.yaml"
: > "$mapping"
if run_rewrite >"$fixture/missing.out" 2>&1; then
  fail "rewrite accepted a missing verified transfer mapping"
elif cmp -s "$original" "$app_dir/pnpm-lock.yaml"; then
  pass "missing mapping fails without modifying the lockfile"
else
  fail "missing mapping failure modified the lockfile"
fi

cp -- "$original" "$app_dir/pnpm-lock.yaml"
surplus="$(printf '0%.0s' {1..128})"
printf '%s\t%s\n%s\t%s\n' "$digest" "$tarball" "$surplus" "$tarball" > "$mapping"
if run_rewrite >"$fixture/surplus.out" 2>&1; then
  fail "rewrite accepted a surplus transfer mapping"
elif grep -qF "$surplus" "$fixture/surplus.out" && cmp -s "$original" "$app_dir/pnpm-lock.yaml"; then
  pass "surplus mapping reports its safe digest and leaves the lockfile unchanged"
else
  fail "surplus mapping did not report its digest or modified the lockfile"
fi

# End-to-end: a real mock registry server plus a real frozen, credentialless
# pnpm install, proving the rewritten lock never contacts npm.pkg.github.com
# and never trips pnpm's own registry-shape supply-chain policy.
cp -- "$original" "$app_dir/pnpm-lock.yaml"
printf '%s\t%s\n' "$digest" "$tarball" > "$mapping"
cat > "$app_dir/.pnpmfile.cjs" <<'JS'
const fs = require('node:fs')
fs.appendFileSync(process.env.GITHUB_ENV, 'PNPM_HOOK_RAN=1\\n')
module.exports = {}
JS
pnpm_hook_env="$fixture/pnpm-hook.env"
rm -rf "$mock_dir"; mkdir -p "$mock_dir/tarball"
port_file="$fixture/live.port"
(cd "$app_dir" && python3 "$server" "$mock_dir" "$port_file") &
server_pid=$!
for _ in $(seq 1 50); do
  [ -s "$port_file" ] && break
  sleep 0.1
done
port="$(cat "$port_file" 2>/dev/null || true)"
store="$fixture/store"
if [ -n "$port" ] \
    && (cd "$app_dir" && PNPM_IMPORT_MAP="$mapping" MOCK_REGISTRY_DIR="$mock_dir" \
        MOCK_REGISTRY_PORT="$port" MOCK_REGISTRY_NPMRC="$fixture/live.npmrc" \
        APPROVED_INTERNAL_SCOPES=$'@verjson' python3 "$rewrite") \
    && (cd "$app_dir" && GITHUB_ENV="$pnpm_hook_env" PNPM_CONFIG_IGNORE_PNPMFILE=true \
        NPM_CONFIG_USERCONFIG="$fixture/live.npmrc" corepack pnpm install \
        --frozen-lockfile --ignore-scripts --prefer-offline --store-dir "$store" >"$fixture/live-install.out" 2>&1) \
    && [ ! -s "$pnpm_hook_env" ] \
    && [ "$(cd "$app_dir" && node -p "require('./node_modules/@verjson/contracts/package.json').version" 2>/dev/null)" = "1.2.3" ]; then
  pass "a real frozen pnpm install completes against the local mock registry without contacting npm.pkg.github.com"
else
  fail "the real frozen pnpm install did not complete against the local mock registry"
  cat "$fixture/live-install.out" >&2 2>/dev/null || true
fi
kill "$server_pid" 2>/dev/null || true
wait "$server_pid" 2>/dev/null || true

cp -- "$original" "$app_dir/pnpm-lock.yaml"
rm -rf "$app_dir/node_modules"
printf '%s\t%s\n' "$digest" "$tarball" > "$mapping"
run_rewrite
cleanup_cache="$fixture/cleanup-cache"
cleanup_transfer="$fixture/cleanup-transfer"
mkdir -p "$cleanup_cache" "$cleanup_transfer" "$store"
cp -- "$original" "$store/pnpm-lock.original.yaml"
(cd "$app_dir" && \
  SECRETLESS_CACHE_DIR="$cleanup_cache" PNPM_STORE_DIR="$store" TRANSFER_DIR="$cleanup_transfer" \
  bash "$cleanup") >/dev/null 2>&1 || true
if cmp -s "$original" "$app_dir/pnpm-lock.yaml"; then
  pass "failure after rewriting restores the original lockfile"
else
  fail "failure after rewriting left a mutated lockfile"
fi

cp -- "$original" "$app_dir/pnpm-lock.yaml"
rm -rf "$mock_dir"; mkdir -p "$mock_dir/tarball"
printf '%s\t%s\n' "$digest" "$tarball" > "$mapping"
if (cd "$app_dir" && PNPM_IMPORT_MAP="$mapping" MOCK_REGISTRY_DIR="$mock_dir" \
    MOCK_REGISTRY_PORT="4873" MOCK_REGISTRY_NPMRC="$fixture/unapproved.npmrc" \
    APPROVED_INTERNAL_SCOPES=$'@other-scope' \
    python3 "$rewrite") >"$fixture/unapproved.out" 2>&1; then
  fail "rewrite mapped a scope absent from APPROVED_INTERNAL_SCOPES to the mock registry"
elif grep -qF 'is not under an approved internal scope' "$fixture/unapproved.out" \
    && cmp -s "$original" "$app_dir/pnpm-lock.yaml" \
    && [ ! -e "$fixture/unapproved.npmrc" ]; then
  pass "the rewrite refuses to reroute a scope outside APPROVED_INTERNAL_SCOPES and writes no partial npmrc override (#918)"
else
  fail "the unapproved-scope rejection was not fail-closed"
fi

# #917 guard: dist-tags.latest is picked from a locked-version dict without a
# real semver comparison, so a packument spanning a version with a mixed
# digit/non-digit prerelease segment (e.g. 1.0.0-beta.1) alongside a plain
# version must not raise a TypeError.
prerelease_dir="$fixture/package-prerelease"
mkdir -p "$prerelease_dir"
printf '%s\n' '{"name":"@verjson/contracts","version":"1.0.0-beta.1","files":["index.js"]}' > "$prerelease_dir/package.json"
printf '%s\n' 'module.exports = "fixture-prerelease";' > "$prerelease_dir/index.js"
(cd "$prerelease_dir" && npm pack --silent --pack-destination "$fixture" >/dev/null)
prerelease_tarball="$(ls "$fixture"/verjson-contracts-1.0.0-beta.1*.tgz)"
prerelease_digest="$(sha512sum "$prerelease_tarball" | cut -d' ' -f1)"
prerelease_integrity="sha512-$(openssl dgst -sha512 -binary "$prerelease_tarball" | openssl base64 -A)"

multi_dir="$fixture/multi-version"
multi_mock="$fixture/multi-version-mock"
mkdir -p "$multi_dir" "$multi_mock/tarball"
cat > "$multi_dir/pnpm-lock.yaml" <<EOF
lockfileVersion: '9.0'
packages:
  '@verjson/contracts@1.2.3':
    resolution:
      integrity: $integrity
      tarball: https://npm.pkg.github.com/download/@verjson/contracts/1.2.3/fixture
  '@verjson/contracts@1.0.0-beta.1':
    resolution:
      integrity: $prerelease_integrity
      tarball: https://npm.pkg.github.com/download/@verjson/contracts/1.0.0-beta.1/fixture
EOF
multi_mapping="$fixture/multi-version-import-map.tsv"
printf '%s\t%s\n%s\t%s\n' "$digest" "$tarball" "$prerelease_digest" "$prerelease_tarball" > "$multi_mapping"
multi_npmrc="$fixture/multi-version.npmrc"
if (cd "$multi_dir" && PNPM_IMPORT_MAP="$multi_mapping" MOCK_REGISTRY_DIR="$multi_mock" \
    MOCK_REGISTRY_PORT="4874" MOCK_REGISTRY_NPMRC="$multi_npmrc" \
    APPROVED_INTERNAL_SCOPES=$'@verjson' python3 "$rewrite") >"$fixture/multi-version.out" 2>&1 \
    && grep -qF '"1.2.3"' "$multi_mock/@verjson/contracts" \
    && grep -qF '"1.0.0-beta.1"' "$multi_mock/@verjson/contracts"; then
  pass "the rewrite selects dist-tags.latest across mixed digit/non-digit prerelease versions without a TypeError (#917)"
else
  fail "the rewrite raised or omitted a version while resolving dist-tags.latest across mixed prerelease versions"
  cat "$fixture/multi-version.out" >&2 2>/dev/null || true
fi

exit "$failures"
