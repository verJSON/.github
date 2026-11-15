#!/usr/bin/env bash
# Behavioral contract for pinned gitlink file: dependencies (#2087).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
helper="$root/scripts/container_gitlink_file_dependencies.sh"
publish="$root/.github/workflows/container-candidate-publish.yml"
generator="$root/scripts/gen-container-candidate.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1" >&2; fails=$((fails + 1)); }

git_identity() {
  git -C "$1" config user.email fixture@example.invalid
  git -C "$1" config user.name fixture
}

expect_fail() {
  local name="$1"
  shift
  local err="$tmp/err"
  if "$@" >"$tmp/out" 2>"$err"; then
    fail "$name succeeded"
    return
  fi
  if ! grep -q 'container gitlink file dependency rejected:' "$err"; then
    fail "$name did not report a gitlink rejection"
    sed 's/^/diag - /' "$err" >&2
    return
  fi
  pass "$name"
}

schema="$tmp/schema"
mkdir -p "$schema"
git -C "$schema" init -q
git_identity "$schema"
printf '%s\n' '{"name":"@verjson/xops-schema","version":"1.0.0","scripts":{"prepare":"node -e \"process.stdout.write(process.env.NODE_AUTH_TOKEN||\\\"\\\")\""}}' > "$schema/package.json"
printf '%s\n' '{"name":"@verjson/xops-schema","lockfileVersion":3,"packages":{"":{"name":"@verjson/xops-schema"}}}' > "$schema/package-lock.json"
git -C "$schema" add package.json package-lock.json
git -C "$schema" commit -qm schema
schema_sha="$(git -C "$schema" rev-parse HEAD)"

parent="$tmp/parent"
mkdir -p "$parent"
git -C "$parent" init -q
git_identity "$parent"
printf '%s\n' '[submodule "xops-schema"]' '	path = xops-schema' '	url = https://github.com/Verjson/xops-schema.git' > "$parent/.gitmodules"
printf '%s\n' '{"dependencies":{"@verjson/xops-schema":"file:xops-schema"}}' > "$parent/package.json"
python3 - "$parent/package-lock.json" <<'PY'
import json, sys
json.dump({
    "lockfileVersion": 3,
    "packages": {
        "": {"name": "consumer"},
        "node_modules/@verjson/xops-schema": {"resolved": "xops-schema", "link": True},
        "xops-schema": {"name": "@verjson/xops-schema", "version": "1.0.0"},
    },
}, open(sys.argv[1], "w"))
PY
git -C "$parent" add .gitmodules package.json package-lock.json
git -C "$parent" update-index --add --cacheinfo "160000,${schema_sha},xops-schema"
git -C "$parent" commit -qm parent
git -C "$parent" clone -q "$schema" xops-schema

proof="$tmp/proof.json"
(cd "$parent" && bash "$helper" admit package-lock.json Verjson "$proof")
python3 - "$proof" "$schema_sha" <<'PY'
import json, sys
proof, expected = json.load(open(sys.argv[1])), sys.argv[2]
assert proof == {"xops-schema": expected}, proof
PY
pass "admit records the pinned gitlink"

(cd "$parent" && bash "$helper" confirm "$proof")
pass "confirm accepts the checked-out pin"

printf '%s\n' 'marker' >> "$parent/xops-schema/package.json"
git_identity "$parent/xops-schema"
git -C "$parent/xops-schema" add package.json
git -C "$parent/xops-schema" commit -qm moved
expect_fail "confirm rejects a moved submodule checkout" \
  bash -c 'cd "$1" && bash "$2" confirm "$3"' _ "$parent" "$helper" "$proof"
git -C "$parent/xops-schema" reset -q --hard "$schema_sha"

python3 - "$parent/.gitmodules" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
path.write_text(path.read_text().replace(
    "https://github.com/Verjson/xops-schema.git",
    "https://example.invalid/evil.git",
))
PY
expect_fail "admit rejects a submodule URL outside the owner" \
  bash -c 'cd "$1" && bash "$2" admit package-lock.json Verjson "$3"' _ "$parent" "$helper" "$tmp/bad-proof.json"
git -C "$parent" checkout -q -- .gitmodules

empty_parent="$tmp/empty"
mkdir -p "$empty_parent"
git -C "$empty_parent" init -q
git_identity "$empty_parent"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{"name":"consumer"}}}' > "$empty_parent/package-lock.json"
git -C "$empty_parent" add package-lock.json
git -C "$empty_parent" commit -qm empty
(cd "$empty_parent" && bash "$helper" admit package-lock.json Verjson "$tmp/empty-proof.json")
python3 - "$tmp/empty-proof.json" <<'PY'
import json, sys
assert json.load(open(sys.argv[1])) == {}
PY
(cd "$empty_parent" && bash "$helper" confirm "$tmp/empty-proof.json")
(cd "$empty_parent" && bash "$helper" require-npm "$tmp/empty-proof.json")
pass "an empty proof does not fetch, confirm, or demand npm 11"

npm10="$tmp/npm10"
mkdir -p "$npm10"
cat > "$npm10/npm" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' 10.9.9
EOF
chmod +x "$npm10/npm"
expect_fail "npm 10 cannot install a pinned gitlink" \
  env PATH="$npm10:$PATH" bash "$helper" require-npm "$proof"
