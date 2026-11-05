#!/usr/bin/env python3

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "scripts/container_private_dependencies.py"
SPEC = importlib.util.spec_from_file_location("container_private_dependencies", MODULE)
assert SPEC and SPEC.loader
contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = contract
SPEC.loader.exec_module(contract)

INTEGRITY = "sha512-" + "A" * 86 + "=="


def refused(reason):
    return 1, f"container dependency acquisition rejected: {reason}\n"


def pnpm_lock():
    return {
        "lockfileVersion": "9.0",
        "packages": {
            "@tequityapp/schema@1.2.3": {
                "resolution": {
                    "integrity": INTEGRITY,
                    "tarball": "https://npm.pkg.github.com/download/@tequityapp/schema/1.2.3/hash",
                }
            },
            "@verjson/authz@2.0.0(peer@1.0.0)": {
                "resolution": {
                    "integrity": INTEGRITY,
                    "tarball": "https://npm.pkg.github.com/download/@verjson/authz/2.0.0/hash",
                }
            },
            "zod@4.0.0": {"resolution": {"integrity": INTEGRITY}},
        },
    }


class PnpmCandidateTests(unittest.TestCase):
    def test_accepts_exact_multi_scope_private_set(self):
        plan = contract.build_pnpm_plan(
            pnpm_lock(), ["@tequityapp/schema", "@verjson/authz"]
        )
        self.assertEqual([item["private"] for item in plan], [True, True])

    def test_rejects_alias_missing_tarball_wrong_scope_and_malformed_peer_context(self):
        candidates = []
        alias = pnpm_lock()
        alias["packages"]["@tequityapp/schema@npm:@attacker/pkg@1.2.3"] = alias["packages"].pop("@tequityapp/schema@1.2.3")
        candidates.append((alias, "invalid or aliased pnpm package key: @tequityapp/schema@npm:@attacker/pkg@1.2.3"))
        missing = pnpm_lock()
        del missing["packages"]["@tequityapp/schema@1.2.3"]["resolution"]["tarball"]
        candidates.append((missing, "approved private package @tequityapp/schema lacks an exact tarball URL"))
        wrong = pnpm_lock()
        wrong["packages"]["@tequityapp/schema@1.2.3"]["resolution"]["tarball"] = "https://npm.pkg.github.com/download/@attacker/schema/1.2.3/hash"
        candidates.append((wrong, "unapproved private package: @attacker/schema"))
        peer = pnpm_lock()
        peer["packages"]["@verjson/authz@2.0.0(peer@1.0.0"] = peer["packages"].pop("@verjson/authz@2.0.0(peer@1.0.0)")
        candidates.append((peer, "invalid or aliased pnpm package key: @verjson/authz@2.0.0(peer@1.0.0"))
        for candidate, reason in candidates:
            with self.subTest(candidate=candidate), self.assertRaisesRegex(contract.DependencyError, "^" + re.escape(reason) + "$"):
                contract.build_pnpm_plan(candidate, ["@tequityapp/schema", "@verjson/authz"])

    def test_rejects_an_unbalanced_peer_context_the_key_pattern_admits(self):
        # The key pattern only brackets the peer context with its first "(" and
        # last ")", so these keys pass it and reach the balance check. The first
        # closes below depth zero and then rebalances, so only the in-loop check
        # refuses it; the second never closes, so only the final check does.
        for key in ("@verjson/authz@2.0.0(peer@1.0.0))((other@1.0.0)", "@verjson/authz@2.0.0((peer@1.0.0)"):
            lock = pnpm_lock()
            lock["packages"][key] = lock["packages"].pop("@verjson/authz@2.0.0(peer@1.0.0)")
            with self.subTest(key=key), self.assertRaisesRegex(
                contract.DependencyError, "^" + re.escape(f"unbalanced pnpm peer context: {key}") + "$"
            ):
                contract.build_pnpm_plan(lock, ["@tequityapp/schema", "@verjson/authz"])

    def test_workflows_bind_reviewed_manager_lock_and_credentials_to_acquisition(self):
        for name in ("container-candidate.yml", "container-candidate-publish.yml"):
            workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
            prepare = workflow["jobs"]["prepare"]
            if name == "container-candidate.yml":
                self.assertNotIn("acquire-private-node-dependencies", workflow["jobs"])
                self.assertNotIn("NODE_AUTH_TOKEN", yaml.safe_dump(workflow["jobs"]["prepare"]))
                continue
            acquire = workflow["jobs"]["acquire-private-node-dependencies"]
            self.assertEqual(prepare["outputs"]["package-manager"], "${{ steps.config.outputs.package-manager }}")
            script = next(step["run"] for step in acquire["steps"] if step.get("name", "").startswith("Acquire exact"))
            self.assertIn('[ "$base_package_manager" = "$PACKAGE_MANAGER" ]', script)
            self.assertIn("corepack pnpm install --frozen-lockfile --ignore-scripts", script)
            self.assertNotIn("pnpm run", script)
            self.assertNotIn("--dangerously-allow-all-builds", script)
            self.assertIn("env -i", script)
            self.assertIn("validator_args+=(--manifest package.json --project-root .)", script)
            self.assertIn('validator_args+=(--reviewed-manifest "$base_manifest")', script)
            self.assertIn('[ "$base_package_manager_spec" = "$head_package_manager_spec" ]', script)
            scopes = "jq -r '.[] | split(\"/\")[0]' <<<\"$APPROVED_PRIVATE_PACKAGES\""
            self.assertIn(scopes, script)
            acquire_text = yaml.safe_dump(acquire)
            self.assertEqual(acquire_text.count("NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}"), 1)
            for job_name, job in workflow["jobs"].items():
                if job_name == "acquire-private-node-dependencies":
                    continue
                self.assertNotIn("secrets.NODE_AUTH_TOKEN", yaml.safe_dump(job))

    def test_cli_rejects_unpinned_manager_and_unsafe_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / "pnpm-lock.yaml"
            manifest_path = root / "package.json"
            lock_path.write_text(yaml.safe_dump(pnpm_lock()), encoding="utf-8")
            manifest_path.write_text(json.dumps({
                "packageManager": "pnpm@11.22.0+sha512." + "a" * 128
            }), encoding="utf-8")
            command = [sys.executable, str(MODULE), "--package-manager", "pnpm",
                       "--lock", str(lock_path), "--manifest", str(manifest_path),
                       "--project-root", str(root),
                       "--approved", '["@tequityapp/schema","@verjson/authz"]']
            self.assertEqual(subprocess.run(command, check=False).returncode, 0)
            manifest_path.write_text('{"packageManager":"pnpm@11.22.0"}', encoding="utf-8")
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((result.returncode, result.stderr), refused("pnpm requires an integrity-pinned packageManager field"))
            manifest_path.write_text(json.dumps({
                "packageManager": "pnpm@11.22.0+sha512." + "a" * 128
            }), encoding="utf-8")
            lock_path.write_text("x: &unsafe value\ny: *unsafe\n", encoding="utf-8")
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((result.returncode, result.stderr), refused("pnpm lock rejects YAML aliases, anchors, and tags"))

    def test_cli_rejects_hooks_config_dependencies_and_head_only_manager_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / "pnpm-lock.yaml"
            manifest_path = root / "package.json"
            reviewed_path = root / "reviewed-package.json"
            exact_spec = "pnpm@11.22.0+sha512." + "a" * 128
            lock_path.write_text(yaml.safe_dump(pnpm_lock()), encoding="utf-8")
            manifest_path.write_text(json.dumps({"packageManager": exact_spec}), encoding="utf-8")
            reviewed_path.write_text(json.dumps({"packageManager": exact_spec}), encoding="utf-8")
            command = [sys.executable, str(MODULE), "--package-manager", "pnpm",
                       "--lock", str(lock_path), "--manifest", str(manifest_path),
                       "--reviewed-manifest", str(reviewed_path), "--project-root", str(root),
                       "--approved", '["@tequityapp/schema","@verjson/authz"]']

            hook = root / ".pnpmfile.cjs"
            hook.write_text("throw new Error(process.env.NODE_AUTH_TOKEN)", encoding="utf-8")
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((result.returncode, result.stderr), refused("pnpm hook file is forbidden: .pnpmfile.cjs"))
            hook.unlink()

            workspace = root / "pnpm-workspace.yaml"
            workspace.write_text("configDependencies:\n  '@attacker/config': 1.0.0\n", encoding="utf-8")
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((result.returncode, result.stderr), refused(
                "pnpm executable configuration is forbidden in pnpm-workspace.yaml: configDependencies"))
            workspace.unlink()

            manifest_path.write_text(json.dumps({
                "packageManager": exact_spec,
                "pnpm": {"pnpmfile": "attacker.cjs"},
            }), encoding="utf-8")
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((result.returncode, result.stderr), refused("pnpm executable configuration is forbidden in package.json.pnpm: pnpmfile"))

            manifest_path.write_text(json.dumps({
                "packageManager": "pnpm@11.23.0+sha512." + "b" * 128
            }), encoding="utf-8")
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((result.returncode, result.stderr), refused("pnpm packageManager spec differs from the reviewed base"))

    def test_cli_rejects_executable_dependencies_anywhere_in_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / "pnpm-lock.yaml"
            manifest_path = root / "package.json"
            manifest_path.write_text(json.dumps({
                "packageManager": "pnpm@11.22.0+sha512." + "a" * 128
            }), encoding="utf-8")
            command = [sys.executable, str(MODULE), "--package-manager", "pnpm",
                       "--lock", str(lock_path), "--manifest", str(manifest_path),
                       "--project-root", str(root),
                       "--approved", '["@tequityapp/schema","@verjson/authz"]']

            for field in ("configDependencies", "packageManagerDependencies"):
                with self.subTest(field=field):
                    lock = pnpm_lock()
                    lock["packages"]["zod@4.0.0"]["nested"] = {
                        field: {"@attacker/config": "1.0.0"}
                    }
                    lock_path.write_text(yaml.safe_dump(lock), encoding="utf-8")
                    result = subprocess.run(command, check=False, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(field, result.stderr)
                    self.assertEqual((result.returncode, result.stderr), refused(
                        f"pnpm executable configuration is forbidden in pnpm-lock.yaml.packages.zod@4.0.0.nested: {field}"))


if __name__ == "__main__":
    unittest.main()
