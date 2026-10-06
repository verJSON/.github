#!/usr/bin/env python3
import copy
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("container_oci_index", ROOT / "container_oci_index.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OCIIndexTest(unittest.TestCase):
    def setUp(self):
        fixtures = ROOT / "fixtures" / "container-candidate"
        self.index = json.loads((fixtures / "oci-index.json").read_text())
        self.evidence = json.loads((fixtures / "oci-evidence-amd64.json").read_text())
        self.reviewed = [
            {"os": "linux", "architecture": "amd64"},
            {"os": "linux", "architecture": "arm64"},
        ]

    def assert_index_rejected(self, mutate, message):
        candidate = copy.deepcopy(self.index)
        mutate(candidate)
        with self.assertRaisesRegex(MODULE.OCIIndexError, message):
            MODULE.validate_index(candidate, self.reviewed)

    def test_live_buildkit_shape_binds_one_evidence_descriptor_per_reviewed_platform(self):
        inventory = MODULE.validate_index(self.index, self.reviewed)

        self.assertEqual(
            [platform["architecture"] for platform in inventory["platforms"]],
            ["amd64", "arm64"],
        )
        self.assertEqual(
            inventory["evidence"][0],
            {
                "os": "linux",
                "architecture": "amd64",
                "digest": "sha256:64966446bf4daf2806a38c29846187cca29b12122ab4540d55eeb65be78b857c",
                "subjectDigest": "sha256:65503088b703464250e1fc2d9f182c2363f385392706378b51c45a245d7e6582",
            },
        )

    def test_unknown_attestation_descriptors_are_not_deployable_platforms(self):
        inventory = MODULE.validate_index(self.index, self.reviewed)

        self.assertEqual(len(inventory["platforms"]), 2)
        self.assertTrue(all(platform["os"] != "unknown" for platform in inventory["platforms"]))

    def test_missing_reviewed_platform_is_rejected(self):
        self.assert_index_rejected(lambda value: value["manifests"].pop(1), "deployable platforms differ")

    def test_unreviewed_deployable_platform_is_rejected(self):
        extra = copy.deepcopy(self.index["manifests"][0])
        extra["digest"] = "sha256:" + "a" * 64
        extra["platform"]["architecture"] = "s390x"
        self.assert_index_rejected(lambda value: value["manifests"].append(extra), "deployable platforms differ")

    def test_missing_attestation_evidence_is_rejected(self):
        self.assert_index_rejected(lambda value: value["manifests"].pop(), "attestation evidence differs")

    def test_duplicate_attestation_evidence_is_rejected(self):
        duplicate = copy.deepcopy(self.index["manifests"][2])
        duplicate["digest"] = "sha256:" + "b" * 64
        self.assert_index_rejected(
            lambda value: value["manifests"].append(duplicate),
            "duplicate attestation evidence",
        )

    def test_evidence_bound_to_an_unknown_subject_is_rejected(self):
        self.assert_index_rejected(
            lambda value: value["manifests"][2]["annotations"].update(
                {"vnd.docker.reference.digest": "sha256:" + "c" * 64}
            ),
            "attestation evidence differs",
        )

    def test_unknown_descriptor_without_attestation_identity_is_rejected(self):
        self.assert_index_rejected(
            lambda value: value["manifests"][2]["annotations"].update(
                {"vnd.docker.reference.type": "not-evidence"}
            ),
            "not an attestation manifest",
        )

    def test_partially_unknown_descriptor_is_rejected(self):
        self.assert_index_rejected(
            lambda value: value["manifests"][2]["platform"].update({"architecture": "amd64"}),
            "partially unknown",
        )

    def test_duplicate_reviewed_platform_is_rejected(self):
        with self.assertRaisesRegex(MODULE.OCIIndexError, "duplicate reviewed platform"):
            MODULE.validate_index(self.index, [self.reviewed[0], self.reviewed[0]])

    def test_live_evidence_manifest_selects_exactly_one_spdx_layer(self):
        self.assertEqual(
            MODULE.validate_spdx_evidence(self.evidence),
            {"spdxLayerDigest": "sha256:f22ae07462fe7ad08bf2087a837e050498929daeebd2aea87aaa902a9cf02abe"},
        )

    def test_evidence_without_spdx_is_rejected(self):
        candidate = copy.deepcopy(self.evidence)
        candidate["layers"][0]["annotations"]["in-toto.io/predicate-type"] = "https://example.invalid"
        with self.assertRaisesRegex(MODULE.OCIIndexError, "exactly one SPDX layer"):
            MODULE.validate_spdx_evidence(candidate)

    def test_duplicate_spdx_layers_are_rejected(self):
        candidate = copy.deepcopy(self.evidence)
        candidate["layers"].append(copy.deepcopy(candidate["layers"][0]))
        with self.assertRaisesRegex(MODULE.OCIIndexError, "exactly one SPDX layer"):
            MODULE.validate_spdx_evidence(candidate)

    def test_unwrapped_sbom_uses_the_only_validated_platform(self):
        document = {"spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT"}
        inventory = {"platforms": [{"os": "linux", "architecture": "amd64"}]}

        result = MODULE.compact_spdx_document({"SPDX": document}, "linux/amd64", inventory)

        self.assertEqual(json.loads(result), document)

    def test_unwrapped_sbom_rejects_multiple_validated_platforms(self):
        document = {"spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT"}
        inventory = {
            "platforms": [
                {"os": "linux", "architecture": "amd64"},
                {"os": "linux", "architecture": "arm64"},
            ]
        }

        with self.assertRaisesRegex(MODULE.OCIIndexError, "exact platform entry"):
            MODULE.compact_spdx_document({"SPDX": document}, "linux/amd64", inventory)

    def test_wrapped_sbom_keeps_exact_platform_selection(self):
        document = {"spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT"}
        inventory = {
            "platforms": [
                {"os": "linux", "architecture": "amd64"},
                {"os": "linux", "architecture": "arm64"},
            ]
        }

        result = MODULE.compact_spdx_document(
            {"linux/amd64": {"SPDX": document}}, "linux/amd64", inventory
        )

        self.assertEqual(json.loads(result), document)

    def test_sbom_rejects_platform_not_in_validated_inventory(self):
        document = {"spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT"}
        inventory = {"platforms": [{"os": "linux", "architecture": "amd64"}]}

        with self.assertRaisesRegex(MODULE.OCIIndexError, "validated OCI inventory"):
            MODULE.compact_spdx_document({"SPDX": document}, "linux/arm64", inventory)


if __name__ == "__main__":
    unittest.main()
