#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/node-ci.yml"
failures=0
tmp="$(mktemp -d -p /var/tmp verjson-node-ci-secretless-consumer.XXXXXX)"
runner_temp="$tmp/runner-temp"
tool_cache="$tmp/tool-cache"
toolchain="$tool_cache/node/24.19.0"
mkdir -p "$tmp/home" "$runner_temp" "$toolchain/bin"
export RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux BWRAP_BINARY="${BWRAP_BINARY:-/usr/bin/bwrap}" HOME="$tmp/home" RUNNER_TEMP="$runner_temp" RUNNER_TOOL_CACHE="$tool_cache" GITHUB_ENV="$runner_temp/_runner_file_commands/GITHUB_ENV" GITHUB_OUTPUT="$runner_temp/GITHUB_OUTPUT" CI_ENTRY_REPOSITORY=Verjson/fixture CI_ENTRY_EVENT=pull_request CI_ENTRY_REF=refs/pull/7/merge CI_ENTRY_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa CI_ENTRY_WORKFLOW_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb CI_ENTRY_RUN_ID=42 CI_ENTRY_RUN_ATTEMPT=1 CI_ENTRY_JOB_ID=build-test CI_LEVEL_ID=fixture CI_LEVEL_FAIL_FAST=true
cat > "$toolchain/bin/node" <<'NODE'
#!/usr/bin/env bash
exit 0
NODE
cat > "$toolchain/bin/npm" <<'NPM'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$GITHUB_WORKSPACE/node_modules/.npm-stub.log"
NPM
chmod +x "$toolchain/bin/node" "$toolchain/bin/npm"
trap 'chmod -R u+rwx "$tmp" 2>/dev/null; rm -rf "$tmp"' EXIT

pass() { printf 'ok - %s\n' "$1"; }
fail() { printf 'not ok - %s\n' "$1" >&2; failures=$((failures + 1)); }

python3 - "$workflow" "$tmp" "$root" <<'PY' \
  && pass "consumer extensions remain validated, credentialless, and canonical" \
  || fail "consumer extension workflow structure violates the secretless contract"
import ast
import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

doc = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
inputs = doc[True]["workflow_call"]["inputs"]
assert inputs["approved-internal-scopes"]["default"] == "@verjson"
assert inputs["secretless-auxiliary-source"]["default"] == ""
assert inputs["secretless-rebuild-packages"]["default"] == ""
assert inputs["secretless-skip-install-scripts"]["default"] == ""
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
def run_with_action_plan(run_script: str) -> str:
    parser = shlex.quote(str(Path(sys.argv[3]) / ".github/actions/node-ci-script-plan/script_plan.py"))
    return f'''set -euo pipefail
plan_output="$(SCRIPT_PLAN_MODE=normalize SCRIPT_PLAN_SOURCE="${{CI_SCRIPT_PLAN:-}}" \\
  NESTED_MANIFESTS_SOURCE="${{NESTED_MANIFESTS:-}}" RUNNER_TEMP="${{RUNNER_TEMP:?}}" \\
  python3 -I {parser})"
mapfile -t plan_outputs <<< "$plan_output"
[[ "${{#plan_outputs[@]}}" -eq 2 ]] || exit 1
[[ "${{plan_outputs[0]}}" == entries-file=* && "${{plan_outputs[1]}}" == default-plan=* ]] || exit 1
SCRIPT_PLAN_ENTRIES_FILE="${{plan_outputs[0]#entries-file=}}"
SCRIPT_PLAN_DEFAULT_PLAN="${{plan_outputs[1]#default-plan=}}"
case "$SCRIPT_PLAN_ENTRIES_FILE" in "$RUNNER_TEMP"/*) ;; *) exit 1 ;; esac
export SCRIPT_PLAN_ENTRIES_FILE SCRIPT_PLAN_DEFAULT_PLAN
unset CI_SCRIPT_PLAN
{run_script}'''

plan_runner = run_with_action_plan(plan["run"])
assert "inputs.secretless-pr" in rebuild["if"] and "secrets." not in str(rebuild.get("env", {}))
assert "inputs.secretless-pr" in plan["if"] and "secrets." not in str(plan.get("env", {}))
assert '"--tmpfs", "/"' in plan["run"]
assert '"--ro-bind", "/", "/"' not in plan["run"]
assert "sandbox_entrypoint" in rebuild["run"]
assert "os.closerange(3, max_fd)" in rebuild["run"]
assert "os.execvpe(sys.argv[1], sys.argv[1:], os.environ)" in rebuild["run"]
assert 'os.execve(str(bubblewrap), arguments,' in rebuild["run"]
assert 'str(bubblewrap)' in plan["run"]
assert '"GITHUB_WORKSPACE": sandbox_workspace' in plan["run"]
assert '"RUNNER_TEMP": "/tmp"' in plan["run"]
assert 'bind_source(arguments, workspace, sandbox_workspace, writable=True)' in plan["run"]
assert 'bind_source(arguments, git_metadata, f"{sandbox_workspace}/.git")' in plan["run"]
assert '"--file", str(global_config_file.fileno()), "/dev/shm/npm-globalconfig"' in plan["run"]
assert "global_config_file.fileno()" in plan["run"]
assert '"PATH": f"{node_toolchain_bin}:/usr/bin:/bin"' in plan["run"]
assert "secretless candidate scripts require a GitHub-hosted Linux runner" in plan["run"]
assert "env=script_env" in plan["run"]
assert plan["env"]["BASH_ENV"] == "/dev/null"
for command_file in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE", "GITHUB_STEP_SUMMARY"):
    assert f'"{command_file}"' in plan["run"]
compatibility = next(
    step for step in build["steps"]
    if step.get("name") == "Run runtime-resolved compatibility lanes without credentials"
)
assert '"--tmpfs", "/"' in compatibility["run"]
assert '"--ro-bind", "/", "/"' not in compatibility["run"]
assert '"--ro-bind", "/usr", "/usr"' in compatibility["run"]
assert "unset -v GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT GITHUB_STATE GITHUB_STEP_SUMMARY" in compatibility["run"]
assert "exec /usr/bin/python3 -I - <<'PY'" in compatibility["run"]
for command_file in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE", "GITHUB_STEP_SUMMARY"):
    assert f'"{command_file}"' in compatibility["run"]
assert "RUN_DEFAULTS" not in plan["env"]
assert 'run_defaults = os.environ.get("SCRIPT_PLAN_DEFAULT_PLAN") == "true"' in plan["run"]
assert plan["env"]["SCRIPT_PLAN_DEFAULT_PLAN"] == "${{ steps.node-ci-script-plan.outputs.default-plan }}"
assert plan["env"]["SCRIPT_PLAN_ENTRIES_FILE"] == "${{ steps.node-ci-script-plan.outputs.entries-file }}"
assert "if run_defaults:" in plan["run"]
assert "inputs.secretless-pr" in plan["if"] and "inputs.secretless-trusted-ref" in plan["if"]
assert "subprocess.Popen(" in plan["run"]
assert "start_new_session=True" in plan["run"]
assert "extinguish_supervised_process_group(process)" in plan["run"]
assert '"--chdir", str(sandbox_directory), "--",' in plan["run"]
assert '"/usr/bin/python3", "-I", "-c", sandbox_entrypoint' in plan["run"]
assert "pass_fds=tuple(" in plan["run"]
assert "global_config_file.fileno()" in plan["run"]
assert "env=script_env" in plan["run"]
for command_file in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE", "GITHUB_STEP_SUMMARY"):
    assert f'"{command_file}"' in plan["run"]
protected_doc = yaml.safe_load(
    Path(sys.argv[1]).with_name("node-ci-protected.yml").read_text(encoding="utf-8")
)
protected_build = protected_doc["jobs"]["build-test"]
protected_plan = next(
    step for step in protected_build["steps"]
    if step.get("name") == "Run exact credentialless consumer script plan"
)
assert 'python3 -I "$RUNNER_TEMP/ci_build_artifacts.py" run-protected-candidate-plan' in protected_plan["run"]
protected_helper_source = Path(sys.argv[3], "scripts/ci_build_artifacts.py").read_text(encoding="utf-8")
protected_helper_lines = protected_helper_source.splitlines()
protected_helper_ast = ast.parse(protected_helper_source)
protected_plan_function = next(
    node for node in protected_helper_ast.body
    if isinstance(node, ast.FunctionDef) and node.name == "run_protected_candidate_plan"
)
body_start = protected_plan_function.body[0].lineno
body_end = protected_plan_function.end_lineno
protected_plan_source = textwrap.dedent(
    "\n".join(protected_helper_lines[body_start - 1:body_end])
)
protected_functions = {
    node.name: node for node in protected_plan_function.body
    if isinstance(node, ast.FunctionDef)
}

