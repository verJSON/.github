#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
workflow="$repo_root/.github/workflows/node-release.yml"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

awk '
  /LOCAL_PACKAGE_PATH_BEGIN/ { active = 1; next }
  /LOCAL_PACKAGE_PATH_END/ { exit }
  active { sub(/^          /, ""); print }
' "$workflow" >"$work/local-path.sh"
pack_line="$(sed -n '/^            npm pack "\$package_path"/ { s/^            //; p; }' "$workflow")"
[ -n "$pack_line" ] || { echo "FAIL - reusable local npm pack command not found" >&2; exit 1; }
bash -n "$work/local-path.sh"

awk '
  /NPM_PACK_JSON_BEGIN/ { active = 1; next }
  /NPM_PACK_JSON_END/ { exit }
  active { sub(/^          /, ""); print }
' "$workflow" >"$work/parse-pack.js"

mkdir -p "$work/repo/compat"
cat >"$work/repo/package.json" <<'JSON'
{"name":"@fixture/root","version":"1.2.3"}
JSON
cat >"$work/repo/compat/package.json" <<'JSON'
{"name":"@fixture/local-compat","version":"9.8.7"}
JSON

pack_and_assert() {
  local package_dir="$1" expected_name="$2" expected_version="$3"
  local pack_json="$work/$package_dir-pack.json"
  local archive_dir="$work/archive" manifest="$work/package-artifacts.json"
  mkdir -p "$archive_dir"
  [ -f "$manifest" ] || printf '[]\n' >"$manifest"
  (
    cd "$work/repo"
    export GITHUB_WORKSPACE="$work/repo" package_dir archive_dir pack_json
    export PACKAGE_VERSION="$expected_version" SCOPE='@fixture' manifest
    source "$work/local-path.sh"
    export npm_config_registry=http://127.0.0.1:9
    export npm_config_fetch_retries=0
    eval "$pack_line"
  )
  node "$work/parse-pack.js" "$pack_json" "$expected_version" "@fixture" "$archive_dir" "$manifest"
  node - "$manifest" "$expected_name" "$expected_version" "$archive_dir" <<'NODE'
const fs = require("fs");
const path = require("path");
const [manifestPath, expectedName, expectedVersion, archiveDir] = process.argv.slice(2);
const entries = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const item = entries.find((entry) => entry.name === expectedName);
if (!item || item.version !== expectedVersion) {
  throw new Error(`packed ${item?.name}@${item?.version}, expected ${expectedName}@${expectedVersion}`);
}
if (!/^sha512-[A-Za-z0-9+/]{86}==$/.test(item.integrity)) throw new Error("missing sha512 integrity");
const archive = path.join(archiveDir, item.filename);
if (!fs.statSync(archive).isFile()) throw new Error(`missing local archive ${archive}`);
NODE
}

python3 - "$work" <<'PY'
import base64
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
integrity = "sha512-" + base64.b64encode(bytes(64)).decode("ascii")
item = {
    "name": "@fixture/pkg",
    "version": "1.2.3",
    "integrity": integrity,
    "filename": "fixture.tgz",
}
fixtures = {
    "npm11": [item],
    "npm12": {"@fixture/pkg": item},
    "mismatched-key": {"@fixture/spoof": item},
    "wrong-version": {"@fixture/pkg": {**item, "version": "9.9.9"}},
    "multiple-keys": {
        "@fixture/pkg": item,
        "@fixture/other": {**item, "name": "@fixture/other", "filename": "other.tgz"},
    },
}
for name, value in fixtures.items():
    (root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    archive = root / f"{name}-archive"
    archive.mkdir()
    (archive / "fixture.tgz").write_bytes(b"fixture")
    (root / f"{name}-manifest.json").write_text("[]\n", encoding="utf-8")
PY

for fixture in npm11 npm12; do
  node "$work/parse-pack.js" "$work/$fixture.json" 1.2.3 @fixture \
    "$work/$fixture-archive" "$work/$fixture-manifest.json"
  node - "$work/$fixture-manifest.json" <<'NODE'
const fs = require("fs");
const entries = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
if (entries.length !== 1 || entries[0].name !== "@fixture/pkg") {
  throw new Error("npm pack format did not yield exactly the named package");
}
NODE
done

if node "$work/parse-pack.js" "$work/mismatched-key.json" 1.2.3 @fixture \
    "$work/mismatched-key-archive" "$work/mismatched-key-manifest.json" >/dev/null 2>&1; then
  echo "FAIL - npm 12 object key did not have to match package metadata" >&2
  exit 1
fi
cp "$work/wrong-version-manifest.json" "$work/wrong-version-before.json"
if node "$work/parse-pack.js" "$work/wrong-version.json" 1.2.3 @fixture \
    "$work/wrong-version-archive" "$work/wrong-version-manifest.json" >/dev/null 2>&1; then
  echo "FAIL - npm pack version mismatch was accepted" >&2
  exit 1
elif ! cmp -s "$work/wrong-version-manifest.json" "$work/wrong-version-before.json"; then
  echo "FAIL - npm pack version mismatch changed the artifact manifest" >&2
  exit 1
fi
echo "ok - npm pack version mismatch is rejected before manifest mutation"
if node "$work/parse-pack.js" "$work/multiple-keys.json" 1.2.3 @fixture \
    "$work/multiple-keys-archive" "$work/multiple-keys-manifest.json" >/dev/null 2>&1; then
  echo "FAIL - npm 12 object accepted more than one packed artifact" >&2
  exit 1
fi

pack_and_assert . @fixture/root 1.2.3
pack_and_assert compat @fixture/local-compat 9.8.7
echo "ok - reusable npm pack command resolves root and secondary packages locally with the registry unreachable"
