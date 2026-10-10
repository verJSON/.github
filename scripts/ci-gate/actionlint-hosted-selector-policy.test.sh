#!/usr/bin/env bash
# Exercises the reusable actionlint boundary that exports the hosted-selector
# policy to Verjson callers while retaining .github's reviewed local exceptions.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
workflow="$root/.github/workflows/actionlint.yml"
fixtures="$root/scripts/fixtures/hosted-selector-policy"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

if python3 - "$workflow" <<'PY'
import re
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    job = yaml.safe_load(stream)["jobs"]["actionlint"]

steps = {step.get("name"): step for step in job["steps"]}
checkout = steps["Check out centrally versioned actionlint policy"]
assert checkout["if"] == "inputs.config-file == '' || github.repository_owner == 'Verjson'"
assert checkout["with"]["repository"] == "${{ job.workflow_repository }}"
assert checkout["with"]["ref"] == "${{ job.workflow_sha }}"
assert set(checkout["with"]["sparse-checkout"].splitlines()) == {
    ".github/actionlint.yaml",
    "scripts/ci-gate/hosted-selector-policy.py",
}
assert checkout["with"]["persist-credentials"] is False

install = steps["Install hosted-selector policy dependency"]
enforce = steps["Refuse literal hosted selectors in Verjson callers"]
cleanup = steps["Remove hosted-selector policy dependency"]
assert install["if"] == "github.repository_owner == 'Verjson'"
assert enforce["if"] == "github.repository_owner == 'Verjson'"
assert enforce["env"] == {
    "VERJSON_HOSTED_SELECTOR_POLICY_SHA": "${{ job.workflow_sha }}"
}
assert cleanup["if"] == "${{ always() && github.repository_owner == 'Verjson' }}"
assert cleanup["working-directory"] == "${{ github.workspace }}"
for step in (install, enforce, cleanup):
    assert 'runner_temp="$(cd "${{ runner.temp }}" && pwd -P)"' in step["run"]
assert "policy_mode=(--consumer-policy)" in enforce["run"]
assert "GITHUB_REPOSITORY" in enforce["run"]
assert "policy_mode=(--visibility public)" in enforce["run"]
assert "python3 -S" in enforce["run"]
assert 'mktemp -d "$runner_temp/verjson-hosted-selector-policy.XXXXXX"' in install["run"]
assert '>>"$GITHUB_ENV"' in install["run"]
assert 'PYTHONPATH="$policy_dir/python"' in enforce["run"]
assert 'rm -rf -- "$policy_dir"' in cleanup["run"]

environment = job["env"]
assert environment["PYYAML_VERSION"] == "6.0.2"
assert re.fullmatch(r"[0-9a-f]{64}", environment["PYYAML_SHA256"])
assert environment["PYYAML_VERSION"] in environment["PYYAML_SOURCE_URL"]
assert "sha256sum --check --strict" in install["run"]
assert '"pyyaml-${PYYAML_VERSION}/lib/yaml"' in install["run"]
PY
then
  pass "the immutable central policy, narrow mode, dependency pin, and Verjson-only gates are wired"
else
  fail "the reusable actionlint hosted-selector contract is incomplete"
fi

extract_step() {
  local name="$1" destination="$2"
  awk -v wanted="      - name: $name" '
    $0 == wanted { seen = 1 }
    seen && $0 == "        run: |" { capture = 1; next }
    capture {
      if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
      if ($0 ~ /^[[:space:]]*$/) { print ""; next }
      exit
    }
  ' "$workflow" >"$destination"
}

install_script="$tmp/install.sh"
enforce_script="$tmp/enforce.sh"
cleanup_script="$tmp/cleanup.sh"
extract_step "Install hosted-selector policy dependency" "$install_script"
extract_step "Refuse literal hosted selectors in Verjson callers" "$enforce_script"
extract_step "Remove hosted-selector policy dependency" "$cleanup_script"
for extracted in "$install_script" "$enforce_script" "$cleanup_script"; do
  sed -i 's#${{ runner.temp }}#${RUNNER_TEMP}#g' "$extracted"
done

