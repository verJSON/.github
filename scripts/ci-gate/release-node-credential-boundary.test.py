#!/usr/bin/env python3
"""Exercise the release-node package credential boundary."""

from copy import deepcopy
from hashlib import sha256, sha512
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
PROJECT_NAME = "release-credential-boundary-fixture"
PACKAGE_NAME = "@verjson/credential-boundary-fixture"
PACKAGE_VERSION = "1.0.0"
PACKAGE_TOKEN = "fixture-package-token"
GITHUB_TOKEN_EXPRESSION = "${{ github.token }}"
PACKAGE_TOKEN_EXPRESSION = "${{ secrets.NODE_AUTH_TOKEN }}"
APPROVED_RELEASE_STATE_SCRIPT_SHA256 = {
    "ec8e3a9157b40c0aaf9fdbebf920733609b297027b1fa972c190a3ef8cc52fca",
}
EXPECTED_RELEASE_STATE_ENV = {
    "VERSION": "${{ steps.release-version.outputs.version }}",
    "GITHUB_TOKEN": GITHUB_TOKEN_EXPRESSION,
    "BASH_ENV": "",
    "ENV": "",
    "SHELLOPTS": "",
    "BASHOPTS": "",
    "BASH_XTRACEFD": "",
    "PS4": "",
    "LD_PRELOAD": "",
    "LD_AUDIT": "",
    "LD_LIBRARY_PATH": "",
    "GIT_TRACE_CURL": "",
    "GIT_TRACE_REDACT": "",
    "GIT_EXEC_PATH": "",
    "GIT_CURL_VERBOSE": "",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_CONFIG_PARAMETERS": "",
    "GIT_TRACE2": "",
    "GIT_TRACE2_EVENT": "",
    "GIT_TRACE2_PERF": "",
    "GIT_TRACE2_ENV_VARS": "",
    "GIT_TRACE2_CONFIG_PARAMS": "",
}
CREDENTIAL_PROCESS_ENV_KEYS = {
    "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "BASH_XTRACEFD", "PS4",
    "LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH", "GIT_TRACE_CURL", "GIT_TRACE_REDACT",
    "GIT_EXEC_PATH", "GIT_CURL_VERBOSE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_PARAMETERS", "GIT_TRACE2", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF",
    "GIT_TRACE2_ENV_VARS", "GIT_TRACE2_CONFIG_PARAMS",
}


