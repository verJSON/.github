#!/usr/bin/env python3
"""Execute the generated release verification step with a hook that prints its
environment, proving the hook receives the package retention window.

A repository verification hook that must protect a still-consumed version from
the post-publish retention job (verjson-ci#298) has to see the same window the
retention job deletes against. The generated step runs the hook under
`env -i`, so the window reaches it only if the generator forwards it.
"""

from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
VERIFY_STEP = "Run the release verification suite"
RETENTION_KEY = "CI_PACKAGE_RETENTION_KEEP"
MODES = {
    "release-node": [],
    "release-artifact": ["--build-runner", "ubuntu-24.04"],
    "release-snapshot": [],
}
VARS_EXPRESSION = re.compile(
    r"^\$\{\{\s*vars\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\|\|\s*'(?P<default>[^']*)'\s*\}\}$"
)


def generate(mode, contract_sha):
    return yaml.safe_load(
        subprocess.check_output(
            ["bash", "scripts/gen-changelog-caller.sh", mode, contract_sha, *MODES[mode]],
            cwd=ROOT,
            text=True,
        )
    )


def verify_step(workflow):
    matches = [
        step for step in workflow["jobs"]["verify"]["steps"] if step.get("name") == VERIFY_STEP
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {VERIFY_STEP!r} step, found {len(matches)}")
    return matches[0]


def evaluate_step_value(value, variables):
    """Evaluate the one expression shape this test admits, as Actions would.

    `vars.NAME` is the empty string when the variable is unset, and the empty
    string is the only falsy string, so `||` falls back only then. Any other
    expression is refused rather than guessed at.
    """
    if value is None:
        return None
    text = str(value)
    if "${{" not in text:
        return text
    match = VARS_EXPRESSION.match(text)
    if match is None:
        raise AssertionError(f"unsupported expression in the verify step env: {text!r}")
    return variables.get(match.group("name"), "") or match.group("default")


def run_verify_step(step, variables):
    """Run the step's script with an environment-printing hook; return (result, hook_env)."""
    with tempfile.TemporaryDirectory(prefix="release-verify-retention-") as temporary:
        root = Path(temporary)
        scripts = root / "scripts"
        scripts.mkdir()
        # The hook IS /usr/bin/env, so its output is exactly what `env -i`
        # handed it; a shell hook would add its own PWD and blur the boundary.
        (scripts / "release-verify.sh").symlink_to("/usr/bin/env")
        runner_temp = root / "runner-temp"
        runner_temp.mkdir()
        environment = {
            **os.environ,
            "PACKAGE_VERSION": "1.0.0",
            "RELEASE_VERIFICATION_PATH": "/usr/bin:/bin",
            "VERJSON_CHANGELOG_TOOL_CACHE": str(runner_temp / "verjson-changelog-tools"),
            "GITHUB_ACTIONS": "true",
            "GITHUB_WORKFLOW": "release",
            "GITHUB_JOB": "verify",
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_NUMBER": "1",
            "GITHUB_REPOSITORY": "verJSON/fixture",
            "GITHUB_REPOSITORY_OWNER": "verJSON",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_NAME": "main",
            "GITHUB_REF_TYPE": "branch",
            "GITHUB_SHA": "0" * 40,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_EVENT_PATH": str(root / "event.json"),
            "GITHUB_WORKSPACE": str(root),
            "GITHUB_STEP_SUMMARY": str(root / "summary.md"),
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
            "RUNNER_TEMP": str(runner_temp),
            "RUNNER_TOOL_CACHE": str(root / "tool-cache"),
        }
        environment.pop(RETENTION_KEY, None)
        retention = evaluate_step_value((step.get("env") or {}).get(RETENTION_KEY), variables)
        if retention is not None:
            environment[RETENTION_KEY] = retention
        result = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    marker = "Running this repository's scripts/release-verify.sh"
    lines = result.stdout.splitlines()
    hook_env = None
    if marker in lines:
        hook_env = dict(
            line.split("=", 1) for line in lines[lines.index(marker) + 1:] if "=" in line
        )
    return result, hook_env


class ReleaseVerifyRetentionKeepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        contract_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        cls.steps = {mode: verify_step(generate(mode, contract_sha)) for mode in MODES}

    def test_hook_receives_the_default_retention_window_when_the_org_variable_is_unset(self):
        for mode, step in self.steps.items():
            with self.subTest(mode=mode):
                result, hook_env = run_verify_step(step, variables={})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIsNotNone(hook_env, "the verification hook never ran")
                self.assertEqual(hook_env.get(RETENTION_KEY), "3")

    def test_hook_receives_the_org_retention_window_when_the_variable_is_set(self):
        for mode, step in self.steps.items():
            with self.subTest(mode=mode):
                result, hook_env = run_verify_step(step, variables={RETENTION_KEY: "7"})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIsNotNone(hook_env, "the verification hook never ran")
                self.assertEqual(hook_env.get(RETENTION_KEY), "7")


if __name__ == "__main__":
    unittest.main()