mkdir -p "$tmp/bin"
cat >"$tmp/bin/curl" <<'SH'
#!/usr/bin/env bash
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then
    output="$2"
    break
  fi
  shift
done
printf '%s' "$DOWNLOAD_CONTENT" >"$output"
SH
cat >"$tmp/bin/tar" <<'SH'
#!/usr/bin/env bash
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-C" ]; then
    destination="$2"
    break
  fi
  shift
done
printf 'tar\n' >>"$POLICY_ACTIONS"
mkdir -p "$destination/yaml"
cp -R "$SYSTEM_YAML/." "$destination/yaml/"
SH
chmod +x "$tmp/bin/curl" "$tmp/bin/tar"

system_yaml="$(python3 -c 'import os, yaml; print(os.path.dirname(yaml.__file__))' 2>"$tmp/system-yaml.err")"
system_yaml_status=$?
if [ "$system_yaml_status" -ne 0 ] || [ -z "$system_yaml" ] \
  || [ "$system_yaml" = / ] || [ ! -f "$system_yaml/__init__.py" ]; then
  printf 'could not resolve a safe PyYAML module directory; refusing to copy it\n' >&2
  cat "$tmp/system-yaml.err" >&2
  exit 1
fi
base_path="$PATH"

mkdir -p "$tmp/missing-yaml-bin"
real_python3="$(command -v python3)"
cat >"$tmp/missing-yaml-bin/python3" <<'SH'
#!/usr/bin/env bash
if [ "${1:-}" = -c ] && [[ "${2:-}" == *'import os, yaml'* ]]; then
  printf 'PyYAML import intentionally unavailable\n' >&2
  exit 1
fi
exec "$ACTIONS_CI_REAL_PYTHON3" "$@"
SH
chmod +x "$tmp/missing-yaml-bin/python3"
if output="$(PATH="$tmp/missing-yaml-bin:$base_path" \
  ACTIONS_CI_REAL_PYTHON3="$real_python3" \
  bash "$root/scripts/ci-gate/actionlint-hosted-selector-policy.test.sh" 2>&1)"; then
  fail "a missing PyYAML import did not stop the policy fixture"
elif grep -qF 'could not resolve a safe PyYAML module directory' <<<"$output"; then
  pass "a missing dependency cannot turn the fixture copy source into the filesystem root"
else
  printf '%s\n' "$output" >&2
  fail "a missing PyYAML import failed without the safe-path diagnostic"
fi

run_install() {
  local checksum="$1" case_dir
  case_dir="$(mktemp -d "$tmp/install.XXXXXX")"
  mkdir -p "$case_dir/workspace" "$case_dir/runner-temp" \
    "$case_dir/caller-redirect/yaml"
  cat >"$case_dir/caller-redirect/yaml/_yaml.py" <<SH
from pathlib import Path
Path(${case_dir@Q} + "/hostile-module-executed").write_text("executed")
SH
  ln -s "$case_dir/caller-redirect" \
    "$case_dir/workspace/.verjson-hosted-selector-policy-deps"
  export PATH="$tmp/bin:$base_path"
  export PYYAML_VERSION=6.0.2
  export PYYAML_SOURCE_URL=https://example.invalid/pyyaml-6.0.2.tar.gz
  export PYYAML_SHA256="$checksum"
  export DOWNLOAD_CONTENT='fixture archive bytes'
  export SYSTEM_YAML="$system_yaml"
  export POLICY_ACTIONS="$case_dir/actions.log"
  export RUNNER_TEMP="$case_dir/runner-temp"
  export GITHUB_WORKSPACE="$case_dir/workspace"
  export GITHUB_ENV="$case_dir/github-env"
  : >"$POLICY_ACTIONS"
  : >"$GITHUB_ENV"
  (cd "$GITHUB_WORKSPACE" && bash "$install_script") >"$case_dir/out.txt" 2>&1
  INSTALL_RC=$?
  INSTALL_CASE_DIR="$case_dir"
  INSTALL_POLICY_DIR="$(sed -n 's/^VERJSON_HOSTED_SELECTOR_POLICY_DIR=//p' "$GITHUB_ENV")"
}

