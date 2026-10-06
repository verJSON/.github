import json
import os
import pathlib
import subprocess
import tempfile
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/container-candidate-publish.yml"


def assembly_script():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["candidate-manifest"]["steps"]
    assembly = next(step for step in steps if step.get("name") == "Assemble complete candidate manifest")
    return assembly["run"]


def artifact_selection_script():
    script = assembly_script()
    start = script.index("shopt -s nullglob")
    end = script.index('for image in "${image_artifacts[@]}"; do', start)
    return script[start:end]


class CandidateArtifactLayoutTests(unittest.TestCase):
    def run_selection(self, files, has_gar=True):
        with tempfile.TemporaryDirectory() as directory:
            for file in files:
                path = pathlib.Path(directory) / file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
            script = (
                "set -euo pipefail\n"
                f"HAS_GAR={'true' if has_gar else 'false'}\n"
                + artifact_selection_script()
                + "printf 'image:%s\\n' \"${image_artifacts[@]}\"\n"
                + "printf 'sbom:%s\\n' \"${sbom_artifacts[@]}\"\n"
                + "printf 'registry:%s\\n' \"${registry_artifacts[@]}\"\n"
            )
            return subprocess.run(
                ["bash", "-c", script], cwd=directory, capture_output=True, text=True
            )

    def test_one_match_is_read_from_the_download_root(self):
        result = self.run_selection([
            "candidate-images/image.json",
            "candidate-sboms/sbom.json",
            "candidate-registry/registry-receipt.json",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "image:candidate-images/image.json",
            "sbom:candidate-sboms/sbom.json",
            "registry:candidate-registry/registry-receipt.json",
        ])

    def test_multiple_matches_are_read_from_named_directories(self):
        files = [
            f"{group}/{group}-{variant}/{name}"
            for group, name in [
                ("candidate-images", "image.json"),
                ("candidate-sboms", "sbom.json"),
                ("candidate-registry", "registry-receipt.json"),
            ]
            for variant in ("base", "worker")
        ]
        result = self.run_selection(files)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 6)
        self.assertEqual(set(result.stdout.splitlines()), {
            f"{kind}:{file}"
            for kind, group in [
                ("image", "candidate-images"),
                ("sbom", "candidate-sboms"),
                ("registry", "candidate-registry"),
            ]
            for file in files if file.startswith(group + "/")
        })

    def test_missing_required_artifact_group_fails(self):
        for files, error in [
            (["candidate-sboms/sbom.json"], "candidate image artifacts are missing"),
            (["candidate-images/image.json"], "candidate SBOM artifacts are missing"),
            (["candidate-images/image.json", "candidate-sboms/sbom.json"], "GAR receipt artifacts are missing"),
        ]:
            with self.subTest(error=error):
                result = self.run_selection(files)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stdout)

    def test_ghcr_only_run_does_not_require_gar_receipts(self):
        result = self.run_selection([
            "candidate-images/image.json", "candidate-sboms/sbom.json"
        ], has_gar=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_destination_receipts_are_assembled_without_stdin(self):
        script = assembly_script()
        start = script.index('destinations="$(jq')
        end = script.index('raw="$(docker', start)
        command = "set -euo pipefail\n" + script[start:end] + "printf '%s\\n' \"$destinations\"\n"
        destination = {
            "provider": "ghcr",
            "repository": "ghcr.io/verjson/canary",
            "digest": "sha256:" + "a" * 64,
            "candidateExpiresAt": "2026-11-05T00:00:00Z",
        }
        receipt = {**destination, "evidenceReferrers": []}
        for receipts, accepted in [([receipt], True), ([{**receipt, "digest": "sha256:" + "b" * 64}], False), ([receipt, receipt], False)]:
            with self.subTest(receipts=receipts):
                result = subprocess.run(
                    ["bash", "-c", command],
                    input="",
                    capture_output=True,
                    text=True,
                    env={
                        **os.environ,
                        "expected_destinations": json.dumps([destination]),
                        "receipts": json.dumps(receipts),
                    },
                )
                if accepted:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), [receipt])
                else:
                    self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