stub_log="$tmp/stub.log"
: > "$stub_log"
npm11="$tmp/npm11"
mkdir -p "$npm11"
cat > "$npm11/npm" <<EOF
#!/usr/bin/env bash
if [ "\${1-}" = "--version" ]; then
  printf '%s\n' 11.6.2
  exit 0
fi
printf 'ARGS %s\n' "\$*" >> "$stub_log"
if [ -n "\${NODE_AUTH_TOKEN-}" ] || [ -n "\${GITHUB_TOKEN-}" ] || [ -n "\${NPM_TOKEN-}" ]; then
  printf 'TOKEN\n' >> "$stub_log"
fi
mkdir -p dist
printf '%s\n' built > dist/index.js
EOF
chmod +x "$npm11/npm"
env PATH="$npm11:$PATH" bash "$helper" require-npm "$proof"
pass "npm 11 may install a pinned gitlink"

(cd "$parent" && env NODE_AUTH_TOKEN=secret GITHUB_TOKEN=gh NPM_TOKEN=npm \
  PATH="$npm11:$PATH" bash "$helper" build "$proof")
grep -qx 'ARGS ci --no-audit --no-fund' "$stub_log" || { fail "build did not run the fixed npm ci command"; cat "$stub_log" >&2; }
if grep -q TOKEN "$stub_log"; then
  fail "build exposed a credential to npm"
else
  pass "build runs npm ci with no package or GitHub token"
fi
[ "$(cat "$parent/xops-schema/dist/index.js")" = built ] || fail "build did not produce the package output"

mkdir -p "$parent/xops-schema/node_modules" "$parent/xops-schema/.git"
printf '%s\n' secret > "$parent/xops-schema/node_modules/leak.txt"
printf '%s\n' gitdir > "$parent/xops-schema/.git/config"
mkdir -p "$parent/node_modules/@verjson"
ln -s ../../xops-schema "$parent/node_modules/@verjson/xops-schema"
mkdir -p "$parent/node_modules/.bin"
ln -s ../placeholder "$parent/node_modules/.bin/tool"
(cd "$parent" && bash "$helper" materialize "$proof" node_modules)
if [ -L "$parent/node_modules/@verjson/xops-schema" ]; then
  fail "materialize left a symlink"
else
  pass "materialize replaces the file: symlink with the built package"
fi
[ "$(cat "$parent/node_modules/@verjson/xops-schema/dist/index.js")" = built ] \
  || fail "materialized package is missing dist"
if [ -e "$parent/node_modules/@verjson/xops-schema/node_modules/leak.txt" ] \
  || [ -e "$parent/node_modules/@verjson/xops-schema/.git/config" ]; then
  fail "materialize copied install state or git metadata"
else
  pass "materialize omits node_modules and .git"
fi
[ -L "$parent/node_modules/.bin/tool" ] || fail "materialize removed an unrelated symlink"
ln -s /tmp/outside "$parent/xops-schema/escape"
expect_fail "materialize rejects a symlink inside the gitlink package" \
  bash -c 'cd "$1" && rm -rf node_modules/@verjson/xops-schema && ln -s ../../xops-schema node_modules/@verjson/xops-schema && bash "$2" materialize "$3" node_modules' \
  _ "$parent" "$helper" "$proof"

python3 - "$publish" "$helper" "$generator" <<'PY'
import hashlib
import pathlib
import sys

publish = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
helper = pathlib.Path(sys.argv[2]).read_bytes()
generator = pathlib.Path(sys.argv[3]).read_text(encoding="utf-8")
digest = hashlib.sha256(helper).hexdigest()
if publish.count(digest) < 1:
    raise SystemExit("publisher does not pin the gitlink helper digest")
if "submodules-token:" not in publish or "secrets.submodules-token || github.token" not in publish:
    raise SystemExit("publisher does not fetch submodules with the scoped token")
if "persist-credentials: false" not in publish.split("acquire-private-node-dependencies:", 1)[1].split("publish-base:", 1)[0]:
    raise SystemExit("acquisition checkout persists credentials")
if "submodules: 'true'" not in publish and "submodules: true" not in publish:
    raise SystemExit("acquisition does not check out submodules")
if "bash \"$helper\" build" not in publish and "container_gitlink_file_dependencies.sh\" build" not in publish:
    raise SystemExit("publisher does not build pinned gitlinks without credentials")
seal = publish.split("name: Build pinned gitlink packages without credentials", 1)
if len(seal) != 2 or "NODE_AUTH_TOKEN: ''" not in seal[1].split("name:", 1)[0]:
    raise SystemExit("gitlink build step does not clear NODE_AUTH_TOKEN")
if "submodules-token: \\${{ secrets.SUBMODULES_TOKEN }}" not in generator:
    raise SystemExit("generator does not route SUBMODULES_TOKEN to publication")
print("ok   - publisher pins the helper, the token, and the credential-free build")
PY

if [ "$fails" -ne 0 ]; then
  printf '%s failure(s)\n' "$fails" >&2
  exit 1
fi
echo "container gitlink file dependencies passed"