good="$(printf '%s' 'fixture archive bytes' | sha256sum | awk '{print $1}')"
run_install "$good"
if [ "$INSTALL_RC" -eq 0 ] \
  && grep -qxF tar "$POLICY_ACTIONS" \
  && [ -f "$INSTALL_POLICY_DIR/python/yaml/__init__.py" ] \
  && [ ! -L "$INSTALL_POLICY_DIR" ] \
  && [ "$(stat -c '%a' "$INSTALL_POLICY_DIR")" = 700 ] \
  && [[ "$INSTALL_POLICY_DIR" == "$RUNNER_TEMP"/verjson-hosted-selector-policy.* ]] \
  && [ ! -e "$INSTALL_CASE_DIR/hostile-module-executed" ] \
  && [ ! -e "$INSTALL_CASE_DIR/caller-redirect/pyyaml-6.0.2.tar.gz" ]; then
  pass "a matching archive installs only in a new secure runner-temporary directory"
else
  fail "the pinned YAML dependency used caller-controlled or predictable storage"
fi
GOOD_CASE_DIR="$INSTALL_CASE_DIR"
GOOD_POLICY_DIR="$INSTALL_POLICY_DIR"
GOOD_RUNNER_TEMP="$RUNNER_TEMP"
GOOD_WORKSPACE="$GITHUB_WORKSPACE"
GOOD_REDIRECT="$INSTALL_CASE_DIR/caller-redirect"

bad="0${good:1}"
[ "${good:0:1}" = 0 ] && bad="1${good:1}"
run_install "$bad"
if [ "$INSTALL_RC" -ne 0 ] && ! grep -qF tar "$POLICY_ACTIONS"; then
  pass "a dependency checksum mismatch fails before archive extraction"
else
  fail "a mismatched YAML dependency reached archive extraction"
fi

source_dir="$GOOD_WORKSPACE"
mkdir -p "$source_dir/.verjson-actionlint-policy/scripts/ci-gate" \
  "$source_dir/.github/workflows"
cp "$root/scripts/ci-gate/hosted-selector-policy.py" \
  "$source_dir/.verjson-actionlint-policy/scripts/ci-gate/hosted-selector-policy.py"
git init --quiet "$source_dir"
git init --quiet "$source_dir/.verjson-actionlint-policy"
git -C "$source_dir/.verjson-actionlint-policy" add \
  scripts/ci-gate/hosted-selector-policy.py

(cd "$source_dir" && env -u VERJSON_HOSTED_SELECTOR_POLICY_DIR \
  RUNNER_TEMP="$GOOD_RUNNER_TEMP" GITHUB_WORKSPACE="$GOOD_WORKSPACE" \
  GITHUB_REPOSITORY="Verjson/consumer" \
  bash "$enforce_script") >"$tmp/missing-dependency.out" 2>&1
missing_dependency_rc=$?
if [ "$missing_dependency_rc" -ne 0 ]; then
  pass "the enforcement step cannot fall back to an ambient PyYAML installation"
else
  fail "the enforcement step passed without its pinned YAML dependency"
fi

