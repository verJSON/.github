#!/usr/bin/env python3
"""Execute the generated release verification step with a hook that prints its
environment, proving the hook receives the package retention window.

A repository verification hook that must protect a still-consumed version from
the post-publish retention job (verjson-ci#298) has to see the same window the
retention job deletes against. The generated step runs the hook under
`env -i`, so the window reaches it only if the generator forwards it.
"""

from copy import deepcopy
import importlib.util
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
_LOCALES_SPEC = importlib.util.spec_from_file_location(
    "ascii_digit_locales", Path(__file__).with_name("ascii_digit_locales.py")
)
ascii_digit_locales = importlib.util.module_from_spec(_LOCALES_SPEC)
_LOCALES_SPEC.loader.exec_module(ascii_digit_locales)
# The one guard every retention-window site applies. Digits are enumerated
# because a [1-9] range is collation-ordered outside the C locale, and capped
# at three so no consumer's integer arithmetic can wrap (2**64 is 0 in bash).
RETENTION_GUARD = "^[123456789][0123456789]{0,2}$"
NON_ASCII_DIGITS = ("\u0663", "\uff13", "1\u0663", "\u0661\u0660")
VERIFY_STEP = "Run the release verification suite"
RETENTION_KEY = "CI_PACKAGE_RETENTION_KEEP"
MODES = {
    "release-node": [],
    "release-artifact": ["--build-runner", "ubuntu-24.04"],
    "release-snapshot": [],
}
# The `env -i` allowlist each mode's hook received before this key was admitted.
# Only release-node also forwards RELEASE_VERIFICATION_PATH by name; the other
# two modes use it solely as PATH.
_SHARED_PRIOR_HOOK_ENVIRONMENT = frozenset({
    "PATH", "HOME", "CI",
    "GITHUB_ACTIONS", "GITHUB_WORKFLOW", "GITHUB_JOB", "GITHUB_RUN_ID",
    "GITHUB_RUN_NUMBER", "GITHUB_REPOSITORY", "GITHUB_REPOSITORY_OWNER",
    "GITHUB_REF", "GITHUB_REF_NAME", "GITHUB_REF_TYPE", "GITHUB_SHA",
    "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH", "GITHUB_WORKSPACE", "GITHUB_STEP_SUMMARY",
    "RUNNER_OS", "RUNNER_ARCH", "RUNNER_TEMP", "RUNNER_TOOL_CACHE",
    "PACKAGE_VERSION", "VERJSON_CHANGELOG_TOOL_CACHE",
    "NODE_AUTH_TOKEN", "GIT_TERMINAL_PROMPT",
    "npm_config_script_shell", "npm_config_ignore_scripts",
    "npm_config_userconfig", "npm_config_globalconfig",
})
PRIOR_HOOK_ENVIRONMENT = {
    "release-node": _SHARED_PRIOR_HOOK_ENVIRONMENT | {"RELEASE_VERIFICATION_PATH"},
    "release-artifact": _SHARED_PRIOR_HOOK_ENVIRONMENT,
    "release-snapshot": _SHARED_PRIOR_HOOK_ENVIRONMENT,
}
RETENTION_STEPS = {
    ".github/workflows/node-release.yml": "Delete package versions outside canonical retention",
    ".github/workflows/container-release.yml": "Delete image versions outside canonical retention",
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


def guard_pattern(script, variable):
    """Return the one `[[ ! "$VAR" =~ <pattern> ]]` guard a script applies to VAR."""
    patterns = re.findall(
        r'^\s*if \[\[ ! "\$' + re.escape(variable) + r'" =~ (\S+) \]\]; then$',
        script,
        re.MULTILINE,
    )
    if len(patterns) != 1:
        raise AssertionError(f"expected one {variable} guard, found {len(patterns)}")
    return patterns[0]


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


def run_verify_step(step, variables, ambient=None, locale=None):
    """Run the step's script with an environment-printing hook; return (result, hook_env).

    `ambient` adds variables to the step process, the way an earlier step's
    GITHUB_ENV export or a runner-level variable would; `env -i` must drop them.
    `locale` replaces the step's locale settings, so a guard is judged under it.
    """
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
            **(ambient or {}),
        }
        environment.pop(RETENTION_KEY, None)
        if locale is not None:
            for key in [key for key in environment if key.startswith("LC_") or key == "LOCPATH"]:
                del environment[key]
            environment.update(locale)
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
        cls.workflows = {mode: generate(mode, contract_sha) for mode in MODES}
        cls.steps = {mode: verify_step(workflow) for mode, workflow in cls.workflows.items()}
        cls.locale_build = tempfile.TemporaryDirectory(prefix="retention-guard-locales-")
        cls.locales, cls.missing_locale = ascii_digit_locales.guard_locales(cls.locale_build.name)

    @classmethod
    def tearDownClass(cls):
        cls.locale_build.cleanup()

    def retention_steps(self):
        """Yield (workflow path, retention job, retention step) for each retention site."""
        for workflow, step_name in RETENTION_STEPS.items():
            document = yaml.safe_load((ROOT / workflow).read_text(encoding="utf-8"))
            job = document["jobs"]["retention"]
            matches = [step for step in job["steps"] if step.get("name") == step_name]
            self.assertEqual(len(matches), 1, f"expected one {step_name!r} step in {workflow}")
            yield workflow, job, matches[0]

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

    def test_an_invalid_org_retention_window_fails_closed_before_the_hook_runs(self):
        invalid = (
            "0", "-1", "03", "abc", "1.5", " 3", "3 ", "1e3",
            "1000", str(2**64), *NON_ASCII_DIGITS,
        )
        cases = [
            (label, locale, mode, step, value)
            for label, locale in self.locales.items()
            for mode, step in self.steps.items()
            for value in invalid
        ]
        for label, locale, mode, step, value in cases:
            with self.subTest(locale=label, mode=mode, value=value):
                result, hook_env = run_verify_step(
                    step, variables={RETENTION_KEY: value}, locale=locale
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(
                    "::error::CI_PACKAGE_RETENTION_KEEP must be a positive integer",
                    result.stdout + result.stderr,
                )
                self.assertIsNone(hook_env, "the hook ran with an invalid retention window")
        if self.missing_locale:
            self.skipTest(self.missing_locale)

    def test_every_retention_guard_site_is_the_canonical_ascii_guard(self):
        # Literal, not behavioral: a reverted [1-9] range passes every value
        # under C.UTF-8, so only the spelling proves the guard in all locales.
        for mode, step in self.steps.items():
            with self.subTest(site=mode):
                self.assertEqual(guard_pattern(step["run"], RETENTION_KEY), RETENTION_GUARD)
        for workflow, _job, step in self.retention_steps():
            with self.subTest(site=workflow):
                self.assertEqual(guard_pattern(step["run"], "RETENTION_KEEP"), RETENTION_GUARD)

    def test_the_smallest_and_a_large_window_reach_the_hook_unchanged(self):
        for mode, step in self.steps.items():
            for value in ("1", "120", "999"):
                with self.subTest(mode=mode, value=value):
                    result, hook_env = run_verify_step(step, variables={RETENTION_KEY: value})
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual((hook_env or {}).get(RETENTION_KEY), value)

    def test_a_step_without_the_retention_env_fails_closed_instead_of_defaulting(self):
        for mode, step in self.steps.items():
            with self.subTest(mode=mode):
                stripped = deepcopy(step)
                del stripped["env"][RETENTION_KEY]
                result, hook_env = run_verify_step(stripped, variables={RETENTION_KEY: "7"})
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(
                    "::error::CI_PACKAGE_RETENTION_KEEP must be a positive integer no greater than 999, got ''.",
                    result.stdout + result.stderr,
                )
                self.assertIsNone(hook_env, "the hook ran without a retention window")

    def test_the_hook_environment_is_the_prior_allowlist_plus_only_the_retention_window(self):
        ambient = {
            "LEAK_CANARY": "must-not-leak",
            "GITHUB_TOKEN": "must-not-leak",
            "RETENTION_KEEP": "9",
            "VERJSON_PACKAGE_RETENTION_KEEP": "9",
            "CI_RUNNER_DEFAULT": "must-not-leak",
            "NODE_OPTIONS": "--require=/must-not-leak.js",
        }
        for mode, step in self.steps.items():
            with self.subTest(mode=mode):
                result, hook_env = run_verify_step(step, variables={}, ambient=ambient)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIsNotNone(hook_env, "the verification hook never ran")
                self.assertEqual(set(hook_env), set(PRIOR_HOOK_ENVIRONMENT[mode] | {RETENTION_KEY}))

    def test_retention_jobs_and_the_verify_step_read_one_window_through_one_guard(self):
        sites = {
            mode: (step["env"][RETENTION_KEY], guard_pattern(step["run"], RETENTION_KEY))
            for mode, step in self.steps.items()
        }
        for workflow, _job, step in self.retention_steps():
            sites[workflow] = (
                step["env"]["RETENTION_KEEP"], guard_pattern(step["run"], "RETENTION_KEEP")
            )
        self.assertEqual(len(sites), 5)
        self.assertEqual(
            {site: value for site, value in sites.items()},
            {site: sites["release-node"] for site in sites},
        )

    def test_no_job_reading_the_window_declares_an_environment_that_could_rebind_it(self):
        # An environment-scoped variable overrides the repository and
        # organization value for that job alone, which would split one window
        # into two between the verify hook and the retention job.
        for mode, workflow in self.workflows.items():
            with self.subTest(site=mode):
                self.assertNotIn("environment", workflow["jobs"]["verify"])
        for workflow, job, _step in self.retention_steps():
            with self.subTest(site=workflow):
                self.assertNotIn("environment", job)


if __name__ == "__main__":
    unittest.main()
