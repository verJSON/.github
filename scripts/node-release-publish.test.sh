#!/usr/bin/env bash
# Contract tests for the publish-only Node reusable workflow (#455, ADR 0067).
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
workflow="$root/.github/workflows/node-release.yml"
fails=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

python3 - "$workflow" <<'PY' || fails=$((fails + 1))
import pathlib
import sys
import yaml

sys.path.insert(0, str(pathlib.Path(sys.argv[1]).resolve().parents[2] / "scripts"))
import node_release_artifact_manifest as artifact_manifest

raw = open(sys.argv[1], encoding="utf-8").read()
doc = yaml.safe_load(raw)
on = doc.get("on", doc.get(True))
assert set(on) == {"workflow_call"}, "node-release must not be triggerable by push or dispatch"
inputs = on["workflow_call"]["inputs"]
assert inputs["version"]["required"] is True, "version must be required"
assert inputs["prefix"]["default"] == "v"
assert inputs["scope"]["default"] == "@verjson"
assert "Required lowercase npm scope" in inputs["scope"]["description"]
assert "unprivileged preparation job" in inputs["runner"]["description"]
assert inputs["package-dirs"]["default"] == '["."]'
assert inputs["release-assets"]["default"] == "[]"
assert inputs["contract-ref"]["required"] is True
assert set(doc["jobs"]) == {"prepare", "release", "retention"}
prepare = doc["jobs"]["prepare"]
release = doc["jobs"]["release"]
retention = doc["jobs"]["retention"]
assert prepare["permissions"] == {"contents": "read", "packages": "read"}
assert prepare["outputs"]["package-artifact-id"] == "${{ steps.upload-packages.outputs.artifact-id }}"
assert "inputs.runner" in prepare["runs-on"]
assert release["needs"] == "prepare"
assert release["runs-on"] == "ubuntu-24.04", "publication must run on a fresh hosted runner"
assert release["permissions"] == {"actions": "read", "contents": "write", "packages": "write"}
assert retention["runs-on"] == "ubuntu-24.04", "retention must not reuse a preparation runner"
assert "NODE_AUTH_TOKEN" not in (doc.get("env") or {})
assert "NODE_AUTH_TOKEN" not in (prepare.get("env") or {})
assert "NODE_AUTH_TOKEN" not in (release.get("env") or {})
prepare_steps = prepare["steps"]
steps = release["steps"]
assert "semantic-release" not in raw

install = next(step for step in prepare_steps if step.get("name") == "Install dependencies")
assert (install.get("run") or "").strip() == "npm ci --ignore-scripts"
assert install.get("env") == {"NODE_AUTH_TOKEN": "${{ secrets.NODE_AUTH_TOKEN }}"}
lifecycle = next(step for step in prepare_steps if step.get("name") == "Run dependency lifecycle scripts without credentials")
assert (lifecycle.get("run") or "").strip() == "npm ci --prefer-offline"
assert "NODE_AUTH_TOKEN" not in (lifecycle.get("env") or {})
assert prepare_steps.index(install) < prepare_steps.index(lifecycle)
package_token_steps = [
    step for step in prepare_steps
    if (step.get("env") or {}).get("NODE_AUTH_TOKEN") == "${{ secrets.NODE_AUTH_TOKEN }}"
]
assert package_token_steps == [install]
assert not any("npm publish" in (step.get("run") or "") for step in prepare_steps)
assert not any("gh release create" in (step.get("run") or "") for step in prepare_steps)
assert not any("npm run" in (step.get("run") or "") for step in steps)
assert not any("npm ci" in (step.get("run") or "") for step in steps)
assert not any("npm pack" in (step.get("run") or "") for step in steps)
assert not any("npm version" in (step.get("run") or "") for step in steps)
assert not any("scripts/release-prepare-packages.sh" in (step.get("run") or "") for step in steps)

