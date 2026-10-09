#!/usr/bin/env python3
import atexit
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/node-ci.yml"
PROTECTED_WORKFLOW = ROOT / ".github/workflows/node-ci-protected.yml"
COMMAND_FILE_ENV = (
    "GITHUB_ENV",
    "GITHUB_OUTPUT",
    "GITHUB_PATH",
    "GITHUB_STATE",
    "GITHUB_STEP_SUMMARY",
)
SENSITIVE_ENV = COMMAND_FILE_ENV + (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "NODE_AUTH_TOKEN",
    "NPM_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_CREDENTIALS",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
)


def run_rebuild(
    step,
    fixture,
    rebuild_env,
    rebuild_packages="onnxruntime-node",
    package_manager="npm",
    real_bwrap=False,
    probe_hidden_paths=False,
    runner_environment="github-hosted",
    corepack_home_override=None,
):
    home = fixture / "runner-home"
    workspace = home / "workspace"
    stub = workspace / "bin"
    runner_temp = fixture / "runner-temp"
    corepack_home = home / ".cache/node/corepack"
    corepack_home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(exist_ok=True)
    stub.mkdir(exist_ok=True)
    runner_temp.mkdir(exist_ok=True)
    executable = "npm" if package_manager == "npm" else "corepack"
    tool_cache = Path(tempfile.mkdtemp(prefix="node-ci-rebuild-toolcache-"))
    atexit.register(shutil.rmtree, tool_cache, ignore_errors=True)
    toolchain = tool_cache / "node" / "test" / "x64"
    tool_bin = toolchain / "bin"
    tool_bin.mkdir(parents=True)
    package_manager_stub = tool_bin / executable
    hidden_path_checks = ""
    if probe_hidden_paths:
        hidden_targets = tuple(
            str(runner_temp / name.lower()) for name in COMMAND_FILE_ENV
        )
        hidden_path_checks = f"hidden_targets = {hidden_targets!r}\n" \
            "hidden_path_writes = []\n" \
            "for target in hidden_targets:\n" \
            "    try:\n" \
            "        with Path(target).open('a', encoding='utf-8') as command_file:\n" \
            "            command_file.write('COMPROMISED=1\\n')\n" \
            "    except OSError:\n" \
            "        continue\n" \
            "    hidden_path_writes.append(target)\n"
        hidden_path_checks += (
            "try:\n"
            "    Path('source-tamper').write_text('COMPROMISED=1\\n')\n"
            "except OSError:\n"
            "    pass\n"
            "else:\n"
            "    hidden_path_writes.append('workspace-root-writable')\n"
        )
    package_manager_stub.write_text(
        "#!/usr/bin/python3\n"
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "import re\n"
        "import sys\n"
        "\n"
        f"sensitive = {SENSITIVE_ENV!r}\n"
        "visible = set()\n"
        "readable_processes = []\n"
        "read_errors = []\n"
        "pid = os.getpid()\n"
        "seen = set()\n"
        "test_harness_pid = int(os.environ.get('TEST_HARNESS_PID', '0'))\n"
        "while pid > 0 and pid != test_harness_pid and pid not in seen:\n"
        "    seen.add(pid)\n"
        "    try:\n"
        "        entries = Path(f'/proc/{pid}/environ').read_bytes().split(b'\\0')\n"
        "    except (FileNotFoundError, PermissionError, ProcessLookupError) as error:\n"
        "        read_errors.append(f'{pid}:{type(error).__name__}')\n"
        "        break\n"
        "    readable_processes.append(pid)\n"
        "    for entry in entries:\n"
        "        name, separator, value = entry.partition(b'=')\n"
        "        name = name.decode(errors='replace')\n"
        "        if separator and name in sensitive:\n"
        "            visible.add(f'{pid}:{name}')\n"
        "    try:\n"
        "        status = Path(f'/proc/{pid}/status').read_text(encoding='utf-8')\n"
        "        pid = int(re.search(r'^PPid:\\s+(\\d+)$', status, re.MULTILINE).group(1))\n"
        "    except (FileNotFoundError, PermissionError, ProcessLookupError, AttributeError, ValueError) as error:\n"
        "        read_errors.append(f'{pid}:{type(error).__name__}')\n"
        "        break\n"
        "if not readable_processes or read_errors:\n"
        "    raise SystemExit(f'process ancestry scan incomplete: {read_errors!r}')\n"
        "attack_targets = [os.environ[name] for name in ('GITHUB_ENV',) if name in os.environ]\n"
        "for target in attack_targets:\n"
        "    with Path(target).open('a', encoding='utf-8') as command_file:\n"
        "        command_file.write(f\"BASH_ENV={Path.cwd() / 'node_modules/malicious-bash-env'}\\n\")\n"
        + hidden_path_checks
        + "hidden_path_writes = locals().get('hidden_path_writes', [])\n"
        "Path('node_modules/.contract-capture.json').write_text(json.dumps({\n"
        "    'onnxruntime_node_install': os.environ.get('ONNXRUNTIME_NODE_INSTALL', '<unset>'),\n"
        "    'argv': [Path(sys.argv[0]).name, *sys.argv[1:]],\n"
        "    'rebuild_env': os.environ.get('REBUILD_ENV', '<unset>'),\n"
        "    'visible_command_files': sorted(name for name in os.environ if name in "
        f"{COMMAND_FILE_ENV!r}),\n"
        "    'node_auth_token': os.environ.get('NODE_AUTH_TOKEN', '<unset>'),\n"
        "    'gh_token': os.environ.get('GH_TOKEN', '<unset>'),\n"
        "    'github_token': os.environ.get('GITHUB_TOKEN', '<unset>'),\n"
        "    'home': os.environ.get('HOME'),\n"
        "    'runner_temp': os.environ.get('RUNNER_TEMP'),\n"
        "    'github_workspace': os.environ.get('GITHUB_WORKSPACE'),\n"
        "    'npm_userconfig': os.environ.get('npm_config_userconfig'),\n"
        "    'visible_sensitive_ancestors': sorted(visible),\n"
        "    'readable_processes': readable_processes,\n"
        "    'read_errors': read_errors,\n"
        "    'hidden_path_writes': hidden_path_writes,\n"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    package_manager_stub.chmod(0o755)
    node_stub = tool_bin / "node"
    node_stub.write_text("#!/usr/bin/python3\nraise SystemExit(0)\n", encoding="utf-8")
    node_stub.chmod(0o755)
    if package_manager == "npm":
        lock = {
            "packages": {
                "node_modules/onnxruntime-node": {
                    "name": "onnxruntime-node",
                    "hasInstallScript": True,
                }
            }
        }
        (workspace / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    else:
        lock = {
            "lockfileVersion": "9.0",
            "packages": {"onnxruntime-node@1.0.0": {"requiresBuild": True}},
            "snapshots": {"onnxruntime-node@1.0.0": {}},
        }
        (workspace / "pnpm-lock.yaml").write_text(yaml.safe_dump(lock), encoding="utf-8")
    (workspace / "node_modules/onnxruntime-node").mkdir(parents=True, exist_ok=True)
    capture = workspace / "node_modules/.contract-capture.json"
    malicious_bash_env = workspace / "node_modules/malicious-bash-env"
    marker = workspace / "node_modules/later-step-token-exposed"
    malicious_bash_env.write_text(
        f"printf '%s' \"${{GH_TOKEN-}}\" > {shlex.quote(str(marker))}\n",
        encoding="utf-8",
    )
    command_files = {name: runner_temp / name.lower() for name in COMMAND_FILE_ENV}
    for command_file in command_files.values():
        command_file.write_text("ORIGINAL=preserved\n", encoding="utf-8")
    capture.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)

    bwrap_stub = stub / "bwrap"
    if not real_bwrap:
        bwrap_stub.write_text(
            f"#!{sys.executable}\n"
            "import json\n"
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            "arguments = sys.argv[1:]\n"
            "separator = arguments.index('--')\n"
            "options = arguments[:separator]\n"
            "resolved_sources = {}\n"
            "environment = {}\n"
            "workspace = None\n"
            "index = 0\n"
            "while index < len(options):\n"
            "    option = options[index]\n"
            "    if option == '--setenv':\n"
            "        environment[options[index + 1]] = options[index + 2]\n"
            "        index += 3\n"
            "    elif option in ('--bind', '--ro-bind'):\n"
            "        source = options[index + 1]\n"
            "        destination = options[index + 2]\n"
            "        if source.startswith('/proc/self/fd/'):\n"
            "            resolved_sources[destination] = os.readlink(source)\n"
            "        if destination == '/tmp/verjson-secretless-workspace':\n"
            "            workspace = source\n"
            "        index += 3\n"
            "    elif option in ('--setenv', '--tmpfs', '--dir', '--chdir', '--proc', '--dev', '--cap-drop'):\n"
            "        index += 2\n"
            "    else:\n"
            "        index += 1\n"
            "if workspace is None:\n"
            "    raise SystemExit('workspace bind missing')\n"
            f"Path(__file__).with_name('bwrap-arguments.json').write_text(json.dumps({{'options': options, 'resolved_sources': resolved_sources}}))\n"
            "os.chdir(workspace)\n"
            "environment['PATH'] = environment['PATH'].replace('/opt/verjson-node-toolchain/bin', "
            f"{str(tool_bin)!r})\n"
            "environment['TEST_HARNESS_PID'] = str(os.getppid())\n"
            "command = arguments[separator + 1:]\n"
            "os.execvpe(command[0], command, environment)\n",
            encoding="utf-8",
        )
        bwrap_stub.chmod(0o755)

    env = {
        **{name: value for name, value in os.environ.items() if name not in SENSITIVE_ENV + ("BASH_ENV",)},
        "PATH": (
            f"{tool_bin}:{os.environ['PATH']}"
            if real_bwrap
            else f"{stub}:{tool_bin}:{os.environ['PATH']}"
        ),
        "HOME": str(home),
        "RUNNER_TEMP": str(runner_temp),
        "RUNNER_TOOL_CACHE": str(tool_cache),
        "COREPACK_HOME": str(corepack_home_override or corepack_home),
        "RUNNER_ENVIRONMENT": runner_environment,
        "PACKAGE_MANAGER": package_manager,
        "REBUILD_PACKAGES": rebuild_packages,
        "REBUILD_ENV": rebuild_env if isinstance(rebuild_env, str) else json.dumps(rebuild_env),
        "BWRAP_BINARY": "/usr/bin/bwrap" if real_bwrap else str(bwrap_stub),
        "NODE_AUTH_TOKEN": "fixture-secret-token",
        "NPM_TOKEN": "fixture-npm-token",
        "GH_TOKEN": "fixture-gh-token",
        "GITHUB_TOKEN": "fixture-github-token",
        "AWS_ACCESS_KEY_ID": "fixture-aws-key",
        "AWS_SECRET_ACCESS_KEY": "fixture-aws-secret",
        "AWS_SESSION_TOKEN": "fixture-aws-session",
        "GOOGLE_APPLICATION_CREDENTIALS": str(fixture / "google-credentials.json"),
        "AZURE_CREDENTIALS": "fixture-azure-credentials",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "fixture-id-token",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.invalid/id-token",
        "ONNXRUNTIME_NODE_INSTALL": "ambient-value-must-not-enable-skip",
        **{name: str(path) for name, path in command_files.items()},
    }
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    shutil.rmtree(tool_cache)
    return result, capture, command_files, marker, bwrap_stub.with_name("bwrap-arguments.json")


def assert_no_lifecycle_command_file_access(command_files, marker):
    later_env = {
        name: value
        for name, value in os.environ.items()
        if name not in SENSITIVE_ENV + ("BASH_ENV",)
    }
    later_env.update({"GH_TOKEN": "later-step-secret", "PATH": os.environ["PATH"]})
    for line in command_files["GITHUB_ENV"].read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name == "BASH_ENV":
            later_env["BASH_ENV"] = value
    later_step = subprocess.run(
        ["bash", "-c", ":"], env=later_env, capture_output=True, text=True, check=False
    )
    assert later_step.returncode == 0, later_step.stderr
    assert not marker.exists(), "a lifecycle-set BASH_ENV executed in a later token-bearing step"
    expected = "ORIGINAL=preserved\n"
    assert all(path.read_text(encoding="utf-8") == expected for path in command_files.values()), (
        "lifecycle process or an ancestor could modify a GitHub command file"
    )


def assert_rebuild_capture(capture, package_manager, onnx_value):
    observed = json.loads(capture.read_text(encoding="utf-8"))
    expected = {
        "onnxruntime_node_install": onnx_value,
        "argv": ["npm", "rebuild", "onnxruntime-node"]
        if package_manager == "npm"
        else ["corepack", "pnpm", "rebuild", "onnxruntime-node"],
        "rebuild_env": "<unset>",
        "visible_command_files": [],
        "node_auth_token": "<unset>",
        "gh_token": "<unset>",
        "github_token": "<unset>",
        "home": "/tmp/home",
        "runner_temp": "/tmp",
        "github_workspace": "/tmp/verjson-secretless-workspace",
        "npm_userconfig": "/dev/null",
        "visible_sensitive_ancestors": [],
        "read_errors": [],
        "hidden_path_writes": [],
    }
    assert all(observed.get(key) == value for key, value in expected.items() if key != "readable_processes"), (
        f"unexpected package-manager execution environment: {observed!r}"
    )
    assert observed["readable_processes"], "process ancestry scan inspected no environments"


def assert_bubblewrap_arguments(arguments_path, workspace, package_manager):
    receipt = json.loads(arguments_path.read_text(encoding="utf-8"))
    arguments = receipt["options"]
    for option in (
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-net",
        "--disable-userns",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "--proc",
    ):
        assert option in arguments, f"missing namespace boundary option: {option}"
    for target in ("/", "/tmp"):
        assert any(
            arguments[index:index + 2] == ["--tmpfs", target]
            for index in range(len(arguments) - 1)
        ), f"host directory is not masked: {target}"
    root_tmpfs_index = arguments.index("--tmpfs")
    assert arguments[root_tmpfs_index + 1] == "/", "host root is not masked"
    readonly_bind_index = next(
        index
        for index, value in enumerate(arguments[:-2])
        if value == "--ro-bind"
        and arguments[index + 2] == "/tmp/verjson-secretless-workspace"
    )
    assert arguments[readonly_bind_index + 1].startswith("/proc/self/fd/")
    assert Path(receipt["resolved_sources"]["/tmp/verjson-secretless-workspace"]) == workspace
    assert readonly_bind_index > root_tmpfs_index, "workspace source was bound before root isolation"
    assert arguments[readonly_bind_index + 2] == "/tmp/verjson-secretless-workspace"
    writable_bind_index = next(
        index
        for index, value in enumerate(arguments[:-2])
        if value == "--bind"
        and arguments[index + 2] == "/tmp/verjson-secretless-workspace/node_modules"
    )
    assert arguments[writable_bind_index + 1].startswith("/proc/self/fd/")
    assert Path(receipt["resolved_sources"]["/tmp/verjson-secretless-workspace/node_modules"]) == workspace / "node_modules"
    assert arguments[writable_bind_index + 2] == "/tmp/verjson-secretless-workspace/node_modules"
    assert not any(
        arguments[index:index + 3] == ["--ro-bind", "/", "/"]
        for index in range(len(arguments) - 2)
    ), "host root must not be exposed to untrusted lifecycle code"
    toolchain_source = Path(receipt["resolved_sources"]["/opt/verjson-node-toolchain"])
    assert toolchain_source.parts[-3:] == ("node", "test", "x64")
    if package_manager == "pnpm":
        assert Path(receipt["resolved_sources"]["/tmp/corepack"]) == workspace.parent / ".cache/node/corepack"
    else:
        assert "/tmp/corepack" not in receipt["resolved_sources"]


def main():
    invalid_values = (
        {"NODE_AUTH_TOKEN": "secret"},
        {"API_KEY": "secret"},
        {"PRIVATE_KEY": "secret"},
        {"npm_config_userconfig": "/tmp/untrusted.npmrc"},
        {"LD_AUDIT": "/tmp/untrusted.so"},
        {"GIT_CONFIG_COUNT": "1"},
        {"PATH": "/tmp/untrusted-bin"},
        {"ONNXRUNTIME_NODE_INSTALL": "skip-secret"},
        {"ONNXRUNTIME_NODE_INSTALL": "skip\n"},
        {"ONNXRUNTIME_NODE_INSTALL": 1},
        {"ONNXRUNTIME_NODE_INSTALL": "skip", "SAFE_FLAG": "true"},
        "[]",
        "{",
        '{"ONNXRUNTIME_NODE_INSTALL":"' + "x" * 4100 + '"}',
    )

    for workflow_path in (WORKFLOW, PROTECTED_WORKFLOW):
        doc = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        inputs = doc[True]["workflow_call"]["inputs"]
        build = doc["jobs"]["build-test"]
        step = next(
            step
            for step in build["steps"]
            if step.get("name") == "Rebuild exact approved lifecycle packages without credentials"
        )
        assert inputs["secretless-rebuild-env"]["default"] == "{}"
        assert step["env"]["REBUILD_ENV"] == "${{ inputs.secretless-rebuild-env }}"
        assert step["env"]["BWRAP_BINARY"] == "/usr/bin/bwrap"
        install = next(candidate for candidate in build["steps"] if candidate.get("name") == "Install from verified secretless npm cache")
        cleanup = next(candidate for candidate in build["steps"] if candidate.get("name") == "Remove local secretless transfer state")
        provision = next(
            candidate
            for candidate in build["steps"]
            if candidate.get("name") == "Provision trusted compatibility sandbox"
        )
        assert build["steps"].index(provision) < build["steps"].index(step)
        assert build["steps"].index(install) < build["steps"].index(cleanup) < build["steps"].index(step)
        assert 'rm -rf "$SECRETLESS_CACHE_DIR" "$PNPM_STORE_DIR"' in install["run"]
        assert "PNPM_STORE_DIR" in cleanup["env"]
        assert "inputs.secretless-rebuild-packages != ''" in provision["if"]
        assert "runner.environment == 'github-hosted'" in provision["if"]
        assert provision["shell"] == "bash"
        assert 'if [ "${RUNNER_OS:-}" != "Linux" ]; then' in provision["run"]
        unsupported = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", provision["run"]],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "RUNNER_OS": "Windows"},
        )
        assert unsupported.returncode != 0
        assert "requires a Linux GitHub-hosted runner" in unsupported.stderr
        assert "--unshare-pid" in step["run"]
        assert '"--unshare-net"' in step["run"]
        assert '"--tmpfs", "/"' in step["run"]
        assert '"--ro-bind", "/", "/"' not in step["run"]
        assert '"GITHUB_WORKSPACE": sandbox_workspace' in step["run"]
        assert '"RUNNER_TEMP": "/tmp"' in step["run"]
        assert "bind_source(workspace, sandbox_workspace)" in step["run"]
        assert 'bind_source(node_modules, f"{sandbox_workspace}/node_modules", writable=True)' in step["run"]
        assert '"PATH": f"{toolchain_bin}:/usr/bin:/bin"' in step["run"]
        assert '"GITHUB_ENV"' not in step["run"]
        if workflow_path == PROTECTED_WORKFLOW:
            steps = build["steps"]
            script_names = {
                "Run exact credentialless consumer script plan",
                "Run default build, typecheck, test, and lint plan",
            }
            script_indexes = [
                index for index, candidate in enumerate(steps)
                if candidate.get("name") in script_names
            ]
            token_indexes = [
                index for index, candidate in enumerate(steps)
                if candidate.get("name") == "Revalidate protected pull-request identity"
                and candidate.get("env", {}).get("GH_TOKEN") == "${{ github.token }}"
            ]
            assert script_indexes and token_indexes
            assert max(token_indexes) < min(script_indexes)
            compatibility_condition = (
                "needs.eligibility.outputs.should-run != 'false' && "
                "(inputs.secretless-pr || inputs.secretless-trusted-ref) && "
                "(inputs.protected-type-surface-declaration-path != '' || "
                "inputs.secretless-compatibility-ranges != '')"
            )
            compatibility_verifier = next(
                index for index, candidate in enumerate(steps)
                if candidate.get("name") == "Revalidate protected pull-request identity"
                and candidate.get("if") == compatibility_condition
            )
            assert compatibility_verifier < min(script_indexes)

        for package_manager in ("npm", "pnpm"):
            with tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
                fixture = Path(temporary)
                result, capture, command_files, marker, arguments_path = run_rebuild(
                    step, fixture, {}, package_manager=package_manager
                )
                assert result.returncode == 0, result.stderr
                assert_rebuild_capture(capture, package_manager, "<unset>")
                assert_bubblewrap_arguments(arguments_path, fixture / "runner-home/workspace", package_manager)
                assert_no_lifecycle_command_file_access(command_files, marker)

                result, capture, command_files, marker, arguments_path = run_rebuild(
                    step,
                    fixture,
                    {"ONNXRUNTIME_NODE_INSTALL": "skip"},
                    package_manager=package_manager,
                )
                assert result.returncode == 0, result.stderr
                assert_rebuild_capture(capture, package_manager, "skip")
                assert_bubblewrap_arguments(arguments_path, fixture / "runner-home/workspace", package_manager)
                assert_no_lifecycle_command_file_access(command_files, marker)

                for invalid in invalid_values:
                    result, capture, command_files, marker, _ = run_rebuild(
                        step, fixture, invalid, package_manager=package_manager
                    )
                    assert result.returncode != 0, f"accepted unsafe rebuild environment: {invalid!r}"
                    assert not capture.exists(), "package manager ran after invalid rebuild environment was rejected"
                    assert_no_lifecycle_command_file_access(command_files, marker)

                result, capture, command_files, marker, _ = run_rebuild(
                    step,
                    fixture,
                    {"ONNXRUNTIME_NODE_INSTALL": "skip"},
                    rebuild_packages="some-other-package",
                    package_manager=package_manager,
                )
                assert result.returncode != 0
                assert "requires onnxruntime-node" in result.stderr
                assert not capture.exists()
                assert_no_lifecycle_command_file_access(command_files, marker)

                result, capture, command_files, marker, _ = run_rebuild(
                    step,
                    fixture,
                    {"ONNXRUNTIME_NODE_INSTALL": "skip"},
                    package_manager=package_manager,
                    runner_environment="self-hosted",
                )
                assert result.returncode != 0
                assert "requires a GitHub-hosted runner" in result.stderr
                assert not capture.exists()
                assert_no_lifecycle_command_file_access(command_files, marker)

        if workflow_path == PROTECTED_WORKFLOW:
            with tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
                fixture = Path(temporary)
                result, capture, _, _, _ = run_rebuild(
                    step,
                    fixture,
                    {"ONNXRUNTIME_NODE_INSTALL": "skip"},
                    package_manager="pnpm",
                    corepack_home_override=str(fixture),
                )
                assert result.returncode != 0
                assert "trusted Corepack cache is unavailable" in result.stderr
                assert not capture.exists()

    if os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1":
        if not Path("/usr/bin/bwrap").is_file():
            raise AssertionError("real bubblewrap integration requested but /usr/bin/bwrap is unavailable")
        for workflow_path in (WORKFLOW, PROTECTED_WORKFLOW):
            doc = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
            step = next(
                step
                for step in doc["jobs"]["build-test"]["steps"]
                if step.get("name") == "Rebuild exact approved lifecycle packages without credentials"
            )
            for package_manager in ("npm", "pnpm"):
                with tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
                    fixture = Path(temporary)
                    result, capture, command_files, marker, _ = run_rebuild(
                        step,
                        fixture,
                        {"ONNXRUNTIME_NODE_INSTALL": "skip"},
                        package_manager=package_manager,
                        real_bwrap=True,
                        probe_hidden_paths=True,
                    )
                    assert result.returncode == 0, result.stderr
                    assert_rebuild_capture(capture, package_manager, "skip")
                    assert_no_lifecycle_command_file_access(command_files, marker)

    print("secretless rebuild environment contract passed")


if __name__ == "__main__":
    main()
