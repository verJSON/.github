#!/usr/bin/env python3
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/node-ci-protected.yml"
GENERATOR = ROOT / "scripts/gen-node-ci-protected.py"
BASE_SHA = "b" * 40
HEAD_SHA = "a" * 40


def load_generator():
    spec = importlib.util.spec_from_file_location("gen_node_ci_protected", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def step(name):
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in document["jobs"].values():
        for candidate in job.get("steps", []):
            if candidate.get("name") == name:
                return candidate
    raise AssertionError(f"workflow step not found: {name}")


def run_resolver(
    declaration, *, allow_prerelease=False, recovery_version=None,
    head_version="3.1.0", changed_files=None, pull_head_sha=HEAD_SHA,
    return_cache=False, registry_failure=False,
):
    resolver = step("Resolve protected type-surface baseline from the pull-request base")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        declaration_file = root / "declaration.json"
        declaration_file.write_text(declaration, encoding="utf-8")
        head_declaration = root / "head-declaration.json"
        head_declaration.write_text(
            json.dumps({"package": "@verjson/authn", "version": head_version,
                        "script": "test:type-surface-compatibility"}),
            encoding="utf-8",
        )
        pull_record = root / "pull.json"
        pull_record.write_text(json.dumps({
            "state": "open",
            "base": {"sha": BASE_SHA},
            "head": {"sha": pull_head_sha, "repo": {"full_name": "Verjson/verjson-authn"}},
            "changed_files": 2,
        }), encoding="utf-8")
        files_record = root / "files.json"
        files_record.write_text(json.dumps(changed_files if changed_files is not None else [
            {"filename": ".github/ci/type-surface-baseline.json", "status": "modified"},
            {"filename": "NEXT/2026-11-13-issue-2066-baseline-recovery.md", "status": "added"},
        ]), encoding="utf-8")
        log = root / "gh.log"
        fake_gh = bin_dir / "gh"
        fake_gh.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$*\" >> \"$GH_LOG\"\n"
            "case \"$*\" in\n"
            "  *'/pulls/42/files?per_page=100'*) cat \"$FILES_RECORD\" ;;\n"
            "  *'/pulls/42'*) cat \"$PULL_RECORD\" ;;\n"
            "  *\"/contents/.github/ci/type-surface-baseline.json?ref=$HEAD_SHA\"*) cat \"$HEAD_DECLARATION\" ;;\n"
            "  *\"/contents/.github/ci/type-surface-baseline.json?ref=$BASE_SHA\"*) cat \"$DECLARATION_FILE\" ;;\n"
            "  *) exit 91 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake_gh.chmod(0o755)
        fake_npm = bin_dir / "npm"
        fake_npm.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"${NPM_CONFIG_CACHE:-}\" > \"$NPM_CACHE_LOG\"\n"
            "if [ -n \"${NPM_CONFIG_CACHE:-}\" ]; then\n"
            "  mkdir -p \"$NPM_CONFIG_CACHE\"\n"
            "  printf '%s\\n' 'private registry metadata' > \"$NPM_CONFIG_CACHE/metadata\"\n"
            "fi\n"
            "[ \"${REGISTRY_FAILURE:-false}\" = false ] || exit 90\n"
            "case \"$*\" in\n"
            "  'view @verjson/authn versions --json') printf '%s\\n' \"$AVAILABLE_VERSIONS\" ;;\n"
            "  *) exit 91 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake_npm.chmod(0o755)
        output = root / "output"
        environment = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ALLOW_PRERELEASE": "true" if allow_prerelease else "false",
            "BASE_SHA": BASE_SHA,
            "HEAD_SHA": HEAD_SHA,
            "DECLARATION_FILE": str(declaration_file),
            "HEAD_DECLARATION": str(head_declaration),
            "PULL_RECORD": str(pull_record),
            "FILES_RECORD": str(files_record),
            "AVAILABLE_VERSIONS": json.dumps([
                recovery_version or json.loads(declaration).get("version", "3.0.0")
            ]) if declaration.startswith('{"package"') else json.dumps(["3.0.0"]),
            "NODE_AUTH_TOKEN": "test-package-token",
            "NPM_CACHE_LOG": str(root / "npm-cache.log"),
            "NPM_CONFIG_CACHE": "",
            "REGISTRY_FAILURE": "true" if registry_failure else "false",
            "EXPECTED_HEAD_SHA": HEAD_SHA,
            "EXPECTED_BASE_SHA": BASE_SHA,
            "DECLARATION_PATH": ".github/ci/type-surface-baseline.json",
            "EXPECTED_PACKAGE": "@verjson/authn",
            "EXPECTED_SCRIPT": "test:type-surface-compatibility",
            "GH_LOG": str(log),
            "GITHUB_OUTPUT": str(output),
            "PULL_REQUEST_NUMBER": "42",
            "REPOSITORY": "Verjson/verjson-authn",
            "RUN_ATTEMPT": "1",
            "RUN_ID": "1001",
            "RUNNER_TEMP": str(root),
        }
        result = subprocess.run(
            ["bash", "-c", resolver["run"]],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        output_text = output.read_text(encoding="utf-8") if output.exists() else ""
        receipt_text = ""
        if output_text:
            values = dict(line.split("=", 1) for line in output_text.splitlines())
            receipt_text = Path(values["receipt-path"]).read_text(encoding="utf-8")
        if return_cache:
            cache_path = (root / "npm-cache.log").read_text(encoding="utf-8").strip()
            return result, calls, output_text, receipt_text, cache_path, Path(cache_path).exists()
        return result, calls, output_text, receipt_text


def run_acquisition(request, *, allow_prerelease):
    acquisition = step("Resolve approved compatibility ranges without lifecycle execution")
    version = "3.0.0-rc.1"
    package = "@verjson/authn"
    tarball = f"https://npm.pkg.github.com/download/{package}/{version}/archive"
    digest = hashlib.sha512(b"prerelease artifact").digest()
    integrity = "sha512-" + base64.b64encode(digest).decode("ascii")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        log = root / "npm.log"
        fake_npm = bin_dir / "npm"
        fake_npm.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$*\" >> \"$NPM_LOG\"\n"
            "case \"$*\" in\n"
            f"  'view {package} versions --json') "
            f"printf '%s\\n' '[\"{version}\"]' ;;\n"
            f"  'view --json {package}@{version} name version dist.integrity dist.tarball') "
            f"printf '%s\\n' '{{\"name\":\"{package}\",\"version\":\"{version}\","
            f"\"dist.integrity\":\"{integrity}\",\"dist.tarball\":\"{tarball}\"}}' ;;\n"
            "  *) exit 91 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake_npm.chmod(0o755)

        entries = root / "private-entries"
        entries.write_text(f"{tarball}\t{digest.hex()}\n", encoding="utf-8")
        provenance = root / "compatibility" / "provenance.json"
        environment = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ALLOW_PRERELEASE": "true" if allow_prerelease else "false",
            "APPROVED_INTERNAL_SCOPES": "@verjson",
            "COMPATIBILITY_PROVENANCE": str(provenance),
            "COMPATIBILITY_RANGES": request,
            "NODE_AUTH_TOKEN": "test-package-token",
            "NPM_CONFIG_GLOBALCONFIG": str(root / "global.npmrc"),
            "NPM_CONFIG_USERCONFIG": str(root / "user.npmrc"),
            "NPM_LOG": str(log),
            "PRIVATE_CACHE_ENTRIES": str(entries),
            "PROTECTED_BASELINE_RECEIPT_PATH": "",
        }
        result = subprocess.run(
            ["bash", "-c", acquisition["run"]],
            cwd=root,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        provenance_text = (
            provenance.read_text(encoding="utf-8") if provenance.exists() else ""
        )
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        expected_lane = {
            "index": 0,
            "package": package,
            "range": version,
            "script": "test:type-surface-compatibility",
            "version": version,
            "integrity": integrity,
            "tarball": tarball,
            "sha512": digest.hex(),
        }
        return result, provenance_text, calls, expected_lane


class ProtectedBaselineTest(unittest.TestCase):
    def test_registry_lookup_uses_and_removes_a_job_scoped_cache(self):
        result, _, _, _, cache_path, cache_exists = run_resolver(
            '{"package":"@verjson/authn","version":"3.0.0",'
            '"script":"test:type-surface-compatibility"}',
            return_cache=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(cache_path.endswith("/protected-baseline-registry-1001-1/cache"))
        self.assertFalse(cache_exists)

    def test_failed_registry_lookup_removes_its_job_scoped_cache(self):
        result, _, output_text, _, cache_path, cache_exists = run_resolver(
            '{"package":"@verjson/authn","version":"3.0.0",'
            '"script":"test:type-surface-compatibility"}',
            return_cache=True, registry_failure=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "protected baseline registry lookup failed\n")
        self.assertTrue(cache_path.endswith("/protected-baseline-registry-1001-1/cache"))
        self.assertFalse(cache_exists)
        self.assertEqual(output_text, "")

    def test_registry_recovery_runs_after_pinned_node_and_npm_configuration_guard(self):
        steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["acquire-secretless-dependencies"]["steps"]
        names = [candidate.get("name", candidate.get("uses", "")) for candidate in steps]
        self.assertLess(names.index("Reject consumer-controlled npm configuration"),
                        next(index for index, name in enumerate(names) if name.startswith("actions/setup-node@")))
        self.assertLess(next(index for index, name in enumerate(names) if name.startswith("actions/setup-node@")),
                        names.index("Resolve protected type-surface baseline from the pull-request base"))
        self.assertLess(names.index("Resolve protected type-surface baseline from the pull-request base"),
                        names.index("Validate approved internal dependency lock"))

    def test_missing_base_release_uses_newer_head_declaration_for_recovery(self):
        result, calls, output_text, receipt_text = run_resolver(
            '{"package":"@verjson/authn","version":"3.0.0",'
            '"script":"test:type-surface-compatibility"}',
            recovery_version="3.1.0",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(line.split("=", 1) for line in output_text.splitlines())
        self.assertEqual(
            json.loads(values["compatibility-ranges"])["ranges"], ["3.1.0"]
        )
        self.assertIn(f"ref={'a' * 40}", "\n".join(calls))
        self.assertEqual(json.loads(receipt_text)["version"], "3.1.0")

    def test_recovery_receipt_binds_the_verified_replacement_artifact(self):
        result, _, output_text, receipt_text = run_resolver(
            '{"package":"@verjson/authn","version":"3.0.0",'
            '"script":"test:type-surface-compatibility"}',
            recovery_version="3.1.0",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        request = json.loads(dict(line.split("=", 1) for line in output_text.splitlines())["compatibility-ranges"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt_path = root / "receipt.json"
            receipt_path.write_text(receipt_text, encoding="utf-8")
            provenance_path = root / "provenance.json"
            artifact = {"integrity": "sha512-verified", "tarball": "https://npm.pkg.github.com/download/@verjson/authn/3.1.0/blob", "sha512": "a" * 128}
            provenance_path.write_text(json.dumps({
                "schemaVersion": 1, "request": request,
                "lanes": [{"package": request["package"], "range": "3.1.0",
                           "version": "3.1.0", "script": request["script"], **artifact}],
            }), encoding="utf-8")
            binder = step("Bind protected baseline receipt to verified artifact provenance")
            bound = subprocess.run(
                ["bash", "-c", binder["run"]], check=False, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ, "PROTECTED_BASELINE_RECEIPT": str(receipt_path),
                     "COMPATIBILITY_PROVENANCE": str(provenance_path)},
            )
            self.assertEqual(bound.returncode, 0, bound.stderr)
            baseline = json.loads(provenance_path.read_text(encoding="utf-8"))["protectedBaseline"]
            self.assertEqual(baseline["schemaVersion"], 2)
            self.assertEqual(baseline["headSha"], HEAD_SHA)
            self.assertEqual(baseline["baseVersion"], "3.0.0")
            self.assertEqual(baseline["artifact"], artifact)

    def test_missing_base_release_does_not_admit_unrelated_pr_changes(self):
        result, _, output_text, _ = run_resolver(
            '{"package":"@verjson/authn","version":"3.0.0",'
            '"script":"test:type-surface-compatibility"}',
            recovery_version="3.1.0",
            changed_files=[
                {"filename": ".github/ci/type-surface-baseline.json", "status": "modified"},
                {"filename": "src/consumer.ts", "status": "modified"},
            ],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "baseline recovery changes unauthorized files\n")
        self.assertEqual(output_text, "")

    def test_missing_base_release_requires_a_newer_published_stable_head(self):
        base = ('{"package":"@verjson/authn","version":"3.0.0",'
                '"script":"test:type-surface-compatibility"}')
        for head_version, available, reason in (
            ("2.9.0", "2.9.0", "baseline recovery requires a newer published stable version\n"),
            ("3.1.0", "3.2.0", "baseline recovery requires a newer published stable version\n"),
            ("3.1.0-rc.1", "3.1.0-rc.1", "protected type-surface declaration is unauthorized\n"),
        ):
            with self.subTest(head_version=head_version, available=available):
                result, _, output_text, _ = run_resolver(
                    base, recovery_version=available, head_version=head_version,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, reason)
                self.assertEqual(output_text, "")

    def test_moved_pr_head_cannot_supply_recovery_declaration(self):
        result, _, output_text, _ = run_resolver(
            '{"package":"@verjson/authn","version":"3.0.0",'
            '"script":"test:type-surface-compatibility"}',
            recovery_version="3.1.0", pull_head_sha="c" * 40,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "pull-request base or head identity is unauthorized\n")
        self.assertEqual(output_text, "")

    def test_generated_range_guard_accepts_stable_declarations(self):
        run = step("Validate approved internal dependency lock")["run"]
        lines = run.splitlines()
        prerelease = next(
            index for index, line in enumerate(lines)
            if "ALLOW_PRERELEASE" in line and "is_bounded_range" not in line
        )
        stable = next(
            index for index, line in enumerate(lines)
            if line.strip() == "if re.fullmatch(core_pattern, value):"
        )
        self.assertEqual(lines[prerelease].index("if"), lines[stable].index("if"))
        self.assertEqual(lines[stable + 1].strip(), "return True")
        self.assertGreater(stable, prerelease)

    def test_generated_candidate_plan_allows_no_optional_runtime_cache(self):
        run = load_generator().protected_candidate_helper_body()
        self.assertIn(
            'baseline_value = os.environ.get("npm_config_cache", "").strip()',
            run,
        )
        self.assertIn("baseline = None", run)
        self.assertIn("candidate_baseline = Path(os.path.abspath(baseline_value))", run)
        self.assertIn("baseline = candidate_baseline", run)
        self.assertIn(
            "baseline is not None and inventory(baseline) != baseline_inventory",
            run,
        )
        self.assertNotIn(
            'baseline = Path(os.path.abspath(os.environ["npm_config_cache"]))',
            run,
        )

    def test_trusted_compatibility_sandbox_is_provisioned_before_candidate_execution(self):
        steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["build-test"]["steps"]
        names = [candidate.get("name") for candidate in steps]
        self.assertLess(
            names.index("Provision trusted compatibility sandbox"),
            names.index("Run exact credentialless consumer script plan"),
        )

    def test_base_sha_is_resolved_once_and_head_or_merge_tree_cannot_supply_declaration(self):
        result, calls, output_text, receipt_text = run_resolver(
            '{"package":"@verjson/authn","version":"3.0.0","script":"test:type-surface-compatibility"}'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        pull_calls = [call for call in calls if "/pulls/" in call]
        content_calls = [call for call in calls if "/contents/" in call]
        self.assertEqual(len(pull_calls), 1)
        self.assertEqual(len(content_calls), 1)
        self.assertIn(f"ref={BASE_SHA}", content_calls[0])
        self.assertNotIn(HEAD_SHA, "\n".join(calls))
        self.assertNotIn("merge", "\n".join(calls).lower())
        values = dict(line.split("=", 1) for line in output_text.splitlines())
        self.assertEqual(
            values["compatibility-ranges"],
            '{"package":"@verjson/authn","ranges":["3.0.0"],"script":"test:type-surface-compatibility"}',
        )
        receipt = json.loads(receipt_text)
        self.assertEqual(receipt["baseSha"], BASE_SHA)
        self.assertEqual(receipt["declarationSha256"], hashlib.sha256(
            b'{"package":"@verjson/authn","version":"3.0.0","script":"test:type-surface-compatibility"}'
        ).hexdigest())

    def test_moving_base_does_not_trigger_a_second_lookup(self):
        result, calls, _, _ = run_resolver(
            '{"package":"@verjson/authn","version":"3.0.0","script":"test:type-surface-compatibility"}'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(1, sum("/pulls/" in call for call in calls))

    def test_explicitly_allowed_prerelease_reaches_compatibility_resolution(self):
        declaration = (
            '{"package":"@verjson/authn","version":"3.0.0-rc.1",'
            '"script":"test:type-surface-compatibility"}'
        )

        result, _, output_text, receipt_text = run_resolver(
            declaration, allow_prerelease=True
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(line.split("=", 1) for line in output_text.splitlines())
        self.assertEqual(
            values["compatibility-ranges"],
            '{"package":"@verjson/authn","ranges":["3.0.0-rc.1"],'
            '"script":"test:type-surface-compatibility"}',
        )
        self.assertEqual(json.loads(receipt_text)["version"], "3.0.0-rc.1")

        acquisition, provenance_text, calls, expected_lane = run_acquisition(
            values["compatibility-ranges"], allow_prerelease=True
        )

        self.assertEqual(acquisition.returncode, 0, acquisition.stderr)
        provenance = json.loads(provenance_text)
        self.assertEqual(
            provenance,
            {
                "schemaVersion": 1,
                "request": json.loads(values["compatibility-ranges"]),
                "lanes": [expected_lane],
            },
        )
        self.assertEqual(
            calls,
            [
                "view @verjson/authn versions --json",
                "view --json @verjson/authn@3.0.0-rc.1 name version "
                "dist.integrity dist.tarball",
            ],
        )

        denied, _, denied_calls, _ = run_acquisition(
            values["compatibility-ranges"], allow_prerelease=False
        )
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn(
            "compatibility range returned no released version: 3.0.0-rc.1",
            denied.stderr,
        )
        self.assertEqual(
            denied_calls,
            ["view @verjson/authn versions --json"],
        )

    def test_malformed_duplicate_and_unauthorized_declarations_fail_closed(self):
        prefix = "protected type-surface declaration"
        shape = f"{prefix} requires exactly package, version, and script\n"
        unauthorized = f"{prefix} is unauthorized\n"
        declarations = [
            ("{}", shape),
            ('{"package":"@verjson/authn","package":"@attacker/pkg","version":"3.0.0","script":"test:type-surface-compatibility"}',
             f"{prefix} is invalid: \n"),
            ('{"package":"@verjson/authz","version":"3.0.0","script":"test:type-surface-compatibility"}', unauthorized),
            ('{"package":"@verjson/authn","version":"latest","script":"test:type-surface-compatibility"}', unauthorized),
            ('{"package":"@verjson/authn","version":"3.0.0-rc.1","script":"test:type-surface-compatibility"}', unauthorized),
            ('{"package":"@verjson/authn","version":"3.0.0","script":"test:other"}', unauthorized),
            ('{"package":"@verjson/authn","version":"3.0.0","script":"test:type-surface-compatibility","extra":true}', shape),
        ]
        for declaration, reason in declarations:
            with self.subTest(declaration=declaration):
                result, _, _, _ = run_resolver(declaration)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, reason)

    def test_receipt_tampering_fails_the_protected_transfer_validator(self):
        generator = load_generator()
        validator = textwrap.dedent(generator.PROTECTED_BASELINE_TRANSFER_VALIDATION)
        baseline = {
            "schemaVersion": 1,
            "repository": "Verjson/verjson-authn",
            "declarationPath": ".github/ci/type-surface-baseline.json",
            "baseSha": BASE_SHA,
            "declarationSha256": "d" * 64,
            "package": "@verjson/authn",
            "version": "3.0.0",
            "script": "test:type-surface-compatibility",
            "artifact": {
                "integrity": "sha512-verified",
                "tarball": "https://npm.pkg.github.com/download/@verjson/authn/3.0.0/blob",
                "sha512": "a" * 128,
            },
        }
        provenance = {
            "schemaVersion": 1,
            "request": {
                "package": "@verjson/authn",
                "ranges": ["3.0.0"],
                "script": "test:type-surface-compatibility",
            },
            "lanes": [
                {
                    "package": "@verjson/authn",
                    "range": "3.0.0",
                    "version": "3.0.0",
                    "script": "test:type-surface-compatibility",
                    **baseline["artifact"],
                }
            ],
            "protectedBaseline": baseline,
        }
        environment = {
            "PROTECTED_BASELINE_DECLARATION_PATH": baseline["declarationPath"],
            "PROTECTED_BASELINE_REPOSITORY": baseline["repository"],
            "EXPECTED_PROTECTED_BASELINE_BASE_SHA": BASE_SHA,
            "EXPECTED_PROTECTED_BASELINE_DECLARATION_SHA256": "d" * 64,
            "PROTECTED_BASELINE_EXPECTED_PACKAGE": baseline["package"],
            "PROTECTED_BASELINE_EXPECTED_SCRIPT": baseline["script"],
        }

        def validate(candidate):
            namespace = {"os": os, "provenance": candidate, "request": candidate["request"]}
            previous = os.environ.copy()
            os.environ.update(environment)
            try:
                exec(validator, namespace)
            finally:
                os.environ.clear()
                os.environ.update(previous)

        validate(provenance)
        for mutation in (
            {**provenance["protectedBaseline"], "baseSha": "c" * 40},
            {**provenance["protectedBaseline"], "artifact": {**baseline["artifact"], "sha512": "b" * 128}},
        ):
            mutated = {**provenance, "protectedBaseline": mutation}
            with self.subTest(mutation=mutation):
                with self.assertRaises(SystemExit) as raised:
                    validate(mutated)
                self.assertEqual(
                    raised.exception.code,
                    "protected baseline receipt does not bind declaration, base, and artifact",
                )

        recovery = {
            **baseline,
            "schemaVersion": 2,
            "headSha": HEAD_SHA,
            "baseVersion": "2.9.0",
            "baseDeclarationSha256": "e" * 64,
        }
        recovery_provenance = {**provenance, "protectedBaseline": recovery}
        environment.update({
            "EXPECTED_PROTECTED_BASELINE_HEAD_SHA": HEAD_SHA,
            "EXPECTED_PROTECTED_BASELINE_BASE_VERSION": "2.9.0",
            "EXPECTED_PROTECTED_BASELINE_BASE_DECLARATION_SHA256": "e" * 64,
        })
        validate(recovery_provenance)
        for field, value in (
            ("headSha", "c" * 40),
            ("baseVersion", "2.8.0"),
            ("baseDeclarationSha256", "f" * 64),
        ):
            with self.subTest(field=field):
                altered = {**recovery_provenance, "protectedBaseline": {**recovery, field: value}}
                with self.assertRaises(SystemExit) as raised:
                    validate(altered)
                self.assertEqual(
                    raised.exception.code,
                    "protected baseline receipt does not bind declaration, base, and artifact",
                )


if __name__ == "__main__":
    unittest.main()
