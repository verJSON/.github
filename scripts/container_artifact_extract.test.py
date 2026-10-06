import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

MODULE = Path(__file__).with_name("container_artifact_extract.py")
SPEC = importlib.util.spec_from_file_location("extractor", MODULE)
extractor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extractor)


class ArtifactExtractionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, entries):
        path = self.root / "candidate.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as value:
            for name, content in entries:
                value.writestr(name, content)
        return path

    def test_extracts_bounded_manifest_and_bundle(self):
        output = self.root / "candidate.json"
        bundle = self.root / "candidate.sigstore.json"
        extractor.extract(
            self.archive(
                [
                    ("candidate-manifest.json", b"{}"),
                    ("candidate-manifest.sigstore.json", b"bundle"),
                ]
            ),
            output,
            bundle,
        )
        self.assertEqual(b"{}", output.read_bytes())
        self.assertEqual(b"bundle", bundle.read_bytes())

    def test_extracts_manifest_and_cosign_bundle_as_the_exact_candidate_artifact(self):
        output = self.root / "candidate.json"
        bundle = self.root / "candidate.sigstore.json"
        extractor.extract(
            self.archive(
                [
                    ("candidate-manifest.json", b"{}"),
                    ("candidate-manifest.sigstore.json", b"bundle"),
                ]
            ),
            output,
            bundle,
        )

        self.assertEqual(b"{}", output.read_bytes())
        self.assertEqual(b"bundle", bundle.read_bytes())

    def test_rejects_extra_and_traversal_entries(self):
        for entries in ([('candidate-manifest.json', b'{}'), ('extra', b'x')], [('../candidate-manifest.json', b'{}')]):
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, "exactly"):
                extractor.extract(
                    self.archive(entries),
                    self.root / "output",
                    self.root / "bundle",
                )

    def test_rejects_compressed_oversized_manifest(self):
        with self.assertRaisesRegex(ValueError, "oversized"):
            extractor.extract(
                self.archive(
                    [
                        ("candidate-manifest.json", b"x" * (extractor.MAX_BYTES + 1)),
                        ("candidate-manifest.sigstore.json", b"bundle"),
                    ]
                ),
                self.root / "output",
                self.root / "bundle",
            )


if __name__ == "__main__":
    unittest.main()
