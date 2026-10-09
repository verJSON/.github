import copy
from datetime import datetime, timezone
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("promotion", ROOT / "scripts/container_release_promotion.py")
promotion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promotion)


class PromotionTest(unittest.TestCase):
    def setUp(self):
        schema_path = (
            ROOT
            / "docs/decisions/0078-container-release-and-runner-deployment-contract/release-manifest.schema.json"
        )
        self.schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.schema_validator = Draft202012Validator(
            self.schema, format_checker=FormatChecker()
        )
        self.config = {
            "repository": "Verjson/example",
            "registryNamespace": "ghcr.io/verjson",
            "nextStableVersion": "1.2.3",
            "images": [{"variant": "default", "repository": "ghcr.io/verjson/example", "platforms": [{"os": "linux", "architecture": "amd64"}], "provenance": {"predicateType": "https://slsa.dev/provenance/v1"}}],
        }
        digest = "sha256:" + "1" * 64
        workflow = "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "b" * 40
        self.config["images"][0]["provenance"]["builderIdentity"] = workflow
        self.candidate = {"schemaVersion": 4, "kind": "container-candidate", "promotionEligible": False, "candidateVersion": "1.2.3-rc.12345.1", "source": {"repository": "Verjson/example", "commit": "a" * 40, "ref": "refs/heads/main", "workflow": workflow, "runId": "12345", "runAttempt": "1"}, "images": [{"variant": "default", "repository": "ghcr.io/verjson/example", "indexDigest": digest, "identities": {"commit": "sha-" + "a" * 40, "candidate": "1.2.3-rc.12345.1"}, "platforms": [{"os": "linux", "architecture": "amd64", "digest": "sha256:" + "2" * 64}], "provenance": {"predicateType": "https://slsa.dev/provenance/v1", "builderIdentity": workflow, "subjectDigest": digest, "attestationId": "attestation-1"}, "sbom": {"predicateType": "https://spdx.dev/Document/v2.3", "attestations": [{"os": "linux", "architecture": "amd64", "digest": "sha256:" + "2" * 64, "attestationId": "attestation-2"}]}}]}
        image = self.candidate["images"][0]
        image["provenance"].update(
            bundleDigest="sha256:" + "3" * 64,
            referrerManifestDigest="sha256:" + "4" * 64,
        )
        image["sbom"]["attestations"][0].update(
            bundleDigest="sha256:" + "5" * 64,
            referrerManifestDigest="sha256:" + "6" * 64,
            spdxDigest="sha256:" + "7" * 64,
        )
        self.candidate["source"]["candidatePublishedAt"] = "2026-08-09T00:00:00Z"
        self.candidate["images"][0]["destinations"] = [
            {
                "provider": "ghcr",
                "repository": "ghcr.io/verjson/example",
                "digest": digest,
                "candidateExpiresAt": "2026-11-05T00:00:00Z",
                "verifiedAt": "2026-08-09T00:01:00Z",
            }
        ]
        self.now = datetime(2026, 8, 9, 1, tzinfo=timezone.utc)
        self.state = {"candidateManifestDigest": "sha256:" + "9" * 64, "aliases": {}, "release": {"workflow": {"path": ".github/workflows/container-release.yml", "contractCommit": "c" * 40}, "sourceCommit": "d" * 40, "runId": 456, "runAttempt": 1}, "timestamps": {"candidatePublishedAt": "2026-08-09T00:00:00Z", "releasedAt": "2026-08-09T01:00:00Z"}, "previousRelease": None, "provenance": {"default": "sha256:" + "7" * 64}, "sbom": {"default": "sha256:" + "5" * 64}}

    def release(self, *args, **kwargs):
        kwargs.setdefault("now", self.now)
        return promotion.release(*args, **kwargs)

    def test_exact_digests_form_deterministic_release(self):
        result = self.release(self.candidate, self.config, self.state, "1.2.3")
        self.schema_validator.validate(result)
        self.assertEqual(sorted(i["indexDigest"] for i in self.candidate["images"]), sorted(i["indexDigest"] for i in result["images"]))
        self.assertEqual("1.2.3", result["releaseVersion"])
        self.assertEqual(3, result["schemaVersion"])
        self.assertEqual(self.state["candidateManifestDigest"], result["candidateManifestDigest"])
        self.assertRegex(result["candidateManifestDigest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(sorted(result["promotion"]["operationOrder"]), result["promotion"]["operationOrder"])

    def test_promotion_projects_v4_evidence_into_the_digest_bound_candidate_record(self):
        gar_receipt = {
            "provider": "gar",
            "repository": "us-central1-docker.pkg.dev/verjson-artifacts/containers/example",
            "digest": self.candidate["images"][0]["indexDigest"],
            "candidateExpiresAt": "2026-09-08T00:00:00Z",
            "verifiedAt": "2026-08-09T00:01:00Z",
            "evidenceReferrers": [
                {
                    "artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                    "digest": "sha256:" + "4" * 64,
                }
            ],
            "platformEvidenceReferrers": [
                {
                    "subjectDigest": "sha256:" + "2" * 64,
                    "evidenceReferrers": [
                        {"artifactType": "application/spdx+json", "digest": "sha256:" + "6" * 64}
                    ],
                }
            ],
        }
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": "ghcr.io/verjson"},
            {
                "provider": "gar",
                "namespace": "us-central1-docker.pkg.dev/verjson-artifacts/containers",
                "workloadIdentityProvider": "projects/123456789/locations/global/workloadIdentityPools/github/providers/verjson",
                "serviceAccount": "container-publisher@verjson-artifacts.iam.gserviceaccount.com",
                "candidateRetentionDays": 30,
            },
        ]
        self.candidate["images"][0]["destinations"].append(gar_receipt)

        result = self.release(self.candidate, self.config, self.state, "1.2.3")

        self.schema_validator.validate(result)
        self.assertEqual(
            [
                {
                    key: receipt[key]
                    for key in (
                        "provider",
                        "repository",
                        "digest",
                        "candidateExpiresAt",
                        "verifiedAt",
                    )
                }
                for receipt in self.candidate["images"][0]["destinations"]
            ],
            result["images"][0]["destinations"],
        )
        self.assertEqual(self.state["candidateManifestDigest"], result["candidateManifestDigest"])

    def test_release_schema_preserves_v2_and_requires_v3_destinations(self):
        current = self.release(self.candidate, self.config, self.state, "1.2.3")
        historical = copy.deepcopy(current)
        historical["schemaVersion"] = 2
        for image in historical["images"]:
            image.pop("destinations")
        self.schema_validator.validate(historical)

        current["images"][0].pop("destinations")
        self.assertTrue(
            any(
                "destinations" in error.message
                for error in self.schema_validator.iter_errors(current)
            )
        )

    def test_release_schema_rejects_v2_with_v3_destination_fields(self):
        historical = self.release(self.candidate, self.config, self.state, "1.2.3")
        historical["schemaVersion"] = 2
        self.assertTrue(list(self.schema_validator.iter_errors(historical)))

    def test_exact_partial_alias_is_idempotent(self):
        image = self.candidate["images"][0]
        state = copy.deepcopy(self.state); state["aliases"] = {f"{image['repository']}:1.2.3": image["indexDigest"]}
        resumed = self.release(self.candidate, self.config, state, "1.2.3")
        fresh = self.release(self.candidate, self.config, self.state, "1.2.3")
        self.assertEqual(fresh, resumed)

    def test_divergent_partial_alias_is_rejected(self):
        image = self.candidate["images"][0]
        state = copy.deepcopy(self.state); state["aliases"] = {f"{image['repository']}:1.2.3": "sha256:" + "f" * 64}
        with self.assertRaisesRegex(Exception, "different digest"):
            self.release(self.candidate, self.config, state, "1.2.3")

    def test_repeat_and_downgrade_are_rejected(self):
        for extra in ({"gitTag": True}, {"githubRelease": True}, {"changelogSnapshot": True}):
            state = self.state | extra
            with self.assertRaises(Exception):
                self.release(self.candidate, self.config, state, "1.2.3")
        with self.assertRaisesRegex(Exception, "reviewed next stable"):
            self.release(self.candidate, self.config, self.state, "1.2.2")

    def test_stable_line_cannot_overwrite_or_move_backward(self):
        state = copy.deepcopy(self.state)
        state["previousRelease"] = {"releaseVersion": "1.2.3", "manifestDigest": "sha256:" + "6" * 64}
        with self.assertRaisesRegex(Exception, "advance"):
            self.release(self.candidate, self.config, state, "1.2.3")

    def test_tampered_candidate_is_rejected_before_plan(self):
        self.candidate["images"][0]["indexDigest"] = "sha256:" + "0" * 64
        with self.assertRaises(Exception):
            self.release(self.candidate, self.config, self.state, "1.2.3")

    def test_expired_candidate_fails_closed_without_rebuilding_or_substituting(self):
        with self.assertRaisesRegex(Exception, "rebuilding or substituting is forbidden"):
            self.release(
                self.candidate,
                self.config,
                self.state,
                "1.2.3",
                now=datetime(2026, 11, 5, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
