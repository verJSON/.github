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
import sys
import yaml

raw = open(sys.argv[1], encoding="utf-8").read()
doc = yaml.safe_load(raw)
on = doc.get("on", doc.get(True))
assert set(on) == {"workflow_call"}, "node-release must not be triggerable by push or dispatch"
inputs = on["workflow_call"]["inputs"]
assert inputs["version"]["required"] is True, "version must be required"
assert inputs["prefix"]["default"] == "v"
assert inputs["scope"]["default"] == "@verjson"
assert "Required lowercase npm scope" in inputs["scope"]["description"]
assert inputs["package-dirs"]["default"] == '["."]'
assert inputs["release-assets"]["default"] == '[]'
assert inputs["contract-ref"]["required"] is True
assert set(doc["jobs"]) == {"release", "retention"}
job = doc["jobs"]["release"]
assert job["permissions"] == {"contents": "write", "packages": "write"}
steps = job["steps"]
assert "semantic-release" not in raw
assert any('gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$VERSION"' in (step.get("run") or "") for step in steps)
assert any('git describe --tags --exact-match HEAD' in (step.get("run") or "") for step in steps)
assert any('test -f "CHANGELOG/$VERSION.md"' in (step.get("run") or "") for step in steps)
guard = next(step for step in steps if "gh api" in (step.get("run") or ""))
assert "scope must be a non-empty lowercase npm scope" in guard["run"]
package_dirs = next(step for step in steps if "package-dirs must be a non-empty JSON array" in (step.get("run") or ""))
setup_node_index = next(i for i, step in enumerate(steps) if step.get("uses", "").startswith("actions/setup-node@"))
package_dirs_index = steps.index(package_dirs)
assert setup_node_index < package_dirs_index, "Node-dependent validation must run after setup-node"
assert all("node -" not in (step.get("run") or "") for step in steps[:setup_node_index]), \
    "no JavaScript may run before setup-node on bootstrap-clean runners"
publish = next(step for step in steps if "npm publish" in (step.get("run") or ""))
assert publish["env"]["NODE_AUTH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
install = next(step for step in steps if (step.get("run") or "").strip() == "bash scripts/install-node-release-dependencies.sh")
assert install["env"]["NODE_AUTH_TOKEN"] == "${{ secrets.NODE_AUTH_TOKEN }}"
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
assert steps.index(assets) < next(
    i for i, step in enumerate(steps)
    if (step.get("run") or "").strip() == "bash scripts/install-node-release-dependencies.sh"
)
for guard in ("at most 16 paths", "symlink", "100 MiB", "250 MiB", 'git cat-file blob "HEAD:$asset"'):
    assert guard in assets["run"], "missing bounded release-asset guard: %s" % guard
stamp_index = next(i for i, step in enumerate(steps) if "npm version" in (step.get("run") or ""))
build_index = next(i for i, step in enumerate(steps) if "npm run build" in (step.get("run") or ""))
assert stamp_index < build_index, "the dispatched version must be stamped before the publish build"
assert "PACKAGE_DIRS_JSON" in (steps[stamp_index].get("env") or {}), \
    "the version stamp must cover every selected package directory"
assert 'mapfile -t package_dirs < <(jq -r \'.[]\' <<<"$PACKAGE_DIRS_JSON")' in steps[stamp_index]["run"], \
    "the version stamp must iterate selected package directories"
assert 'npm version "$PACKAGE_VERSION" --prefix "$package_path"' in steps[stamp_index]["run"], \
    "the version stamp must update each selected package before its build"
assert "--allow-same-version" in steps[stamp_index]["run"], \
    "publisher stamp must accept a scaffold already at the dispatched first version"
for guard in (
    'scripts/release-prepare-packages.sh "$PACKAGE_VERSION"',
    'npm --prefix "$package_dir" run build --if-present',
    'for package_dir in "${package_dirs[@]}"',
    'package_path="./$package_dir"',
    'npm pack "$package_path" --json --ignore-scripts',
    'npm view "$package_name@$published_version" --json',
    '--registry=https://npm.pkg.github.com >"$registry_json"',
    "published.name !== expectedName",
    "published.version !== expectedVersion",
    "published.dist.integrity !== expectedIntegrity",
    "notes_limit=125000",
    'head -c 120000 "$snapshot"',
    'GITHUB_SERVER_URL" "$GITHUB_REPOSITORY" "$VERSION" "$snapshot"',
    'gh release view "$VERSION" --json tagName',
    'gh release edit "$VERSION" --notes-file',
):
    assert guard in raw, "missing restart-safety guard: %s" % guard
selected_package_build = next(step for step in steps if "Build every selected release package" in (step.get("name") or ""))
selected_package_build_index = steps.index(selected_package_build)
publish_index = next(i for i, step in enumerate(steps) if "npm publish" in (step.get("run") or ""))
assert build_index < selected_package_build_index < publish_index, \
    "every selected package must build before script-disabled npm pack/publish"
assert stamp_index < selected_package_build_index, \
    "every selected package must be stamped before its build"
assert 'mapfile -t package_dirs < <(jq -r \'.[]\' <<<"$PACKAGE_DIRS_JSON")' in selected_package_build["run"]
outputs = on["workflow_call"]["outputs"]
assert outputs["new-release-published"]["value"] == "${{ jobs.release.outputs.new-release-published }}"
assert outputs["new-release-version"]["value"] == "${{ jobs.release.outputs.new-release-version }}"
retention = doc["jobs"]["retention"]
assert retention["needs"] == "release"
assert not retention.get("continue-on-error", False), "cleanup authorization failures must fail the release workflow"
assert retention["permissions"] == {"contents": "read", "packages": "write"}
assert retention["if"] == "needs.release.outputs.new-release-published == 'true'"
cleanup = retention["steps"][-1]
assert "package_retention.py" in cleanup["run"] and "--apply" in cleanup["run"]
assert cleanup["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
print("ok   - workflow is callable only with an explicit contract version")
print("ok   - publication verifies the existing tag and immutable snapshot")
print("ok   - install and publication tokens are separated by purpose")
print("ok   - publication stamps before build and is restart-safe")
print("ok   - successful publication remains observable by callers")
print("ok   - destructive retention is least-privileged and its authorization failures remain visible")
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

[ "$fails" -eq 0 ] || { echo "$fails test(s) failed."; exit 1; }
echo "All tests passed."