def loaded_names(node):
    names = set()

    def visit(child):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            names.add(child.id)
        for descendant in ast.iter_child_nodes(child):
            visit(descendant)

    for statement in node.body:
        visit(statement)
    return names

parser_imports = {
    alias.name
    for node in protected_plan_function.body
    if isinstance(node, ast.ImportFrom) and node.module == "urllib.parse"
    for alias in node.names
}
assert {"parse_qsl", "unquote", "urlsplit"} <= parser_imports
assert {"parse_qsl", "unquote"} <= loaded_names(
    protected_functions["credential_component_is_present"]
)
assert {"urlsplit", "credential_component_is_present"} <= loaded_names(
    protected_functions["credential_environment_value_is_sensitive"]
)
assert {"unquote"} <= loaded_names(
    protected_functions["opaque_service_value_contains_userinfo"]
)
assert {"urlsplit", "credential_component_is_present"} <= loaded_names(
    protected_functions["validate_candidate_service_value"]
)
entrypoint_assignment = next(
    node for node in protected_plan_function.body
    if isinstance(node, ast.Assign)
    and any(
        isinstance(target, ast.Name) and target.id == "sandbox_entrypoint"
        for target in node.targets
    )
)
sandbox_entrypoint = ast.literal_eval(entrypoint_assignment.value)
assert "os.closerange(3, max_fd)" in sandbox_entrypoint
assert "os.execvpe(sys.argv[1], sys.argv[1:], os.environ)" in sandbox_entrypoint
assert '"--tmpfs", "/"' in protected_plan_source
assert '"--ro-bind", "/", "/"' not in protected_plan_source
assert 'legacy_network_args = [] if shared_artifact or requires_services else ["--unshare-net"]' in protected_plan_source
assert 'bubblewrap_network_args(shared_artifact=True)' in protected_plan_source
assert "*legacy_network_args," in protected_plan_source
assert "if requires_services:" in protected_plan_source
assert "script_env.update(candidate_service_env)" in protected_plan_source
assert "candidate scripts requiring services need an isolated GitHub-hosted runner" in protected_plan_source
assert '"--unshare-net"' in protected_plan_source
assert 'script_env = {' in protected_plan_source
assert "candidate_service_env" in protected_plan_source
assert "credential_environment_name_pattern" in protected_plan_source
assert "credential_parameter_name_pattern" in protected_plan_source
assert "candidate service URL contains credential-bearing data" in protected_plan_source
assert "credentialed candidate service URLs must target the local service" in protected_plan_source
assert '"COREPACK_"' in protected_plan_source
assert "git_metadata" in protected_plan_source and "git_mount_args" in protected_plan_source
assert '"COREPACK_HOME": str(corepack_home) if corepack_home is not None' in protected_plan_source
assert '"COREPACK_ENABLE_NETWORK": "0"' in protected_plan_source
assert '"--bind" if tool_prefix == browser_cache else "--ro-bind"' in protected_plan_source
assert 'from urllib.parse import parse_qsl, unquote, urlsplit' in compatibility["run"]
assert '"/usr/bin/python3", "-I", "-c", sandbox_entrypoint' in protected_plan_source
assert "BASH_ENV" in protected_plan_source
assert "env.BASH_ENV" not in protected_plan_source
assert "BASH_ENV" not in protected_plan["run"]
assert '"--unshare-net"' in rebuild["run"]
source_defaults = [
    step for step in build["steps"]
    if step.get("run") in (
        "npm run build", "npm run typecheck --if-present", "npm test", "npm run lint --if-present"
    )
]
assert len(source_defaults) == 4
assert all("!(inputs.secretless-pr || inputs.secretless-trusted-ref)" in step["if"]
           for step in source_defaults)
protected_defaults = next(
    step for step in protected_build["steps"]
    if step.get("name") == "Run default build, typecheck, test, and lint plan"
)
assert "!(inputs.secretless-pr || inputs.secretless-trusted-ref)" in protected_defaults["if"]
assert "env.BASH_ENV" not in protected_defaults["run"]

