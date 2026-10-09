#!/usr/bin/env python3
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import yaml


def run(command, *, cwd, env):
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout


def write_package(directory, manifest, recorder):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(json.dumps(manifest))
    (directory / "record-lifecycle.cjs").write_text(recorder)


def main():
    repo_root = Path(__file__).resolve().parent.parent
    workflow = (repo_root / ".github/workflows/node-release.yml").read_text()
    workflow_document = yaml.safe_load(workflow)
    prepare_job = workflow_document["jobs"]["prepare"]
    assert prepare_job.get("env") == {"NODE_AUTH_TOKEN": ""}, (
        "the preparation job must mask runner-inherited package credentials"
    )
    assert prepare_job["runs-on"] == "ubuntu-24.04", (
        "preparation must use a fresh GitHub-hosted runner"
    )
    runner_guard_names = {
        "prepare": "Require a fresh GitHub-hosted preparation runner",
        "release": "Require a fresh GitHub-hosted publication runner",
        "retention": "Require a fresh GitHub-hosted retention runner",
    }
    runner_guard_commands = {}
    for job_name, expected_name in runner_guard_names.items():
        runner_guard = workflow_document["jobs"][job_name]["steps"][0]
        assert runner_guard.get("name") == expected_name
        assert runner_guard.get("env") == {"RUNNER_ENVIRONMENT": "${{ runner.environment }}"}
        runner_guard_command = runner_guard.get("run")
        assert isinstance(runner_guard_command, str)
        assert '"$RUNNER_ENVIRONMENT" != "github-hosted"' in runner_guard_command
        runner_guard_commands[job_name] = runner_guard_command
    install_step = re.search(
        r"(?ms)^      - name: Install dependencies\n(?P<step>.*?)(?=^      - name: |\Z)",
        workflow,
    )
    lifecycle_step = re.search(
        r"(?ms)^      - name: Run dependency lifecycle scripts without credentials\n(?P<step>.*?)(?=^      - name: |\Z)",
        workflow,
    )
    assert install_step, "node-release workflow is missing its dependency acquisition step"
    assert lifecycle_step, "node-release workflow is missing its tokenless lifecycle step"
    assert "run: npm ci --ignore-scripts" in install_step["step"]
    assert "NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}" in install_step["step"]
    assert "run: npm ci --prefer-offline" in lifecycle_step["step"]
    assert "NODE_AUTH_TOKEN" not in lifecycle_step["step"]

    lifecycle_recorder = (
        "const fs = require('node:fs');\n"
        "const token = process.env.NODE_AUTH_TOKEN || '<unset>';\n"
        "const tokenEnvKeys = Object.entries(process.env)\n"
        "  .filter(([, value]) => typeof value === 'string' && value.includes('synthetic-package-token'))\n"
        "  .map(([key]) => key);\n"
        "const tokenAncestorPids = [];\n"
        "let parentPid = process.ppid;\n"
        "while (parentPid > 1) {\n"
        "  let parentEnvironment;\n"
        "  let parentStatus;\n"
        "  try {\n"
        "    parentEnvironment = fs.readFileSync(`/proc/${parentPid}/environ`, 'utf8').split('\\0');\n"
        "    parentStatus = fs.readFileSync(`/proc/${parentPid}/status`, 'utf8');\n"
        "  } catch { break; }\n"
        "  if (parentEnvironment.includes('NODE_AUTH_TOKEN=synthetic-package-token')) tokenAncestorPids.push(parentPid);\n"
        "  const parentPidMatch = parentStatus.match(/^PPid:\\s*(\\d+)/m);\n"
        "  if (!parentPidMatch) break;\n"
        "  parentPid = Number(parentPidMatch[1]);\n"
        "}\n"
        "fs.appendFileSync(process.env.LIFECYCLE_LOG, "
        "`${process.env.npm_package_name}:${process.argv[2]}:${token}:${JSON.stringify(tokenEnvKeys)}:${JSON.stringify(tokenAncestorPids)}\\n`);\n"
    )
    root_scripts = {
        hook: f"node record-lifecycle.cjs root-{hook}"
        for hook in (
            "preinstall",
            "install",
            "postinstall",
            "prepublish",
            "preprepare",
            "prepare",
            "postprepare",
            "dependencies",
        )
    }
    dependency_scripts = {
        hook: f"node record-lifecycle.cjs dependency-{hook}"
        for hook in ("preinstall", "install", "postinstall", "prepare")
    }

    with tempfile.TemporaryDirectory(prefix="node-release-credential-isolation-") as temp:
        root = Path(temp)
        for job_name, runner_guard_command in runner_guard_commands.items():
            hosted_runner_env = os.environ.copy()
            hosted_runner_env["RUNNER_ENVIRONMENT"] = "github-hosted"
            hosted_runner = subprocess.run(
                ["bash", "-c", runner_guard_command],
                cwd=root,
                env=hosted_runner_env,
                text=True,
                capture_output=True,
            )
            assert hosted_runner.returncode == 0, f"{job_name}: {hosted_runner.stderr}"
            self_hosted_env = os.environ.copy()
            self_hosted_env["RUNNER_ENVIRONMENT"] = "self-hosted"
            self_hosted = subprocess.run(
                ["bash", "-c", runner_guard_command],
                cwd=root,
                env=self_hosted_env,
                text=True,
                capture_output=True,
            )
            assert self_hosted.returncode != 0, f"{job_name}: runner guard accepted self-hosted"
            assert "requires a fresh GitHub-hosted runner" in self_hosted.stdout

        write_package(
            root / "fixture-transitive-dependency",
            {
                "name": "fixture-transitive-dependency",
                "version": "1.0.0",
                "scripts": dependency_scripts,
            },
            lifecycle_recorder,
        )
        write_package(
            root / "fixture-dependency",
            {
                "name": "fixture-dependency",
                "version": "1.0.0",
                "scripts": dependency_scripts,
                "dependencies": {
                    "fixture-transitive-dependency": "file:../fixture-transitive-dependency"
                },
            },
            lifecycle_recorder,
        )
        write_package(
            root,
            {
                "name": "credential-isolation-fixture",
                "version": "1.0.0",
                "allowScripts": {
                    "file:./fixture-dependency": True,
                    "file:../fixture-transitive-dependency": True,
                },
                "scripts": root_scripts,
                "dependencies": {"fixture-dependency": "file:./fixture-dependency"},
            },
            lifecycle_recorder,
        )
        (root / ".npmrc").write_text(
            "//registry.npmjs.org/:_authToken=${NODE_AUTH_TOKEN}\n"
        )

        env = os.environ.copy()
        lifecycle_log = root / "lifecycle.log"
        env.update(
            {
                "NODE_AUTH_TOKEN": "synthetic-package-token",
                "LIFECYCLE_LOG": str(lifecycle_log),
                "npm_config_cache": str(root / "npm-cache"),
                "npm_config_offline": "true",
                "npm_config_audit": "false",
                "npm_config_fund": "false",
                "NPM_CONFIG_USERCONFIG": str(root / "user.npmrc"),
                "NPM_CONFIG_GLOBALCONFIG": str(root / "global.npmrc"),
            }
        )
        (root / "user.npmrc").write_text("")
        (root / "global.npmrc").write_text("")
        npm_major = int(run(["npm", "--version"], cwd=root, env=env).split(".", 1)[0])

        run(
            [
                "npm",
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--offline",
                "--no-audit",
                "--no-fund",
            ],
            cwd=root,
            env=env,
        )
        real_npm = shutil.which("npm")
        assert real_npm, "npm must be available for the credential-isolation fixture"
        wrapper_dir = root / "npm-wrapper"
        wrapper_dir.mkdir()
        npm_wrapper = wrapper_dir / "npm"
        npm_wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ -n "${NODE_AUTH_TOKEN-}" ]]; then credential=present; else credential=absent; fi\n'
            "printf '%s|%s\\n' \"$*\" \"$credential\" >> \"$NPM_TRACE\"\n"
            "exec \"$REAL_NPM\" \"$@\"\n"
        )
        npm_wrapper.chmod(0o755)
        npm_trace = root / "npm-commands.log"
        helper_env = env | {
            "PATH": f"{wrapper_dir}{os.pathsep}{env['PATH']}",
            "REAL_NPM": real_npm,
            "NPM_TRACE": str(npm_trace),
        }
        run(["npm", "ci", "--ignore-scripts"], cwd=root, env=helper_env)
        lifecycle_env = helper_env.copy()
        lifecycle_env["NODE_AUTH_TOKEN"] = ""
        run(["npm", "ci", "--prefer-offline"], cwd=root, env=lifecycle_env)
        assert npm_trace.read_text().splitlines() == [
            "ci --ignore-scripts|present",
            "ci --prefer-offline|absent",
        ]

        records = lifecycle_log.read_text().splitlines()
        assert all(record.endswith(":<unset>:[]:[]") for record in records), records

        baseline_root = root / "npm-ci-baseline"
        for package_name in ("fixture-transitive-dependency", "fixture-dependency"):
            package_dir = root / package_name
            write_package(
                baseline_root / package_name,
                json.loads((package_dir / "package.json").read_text()),
                lifecycle_recorder,
            )
        write_package(
            baseline_root,
            json.loads((root / "package.json").read_text()),
            lifecycle_recorder,
        )
        (baseline_root / ".npmrc").write_text(
            "//registry.npmjs.org/:_authToken=${NODE_AUTH_TOKEN}\n"
        )
        baseline_env = env | {
            "LIFECYCLE_LOG": str(root / "baseline-lifecycle.log"),
            "npm_config_cache": str(root / "baseline-npm-cache"),
        }
        run(
            [
                "npm",
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--offline",
                "--no-audit",
                "--no-fund",
            ],
            cwd=baseline_root,
            env=baseline_env,
        )
        run(
            ["npm", "ci", "--offline", "--no-audit", "--no-fund"],
            cwd=baseline_root,
            env=baseline_env,
        )
        helper_hooks = Counter(":".join(record.split(":")[:2]) for record in records)
        baseline_records = (root / "baseline-lifecycle.log").read_text().splitlines()
        baseline_hooks = Counter(":".join(record.split(":")[:2]) for record in baseline_records)
        required_dependency_hooks = {
            f"{package_name}:dependency-{hook}"
            for package_name in ("fixture-dependency", "fixture-transitive-dependency")
            for hook in ("preinstall", "install", "postinstall", "prepare")
        }
        assert required_dependency_hooks <= baseline_hooks.keys(), (
            f"baseline did not run explicitly approved dependency hooks: {required_dependency_hooks - baseline_hooks.keys()}"
        )
        assert helper_hooks == baseline_hooks, (
            f"credentialless install changed npm ci lifecycle hooks: {helper_hooks!r} != {baseline_hooks!r}"
        )
        def hooks_by_package(hook_records):
            hooks = {}
            for record in hook_records:
                package_name, hook = record.split(":", 2)[:2]
                hooks.setdefault(package_name, []).append(hook)
            return hooks

        assert hooks_by_package(records) == hooks_by_package(baseline_records), (
            "credentialless install changed lifecycle order within a package"
        )
        assert any(":synthetic-package-token:" in record for record in baseline_records), (
            "baseline fixture must demonstrate the credential reaches npm ci lifecycle hooks"
        )
        assert any(
            json.loads(record.rsplit(":", 1)[-1]) for record in baseline_records
        ), "baseline fixture must demonstrate lifecycle code can read an ancestor environment"

        empty_root = root / "empty-dependency-fixture"
        write_package(
            empty_root,
            {
                "name": "empty-dependency-fixture",
                "version": "1.0.0",
                "scripts": {
                    "dependencies": "node record-lifecycle.cjs empty-dependencies",
                    "postinstall": "node record-lifecycle.cjs empty-postinstall",
                },
            },
            lifecycle_recorder,
        )
        (empty_root / ".npmrc").write_text(
            "//registry.npmjs.org/:_authToken=${NODE_AUTH_TOKEN}\n"
        )
        empty_env = env | {"LIFECYCLE_LOG": str(root / "empty-lifecycle.log")}
        run(
            [
                "npm",
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--offline",
                "--no-audit",
                "--no-fund",
            ],
            cwd=empty_root,
            env=empty_env,
        )
        empty_acquisition_env = empty_env | {
            "PATH": f"{wrapper_dir}{os.pathsep}{empty_env['PATH']}",
            "REAL_NPM": real_npm,
            "NPM_TRACE": str(npm_trace),
        }
        run(["npm", "ci", "--ignore-scripts"], cwd=empty_root, env=empty_acquisition_env)
        empty_lifecycle_env = empty_acquisition_env.copy()
        empty_lifecycle_env.pop("NODE_AUTH_TOKEN")
        run(
            ["npm", "ci", "--prefer-offline"],
            cwd=empty_root,
            env=empty_lifecycle_env,
        )
        empty_records = (root / "empty-lifecycle.log").read_text().splitlines()
        assert empty_records == [
            "empty-dependency-fixture:empty-postinstall:<unset>:[]:[]"
        ], empty_records

if __name__ == "__main__":
    main()
