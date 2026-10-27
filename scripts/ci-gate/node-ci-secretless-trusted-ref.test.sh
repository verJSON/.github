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
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parents[2] / "scripts"))
import runner_lane_catalog as runner_lanes

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
assert "inputs.build-artifact-role == 'level'" in acquire["if"]
assert "inputs.build-artifact-role == 'consumer'" in acquire["if"]
assert "inputs.build-artifact-role == 'producer'" in acquire["if"]
assert "inputs.secretless-pr" in acquire["if"]
assert "inputs.secretless-trusted-ref" in acquire["if"]
assert "needs.eligibility.outputs.should-run != 'false'" in acquire["if"]
assert acquire["permissions"] == {"contents": "read", "packages": "read"}
assert acquire["runs-on"] == "${{ fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') }}"
assert build["permissions"] == {"contents": "read"}
# Both secretless builds need hosted capacity. Successful eligibility may
# use the configured untrusted lane; all other results use the hosted fallback.
secretless_prefix = (
    runner_lanes.SECRETLESS_ELIGIBLE_UNTRUSTED
    + " || "
    + runner_lanes.SECRETLESS_HOSTED_FALLBACK
    + " || "
)
assert build["runs-on"] == "$" + "{{ " + runner_lanes.expression("secretless-first") + " }}"
assert build["runs-on"].startswith("${{ " + secretless_prefix)
assert "eligibility" in build["needs"]
assert build["runs-on"].index(runner_lanes.SECRETLESS_HOSTED_FALLBACK) < build["runs-on"].index("inputs.runner != ''")
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
fail_fast_guard = next(
    step for step in steps if step.get("name") == "Reject unsupported level-local fail-fast mode"
)
helper_download = next(
    step for step in steps if step.get("name") == "Download trusted script-plan receipt supervisor"
)
assert steps.index(fail_fast_guard) == 1
assert fail_fast_guard["env"] == {
    "CI_LEVEL_FAIL_FAST_JSON": "${{ toJSON(inputs.ci-level-fail-fast) }}",
    "CI_LEVEL_SECRETLESS_PR_JSON": "${{ toJSON(inputs.secretless-pr) }}",
    "CI_LEVEL_SECRETLESS_TRUSTED_REF_JSON": "${{ toJSON(inputs.secretless-trusted-ref) }}",
}
assert steps.index(fail_fast_guard) < steps.index(plan)
assert steps.index(fail_fast_guard) < min(
    steps.index(step)
    for step in steps
    if step.get("run") in ("npm run build", "npm run typecheck --if-present", "npm test", "npm run lint --if-present")
)
assert helper_download["env"]["JOB_WORKFLOW_SHA"] == "${{ fromJSON(toJSON(job)).workflow_sha }}"
assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in helper_download["if"]
assert "${JOB_WORKFLOW_SHA}/scripts/ci_build_artifacts.py" in helper_download["run"]
assert '"$RUNNER_TEMP/ci_build_artifacts.py"' in helper_download["run"]
assert steps.index(helper_download) < steps.index(plan)

def run_fail_fast_guard(fail_fast, secretless_pr, trusted_ref):
    environment = {
        "CI_LEVEL_FAIL_FAST_JSON": fail_fast,
        "CI_LEVEL_SECRETLESS_PR_JSON": secretless_pr,
        "CI_LEVEL_SECRETLESS_TRUSTED_REF_JSON": trusted_ref,
    }
    return subprocess.run(
        ["/usr/bin/bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", fail_fast_guard["run"]],
        env=environment,
        capture_output=True,
        text=True,
    )

assert run_fail_fast_guard("false", "false", "false").returncode != 0
assert run_fail_fast_guard("false", "true", "false").returncode == 0
assert run_fail_fast_guard("false", "false", "true").returncode == 0
assert run_fail_fast_guard("true", "false", "false").returncode == 0
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
assert "exec python3 -I - <<'PY'" in rebuild["run"]
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
assert '"NPM_CONFIG_USERCONFIG": "/dev/null"' not in rebuild["run"]
assert "config_directory = '/tmp/npm-config'" in rebuild["run"]
assert "os.environ[f'NPM_CONFIG_{config_role.upper()}CONFIG'] = config_path" in rebuild["run"]
assert "os.environ[f'npm_config_{config_role}config'] = config_path" in rebuild["run"]
assert '"/usr/bin/python3",\n    "-I",\n    "-c",\n    sandbox_entrypoint,' in rebuild["run"]
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
assert "run_script_plan_with_receipts(" in plan["run"] and "pass_fds=tuple(" in plan["run"]
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