fixture = Path(sys.argv[2]) / "default-plan"
fixture.mkdir()
tool_cache = Path(sys.argv[2]) / "tool-cache"
toolchain = tool_cache / "node" / "24.19.0" / "x64"
bin_dir = toolchain / "bin"
bin_dir.mkdir(parents=True)
(fixture / ".git").mkdir()
(fixture / "node_modules").mkdir()
(fixture / "package.json").write_text(
    '{"scripts":{"build":"fixture","typecheck":"fixture","test":"fixture","lint":"fixture","fail":"fixture","spawn":"fixture","after":"fixture"}}',
    encoding="utf-8",
)
command_log = fixture / "node_modules" / ".npm-commands"
service_log = fixture / "node_modules" / ".npm-services"
host_path_probe_log = fixture / "node_modules" / ".host-path-probes"
command_files_dir = Path(os.environ["RUNNER_TEMP"]) / "_runner_file_commands"
command_files_dir.mkdir(parents=True)
command_file = command_files_dir / "set_env_probe"
command_file.write_text("BASELINE=preserved\n", encoding="utf-8")
node = bin_dir / "node"
host_home_sentinel = Path(os.environ["HOME"]) / ".host-runner-sentinel"
host_home_sentinel.write_text("runner home must stay hidden\n", encoding="utf-8")
node.write_text(
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    'for host_path in "$HOST_HOME_SENTINEL" /run/docker.sock /var/run/docker.sock; do\n'
    '  [[ ! -e "$host_path" ]] || exit 23\n'
    'done\n'
    'printf "hidden\\n" >> "$HOST_PATH_PROBE_LOG"\n'
    "shift\n"
    'printf "%s\\n" "$*" >> "$NPM_COMMAND_LOG"\n'
    'printf "%s|%s|%s|%s|%s\\n" "$*" "${DATABASE_URL-unset}" '
    '"${OPENAI_API_KEY-unset}" "${POSTGRES_PASSWORD-unset}" "${DB_ENV-unset}" '
    '>> "$NPM_SERVICE_LOG"\n'
    'for name in GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT GITHUB_STATE GITHUB_STEP_SUMMARY; do\n'
    '  if [[ -v "$name" ]]; then exit 9; fi\n'
    "done\n"
    'mkdir -p "$RUNNER_TEMP/_runner_file_commands"\n'
    'printf "BASH_ENV=/tmp/hostile-candidate.sh\\n" >> "$RUNNER_TEMP/_runner_file_commands/set_env_probe"\n'
    "    case \" $* \" in\n"
    "      *' run fail'*) exit 23 ;;\n"
    "      *' run spawn'*)\n"
    '        (sleep 1; touch /workspace/node_modules/.child-late) &\n'
    '        touch /workspace/node_modules/.child-started\n'
    '        for _ in {1..100}; do [[ -f /workspace/node_modules/.child-started ]] && break; sleep 0.01; done\n'
    '        [[ -f /workspace/node_modules/.child-started ]] || exit 28 ;;\n'
    '      *\' run after\'*) sleep 2; [[ ! -f /workspace/node_modules/.child-late ]] || exit 29 ;;\n'
    '    esac\n',
    encoding="utf-8",
)
node.chmod(0o755)
npm = bin_dir / "npm"
npm.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
npm.chmod(0o755)
npm_cli = toolchain / "lib/node_modules/npm/bin/npm-cli.js"
npm_cli.parent.mkdir(parents=True)
npm_cli.write_text("// fixture npm cli\n", encoding="utf-8")
runner_environment = os.environ.copy()
runner_environment.update({
    "PATH": f"{bin_dir}:/usr/bin:/bin",
    "RUNNER_TOOL_CACHE": str(tool_cache),
    "HOME": str(fixture),
    "NPM_COMMAND_LOG": "/workspace/node_modules/.npm-commands",
    "NPM_SERVICE_LOG": "/workspace/node_modules/.npm-services",
    "HOST_HOME_SENTINEL": str(host_home_sentinel),
    "HOST_PATH_PROBE_LOG": "/workspace/node_modules/.host-path-probes",
    "CI_SCRIPT_PLAN": "",
    "NESTED_MANIFESTS": "",
    "RUNNER_ENVIRONMENT": "github-hosted",
    "RUNNER_OS": "Linux",
    "GITHUB_WORKSPACE": str(fixture),
    "GITHUB_ENV": str(command_file),
    "GITHUB_OUTPUT": str(fixture / "runner-output"),
    "VERJSON_CI_OUTPUT_FILE": str(fixture / "runner-output"),
    "CI_ENTRY_REPOSITORY": "Verjson/fixture",
    "CI_ENTRY_EVENT": "pull_request",
    "CI_ENTRY_REF": "refs/pull/7/merge",
    "CI_ENTRY_COMMIT": "a" * 40,
    "CI_ENTRY_WORKFLOW_SHA": "b" * 40,
    "CI_ENTRY_RUN_ID": "42",
    "CI_ENTRY_RUN_ATTEMPT": "2",
    "CI_ENTRY_JOB_ID": "build-test",
    "CI_LEVEL_ID": "test-levels",
    "CI_LEVEL_FAIL_FAST": "true",
    "DB_ENV": "POSTGRES_PASSWORD=postgres\nDATABASE_URL=postgres://app:pw@127.0.0.1:5432/app\nOPENAI_API_KEY=ci-dummy-key",
    "POSTGRES_PASSWORD": "postgres",
    "DATABASE_URL": "postgres://app:pw@127.0.0.1:5432/app",
    "OPENAI_API_KEY": "ci-dummy-key",
})
runner_temp = Path(runner_environment["RUNNER_TEMP"])
shutil.copyfile(Path(sys.argv[3]) / "scripts/ci_build_artifacts.py", runner_temp / "ci_build_artifacts.py")
hostile_bash_env = fixture / "hostile-bash-env"
startup_marker = fixture / "bash-env-sourced"
hostile_bash_env.write_text(f"printf sourced > {startup_marker}\n", encoding="utf-8")
hostile_environment = runner_environment.copy()
hostile_environment["BASH_ENV"] = str(hostile_bash_env)
control = subprocess.run(
    ["bash", "--noprofile", "--norc", "-c", "true"],
    cwd=fixture,
    env=hostile_environment,
    check=False,
    capture_output=True,
    text=True,
)
assert control.returncode == 0 and startup_marker.exists(), "hostile BASH_ENV control did not execute"
startup_marker.unlink()
runner_environment["BASH_ENV"] = plan["env"]["BASH_ENV"]
result = subprocess.run(
    ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", plan_runner],
    cwd=fixture,
    env=runner_environment,
    check=False,
    capture_output=True,
    text=True,
)
assert result.returncode == 0, result.stderr
assert not startup_marker.exists(), "step-level BASH_ENV guard allowed hostile startup code"
assert command_file.read_text(encoding="utf-8") == "BASELINE=preserved\n", (
    "candidate script changed the host runner command file"
)
assert command_log.read_text(encoding="utf-8").splitlines() == [
    "run build", "run typecheck", "run test", "run lint"
]
assert service_log.read_text(encoding="utf-8").splitlines() == [
    "run build|unset|unset|unset|unset",
    "run typecheck|unset|unset|unset|unset",
    "run test|postgres://app:pw@127.0.0.1:5432/app|ci-dummy-key|unset|unset",
    "run lint|unset|unset|unset|unset",
]
command_log.write_text("", encoding="utf-8")
service_log.write_text("", encoding="utf-8")
host_path_probe_log.write_text("", encoding="utf-8")
runner_environment["CI_SCRIPT_PLAN"] = " \t "
result = subprocess.run(
    ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", plan_runner],
    cwd=fixture,
    env=runner_environment,
    check=False,
    capture_output=True,
    text=True,
)
assert result.returncode == 0, result.stderr
assert command_log.read_text(encoding="utf-8").splitlines() == [
    "run build", "run typecheck", "run test", "run lint"
], "whitespace-only script plan skipped default consumer scripts"
assert service_log.read_text(encoding="utf-8").splitlines() == [
    "run build|unset|unset|unset|unset",
    "run typecheck|unset|unset|unset|unset",
    "run test|postgres://app:pw@127.0.0.1:5432/app|ci-dummy-key|unset|unset",
    "run lint|unset|unset|unset|unset",
]
command_log.write_text("", encoding="utf-8")
service_log.write_text("", encoding="utf-8")
runner_environment["CI_SCRIPT_PLAN"] = ""
runner_environment["CI_LEVEL_FAIL_FAST"] = "false"
runner_environment["CI_SCRIPT_PLAN"] = '["fail","spawn","after"]'
receipts_before = set(runner_temp.glob(".verjson-node-ci-entry-receipts-*"))
result = subprocess.run(
    ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", plan_runner],
    cwd=fixture,
    env=runner_environment,
    check=False,
    capture_output=True,
    text=True,
)
assert result.returncode != 0, "failFast=false must fail the level after ordinary entry failures"
assert command_log.read_text(encoding="utf-8").splitlines() == [
    "run fail", "run spawn", "run after"
], f"failFast=false did not continue through every candidate script: {command_log.read_text(encoding='utf-8')} {result.stderr}"
assert not (fixture / "node_modules/.child-late").exists(), (
    "source executor advanced while a detached candidate process could still run"
)
receipt_dirs = list(set(runner_temp.glob(".verjson-node-ci-entry-receipts-*")) - receipts_before)
assert len(receipt_dirs) == 1, "source executor did not leave one trusted receipt directory"
receipts = [
    json.loads((receipt_dirs[0] / f"entry-{ordinal:06d}.json").read_text(encoding="utf-8"))
    for ordinal in range(3)
]
assert [receipt["exitCode"] for receipt in receipts] == [23, 0, 0]
assert all(receipt["cleanup"] == "verified" for receipt in receipts)
print("source Bubblewrap executor continued after exit 23 and extinguished the detached child before the next entry")
runner_environment["CI_SCRIPT_PLAN"] = '["fail","after"]'
runner_environment["CI_LEVEL_FAIL_FAST"] = "true"
command_log.write_text("", encoding="utf-8")
receipts_before = set(runner_temp.glob(".verjson-node-ci-entry-receipts-*"))
result = subprocess.run(
    ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", plan_runner],
    cwd=fixture,
    env=runner_environment,
    check=False,
    capture_output=True,
    text=True,
)
assert result.returncode != 0, "failFast=true must fail after an ordinary entry failure"
assert command_log.read_text(encoding="utf-8").splitlines() == ["run fail"]
receipt_dirs = list(set(runner_temp.glob(".verjson-node-ci-entry-receipts-*")) - receipts_before)
assert len(receipt_dirs) == 1
receipts = [
    json.loads((receipt_dirs[0] / f"entry-{ordinal:06d}.json").read_text(encoding="utf-8"))
    for ordinal in range(2)
]
assert [receipt["state"] for receipt in receipts] == ["completed", "not-run-fail-fast"]
assert receipts[1]["exitCode"] is None and receipts[1]["signal"] is None
print("source Bubblewrap executor records not-run receipts when fail-fast is enabled")
assert (fixture / "node_modules/.host-path-probes").read_text(encoding="utf-8").splitlines() == [
    "hidden"
] * 8
print("consumer sandbox hides runner home and Docker socket paths")
print("secretless default plan scopes service variables to the test script")
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
            run_script = plan_runner if step["name"] == "Run exact credentialless consumer script plan" else step["run"]
            Path(sys.argv[2], names[step["name"]]).write_text(run_script, encoding="utf-8")
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

