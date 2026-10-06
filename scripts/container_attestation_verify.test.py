#!/usr/bin/env python3

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import container_attestation_verify as verifier


WORKFLOW = "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "b" * 40
SOURCE_SHA = "a" * 40
INDEX_DIGEST = "sha256:" + "1" * 64
PLATFORM_DIGESTS = ["sha256:" + "2" * 64, "sha256:" + "3" * 64]
GAR_REPOSITORY = "us-central1-docker.pkg.dev/verjson/candidates/example"


def config():
    return {
        "repository": "Verjson/example",
        "images": [
            {
                "variant": "default",
                "repository": "ghcr.io/verjson/example",
                "platforms": [
                    {"os": "linux", "architecture": "amd64"},
                    {"os": "linux", "architecture": "arm64", "variant": "v8"},
                ],
            }
        ],
    }


def candidate():
    return {
        "source": {
            "repository": "Verjson/example",
            "commit": SOURCE_SHA,
            "ref": "refs/heads/main",
            "workflow": WORKFLOW,
        },
        "images": [
            {
                "variant": "default",
                "repository": "ghcr.io/verjson/example",
                "indexDigest": INDEX_DIGEST,
                "destinations": [
                    {"provider": "gar", "repository": GAR_REPOSITORY, "digest": INDEX_DIGEST}
                ],
                "provenance": {
                    "bundleDigest": "sha256:" + "4" * 64,
                    "referrerManifestDigest": "sha256:" + "5" * 64,
                    "builderIdentity": WORKFLOW,
                },
                "platforms": [
                    {"os": "linux", "architecture": "amd64", "digest": PLATFORM_DIGESTS[0]},
                    {
                        "os": "linux",
                        "architecture": "arm64",
                        "variant": "v8",
                        "digest": PLATFORM_DIGESTS[1],
                    },
                ],
                "sbom": {
                    "attestations": [
                        {
                            "os": "linux",
                            "architecture": "amd64",
                            "digest": PLATFORM_DIGESTS[0],
                            "spdxDigest": PLATFORM_DIGESTS[0],
                            "bundleDigest": "sha256:" + "6" * 64,
                            "referrerManifestDigest": "sha256:" + "7" * 64,
                        },
                        {
                            "os": "linux",
                            "architecture": "arm64",
                            "variant": "v8",
                            "digest": PLATFORM_DIGESTS[1],
                            "spdxDigest": PLATFORM_DIGESTS[1],
                            "bundleDigest": "sha256:" + "8" * 64,
                            "referrerManifestDigest": "sha256:" + "9" * 64,
                        },
                    ]
                },
            }
        ],
    }


class ContainerAttestationVerifierTests(unittest.TestCase):
    def test_verifies_image_and_sbom_evidence_from_gar_with_independent_identity_policy(self):
        helper = Path("/tmp/container_cosign_provenance.py")
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[0] == "docker":
                output = json.dumps(INDEX_DIGEST) if "{{json .Manifest.Digest}}" in command else "{}"
                return subprocess.CompletedProcess(command, 0, output, "")
            if command[0] == sys.executable and command[2] in {"verify-image", "verify-sbom"}:
                return subprocess.CompletedProcess(command, 0, json.dumps({"verified": True}), "")
            raise AssertionError(command)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            verifier, "validate_manifest"
        ):
            receipts = verifier.verify(
                candidate(),
                config(),
                Path(directory),
                expected_repository="Verjson/example",
                repository_id="12345",
                expected_source_ref="refs/heads/main",
                expected_source_commit=SOURCE_SHA,
                contract_ref="b" * 40,
                cosign_helper=helper,
                run=run,
            )
            gar_receipt = json.loads((Path(directory) / "default-provenance.json").read_text())

        image_call = next(call for call in calls if "verify-image" in call)
        self.assertEqual(image_call[image_call.index("--registry-repository") + 1], GAR_REPOSITORY)
        self.assertEqual(image_call[image_call.index("--expected-source-repository-id") + 1], "12345")
        self.assertEqual(image_call[image_call.index("--expected-source-commit") + 1], SOURCE_SHA)
        self.assertEqual(
            image_call[image_call.index("--expected-publisher-workflow-ref") + 1],
            WORKFLOW,
        )
        self.assertEqual(
            image_call[image_call.index("--expected-contract-sha") + 1],
            "b" * 40,
        )
        self.assertEqual(2, sum("verify-sbom" in call for call in calls))
        self.assertEqual({"default"}, set(receipts["provenance"]))
        self.assertEqual({"default"}, set(receipts["sbom"]))
        self.assertEqual(INDEX_DIGEST, gar_receipt["registryImageDigest"])

    def test_rejects_manifest_source_outside_independently_verified_release_context(self):
        value = candidate()
        value["source"]["repository"] = "attacker/other"
        with tempfile.TemporaryDirectory() as directory, patch.object(
            verifier, "validate_manifest"
        ):
            with self.assertRaisesRegex(ValueError, "source repository"):
                verifier.verify(
                    value,
                    config(),
                    Path(directory),
                    expected_repository="Verjson/example",
                    repository_id="12345",
                    expected_source_ref="refs/heads/main",
                    expected_source_commit=SOURCE_SHA,
                    contract_ref="b" * 40,
                    cosign_helper=Path("/tmp/container_cosign_provenance.py"),
                )

    def test_rejects_candidate_publisher_revision_outside_pinned_contract(self):
        value = candidate()
        value["images"][0]["provenance"]["builderIdentity"] = (
            "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "c" * 40
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            verifier, "validate_manifest"
        ):
            with self.assertRaisesRegex(ValueError, "publisher identity"):
                verifier.verify(
                    value,
                    config(),
                    Path(directory),
                    expected_repository="Verjson/example",
                    repository_id="12345",
                    expected_source_ref="refs/heads/main",
                    expected_source_commit=SOURCE_SHA,
                    contract_ref="b" * 40,
                    cosign_helper=Path("/tmp/container_cosign_provenance.py"),
                )


if __name__ == "__main__":
    unittest.main()
