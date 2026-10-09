#!/usr/bin/env python3
"""Behavioral tests for validating artifacts from an unprivileged build job."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import node_release_artifact_manifest as validator  # noqa: E402


class ArtifactManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.artifact_dir = self.root / "artifacts"
        self.artifact_dir.mkdir()
        self.name = "@verjson/example"
        self.version = "1.2.3"
        self.filename = "example-1.2.3.tgz"
        self.expected = [{"name": self.name, "version": self.version}]
        self.write_archive()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_archive(self, package: dict[str, str] | None = None, extra: tuple[str, bytes] | None = None) -> None:
        package = package or {"name": self.name, "version": self.version}
        archive_path = self.artifact_dir / self.filename
        with tarfile.open(archive_path, mode="w:gz") as archive:
            for name, body in (
                ("package/package.json", json.dumps(package).encode("utf-8")),
                ("package/index.js", b"module.exports = true;\n"),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
            if extra is not None:
                info = tarfile.TarInfo(extra[0])
                info.size = len(extra[1])
                archive.addfile(info, io.BytesIO(extra[1]))
        self.refresh_manifest_integrity()

    def refresh_manifest_integrity(self) -> None:
        archive_path = self.artifact_dir / self.filename
        digest = base64.b64encode(hashlib.sha512(archive_path.read_bytes()).digest()).decode("ascii")
        self.artifact_manifest = [{
            "name": self.name,
            "version": self.version,
            "integrity": f"sha512-{digest}",
            "filename": self.filename,
        }]
        (self.artifact_dir / "package-artifacts.json").write_text(
            json.dumps(self.artifact_manifest), encoding="utf-8"
        )

    def test_accepts_a_digest_bound_archive_matching_the_trusted_package_identity(self) -> None:
        result = validator.validate_artifacts(self.artifact_dir, self.expected)

        self.assertEqual(1, len(result))
        self.assertEqual(self.name, result[0]["name"])
        self.assertEqual(self.version, result[0]["version"])
        self.assertEqual(str(self.artifact_dir / self.filename), result[0]["path"])

    def test_rejects_an_artifact_manifest_with_a_different_package_identity(self) -> None:
        self.artifact_manifest[0]["name"] = "@verjson/other"
        (self.artifact_dir / "package-artifacts.json").write_text(
            json.dumps(self.artifact_manifest), encoding="utf-8"
        )

        with self.assertRaisesRegex(ValueError, "trusted package identity"):
            validator.validate_artifacts(self.artifact_dir, self.expected)

    def test_rejects_archive_identity_that_differs_from_the_manifest(self) -> None:
        self.write_archive({"name": "@verjson/other", "version": self.version})

        with self.assertRaisesRegex(ValueError, "identity does not match"):
            validator.validate_artifacts(self.artifact_dir, self.expected)

    def test_rejects_tampering_after_the_archive_integrity_was_recorded(self) -> None:
        with (self.artifact_dir / self.filename).open("ab") as archive_file:
            archive_file.write(b"tampered")

        with self.assertRaisesRegex(ValueError, "integrity does not match"):
            validator.validate_artifacts(self.artifact_dir, self.expected)

    def test_rejects_unsafe_tar_paths(self) -> None:
        self.write_archive(extra=("package/../outside", b"unsafe"))

        with self.assertRaisesRegex(ValueError, "unsafe or duplicate path"):
            validator.validate_artifacts(self.artifact_dir, self.expected)

    def test_rejects_links_inside_the_tarball(self) -> None:
        archive_path = self.artifact_dir / self.filename
        with tarfile.open(archive_path, mode="w:gz") as archive:
            link = tarfile.TarInfo("package/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../outside"
            archive.addfile(link)
        digest = base64.b64encode(hashlib.sha512(archive_path.read_bytes()).digest()).decode("ascii")
        self.artifact_manifest[0]["integrity"] = f"sha512-{digest}"
        (self.artifact_dir / "package-artifacts.json").write_text(
            json.dumps(self.artifact_manifest), encoding="utf-8"
        )

        with self.assertRaisesRegex(ValueError, "link or special file"):
            validator.validate_artifacts(self.artifact_dir, self.expected)

    def test_rejects_unexpected_files_in_the_downloaded_artifact(self) -> None:
        (self.artifact_dir / "unexpected.txt").write_text("extra", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "unexpected files"):
            validator.validate_artifacts(self.artifact_dir, self.expected)

    def test_rejects_oversized_artifact_manifests_before_parsing(self) -> None:
        (self.artifact_dir / "package-artifacts.json").write_bytes(
            b" " * (validator.MAX_MANIFEST_BYTES + 1)
        )

        with self.assertRaisesRegex(ValueError, "within the size limit"):
            validator.validate_artifacts(self.artifact_dir, self.expected)

    def test_rejects_oversized_pax_metadata_before_tarfile_reads_it(self) -> None:
        archive_path = self.artifact_dir / self.filename
        with tarfile.open(archive_path, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            member = tarfile.TarInfo("package/index.js")
            member.pax_headers = {"comment": "x" * (validator.MAX_TAR_METADATA_BYTES + 1)}
            member.size = 0
            archive.addfile(member, io.BytesIO())
        self.refresh_manifest_integrity()

        with self.assertRaisesRegex(ValueError, "metadata field exceeds the read limit"):
            validator.validate_artifacts(self.artifact_dir, self.expected)

    def test_rejects_aggregate_compressed_archive_bytes_over_the_limit(self) -> None:
        archive_size = (self.artifact_dir / self.filename).stat().st_size
        with patch.object(validator, "MAX_TOTAL_ARCHIVE_BYTES", archive_size - 1):
            with self.assertRaisesRegex(ValueError, "aggregate compressed size"):
                validator.validate_artifacts(self.artifact_dir, self.expected)

    def test_rejects_aggregate_expanded_archive_bytes_over_the_limit(self) -> None:
        with patch.object(validator, "MAX_TOTAL_EXPANDED_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "aggregate expanded size"):
                validator.validate_artifacts(self.artifact_dir, self.expected)

    def test_rejects_duplicate_expected_package_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid or duplicate package"):
            validator._expected_packages(self.expected * 2)


if __name__ == "__main__":
    unittest.main()