plan_toolchain="$tool_cache/node/22.0.0/x64"
mkdir -p "$plan_toolchain/bin" "$tmp/commands"
cat > "$plan_toolchain/bin/node" <<'NODE'
#!/usr/bin/env bash
exit 0
NODE
cat > "$plan_toolchain/bin/npm" <<'NPM'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$NPM_STUB_LOG"
if [ -n "${NPM_STUB_ENV_LOG:-}" ]; then
  printf '%s=%s\n' "${*: -1}" "${OTEL_SDK_DISABLED-unset}" >> "$NPM_STUB_ENV_LOG"
  for name in GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT GITHUB_STATE GITHUB_STEP_SUMMARY; do
    if [[ -v $name ]]; then
      printf '%s=%s\n' "$name" "${!name}" >> "$NPM_STUB_ENV_LOG"
    else
      printf '%s=unset\n' "$name" >> "$NPM_STUB_ENV_LOG"
    fi
  done
fi
NPM
chmod +x "$plan_toolchain/bin/node" "$plan_toolchain/bin/npm"
printf '%s\n' '{"scripts":{"verify:worker-schema":"fixture","build":"fixture","audit:deps":"fixture","lint":"fixture","test":"fixture","typecheck:smoke":"fixture","smoke:otel":"fixture"}}' \
  > "$tmp/commands/package.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/argon2":{"name":"argon2","hasInstallScript":true},"node_modules/esbuild":{"name":"esbuild","hasInstallScript":true},"node_modules/leftpad":{"name":"leftpad"},"node_modules/@esbuild/linux-x64":{"name":"@esbuild/linux-x64","optional":true,"os":["linux"],"hasInstallScript":true}}}' \
  > "$tmp/commands/package-lock.json"
mkdir -p "$tmp/commands/.git" "$tmp/commands/node_modules/argon2" "$tmp/commands/node_modules/esbuild" "$tmp/commands/node_modules/leftpad"

plan='["verify:worker-schema","build","audit:deps","lint","test","typecheck:smoke",{"script":"smoke:otel","unsetEnv":["OTEL_SDK_DISABLED"]}]'
if (cd "$tmp/commands" && PATH="$plan_toolchain/bin:$PATH" \
    NPM_STUB_LOG=/workspace/node_modules/.plan.log \
    NPM_STUB_ENV_LOG=/workspace/node_modules/.plan-env.log OTEL_SDK_DISABLED=true \
        GITHUB_ENV="$runner_temp/_runner_file_commands/GITHUB_ENV" GITHUB_PATH="$tmp/runner-path" \
    GITHUB_OUTPUT="$tmp/runner-output" GITHUB_STATE="$tmp/runner-state" \
    GITHUB_STEP_SUMMARY="$tmp/runner-summary" \
    CI_SCRIPT_PLAN="$plan" bash "$tmp/plan.sh") \
    && [ "$(wc -l < "$tmp/commands/node_modules/.plan.log")" -eq 7 ] \
    && [ "$(head -1 "$tmp/commands/node_modules/.plan.log")" = 'run verify:worker-schema' ] \
    && [ "$(tail -1 "$tmp/commands/node_modules/.plan.log")" = 'run smoke:otel' ] \
    && grep -qFx 'smoke:otel=unset' "$tmp/commands/node_modules/.plan-env.log"; then
  pass "the exact consumer npm script plan runs in order with a bounded per-script environment clear"
else
  fail "the exact consumer npm script plan did not run in order"
fi
for command_file in GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT GITHUB_STATE GITHUB_STEP_SUMMARY; do
    grep -qFx "$command_file=unset" "$tmp/commands/node_modules/.plan-env.log" \
    && pass "untrusted consumer cannot access $command_file" \
    || fail "untrusted consumer received $command_file"
done

# Node 26's validated toolcache layout keeps the launcher in bin/ but the npm
# package in lib/node_modules/npm. The launcher fixture resolves npm-prefix.js
# relative to bin/, reproducing the broken lookup without copying tool files.
node26="$tool_cache/node/26.0.0/x64"
mkdir -p "$node26/bin" "$node26/lib/node_modules/npm/bin"
cp -- "$(command -v node)" "$node26/bin/node"
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
: > "$tmp/commands/node_modules/.npm-stub.log"
node26_plan_status=0
(cd "$tmp/commands" && PATH="$node26/bin:/usr/bin:/bin" \
    GITHUB_WORKSPACE="$tmp/commands" NPM_STUB_LOG=/workspace/node_modules/.npm-stub.log \
    NODE26_TOOLCACHE="$node26" \
    CI_SCRIPT_PLAN='["build"]' bash "$tmp/plan.sh") \
    > "$tmp/node26-plan-output.log" 2>&1 || node26_plan_status=$?
if [ "$node26_plan_status" -eq 0 ] && [ "$(cat "$tmp/commands/node_modules/.npm-stub.log")" = 'run build' ]; then
  pass "the consumer script plan resolves npm from the Node 26 toolcache package layout"
elif grep -Fq "$node26/bin/node_modules/npm/bin/npm-prefix.js" "$tmp/node26-plan-output.log"; then
  fail "the Node 26 npm launcher looked for missing $node26/bin/node_modules/npm/bin/npm-prefix.js"
else
  fail "the Node 26 npm launcher layout failed: $(tail -1 "$tmp/node26-plan-output.log")"
fi

# Node 24 can expose npm itself as a symlink to npm-cli.js. The raw
# credentialless path must derive and deduplicate the resolved package root.
node24="$tool_cache/node/24.0.0/x64"
mkdir -p "$node24/bin" "$node24/lib/node_modules/npm/bin"
cp -- "$(command -v node)" "$node24/bin/node"
cat > "$node24/lib/node_modules/npm/bin/npm-cli.js" <<'JS'
#!/usr/bin/env node
const fs = require("node:fs");
fs.appendFileSync(process.env.NPM_STUB_LOG, `${process.argv.slice(2).join(" ")}\n`);
JS
chmod +x "$node24/lib/node_modules/npm/bin/npm-cli.js"
ln -s ../lib/node_modules/npm/bin/npm-cli.js "$node24/bin/npm"
: > "$tmp/commands/node_modules/.npm-stub.log"
node24_plan_status=0
(cd "$tmp/commands" && PATH="$node24/bin:/usr/bin:/bin" \
    GITHUB_WORKSPACE="$tmp/commands" NPM_STUB_LOG=/workspace/node_modules/.npm-stub.log \
    CI_SCRIPT_PLAN='["build"]' bash "$tmp/plan.sh") \
    > "$tmp/node24-plan-output.log" 2>&1 || node24_plan_status=$?
if [ "$node24_plan_status" -eq 0 ] && [ "$(cat "$tmp/commands/node_modules/.npm-stub.log")" = 'run build' ]; then
  pass "the credentialless consumer plan resolves a Node 24 npm CLI symlink"
else
  fail "the Node 24 npm CLI symlink layout failed: $(tail -1 "$tmp/node24-plan-output.log")"
fi

# #1782: Debian and Ubuntu route /usr/bin/awk (and cc, c++, which, editor, ...)
# through /etc/alternatives. A temporary-root sandbox that binds /usr but not
# that directory leaves every such name dangling, so consumer scripts calling
# awk failed with "command not found" once #1729 sandboxed them.
alternatives_toolchain="$tool_cache/node/22.1.0/x64"
mkdir -p "$alternatives_toolchain/bin"
cat > "$alternatives_toolchain/bin/node" <<'NODE'
#!/usr/bin/env bash
exit 0
NODE
cat > "$alternatives_toolchain/bin/npm" <<'NPM'
#!/usr/bin/env bash
set -u
awk 'BEGIN { print "awk-ok" }' > /workspace/node_modules/.alternatives-awk 2>&1
while IFS= read -r routed; do
  [ -e "$routed" ] || printf '%s\n' "$routed"
done < /workspace/node_modules/.alternatives-expected > /workspace/node_modules/.alternatives-missing
NPM
chmod +x "$alternatives_toolchain/bin/node" "$alternatives_toolchain/bin/npm"
python3 -I - "$tmp/commands/node_modules/.alternatives-expected" <<'PY'
import os
import sys
from pathlib import Path

routed = []
for directory in ("/usr/bin", "/usr/sbin"):
    for entry in sorted(Path(directory).iterdir()):
        if not entry.is_symlink():
            continue
        if not os.readlink(entry).startswith("/etc/alternatives/"):
            continue
        if os.path.realpath(entry).startswith("/usr/"):
            routed.append(str(entry))
