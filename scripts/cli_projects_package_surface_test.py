#!/usr/bin/env python3
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts/cli-projects-package-surface.py"
SPEC = importlib.util.spec_from_file_location("cli_projects_package_surface", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
GENERATOR_SPEC = importlib.util.spec_from_file_location(
    "gen_node_required_workflow", ROOT / "scripts/gen-node-required-workflow.py"
)
GENERATOR = importlib.util.module_from_spec(GENERATOR_SPEC)
GENERATOR_SPEC.loader.exec_module(GENERATOR)


class CliProjectsPackageSurfaceTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        (self.root / ".github/workflows").mkdir(parents=True)
        (self.root / "templates/package/.github/workflows").mkdir(parents=True)
        (self.root / "package.json").write_text(json.dumps({
            "name": "@verjson/cli-projects",
            "type": "module",
            "bin": MODULE.EXPECTED_BINS,
            "files": ["src", "templates", "README.md"],
            "publishConfig": {"registry": "https://npm.pkg.github.com"},
            "scripts": {"build": "bash scripts/check-sources.sh"},
            "engines": {"node": ">=24.19.0"},
        }), encoding="utf-8")
        release = """on:\n  workflow_dispatch:\njobs:\n  release:\n    uses: Verjson/.github/.github/workflows/node-release.yml@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"""
        ci = """on:\n  pull_request:\njobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"""
        (self.root / ".github/workflows/release.yml").write_text(release, encoding="utf-8")
        for name in ("ci.yml.tmpl", "actionlint.yml.tmpl"):
            (self.root / f"templates/package/.github/workflows/{name}").write_text(ci, encoding="utf-8")

    def tearDown(self):
        self.scratch.cleanup()

    def test_valid_surface_is_accepted(self):
        MODULE.validate_tree(self.root)

    def test_release_push_in_block_or_flow_form_is_rejected(self):
        path = self.root / ".github/workflows/release.yml"
        for trigger in ("on: [workflow_dispatch, push]\n", "on:\n  workflow_dispatch:\n  push:\n"):
            with self.subTest(trigger=trigger):
                path.write_text(trigger + "jobs:\n  release:\n    uses: x/y/z@" + "a" * 40 + "\n", encoding="utf-8")
                with self.assertRaisesRegex(MODULE.ContractError, "workflow_dispatch only"):
                    MODULE.validate_workflow(path, release=True)

    def test_duplicate_trigger_key_is_rejected(self):
        path = self.root / ".github/workflows/release.yml"
        path.write_text("on:\n  workflow_dispatch:\non:\n  push:\njobs:\n  x:\n    uses: x/y/z@" + "a" * 40 + "\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ContractError, "duplicate YAML key"):
            MODULE.validate_workflow(path, release=True)

    def test_mutable_action_and_container_references_are_rejected(self):
        path = self.root / "templates/package/.github/workflows/ci.yml.tmpl"
        for uses, message in (("actions/checkout@v4", "40-hex"), ("docker://alpine:latest", "sha256")):
            with self.subTest(uses=uses):
                path.write_text(f"on:\n  pull_request:\njobs:\n  x:\n    steps:\n      - uses: {uses}\n", encoding="utf-8")
                with self.assertRaisesRegex(MODULE.ContractError, message):
                    MODULE.validate_workflow(path)

    def test_verification_cannot_be_softened(self):
        path = self.root / "templates/package/.github/workflows/ci.yml.tmpl"
        cases = (
            ("continue-on-error: true\n        run: npm test", "continue on error"),
            ("run: npm test || true", "hide failure"),
            ("if: false\n        run: npm test", "unconditionally skipped"),
        )
        for body, message in cases:
            with self.subTest(body=body):
                path.write_text("on:\n  pull_request:\njobs:\n  x:\n    steps:\n      - " + body + "\n", encoding="utf-8")
                with self.assertRaisesRegex(MODULE.ContractError, message):
                    MODULE.validate_workflow(path)

    def test_manifest_rejects_added_binary_or_credential_file(self):
        package = json.loads((self.root / "package.json").read_text())
        package["bin"]["spoof"] = "src/spoof.js"
        (self.root / "package.json").write_text(json.dumps(package), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ContractError, "binary surface"):
            MODULE.validate_manifest(self.root)

    def test_manifest_accepts_reviewed_v1_floor_and_rejects_legacy_floor(self):
        MODULE.validate_manifest(self.root)
        package_path = self.root / "package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        package["engines"]["node"] = ">=20.20.2"
        package_path.write_text(json.dumps(package), encoding="utf-8")

        with self.assertRaisesRegex(MODULE.ContractError, "supported Node engine floor drifted"):
            MODULE.validate_manifest(self.root)

    def test_manifest_floor_stays_synchronized_with_required_node_config(self):
        config = json.loads(MODULE.REQUIRED_NODE_CONFIG.read_text(encoding="utf-8"))
        workflow = MODULE.load_yaml(
            ROOT / ".github/workflows/cli-projects-package-surface-required.yml"
        )
        floor_lane = workflow["jobs"]["ci-node-floor"]["with"]["node-version"]
        self.assertEqual(floor_lane, config["node_versions"][1])
        self.assertEqual(MODULE.required_node_engine(), f">={floor_lane}")

    def test_required_workflow_uses_current_canonical_node_ci_pin(self):
        expected_sha = "576707fe0be0dabe43ea57c504575f8cda88b0d2"
        config = json.loads(MODULE.REQUIRED_NODE_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(config["protected_node_ci_sha"], expected_sha)

        workflow = MODULE.load_yaml(
            ROOT / ".github/workflows/cli-projects-package-surface-required.yml"
        )
        expected_uses = (
            "Verjson/.github/.github/workflows/node-ci-protected.yml@" + expected_sha
        )
        for job in ("ci", "ci-node-floor"):
            with self.subTest(job=job):
                self.assertEqual(workflow["jobs"][job]["uses"], expected_uses)

    def test_admitted_consumer_caller_is_the_v4_2_0_generated_image(self):
        # The consumer declares its contract release, and contract-version.py requires
        # every Verjson/.github pin in it to name that release, so the admitted caller
        # must track the release the consumer adopts (ADR 0212, 2026-10-20). Moving it
        # changes the digest admission requires; pin both so a move is deliberate.
        config = json.loads(MODULE.REQUIRED_NODE_CONFIG.read_text(encoding="utf-8"))
        consumer = GENERATOR.render_consumer(
            config, "config/cli-projects-required-node-ci.json"
        )
        self.assertEqual(
            config["consumer_node_ci_sha"], "a89fe1f090545c15cdd6987f20119327206e2c59"
        )
        self.assertEqual(
            hashlib.sha256(consumer.encode()).hexdigest(),
            "d23be004f196ce321965bd07d3f4fa221374d61270215ed17ed5f228239bc117",
        )

    def test_protected_lane_repin_leaves_the_consumer_caller_bytes_unchanged(self):
        # The push-only consumer caller has its own pin, so hardening the protected
        # pull-request lane never forces a consumer caller change (ADR 0153).
        config = json.loads(MODULE.REQUIRED_NODE_CONFIG.read_text(encoding="utf-8"))
        path = "config/cli-projects-required-node-ci.json"
        consumer = GENERATOR.render_consumer(config, path)
        repinned = {**config, "protected_node_ci_sha": "f" * 40}
        self.assertNotEqual(repinned["protected_node_ci_sha"], config["protected_node_ci_sha"])
        self.assertEqual(GENERATOR.render_consumer(repinned, path), consumer)
        self.assertNotEqual(
            GENERATOR.render(repinned, path), GENERATOR.render(config, path)
        )

    def test_generated_admission_requires_exact_pinned_caller(self):
        config = json.loads(MODULE.REQUIRED_NODE_CONFIG.read_text(encoding="utf-8"))
        workflow_text = GENERATOR.render(
            config, Path("config/cli-projects-required-node-ci.json")
        )
        workflow = yaml.safe_load(workflow_text)
        script = workflow["jobs"]["admission"]["steps"][0]["run"]

        self.assertIn(".base.ref", script)
        self.assertIn('[ "$live_base_ref" = main ]', script)
        self.assertIn('[ "$candidate_sha256" = "$CONSUMER_WORKFLOW_SHA256" ]', script)
        self.assertNotIn("git/ref/heads/main", script)
        self.assertNotIn("LEGACY_CONSUMER_WORKFLOW_SHA256", workflow_text)
        self.assertNotIn("default_main_workflow_sha256", script)

    def run_admission(self, repository, live_repository):
        """Execute the committed admission step against a stubbed `gh`."""
        workflow = MODULE.load_yaml(
            ROOT / ".github/workflows/cli-projects-package-surface-required.yml"
        )
        script = workflow["jobs"]["admission"]["steps"][0]["run"]
        head = "f" * 40
        candidate = "consumer workflow\n"
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        stub = bin_dir / "gh"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"/actions/runs/"*) printf \'pull_request\\t%s\\t1\\t5\\n\' "$HEAD" ;;\n'
            '  *"/pulls/5 "*) printf \'5\\topen\\t%s\\t%s\\tmain\\n\' "$LIVE_REPOSITORY" "$HEAD" ;;\n'
            '  *"/contents/.github/workflows/ci.yml?ref="*) printf \'%s\' "$CANDIDATE" ;;\n'
            '  *) echo "UNSTUBBED gh $*" >&2; exit 1 ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        output = self.root / "github-output"
        output.write_text("", encoding="utf-8")
        environment = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LC_ALL": "C.UTF-8",  # the hosted runner locale
            "REPOSITORY": repository,
            "RUN_ID": "123",
            "CONSUMER_WORKFLOW_SHA256": hashlib.sha256(candidate.encode()).hexdigest(),
            "GH_TOKEN": "stub-token",
            "GITHUB_OUTPUT": str(output),
            "HEAD": head,
            "LIVE_REPOSITORY": live_repository,
            "CANDIDATE": candidate,
        }
        result = subprocess.run(
            ["bash", "-e", "-c", script], env=environment,
            capture_output=True, text=True, check=False,
        )
        return result, output.read_text(encoding="utf-8")

    def test_admission_accepts_the_consumer_in_its_renamed_display_case(self):
        # GitHub reports the renamed organization as `verJSON` in both the run
        # context and the pull request API since 2026-10-02 (ADR 0213); every
        # admission since refused verjson-cli-projects run 38432170170 on it.
        repository = "verJSON/verjson-cli-projects"
        result, output = self.run_admission(repository, repository)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"head-repository={repository}\n", output)

    def test_admission_accepts_the_consumer_in_any_ascii_case(self):
        for repository in ("Verjson/verjson-cli-projects", "VERJSON/VERJSON-CLI-PROJECTS"):
            for live_repository in ("verJSON/verjson-cli-projects", repository):
                with self.subTest(repository=repository, live_repository=live_repository):
                    result, output = self.run_admission(repository, live_repository)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"head-repository={live_repository}\n", output)

    def test_admission_rejects_every_other_repository_identity(self):
        # Each spelling defeats a weaker comparison: owner-only, prefix, regex
        # wildcard, untrimmed whitespace, and Unicode case folding (U+0130 and
        # U+017F fold to ASCII outside the C locale; U+0456 is a lookalike).
        consumer = "verJSON/verjson-cli-projects"
        for other in (
            "verJSON/other", "evil/verjson-cli-projects", "verJSON/verjson-cli-projects-x",
            "verJSON/verjson.cli-projects", " verJSON/verjson-cli-projects",
            "verJSON/verjson-clİ-projects", "verJSON/verjſon-cli-projects",
            "verJSON/verjson-cl\u0456-projects",
        ):
            for repository, live_repository in ((other, consumer), (consumer, other)):
                with self.subTest(repository=repository, live_repository=live_repository):
                    result, output = self.run_admission(repository, live_repository)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output, "")

    def test_rollout_legacy_consumer_workflow_digest_accepts_digest_or_null(self):
        config = json.loads(MODULE.REQUIRED_NODE_CONFIG.read_text(encoding="utf-8"))
        for value in ("a" * 64, None):
            with self.subTest(value=value):
                candidate = {**config, "rollout_legacy_consumer_workflow_sha256": value}
                self.assert_required_node_config_accepted(candidate)

    def test_rollout_legacy_consumer_workflow_digest_rejects_malformed_values(self):
        config = json.loads(MODULE.REQUIRED_NODE_CONFIG.read_text(encoding="utf-8"))
        malformed_values = (
            "a" * 63,
            "a" * 65,
            "A" * 64,
            "g" * 64,
            "",
            False,
            1,
            [],
        )
        for value in malformed_values:
            with self.subTest(value=value):
                candidate = {**config, "rollout_legacy_consumer_workflow_sha256": value}
                self.assert_required_node_config_rejected(candidate)

    def test_required_node_config_rejects_every_malformed_field_in_both_consumers(self):
        config = json.loads(MODULE.REQUIRED_NODE_CONFIG.read_text(encoding="utf-8"))
        mutations = {
            "schema_version": False,
            "repository": "attacker/example",
            "protected_node_ci_sha": "main",
            "consumer_node_ci_sha": "v1",
            "node_versions": [{"unhashable": True}, "24.19.0"],
            "approved_internal_packages": [{"unhashable": True}],
            "scripts": [{"unhashable": True}],
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                candidate = dict(config)
                candidate[field] = value
                self.assert_required_node_config_rejected(candidate)

    def test_required_node_config_rejects_missing_unknown_and_duplicate_fields(self):
        config = json.loads(MODULE.REQUIRED_NODE_CONFIG.read_text(encoding="utf-8"))
        for field in config:
            with self.subTest(missing=field):
                candidate = dict(config)
                del candidate[field]
                self.assert_required_node_config_rejected(candidate)

        self.assert_required_node_config_rejected({**config, "unknown": True})
        serialized = json.dumps(config)
        for field, value in config.items():
            with self.subTest(duplicate=field):
                duplicate = "{" + json.dumps(field) + ":" + json.dumps(value) + "," + serialized[1:]
                self.assert_required_node_config_rejected_text(duplicate)

    def test_required_node_config_rejects_legacy_consumer_digest(self):
        config = json.loads(MODULE.REQUIRED_NODE_CONFIG.read_text(encoding="utf-8"))
        self.assert_required_node_config_rejected({
            **config,
            "legacy_consumer_workflow_sha256": "a" * 64,
        })

    def assert_required_node_config_rejected(self, config):
        self.assert_required_node_config_rejected_text(json.dumps(config))

    def assert_required_node_config_accepted(self, config):
        path = self.root / "required-node.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        self.assertEqual(
            MODULE.required_node_engine(path), f">={config['node_versions'][1]}"
        )
        self.assertEqual(GENERATOR.load_config(path), config)

    def assert_required_node_config_rejected_text(self, text):
        path = self.root / "required-node.json"
        path.write_text(text, encoding="utf-8")
        with self.assertRaises(MODULE.ContractError):
            MODULE.required_node_engine(path)
        with self.assertRaises(GENERATOR.ContractError):
            GENERATOR.load_config(path)

    def test_container_digest_rejects_tag_and_action_pin_rejects_mutable_ref(self):
        with self.assertRaisesRegex(MODULE.ContractError, "sha256"):
            MODULE.validate_uses("fixture", "docker://example.invalid/tool:latest")
        with self.assertRaisesRegex(MODULE.ContractError, "40-hex"):
            MODULE.validate_uses("fixture", "Verjson/.github/action@main")


if __name__ == "__main__":
    unittest.main()
