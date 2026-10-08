import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("container_dependency_transfer.py")


class ContainerDependencyTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.key = "ab" * 32
        self.source = self.root / "source.tgz"
        self.source.write_bytes(b"private package contents\n")
        self.encrypted = self.root / "source.tgz.enc"
        self.decrypted = self.root / "source.tgz.out"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def run_transfer(self, operation: str, source: Path, destination: Path, key: str | None = None):
        environment = os.environ.copy()
        if key is not None:
            environment["TRANSFER_KEY"] = key
        else:
            environment.pop("TRANSFER_KEY", None)
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                operation,
                "--source",
                str(source),
                "--destination",
                str(destination),
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_private_package_bytes_are_authenticated_and_round_trip(self) -> None:
        encrypted = self.run_transfer("encrypt", self.source, self.encrypted, self.key)
        self.assertEqual(0, encrypted.returncode, encrypted.stderr)
        self.assertNotIn(b"private package contents", self.encrypted.read_bytes())

        decrypted = self.run_transfer("decrypt", self.encrypted, self.decrypted, self.key)
        self.assertEqual(0, decrypted.returncode, decrypted.stderr)
        self.assertEqual(self.source.read_bytes(), self.decrypted.read_bytes())

    def test_wrong_key_fails_without_creating_plaintext(self) -> None:
        self.assertEqual(0, self.run_transfer("encrypt", self.source, self.encrypted, self.key).returncode)

        decrypted = self.run_transfer("decrypt", self.encrypted, self.decrypted, "cd" * 32)

        self.assertNotEqual(0, decrypted.returncode)
        self.assertFalse(self.decrypted.exists())

    def test_tampered_ciphertext_fails_before_plaintext_is_written(self) -> None:
        self.assertEqual(0, self.run_transfer("encrypt", self.source, self.encrypted, self.key).returncode)
        tampered = bytearray(self.encrypted.read_bytes())
        tampered[-1] ^= 1
        self.encrypted.write_bytes(tampered)

        decrypted = self.run_transfer("decrypt", self.encrypted, self.decrypted, self.key)

        self.assertNotEqual(0, decrypted.returncode)
        self.assertFalse(self.decrypted.exists())

    def test_destination_is_never_overwritten(self) -> None:
        self.encrypted.write_text("preserve", encoding="utf-8")

        encrypted = self.run_transfer("encrypt", self.source, self.encrypted, self.key)

        self.assertNotEqual(0, encrypted.returncode)
        self.assertEqual("preserve", self.encrypted.read_text(encoding="utf-8"))

    def test_invalid_key_is_rejected(self) -> None:
        encrypted = self.run_transfer("encrypt", self.source, self.encrypted, "not-a-key")

        self.assertNotEqual(0, encrypted.returncode)
        self.assertFalse(self.encrypted.exists())

    def _encrypted_archive(self, member_name: str = "node_modules/private-package.js") -> Path:
        archive = self.root / "node-modules.tgz"
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo(member_name)
            data = b"module.exports = true\n"
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
        encrypted_archive = self.root / "node-modules.tgz.enc"
        result = self.run_transfer("encrypt", archive, encrypted_archive, self.key)
        self.assertEqual(0, result.returncode, result.stderr)
        return encrypted_archive

    def test_restore_tar_streams_authenticated_archive_without_plaintext_tar_copy(self) -> None:
        encrypted_archive = self._encrypted_archive()
        destination = self.root / "restored-context"

        restored = self.run_transfer("restore-tar", encrypted_archive, destination, self.key)

        self.assertEqual(0, restored.returncode, restored.stderr)
        self.assertEqual(
            "module.exports = true\n",
            (destination / "node_modules/private-package.js").read_text(encoding="utf-8"),
        )
        self.assertEqual([], list(self.root.glob(".dependency-ciphertext-*")))
        self.assertEqual([], list(self.root.glob(".dependency-plaintext-*")))
        self.assertEqual([], list(self.root.glob(".dependency-restore-*")))

    def test_restore_tar_rejects_tampering_before_publishing_destination(self) -> None:
        encrypted_archive = self._encrypted_archive()
        tampered = bytearray(encrypted_archive.read_bytes())
        tampered[-1] ^= 1
        encrypted_archive.write_bytes(tampered)
        destination = self.root / "restored-context"

        result = self.run_transfer("restore-tar", encrypted_archive, destination, self.key)

        self.assertNotEqual(0, result.returncode)
        self.assertFalse(destination.exists())
        self.assertEqual([], list(self.root.glob(".dependency-restore-*")))

    def test_restore_tar_rejects_archive_path_escape(self) -> None:
        encrypted_archive = self._encrypted_archive("../escaped-package.js")
        destination = self.root / "restored-context"

        result = self.run_transfer("restore-tar", encrypted_archive, destination, self.key)

        self.assertNotEqual(0, result.returncode)
        self.assertFalse(destination.exists())
        self.assertFalse((self.root / "escaped-package.js").exists())

    def test_restore_tar_preserves_safe_node_modules_symlinks(self) -> None:
        contents = self.root / "node_modules"
        package = contents / "resolved-package"
        package.mkdir(parents=True)
        (package / "index.js").write_text("module.exports = true\n", encoding="utf-8")
        (contents / "linked-package").symlink_to("resolved-package")
        archive = self.root / "node-modules.tgz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(contents, arcname="node_modules")
        encrypted_archive = self.root / "node-modules.tgz.enc"
        self.assertEqual(0, self.run_transfer("encrypt", archive, encrypted_archive, self.key).returncode)
        destination = self.root / "restored-context"

        result = self.run_transfer("restore-tar", encrypted_archive, destination, self.key)

        self.assertEqual(0, result.returncode, result.stderr)
        restored_link = destination / "node_modules/linked-package"
        self.assertTrue(restored_link.is_symlink())
        self.assertEqual("module.exports = true\n", (restored_link / "index.js").read_text(encoding="utf-8"))

    def test_restore_tar_rejects_symlink_escape(self) -> None:
        archive = self.root / "node-modules.tgz"
        with tarfile.open(archive, "w:gz") as bundle:
            link = tarfile.TarInfo("node_modules/escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../outside"
            bundle.addfile(link)
            member = tarfile.TarInfo("node_modules/escape/escaped.js")
            data = b"must not escape\n"
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
        encrypted_archive = self.root / "node-modules.tgz.enc"
        self.assertEqual(0, self.run_transfer("encrypt", archive, encrypted_archive, self.key).returncode)
        destination = self.root / "restored-context"

        result = self.run_transfer("restore-tar", encrypted_archive, destination, self.key)

        self.assertNotEqual(0, result.returncode)
        self.assertFalse(destination.exists())
        self.assertFalse((self.root.parent / "outside/escaped.js").exists())


if __name__ == "__main__":
    unittest.main()
