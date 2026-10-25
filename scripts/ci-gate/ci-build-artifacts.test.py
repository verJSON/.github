#!/usr/bin/env python3
"""Adversarial tests for trusted same-run ArtifactSet handling."""

from __future__ import annotations

import hashlib
import gzip
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yaml
import ci_build_artifacts as artifacts


class SharedNetworkBoundaryTests(unittest.TestCase):
    def _network_probe_mocks(self, external_error=None):
        listener = unittest.mock.Mock()
        listener.getsockname.return_value = ("127.0.0.1", 2345)
        accepted = unittest.mock.Mock()
        listener.accept.return_value = (accepted, ("127.0.0.1", 4567))
        client = unittest.mock.Mock()
        external = unittest.mock.Mock()
        if external_error is not None:
            external.connect.side_effect = external_error

        return listener, accepted, client, external

    def test_network_probe_accepts_loopback_and_proven_no_route(self) -> None:
        listener, _accepted, client, external = self._network_probe_mocks(
            OSError(__import__("errno").ENETUNREACH, "network unreachable")
        )
        with (
            patch.object(artifacts, "enforce_shared_sandbox_process_state"),
            patch.object(artifacts.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(artifacts.socket, "socket", side_effect=[listener, external]),
            patch.object(artifacts.socket, "create_connection", return_value=client),
        ):
            artifacts.network_probe()

        listener.accept.assert_called_once_with()
        listener.close.assert_called_once_with()
        client.close.assert_called_once_with()
        external.close.assert_called_once_with()

    def test_network_probe_rejects_unexpected_interface(self) -> None:
        with (
            patch.object(artifacts, "enforce_shared_sandbox_process_state"),
            patch.object(artifacts.socket, "if_nameindex", return_value=[(1, "lo"), (2, "eth0")]),
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "unexpected network interface"):
                artifacts.network_probe()

    def test_network_probe_rejects_external_errors_that_do_not_prove_no_route(self) -> None:
        errno = __import__("errno")
        for error in (
            OSError(errno.ECONNREFUSED, "connection refused"),
            OSError(errno.ETIMEDOUT, "timed out"),
        ):
            with self.subTest(error=error.errno):
                listener, _accepted, client, external = self._network_probe_mocks(error)
                with (
                    patch.object(artifacts, "enforce_shared_sandbox_process_state"),
                    patch.object(artifacts.socket, "if_nameindex", return_value=[(1, "lo")]),
                    patch.object(artifacts.socket, "socket", side_effect=[listener, external]),
                    patch.object(artifacts.socket, "create_connection", return_value=client),
                ):
                    with self.assertRaisesRegex(artifacts.IntegrityError, "did not prove a missing route"):
                        artifacts.network_probe()

                external.close.assert_called_once_with()

    def test_mode_gate_output_is_wired_into_preflight_in_both_workflows(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for filename in ("node-ci.yml", "node-ci-protected.yml"):
            with self.subTest(workflow=filename):
                workflow = yaml.safe_load((root / ".github/workflows" / filename).read_text())
                events = workflow.get("on", workflow.get(True))
                if filename == "node-ci.yml":
                    declared_runtime_inputs = {
                        name: spec.get("default")
                        for name, spec in events["workflow_call"]["inputs"].items()
                        if not name.startswith("build-artifact-")
                    }
                    self.assertEqual(declared_runtime_inputs, artifacts.RUNTIME_INPUT_DEFAULTS)
                self.assertFalse(events["workflow_call"]["inputs"]["build-artifact-mode-share-eligible"]["default"])
                self.assertEqual(
                    events["workflow_call"]["outputs"]["build-artifact-mode-share-eligible"]["value"],
                    "${{ jobs.artifact-mode-gate.outputs.share-eligible }}",
                )
                jobs = workflow["jobs"]
                self.assertEqual(
                    jobs["artifact-mode-gate"]["outputs"]["share-eligible"],
                    "${{ steps.mode.outputs.share-eligible }}",
                )
                select = next(step for step in jobs["artifact-preflight"]["steps"] if step.get("id") == "select")
                self.assertEqual(
                    select["env"]["BUILD_ARTIFACT_MODE_SHARE_ELIGIBLE"],
                    "${{ inputs.build-artifact-mode-share-eligible }}",
                )

    def test_both_workflow_variants_probe_egress_and_unshare_candidate_networks(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for filename in ("node-ci.yml", "node-ci-protected.yml"):
            with self.subTest(workflow=filename):
                workflow = yaml.safe_load((root / ".github/workflows" / filename).read_text())
                steps = workflow["jobs"]["build-test"]["steps"]
                probe = next(step for step in steps if step.get("name") == "Prove shared sandbox network boundary")
                candidate = next(step for step in steps if step.get("name") == "Run exact credentialless consumer script plan")
                candidate_runtime = (
                    __import__("inspect").getsource(artifacts.run_protected_candidate_plan)
                    if filename == "node-ci-protected.yml"
                    else candidate["run"]
                )
                self.assertIn("--unshare-net", probe["run"])
                self.assertIn("--disable-userns", probe["run"])
                self.assertIn("network-probe", probe["run"])
                self.assertNotIn("--cap-add", probe["run"])
                self.assertIn("inputs.build-artifact-role == 'producer' || inputs.build-artifact-role == 'consumer'", probe["if"])
                self.assertIn("*network_args", candidate_runtime)
                self.assertIn("bubblewrap_network_args(shared_artifact=True)", candidate_runtime)
                bootstrap = candidate_runtime
                self.assertIn("enforce_shared_sandbox_process_state()", bootstrap)
                self.assertIn("artifact_helper_args", bootstrap)
                self.assertIn("*artifact_helper_args", bootstrap)
                self.assertIn("artifact_mount_roots", bootstrap)
                self.assertIn('"NPM_CONFIG_SCRIPT_SHELL": "/bin/sh"', bootstrap)
                self.assertIn('"npm_config_script_shell": "/bin/sh"', bootstrap)
                unset_position = bootstrap.index("for env_name in unset_env:")
                shell_pin_position = bootstrap.index('"NPM_CONFIG_SCRIPT_SHELL": "/bin/sh"', unset_position)
                self.assertLess(unset_position, shell_pin_position)
                self.assertNotIn("CAP_NET_ADMIN", bootstrap)
                self.assertNotIn("CAP_SETPCAP", bootstrap)
                self.assertNotIn("/usr/sbin/ip", bootstrap)
                self.assertNotIn("--bounding-set=-all", bootstrap)
                if filename == "node-ci.yml":
                    self.assertIn("consume_artifact = os.environ.get(\"BUILD_ARTIFACT_ROLE\") == \"consumer\"", candidate_runtime)
                    self.assertIn("if consume_artifact:", candidate_runtime)
                    self.assertIn('bind_source(source, f"{sandbox_workspace}/{relative}")', candidate_runtime)
                    network_position = candidate_runtime.index("*network_args")
                    self.assertLess(candidate_runtime.rfind("arguments = [", 0, network_position), network_position)
                    self.assertLess(network_position, candidate_runtime.index("subprocess.run(", network_position))
                else:
                    self.assertIn("verified_artifact_mounts()", candidate_runtime)
                    self.assertIn('roots = artifact_mount_roots(manifest["exportPolicy"]["paths"])', candidate_runtime)
                    self.assertIn("isinstance(roots, tuple)", candidate_runtime)
                    self.assertIn("artifact_mount_args, artifact_mount_fds = verified_artifact_mounts()", candidate_runtime)
                    self.assertIn("*artifact_mount_args", candidate_runtime)
                    self.assertIn('"--ro-bind"', candidate_runtime)
                    self.assertIn("if not consume_artifact:", candidate_runtime)
                    self.assertIn("return [], []", candidate_runtime)
                    self.assertLess(candidate_runtime.index("active_process = subprocess.Popen("), candidate_runtime.index("*network_args"))
                    self.assertLess(candidate_runtime.index("*network_args"), candidate_runtime.index("*artifact_mount_args"))

    def test_shared_bubblewrap_initializes_loopback_without_candidate_capabilities(self) -> None:
        args = artifacts.bubblewrap_network_args(shared_artifact=True)
        self.assertEqual(
            args,
            ["--cap-drop", "ALL", "--unshare-net"],
        )
        self.assertNotIn("CAP_NET_ADMIN", __import__("inspect").getsource(artifacts.bubblewrap_network_args))
        self.assertNotIn("CAP_SETPCAP", __import__("inspect").getsource(artifacts.bubblewrap_network_args))

        probe_source = __import__("inspect").getsource(artifacts.network_probe)
        self.assertIn("enforce_shared_sandbox_process_state()", probe_source)
        self.assertNotIn("/usr/sbin/ip", probe_source)
        self.assertIn('listener.bind(("127.0.0.1", 0))', probe_source)
        self.assertIn('external.connect(("1.1.1.1", 443))', probe_source)

    def test_shared_sandbox_rejects_capabilities_and_requires_enforcing_profile(self) -> None:
        status = "\n".join(
            [*(f"{name}:\t0000000000000000" for name in artifacts._SANDBOX_CAPABILITY_FIELDS), "NoNewPrivs:\t0"]
        )
        profile = "bwrap//&unpriv_bwrap (enforce)\n"
        artifacts.validate_shared_sandbox_process_state(
            status,
            profile,
            require_no_new_privs=False,
        )
        for capability in artifacts._SANDBOX_CAPABILITY_FIELDS:
            with self.assertRaises(artifacts.IntegrityError):
                changed_status = status.replace(f"{capability}:\t0000000000000000", f"{capability}:\t0000000000000001")
                artifacts.validate_shared_sandbox_process_state(
                    changed_status,
                    profile,
                    require_no_new_privs=False,
                )
        with self.assertRaisesRegex(artifacts.IntegrityError, "AppArmor child profile"):
            artifacts.validate_shared_sandbox_process_state(
                status,
                "unconfined",
                require_no_new_privs=False,
            )

    def test_shared_sandbox_requires_no_new_privs_before_candidate_execution(self) -> None:
        status = "\n".join(
            [*(f"{name}:\t0000000000000000" for name in artifacts._SANDBOX_CAPABILITY_FIELDS), "NoNewPrivs:\t1"]
        )
        artifacts.validate_shared_sandbox_process_state(
            status,
            "bwrap//&unpriv_bwrap (enforce)",
            require_no_new_privs=True,
        )
        with self.assertRaisesRegex(artifacts.IntegrityError, "lacks no_new_privs"):
            artifacts.validate_shared_sandbox_process_state(
                status.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0"),
                "bwrap//&unpriv_bwrap (enforce)",
                require_no_new_privs=True,
            )


class ArtifactPolicyTests(unittest.TestCase):
    def test_missing_exact_repository_policy_row_selects_fallback(self) -> None:
        table = artifacts.load_policy_table(b'{"version":1,"repositories":{}}')
        self.assertIsNone(artifacts._policy_row(table, "Verjson/private-repo"))

    def test_policy_path_matching_uses_components(self) -> None:
        requested = artifacts.normalize_roots(["dist/app"], "paths")
        allowed = artifacts.normalize_roots(["dist"], "allowed")
        required = artifacts.normalize_roots(["dist/app"], "required")
        self.assertTrue(artifacts.requested_roots_allowed(requested, allowed, required))
        self.assertFalse(artifacts.requested_roots_allowed(("dist-old",), allowed, required))
        for value in ("../dist", "/dist", "dist/**", "dist//app", "dist/."):
            self.assertIsNone(artifacts.normalize_roots([value], "paths"), value)
        too_deep = "/".join(["d"] * (artifacts.MAX_DEPTH + 1))
        self.assertIsNone(artifacts.normalize_roots([too_deep], "paths"))

    def test_artifact_mount_roots_reject_nested_paths_that_hide_checkout_siblings(self) -> None:
        with self.assertRaisesRegex(artifacts.IntegrityError, "nested ArtifactSet roots"):
            artifacts.artifact_mount_roots(["dist/app", "dist/worker", "docs"])
        self.assertEqual(artifacts.artifact_mount_roots(["dist", "docs"]), ("dist", "docs"))
        with self.assertRaisesRegex(artifacts.IntegrityError, "overlap"):
            artifacts.artifact_mount_roots(["dist", "dist/app"])

    def test_mode_gate_accepts_only_event_specific_managed_mode(self) -> None:
        plan = json.dumps([{"id": "unit", "events": ["pull_request"], "branches": None}])
        artifacts.mode_gate(
            plan,
            event_name="pull_request",
            base_ref="main",
            ref_name="feature",
            repository="Verjson/app",
            head_repository="Verjson/app",
            secretless_pr=True,
            secretless_trusted_ref=False,
        )
        self.assertFalse(
            artifacts.mode_gate(
                plan,
                event_name="pull_request",
                base_ref="main",
                ref_name="feature",
                repository="Verjson/app",
                head_repository="contributor/app",
                secretless_pr=True,
                secretless_trusted_ref=False,
            )
        )

    def test_mode_gate_cli_exports_same_repo_and_fork_eligibility(self) -> None:
        plan = json.dumps([{"id": "unit", "events": ["pull_request"], "branches": None}])
        for head_repository, expected in (("Verjson/app", "true"), ("contributor/app", "false")):
            with self.subTest(head_repository=head_repository), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "github-output"
                env = {
                    "BUILD_ARTIFACT_MODE_PLAN": plan,
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_BASE_REF": "main",
                    "GITHUB_REF_NAME": "feature",
                    "GITHUB_REPOSITORY": "Verjson/app",
                    "BUILD_ARTIFACT_HEAD_REPOSITORY": head_repository,
                    "SECRETLESS_PR": "true",
                    "SECRETLESS_TRUSTED_REF": "false",
                    "GITHUB_OUTPUT": str(output),
                }
                with patch.dict(os.environ, env, clear=False):
                    self.assertEqual(artifacts.main(["mode-gate"]), 0)
                output_text = output.read_text(encoding="utf-8")
                self.assertIn("share-eligible<<", output_text)
                self.assertIn(f"\n{expected}\n", output_text)
                self.assertIn("passed<<", output_text)

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "github-output"
            env["BUILD_ARTIFACT_MODE_PLAN"] = "{malformed"
            env["GITHUB_OUTPUT"] = str(output)
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(artifacts.main(["mode-gate"]), 1)
            self.assertFalse(output.exists())

    def test_consumer_test_script_that_rebuilds_is_not_share_eligible(self) -> None:
        package_scripts = {
            "build": "tsc",
            "test": "npm run build && vitest",
        }
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "test", "build"))

    def test_direct_build_tools_task_runners_and_local_executables_are_ineligible(self) -> None:
        package_scripts = {
            "build": "vite build",
            "direct-build-tool": "vite build --outDir /tmp/rebuilt && vitest",
            "task-runner": "turbo run build && vitest",
            "local-executable": "./tools/rebuild && vitest",
            "multiline-build-tool": "echo ok\nvite build --outDir /tmp/rebuilt",
            "multiline-task-runner": "true\nmake build",
            "multiline-local-executable": "true\n./tools/rebuild",
            "tsc-no-emit-overridden": "tsc --noEmit false --outDir /tmp/rebuilt",
            "tsc-no-emit-equals-overridden": "tsc --noEmit=false --outDir /tmp/rebuilt",
            "package-manager-shell-override": "npm run lint --script-shell=./tools/rebuild",
            "safe-test": "vitest run",
            "safe-typecheck": "tsc --noEmit",
            "lint": "eslint",
        }

        for script in (
            "direct-build-tool",
            "task-runner",
            "local-executable",
            "multiline-build-tool",
            "multiline-task-runner",
            "multiline-local-executable",
            "tsc-no-emit-overridden",
            "tsc-no-emit-equals-overridden",
            "package-manager-shell-override",
        ):
            with self.subTest(script=script):
                self.assertTrue(artifacts._script_reaches_build(package_scripts, script, "build"))
        self.assertFalse(artifacts._script_reaches_build(package_scripts, "safe-test", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "safe-typecheck", "build"))

    def test_direct_typescript_checks_remain_per_lane(self) -> None:
        unsafe_commands = (
            "tsc --noEmit --noemit false --outDir /tmp/rebuilt",
            "tsc --noEmit null --outDir /tmp/rebuilt",
            "tsc --outDir --noEmit",
            "tsc --noEmit @rebuild-options",
            'tsc --noEmit "@rebuild-options"',
            "tsc --NoEmit --NOEMIT=0",
            "tsc --noEmit *",
            "tsc --noEmit [@]rebuild-options.txt",
            "vitest run **/*.test.ts",
        )
        for command in unsafe_commands:
            with self.subTest(command=command):
                self.assertFalse(artifacts._direct_commands_are_supported_checks(command))
        self.assertFalse(artifacts._direct_commands_are_supported_checks("tsc --noEmit"))
        self.assertTrue(
            artifacts._script_reaches_build(
                {"build": "tsc", "typecheck": "tsc --noEmit"}, "typecheck", "build"
            )
        )

    def test_direct_check_admission_rejects_shell_redirection_and_mutating_options(self) -> None:
        unsafe_commands = (
            "printf x > dist/app.js",
            "printf x >> dist/app.js",
            "printf x <> dist/app.js",
            "printf x < input.txt",
            "prettier --write dist",
            "prettier -w dist",
            "eslint --fix dist",
            "eslint --fix=true dist",
            "biome check --write dist",
            "biome format --apply dist",
            "stylelint --fix dist",
            "markdownlint --fix dist",
            "vitest run -u",
            "vitest run --outputFile dist/results.json",
            "vitest run --coverage",
            "jest --updateSnapshot",
            "jest -iu",
            "jest --json --outputFile dist/results.json",
            "eslint --output-file dist/results.json",
            "eslint --cache --cache-location dist/.eslintcache",
            "playwright test -u",
            "playwright test --update-snapshots",
            "playwright test --output dist/test-results",
            "ava -u",
            "ava --update-snapshots",
            "ava tests --update-snapshots",
            "vitest init browser",
            "vitest bench",
            "vitest list",
            "vitest watch",
            "tap plugin add @tapjs/fixture",
            "tap plugin rm @tapjs/typescript",
            "tap build",
            "tap report html",
            "tap config edit",
            "mocha init dist",
            "tsc --noEmit",
            "mocha --reporter-options output=dist/results.json",
            "cypress run --config screenshotsFolder=dist/screenshots",
        )
        for command in unsafe_commands:
            with self.subTest(command=command):
                self.assertFalse(artifacts._direct_commands_are_supported_checks(command))
                self.assertTrue(
                    artifacts._script_reaches_build(
                        {"build": "tsc", "test": command}, "test", "build"
                    )
                )

    def test_direct_check_admission_accepts_only_supported_read_only_shapes(self) -> None:
        safe_commands = (
            "eslint src",
            "prettier --check src",
            "prettier --list-different src",
            "biome check src",
            "biome format --check src",
            "vitest",
            "vitest run",
            "jest tests/unit.test.js",
            "playwright test",
            "cypress run",
            "ava tests",
            "uvu tests",
        )
        for command in safe_commands:
            with self.subTest(command=command):
                self.assertTrue(artifacts._direct_commands_are_supported_checks(command))

    def test_direct_command_admission_rejects_shell_group_and_ambiguous_whitespace(self) -> None:
        unsupported = (
            "npm run lint{true}",
            "npm run lint\rfalse",
            "npm run lint\ffalse",
            "npm run lint\vfalse",
            "npm run lint\u00a0false",
            "npm run lint\u200bfalse",
            "npm run lint && (npm run build)",
        )
        for command in unsupported:
            with self.subTest(command=command):
                self.assertFalse(artifacts._direct_commands_are_supported_checks(command))

    def test_package_manager_options_do_not_hide_rebuilds(self) -> None:
        package_scripts = {
            "build": "tsc",
            "test": "npm --silent run build && vitest",
            "e2e": "npm --unknown-option value run build",
            "integration": 'PM=npm; "$PM" run build && playwright test',
            "system": "bash -c 'npm run build' && vitest",
            "acceptance": "eval 'npm run build' && playwright test",
            "inspect": "node -p \"require('child_process').execSync('npm run build')\"",
            "nested": "npm run verify && vitest",
            "verify": "eslint",
            "preverify": "npm run build",
            "stdin-shell": "printf '%s\\n' 'npm run build' | sh -s",
            "stdin-node": "printf '%s\\n' \"require('child_process').execSync('npm run build')\" | node",
            "stdin-python": "printf '%s\\n' 'npm run build' | python -",
            "stdin-deno": "printf '%s\\n' 'npm run build' | deno run -",
            "node-file": "node ./scripts/test-helper.js",
            "exec-node": "exec node ./scripts/test-helper.js",
            "timeout-node": "timeout 30 node ./scripts/test-helper.js",
            "env-node": "env -i node ./scripts/test-helper.js",
            "assigned-node": "NODE_ENV=test node ./scripts/test-helper.js",
            "spaced-node": " node ./scripts/test-helper.js",
            "trailing-prefix": "npm run verify --prefix tools",
            "trailing-workspace": "npm run verify --workspace tools",
            "changed-directory": "cd tools && npm run verify",
        }
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "test", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "e2e", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "integration", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "system", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "acceptance", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "inspect", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "nested", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "stdin-shell", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "stdin-node", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "stdin-python", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "stdin-deno", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "node-file", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "exec-node", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "timeout-node", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "env-node", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "assigned-node", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "spaced-node", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "trailing-prefix", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "trailing-workspace", "build"))
        self.assertTrue(artifacts._script_reaches_build(package_scripts, "changed-directory", "build"))

    def test_role_mapping_allows_source_lane_to_consume_but_only_synthetic_role_builds(self) -> None:
        artifact = {"producer": "build", "consumers": ["test"]}
        self.assertTrue(artifacts._role_matches_lane("consumer", "build", artifact))
        self.assertTrue(artifacts._role_matches_lane("consumer", "test", artifact))
        self.assertTrue(artifacts._role_matches_lane("producer", "build", artifact))
        self.assertFalse(artifacts._role_matches_lane("producer", "test", artifact))

    def test_trusted_preflight_selects_and_derives_only_the_shared_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "user.email", "ci@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "user.name", "CI"], check=True)
            (checkout / "dist").mkdir()
            (checkout / "package.json").write_text(
                json.dumps({"scripts": {"build": "tsc", "test": "vitest", "lint": "eslint"}}),
                encoding="utf-8",
            )
            (checkout / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
            caller_workflow = checkout / ".github/workflows/node-ci.yml"
            caller_workflow.parent.mkdir(parents=True)
            caller_workflow_bytes = b"name: trusted caller\n"
            caller_workflow.write_bytes(caller_workflow_bytes)
            subprocess.run(
                ["git", "-C", str(checkout), "add", "package.json", "package-lock.json", ".github/workflows/node-ci.yml"],
                check=True,
            )
            subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True)
            commit = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, text=True, capture_output=True
            ).stdout.strip()
            trusted_base_commit = commit
            full = json.dumps(["build", {"script": "test", "requiresServices": False}], separators=(",", ":"))
            nonapp = json.dumps(["build", "lint"], separators=(",", ":"))
            inputs = {
                "secretless-ci-script-plan": full,
                "secretless-ci-non-app-script-plan": nonapp,
                "node-version": "24.1.0",
                "package-manager": "npm",
            }
            plan = {
                "version": 1,
                "pushBranches": ["main"],
                "secrets": ["NODE_AUTH_TOKEN"],
                "levels": [
                    {"id": "build", "needs": [], "events": ["pull_request"], "branches": ["main"], "inputs": inputs},
                    {"id": "test", "needs": [], "events": ["pull_request"], "branches": ["main"], "inputs": inputs},
                ],
                "buildArtifact": {"producer": "build", "consumers": ["test"], "paths": ["dist"]},
            }
            row = {
                "repository": "Verjson/app",
                "visibility": "private",
                "producer": "build",
                "consumers": ["test"],
                "buildScript": "build",
                "allowedPaths": ["dist"],
                "requiredPaths": ["dist"],
                "profileFiles": ["package.json", "package-lock.json"],
                "audienceApproved": True,
            }
            policy_table = {"version": 1, "repositories": {"Verjson/app": row}}

            def select(candidate_plan, *, run_attempt="1", mode_share_eligible=True, policy_override=None,
                       candidate_commit=None,
                       candidate_base_sha=None):
                return artifacts.select_preflight(
                    candidate_plan,
                    policy_table if policy_override is None else policy_override,
                    repository="Verjson/app",
                    private=True,
                    event_name="pull_request",
                    base_ref="main",
                    ref_name="feature",
                    commit=commit if candidate_commit is None else candidate_commit,
                    caller_workflow_sha="b" * 40,
                    caller_base_sha=commit if candidate_base_sha is None else candidate_base_sha,
                    workflow_sha="c" * 40,
                    runner_policy='["ubuntu-24.04"]',
                    runner_os="Linux",
                    runner_arch="X64",
                    runner_image_os="ubuntu24",
                    runner_image_version="20261001.1.0",
                    package_manager_version="10.8.2",
                    needs_results={},
                    checkout=checkout,
                    mode_share_eligible=mode_share_eligible,
                    run_attempt=run_attempt,
                )

            result = select(plan, mode_share_eligible=True)
            mutation_commands = (
                "jest --json --outputFile dist/report.json",
                "eslint --output-file dist/report.json",
                "eslint --cache --cache-location dist/.eslintcache",
                "playwright test -u",
                "ava --update-snapshots",
                "jest -iu",
            )
            for command in mutation_commands:
                with self.subTest(command=command):
                    (checkout / "package.json").write_text(
                        json.dumps({"scripts": {"build": "tsc", "test": command, "lint": "eslint"}}),
                        encoding="utf-8",
                    )
                    subprocess.run(
                        ["git", "-C", str(checkout), "add", "package.json"], check=True
                    )
                    subprocess.run(
                        ["git", "-C", str(checkout), "commit", "-qm", "add mutating check"],
                        check=True,
                    )
                    mutating_commit = subprocess.run(
                        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout.strip()
                    candidate_result = select(
                        plan,
                        candidate_commit=mutating_commit,
                        candidate_base_sha=mutating_commit,
                    )
                    self.assertFalse(candidate_result["share-selected"])
            (checkout / "package.json").write_text(
                json.dumps({"scripts": {"build": "tsc", "test": "vitest", "lint": "eslint"}}),
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "-C", str(checkout), "add", "package.json"], check=True
            )
            subprocess.run(
                ["git", "-C", str(checkout), "commit", "-qm", "restore baseline scripts"],
                check=True,
            )
            nested_plan = json.loads(json.dumps(plan))
            nested_plan["buildArtifact"]["paths"] = ["packages/widget/dist"]
            nested_row = json.loads(json.dumps(row))
            nested_row["allowedPaths"] = ["packages/widget/dist"]
            nested_row["requiredPaths"] = ["packages/widget/dist"]
            nested_result = select(
                nested_plan,
                policy_override={"version": 1, "repositories": {"Verjson/app": nested_row}},
            )
            self.assertFalse(nested_result["share-selected"])
            self.assertEqual(nested_result["reason"], "nested-export-roots-not-admitted")
            fork_fallback = select(plan, mode_share_eligible=False)
            self.assertFalse(fork_fallback["share-selected"])
            self.assertEqual(fork_fallback["reason"], "mode-gate-share-ineligible")
            self.assertEqual(fork_fallback["participants"], ["build", "test"])

            empty_policy_fallback = select(plan, policy_override={"version": 1, "repositories": {}})
            self.assertFalse(empty_policy_fallback["share-selected"])
            self.assertEqual(empty_policy_fallback["reason"], "policy-row-missing-or-invalid")
            self.assertEqual(empty_policy_fallback["participants"], ["build", "test"])
            filtered_plan = json.loads(json.dumps(plan))
            filtered_plan["levels"][1]["events"] = ["push"]
            filtered_fallback = select(
                filtered_plan, policy_override={"version": 1, "repositories": {}}
            )
            self.assertEqual(filtered_fallback["participants"], ["build", "test"])
            self.assertEqual(filtered_fallback["aggregate-lanes"], ["build"])
            fallback_receipts = {
                lane: {"result": "success"}
                for lane in empty_policy_fallback["participants"]
            }
            artifacts.verify_aggregate(
                share_selected=False,
                policy_selected=False,
                mode_gate_result="success",
                preflight_result="success",
                producer_result="success",
                upload_result="skipped",
                artifact_id="",
                artifact_digest="",
                expected_lanes=empty_policy_fallback["aggregate-lanes"],
                receipts=fallback_receipts,
                expected_profile="",
            identity={},
                attempt="1",
            )

            floating = json.loads(json.dumps(plan))
            floating["levels"][1]["inputs"]["node-version"] = "24"
            floating_result = artifacts.select_preflight(
                floating,
                {"version": 1, "repositories": {"Verjson/app": row}},
                repository="Verjson/app",
                private=True,
                event_name="pull_request",
                base_ref="main",
                ref_name="feature",
                commit=commit,
                caller_workflow_sha="b" * 40,
            caller_base_sha=commit,
                workflow_sha="c" * 40,
                runner_policy='["ubuntu-24.04"]',
                runner_os="Linux",
                runner_arch="X64",
                runner_image_os="ubuntu24",
                runner_image_version="20261001.1.0",
                package_manager_version="10.8.2",
                needs_results={},
                checkout=checkout,
            )
            self.assertTrue(result["share-selected"])
            caller_workflow.write_bytes(b"name: attacker-modified caller\n")
            subprocess.run(
                ["git", "-C", str(checkout), "add", ".github/workflows/node-ci.yml"], check=True
            )
            subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "modify caller"], check=True)
            modified_commit = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            modified_caller = select(
                plan, candidate_commit=modified_commit, candidate_base_sha=trusted_base_commit
            )
            self.assertFalse(modified_caller["share-selected"])
            self.assertEqual(modified_caller["reason"], "caller-workflow-changed-in-pull-request")
            merged_caller = select(
                plan, candidate_commit=modified_commit, candidate_base_sha=modified_commit
            )
            self.assertTrue(merged_caller["share-selected"], merged_caller)
            caller_workflow.write_bytes(caller_workflow_bytes)
            subprocess.run(
                ["git", "-C", str(checkout), "add", ".github/workflows/node-ci.yml"], check=True
            )
            subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "restore trusted caller"], check=True)
            commit = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            producer_plan = json.loads(result["producer-plan"])
            self.assertEqual(json.loads(producer_plan["full"]), [{"script": "build", "unsetEnv": [], "requiresServices": False}])
            consumer_plan = json.loads(result["consumer-plans"])["test"]
            self.assertEqual(json.loads(consumer_plan["full"])[0]["script"], "test")
            normalizer = artifacts._script_plan_module()
            for lane_plan in (producer_plan, consumer_plan):
                for encoded in lane_plan.values():
                    normalized = normalizer.parse_script_plan(encoded)
                    self.assertTrue(normalized)
                    self.assertTrue(all(set(json.loads(encoded)[0]) == {"script", "unsetEnv", "requiresServices"} for _ in (0,)))
            self.assertEqual(result["export-paths"], ["dist"])

            variant_mismatch = json.loads(json.dumps(plan))
            variant_mismatch["levels"][1]["inputs"]["secretless-ci-non-app-script-plan"] = json.dumps(
                [{"script": "build", "unsetEnv": ["CI"], "requiresServices": False}, "lint"]
            )
            self.assertEqual(select(variant_mismatch)["reason"], "participant-plan-ineligible")

            with tempfile.TemporaryDirectory() as temporary:
                policy_path = Path(temporary) / "policy.json"
                output_path = Path(temporary) / "github-output"
                policy_path.write_bytes(artifacts.canonical_json(policy_table))
                output_env = {
                    "BUILD_ARTIFACT_POLICY_FILE": str(policy_path),
                    "BUILD_ARTIFACT_PLAN_JSON": json.dumps(plan),
                    "BUILD_ARTIFACT_NEEDS_RESULTS": "{}",
                    "BUILD_ARTIFACT_PACKAGE_MANAGER_VERSION": "10.8.2",
                    "REPOSITORY_PRIVATE": "true",
                    "CALLER_COMMIT": commit,
            "CALLER_BASE_SHA": commit,
                    "CALLER_WORKFLOW_SHA": "b" * 40,
                    "REUSABLE_WORKFLOW_SHA": "c" * 40,
                    "RUNNER_POLICY": '["ubuntu-24.04"]',
                    "RUNNER_OS": "Linux",
                    "RUNNER_ARCH": "X64",
                    "ImageOS": "ubuntu24",
                    "ImageVersion": "20261001.1.0",
                    "GITHUB_REPOSITORY": "Verjson/app",
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_BASE_REF": "main",
                    "GITHUB_REF_NAME": "feature",
                    "GITHUB_RUN_ATTEMPT": "1",
                    "BUILD_ARTIFACT_MODE_SHARE_ELIGIBLE": "true",
                    "GITHUB_WORKSPACE": str(checkout),
                    "GITHUB_OUTPUT": str(output_path),
                }
                with patch.dict(os.environ, output_env):
                    self.assertEqual(artifacts.main(["preflight"]), 0)
                self.assertIn("package-manager-version<<", output_path.read_text(encoding="utf-8"))
                self.assertIn("\n10.8.2\n", output_path.read_text(encoding="utf-8"))

            package_path = checkout / "package.json"
            package_path.write_text(
                json.dumps({"scripts": {"build": "tsc", "test": "npm run build && vitest", "e2e": "playwright", "lint": "eslint"}}),
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(checkout), "add", "package.json"], check=True)
            subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "hidden test rebuild"], check=True)
            commit = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, text=True, capture_output=True
            ).stdout.strip()
            producer_hook_plan = json.loads(json.dumps(plan))
            producer_hook_plan["levels"][1]["inputs"]["secretless-ci-script-plan"] = json.dumps(["build", "e2e"])
            producer_hook_plan["levels"][1]["inputs"]["secretless-ci-non-app-script-plan"] = json.dumps(["build", "lint"])
            self.assertEqual(select(producer_hook_plan)["reason"], "participant-lifecycle-build-hook")
            self.assertEqual(select(plan)["reason"], "participant-lifecycle-build-hook")
            package_path.write_text(
                json.dumps({"scripts": {"build": "tsc", "test": "vitest", "lint": "eslint"}}),
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(checkout), "add", "package.json"], check=True)
            subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "restore test script"], check=True)
            commit = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, text=True, capture_output=True
            ).stdout.strip()

            env_profile = json.loads(json.dumps(plan))
            build_entry = {"script": "build", "unsetEnv": ["NODE_OPTIONS"], "requiresServices": False}
            test_entry = {"script": "test", "unsetEnv": [], "requiresServices": False}
            lint_entry = {"script": "lint", "unsetEnv": [], "requiresServices": False}
            for level in env_profile["levels"]:
                level["inputs"]["secretless-ci-script-plan"] = json.dumps([build_entry, test_entry])
                level["inputs"]["secretless-ci-non-app-script-plan"] = json.dumps([build_entry, lint_entry])
            env_result = select(env_profile)
            self.assertTrue(env_result["share-selected"], env_result)
            changed_build_env = json.loads(json.dumps(env_profile))
            changed_build_env["levels"][1]["inputs"]["secretless-ci-script-plan"] = json.dumps(
                [{"script": "build", "unsetEnv": ["CI"], "requiresServices": False}, test_entry]
            )
            changed_build_env["levels"][1]["inputs"]["secretless-ci-non-app-script-plan"] = json.dumps(
                [{"script": "build", "unsetEnv": ["CI"], "requiresServices": False}, lint_entry]
            )
            self.assertEqual(select(changed_build_env)["reason"], "build-entry-mismatch")
            with patch.object(artifacts, "git_profile_blobs", side_effect=artifacts.IntegrityError("trusted blob failure")):
                with self.assertRaises(artifacts.IntegrityError):
                    select(plan)
            self.assertFalse(floating_result["share-selected"])
            self.assertEqual(floating_result["reason"], "node-version-not-exact")
            versioned_plan = json.loads(json.dumps(plan))
            for level in versioned_plan["levels"]:
                level["inputs"]["node-version"] = "v24.1.0"
            versioned_result = select(versioned_plan)
            self.assertTrue(versioned_result["share-selected"], versioned_result)
            empty_plan = json.loads(json.dumps(plan))
            for level in empty_plan["levels"]:
                level["inputs"]["secretless-ci-script-plan"] = json.dumps(["build"])
                level["inputs"]["secretless-ci-non-app-script-plan"] = json.dumps(["build"])
            self.assertEqual(select(empty_plan)["reason"], "participant-plan-empty-after-build")
            retry = select(plan, run_attempt="2")
            self.assertFalse(retry["share-selected"])
            self.assertEqual(retry["reason"], "retry-per-lane")

            redirection_cases = {
                "dotted script names": {
                    "build": "tsc",
                    "build.prod": "npm run build",
                    "test": "npm run build.prod && vitest",
                    "lint": "eslint",
                },
                "quoted package manager": {
                    "build": "tsc",
                    "test": 'n"pm" run build',
                    "lint": "eslint",
                },
                "quoted script name": {
                    "build": "tsc",
                    "test": 'npm run b"uild"',
                    "lint": "eslint",
                },
                "absolute shell interpreter": {
                    "build": "tsc",
                    "test": "/bin/sh -c 'npm run build'",
                    "lint": "eslint",
                },
                "npm prefix option": {
                    "build": "tsc",
                    "test": "npm --prefix tools run verify && vitest",
                    "lint": "eslint",
                },
                "npm prefix after script": {
                    "build": "tsc",
                    "test": "npm run verify --prefix tools && vitest",
                    "verify": "eslint",
                    "lint": "eslint",
                },
                "npm workspace after script": {
                    "build": "tsc",
                    "test": "npm run verify --workspace tools && vitest",
                    "verify": "eslint",
                    "lint": "eslint",
                },
        "npm workspace shorthand after script": {
            "build": "tsc",
            "test": "npm run verify --ws && vitest",
            "verify": "eslint",
            "lint": "eslint",
        },
        "npm workspace option with one leading dash": {
            "build": "tsc",
            "test": "npm run verify -workspace=tools && vitest",
            "verify": "eslint",
            "lint": "eslint",
        },
        "npm workspace option with three leading dashes": {
            "build": "tsc",
            "test": "npm run verify ---workspace=tools && vitest",
            "verify": "eslint",
            "lint": "eslint",
        },
        "npm workspaces override": {
            "build": "tsc",
            "test": "npm run verify --no-workspaces=false && vitest",
            "verify": "eslint",
            "lint": "eslint",
        },
        "npm positional argument after script": {
            "build": "tsc",
            "test": "npm run verify extra && vitest",
            "verify": "eslint",
            "lint": "eslint",
        },
        "npm alternate user config after script": {
            "build": "tsc",
            "test": "npm run verify --userconfig=ci.rc && vitest",
            "verify": "eslint",
            "lint": "eslint",
        },
        "npm alternate global config after script": {
            "build": "tsc",
            "test": "npm run verify --globalconfig=ci.rc && vitest",
            "verify": "eslint",
            "lint": "eslint",
        },
            "npm script-shell override": {
                "build": "vite build",
                "test": "npm run lint --script-shell=./scripts/build",
                "lint": "eslint",
            },
        "npm script-shell option with one leading dash": {
            "build": "vite build",
            "test": "npm run lint -script-shell=/bin/true",
            "lint": "eslint",
        },
        "npm script-shell option with three leading dashes": {
            "build": "vite build",
            "test": "npm run lint ---script-shell=/bin/true",
            "lint": "eslint",
        },
        "nested npm forwarded tsc emission arguments": {
            "build": "vite build",
            "test": "npm run typecheck -- --noEmit false --outDir dist",
            "typecheck": "tsc --noEmit",
            "lint": "eslint",
        },
        "nested pnpm scripts": {
            "build": "vite build",
            "test": "pnpm run lint && vitest",
            "lint": "eslint",
        },
        "nested yarn scripts": {
            "build": "vite build",
            "test": "yarn run lint && vitest",
            "lint": "eslint",
        },
                "changed directory": {
                    "build": "tsc",
                    "test": "cd tools && npm run verify",
                    "verify": "eslint",
                    "lint": "eslint",
                },
                "wrapper with interpreter": {
                    "build": "tsc",
                    "test": "timeout 30 node ./scripts/test-helper.js",
                    "lint": "eslint",
                },
                "assigned interpreter": {
                    "build": "tsc",
                    "test": "NODE_ENV=test node ./scripts/test-helper.js",
                    "lint": "eslint",
                },
                "leading whitespace interpreter": {
                    "build": "tsc",
                    "test": " node ./scripts/test-helper.js",
                    "lint": "eslint",
                },
            "direct build tool": {
                "build": "vite build",
                "test": "vite build --outDir /tmp/rebuilt && vitest",
                "lint": "eslint",
            },
            "direct task runner": {
                "build": "vite build",
                "test": "turbo run build && vitest",
                "lint": "eslint",
            },
            "direct local executable": {
                "build": "vite build",
                "test": "./tools/rebuild && vitest",
                "lint": "eslint",
            },
            "newline build tool": {
                "build": "vite build",
                "test": "echo ok\nvite build --outDir /tmp/rebuilt",
                "lint": "eslint",
            },
            "newline task runner": {
                "build": "vite build",
                "test": "true\nmake build",
                "lint": "eslint",
            },
            "newline local executable": {
                "build": "vite build",
                "test": "true\n./tools/rebuild",
                "lint": "eslint",
            },
            "TypeScript noEmit overridden by separate value": {
                "build": "vite build",
                "test": "tsc --noEmit false --outDir /tmp/rebuilt",
                "lint": "eslint",
            },
            "TypeScript noEmit overridden by equals value": {
                "build": "vite build",
                "test": "tsc --noEmit=false --outDir /tmp/rebuilt",
                "lint": "eslint",
            },
            }
            tools_package = checkout / "tools" / "package.json"
            tools_package.parent.mkdir(exist_ok=True)
            tools_package.write_text(
                json.dumps({"scripts": {"verify": "npm run build"}}),
                encoding="utf-8",
            )
            for label, scripts in redirection_cases.items():
                with self.subTest(label=label):
                    package_path.write_text(json.dumps({"scripts": scripts}), encoding="utf-8")
                    subprocess.run(
                        ["git", "-C", str(checkout), "add", "package.json", "tools/package.json"],
                        check=True,
                    )
                    subprocess.run(
                        ["git", "-C", str(checkout), "commit", "-qm", f"fixture {label}"],
                        check=True,
                    )
                    commit = subprocess.run(
                        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout.strip()
                    selected = select(plan)
                    self.assertFalse(selected["share-selected"], selected)

            npmrc = checkout / ".npmrc"
            package_path.write_text(
                json.dumps({"scripts": {"build": "tsc", "test": "vitest", "lint": "eslint"}}),
                encoding="utf-8",
            )
            npmrc.write_text("@verjson:registry=https://npm.pkg.github.com\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(checkout), "add", "package.json", ".npmrc"], check=True)
            subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "safe registry config"], check=True)
            commit = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            self.assertTrue(select(plan)["share-selected"])

            for label, config in (
                ("plain workspace array", "workspace[]=tools\n"),
                ("quoted workspace array", '"workspace[]"=tools\n'),
                ("quoted workspace key", '"workspace"=tools\n'),
                ("quoted workspaces key", '"workspaces"=true\n'),
            ):
                with self.subTest(config=label):
                    npmrc.write_text(config, encoding="utf-8")
                    subprocess.run(["git", "-C", str(checkout), "add", ".npmrc"], check=True)
                    subprocess.run(["git", "-C", str(checkout), "commit", "-qm", label], check=True)
                    commit = subprocess.run(
                        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout.strip()
                    workspace_config_fallback = select(plan)
                    self.assertFalse(workspace_config_fallback["share-selected"])
                    self.assertEqual(workspace_config_fallback["reason"], "npm-workspace-config-not-admitted")


class ParticipantSchedulingTests(unittest.TestCase):
    def test_fallback_receipt_lanes_follow_event_branch_and_prerequisite_filters(self) -> None:
        plan = {
            "levels": [
                {"id": "setup", "needs": []},
                {
                    "id": "build",
                    "needs": [{"level": "setup", "condition": "success"}],
                    "events": ["pull_request"],
                    "branches": ["main"],
                },
                {
                    "id": "test",
                    "needs": [{"level": "setup", "condition": "completed"}],
                    "events": ["pull_request"],
                    "branches": ["main"],
                },
            ]
        }
        scheduled = artifacts._scheduled_participants(
            plan,
            ["build", "test"],
            event_name="pull_request",
            base_ref="main",
            ref_name="feature",
            needs_results={"setup": {"result": "failure"}},
        )
        self.assertEqual(scheduled, ["test"])


class ArtifactFilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "dist").mkdir()
        (self.workspace / "dist/app.js").write_bytes(b"built bytes")
        os.chmod(self.workspace / "dist/app.js", 0o644)
        self.identity = {
            "repository": "Verjson/app",
            "ref": "refs/heads/main",
            "commit": "a" * 40,
            "callerWorkflowSha": "b" * 40,
            "reusableWorkflowSha": "c" * 40,
            "runId": "123",
            "runAttempt": "1",
            "lane": "unit",
            "profile": "d" * 64,
            "commands": {"full": ["build"]},
            "inputs": {"node-version": "24"},
            "toolchain": {"node": "24.1.0"},
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_export_stops_directory_enumeration_at_the_entry_limit(self) -> None:
        class FakeEntry:
            def __init__(self, name: str) -> None:
                self.name = name

        class FakeScandir:
            def __init__(self) -> None:
                self.read_count = 0

            def __enter__(self) -> "FakeScandir":
                return self

            def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
                return None

            def __iter__(self):
                for index in range(artifacts.MAX_FILES + 100_000):
                    self.read_count += 1
                    yield FakeEntry(f"entry-{index}")

        directory_entries = FakeScandir()
        root_fd = artifacts._open_workspace(self.workspace)
        try:
            with patch("ci_build_artifacts.os.scandir", return_value=directory_entries):
                with self.assertRaisesRegex(artifacts.ArtifactError, "directory entry count limit"):
                    artifacts._list_files(root_fd, "dist")
        finally:
            os.close(root_fd)

        self.assertEqual(directory_entries.read_count, artifacts.MAX_FILES + 1)

    def test_participant_count_must_match_trusted_selection(self) -> None:
        self.assertEqual(artifacts._validate_participant_count(["build", "test"], "2"), 2)
        for participants, requested in (
            (["build", "test"], "1"),
            (["build", "build"], "2"),
            (["build"], "２"),
        ):
            with self.subTest(participants=participants, requested=requested):
                with self.assertRaises(artifacts.IntegrityError):
                    artifacts._validate_participant_count(participants, requested)

    def test_export_and_verify_extract_bind_exact_bytes_and_digest(self) -> None:
        artifact_file = self.root / "artifact.tar.gz"
        exported = artifacts.export_artifact(
            self.workspace,
            artifact_file,
            roots=("dist",),
            identity=self.identity,
            consumers=2,
        )
        digest = hashlib.sha256(artifact_file.read_bytes()).hexdigest()
        destination = self.root / "verified"
        verified = artifacts.verify_and_extract(
            artifact_file,
            destination,
            expected_digest=digest,
            artifact_id="101",
            expected_identity={key: value for key, value in self.identity.items() if key != "lane"},
            consumers=2,
        )
        self.assertEqual(verified["digest"], digest)
        self.assertEqual((destination / "dist/app.js").read_bytes(), b"built bytes")
        self.assertEqual((destination / "dist/app.js").stat().st_mode & 0o222, 0)
        self.assertLessEqual(exported["bytes"], artifacts.MAX_ARTIFACT_BYTES)

    def test_export_rejects_symlinks_and_hardlinks(self) -> None:
        (self.workspace / "dist/link").symlink_to("app.js")
        with self.assertRaises((artifacts.ArtifactError, OSError)):
            artifacts.export_artifact(
                self.workspace,
                self.root / "link.tar.gz",
                roots=("dist",),
                identity=self.identity,
                consumers=1,
            )
        (self.workspace / "dist/link").unlink()
        os.link(self.workspace / "dist/app.js", self.workspace / "dist/alias.js")
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.export_artifact(
                self.workspace,
                self.root / "hardlink.tar.gz",
                roots=("dist",),
                identity=self.identity,
                consumers=1,
            )

    def test_consumer_rejects_digest_mismatch_and_archive_traversal(self) -> None:
        artifact_file = self.root / "artifact.tar.gz"
        artifacts.export_artifact(
            self.workspace,
            artifact_file,
            roots=("dist",),
            identity=self.identity,
            consumers=1,
        )
        with self.assertRaises(artifacts.IntegrityError):
            artifacts.verify_and_extract(
                artifact_file,
                self.root / "wrong-digest",
                expected_digest="0" * 64,
                artifact_id="101",
                expected_identity={key: value for key, value in self.identity.items() if key != "lane"},
                consumers=1,
            )

    def test_consumer_streams_bounded_archive_members_without_getmembers(self) -> None:
        artifact_file = self.root / "artifact.tar.gz"
        artifacts.export_artifact(
            self.workspace,
            artifact_file,
            roots=("dist",),
            identity=self.identity,
            consumers=1,
        )
        destination = self.root / "streamed-out"
        digest = hashlib.sha256(artifact_file.read_bytes()).hexdigest()
        with patch.object(tarfile.TarFile, "getmembers", side_effect=AssertionError("unbounded member list")):
            verified = artifacts.verify_and_extract(
                artifact_file,
                destination,
                expected_digest=digest,
                artifact_id="101",
                expected_identity={key: value for key, value in self.identity.items() if key != "lane"},
                consumers=1,
            )
        self.assertEqual(verified["digest"], digest)
        self.assertEqual((destination / "dist/app.js").read_bytes(), b"built bytes")

    def test_exported_long_path_pax_header_remains_compatible(self) -> None:
        long_name = "x" * 120 + ".js"
        long_file = self.workspace / "dist" / long_name
        long_file.write_bytes(b"long path output")
        os.chmod(long_file, 0o644)
        artifact_file = self.root / "long-path.tar.gz"
        artifacts.export_artifact(
            self.workspace, artifact_file, roots=("dist",), identity=self.identity, consumers=1,
        )
        verified = artifacts.verify_and_extract(
            artifact_file, self.root / "long-path-out",
            expected_digest=hashlib.sha256(artifact_file.read_bytes()).hexdigest(),
            artifact_id="101",
            expected_identity={key: value for key, value in self.identity.items() if key != "lane"},
            consumers=1,
        )
        self.assertEqual((self.root / "long-path-out" / "dist" / long_name).read_bytes(), b"long path output")
        self.assertEqual(verified["digest"], hashlib.sha256(artifact_file.read_bytes()).hexdigest())

    def test_tar_path_capacity_is_an_eligibility_fallback(self) -> None:
        artifact_file = self.root / "unrepresentable-path.tar.gz"
        original_tobuf = tarfile.TarInfo.tobuf

        def reject_payload_header(info, *args, **kwargs):
            if info.name.startswith("payload/"):
                raise ValueError("simulated tar path capacity limit")
            return original_tobuf(info, *args, **kwargs)

        with patch.object(tarfile.TarInfo, "tobuf", reject_payload_header):
            with self.assertRaises(artifacts.ArtifactError):
                artifacts.export_artifact(
                    self.workspace, artifact_file, roots=("dist",), identity=self.identity, consumers=1,
                )
        self.assertFalse(artifact_file.exists())

    def test_oversized_tar_extensions_are_rejected_before_payload_read(self) -> None:
        for extension_type in (tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME):
            with self.subTest(extension_type=extension_type):
                archive_path = self.root / f"oversized-{extension_type.decode('ascii')}.tar.gz"
                info = tarfile.TarInfo("PaxHeaders/oversized")
                info.type = extension_type
                info.size = artifacts.MAX_TAR_EXTENSION_BYTES + 1
                info.mtime = 0
                archive_path.write_bytes(gzip.compress(info.tobuf()))
                digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
                requested_reads = []
                original_read = tarfile._Stream.read

                def record_read(stream, size):
                    requested_reads.append(size)
                    self.assertLessEqual(size, artifacts.MAX_TAR_EXTENSION_BYTES)
                    return original_read(stream, size)

                with patch.object(tarfile._Stream, "read", record_read):
                    with self.assertRaises(artifacts.ArtifactError):
                        artifacts.verify_and_extract(
                            archive_path, self.root / f"oversized-{extension_type.decode('ascii')}-out",
                            expected_digest=digest, artifact_id="101",
                            expected_identity={key: value for key, value in self.identity.items() if key != "lane"},
                            consumers=1,
                        )
                self.assertNotIn(info.size, requested_reads)
        valid_artifact = self.root / "valid.tar.gz"
        artifacts.export_artifact(
            self.workspace, valid_artifact, roots=("dist",), identity=self.identity, consumers=1
        )
        malicious = self.root / "malicious.tar.gz"
        with tarfile.open(valid_artifact, "r:gz") as valid_archive, tarfile.open(
            malicious, "w:gz"
        ) as archive:
            for member in valid_archive:
                if member.isfile():
                    with valid_archive.extractfile(member) as payload:
                        archive.addfile(member, payload)
                else:
                    archive.addfile(member)
            info = tarfile.TarInfo("payload/../escape")
            info.size = 1
            import io

            archive.addfile(info, io.BytesIO(b"x"))
        digest = hashlib.sha256(malicious.read_bytes()).hexdigest()
        with self.assertRaisesRegex(artifacts.ArtifactError, "path is invalid or too deep"):
            artifacts.verify_and_extract(
                malicious,
                self.root / "malicious-out",
                expected_digest=digest,
                artifact_id="101",
                expected_identity={key: value for key, value in self.identity.items() if key != "lane"},
                consumers=1,
            )


class ExportCommandFailureTests(unittest.TestCase):
    def test_export_validation_error_fails_instead_of_selecting_lane_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "github-output"
            command = [
                "export", "--workspace", temporary, "--output", str(Path(temporary) / "artifact.tar.gz"),
                "--roots-json", '["dist"]', "--identity-json", "{}", "--consumers", "1",
            ]
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch.object(
                artifacts, "export_artifact", side_effect=artifacts.ArtifactError("unsafe export")
            ):
                self.assertEqual(artifacts.main(command), 1)
            self.assertFalse(output.exists())


class ArtifactReceiptTests(unittest.TestCase):
    def test_aggregate_validates_exact_lane_receipts_and_command_digests(self) -> None:
        environment = {
            "GITHUB_REPOSITORY": "Verjson/app",
            "GITHUB_REF": 'refs/heads/quoted-"\\path',
            "CALLER_COMMIT": "b" * 40,
            "CALLER_WORKFLOW_SHA": "c" * 40,
            "REUSABLE_WORKFLOW_SHA": "d" * 40,
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
        }
        with patch.dict(os.environ, environment):
            identity = {key: value for key, value in artifacts.receipt_identity_from_environment().items() if key != "runAttempt"}
        manifest = {"files": [{"path": "dist/app.js", "size": 3, "mode": 0o644, "sha256": "e" * 64}]}
        needs = {}
        for lane in ("unit", "typecheck"):
            encoded, digest = artifacts.create_receipt(
                identity={**identity, "runAttempt": "1"}, lane=lane, profile="f" * 64,
                artifact_id="101", artifact_digest="a" * 64, verified_manifest=manifest,
                command_digest="b" * 64,
            )
            needs[lane] = {"result": "success", "outputs": {
                "build-artifact-receipt": encoded,
                "build-artifact-receipt-id": digest,
                "build-artifact-receipt-digest": digest,
                "build-artifact-command-digest": "b" * 64,
            }}
        receipts = artifacts.receipts_from_needs(needs, ["unit", "typecheck"])
        artifacts.verify_aggregate(
            share_selected=True, policy_selected=True, mode_gate_result="success", preflight_result="success",
            producer_result="success", artifact_id="101", artifact_digest="a" * 64,
            upload_result="success",
            expected_lanes=["unit", "typecheck"], receipts=receipts,
            expected_profile="f" * 64, identity=identity, attempt="1",
        )
        aggregate_environment = {
            **environment, "SHARE_SELECTED": "true", "MODE_GATE_RESULT": "success",
            "POLICY_SELECTED": "true",
            "PREFLIGHT_RESULT": "success", "PRODUCER_RESULT": "success",
            "UPLOAD_RESULT": "success",
            "ARTIFACT_ID": "101", "ARTIFACT_DIGEST": "a" * 64,
            "EXPECTED_LANES_JSON": json.dumps(["unit", "typecheck"]),
            "NEEDS_JSON": json.dumps(needs), "EXPECTED_PROFILE": "f" * 64,
            "EXPECTED_ATTEMPT": "1",
        }
        with patch.dict(os.environ, aggregate_environment), patch.object(sys, "argv", ["ci_build_artifacts.py", "aggregate"]):
            self.assertEqual(artifacts.main(), 0)
        receipts["unit"]["command-digest"] = "0" * 64
        with self.assertRaises(artifacts.IntegrityError):
            artifacts.verify_aggregate(
                share_selected=True, policy_selected=True, mode_gate_result="success", preflight_result="success",
                producer_result="success", artifact_id="101", artifact_digest="a" * 64,
                upload_result="success",
                expected_lanes=["unit", "typecheck"], receipts=receipts,
                expected_profile="f" * 64, identity=identity, attempt="1",
            )


class CompactReceiptTests(unittest.TestCase):
    def test_failed_policy_selected_producer_cannot_be_hidden_by_lane_fallback(self) -> None:
        with self.assertRaisesRegex(artifacts.IntegrityError, "policy-selected ArtifactSet producer"):
            artifacts.verify_aggregate(
                share_selected=False,
                policy_selected=True,
                mode_gate_result="success",
                preflight_result="success",
                producer_result="failure",
                upload_result="skipped",
                artifact_id="",
                artifact_digest="",
                expected_lanes=["unit"],
                receipts={"unit": {"result": "success"}},
                expected_profile="",
                identity={},
                attempt="1",
            )

    def test_policy_selected_upload_failure_fails_aggregate(self) -> None:
        with self.assertRaisesRegex(artifacts.IntegrityError, "upload did not succeed"):
            artifacts.verify_aggregate(
                share_selected=False,
                policy_selected=True,
                mode_gate_result="success",
                preflight_result="success",
                producer_result="success",
                upload_result="failure",
                artifact_id="",
                artifact_digest="",
                expected_lanes=["unit"],
                receipts={"unit": {"result": "success"}},
                expected_profile="",
                identity={},
                attempt="1",
            )

    def test_policy_selected_skipped_upload_cannot_fall_back(self) -> None:
        with self.assertRaisesRegex(artifacts.IntegrityError, "upload did not succeed"):
            artifacts.verify_aggregate(
                share_selected=False,
                policy_selected=True,
                mode_gate_result="success",
                preflight_result="success",
                producer_result="success",
                upload_result="skipped",
                artifact_id="",
                artifact_digest="",
                expected_lanes=["unit"],
                receipts={"unit": {"result": "success"}},
                expected_profile="",
                identity={},
                attempt="1",
            )

    def test_policy_selected_success_upload_requires_artifact_evidence(self) -> None:
        with self.assertRaisesRegex(artifacts.IntegrityError, "producer evidence is missing"):
            artifacts.verify_aggregate(
                share_selected=True,
                policy_selected=True,
                mode_gate_result="success",
                preflight_result="success",
                producer_result="success",
                upload_result="success",
                artifact_id="",
                artifact_digest="",
                expected_lanes=["unit"],
                receipts={"unit": {"result": "success"}},
                expected_profile="",
                identity={},
                attempt="1",
            )

    def test_fallback_checks_lane_results_without_requiring_receipts(self) -> None:
        artifacts.verify_aggregate(
            share_selected=False,
            policy_selected=False,
            mode_gate_result="success",
            preflight_result="success",
            producer_result="skipped",
            upload_result="skipped",
            artifact_id="",
            artifact_digest="",
            expected_lanes=["unit"],
            receipts={"unit": {"result": "success"}},
            expected_profile="",
            identity={},
            attempt="1",
        )

    def test_no_scheduled_participants_is_a_valid_no_share_result(self) -> None:
        artifacts.verify_aggregate(
            share_selected=False,
            policy_selected=False,
            mode_gate_result="success",
            preflight_result="success",
            producer_result="skipped",
            upload_result="skipped",
            artifact_id="",
            artifact_digest="",
            expected_lanes=[],
            receipts={},
            expected_profile="",
            identity={},
            attempt="1",
        )

    def test_retry_attempt_three_uses_lane_results_without_receipts(self) -> None:
        artifacts.verify_aggregate(
            share_selected=False,
            policy_selected=False,
            mode_gate_result="success",
            preflight_result="success",
            producer_result="skipped",
            upload_result="skipped",
            artifact_id="",
            artifact_digest="",
            expected_lanes=["prior", "rerun"],
            receipts={"prior": {"result": "success"}, "rerun": {"result": "success"}},
            expected_profile="",
            identity={},
            attempt="3",
        )

    def test_maximum_participant_receipts_fit_compact_needs_limit(self) -> None:
        identity = {
            "repository": "Verjson/example", "ref": "refs/heads/main", "commit": "b" * 40,
            "callerWorkflowSha": "c" * 40, "reusableWorkflowSha": "d" * 40, "runId": "123",
        }
        lanes = [f"lane-{index}" for index in range(16)]
        needs = {}
        for lane in lanes:
            receipt, digest = artifacts.create_receipt(
                identity={**identity, "runAttempt": "1"}, lane=lane, profile="",
                artifact_id=None, artifact_digest=None, verified_manifest=None,
                command_digest="b" * 64,
            )
            self.assertLess(len(receipt.encode("utf-8")), artifacts.MAX_RECEIPT_BYTES)
            needs[lane] = {"result": "success", "outputs": {
                "build-artifact-receipt": receipt,
                "build-artifact-receipt-id": digest,
                "build-artifact-receipt-digest": digest,
                "build-artifact-command-digest": "b" * 64,
            }}
        self.assertLess(len(json.dumps(needs).encode("utf-8")), artifacts.MAX_AGGREGATE_NEEDS_BYTES)
        artifacts.verify_aggregate(
            share_selected=False, mode_gate_result="success", preflight_result="success",
            policy_selected=False,
            producer_result="skipped", artifact_id="", artifact_digest="",
            upload_result="skipped",
            expected_lanes=lanes, receipts=artifacts.receipts_from_needs(needs, lanes),
            expected_profile="", identity=identity, attempt="1",
        )

    def test_retry_does_not_accept_current_attempt_artifact_claims(self) -> None:
        identity = {
            "repository": "Verjson/example", "ref": "refs/heads/main", "commit": "b" * 40,
            "callerWorkflowSha": "c" * 40, "reusableWorkflowSha": "d" * 40, "runId": "123",
        }
        encoded, digest = artifacts.create_receipt(
            identity={**identity, "runAttempt": "2"}, lane="unit", profile="",
            artifact_id="101", artifact_digest="a" * 64,
            verified_manifest={"files": []}, command_digest="b" * 64,
        )
        with self.assertRaises(artifacts.IntegrityError):
            artifacts.verify_aggregate(
                share_selected=True, policy_selected=True, mode_gate_result="success", preflight_result="success",
                producer_result="success", artifact_id="101", artifact_digest="a" * 64,
                upload_result="success",
                expected_lanes=["unit"], receipts={"unit": {
                    "result": "success", "receipt": encoded, "receipt-id": digest,
                    "receipt-digest": digest, "command-digest": "b" * 64,
                }}, expected_profile="", identity=identity, attempt="2",
            )


class RoleAuthorizationTests(unittest.TestCase):
    def test_direct_producer_and_consumer_roles_fail_with_empty_policy(self) -> None:
        root = Path(__file__).resolve().parents[2]
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, text=True, capture_output=True
        ).stdout.strip()
        plan = {
            "version": 1,
            "pushBranches": ["main"],
            "secrets": ["NODE_AUTH_TOKEN"],
            "levels": [
                {
                    "id": lane,
                    "needs": [],
                    "events": ["pull_request"],
                    "branches": ["main"],
                    "inputs": {"node-version": "24.1.0", "package-manager": "npm"},
                }
                for lane in ("build", "test")
            ],
            "buildArtifact": {"producer": "build", "consumers": ["test"], "paths": ["dist"]},
        }
        base = {
            "BUILD_ARTIFACT_AUTHORIZATION_PLAN_JSON": json.dumps(plan),
            "BUILD_ARTIFACT_POLICY_FILE": str(root / "config/ci-build-artifact-policy.json"),
            "BUILD_ARTIFACT_NEEDS_RESULTS": "{}",
            "BUILD_ARTIFACT_ROLE": "producer",
            "BUILD_ARTIFACT_LEVEL_ID": "build",
            "BUILD_ARTIFACT_PARTICIPANT_COUNT": "2",
            "BUILD_ARTIFACT_PLAN_JSON": "{}",
            "BUILD_ARTIFACT_CONSUMER_PLANS": "{}",
            "BUILD_ARTIFACT_PATHS_JSON": '["dist"]',
            "BUILD_ARTIFACT_RUNNER_IMAGE_JSON": '{"os":"ubuntu24","version":"20261001.1.0"}',
            "BUILD_ARTIFACT_PROFILE": "f" * 64,
            "BUILD_ARTIFACT_NODE_VERSION": "24.1.0",
            "BUILD_ARTIFACT_PACKAGE_MANAGER": "npm",
            "BUILD_ARTIFACT_PACKAGE_MANAGER_VERSION": "10.8.2",
            "BUILD_ARTIFACT_ID": "101",
            "BUILD_ARTIFACT_DIGEST": "a" * 64,
            "CALLER_COMMIT": commit,
            "CALLER_BASE_SHA": commit,
            "CALLER_WORKFLOW_SHA": "c" * 40,
            "REUSABLE_WORKFLOW_SHA": "d" * 40,
            "GITHUB_WORKSPACE": str(root),
            "GITHUB_REPOSITORY": "Verjson/app",
            "GITHUB_REF": "refs/pull/1/merge",
            "GITHUB_REF_NAME": "1/merge",
            "GITHUB_BASE_REF": "main",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "BUILD_ARTIFACT_HEAD_REPOSITORY": "Verjson/app",
            "SECRETLESS_PR": "true",
            "SECRETLESS_TRUSTED_REF": "false",
            "REPOSITORY_PRIVATE": "true",
            "RUNNER_POLICY": '["ubuntu-24.04"]',
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
            "ImageOS": "ubuntu24",
            "ImageVersion": "20261001.1.0",
        }
        for input_name, value in artifacts.effective_runtime_inputs(plan["levels"][0], "pull_request").items():
            base[artifacts._runtime_input_environment_name(input_name)] = json.dumps(value)
        original_run = subprocess.run

        def run_with_fixed_versions(args, *call_args, **call_kwargs):
            if args == ["npm", "--version"]:
                return subprocess.CompletedProcess(args, 0, "10.8.2\n", "")
            if args == ["node", "--version"]:
                return subprocess.CompletedProcess(args, 0, "v24.1.0\n", "")
            return original_run(args, *call_args, **call_kwargs)

        with patch("ci_build_artifacts.subprocess.run", side_effect=run_with_fixed_versions):
            for role, lane in (("producer", "build"), ("consumer", "test")):
                with self.subTest(role=role), patch.dict(
                    os.environ, {**base, "BUILD_ARTIFACT_ROLE": role, "BUILD_ARTIFACT_LEVEL_ID": lane}
                ), self.assertRaisesRegex(artifacts.IntegrityError, "lacks trusted policy selection"):
                    artifacts._authorize_role_from_environment()

            runtime_scope = artifacts._runtime_input_environment_name("scope")
            with patch.dict(os.environ, {**base, runtime_scope: json.dumps("@attacker")}, clear=True):
                with self.assertRaisesRegex(artifacts.IntegrityError, "effective workflow inputs differ"):
                    artifacts._authorize_role_from_environment()

            forged_plan = json.loads(json.dumps(plan))
            forged_plan["levels"][0]["inputs"]["scope"] = "@attacker"
            with patch.dict(
                os.environ,
                {
                    **base,
                    "BUILD_ARTIFACT_AUTHORIZATION_PLAN_JSON": json.dumps(forged_plan),
                    "BUILD_ARTIFACT_PLAN_JSON": json.dumps({"forged": "roots"}),
                },
                clear=True,
            ):
                with self.assertRaisesRegex(artifacts.IntegrityError, "effective workflow inputs differ"):
                    artifacts._authorize_role_from_environment()
            consumer_plans = {
                "build": {"full": '[{"script":"test"}]', "nonapp": '[{"script":"lint"}]'},
                "test": {"full": '[{"script":"test"}]', "nonapp": '[{"script":"lint"}]'},
            }
            selected = {
                "share-selected": True,
            "participants": ["build", "test"],
                "profile": "f" * 64,
                "export-paths": ["dist"],
                "runner-image": '{"os":"ubuntu24","version":"20261001.1.0"}',
                "producer-plan": '{"full":"[{\\"script\\":\\"build\\"}]","nonapp":"[{\\"script\\":\\"build\\"}]"}',
                "consumer-plans": json.dumps(consumer_plans, separators=(",", ":")),
            }
        versioned_plan = json.loads(json.dumps(plan))
        for level in versioned_plan["levels"]:
            level["inputs"]["node-version"] = "v24.1.0"
        source_lane_consumer = {
            **base,
            "BUILD_ARTIFACT_AUTHORIZATION_PLAN_JSON": json.dumps(versioned_plan),
            artifacts._runtime_input_environment_name("node-version"): json.dumps("v24.1.0"),
            "BUILD_ARTIFACT_ROLE": "consumer",
            "BUILD_ARTIFACT_LEVEL_ID": "build",
            "BUILD_ARTIFACT_CONSUMER_PLANS": selected["consumer-plans"],
        }
        with patch.object(artifacts, "select_preflight", return_value=selected), patch(
            "ci_build_artifacts.subprocess.run", side_effect=run_with_fixed_versions
        ), patch.dict(
                os.environ, source_lane_consumer
        ):
            artifacts._authorize_role_from_environment()
            with self.subTest(mode="missing"), patch.dict(
                os.environ, {**base, "SECRETLESS_PR": "false", "SECRETLESS_TRUSTED_REF": "false"}
            ), self.assertRaises(artifacts.ArtifactError):
                artifacts._authorize_role_from_environment()
            extra_secret_plan = json.loads(json.dumps(plan))
            extra_secret_plan["secrets"].append("submodules-token")
            with self.subTest(secret="extra"), patch.dict(
                os.environ,
                {**base, "BUILD_ARTIFACT_AUTHORIZATION_PLAN_JSON": json.dumps(extra_secret_plan)},
            ), self.assertRaisesRegex(artifacts.IntegrityError, "unsupported secret mapping"):
                artifacts._authorize_role_from_environment()
            internal_input_plan = json.loads(json.dumps(plan))
            internal_input_plan["levels"][0]["inputs"]["build-artifact-role"] = "producer"
            with self.subTest(input="reserved"), patch.dict(
                os.environ,
                {**base, "BUILD_ARTIFACT_AUTHORIZATION_PLAN_JSON": json.dumps(internal_input_plan)},
            ), self.assertRaisesRegex(artifacts.IntegrityError, "reserved internal inputs"):
                artifacts._authorize_role_from_environment()

    def test_both_workflows_reject_unknown_roles_and_guard_acquisition(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for filename in ("node-ci.yml", "node-ci-protected.yml"):
            with self.subTest(workflow=filename):
                workflow = yaml.safe_load((root / ".github/workflows" / filename).read_text())
                eligibility = workflow["jobs"]["eligibility"]
                role_guard = next(
                    step for step in eligibility["steps"]
                    if step.get("name") == "Reject unknown reusable build-artifact roles"
                )
                rejected = subprocess.run(
                    ["bash", "-c", role_guard["run"]],
                    env={**os.environ, "BUILD_ARTIFACT_ROLE": "unknown"},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(rejected.returncode, 0)
                build_test = workflow["jobs"]["build-test"]
                acquisition_guard = next(
                    step for step in build_test["steps"]
                    if step.get("name") == "Require completed secretless dependency acquisition"
                )
                self.assertIn("needs.acquire-secretless-dependencies.result", acquisition_guard["if"])
                guard_run = subprocess.run(
                    ["bash", "-c", acquisition_guard["run"]],
                    env={**os.environ, "ELIGIBILITY_RESULT": "success", "ACQUISITION_RESULT": "failure"},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(guard_run.returncode, 0)
                if filename == "node-ci-protected.yml":
                    self.assertNotIn("secretless-pr", acquisition_guard["if"])
                else:
                    self.assertIn("inputs.build-artifact-role == 'producer'", acquisition_guard["if"])
                acquire_if = workflow["jobs"]["acquire-secretless-dependencies"]["if"]
                self.assertIn("producer", acquire_if)
                self.assertIn("consumer", acquire_if)
                if filename == "node-ci.yml":
                    self.assertIn("inputs.secretless-pr || inputs.secretless-trusted-ref", acquire_if)
                steps = build_test["steps"]
                authorization = next(
                    i for i, step in enumerate(steps)
                    if step.get("name") == "Authorize producer or consumer from immutable policy"
                )
                download = next(
                    i for i, step in enumerate(steps)
                    if step.get("name") == "Download exact same-run ArtifactSet"
                )
                upload = next(
                    i for i, step in enumerate(steps)
                    if step.get("name") == "Upload one-day same-run ArtifactSet"
                )
                self.assertLess(authorization, download)
                self.assertLess(authorization, upload)
                for earlier_candidate_step in (
                    "Start database service",
                    "Install schema submodule deps",
                    "Rebuild exact approved lifecycle packages without credentials",
                ):
                    step_index = next(
                        (i for i, step in enumerate(steps) if step.get("name") == earlier_candidate_step),
                        None,
                    )
                    if step_index is not None:
                        self.assertLess(authorization, step_index)
                candidate_index = next(
                    i for i, step in enumerate(steps)
                    if step.get("name") == "Run exact credentialless consumer script plan"
                )
                self.assertLess(authorization, candidate_index)
                self.assertIn("job)).workflow_sha", steps[authorization]["env"]["JOB_WORKFLOW_SHA"])
                self.assertIn("authorization-plan-json", steps[authorization]["env"]["BUILD_ARTIFACT_AUTHORIZATION_PLAN_JSON"])
                candidate = next(
                    step for step in steps
                    if step.get("name") == "Run exact credentialless consumer script plan"
                )
                candidate_runtime = (
                    __import__("inspect").getsource(artifacts.run_protected_candidate_plan)
                    if filename == "node-ci-protected.yml"
                    else candidate["run"]
                )
                unset_at = candidate_runtime.index("for env_name in unset_env:")
                role_assignments = [
                    candidate_runtime.find(needle)
                    for needle in (
                        '"BUILD_ARTIFACT_ROLE": os.environ.get("BUILD_ARTIFACT_ROLE", "")',
                        'script_env["BUILD_ARTIFACT_ROLE"] = os.environ.get("BUILD_ARTIFACT_ROLE", "")',
                    )
                ]
                role_at = min(index for index in role_assignments if index >= 0)
                self.assertLess(unset_at, role_at)
                candidate_environment = {"BUILD_ARTIFACT_ROLE": "caller-value"}
                for env_name in ["BUILD_ARTIFACT_ROLE"]:
                    candidate_environment.pop(env_name, None)
                candidate_environment.update({"BUILD_ARTIFACT_ROLE": "consumer"})
                self.assertEqual(candidate_environment["BUILD_ARTIFACT_ROLE"], "consumer")


if __name__ == "__main__":
    unittest.main()