Path(sys.argv[1]).write_text("".join(f"{path}\n" for path in routed), encoding="utf-8")
PY
rm -f "$tmp/commands/node_modules/.alternatives-awk" "$tmp/commands/node_modules/.alternatives-missing"
alternatives_status=0
(cd "$tmp/commands" && PATH="$alternatives_toolchain/bin:/usr/bin:/bin" \
    CI_SCRIPT_PLAN='["build"]' bash "$tmp/plan.sh") \
    > "$tmp/alternatives-plan-output.log" 2>&1 || alternatives_status=$?
if [ "$alternatives_status" -eq 0 ] \
    && grep -qFx 'awk-ok' "$tmp/commands/node_modules/.alternatives-awk"; then
  pass "a sandboxed consumer script can execute the alternatives-managed awk (#1782)"
else
  fail "a sandboxed consumer script cannot execute awk (#1782): $(cat "$tmp/commands/node_modules/.alternatives-awk" 2>/dev/null) $(tail -1 "$tmp/alternatives-plan-output.log")"
fi
if [ "$alternatives_status" -eq 0 ] && [ -f "$tmp/commands/node_modules/.alternatives-missing" ] \
    && [ ! -s "$tmp/commands/node_modules/.alternatives-missing" ]; then
  pass "every host /usr tool routed through /etc/alternatives resolves in the consumer sandbox (#1782)"
else
  fail "alternatives-routed tools dangle in the consumer sandbox (#1782): $(head -5 "$tmp/commands/node_modules/.alternatives-missing" 2>/dev/null | tr '\n' ' ')"
fi

# #1798: secretless-runtime-public-cache exists for repository-authored offline
# reinstall rehearsals. Before #1729 the consumer plan ran on the host with
# npm_config_cache naming the verified runtime cache; the sandbox gave scripts an
# empty cache instead, so `npm ci --offline` failed with ENOTCACHED
# (verjson-cli#318). This runs real npm in the real sandbox against a verified
# public blob, then checks the job's runtime cache survived a tampering script.
real_node="$(command -v node)"
real_source="$(dirname "$(dirname "$(readlink -f "$real_node")")")"
rehearsal_toolchain="$tool_cache/node/24.98.0/x64"
mkdir -p "$rehearsal_toolchain/bin" "$rehearsal_toolchain/lib/node_modules"
cp -- "$real_source/bin/node" "$rehearsal_toolchain/bin/node"
cp -a -- "$real_source/lib/node_modules/npm" "$rehearsal_toolchain/lib/node_modules/npm"
ln -s ../lib/node_modules/npm/bin/npm-cli.js "$rehearsal_toolchain/bin/npm"
rehearsal="$tmp/rehearsal"
runtime_cache="$runner_temp/secretless-runtime-cache-4242-1"
python3 -I - "$rehearsal" "$runtime_cache" <<'PY'
import base64
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

rehearsal, runtime_cache = Path(sys.argv[1]), Path(sys.argv[2])
(rehearsal / ".git").mkdir(parents=True)
(rehearsal / "consumer").mkdir()
members = {
    "package/package.json": json.dumps({"name": "fixture-dep", "version": "1.0.0", "main": "index.js"}),
    "package/index.js": "module.exports = { value: 'from-verified-cache' };\n",
}
raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode="w:gz") as archive:
    for name, content in members.items():
        data = content.encode()
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(data))
tarball = raw.getvalue()
digest = hashlib.sha512(tarball).digest()
blob = runtime_cache / "_cacache/content-v2/sha512" / digest.hex()[:2] / digest.hex()[2:4] / digest.hex()[4:]
blob.parent.mkdir(parents=True)
blob.write_bytes(tarball)
dependencies = {"fixture-dep": "1.0.0"}
(rehearsal / "consumer/package.json").write_text(json.dumps(
    {"name": "consumer", "version": "1.0.0", "dependencies": dependencies}), encoding="utf-8")
(rehearsal / "consumer/package-lock.json").write_text(json.dumps({
    "name": "consumer", "version": "1.0.0", "lockfileVersion": 3, "requires": True,
    "packages": {
        "": {"name": "consumer", "version": "1.0.0", "dependencies": dependencies},
        "node_modules/fixture-dep": {
            "version": "1.0.0",
            "resolved": "https://registry.npmjs.org/fixture-dep/-/fixture-dep-1.0.0.tgz",
            "integrity": "sha512-" + base64.b64encode(digest).decode(),
        },
    },
}), encoding="utf-8")
(rehearsal / "package.json").write_text(json.dumps(
    {"name": "rehearsal", "private": True, "scripts": {"rehearse": "node rehearse.js"}}),
    encoding="utf-8")
(rehearsal / "rehearse.js").write_text("""\
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const cache = execFileSync("npm", ["config", "get", "cache"], { encoding: "utf8" }).trim();
const content = path.join(cache, "_cacache", "content-v2");
fs.mkdirSync(content, { recursive: true });
fs.writeFileSync(path.join(content, "tampered-by-consumer"), "x");
execFileSync("npm", ["ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"], {
  cwd: path.join(__dirname, "consumer"),
  stdio: "inherit",
});
const dependency = require(path.join(__dirname, "consumer", "node_modules", "fixture-dep"));
const leaked = ["RUNTIME_CACHE_DIR", "RESTORE_PERSISTED_PUBLIC_CACHE", "SECRETLESS_RUNTIME_PUBLIC_CACHE", "RUN_ID", "RUN_ATTEMPT"]
  .filter((name) => name in process.env);
fs.writeFileSync(path.join(__dirname, "rehearsed"), `${leaked.length ? `leaked ${leaked}` : dependency.value}\\n`);
// Overwrite every staged blob in place, then seal the staging tree so the
// trusted cleanup must restore permissions before it can remove it.
const blobs = [];
const collect = (directory) => {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) collect(entryPath);
    else if (entry.isFile()) blobs.push(entryPath);
  }
};
collect(path.join(content, "sha512"));
if (blobs.length === 0) throw new Error("no staged blob to overwrite");
for (const blob of blobs) fs.writeFileSync(blob, "poisoned by consumer");
// Cleanup must never follow these into the host directory they name.
fs.symlinkSync(process.env.HOST_SEALED_TARGET, path.join(content, "sha512", "host-link"));
fs.symlinkSync(process.env.HOST_SEALED_TARGET, path.join(content, "host-link"));
fs.chmodSync(path.dirname(blobs[0]), 0o000);
fs.chmodSync(path.dirname(path.dirname(blobs[0])), 0o000);
fs.chmodSync(path.join(content, "sha512"), 0o000);
fs.chmodSync(content, 0o000);
""", encoding="utf-8")
PY
runtime_inventory() { (cd "$runtime_cache" && find . -print0 | sort -z | xargs -0 sha256sum 2>/dev/null; find . | sort); }
runtime_before="$(runtime_inventory)"
host_sealed_target="$tmp/host-sealed-target"
mkdir -p "$host_sealed_target"
chmod 0555 "$host_sealed_target"
rehearsal_status=0
(cd "$rehearsal" && PATH="$rehearsal_toolchain/bin:/usr/bin:/bin" \
    HOST_SEALED_TARGET="$host_sealed_target" \
    RUNTIME_CACHE_DIR="$runtime_cache" RUN_ID=4242 RUN_ATTEMPT=1 \
    SECRETLESS_RUNTIME_PUBLIC_CACHE=true RESTORE_PERSISTED_PUBLIC_CACHE=false \
    CI_SCRIPT_PLAN='["rehearse"]' bash "$tmp/plan.sh") \
    > "$tmp/rehearsal-output.log" 2>&1 || rehearsal_status=$?
if [ "$rehearsal_status" -eq 0 ] && [ "$(cat "$rehearsal/rehearsed" 2>/dev/null)" = from-verified-cache ]; then
  pass "a sandboxed consumer script reinstalls offline from the verified runtime cache (#1798)"
else
  fail "a sandboxed consumer script cannot reinstall from the verified runtime cache (#1798): $(grep -m1 -E 'ENOTCACHED|Error|error' "$tmp/rehearsal-output.log")"
fi
if [ "$(runtime_inventory)" = "$runtime_before" ]; then
  pass "consumer scripts receive a disposable copy, never the job's runtime cache (#1798)"
else
  fail "a consumer script changed the job's verified runtime cache (#1798)"
