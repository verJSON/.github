#!/usr/bin/env python3
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("container_oci_index", ROOT / "container_oci_index.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SPDXCompactionTest(unittest.TestCase):
    def setUp(self):
        self.document = {
            "spdxVersion": "SPDX-2.3",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": "runner image",
            "packages": [{"name": "example", "versionInfo": "1.0.0"}],
        }
        self.index = {"linux/amd64": {"SPDX": self.document}}
        self.inventory = {
            "platforms": [
                {"os": "linux", "architecture": "amd64"},
                {"os": "linux", "architecture": "arm64"},
            ]
        }

    def test_compaction_preserves_the_complete_spdx_document(self):
        rendered = MODULE.compact_spdx_document(self.index, "linux/amd64", self.inventory)

        self.assertEqual(json.loads(rendered), self.document)
        self.assertEqual(rendered, json.dumps(self.document, separators=(",", ":")) + "\n")

    def test_compaction_fits_when_only_pretty_print_whitespace_crosses_the_limit(self):
        self.document["packages"] = [
            {"name": f"package-{index}", "versionInfo": "1.0.0"} for index in range(20)
        ]
        compact_size = len(
            (json.dumps(self.document, separators=(",", ":")) + "\n").encode("utf-8")
        )
        pretty_size = len((json.dumps(self.document, indent=2) + "\n").encode("utf-8"))
        self.assertGreater(pretty_size, compact_size)
        original_limit = MODULE.MAX_ATTESTATION_PREDICATE_BYTES
        self.addCleanup(setattr, MODULE, "MAX_ATTESTATION_PREDICATE_BYTES", original_limit)
        MODULE.MAX_ATTESTATION_PREDICATE_BYTES = compact_size

        rendered = MODULE.compact_spdx_document(self.index, "linux/amd64", self.inventory)

        self.assertEqual(len(rendered.encode("utf-8")), compact_size)

    def test_exact_platform_binding_rejects_missing_or_ambiguous_fallbacks(self):
        with self.assertRaisesRegex(MODULE.OCIIndexError, "no exact platform entry"):
            MODULE.compact_spdx_document(self.index, "linux/arm64", self.inventory)

    def test_malformed_spdx_identity_is_rejected(self):
        self.document["spdxVersion"] = "SPDX-2.2"

        with self.assertRaisesRegex(MODULE.OCIIndexError, "must be an SPDX 2.3 document"):
            MODULE.compact_spdx_document(self.index, "linux/amd64", self.inventory)

    def test_compact_document_over_the_github_limit_is_rejected(self):
        original_limit = MODULE.MAX_ATTESTATION_PREDICATE_BYTES
        self.addCleanup(setattr, MODULE, "MAX_ATTESTATION_PREDICATE_BYTES", original_limit)
        MODULE.MAX_ATTESTATION_PREDICATE_BYTES = 128
        self.document["name"] = "x" * 128

        with self.assertRaisesRegex(MODULE.OCIIndexError, "exceeds GitHub's .* predicate limit"):
            MODULE.compact_spdx_document(self.index, "linux/amd64", self.inventory)

    def test_non_standard_json_numbers_are_rejected(self):
        self.document["invalid"] = float("nan")

        with self.assertRaisesRegex(MODULE.OCIIndexError, "not strict JSON"):
            MODULE.compact_spdx_document(self.index, "linux/amd64", self.inventory)


if __name__ == "__main__":
    unittest.main()
