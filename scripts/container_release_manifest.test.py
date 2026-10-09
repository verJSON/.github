#!/usr/bin/env python3

import copy
import importlib.util
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

import container_registry_destinations as destination_contract

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

MODULE_PATH = Path(__file__).with_name("container_release_manifest.py")
SPEC = importlib.util.spec_from_file_location("container_release_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
manifest_contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manifest_contract
SPEC.loader.exec_module(manifest_contract)


def config():
    return {
        "repository": "Verjson/verjson-github-runner",
        "registryNamespace": "ghcr.io/verjson",
        "nextStableVersion": "2.4.0",
        "images": [
            {
                "variant": "default",
                "repository": "ghcr.io/verjson/runner",
                "platforms": [
                    {"os": "linux", "architecture": "amd64"},
                    {"os": "linux", "architecture": "arm64", "variant": "v8"},
                ],
                "provenance": {
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "builderIdentity": "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "b" * 40,
                },
            }
        ],
    }


def manifest():
    return {
        "schemaVersion": 4,
        "kind": "container-candidate",
        "candidateVersion": "2.4.0-rc.123.1",
        "promotionEligible": False,
        "source": {
            "repository": "Verjson/verjson-github-runner",
            "commit": "a" * 40,
            "ref": "refs/heads/main",
            "workflow": "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "b" * 40,
            "runId": "123",
            "runAttempt": "1",
            "candidatePublishedAt": "2026-10-02T00:00:00Z",
        },
        "images": [
            {
                "variant": "default",
                "repository": "ghcr.io/verjson/runner",
                "indexDigest": "sha256:" + "1" * 64,
                "destinations": [
                    {
                        "provider": "ghcr",
                        "repository": "ghcr.io/verjson/runner",
                        "digest": "sha256:" + "1" * 64,
                        "candidateExpiresAt": "2026-12-29T00:00:00Z",
                        "verifiedAt": "2026-10-02T00:02:00Z",
                    }
                ],
                "identities": {"commit": "sha-" + "a" * 40, "candidate": "2.4.0-rc.123.1"},
                "platforms": [
                    {
                        "os": "linux",
                        "architecture": "amd64",
                        "digest": "sha256:" + "2" * 64,
                    },
                    {
                        "os": "linux",
                        "architecture": "arm64",
                        "variant": "v8",
                        "digest": "sha256:" + "3" * 64,
                    },
                ],
                "provenance": {
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "builderIdentity": "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "b" * 40,
                    "subjectDigest": "sha256:" + "1" * 64,
                    "bundleDigest": "sha256:" + "4" * 64,
                    "referrerManifestDigest": "sha256:" + "5" * 64,
                },
                "sbom": {
                    "predicateType": "https://spdx.dev/Document/v2.3",
                    "attestations": [
                        {
                            "os": "linux",
                            "architecture": "amd64",
                            "digest": "sha256:" + "2" * 64,
                            "bundleDigest": "sha256:" + "6" * 64,
                            "referrerManifestDigest": "sha256:" + "7" * 64,
                            "spdxDigest": "sha256:" + "8" * 64,
                        },
                        {
                            "os": "linux",
                            "architecture": "arm64",
                            "variant": "v8",
                            "digest": "sha256:" + "3" * 64,
                            "bundleDigest": "sha256:" + "9" * 64,
                            "referrerManifestDigest": "sha256:" + "a" * 64,
                            "spdxDigest": "sha256:" + "b" * 64,
                        },
                    ],
                },
            }
        ],
    }


def multi_registry_config():
    reviewed = config()
    reviewed["registryDestinations"] = [
        {"provider": "ghcr", "namespace": "ghcr.io/verjson"},
        {
            "provider": "gar",
            "namespace": "us-central1-docker.pkg.dev/verjson-artifacts/containers",
            "workloadIdentityProvider": "projects/123456789/locations/global/workloadIdentityPools/github/providers/verjson",
            "serviceAccount": "container-publisher@verjson-artifacts.iam.gserviceaccount.com",
            "candidateRetentionDays": 30,
        },
    ]
    return reviewed


def gar_receipt():
    return {
        "provider": "gar",
        "repository": "us-central1-docker.pkg.dev/verjson-artifacts/containers/runner",
        "digest": "sha256:" + "1" * 64,
        "candidateExpiresAt": "2026-11-01T00:00:00Z",
        "verifiedAt": "2026-10-02T00:03:00Z",
        "evidenceReferrers": [
            {
                "artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                "digest": "sha256:" + "5" * 64,
            },
        ],
        "platformEvidenceReferrers": [
            {
                "subjectDigest": "sha256:" + "2" * 64,
                "evidenceReferrers": [
                    {"artifactType": "application/spdx+json", "digest": "sha256:" + "7" * 64},
                ],
            },
            {
                "subjectDigest": "sha256:" + "3" * 64,
                "evidenceReferrers": [
                    {"artifactType": "application/spdx+json", "digest": "sha256:" + "a" * 64},
                ],
            },
        ],
    }


class ContainerReleaseManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema_path = (
            ROOT
            / "docs/decisions/0078-container-release-and-runner-deployment-contract/candidate-manifest.schema.json"
        )
        cls.schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.schema_validator = Draft202012Validator(
            cls.schema, format_checker=FormatChecker()
        )

    def assert_rejected(self, candidate, expected):
        with self.assertRaisesRegex(manifest_contract.ManifestError, expected):
            manifest_contract.validate_manifest(candidate, config())

    def test_candidate_schema_accepts_v4_and_historical_v3_and_v2_manifests(self):
        self.schema_validator.validate(manifest())

        historical_v3 = manifest()
        historical_v3["schemaVersion"] = 3
        historical_gar_receipt = gar_receipt()
        historical_gar_receipt.pop("platformEvidenceReferrers")
        historical_v3["images"][0]["destinations"].append(historical_gar_receipt)
        self.schema_validator.validate(historical_v3)

        historical_v2 = manifest()
        historical_v2["schemaVersion"] = 2
        historical_v2["source"].pop("candidatePublishedAt")
        historical_v2["images"][0].pop("destinations")
        self.schema_validator.validate(historical_v2)

    def test_candidate_manifest_is_ineligible_until_provenance_gates_are_enabled_by_contract(self):
        candidate = manifest()
        manifest_contract.validate_manifest(candidate, config())

        candidate["promotionEligible"] = True
        self.assert_rejected(candidate, "promotion eligibility is disabled")

    def test_candidate_schema_requires_v4_publication_evidence(self):
        candidate = manifest()
        candidate["images"][0].pop("destinations")
        self.assertTrue(
            any(
                "destinations" in error.message
                for error in self.schema_validator.iter_errors(candidate)
            )
        )

        candidate = manifest()
        candidate["source"].pop("candidatePublishedAt")
        self.assertTrue(
            any(
                "candidatePublishedAt" in error.message
                for error in self.schema_validator.iter_errors(candidate)
            )
        )

    def test_candidate_schema_rejects_each_v4_field_on_v2(self):
        cases = (
            ("timestamp", "destinations"),
            ("destinations", "candidatePublishedAt"),
            ("both fields", None),
        )
        for label, field_to_remove in cases:
            with self.subTest(label=label):
                candidate = manifest()
                candidate["schemaVersion"] = 2
                if field_to_remove == "destinations":
                    candidate["images"][0].pop("destinations")
                elif field_to_remove == "candidatePublishedAt":
                    candidate["source"].pop("candidatePublishedAt")
                self.assertTrue(list(self.schema_validator.iter_errors(candidate)))

    def test_candidate_schema_accepts_gar_destination_receipt(self):
        candidate = manifest()
        candidate["images"][0]["destinations"] = [gar_receipt()]
        self.schema_validator.validate(candidate)

    def test_candidate_schema_requires_platform_evidence_for_v4_gar_receipts(self):
        candidate = manifest()
        receipt = gar_receipt()
        receipt.pop("platformEvidenceReferrers")
        candidate["images"][0]["destinations"].append(receipt)

        self.assertTrue(
            any(
                "platformEvidenceReferrers" in error.message
                for error in self.schema_validator.iter_errors(candidate)
            )
        )

    def test_accepts_complete_manifest_bound_to_reviewed_identity(self):
        manifest_contract.validate_manifest(manifest(), config())

    def test_rejects_legacy_v2_candidate_with_rebuild_guidance(self):
        candidate = manifest()
        candidate["schemaVersion"] = 2
        candidate["source"].pop("candidatePublishedAt")
        candidate["images"][0].pop("destinations")
        self.assert_rejected(
            candidate,
            "manifest.schemaVersion must be 4; rebuild candidates published with schema v2 or v3",
        )

    def test_rejects_legacy_v3_candidate_with_rebuild_guidance(self):
        candidate = manifest()
        candidate["schemaVersion"] = 3
        self.assert_rejected(
            candidate,
            "manifest.schemaVersion must be 4; rebuild candidates published with schema v2 or v3",
        )

    def test_accepts_case_insensitive_github_repository_identity(self):
        candidate = manifest()
        candidate["source"]["repository"] = "verjson/VERJSON-GITHUB-RUNNER"
        manifest_contract.validate_manifest(candidate, config())

    def test_rejects_non_ascii_repository_casefolding(self):
        reviewed = config()
        reviewed["repository"] = "VerjoK/verjson-github-runner"
        candidate = manifest()
        candidate["source"]["repository"] = "VerjoK/verjson-github-runner"
        with self.assertRaisesRegex(manifest_contract.ManifestError, "source repository"):
            manifest_contract.validate_manifest(candidate, reviewed)

    def test_accepts_destination_receipt_generated_by_candidate_readback(self):
        reviewed = config()
        candidate = manifest()
        published_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        candidate["source"]["candidatePublishedAt"] = published_at
        digest = candidate["images"][0]["indexDigest"]
        with TemporaryDirectory() as directory:
            authfile = Path(directory) / "auth.json"
            authfile.write_text("{}", encoding="utf-8")
            with patch.object(destination_contract, "_remote_digest", return_value=digest):
                receipt = destination_contract.verify_candidate(
                    reviewed, "Verjson", "default", "ghcr", candidate["candidateVersion"],
                    digest, authfile, published_at,
                )
        candidate["images"][0]["destinations"] = [receipt]
        manifest_contract.validate_manifest(candidate, reviewed)

    def test_accepts_verified_multi_registry_destinations(self):
        reviewed = multi_registry_config()
        candidate = manifest()
        candidate["images"][0]["destinations"].append(gar_receipt())
        manifest_contract.validate_manifest(candidate, reviewed)

    def test_rejects_earlier_invalid_destination_receipt_when_last_is_valid(self):
        candidate = manifest()
        candidate["images"][0]["destinations"][0]["verifiedAt"] = "2026-10-01T23:59:59Z"
        later_receipt = gar_receipt()
        later_receipt.pop("platformEvidenceReferrers")
        candidate["images"][0]["destinations"].append(later_receipt)

        with self.assertRaisesRegex(manifest_contract.ManifestError, "not verified before expiry"):
            manifest_contract.validate_manifest(candidate, multi_registry_config())

    def test_rejects_gar_provenance_referrer_digest_mismatch(self):
        candidate = manifest()
        receipt = gar_receipt()
        receipt["evidenceReferrers"][0]["digest"] = "sha256:" + "c" * 64
        candidate["images"][0]["destinations"].append(receipt)

        with self.assertRaisesRegex(manifest_contract.ManifestError, "provenance referrer"):
            manifest_contract.validate_manifest(candidate, multi_registry_config())

    def test_rejects_gar_platform_sbom_referrer_digest_mismatch(self):
        candidate = manifest()
        receipt = gar_receipt()
        receipt["platformEvidenceReferrers"][0]["evidenceReferrers"][0]["digest"] = (
            "sha256:" + "c" * 64
        )
        candidate["images"][0]["destinations"].append(receipt)

        with self.assertRaisesRegex(manifest_contract.ManifestError, "SBOM referrer"):
            manifest_contract.validate_manifest(candidate, multi_registry_config())

    def test_rejects_missing_registry_receipt(self):
        reviewed = config()
        reviewed["registryDestinations"] = [
            {"provider": "ghcr", "namespace": "ghcr.io/verjson"},
            {
                "provider": "gar",
                "namespace": "us-central1-docker.pkg.dev/verjson-artifacts/containers",
                "workloadIdentityProvider": "projects/123456789/locations/global/workloadIdentityPools/github/providers/verjson",
                "serviceAccount": "container-publisher@verjson-artifacts.iam.gserviceaccount.com",
                "candidateRetentionDays": 30,
            },
        ]
        with self.assertRaisesRegex(manifest_contract.ManifestError, "destination receipts"):
            manifest_contract.validate_manifest(manifest(), reviewed)

    def test_accepts_exact_reviewed_private_node_packages(self):
        reviewed = config()
        reviewed["privateNodePackages"] = ["@verjson/pg", "@verjson/observability"]
        manifest_contract.validate_manifest(manifest(), reviewed)

    def test_rejects_unapproved_private_package_shapes(self):
        for unsafe in ("@verjson/pg", ["lodash"], ["@verjson/pg", "@verjson/pg"], ["@verjson/../pg"]):
            with self.subTest(unsafe=unsafe):
                reviewed = config()
                reviewed["privateNodePackages"] = unsafe
                with self.assertRaisesRegex(manifest_contract.ManifestError, "privateNodePackages"):
                    manifest_contract.validate_manifest(manifest(), reviewed)

    def test_rejects_duplicate_image_variant_even_when_digests_diverge(self):
        candidate = manifest()
        duplicate = copy.deepcopy(candidate["images"][0])
        duplicate["indexDigest"] = "sha256:" + "9" * 64
        candidate["images"].append(duplicate)
        self.assert_rejected(candidate, "duplicate identity 'default'")

    def test_rejects_candidate_contract_pin_different_from_reviewed_config(self):
        candidate = manifest()
        candidate["source"]["workflow"] = "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "c" * 40
        candidate["images"][0]["provenance"]["builderIdentity"] = candidate["source"]["workflow"]
        self.assert_rejected(candidate, "provenance builder identity differs")

    def test_rejects_duplicate_platform_tuple_even_when_digests_diverge(self):
        candidate = manifest()
        duplicate = copy.deepcopy(candidate["images"][0]["platforms"][0])
        duplicate["digest"] = "sha256:" + "9" * 64
        candidate["images"][0]["platforms"].append(duplicate)
        self.assert_rejected(candidate, "duplicate identity.*amd64")

    def test_rejects_incomplete_platform_matrix(self):
        candidate = manifest()
        candidate["images"][0]["platforms"].pop()
        self.assert_rejected(candidate, "platform matrix differs")

    def test_rejects_extra_variant(self):
        candidate = manifest()
        extra = copy.deepcopy(candidate["images"][0])
        extra["variant"] = "debug"
        candidate["images"].append(extra)
        self.assert_rejected(candidate, "variants differ")

    def test_rejects_repository_substitution(self):
        candidate = manifest()
        candidate["images"][0]["repository"] = "ghcr.io/attacker/runner"
        self.assert_rejected(candidate, "image repository differs")

    def test_rejects_platform_identity_substitution(self):
        candidate = manifest()
        candidate["images"][0]["platforms"][0]["architecture"] = "s390x"
        self.assert_rejected(candidate, "platform matrix differs")

    def test_rejects_provenance_identity_substitution(self):
        candidate = manifest()
        candidate["images"][0]["provenance"]["builderIdentity"] = "attacker"
        self.assert_rejected(candidate, "provenance signer workflow differs")

    def test_rejects_source_repository_substitution(self):
        candidate = manifest()
        candidate["source"]["repository"] = "attacker/repository"
        self.assert_rejected(candidate, "source repository differs")

    def test_rejects_mutable_manifest_identity(self):
        candidate = manifest()
        candidate["images"][0]["identities"] = {"commit": "latest", "candidate": "candidate"}
        self.assert_rejected(candidate, "immutable identities differ")

    def test_rejects_malformed_digest(self):
        candidate = manifest()
        candidate["images"][0]["indexDigest"] = "latest"
        self.assert_rejected(candidate, "lowercase sha256 digest")

    def test_rejects_candidate_not_derived_from_reviewed_release_line(self):
        candidate = manifest()
        candidate["candidateVersion"] = "2.5.0-rc.123.1"
        self.assert_rejected(candidate, "candidateVersion is not derived")

    def test_rejects_attestation_from_another_ref(self):
        candidate = manifest()
        candidate["source"]["ref"] = "refs/heads/feature"
        self.assert_rejected(candidate, "source ref")

    def test_rejects_unobserved_provenance_claim(self):
        candidate = manifest()
        candidate["images"][0]["provenance"].pop("bundleDigest")
        self.assert_rejected(candidate, "bundleDigest")

    def test_rejects_missing_platform_sbom_attestation(self):
        candidate = manifest()
        candidate["images"][0]["sbom"]["attestations"].pop()
        self.assert_rejected(candidate, "SBOM platform attestations differ")

    def test_rejects_wrong_sbom_predicate(self):
        candidate = manifest()
        candidate["images"][0]["sbom"]["predicateType"] = "https://example.invalid/sbom"
        self.assert_rejected(candidate, "SBOM predicate differs")

    def test_rejects_sbom_for_another_platform_digest(self):
        candidate = manifest()
        candidate["images"][0]["sbom"]["attestations"][0]["digest"] = "sha256:" + "9" * 64
        self.assert_rejected(candidate, "SBOM digest differs")

    def test_rejects_arbitrary_registry_namespace(self):
        candidate = manifest()
        candidate["images"][0]["repository"] = "ghcr.io/attacker/runner"
        config()["images"][0]["repository"] = "ghcr.io/attacker/runner"
        self.assert_rejected(candidate, "image repository differs")

    def test_accepts_derived_variant_bound_to_same_run_base_digest(self):
        reviewed = config()
        candidate = manifest()
        derived_config = copy.deepcopy(reviewed["images"][0])
        derived_config["variant"] = "debug"
        derived_config["baseVariant"] = "default"
        reviewed["images"].append(derived_config)
        derived = copy.deepcopy(candidate["images"][0])
        derived["variant"] = "debug"
        derived["indexDigest"] = "sha256:" + "6" * 64
        derived["destinations"][0]["digest"] = derived["indexDigest"]
        derived["provenance"]["subjectDigest"] = derived["indexDigest"]
        derived["base"] = {"variant": "default", "digest": candidate["images"][0]["indexDigest"]}
        candidate["images"].append(derived)
        manifest_contract.validate_manifest(candidate, reviewed)

    def test_rejects_derived_variant_bound_to_other_digest(self):
        candidate = manifest()
        reviewed = config()
        derived_config = copy.deepcopy(reviewed["images"][0])
        derived_config["variant"] = "debug"
        derived_config["baseVariant"] = "default"
        reviewed["images"].append(derived_config)
        derived = copy.deepcopy(candidate["images"][0])
        derived["variant"] = "debug"
        derived["indexDigest"] = "sha256:" + "6" * 64
        derived["destinations"][0]["digest"] = derived["indexDigest"]
        derived["provenance"]["subjectDigest"] = derived["indexDigest"]
        derived["base"] = {"variant": "default", "digest": "sha256:" + "9" * 64}
        candidate["images"].append(derived)
        with self.assertRaisesRegex(manifest_contract.ManifestError, "same-run base digest"):
            manifest_contract.validate_manifest(candidate, reviewed)


if __name__ == "__main__":
    unittest.main()