assert not any(
    'gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$VERSION"' in (step.get("run") or "")
    for step in prepare_steps
), "preparation must not depend on release-time GitHub credentials"
assert any(
    'gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$VERSION"' in (step.get("run") or "")
    for step in steps
), "the fresh publisher must verify the exact tag before publication"
assert any('git describe --tags --exact-match HEAD' in (step.get("run") or "") for step in steps)
assert any('test -f "CHANGELOG/$VERSION.md"' in (step.get("run") or "") for step in steps)

setup_node_index = next(i for i, step in enumerate(steps) if step.get("uses", "").startswith("actions/setup-node@"))
prepare_setup_node_index = next(
    i for i, step in enumerate(prepare_steps) if step.get("uses", "").startswith("actions/setup-node@")
)
cache_setup = next(step for step in prepare_steps if step.get("name") == "Configure the bounded npm cache")
cache_cleanup = next(step for step in prepare_steps if step.get("name") == "Bound npm cache upload")
assert cache_setup["if"] == "inputs.cache && hashFiles(inputs.cache-dependency-path) != ''"
assert prepare_steps.index(cache_setup) < prepare_setup_node_index, "npm cache path must be configured before setup-node"
assert 'cache_dir="$RUNNER_TEMP/verjson-npm-cache"' in cache_setup["run"]
assert "npm_config_cache=%s\\n" in cache_setup["run"]
assert '"$GITHUB_ENV"' in cache_setup["run"]
assert 'cache_dir="$RUNNER_TEMP/verjson-npm-cache"' in cache_cleanup["run"]
package_dirs = next(step for step in steps if "package-dirs must be a non-empty JSON array" in (step.get("run") or ""))
assert setup_node_index < steps.index(package_dirs), "Node-dependent validation must run after setup-node"
assert all("node -" not in (step.get("run") or "") for step in steps[:setup_node_index]),     "no JavaScript may run before setup-node on bootstrap-clean runners"
assert 'expectedPackages.push({name: packageJson.name, version: packageVersion})' in package_dirs["run"]
assert 'expected-manifest=${expectedManifest}' in package_dirs["run"]
download = next(step for step in steps if step.get("uses", "").startswith("actions/download-artifact@"))
assert download["uses"] == "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
assert download["with"]["artifact-ids"] == "${{ needs.prepare.outputs.package-artifact-id }}"
upload = next(step for step in prepare["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
assert upload["id"] == "upload-packages"
assert upload["with"]["name"].find("github.run_attempt") >= 0
assert upload["with"]["compression-level"] == 0
artifact_size = next(step for step in steps if step.get("name") == "Enforce prepared package artifact size before download")
assert artifact_size["env"]["ARTIFACT_ID"] == "${{ needs.prepare.outputs.package-artifact-id }}"
assert int(artifact_size["env"]["MAX_ARTIFACT_BYTES"]) == artifact_manifest.MAX_ARTIFACT_BYTES
assert 'actions/artifacts/$ARTIFACT_ID' in artifact_size["run"]
assert steps.index(artifact_size) < steps.index(download), "artifact size must be checked before download"
contract_checkout = next(step for step in steps if step.get("with", {}).get("repository") == "Verjson/.github")
assert contract_checkout["with"]["ref"] == "${{ inputs.contract-ref }}"
assert contract_checkout["with"]["persist-credentials"] is False
validate_artifacts = next(step for step in steps if "node_release_artifact_manifest.py" in (step.get("run") or ""))
assert steps.index(download) < steps.index(validate_artifacts)
publish = next(step for step in steps if "npm publish" in (step.get("run") or ""))
assert publish["working-directory"] == "${{ runner.temp }}"
assert publish["env"]["NODE_AUTH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
assert "--ignore-scripts" in publish["run"]
assert "--registry=https://npm.pkg.github.com" in publish["run"]
assert 'package_file="$ARTIFACT_DIR/$package_filename"' in publish["run"]
assert "npm pack" not in publish["run"]
assert 'npm view "$package_name@$published_version" --json' in publish["run"]
assert "published.dist.integrity !== expectedIntegrity" in publish["run"]

release = next(step for step in steps if "gh release create" in (step.get("run") or ""))
assert "--verify-tag" in release["run"]
assert 'CHANGELOG/$VERSION.md' in release["run"]
assert 'gh release upload "$VERSION" "$ASSET_ROOT/$name"' in release["run"]
assert "--clobber" not in release["run"]
assert 'asset.get("digest")' in release["run"]
assert 'sha256:$expected_digest' in release["run"]
assert '"assets" not in release' in release["run"]
assert 'not isinstance(release["assets"], list)' in release["run"]
assert 'not isinstance(asset, dict)' in release["run"]
assets = next(step for step in steps if "BOUNDED_RELEASE_ASSETS_BEGIN" in (step.get("run") or ""))
for guard in ("at most 16 paths", "symlink", "100 MiB", "250 MiB", 'git cat-file blob "HEAD:$asset"'):
    assert guard in assets["run"], "missing bounded release-asset guard: %s" % guard

stamp = next(step for step in prepare_steps if "npm version" in (step.get("run") or ""))
root_build = next(step for step in prepare_steps if (step.get("run") or "").strip() == "npm run build --if-present")
selected_build = next(step for step in prepare_steps if "Build every selected release package" in (step.get("name") or ""))
pack = next(step for step in prepare_steps if step.get("name") == "Pack selected release packages")
assert prepare_steps.index(stamp) < prepare_steps.index(root_build) < prepare_steps.index(selected_build) < prepare_steps.index(pack)
assert "PACKAGE_DIRS_JSON" in (stamp.get("env") or {})
assert "jq -r" in stamp["run"] and "PACKAGE_DIRS_JSON" in stamp["run"] and "package_dirs" in stamp["run"]
assert 'npm version "$PACKAGE_VERSION" --prefix "$package_path"' in stamp["run"]
assert "--allow-same-version" in stamp["run"]
assert 'scripts/release-prepare-packages.sh "$PACKAGE_VERSION"' in raw
assert 'npm --prefix "$package_dir" run build --if-present' in selected_build["run"]
assert 'npm pack "$package_path" --json --ignore-scripts --pack-destination "$archive_dir"' in pack["run"]
assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in raw
assert "retention-days: 1" in raw

outputs = on["workflow_call"]["outputs"]
assert outputs["new-release-published"]["value"] == "${{ jobs.release.outputs.new-release-published }}"
assert outputs["new-release-version"]["value"] == "${{ jobs.release.outputs.new-release-version }}"
assert retention["needs"] == "release"
assert not retention.get("continue-on-error", False), "cleanup authorization failures must fail the release workflow"
assert retention["permissions"] == {"contents": "read", "packages": "write"}
assert retention["if"] == "needs.release.outputs.new-release-published == 'true'"
cleanup = retention["steps"][-1]
assert "package_retention.py" in cleanup["run"] and "--apply" in cleanup["run"]
assert cleanup["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
print("ok - build and dependency lifecycle work runs without publication permissions")
print("ok - package artifacts are validated on a fresh hosted publisher before npm publish")
print("ok - npm publication, GitHub release, and retention retain their existing authorization and restart guards")

PY

# Execute the real version/tag guard. `git` is stubbed so both sides of the
# boundary are observed without contacting GitHub or publishing anything.
sandbox="$(mktemp -d)"
trap 'rm -rf -- "$sandbox"' EXIT
python3 - "$workflow" >"$sandbox/guard.sh" <<'PY'
import sys
import yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for step in doc["jobs"]["release"]["steps"]:
    if step.get("name") == "Require the contract's exact release tag":
        print(step["run"])
        break
else:
    raise SystemExit("guard step missing")
PY
mkdir "$sandbox/bin"
printf '%s\n' '#!/usr/bin/env bash' 'exit "${GH_STUB_STATUS:-0}"' >"$sandbox/bin/gh"
chmod +x "$sandbox/bin/gh"

run_guard() {
  : >"$sandbox/output"
  env VERSION="$1" PREFIX="${4-v}" SCOPE="${3-@verjson}" PACKAGE_DIRS='["."]' \
    GITHUB_REPOSITORY=Verjson/example GH_STUB_STATUS="$2" \
    GITHUB_OUTPUT="$sandbox/output" \
    PATH="$sandbox/bin:$PATH" bash -eo pipefail "$sandbox/guard.sh" >/dev/null 2>&1
}

run_guard v1.2.3 0 \
  && pass "the guard accepts an exact existing v-prefixed SemVer tag" \
  || fail "the guard rejected a valid existing tag"
run_guard python-v1.2.3 0 @verjson python-v \
  && [ "$(cat "$sandbox/output")" = 'package-version=1.2.3' ] \
  && pass "the guard accepts a stream namespace and extracts the package SemVer" \
  || fail "the guard rejected or mis-stamped a valid stream-prefixed tag"
for version in 1.2.3 v01.2.3 v1.2 vlatest; do
  run_guard "$version" 0 \
    && fail "the guard accepted invalid version '$version'" \
    || pass "the guard rejects invalid version '$version'"
done
for pair in 'python-v v1.2.3' 'v python-v1.2.3' 'Python-v Python-v1.2.3' 'python python1.2.3'; do
  prefix="${pair%% *}"
  version="${pair#* }"
  run_guard "$version" 0 @verjson "$prefix" \
    && fail "the guard accepted namespace '$prefix' for '$version'" \
    || pass "the guard rejects namespace '$prefix' for '$version'"
done
run_guard v1.2.3 2 \
  && fail "the guard accepted a version before its contract tag exists" \
  || pass "the guard refuses publication before the contract tag exists"
run_guard v1.2.3 0 '' \
  && fail "the guard accepted an empty registry scope" \
  || pass "the guard rejects an empty registry scope at the reusable boundary"

if python3 "$root/scripts/node_release_artifact_manifest.test.py"; then
  pass "the package artifact validator rejects identity, digest, and archive-path tampering"
else
  fail "the package artifact validator regression suite failed"
fi

mkdir -p "$sandbox/package" "$sandbox/packed"
cat >"$sandbox/package/package.json" <<'JSON'
{"name":"@verjson/release-contract-fixture","version":"1.2.3","scripts":{"prepack":"touch prepack-ran"}}
JSON
pack_status=0
npm pack "$sandbox/package" --json --ignore-scripts --pack-destination "$sandbox/packed" >"$sandbox/pack.json" || pack_status=$?
[ ! -e "$sandbox/package/prepack-ran" ] || pack_status=1
if [ "$pack_status" -eq 0 ]; then
  python3 - "$sandbox/pack.json" "$sandbox/packed" "$root/scripts/node_release_artifact_manifest.py" "$sandbox/expected.json" "$sandbox/validated.json" <<'PY'
import json
import pathlib
import subprocess
import sys

pack_json, archive_dir, validator, expected_path, output_path = map(pathlib.Path, sys.argv[1:])
value = json.loads(pack_json.read_text(encoding="utf-8"))
entries = value if isinstance(value, list) else list(value.values())
assert len(entries) == 1
entry = entries[0]
assert entry["name"] == "@verjson/release-contract-fixture"
assert entry["version"] == "1.2.3"
assert (archive_dir / entry["filename"]).is_file()
assert entry["integrity"].startswith("sha512-")
(archive_dir / "package-artifacts.json").write_text(json.dumps([{
    "name": entry["name"],
    "version": entry["version"],
    "integrity": entry["integrity"],
    "filename": entry["filename"],
}]), encoding="utf-8")
expected_path.write_text(json.dumps([{"name": entry["name"], "version": entry["version"]}]), encoding="utf-8")
subprocess.run([
    sys.executable, str(validator), "--artifact-dir", str(archive_dir),
    "--expected-manifest", str(expected_path), "--output", str(output_path),
], check=True)
validated = json.loads(output_path.read_text(encoding="utf-8"))
assert len(validated) == 1 and validated[0]["integrity"] == entry["integrity"]

PY
  pack_status=$?
fi
if [ "$pack_status" -eq 0 ]; then
  pass "npm pack writes the archive to staging without running prepack"
else
  fail "the script-disabled npm pack contract failed"
fi

[ "$fails" -eq 0 ] || { echo "$fails test(s) failed."; exit 1; }
echo "All tests passed."