fi
if [ -z "$(find "$runner_temp" -maxdepth 1 -name 'verjson-consumer-runtime-cache-*' -print -quit)" ]; then
  pass "a staging copy the consumer script sealed is still removed (#1798)"
else
  fail "a sealed consumer runtime cache staging directory was left behind (#1798)"
fi
if [ -d "$host_sealed_target" ] && [ "$(stat -c %a "$host_sealed_target")" = 555 ]; then
  pass "staging cleanup never follows a planted symlink to a host directory (#1798)"
else
  fail "staging cleanup followed a planted symlink and changed a host directory (#1798): $(stat -c %a "$host_sealed_target" 2>&1)"
fi

# SIGTERM to the step's interpreter during a script must still remove the copy.
python3 -I - "$rehearsal/package.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
document = json.loads(manifest.read_text(encoding="utf-8"))
document["scripts"]["linger"] = (
    "node -e \"require('node:fs').writeFileSync('started', ''); setTimeout(() => {}, 60000)\""
)
manifest.write_text(json.dumps(document), encoding="utf-8")
PY
rm -f "$rehearsal/started"
(cd "$rehearsal" && PATH="$rehearsal_toolchain/bin:/usr/bin:/bin" \
    RUNTIME_CACHE_DIR="$runtime_cache" RUN_ID=4242 RUN_ATTEMPT=1 \
    SECRETLESS_RUNTIME_PUBLIC_CACHE=true RESTORE_PERSISTED_PUBLIC_CACHE=false \
    CI_SCRIPT_PLAN='["linger"]' exec bash "$tmp/plan.sh") > "$tmp/linger-output.log" 2>&1 &
linger_shell=$!
for _ in $(seq 1 300); do [ -e "$rehearsal/started" ] && break; sleep 0.1; done
# The step's interpreter is the plan shell's python3 child.
linger_step="$(python3 -I - "$linger_shell" <<'PY'
import sys
from pathlib import Path

shell = sys.argv[1]
for entry in Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    try:
        parent = (entry / "stat").read_text().rsplit(")", 1)[1].split()[1]
        command = (entry / "comm").read_text().strip()
    except (OSError, IndexError):
        continue
    if parent == shell and command.startswith("python3"):
        print(entry.name)
PY
)"
staged_while_running="$(find "$runner_temp" -maxdepth 1 -name 'verjson-consumer-runtime-cache-*' -print -quit)"
[ -n "$linger_step" ] && kill -TERM "$linger_step"
linger_status=0
wait "$linger_shell" || linger_status=$?
if [ -n "$linger_step" ] && [ -n "$staged_while_running" ] && [ "$linger_status" -ne 0 ] \
    && [ -z "$(find "$runner_temp" -maxdepth 1 -name 'verjson-consumer-runtime-cache-*' -print -quit)" ]; then
  pass "SIGTERM during a consumer script still removes the staging copy (#1798)"
else
  fail "SIGTERM left the staging copy behind (#1798): step=${linger_step:-none} staged=${staged_while_running:-none} status=$linger_status"
  find "$runner_temp" -maxdepth 1 -name 'verjson-consumer-runtime-cache-*' -exec chmod -R u+rwx {} + 2>/dev/null
  find "$runner_temp" -maxdepth 1 -name 'verjson-consumer-runtime-cache-*' -exec rm -rf {} + 2>/dev/null
fi

# A requested cache that is missing, relocated, or carries a non-regular blob
# fails before any candidate code runs; an unrequested absent cache is a no-op.
rehearsal_case() {
  local label="$1" expected="$2" cache_dir="$3" run_id="$4" requested="$5" status=0
  rm -f "$rehearsal/rehearsed" "$rehearsal/started"
  (cd "$rehearsal" && PATH="$rehearsal_toolchain/bin:/usr/bin:/bin" \
      RUNTIME_CACHE_DIR="$cache_dir" RUN_ID="$run_id" RUN_ATTEMPT=1 \
      SECRETLESS_RUNTIME_PUBLIC_CACHE="$requested" RESTORE_PERSISTED_PUBLIC_CACHE=false \
      CI_SCRIPT_PLAN='["probe"]' bash "$tmp/plan.sh") \
      > "$tmp/rehearsal-case.log" 2>&1 || status=$?
  if [ -n "$(find "$runner_temp" -maxdepth 1 -name 'verjson-consumer-runtime-cache-*' -print -quit)" ]; then
    fail "$label left a runtime cache staging directory behind (#1798)"
  elif [ "$expected" = reject ] && [ "$status" -ne 0 ] && [ ! -e "$rehearsal/started" ] \
      && grep -q 'consumer runtime cache' "$tmp/rehearsal-case.log"; then
    pass "$label fails closed before candidate code runs (#1798)"
  elif [ "$expected" = run ] && [ "$status" -eq 0 ] && [ -e "$rehearsal/started" ]; then
    pass "$label runs the candidate script without a cache (#1798)"
  else
    fail "$label: status $status, $(tail -1 "$tmp/rehearsal-case.log")"
  fi
}
python3 -I - "$rehearsal/package.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
document = json.loads(manifest.read_text(encoding="utf-8"))
document["scripts"]["probe"] = "node -e \"require('node:fs').writeFileSync('started', '')\""
manifest.write_text(json.dumps(document), encoding="utf-8")
PY
rehearsal_case "a requested runtime cache that was never populated" reject "$runner_temp/secretless-runtime-cache-4343-1" 4343 true
rehearsal_case "a requested runtime cache outside the run-scoped path" reject "$runtime_cache" 4343 true
rehearsal_case "a requested runtime cache without a path" reject "" 4242 true
rehearsal_case "an unrequested absent runtime cache" run "$runner_temp/secretless-runtime-cache-4343-1" 4343 false
host_secret="$tmp/host-only-secret"
printf 'host secret\n' > "$host_secret"
ln -s "$host_secret" "$runtime_cache/_cacache/content-v2/sha512/linked-blob"
rehearsal_case "a runtime cache blob that is a symlink" reject "$runtime_cache" 4242 true
rm -f "$runtime_cache/_cacache/content-v2/sha512/linked-blob"
host_directory="$tmp/host-only-directory"
mkdir -p "$host_directory"
printf 'host secret\n' > "$host_directory/blob"
ln -s "$host_directory" "$runtime_cache/_cacache/content-v2/sha512/linked-directory"
rehearsal_case "a runtime cache directory that is a symlink" reject "$runtime_cache" 4242 true
rm -f "$runtime_cache/_cacache/content-v2/sha512/linked-directory"
mkfifo "$runtime_cache/_cacache/content-v2/sha512/fifo-blob"
rehearsal_case "a runtime cache blob that is a FIFO" reject "$runtime_cache" 4242 true
rm -f "$runtime_cache/_cacache/content-v2/sha512/fifo-blob"