run_policy_fixture() {
  local fixture="$1"
  local repository="${2:-Verjson/consumer}"
  find "$source_dir/.github/workflows" -type f -delete
  cp "$fixtures/$fixture"/* "$source_dir/.github/workflows/"
  (cd "$source_dir" && \
    RUNNER_TEMP="$GOOD_RUNNER_TEMP" \
    GITHUB_WORKSPACE="$GOOD_WORKSPACE" \
    GITHUB_REPOSITORY="$repository" \
    VERJSON_HOSTED_SELECTOR_POLICY_DIR="$GOOD_POLICY_DIR" \
    bash "$enforce_script") >"$tmp/policy.out" 2>&1
  POLICY_RC=$?
}

run_policy_fixture metered-macos
if [ "$POLICY_RC" -eq 1 ]; then
  pass "the reusable step rejects a caller's literal macOS selector"
else
  fail "the reusable step did not reject a literal macOS selector"
fi

run_policy_fixture metered-windows
if [ "$POLICY_RC" -eq 1 ]; then
  pass "the reusable step rejects a caller's literal Windows selector"
else
  fail "the reusable step did not reject a literal Windows selector"
fi

run_policy_fixture rolling-linux-latest
if [ "$POLICY_RC" -eq 1 ]; then
  pass "the reusable step rejects a caller's literal Linux selector"
else
  fail "the reusable step did not reject a literal Linux selector"
fi

run_policy_fixture linux-hosted-literal
if [ "$POLICY_RC" -eq 1 ]; then
  pass "the reusable step rejects a caller's pinned Linux selector"
else
  fail "the reusable step did not reject a pinned Linux selector"
fi

find "$source_dir/.github/workflows" -type f -delete
cp "$root/.github/workflows/ai-privileged-merge.yml" \
  "$root/.github/workflows/privileged-merge-conformance.yml" \
  "$source_dir/.github/workflows/"
(cd "$source_dir" && \
  RUNNER_TEMP="$GOOD_RUNNER_TEMP" \
  GITHUB_WORKSPACE="$GOOD_WORKSPACE" \
  GITHUB_REPOSITORY="verJSON/.github" \
  VERJSON_HOSTED_SELECTOR_POLICY_DIR="$GOOD_POLICY_DIR" \
  bash "$enforce_script") >"$tmp/local-policy.out" 2>&1
local_policy_rc=$?
if [ "$local_policy_rc" -eq 0 ]; then
  pass "the local policy branch preserves .github's reviewed fixed-hosted exceptions"
else
  fail "the local policy branch did not preserve .github's reviewed exceptions"
fi

run_policy_fixture reusable-input-macos
if [ "$POLICY_RC" -eq 1 ]; then
  pass "the reusable step rejects a metered runner passed into another reusable workflow"
else
  fail "the reusable step missed a metered reusable-workflow runner input"
fi

run_policy_fixture reusable-input-dynamic
if [ "$POLICY_RC" -eq 2 ]; then
  pass "the reusable step fails closed on a dynamic runner pass-through"
else
  fail "the reusable step accepted an unresolved runner pass-through"
fi

if [ ! -e "$GOOD_CASE_DIR/hostile-module-executed" ] \
  && [ -f "$GOOD_REDIRECT/yaml/_yaml.py" ] \
  && [ ! -e "$GOOD_REDIRECT/python" ]; then
  pass "a caller-prepopulated optional module and dependency symlink are neither executed nor written"
else
  fail "caller-controlled dependency content influenced installation or enforcement"
fi

RUNNER_TEMP="$GOOD_RUNNER_TEMP" \
  VERJSON_HOSTED_SELECTOR_POLICY_DIR="$GOOD_POLICY_DIR" \
  bash "$cleanup_script" >"$tmp/cleanup.out" 2>&1
cleanup_rc=$?
if [ "$cleanup_rc" -eq 0 ] && [ ! -e "$GOOD_POLICY_DIR" ]; then
  pass "cleanup removes the exact secure runner-temporary directory"
else
  fail "cleanup did not remove the bounded policy directory"
fi

cleanup_redirect="$tmp/cleanup-redirect"
mkdir -p "$cleanup_redirect"
printf 'preserve\n' >"$cleanup_redirect/sentinel"
cleanup_symlink="$GOOD_RUNNER_TEMP/verjson-hosted-selector-policy.hostile"
ln -s "$cleanup_redirect" "$cleanup_symlink"
RUNNER_TEMP="$GOOD_RUNNER_TEMP" \
  VERJSON_HOSTED_SELECTOR_POLICY_DIR="$cleanup_symlink" \
  bash "$cleanup_script" >"$tmp/symlink-cleanup.out" 2>&1
symlink_cleanup_rc=$?
if [ "$symlink_cleanup_rc" -ne 0 ] \
  && [ -L "$cleanup_symlink" ] \
  && [ -f "$cleanup_redirect/sentinel" ]; then
  pass "cleanup refuses a substituted symlink without touching its target"
else
  fail "cleanup followed or removed a substituted dependency symlink"
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
