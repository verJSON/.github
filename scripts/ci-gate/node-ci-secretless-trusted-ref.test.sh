#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/node-ci.yml"
docs="$root/docs/node-workflows.md"
failures=0

pass() { printf 'ok - %s\n' "$1"; }
fail() { printf 'not ok - %s\n' "$1" >&2; failures=$((failures + 1)); }

python3 - "$workflow" "$docs" <<'PY' \
  && pass "trusted-ref mode reuses the bounded secretless pipeline" \
  || fail "trusted-ref mode does not preserve the secretless pipeline boundary"
import sys
from pathlib import Path

import yaml

workflow = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
docs = Path(sys.argv[2]).read_text(encoding="utf-8")
inputs = workflow[True]["workflow_call"]["inputs"]
jobs = workflow["jobs"]
assert inputs["secretless-pr"]["default"] is False
assert inputs["secretless-trusted-ref"]["default"] is False
assert inputs["secretless-rebuild-env"]["default"] == "{}"

acquire = jobs["acquire-secretless-dependencies"]
build = jobs["build-test"]
assert acquire["if"] == "(inputs.secretless-pr || inputs.secretless-trusted-ref) && needs.eligibility.outputs.should-run != 'false'"
assert acquire["permissions"] == {"contents": "read", "packages": "read"}
assert acquire["runs-on"] == "${{ fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') }}"
assert build["permissions"] == {"contents": "read"}
assert build["runs-on"].startswith("${{ inputs.secretless-pr && fromJSON(vars.CI_LANE_UNTRUSTED")
assert "secretless-trusted-ref" not in build["runs-on"]
assert "cleanup-secretless-transfer" not in jobs
assert docs.count("permissions:\n      contents: read\n      packages: read\n      statuses: read") == 4

steps = build["steps"]
checkout = next(step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@"))
assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in checkout["with"]["submodules"]
assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in checkout["with"]["token"]
assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in checkout["with"]["persist-credentials"]
restore = next(step for step in steps if str(step.get("uses", "")).startswith("actions/cache/restore@"))
install = next(step for step in steps if step.get("name") == "Install from verified secretless npm cache")
rebuild = next(step for step in steps if step.get("name") == "Rebuild exact approved lifecycle packages without credentials")
provision = next(step for step in steps if step.get("name") == "Provision trusted compatibility sandbox")
plan = next(step for step in steps if step.get("name") == "Run exact credentialless consumer script plan")
for step in (restore, install, rebuild, plan):
    assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in step["if"]
for name in ("GH_TOKEN", "GITHUB_TOKEN", "NODE_AUTH_TOKEN", "NPM_TOKEN",
             "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
             "GOOGLE_APPLICATION_CREDENTIALS", "AZURE_CREDENTIALS",
             "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL"):
    assert install["env"][name] == ""
assert "npm ci --ignore-scripts" in install["run"]
assert "printf '%s=\\n' \"$name\"" in install["run"]
assert 'command = ["npm", "rebuild"] if package_manager == "npm" else ["corepack", "pnpm", "rebuild"]' in rebuild["run"]
assert 'unset -v GH_TOKEN GITHUB_TOKEN NODE_AUTH_TOKEN' in rebuild["run"]
assert "exec python3 - <<'PY'" in rebuild["run"]
assert rebuild["env"]["BWRAP_BINARY"] == "/usr/bin/bwrap"
assert steps.index(provision) < steps.index(rebuild)
assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in provision["if"]
assert "runner.environment == 'github-hosted'" in provision["if"]
assert "bubblewrap_metadata.st_uid not in (0, os.getuid())" in rebuild["run"]
assert '"--unshare-pid"' in rebuild["run"]
assert '"--unshare-net"' in rebuild["run"]
assert '"--tmpfs", "/"' in rebuild["run"]
assert '"--ro-bind", "/", "/"' not in rebuild["run"]
assert "bind_source(workspace, sandbox_workspace)" in rebuild["run"]
assert 'bind_source(node_modules, f"{sandbox_workspace}/node_modules", writable=True)' in rebuild["run"]
assert '"GITHUB_WORKSPACE": sandbox_workspace' in rebuild["run"]
assert '"RUNNER_TEMP": "/tmp"' in rebuild["run"]
assert '"NPM_CONFIG_USERCONFIG": "/dev/null"' in rebuild["run"]
assert '"GITHUB_ENV"' not in rebuild["run"]
assert 'rebuild_process_env = {' in rebuild["run"]
assert 'os.execve(str(bubblewrap), arguments, {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})' in rebuild["run"]
# Each planned script runs in the manifest that declared it (#1229), so the
# the mapped Node toolchain and manifest directory instead of the host paths.
assert 'node_toolchain_destination = "/opt/verjson-node-toolchain"' in plan["run"]
assert 'node_command = Path(node_toolchain_destination) / node_path.relative_to(node_toolchain_root)' in plan["run"]
assert 'npm_command = [str(node_command), npm_cli_command]' in plan["run"]
assert 'npm_command = [str(resolved_npm_executable)]' in plan["run"]
assert '"--chdir", str(sandbox_directory), "--",' in plan["run"]
assert '*npm_command, "run", name,' in plan["run"]
assert "subprocess.run(" in plan["run"] and "pass_fds=tuple(" in plan["run"]
assert "global_config_file.fileno()" in plan["run"]

for command in ("npm run build", "npm run typecheck --if-present", "npm test", "npm run lint --if-present"):
    step = next(step for step in steps if step.get("run") == command)
    assert "!(inputs.secretless-pr || inputs.secretless-trusted-ref)" in step["if"]
PY

boundary_script="$(mktemp)"
trap 'rm -f "$boundary_script"' EXIT
python3 - "$workflow" "$boundary_script" <<'PY'
import sys
from pathlib import Path

import yaml

workflow = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
step = next(step for step in workflow["jobs"]["acquire-secretless-dependencies"]["steps"]
            if step.get("name") == "Enforce the secretless event boundary")
Path(sys.argv[2]).write_text(step["run"], encoding="utf-8")
assert step["env"]["SECRETLESS_PR"] == "${{ inputs.secretless-pr }}"
assert step["env"]["SECRETLESS_TRUSTED_REF"] == "${{ inputs.secretless-trusted-ref }}"
PY

run_boundary() {
  EVENT_NAME="$1" HEAD_REPOSITORY="$2" REPOSITORY="Verjson/example" \
    NODE_AUTH_TOKEN="${3-token}" SCHEMA_DIR='' APPROVED_INTERNAL_PACKAGES='' \
    SECRETLESS_PR="$4" SECRETLESS_TRUSTED_REF="$5" bash "$boundary_script" >/dev/null 2>&1
}

if run_boundary pull_request Verjson/example token true false \
    && ! run_boundary pull_request attacker/fork token true false \
    && run_boundary push '' token false true \
    && run_boundary workflow_dispatch '' token false true \
    && ! run_boundary pull_request Verjson/example token false true \
    && ! run_boundary push '' token true false \
    && ! run_boundary push '' token true true \
    && ! run_boundary push '' '' false true; then
  pass "event admission keeps PR and trusted-ref credential lanes disjoint"
else
  fail "event admission permits an untrusted or ambiguous credential lane"
fi

grep -qF 'secretless-trusted-ref: true' "$docs" \
  && grep -qF 'on:' "$docs" \
  && grep -qF 'push:' "$docs" \
  && grep -qF 'workflow_dispatch:' "$docs" \
  && grep -qF 'secrets: inherit' "$docs" \
  && pass "adopter guidance documents the trusted event and permission split" \
  || fail "adopter guidance omits the trusted event or permission split"

exit "$failures"
