import json
import unittest
from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import container_registry_destinations as destinations
from container_registry_destinations import DestinationError, expand_image_destinations, normalize_destinations


OWNER = "verjson"
GHCR = "ghcr.io/verjson"
GAR = "us-central1-docker.pkg.dev/verjson-artifacts/containers"
IMAGE = {"variant": "api", "repository": f"{GHCR}/api"}
GAR_DESTINATION = {
    "provider": "gar",
    "namespace": GAR,
    "workloadIdentityProvider": "projects/123456789/locations/global/workloadIdentityPools/github/providers/verjson",
    "serviceAccount": "container-publisher@verjson-artifacts.iam.gserviceaccount.com",
    "candidateRetentionDays": 88,
}


class RegistryDestinationTests(unittest.TestCase):
    def setUp(self):
        self.config = {"registryNamespace": GHCR, "images": [IMAGE]}

    def test_ghcr_remains_the_default_destination(self):
        self.assertEqual(
            normalize_destinations(self.config, OWNER),
            [{"provider": "ghcr", "namespace": GHCR, "registryHost": "ghcr.io", "candidateRetentionDays": 88}],
        )

    def test_gar_is_expanded_from_the_ghcr_repository_suffix(self):
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR}, GAR_DESTINATION
        ]
        self.assertEqual(
            expand_image_destinations(self.config, OWNER, IMAGE)[1]["repository"],
            f"{GAR}/api",
        )

    def test_non_ghcr_primary_is_rejected(self):
        self.config["registryDestinations"] = [GAR_DESTINATION]
        with self.assertRaises(DestinationError):
            normalize_destinations(self.config, OWNER)

    def test_ghcr_namespace_must_match_the_repository_owner(self):
        self.config["registryNamespace"] = "ghcr.io/another-owner"
        with self.assertRaises(DestinationError):
            normalize_destinations(self.config, OWNER)

    def test_gar_namespace_must_have_a_project_and_repository(self):
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR},
            {**GAR_DESTINATION, "namespace": "us-central1-docker.pkg.dev/verjson-artifacts"},
        ]
        with self.assertRaises(DestinationError):
            normalize_destinations(self.config, OWNER)

    def test_gar_requires_a_narrow_oidc_provider_and_service_account(self):
        for field, value in [
            ("workloadIdentityProvider", "https://accounts.google.com"),
            ("serviceAccount", "publisher@example.com"),
        ]:
            with self.subTest(field=field):
                self.config["registryDestinations"] = [
                    {"provider": "ghcr", "namespace": GHCR},
                    {**GAR_DESTINATION, field: value},
                ]
                with self.assertRaises(DestinationError):
                    normalize_destinations(self.config, OWNER)

    def test_duplicate_or_unsupported_destinations_are_rejected(self):
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR},
            GAR_DESTINATION,
            GAR_DESTINATION,
        ]
        with self.assertRaises(DestinationError):
            normalize_destinations(self.config, OWNER)
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR},
            {"provider": "nexus", "namespace": "nexus.example/containers"},
        ]
        with self.assertRaises(DestinationError):
            normalize_destinations(self.config, OWNER)

    def test_retention_days_must_be_integers_and_reject_booleans(self):
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR, "candidateRetentionDays": True}
        ]
        with self.assertRaises(DestinationError):
            normalize_destinations(self.config, OWNER)

        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR},
            {**GAR_DESTINATION, "candidateRetentionDays": True},
        ]
        with self.assertRaises(DestinationError):
            normalize_destinations(self.config, OWNER)

    def test_image_must_stay_under_the_canonical_namespace(self):
        with self.assertRaises(DestinationError):
            expand_image_destinations(self.config, OWNER, {"repository": "ghcr.io/unrelated/api"})

    def test_referrer_inventory_requires_evidence_for_its_actual_subject(self):
        descriptors = [
            {
                "digest": "sha256:" + "a" * 64,
                "reference": f"{GHCR}/api@sha256:" + "a" * 64,
                "artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json",
            },
            {
                "digest": "sha256:" + "b" * 64,
                "reference": f"{GHCR}/api@sha256:" + "b" * 64,
                "artifactType": "application/spdx+json",
            },
        ]

        self.assertEqual(
            destinations.parse_referrer_inventory(
                json.dumps({"referrers": descriptors[:1]}).encode(),
                repository=f"{GHCR}/api",
            ),
            [
                {
                    "artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                    "digest": "sha256:" + "a" * 64,
                },
            ],
        )
        self.assertEqual(
            destinations.parse_referrer_inventory(
                json.dumps({"referrers": descriptors[1:]}).encode(),
                repository=f"{GHCR}/api",
                subject="platform",
            ),
            [{"artifactType": "application/spdx+json", "digest": "sha256:" + "b" * 64}],
        )

    def test_referrer_inventory_rejects_missing_ambiguous_or_unbound_evidence(self):
        bundle = {
            "digest": "sha256:" + "a" * 64,
            "reference": f"{GHCR}/api@sha256:" + "a" * 64,
            "artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json",
        }
        sbom = {
            "digest": "sha256:" + "b" * 64,
            "reference": f"{GHCR}/api@sha256:" + "b" * 64,
            "artifactType": "application/spdx+json",
        }
        for descriptors in ([], [sbom], [bundle, sbom, bundle]):
            with self.subTest(descriptors=descriptors):
                with self.assertRaises(DestinationError):
                    destinations.parse_referrer_inventory(
                        json.dumps({"referrers": descriptors}).encode(),
                        repository=f"{GHCR}/api",
                    )
        for descriptors in ([], [bundle], [sbom, sbom]):
            with self.subTest(platform_descriptors=descriptors):
                with self.assertRaises(DestinationError):
                    destinations.parse_referrer_inventory(
                        json.dumps({"referrers": descriptors}).encode(),
                        repository=f"{GHCR}/api",
                        subject="platform",
                    )

    def test_mirror_copies_all_platforms_and_requires_exact_digest_readback(self):
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR}, GAR_DESTINATION
        ]
        payload = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json"}'
        digest = "sha256:" + sha256(payload).hexdigest()
        with TemporaryDirectory() as directory:
            authfile = Path(directory) / "config.json"
            authfile.write_text("{}", encoding="utf-8")
            referrers = [
                {"artifactType": "application/spdx+json", "digest": "sha256:" + "b" * 64},
                {
                    "artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                    "digest": "sha256:" + "a" * 64,
                },
            ]
            with (
                patch.object(destinations, "_skopeo", side_effect=[
                    (1, b"", b"manifest unknown"), (0, payload, b"")
                ]),
                patch.object(destinations, "_oras", return_value=(0, b"", b"")) as run,
                patch.object(destinations, "_platform_subjects", return_value=[]),
                patch.object(destinations, "_referrer_inventory", side_effect=[referrers, referrers]),
            ):
                receipt = destinations.mirror_candidate(
                    self.config, OWNER, "api", "gar", "1.2.3-rc.123.1", digest, authfile
                )
                self.assertEqual(receipt, {
                "provider": "gar", "variant": "api", "repository": f"{GAR}/api", "digest": digest,
                "evidenceReferrers": referrers,
                })
                copy_args = run.call_args.args[0]
                self.assertIn("cp", copy_args)
                self.assertIn("--recursive", copy_args)
                self.assertIn(f"{GHCR}/api@{digest}", copy_args)
                self.assertIn(f"{GAR}/api:1.2.3-rc.123.1", copy_args)

    def test_mirror_verifies_platform_sbom_referrer_at_platform_digest(self):
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR}, GAR_DESTINATION
        ]
        platform = "sha256:" + "c" * 64
        index_referrers = [{
            "artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json",
            "digest": "sha256:" + "a" * 64,
        }]
        sbom_referrers = [{
            "artifactType": "application/spdx+json", "digest": "sha256:" + "b" * 64,
        }]
        payload = b"index"
        digest = "sha256:" + sha256(payload).hexdigest()
        with TemporaryDirectory() as directory:
            authfile = Path(directory) / "config.json"
            authfile.write_text("{}", encoding="utf-8")
            with (
                patch.object(destinations, "_skopeo", side_effect=[
                    (1, b"", b"manifest unknown"), (0, payload, b"")
                ]),
                patch.object(destinations, "_oras", return_value=(0, b"", b"")),
                patch.object(destinations, "_platform_subjects", return_value=[platform]),
                patch.object(destinations, "_referrer_inventory", side_effect=[
                    index_referrers, sbom_referrers, index_referrers, sbom_referrers,
                ]) as discover,
            ):
                destinations.mirror_candidate(
                    self.config, OWNER, "api", "gar", "1.2.3-rc.123.1", digest, authfile
                )
            self.assertEqual(discover.call_args_list[1].args[1], platform)
            self.assertEqual(discover.call_args_list[3].args[1], platform)
            self.assertEqual(discover.call_args_list[1].args[3], "platform")
            with (
                patch.object(destinations, "_skopeo", side_effect=[
                    (1, b"", b"manifest unknown"), (0, payload, b"")
                ]),
                patch.object(destinations, "_oras", return_value=(0, b"", b"")),
                patch.object(destinations, "_platform_subjects", return_value=[platform]),
                patch.object(destinations, "_referrer_inventory", side_effect=[
                    index_referrers, sbom_referrers, index_referrers, [],
                ]),
            ):
                with self.assertRaisesRegex(DestinationError, "platform SBOM differs"):
                    destinations.mirror_candidate(
                        self.config, OWNER, "api", "gar", "1.2.3-rc.123.1", digest, authfile
                    )

    def test_platform_subjects_are_bound_to_the_pinned_index_and_reviewed_platforms(self):
        platform = "sha256:" + "c" * 64
        evidence = "sha256:" + "d" * 64
        manifest = "application/vnd.oci.image.manifest.v1+json"
        payload = json.dumps({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {"mediaType": manifest, "digest": platform,
                 "platform": {"os": "linux", "architecture": "amd64"}},
                {"mediaType": manifest, "digest": evidence,
                 "platform": {"os": "unknown", "architecture": "unknown"},
                 "annotations": {"vnd.docker.reference.type": "attestation-manifest",
                                 "vnd.docker.reference.digest": platform}},
            ],
        }).encode()
        digest = "sha256:" + sha256(payload).hexdigest()
        with patch.object(destinations, "_skopeo", return_value=(0, payload, b"")):
            self.assertEqual(
                destinations._platform_subjects(
                    f"{GHCR}/api", digest, Path("/tmp/auth.json"),
                    [{"os": "linux", "architecture": "amd64"}],
                ),
                [platform],
            )
            with self.assertRaisesRegex(DestinationError, "pinned candidate digest"):
                destinations._platform_subjects(
                    f"{GHCR}/api", "sha256:" + "a" * 64, Path("/tmp/auth.json"),
                    [{"os": "linux", "architecture": "amd64"}],
                )
            with self.assertRaisesRegex(DestinationError, "platform inventory"):
                destinations._platform_subjects(
                    f"{GHCR}/api", digest, Path("/tmp/auth.json"),
                    [{"os": "linux", "architecture": "arm64"}],
                )

    def test_mirror_is_idempotent_for_same_digest_and_rejects_conflicts(self):
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR}, GAR_DESTINATION
        ]
        payload = b'{"schemaVersion":2}'
        digest = "sha256:" + sha256(payload).hexdigest()
        with TemporaryDirectory() as directory:
            authfile = Path(directory) / "config.json"
            authfile.write_text("{}", encoding="utf-8")
            with (
                patch.object(destinations, "_skopeo", return_value=(0, payload, b"")) as run,
                patch.object(destinations, "_platform_subjects", return_value=[]),
                patch.object(destinations, "_referrer_inventory", return_value=[
                    {"artifactType": "application/spdx+json", "digest": "sha256:" + "b" * 64},
                    {"artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json", "digest": "sha256:" + "a" * 64},
                ]),
            ):
                destinations.mirror_candidate(
                    self.config, OWNER, "api", "gar", "1.2.3", digest, authfile
                )
            self.assertEqual(run.call_count, 2)
            with patch.object(destinations, "_skopeo", return_value=(0, b"different", b"")):
                with self.assertRaisesRegex(DestinationError, "different digest"):
                    destinations.mirror_candidate(
                        self.config, OWNER, "api", "gar", "1.2.3", digest, authfile
                    )

    def test_mirror_receipt_records_candidate_expiry_and_digest_readback_time(self):
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR}, GAR_DESTINATION
        ]
        payload = b'{"schemaVersion":2}'
        digest = "sha256:" + sha256(payload).hexdigest()
        with TemporaryDirectory() as directory:
            authfile = Path(directory) / "config.json"
            authfile.write_text("{}", encoding="utf-8")
            with (
                patch.object(destinations, "_skopeo", side_effect=[
                    (1, b"", b"manifest unknown"), (0, payload, b"")
                ]),
                patch.object(destinations, "_oras", return_value=(0, b"", b"")),
                patch.object(destinations, "_platform_subjects", return_value=[]),
                patch.object(destinations, "_referrer_inventory", side_effect=[
                    [
                        {"artifactType": "application/spdx+json", "digest": "sha256:" + "b" * 64},
                        {"artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json", "digest": "sha256:" + "a" * 64},
                    ]
                ] * 2),
            ):
                receipt = destinations.mirror_candidate(
                    self.config, OWNER, "api", "gar", "1.2.3-rc.123.1", digest,
                    authfile, "2026-10-02T00:00:00Z",
                )
        self.assertEqual(receipt["candidateExpiresAt"], "2026-12-29T00:00:00Z")
        self.assertRegex(receipt["verifiedAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(receipt["variant"], "api")
        self.assertEqual(receipt["digest"], digest)

    def test_canonical_candidate_readback_returns_the_same_expiry_contract(self):
        payload = b'{"schemaVersion":2}'
        digest = "sha256:" + sha256(payload).hexdigest()
        with TemporaryDirectory() as directory:
            authfile = Path(directory) / "config.json"
            authfile.write_text("{}", encoding="utf-8")
            with patch.object(destinations, "_skopeo", return_value=(0, payload, b"")):
                receipt = destinations.verify_candidate(
                    self.config, OWNER, "api", "ghcr", "1.2.3-rc.123.1", digest,
                    authfile, "2026-10-02T00:00:00Z",
                )
        self.assertEqual(receipt["candidateExpiresAt"], "2026-12-29T00:00:00Z")
        self.assertEqual(receipt["repository"], f"{GHCR}/api")

    def test_mirror_does_not_treat_registry_authorization_failure_as_absence(self):
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR}, GAR_DESTINATION
        ]
        with TemporaryDirectory() as directory:
            authfile = Path(directory) / "config.json"
            authfile.write_text("{}", encoding="utf-8")
            with patch.object(destinations, "_skopeo", return_value=(1, b"", b"unauthorized")):
                with self.assertRaisesRegex(DestinationError, "observation failed"):
                    destinations.mirror_candidate(
                        self.config, OWNER, "api", "gar", "1.2.3", "sha256:" + "a" * 64, authfile
                    )

    def test_mirror_cli_with_published_at_still_copies_and_reads_back(self):
        self.config["registryDestinations"] = [
            {"provider": "ghcr", "namespace": GHCR}, GAR_DESTINATION
        ]
        payload = b'{"schemaVersion":2}'
        digest = "sha256:" + sha256(payload).hexdigest()
        referrers = [
            {"artifactType": "application/spdx+json", "digest": "sha256:" + "b" * 64},
            {"artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json", "digest": "sha256:" + "a" * 64},
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "candidate.json"
            config.write_text(json.dumps(self.config), encoding="utf-8")
            authfile = root / "auth.json"
            authfile.write_text("{}", encoding="utf-8")
            output = StringIO()
            argv = [
                "container_registry_destinations.py",
                "--config", str(config), "--owner", OWNER,
                "--mirror-provider", "gar", "--variant", "api",
                "--tag", "1.2.3-rc.123.1", "--digest", digest,
                "--authfile", str(authfile), "--published-at", "2026-10-02T00:00:00Z",
            ]
            with (
                patch.object(destinations.sys, "argv", argv),
                patch.object(destinations, "_skopeo", side_effect=[
                    (1, b"", b"manifest unknown"), (0, payload, b"")
                ]),
                patch.object(destinations, "_oras", return_value=(0, b"", b"")) as run,
                patch.object(destinations, "_platform_subjects", return_value=[]),
                patch.object(destinations, "_referrer_inventory", side_effect=[referrers, referrers]),
                redirect_stdout(output),
            ):
                self.assertEqual(destinations.main(), 0)

        self.assertEqual(run.call_count, 1)
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt["provider"], "gar")
        self.assertEqual(receipt["repository"], f"{GAR}/api")
        self.assertEqual(receipt["digest"], digest)
        self.assertEqual(receipt["evidenceReferrers"], referrers)


if __name__ == "__main__":
    unittest.main()
