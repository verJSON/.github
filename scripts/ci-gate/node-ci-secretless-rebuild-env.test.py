#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shlex
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
):
    stub = fixture / "bin"
    stub.mkdir(exist_ok=True)
    executable = "npm" if package_manager == "npm" else "corepack"
    package_manager_stub = stub / executable
    package_manager_stub.write_text(
        f"#!{sys.executable}\n"
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "import re\n"
        "import sys\n"
        "\n"
        f"sensitive = {SENSITIVE_ENV!r}\n"
        "visible = set()\n"
        "attack_targets = set()\n"
        "pid = os.getpid()\n"
        "seen = set()\n"
        "test_harness_pid = int(os.environ['TEST_HARNESS_PID'])\n"
        "while pid > 1 and pid != test_harness_pid and pid not in seen:\n"
        "    seen.add(pid)\n"
        "    try:\n"
        "        entries = Path(f'/proc/{pid}/environ').read_bytes().split(b'\\0')\n"
        "    except (FileNotFoundError, PermissionError, ProcessLookupError):\n"
        "        entries = []\n"
        "    for entry in entries:\n"
        "        name, separator, value = entry.partition(b'=')\n"
        "        name = name.decode(errors='replace')\n"
        "        if separator and name in sensitive:\n"
        "            visible.add(f'{pid}:{name}')\n"
        "            if name == 'GITHUB_ENV':\n"
        "                attack_targets.add(value.decode(errors='replace'))\n"
        "    try:\n"
        "        status = Path(f'/proc/{pid}/status').read_text(encoding='utf-8')\n"
        "        pid = int(re.search(r'^PPid:\\s+(\\d+)$', status, re.MULTILINE).group(1))\n"
        "    except (FileNotFoundError, PermissionError, ProcessLookupError, AttributeError, ValueError):\n"
        "        break\n"
        "for target in attack_targets:\n"
        "    with Path(target).open('a', encoding='utf-8') as command_file:\n"
        "        command_file.write(f\"BASH_ENV={os.environ['ATTACK_BASH_ENV']}\\n\")\n"
        "Path(os.environ['CAPTURE']).write_text(json.dumps({\n"
        "    'onnxruntime_node_install': os.environ.get('ONNXRUNTIME_NODE_INSTALL', '<unset>'),\n"
        "    'argv': [Path(sys.argv[0]).name, *sys.argv[1:]],\n"
        "    'rebuild_env': os.environ.get('REBUILD_ENV', '<unset>'),\n"
        "    'node_auth_token': os.environ.get('NODE_AUTH_TOKEN', '<unset>'),\n"
        "    'gh_token': os.environ.get('GH_TOKEN', '<unset>'),\n"
        "    'github_token': os.environ.get('GITHUB_TOKEN', '<unset>'),\n"
        "    'visible_sensitive_ancestors': sorted(visible),\n"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    package_manager_stub.chmod(0o755)
    if package_manager == "npm":
        lock = {
            "packages": {
                "node_modules/onnxruntime-node": {
                    "name": "onnxruntime-node",
                    "hasInstallScript": True,
                }
            }
        }
        (fixture / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    else:
        lock = {
            "lockfileVersion": "9.0",
            "packages": {"onnxruntime-node@1.0.0": {"requiresBuild": True}},
            "snapshots": {"onnxruntime-node@1.0.0": {}},
        }
        (fixture / "pnpm-lock.yaml").write_text(yaml.safe_dump(lock), encoding="utf-8")
    (fixture / "node_modules/onnxruntime-node").mkdir(parents=True, exist_ok=True)
    capture = fixture / "capture"
    malicious_bash_env = fixture / "malicious-bash-env"
    marker = fixture / "later-step-token-exposed"
    malicious_bash_env.write_text(
        f"printf '%s' \"${{GH_TOKEN-}}\" > {shlex.quote(str(marker))}\n",
        encoding="utf-8",
    )
    command_files = {name: fixture / name.lower() for name in COMMAND_FILE_ENV}
    for command_file in command_files.values():
        command_file.write_text("ORIGINAL=preserved\n", encoding="utf-8")
    capture.unlink(missing_ok=True)
    env = {
        **{name: value for name, value in os.environ.items() if name not in SENSITIVE_ENV + ("BASH_ENV",)},
        "PATH": f"{stub}:{os.environ['PATH']}",
        "CAPTURE": str(capture),
        "ATTACK_BASH_ENV": str(malicious_bash_env),
        # The test runner's command files do not belong to the simulated rebuild step.
        "TEST_HARNESS_PID": str(os.getpid()),
        "PACKAGE_MANAGER": package_manager,
        "REBUILD_PACKAGES": rebuild_packages,
        "REBUILD_ENV": rebuild_env if isinstance(rebuild_env, str) else json.dumps(rebuild_env),
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
        cwd=fixture,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, capture, command_files, marker


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
        "node_auth_token": "<unset>",
        "gh_token": "<unset>",
        "github_token": "<unset>",
        "visible_sensitive_ancestors": [],
    }
    assert observed == expected, f"unexpected package-manager execution environment: {observed!r}"


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

        for package_manager in ("npm", "pnpm"):
            with tempfile.TemporaryDirectory() as temporary:
                fixture = Path(temporary)
                result, capture, command_files, marker = run_rebuild(
                    step, fixture, {}, package_manager=package_manager
                )
                assert result.returncode == 0, result.stderr
                assert_rebuild_capture(capture, package_manager, "<unset>")
                assert_no_lifecycle_command_file_access(command_files, marker)

                result, capture, command_files, marker = run_rebuild(
                    step,
                    fixture,
                    {"ONNXRUNTIME_NODE_INSTALL": "skip"},
                    package_manager=package_manager,
                )
                assert result.returncode == 0, result.stderr
                assert_rebuild_capture(capture, package_manager, "skip")
                assert_no_lifecycle_command_file_access(command_files, marker)

                for invalid in invalid_values:
                    result, capture, command_files, marker = run_rebuild(
                        step, fixture, invalid, package_manager=package_manager
                    )
                    assert result.returncode != 0, f"accepted unsafe rebuild environment: {invalid!r}"
                    assert not capture.exists(), "package manager ran after invalid rebuild environment was rejected"
                    assert_no_lifecycle_command_file_access(command_files, marker)

                result, capture, command_files, marker = run_rebuild(
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

    print("secretless rebuild environment contract passed")


if __name__ == "__main__":
    main()