def find_step(steps, name):
    matches = [step for step in steps if step.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"expected one {name!r} step, found {len(matches)}")
    return matches[0]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_generated_workflow(workflow):
    jobs = workflow.get("jobs") or {}
    verify = jobs.get("verify") or {}
    steps = verify.get("steps") or []
    require(
        workflow.get("permissions") == {"contents": "read"},
        "workflow permissions are not limited to contents read",
    )
    require(
        verify.get("permissions") == {"contents": "read"},
        "verify-job permissions are not limited to contents read",
    )
    require(
        "GITHUB_TOKEN" not in (workflow.get("env") or {}),
        "workflow exposes Git credentials to release code",
    )
    require(
        "GITHUB_TOKEN" not in (verify.get("env") or {}),
        "verify job exposes Git credentials to release code",
    )
    checkouts = [step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")]
    require(checkouts, "verify job has no checkout")
    require(
        all((step.get("with") or {}).get("persist-credentials") is False for step in checkouts),
        "verify checkout persists Git credentials",
    )
    require("NODE_AUTH_TOKEN" not in (verify.get("env") or {}), "verify job exposes package credentials")
    secret_context = re.compile(r"\bsecrets\b", re.IGNORECASE)
    github_token_context = re.compile(
        r"\b(?:github\s*(?:\.|\[\s*['\"]?)\s*token|"
        r"secrets\s*(?:\.|\[\s*['\"]?)\s*GITHUB_TOKEN)\b",
        re.IGNORECASE,
    )
    github_context_object = re.compile(r"(?<![\w.])github\b(?!\s*\.)", re.IGNORECASE)

    def has_credentials(value):
        serialized = value if isinstance(value, str) else json.dumps(value)
        return any(
            pattern.search(serialized)
            for pattern in (secret_context, github_token_context, github_context_object)
        )

    require(
        not has_credentials(workflow.get("env") or {}),
        "workflow exposes a secret context to release code",
    )
    require(
        not has_credentials(verify.get("env") or {}),
        "verify job exposes a secret context to release code",
    )

    state = find_step(steps, "Resolve restart-safe release state")
    state_env = state.get("env") or {}
    require(state_env.get("GITHUB_TOKEN") == GITHUB_TOKEN_EXPRESSION, "release state lacks its scoped read token")
    require(
        state_env == EXPECTED_RELEASE_STATE_ENV,
        "release state exposes an unexpected environment variable",
    )
    require("shell" not in state and "uses" not in state, "release state customizes its shell or action")
    for scope_name, scope in (("workflow", workflow), ("verify job", verify)):
        require(
            not any(key.upper() in CREDENTIAL_PROCESS_ENV_KEYS for key in (scope.get("env") or {})),
            f"{scope_name} configures credential-sensitive process environment",
        )
        require(
            not ((scope.get("defaults") or {}).get("run") or {}).get("shell"),
            f"{scope_name} configures a custom shell",
        )
    state_run = str(state.get("run") or "")
    require("GIT_CONFIG_COUNT=1" in state_run, "release state does not scope Git auth to its commands")
    require("GIT_CONFIG_VALUE_0" in state_run, "release state does not pass Git auth through process config")
    require(
        sha256(state_run.rstrip("\n").encode()).hexdigest()
        in APPROVED_RELEASE_STATE_SCRIPT_SHA256,
        "release state does not match the approved restart-safe script",
    )
    for step in steps:
        if step is state:
            continue
        require(
            "GITHUB_TOKEN" not in (step.get("env") or {}),
            f"{step.get('name', 'unnamed step')} receives Git credentials",
        )

    install = find_step(steps, "Install dependencies")
    require(
        str(install.get("run") or "").strip() == "npm ci --ignore-scripts",
        "package acquisition runs lifecycle scripts with credentials",
    )
    require(
        (install.get("env") or {}).get("NODE_AUTH_TOKEN") == PACKAGE_TOKEN_EXPRESSION,
        "package acquisition lost private package authorization",
    )
    require(
        (install.get("env") or {}) == {"NODE_AUTH_TOKEN": PACKAGE_TOKEN_EXPRESSION},
        "package acquisition carries credentials beyond NODE_AUTH_TOKEN",
    )
    for step in steps:
        if step is install:
            continue
        if step is state:
            serialized = json.dumps(step).replace(GITHUB_TOKEN_EXPRESSION, "")
            require(
                not any(
                    pattern.search(serialized)
                    for pattern in (secret_context, github_token_context, github_context_object)
                ),
                "release state exposes additional credential contexts",
            )
            continue
        require(
            not has_credentials(step),
            f"{step.get('name', 'unnamed step')} references a secret context",
        )

    build = jobs.get("build")
    if build:
        require(not has_credentials(build), "artifact build job exposes credentials to repository code")

    acquisition = jobs.get("acquire-private-dependencies")
    if acquisition:
        acquisition_steps = acquisition.get("steps") or []
        for step in acquisition_steps:
            step_text = json.dumps(step)
            if step.get("name") == "Acquire dependencies without lifecycle execution":
                require(
                    (step.get("env") or {}).get("NODE_AUTH_TOKEN") == PACKAGE_TOKEN_EXPRESSION,
                    "private acquisition lost private package authorization",
                )
                step_text = step_text.replace(PACKAGE_TOKEN_EXPRESSION, "")
            require(
                not has_credentials(step_text),
                "private acquisition exposes credentials outside package acquisition",
            )

    downstream_names = (
        "Run dependency lifecycle scripts without credentials",
        "Prepare release package metadata",
        "Stamp the dispatched package versions",
        "Run the release verification suite",
    )
    for name in downstream_names:
        step = find_step(steps, name)
        require(
            (step.get("env") or {}).get("NODE_AUTH_TOKEN") == "",
            f"{name} does not explicitly clear package credentials",
        )

    lifecycle = find_step(steps, downstream_names[0])
    require(
        str(lifecycle.get("run") or "").strip() == "npm rebuild",
        "dependency lifecycle step does not run exactly npm rebuild",
    )
    verify_suite = find_step(steps, downstream_names[-1])
    require(
        "NODE_AUTH_TOKEN" not in str(verify_suite.get("run") or ""),
        "release verification script interpolates package credentials",
    )
    publish = jobs.get("publish") or {}
    publish_secrets = publish.get("secrets") or {}
    require(
        "node-release.yml" not in str(publish.get("uses") or "")
        or publish_secrets.get("NODE_AUTH_TOKEN") == "${{ secrets.NODE_AUTH_TOKEN }}",
        "release-node publication lost its package credential mapping",
    )


def create_package_archive():
    marker_script = (
        "const fs = require('fs');\n"
        "const value = Object.prototype.hasOwnProperty.call(process.env, 'NODE_AUTH_TOKEN')\n"
        "  ? process.env.NODE_AUTH_TOKEN\n"
        "  : '<unset>';\n"
        "fs.writeFileSync(process.env.POSTINSTALL_MARKER, value);\n"
    ).encode()
    package_json = json.dumps(
        {
            "name": PACKAGE_NAME,
            "version": PACKAGE_VERSION,
            "scripts": {"postinstall": "node postinstall.js"},
        }
    ).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in (("package/package.json", package_json), ("package/postinstall.js", marker_script)):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


class ReleaseNodeCredentialBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        commands = {
            "release-node": ["release-node", cls.contract_sha],
            "release-artifact": [
                "release-artifact", cls.contract_sha, "--build-runner", "ubuntu-24.04"
            ],
            "release-snapshot": ["release-snapshot", cls.contract_sha],
        }
        cls.workflows = {
            mode: yaml.safe_load(
                subprocess.check_output(
                    ["bash", "scripts/gen-changelog-caller.sh", *arguments],
                    cwd=ROOT,
                    text=True,
                )
            )
            for mode, arguments in commands.items()
        }
        cls.workflow = cls.workflows["release-node"]

    def test_generated_workflow_limits_package_and_git_credentials(self):
        for mode, workflow in self.workflows.items():
            with self.subTest(mode=mode):
                validate_generated_workflow(workflow)

    def test_workflow_audit_rejects_credentials_in_lifecycle_or_persisted_checkout(self):
        with self.subTest("lifecycle token"):
            mutated = deepcopy(self.workflow)
            steps = mutated["jobs"]["verify"]["steps"]
            lifecycle = find_step(steps, "Run dependency lifecycle scripts without credentials")
            lifecycle["env"]["NODE_AUTH_TOKEN"] = PACKAGE_TOKEN_EXPRESSION
            with self.assertRaisesRegex(ValueError, "references a secret context"):
                validate_generated_workflow(mutated)

        with self.subTest("acquisition extra command"):
            mutated = deepcopy(self.workflow)
            install = find_step(mutated["jobs"]["verify"]["steps"], "Install dependencies")
            install["run"] += "\nnode ./scripts/leak-token.js"
            with self.assertRaisesRegex(ValueError, "lifecycle scripts with credentials"):
                validate_generated_workflow(mutated)

        with self.subTest("persisted checkout token"):
            mutated = deepcopy(self.workflow)
            source_checkout = find_step(
                mutated["jobs"]["verify"]["steps"], "Check out the tree that will be released"
            )
            source_checkout["with"]["persist-credentials"] = True
            with self.assertRaisesRegex(ValueError, "persists Git credentials"):
                validate_generated_workflow(mutated)

        for command in (
            "git config --local http.extraheader $GIT_AUTH_HEADER",
            "git config --file .git/config http.extraheader $GIT_AUTH_HEADER",
            "git config http.extraheader $GIT_AUTH_HEADER",
            'git -C "$GITHUB_WORKSPACE" config --local http.extraheader $GIT_AUTH_HEADER',
            'git --git-dir="$GITHUB_WORKSPACE/.git" config --local http.extraheader $GIT_AUTH_HEADER',
            'printf "%s\\n" "$GITHUB_TOKEN" >> "$GITHUB_ENV"',
            'printf "%s\\n" "$GITHUB_TOKEN" >> "$GITHUB_WORKSPACE/.git/config"',
        ):
            with self.subTest("persisted release state token", command=command):
                mutated = deepcopy(self.workflow)
                state = find_step(
                    mutated["jobs"]["verify"]["steps"], "Resolve restart-safe release state"
                )
                state["run"] += f"\n{command}\n"
                with self.assertRaisesRegex(ValueError, "approved restart-safe script"):
                    validate_generated_workflow(mutated)

        with self.subTest("release-state shell startup environment"):
            mutated = deepcopy(self.workflow)
            state = find_step(
                mutated["jobs"]["verify"]["steps"], "Resolve restart-safe release state"
            )
            state["env"]["BASH_ENV"] = ".bash_env"
            with self.assertRaisesRegex(ValueError, "unexpected environment"):
                validate_generated_workflow(mutated)

        with self.subTest("release-state custom shell"):
            mutated = deepcopy(self.workflow)
            state = find_step(
                mutated["jobs"]["verify"]["steps"], "Resolve restart-safe release state"
            )
            state["shell"] = "bash --noprofile {0}"
            with self.assertRaisesRegex(ValueError, "customizes its shell"):
                validate_generated_workflow(mutated)

        with self.subTest("verify job custom default shell"):
            mutated = deepcopy(self.workflow)
            mutated["jobs"]["verify"]["defaults"] = {
                "run": {"shell": "bash --noprofile {0}"}
            }
            with self.assertRaisesRegex(ValueError, "configures a custom shell"):
                validate_generated_workflow(mutated)

        with self.subTest("workflow shell tracing option"):
            mutated = deepcopy(self.workflow)
            mutated["jobs"]["verify"]["env"] = {"SHELLOPTS": "xtrace"}
            with self.assertRaisesRegex(ValueError, "credential-sensitive process environment"):
                validate_generated_workflow(mutated)

        with self.subTest("workflow Git remote-helper override"):
            mutated = deepcopy(self.workflow)
            mutated["jobs"]["verify"]["env"] = {"GIT_EXEC_PATH": "./attacker-bin"}
            with self.assertRaisesRegex(ValueError, "credential-sensitive process environment"):
                validate_generated_workflow(mutated)

        with self.subTest("workflow Git config source override"):
            mutated = deepcopy(self.workflow)
            mutated["jobs"]["verify"]["env"] = {"GIT_CONFIG_GLOBAL": "/tmp/attacker.gitconfig"}
            with self.assertRaisesRegex(ValueError, "credential-sensitive process environment"):
                validate_generated_workflow(mutated)

        with self.subTest("workflow legacy Git curl tracing option"):
            mutated = deepcopy(self.workflow)
            mutated["jobs"]["verify"]["env"] = {"GIT_CURL_VERBOSE": "1"}
            with self.assertRaisesRegex(ValueError, "credential-sensitive process environment"):
                validate_generated_workflow(mutated)

        with self.subTest("workflow Git Trace2 environment logging"):
            mutated = deepcopy(self.workflow)
            mutated["jobs"]["verify"]["env"] = {
                "GIT_TRACE2": "1",
                "GIT_TRACE2_ENV_VARS": "GIT_CONFIG_VALUE_0",
            }
            with self.assertRaisesRegex(ValueError, "credential-sensitive process environment"):
                validate_generated_workflow(mutated)

        with self.subTest("verification token"):
            mutated = deepcopy(self.workflow)
            verify_step = find_step(
                mutated["jobs"]["verify"]["steps"], "Run the release verification suite"
            )
            verify_step["env"]["NODE_AUTH_TOKEN"] = PACKAGE_TOKEN_EXPRESSION
            with self.assertRaisesRegex(ValueError, "references a secret context"):
                validate_generated_workflow(mutated)

        with self.subTest("wrapped verification token"):
            mutated = deepcopy(self.workflow)
            verify_step = find_step(
                mutated["jobs"]["verify"]["steps"], "Run the release verification suite"
            )
            verify_step.setdefault("env", {})[
                "PRIVATE_REGISTRY_CREDENTIAL"
            ] = "${{ format('{{{0}}}', secrets.NODE_AUTH_TOKEN) }}"
            with self.assertRaisesRegex(ValueError, "references a secret context"):
                validate_generated_workflow(mutated)

        with self.subTest("folded wrapped verification token"):
            mutated = deepcopy(self.workflow)
            verify_step = find_step(
                mutated["jobs"]["verify"]["steps"], "Run the release verification suite"
            )
            verify_step.setdefault("env", {})["PRIVATE_REGISTRY_CREDENTIAL"] = (
                "${{ format(\n  '{{{0}}}', secrets.NODE_AUTH_TOKEN\n) }}"
            )
            with self.assertRaisesRegex(ValueError, "references a secret context"):
                validate_generated_workflow(mutated)

        with self.subTest("serialized verification secrets object"):
            mutated = deepcopy(self.workflow)
            verify_step = find_step(
                mutated["jobs"]["verify"]["steps"], "Run the release verification suite"
            )
            verify_step.setdefault("env", {})["PRIVATE_REGISTRY_CREDENTIALS"] = (
                "${{ toJSON(secrets) }}"
            )
            with self.assertRaisesRegex(ValueError, "references a secret context"):
                validate_generated_workflow(mutated)

        with self.subTest("serialized verification GitHub context"):
            mutated = deepcopy(self.workflow)
            verify_step = find_step(
                mutated["jobs"]["verify"]["steps"], "Run the release verification suite"
            )
            verify_step.setdefault("env", {})["PRIVATE_GITHUB_CONTEXT"] = (
                "${{ toJSON(github) }}"
            )
            with self.assertRaisesRegex(ValueError, "references a secret context"):
                validate_generated_workflow(mutated)

        with self.subTest("indexed verification GitHub token"):
            mutated = deepcopy(self.workflow)
            verify_step = find_step(
                mutated["jobs"]["verify"]["steps"], "Run the release verification suite"
            )
            verify_step.setdefault("env", {})["PRIVATE_GITHUB_TOKEN"] = (
                "${{ github['token'] }}"
            )
            with self.assertRaisesRegex(ValueError, "references a secret context"):
                validate_generated_workflow(mutated)

        with self.subTest("artifact build GitHub token"):
            mutated = deepcopy(self.workflows["release-artifact"])
            build_step = mutated["jobs"]["build"]["steps"][0]
            build_step.setdefault("env", {})["PRIVATE_GITHUB_TOKEN"] = (
                "${{ format('{{{0}}}', github.token) }}"
            )
            with self.assertRaisesRegex(ValueError, "exposes credentials to repository code"):
                validate_generated_workflow(mutated)

        with self.subTest("artifact build serialized GitHub context"):
            mutated = deepcopy(self.workflows["release-artifact"])
            build_step = mutated["jobs"]["build"]["steps"][0]
            build_step.setdefault("env", {})["PRIVATE_GITHUB_CONTEXT"] = (
                "${{ toJSON(github) }}"
            )
            with self.assertRaisesRegex(ValueError, "exposes credentials to repository code"):
                validate_generated_workflow(mutated)

        with self.subTest("lifecycle command must be exact"):
            mutated = deepcopy(self.workflow)
            lifecycle = find_step(
                mutated["jobs"]["verify"]["steps"], "Run dependency lifecycle scripts without credentials"
            )
            lifecycle["run"] = "echo npm rebuild"
            with self.assertRaisesRegex(ValueError, "does not run exactly npm rebuild"):
                validate_generated_workflow(mutated)

        with self.subTest("verify permissions must be read-only"):
            mutated = deepcopy(self.workflow)
            mutated["jobs"]["verify"]["permissions"]["contents"] = "write"
            with self.assertRaisesRegex(ValueError, "not limited to contents read"):
                validate_generated_workflow(mutated)

        with self.subTest("workflow-scoped Git token"):
            mutated = deepcopy(self.workflow)
            mutated["env"] = {"GITHUB_TOKEN": GITHUB_TOKEN_EXPRESSION}
            with self.assertRaisesRegex(ValueError, "workflow exposes Git credentials"):
                validate_generated_workflow(mutated)

        with self.subTest("job-scoped Git token"):
            mutated = deepcopy(self.workflow)
            mutated["jobs"]["verify"]["env"] = {"GITHUB_TOKEN": GITHUB_TOKEN_EXPRESSION}
            with self.assertRaisesRegex(ValueError, "verify job exposes Git credentials"):
                validate_generated_workflow(mutated)

        with self.subTest("verification-step Git token"):
            mutated = deepcopy(self.workflow)
            verify_step = find_step(
                mutated["jobs"]["verify"]["steps"], "Run the release verification suite"
            )
            verify_step.setdefault("env", {})["GITHUB_TOKEN"] = GITHUB_TOKEN_EXPRESSION
            with self.assertRaisesRegex(ValueError, "receives Git credentials"):
                validate_generated_workflow(mutated)

    def test_release_state_git_lookup_distinguishes_found_absent_and_failure(self):
        state = find_step(self.workflow["jobs"]["verify"]["steps"], "Resolve restart-safe release state")
        actual_git = shutil.which("git")
        self.assertIsNotNone(actual_git)
        token = "fixture-read-token"
        encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cases = (
            ("tag exists", True, 2, 0, 0, ["ls-remote", "fetch"]),
            ("tag absent", False, 2, 0, 0, ["ls-remote"]),
            ("remote lookup fails", False, 128, 0, 128, ["ls-remote"]),
            ("tag fetch fails", True, 2, 128, 128, ["ls-remote", "fetch"]),
        )
        for label, tag_exists, lookup_status, fetch_status, expected_status, expected_calls in cases:
            with self.subTest(label):
                with tempfile.TemporaryDirectory(prefix="release-state-git-auth-") as temporary:
                    root = Path(temporary)
                    subprocess.run([actual_git, "init", "--quiet", str(root)], check=True)
                    if tag_exists:
                        release_log = root / "CHANGELOG" / "v1.2.3.md"
                        release_log.parent.mkdir()
                        release_log.write_text("# v1.2.3\n", encoding="utf-8")
                        subprocess.run([actual_git, "-C", str(root), "add", "CHANGELOG/v1.2.3.md"], check=True)
                        subprocess.run(
                            [actual_git, "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture release"],
                            check=True,
                        )
                        subprocess.run([actual_git, "-C", str(root), "tag", "v1.2.3"], check=True)
                    subprocess.run(
                        [actual_git, "-C", str(root), "remote", "add", "origin", "https://github.com/verjson/example.git"],
                        check=True,
                    )
                    bin_dir = root / "bin"
                    bin_dir.mkdir()
                    capture = root / "git-auth.txt"
                    fake_git = bin_dir / "git"
                    fake_git.write_text(
                        "#!/bin/sh\n"
                        "case \"$1\" in\n"
                        "  ls-remote|fetch)\n"
                        "    printf '%s\\t%s\\t%s\\t%s\\n' \"$1\" \"$GIT_CONFIG_COUNT\" \"$GIT_CONFIG_KEY_0\" \"$GIT_CONFIG_VALUE_0\" >> \"$GIT_AUTH_CAPTURE\"\n"
                        "    if [ \"$1\" = ls-remote ]; then\n"
                        "      if [ \"$GIT_TAG_EXISTS\" = true ]; then exit 0; fi\n"
                        "      exit \"$GIT_LOOKUP_STATUS\"\n"
                        "    fi\n"
                        "    exit \"$GIT_FETCH_STATUS\"\n"
                        "    ;;\n"
                        "esac\n"
                        "exec \"$ACTUAL_GIT\" \"$@\"\n",
                        encoding="utf-8",
                    )
                    fake_git.chmod(0o755)
                    test_run = state["run"].replace("/usr/bin/git", str(fake_git))
                    git_test_environment = " ".join(
                        f'{name}="${name}"'
                        for name in (
                            "ACTUAL_GIT",
                            "GIT_AUTH_CAPTURE",
                            "GIT_TAG_EXISTS",
                            "GIT_LOOKUP_STATUS",
                            "GIT_FETCH_STATUS",
                        )
                    )
                    isolated_env_call = "/usr/bin/env -i "
                    self.assertEqual(test_run.count(isolated_env_call), 1)
                    test_run = test_run.replace(
                        isolated_env_call,
                        isolated_env_call + git_test_environment + " ",
                        1,
                    )
                    completed = subprocess.run(
                        ["bash", "-euo", "pipefail", "-c", test_run],
                        cwd=root,
                        env={
                            **os.environ,
                            "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                            "ACTUAL_GIT": actual_git,
                            "GITHUB_TOKEN": token,
                            "VERSION": "v1.2.3",
                            "GITHUB_OUTPUT": str(root / "github-output"),
                            "GIT_AUTH_CAPTURE": str(capture),
                            "GIT_TAG_EXISTS": "true" if tag_exists else "false",
                            "GIT_LOOKUP_STATUS": str(lookup_status),
                            "GIT_FETCH_STATUS": str(fetch_status),
                        },
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(completed.returncode, expected_status, completed.stdout + completed.stderr)
                    if label == "remote lookup fails":
                        self.assertIn("Unable to resolve remote release tag state", completed.stderr)
                    records = [line.split("\t") for line in capture.read_text(encoding="utf-8").splitlines()]
                    self.assertEqual([record[0] for record in records], expected_calls)
                    for _, count, key, value in records:
                        self.assertEqual(count, "1")
                        self.assertEqual(key, "http.https://github.com/.extraheader")
                        self.assertEqual(value, f"AUTHORIZATION: basic {encoded}")
                    persisted = subprocess.run(
                        [actual_git, "-C", str(root), "config", "--local", "--get", "http.https://github.com/.extraheader"],
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(persisted.returncode, 0, "release-state Git auth persisted in .git/config")

    def test_private_acquisition_succeeds_and_lifecycle_runs_without_credential(self):
        archive = create_package_archive()
        integrity = "sha512-" + base64.b64encode(sha512(archive).digest()).decode("ascii")
        expected_path = f"/{PACKAGE_NAME}/-/{PACKAGE_NAME.rsplit('/', 1)[-1]}-{PACKAGE_VERSION}.tgz"
        authorizations = []

        class RegistryHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                authorizations.append(self.headers.get("Authorization"))
                if self.path != expected_path:
                    self.send_error(404)
                    return
                if self.headers.get("Authorization") != f"Bearer {PACKAGE_TOKEN}":
                    self.send_error(401)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(archive)))
                self.end_headers()
                self.wfile.write(archive)

            def log_message(self, *_):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), RegistryHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server_thread.join, timeout=5)
        self.addCleanup(server.shutdown)

        with tempfile.TemporaryDirectory(prefix="release-node-credential-boundary-") as temporary:
            root = Path(temporary)
            registry = f"http://127.0.0.1:{server.server_port}/"
            archive_url = registry + expected_path.lstrip("/")
            npmrc = root / "npmrc"
            npmrc.write_text(
                f"registry={registry}\n"
                f"//127.0.0.1:{server.server_port}/:_authToken=${{NODE_AUTH_TOKEN}}\n",
                encoding="utf-8",
            )
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": PROJECT_NAME,
                        "version": "1.0.0",
                        "dependencies": {PACKAGE_NAME: PACKAGE_VERSION},
                        "allowScripts": {PACKAGE_NAME: True},
                    }
                ),
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text(
                json.dumps(
                    {
                        "name": PROJECT_NAME,
                        "version": "1.0.0",
                        "lockfileVersion": 3,
                        "requires": True,
                        "packages": {
                            "": {
                                "name": PROJECT_NAME,
                                "version": "1.0.0",
                                "dependencies": {PACKAGE_NAME: PACKAGE_VERSION},
                            },
                            f"node_modules/{PACKAGE_NAME}": {
                                "version": PACKAGE_VERSION,
                                "resolved": archive_url,
                                "integrity": integrity,
                                "hasInstallScript": True,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            marker = root / "postinstall-token.txt"
            environment = {
                **os.environ,
                "NODE_AUTH_TOKEN": PACKAGE_TOKEN,
                "NPM_CONFIG_USERCONFIG": str(npmrc),
                "POSTINSTALL_MARKER": str(marker),
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
            }
            install = subprocess.run(
                ["npm", "ci", "--ignore-scripts", "--audit=false", "--fund=false"],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
            self.assertTrue((root / "node_modules" / "@verjson" / "credential-boundary-fixture").is_dir())
            self.assertFalse(marker.exists(), "npm ci ran package lifecycle code during credentialed acquisition")
            self.assertIn(f"Bearer {PACKAGE_TOKEN}", authorizations)

            credentialless_environment = {**environment, "NODE_AUTH_TOKEN": ""}
            rebuild = subprocess.run(
                ["npm", "rebuild"],
                cwd=root,
                env=credentialless_environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(rebuild.returncode, 0, rebuild.stdout + rebuild.stderr)
            self.assertTrue(marker.exists(), "npm rebuild did not restore dependency lifecycle execution")
            self.assertEqual(marker.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
