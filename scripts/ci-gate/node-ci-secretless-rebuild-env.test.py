#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/node-ci.yml"
PROTECTED_WORKFLOW = ROOT / ".github/workflows/node-ci-protected.yml"


def run_rebuild(step, fixture, rebuild_env, rebuild_packages="onnxruntime-node"):
    stub = fixture / "bin"
    stub.mkdir(exist_ok=True)
    npm = stub / "npm"
    npm.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"${ONNXRUNTIME_NODE_INSTALL-<unset>}\" > \"$CAPTURE\"\n"
        "printf '%s\\n' \"$*\" >> \"$CAPTURE\"\n"
        "printf '%s\\n' \"${REBUILD_ENV-<unset>}\" >> \"$CAPTURE\"\n"
        "printf '%s\\n' \"${NODE_AUTH_TOKEN-<unset>}\" >> \"$CAPTURE\"\n",
        encoding="utf-8",
    )
    npm.chmod(0o755)
    lock = {
        "packages": {
            "node_modules/onnxruntime-node": {
                "name": "onnxruntime-node",
                "hasInstallScript": True,
            }
        }
    }
    (fixture / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    (fixture / "node_modules/onnxruntime-node").mkdir(parents=True, exist_ok=True)
    capture = fixture / "capture"
    env = {
        **os.environ,
        "PATH": f"{stub}:{os.environ['PATH']}",
        "CAPTURE": str(capture),
        "PACKAGE_MANAGER": "npm",
        "REBUILD_PACKAGES": rebuild_packages,
        "REBUILD_ENV": rebuild_env if isinstance(rebuild_env, str) else json.dumps(rebuild_env),
        "NODE_AUTH_TOKEN": "fixture-secret-token",
        "ONNXRUNTIME_NODE_INSTALL": "ambient-value-must-not-enable-skip",
    }
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=fixture,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, capture


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

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            result, capture = run_rebuild(step, fixture, {})
            assert result.returncode == 0, result.stderr
            assert capture.read_text(encoding="utf-8").splitlines() == [
                "<unset>",
                "rebuild onnxruntime-node",
                "<unset>",
                "<unset>",
            ]

            capture.unlink()
            result, capture = run_rebuild(step, fixture, {"ONNXRUNTIME_NODE_INSTALL": "skip"})
            assert result.returncode == 0, result.stderr
            assert capture.read_text(encoding="utf-8").splitlines() == [
                "skip",
                "rebuild onnxruntime-node",
                "<unset>",
                "<unset>",
            ]

            for invalid in invalid_values:
                capture.unlink(missing_ok=True)
                result, capture = run_rebuild(step, fixture, invalid)
                assert result.returncode != 0, f"accepted unsafe rebuild environment: {invalid!r}"
                assert not capture.exists(), "npm ran after invalid rebuild environment was rejected"

            capture.unlink(missing_ok=True)
            result, capture = run_rebuild(
                step,
                fixture,
                {"ONNXRUNTIME_NODE_INSTALL": "skip"},
                rebuild_packages="some-other-package",
            )
            assert result.returncode != 0
            assert "requires onnxruntime-node" in result.stderr
            assert not capture.exists()

    print("secretless rebuild environment contract passed")


if __name__ == "__main__":
    main()