for bad_plan in '["build","build"]' '["--help"]' '["missing"]' '{"script":"build"}' \
  '[{"script":"build","unsetEnv":["NODE_AUTH_TOKEN"]}]'; do
  : > "$tmp/commands/bad-plan.log"
  if (cd "$tmp/commands" && PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$tmp/commands/bad-plan.log" \
      CI_SCRIPT_PLAN="$bad_plan" bash "$tmp/plan.sh") >/dev/null 2>&1 \
      || [ -s "$tmp/commands/bad-plan.log" ]; then
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

# #1888: secretless-skip-install-scripts shares the exact accounting with
# secretless-rebuild-packages but its names never reach the package manager.
skip_case() {
  local description=$1 expectation=$2 rebuild=$3 skip=$4 expected_log=$5 out
  : > "$tmp/commands/node_modules/.npm-stub.log"
  out=$(cd "$tmp/commands" && PATH="$toolchain/bin:/usr/bin:/bin" NPM_STUB_LOG="$tmp/skip-rebuild.log" \
    REBUILD_PACKAGES="$rebuild" SKIP_INSTALL_SCRIPTS="$skip" bash "$tmp/rebuild.sh" 2>&1) && status=0 || status=$?
  if [ "$expectation" = accept ]; then
    if [ "$status" -eq 0 ] && [ "$(cat "$tmp/commands/node_modules/.npm-stub.log")" = "$expected_log" ]; then
      pass "$description"
    else
      fail "$description (status $status, npm log: $(cat "$tmp/commands/node_modules/.npm-stub.log"))"
    fi
  elif [ "$status" -ne 0 ] && [ ! -s "$tmp/commands/node_modules/.npm-stub.log" ] \
      && grep -qF -- "$expected_log" <<<"$out"; then
    pass "$description"
  else
    fail "$description (status $status, npm log: $(cat "$tmp/commands/node_modules/.npm-stub.log"))"
  fi
  skip_output=$out
}
skip_case "a skipped package passes accounting and is never passed to npm rebuild (#1888)" \
  accept argon2 esbuild 'rebuild argon2'
if grep -qF 'install scripts deliberately skipped (never executed): esbuild' <<<"$skip_output"; then
  pass "the skipped set is printed in the job log (#1888)"
else
  fail "the skipped set is missing from the job log: $skip_output"
fi
skip_case "skipping every lifecycle package runs no rebuild at all (#1888)" \
  accept '' $'argon2\nesbuild' ''
# The skip-only exit precedes the hosted-runner and Bubblewrap checks: nothing runs.
: > "$tmp/commands/node_modules/.npm-stub.log"
if (cd "$tmp/commands" && PATH="$toolchain/bin:/usr/bin:/bin" RUNNER_ENVIRONMENT=self-hosted \
    REBUILD_PACKAGES='' SKIP_INSTALL_SCRIPTS=$'argon2\nesbuild' bash "$tmp/rebuild.sh") >/dev/null 2>&1 \
    && [ ! -s "$tmp/commands/node_modules/.npm-stub.log" ]; then
  pass "a skip-only run needs no hosted runner or sandbox and runs nothing (#1888)"
else
  fail "a skip-only run failed or ran a package manager on a non-hosted runner"
fi
skip_case "empty rebuild and skip lists fail with a dedicated message (#1888)" \
  reject '' '' 'at least one of secretless-rebuild-packages or secretless-skip-install-scripts is required'
skip_case "a lifecycle package on neither list still fails when another is skipped (#1888)" \
  reject '' argon2 'lock declares install scripts for packages absent from secretless-rebuild-packages and secretless-skip-install-scripts: esbuild'
skip_case "a package on both lists fails (#1888)" \
  reject $'argon2\nesbuild' esbuild 'packages named in both secretless-rebuild-packages and secretless-skip-install-scripts: esbuild'
skip_case "a skip entry the lock does not mark as needing install scripts is stale (#1888)" \
  reject $'argon2\nesbuild' leftpad 'secretless-skip-install-scripts names packages the lock does not mark as needing install scripts: leftpad'
skip_case "a skip entry installed nowhere in the lock fails (#1888)" \
  reject $'argon2\nesbuild' ghost-package 'absent from lock: ghost-package'
skip_case "a skip entry for a lock package absent from node_modules/ is stale (#1888)" \
  reject $'argon2\nesbuild' '@esbuild/linux-x64' 'names packages the lock does not mark as needing install scripts: @esbuild/linux-x64'
skip_case "a duplicated skip entry fails (#1888)" \
  reject argon2 $'esbuild\nesbuild' 'secretless-skip-install-scripts accepts unique exact npm package names only'
skip_case "a skip entry that is not an exact package name fails (#1888)" \
  reject argon2 $'esbuild\n--foreground-scripts' 'secretless-skip-install-scripts accepts unique exact npm package names only'

# Codex review of #1888: npm rebuild selects by installed name and rebuilds
# bundled dependencies, so aliases and bundles must not reach a skipped package.
mkdir -p "$tmp/alias/node_modules/esbuild" "$tmp/alias/node_modules/argon2" "$tmp/alias/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/esbuild":{"name":"argon2","hasInstallScript":true},"node_modules/argon2":{"name":"esbuild","hasInstallScript":true}}}' \
  > "$tmp/alias/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/alias/package.json"
mkdir -p "$tmp/bundle/node_modules/approved/node_modules/skipped" "$tmp/bundle/node_modules/skipped" "$tmp/bundle/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/approved":{"hasInstallScript":true},"node_modules/skipped":{"hasInstallScript":true},"node_modules/approved/node_modules/skipped":{"hasInstallScript":true,"inBundle":true}}}' \
  > "$tmp/bundle/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/bundle/package.json"
other_lock_case() {
  local description=$1 directory=$2 expectation=$3 rebuild=$4 skip=$5 expected=$6 out
  : > "$tmp/$directory/node_modules/.npm-stub.log"
  out=$(cd "$tmp/$directory" && PATH="$toolchain/bin:/usr/bin:/bin" NPM_STUB_LOG="$tmp/other-lock.log" \
    PACKAGE_MANAGER="${PACKAGE_MANAGER_OVERRIDE:-npm}" REBUILD_PACKAGES="$rebuild" SKIP_INSTALL_SCRIPTS="$skip" bash "$tmp/rebuild.sh" 2>&1) && status=0 || status=$?
  if [ "$expectation" = reject ] && [ "$status" -ne 0 ] && [ ! -s "$tmp/$directory/node_modules/.npm-stub.log" ] \
      && grep -qF -- "$expected" <<<"$out"; then
    pass "$description"
  elif [ "$expectation" = accept ] && [ "$status" -eq 0 ] && [ "$(cat "$tmp/$directory/node_modules/.npm-stub.log")" = "$expected" ]; then
    pass "$description"
  else
    fail "$description (status $status: $out)"
  fi
}
other_lock_case "an npm alias that lets a rebuild selector reach a skipped package fails (#1888)" alias reject \
  esbuild argon2 'selects npm aliases, links, or workspace directories of skipped packages: esbuild'
other_lock_case "a skipped package bundled inside a rebuilt package fails (#1888)" bundle reject \
  approved skipped 'skipped packages are bundled inside another package'
other_lock_case "a skipped package bundled elsewhere is harmless when nothing is rebuilt (#1888)" bundle accept \
  '' $'approved\nskipped' ''

# Link and workspace nodes are selected by directory name; a bundle only matters
# when its host is selected, and scoped and workspace hosts keep their identity.
mkdir -p "$tmp/link/node_modules/skipped" "$tmp/link/node_modules/approved" "$tmp/link/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/approved":{"hasInstallScript":true},"node_modules/skipped":{"resolved":"packages/approved","link":true},"packages/approved":{"name":"skipped","hasInstallScript":true}}}' \
  > "$tmp/link/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/link/package.json"
mkdir -p "$tmp/nested/node_modules/approved/node_modules/skipped" "$tmp/nested/node_modules/other/node_modules/skipped" "$tmp/nested/node_modules/skipped" "$tmp/nested/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/approved":{"hasInstallScript":true},"node_modules/other":{},"node_modules/skipped":{"hasInstallScript":true},"node_modules/other/node_modules/skipped":{"hasInstallScript":true,"inBundle":true}}}' \
  > "$tmp/nested/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/nested/package.json"
other_lock_case "a link whose directory name selects a skipped package's source fails (#1888)" link reject \
  approved skipped 'selects npm aliases, links, or workspace directories of skipped packages: approved'
other_lock_case "a skipped package bundled in an unselected host does not block the rebuild (#1888)" nested accept \
  approved skipped 'rebuild approved'

mkdir -p "$tmp/workspace/node_modules/skipped" "$tmp/workspace/node_modules/approved" "$tmp/workspace/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/approved":{"hasInstallScript":true},"node_modules/skipped":{"hasInstallScript":true},"packages/approved":{"name":"skipped","hasInstallScript":true}}}' \
  > "$tmp/workspace/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/workspace/package.json"
other_lock_case "a workspace directory whose name selects a skipped package fails (#1888)" workspace reject \
  approved skipped 'selects npm aliases, links, or workspace directories of skipped packages: approved'

mkdir -p "$tmp/scoped/node_modules/@s/approved/node_modules/skipped" "$tmp/scoped/node_modules/skipped" "$tmp/scoped/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/@s/approved":{"hasInstallScript":true},"node_modules/skipped":{"hasInstallScript":true},"node_modules/@s/approved/node_modules/skipped":{"hasInstallScript":true,"inBundle":true}}}' \
  > "$tmp/scoped/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/scoped/package.json"
other_lock_case "a skipped package bundled in a selected scoped host fails (#1888)" scoped reject \
  '@s/approved' skipped 'skipped packages are bundled inside another package'
