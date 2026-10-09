#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/node-ci.yml"


def run_rebuild(step, fixture, rebuild_env):
    stub = fixture / "bin"
    stub.mkdir(exist_ok=True)
    npm = stub / "npm"
    npm.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$ONNXRUNTIME_NODE_INSTALL\" > \"$CAPTURE\"\n"
        "printf '%s\\n' \"$*\" >> \"$CAPTURE\"\n",
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
        "REBUILD_PACKAGES": "onnxruntime-node",
        "REBUILD_ENV": json.dumps(rebuild_env),
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
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
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
        result, capture = run_rebuild(step, fixture, {"ONNXRUNTIME_NODE_INSTALL": "skip"})
        assert result.returncode == 0, result.stderr
        assert capture.read_text(encoding="utf-8").splitlines() == [
            "skip",
            "rebuild onnxruntime-node",
        ]

        for protected in (
            {"NODE_AUTH_TOKEN": "secret"},
            {"npm_config_userconfig": "/tmp/untrusted.npmrc"},
            {"PATH": "/tmp/untrusted-bin"},
        ):
            capture.unlink(missing_ok=True)
            result, capture = run_rebuild(step, fixture, protected)
            assert result.returncode != 0
            assert not capture.exists()

    print("secretless rebuild environment contract passed")


if __name__ == "__main__":
    main()