mkdir -p "$tmp/wsbundle/node_modules/approved" "$tmp/wsbundle/node_modules/skipped" "$tmp/wsbundle/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/approved":{"hasInstallScript":true},"node_modules/skipped":{"hasInstallScript":true},"packages/approved/node_modules/skipped":{"hasInstallScript":true,"inBundle":true}}}' \
  > "$tmp/wsbundle/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/wsbundle/package.json"
other_lock_case "a skipped package bundled in a selected workspace host fails (#1888)" wsbundle reject \
  approved skipped 'skipped packages are bundled inside another package'

mkdir -p "$tmp/scopedws/node_modules/approved" "$tmp/scopedws/node_modules/skipped" "$tmp/scopedws/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/approved":{"hasInstallScript":true},"node_modules/skipped":{"hasInstallScript":true},"packages/@s/approved":{"name":"skipped","hasInstallScript":true}}}' \
  > "$tmp/scopedws/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/scopedws/package.json"
other_lock_case "a bare name does not select a scoped workspace directory (#1888)" scopedws accept \
  approved skipped 'rebuild approved'
mkdir -p "$tmp/scopedws2/node_modules/@s/approved" "$tmp/scopedws2/node_modules/skipped" "$tmp/scopedws2/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/@s/approved":{"hasInstallScript":true},"node_modules/skipped":{"hasInstallScript":true},"packages/@s/approved":{"name":"skipped","hasInstallScript":true}}}' \
  > "$tmp/scopedws2/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/scopedws2/package.json"
other_lock_case "a scoped workspace directory selected as @scope/name reaches a skipped package (#1888)" scopedws2 reject \
  '@s/approved' skipped 'selects npm aliases, links, or workspace directories of skipped packages: @s/approved'
mkdir -p "$tmp/root/node_modules/approved" "$tmp/root/node_modules/skipped" "$tmp/root/node_modules/verjson-secretless-workspace" "$tmp/root/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{"name":"root-app"},"node_modules/approved":{"hasInstallScript":true},"node_modules/skipped":{"hasInstallScript":true},"node_modules/verjson-secretless-workspace":{"hasInstallScript":true}}}' \
  > "$tmp/root/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/root/package.json"
other_lock_case "the sandbox root cannot be requested next to a skip list (#1888)" root reject \
  $'approved\nverjson-secretless-workspace' skipped 'must not select the project root'

mkdir -p "$tmp/linkbundle/node_modules/host" "$tmp/linkbundle/node_modules/skipped" "$tmp/linkbundle/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/host":{"resolved":"packages/approved","link":true},"packages/approved":{"name":"approved-pkg","hasInstallScript":true},"node_modules/skipped":{"hasInstallScript":true},"packages/approved/node_modules/skipped":{"hasInstallScript":true,"inBundle":true}}}' \
  > "$tmp/linkbundle/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/linkbundle/package.json"
other_lock_case "a skipped package bundled under a directory selected through a link name fails (#1888)" linkbundle reject \
  host skipped 'skipped packages are bundled inside another package'
mkdir -p "$tmp/scopedbundle/node_modules/skipped" "$tmp/scopedbundle/node_modules/approved" "$tmp/scopedbundle/.git"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/approved":{"hasInstallScript":true},"node_modules/skipped":{"hasInstallScript":true},"packages/@s/approved":{"name":"approved","hasInstallScript":true},"packages/@s/approved/node_modules/skipped":{"hasInstallScript":true,"inBundle":true}}}' \
  > "$tmp/scopedbundle/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/scopedbundle/package.json"
other_lock_case "a skipped package bundled in a bare-named request is unaffected by a scoped workspace host (#1888)" scopedbundle accept \
  approved skipped 'rebuild approved'

mkdir -p "$tmp/manifestbundle/node_modules/approved" "$tmp/manifestbundle/node_modules/skipped" "$tmp/manifestbundle/.git"
printf '%s\n' '{"name":"approved","bundleDependencies":["skipped"]}' > "$tmp/manifestbundle/node_modules/approved/package.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{},"node_modules/approved":{"hasInstallScript":true},"node_modules/skipped":{"hasInstallScript":true}}}' \
  > "$tmp/manifestbundle/package-lock.json"
cp "$tmp/commands/package.json" "$tmp/manifestbundle/package.json"
other_lock_case "a host manifest that bundles a skipped package fails even when the lock omits inBundle (#1888)" manifestbundle reject \
  approved skipped 'declares bundled dependencies'
for bundle_form in '{"skipped":"1.0.0"}' '["some-alias"]' true; do
  printf '%s\n' "{\"name\":\"approved\",\"bundledDependencies\":$bundle_form}" > "$tmp/manifestbundle/node_modules/approved/package.json"
  other_lock_case "a bundled-dependency declaration of any form ($bundle_form) beside a skip list fails (#1888)" manifestbundle reject \
    approved skipped 'declares bundled dependencies'
done
printf '%s\n' '{"name":"approved","bundleDependencies":["skipped"]}' > "$tmp/manifestbundle/node_modules/approved/package.json"
other_lock_case "a host with bundled dependencies still rebuilds while the skip list is empty (#1888)" manifestbundle accept \
  $'approved\nskipped' '' 'rebuild approved skipped'
mkdir -p "$tmp/shrinkwrap/node_modules/approved" "$tmp/shrinkwrap/node_modules/skipped" "$tmp/shrinkwrap/.git"
cp "$tmp/bundle/package-lock.json" "$tmp/shrinkwrap/package-lock.json"
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{}}}' > "$tmp/shrinkwrap/npm-shrinkwrap.json"
cp "$tmp/commands/package.json" "$tmp/shrinkwrap/package.json"
other_lock_case "npm-shrinkwrap.json beside a skip list fails closed (#1888)" shrinkwrap reject \
  approved skipped 'npm-shrinkwrap.json takes precedence'
for pnpm_case in pnpm-skip-only pnpm-undeclared pnpm-overlap; do
  mkdir -p "$tmp/$pnpm_case/node_modules/approved" "$tmp/$pnpm_case/node_modules/skipped" "$tmp/$pnpm_case/.git"
  printf '%s\n' "packages:" "  approved@1.0.0:" "    requiresBuild: true" "  skipped@1.0.0:" "    requiresBuild: true" "snapshots:" "  approved@1.0.0: {}" "  skipped@1.0.0: {}" \
    > "$tmp/$pnpm_case/pnpm-lock.yaml"
  cp "$tmp/commands/package.json" "$tmp/$pnpm_case/package.json"
done
PACKAGE_MANAGER_OVERRIDE=pnpm other_lock_case "pnpm: skipping every requiresBuild package runs nothing (#1888)" pnpm-skip-only accept \
  '' $'approved\nskipped' ''
PACKAGE_MANAGER_OVERRIDE=pnpm other_lock_case "pnpm: a requiresBuild package on neither list fails (#1888)" pnpm-undeclared reject \
  '' skipped 'lock declares install scripts for packages absent from secretless-rebuild-packages and secretless-skip-install-scripts: approved'
PACKAGE_MANAGER_OVERRIDE=pnpm other_lock_case "pnpm: a package on both lists fails (#1888)" pnpm-overlap reject \
  $'approved\nskipped' skipped 'packages named in both secretless-rebuild-packages and secretless-skip-install-scripts: skipped'

printf '%s\n' '{broken' > "$tmp/manifestbundle/node_modules/approved/package.json"
other_lock_case "an unreadable host manifest beside a skip list fails closed (#1888)" manifestbundle reject \
  approved skipped 'cannot read the package manifest of approved'
cp "$tmp/bundle/package-lock.json" "$tmp/shrinkwrap/package-lock.json"
skip_case "an empty skip list keeps the original absent-from-lock message (#1888)" \
  reject $'argon2\nghost-package' '' 'approved rebuild packages absent from lock: ghost-package'
printf '%s\n' '{"lockfileVersion":3,"packages":{"":{}}}' > "$tmp/commands/npm-shrinkwrap.json"
skip_case "npm-shrinkwrap.json is ignored while the skip list is empty (#1888)" \
  accept $'argon2\nesbuild' '' 'rebuild argon2 esbuild'
rm -f "$tmp/commands/npm-shrinkwrap.json"

exit "$failures"
