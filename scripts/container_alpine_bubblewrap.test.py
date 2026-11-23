from hashlib import sha256
from http.client import HTTPException
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import container_alpine_bubblewrap as alpine


class AlpineBuildInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.context = Path(temporary.name)
        self.container = self.context / "container"
        self.container.mkdir()
        (self.container / "Dockerfile").write_text("FROM scratch\n")
        self.manifest = self.container / "bubblewrap-apks.json"
        self.stage = self.container / ".bubblewrap-apks"
        self.packages = {
            "bubblewrap.apk": b"fixture bubblewrap bytes",
            "libcap2.apk": b"fixture libcap2 bytes",
        }
        self.record = {
            "schemaVersion": 1,
            "artifacts": [
                {
                    "file": file,
                    "url": "https://dl-cdn.alpinelinux.org/alpine/v3.23/main/x86_64/"
                    f"{file.removesuffix('.apk')}-1.2-r0.apk",
                    "size": len(contents),
                    "sha256": sha256(contents).hexdigest(),
                }
                for file, contents in self.packages.items()
            ],
        }
        self.write_manifest()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.record))

    def test_stages_exactly_the_two_packages_after_both_are_verified(self):
        def fetch(package):
            self.assertFalse(self.stage.exists())
            return self.packages[package.file]

        with patch.object(alpine, "fetch_package", side_effect=fetch):
            alpine.stage(self.context)

        self.assertEqual(self.packages, {entry.name: entry.read_bytes() for entry in self.stage.iterdir()})

    def test_rejects_other_hosts_schemes_paths_or_package_names_before_fetch(self):
        official = self.record["artifacts"][0]["url"]
        for url in (
            official.replace("https:", "http:"),
            official.replace("dl-cdn.alpinelinux.org", "example.invalid"),
            official.replace("://", "://user@"),
            official.replace("x86_64", "aarch64"),
            official.replace("main/", "community/"),
            official.replace("bubblewrap-", "libcap2-"),
            official.replace("main/", "main/../main/"),
            official + "?mirror=1",
        ):
            with self.subTest(url=url), patch.object(
                alpine, "fetch_package", side_effect=AssertionError("invalid URL reached package fetch"),
            ) as fetch:
                self.record["artifacts"][0]["url"] = url
                self.write_manifest()

                with self.assertRaisesRegex(alpine.InputError, "official Alpine x86_64 package"):
                    alpine.stage(self.context)

                fetch.assert_not_called()

    def test_rejects_duplicate_or_nonpackage_destinations_before_fetch(self):
        for file in ("bubblewrap.apk", "../libcap2.apk", "anything.apk"):
            with self.subTest(file=file), patch.object(alpine, "fetch_package") as fetch:
                self.record["artifacts"][1]["file"] = file
                self.write_manifest()

                with self.assertRaisesRegex(alpine.InputError, "once each"):
                    alpine.stage(self.context)

                fetch.assert_not_called()

    def test_refuses_existing_stage_including_broken_symlinks(self):
        self.stage.symlink_to(self.context / "missing")
        with patch.object(alpine, "fetch_package") as fetch:
            with self.assertRaisesRegex(alpine.InputError, "reserved package stage already exists"):
                alpine.stage(self.context)

        self.assertTrue(self.stage.is_symlink())
        fetch.assert_not_called()

    def test_refuses_symlinked_inputs_without_fetch(self):
        for path, reason in (
            (self.manifest, "manifest must be a regular file"),
            (self.container / "Dockerfile", "requires a regular container/Dockerfile"),
            (self.container, "non-symlink container directory"),
        ):
            with self.subTest(path=path), patch.object(
                alpine, "fetch_package", side_effect=AssertionError("symlinked input reached package fetch"),
            ) as fetch:
                target = self.context / "original-input"
                path.rename(target)
                path.symlink_to(target)
                try:
                    with self.assertRaisesRegex(alpine.InputError, reason):
                        alpine.stage(self.context)
                finally:
                    path.unlink()
                    target.rename(path)

                fetch.assert_not_called()

    def test_refuses_duplicate_manifest_keys(self):
        self.manifest.write_text('{"schemaVersion":2,' + json.dumps(self.record)[1:])

        with patch.object(
            alpine, "fetch_package", side_effect=AssertionError("duplicate manifest key reached package fetch"),
        ) as fetch:
            with self.assertRaisesRegex(alpine.InputError, "duplicate keys"):
                alpine.stage(self.context)

        fetch.assert_not_called()

    def test_failed_second_package_never_creates_the_stage(self):
        with patch.object(alpine, "fetch_package", side_effect=(
            self.packages["bubblewrap.apk"],
            alpine.InputError("libcap2.apk SHA-256 disagrees with the manifest"),
        )):
            with self.assertRaisesRegex(alpine.InputError, "libcap2.apk SHA-256"):
                alpine.stage(self.context)

        self.assertFalse(self.stage.exists())

    def test_unsuccessful_stage_write_removes_only_its_output(self):
        original_open = os.open

        for exception in (OSError("fixture write failure"), KeyboardInterrupt("fixture cancel")):
            def open_file(path, flags, mode=0o777):
                if path == self.stage / "libcap2.apk":
                    raise exception
                return original_open(path, flags, mode)

            with self.subTest(exception=exception), patch.object(
                alpine, "fetch_package", side_effect=self.packages.values(),
            ), patch.object(alpine.os, "open", side_effect=open_file):
                with self.assertRaisesRegex(type(exception), str(exception)):
                    alpine.stage(self.context)

                self.assertFalse(self.stage.exists())
                self.assertTrue(self.manifest.is_file())

    def test_interrupt_after_mkdir_rolls_back_the_owned_stage(self):
        original_mkdir = Path.mkdir

        def cancel_after_mkdir(path, *args, **kwargs):
            original_mkdir(path, *args, **kwargs)
            if path == self.stage:
                signal.raise_signal(signal.SIGINT)

        with patch.object(
            alpine, "fetch_package", side_effect=self.packages.values(),
        ), patch.object(Path, "mkdir", cancel_after_mkdir):
            with self.assertRaises(KeyboardInterrupt):
                alpine.stage(self.context)

        self.assertFalse(self.stage.exists())
        self.assertTrue(self.manifest.is_file())

    def test_stage_created_by_another_writer_is_preserved(self):
        original_mkdir = Path.mkdir
        marker = self.stage / "existing.txt"

        def create_competing_stage(path, *args, **kwargs):
            if path == self.stage:
                original_mkdir(path, mode=0o700)
                marker.write_text("original contents")
            original_mkdir(path, *args, **kwargs)

        with patch.object(
            alpine, "fetch_package", side_effect=self.packages.values(),
        ), patch.object(Path, "mkdir", create_competing_stage):
            with self.assertRaisesRegex(alpine.InputError, "reserved package stage already exists"):
                alpine.stage(self.context)

        self.assertEqual("original contents", marker.read_text())
        self.assertEqual([marker], list(self.stage.iterdir()))

    def test_termination_after_open_rolls_back_the_owned_stage(self):
        original_open = os.open

        def cancel_after_open(path, flags, mode=0o777):
            fd = original_open(path, flags, mode)
            if path == self.stage / "libcap2.apk":
                try:
                    signal.raise_signal(signal.SIGTERM)
                except KeyboardInterrupt:
                    os.close(fd)
                    raise
            return fd

        original_handler = signal.signal(signal.SIGTERM, signal.default_int_handler)
        try:
            with patch.object(
                alpine, "fetch_package", side_effect=self.packages.values(),
            ), patch.object(alpine.os, "open", side_effect=cancel_after_open):
                with self.assertRaises(KeyboardInterrupt):
                    alpine.stage(self.context)
        finally:
            signal.signal(signal.SIGTERM, original_handler)

        self.assertFalse(self.stage.exists())
        self.assertTrue(self.manifest.is_file())

    def test_download_uses_a_direct_unauthenticated_bounded_get(self):
        package, _ = alpine.read_packages(self.manifest)
        contents = self.packages[package.file]
        response = MagicMock(status=200)
        response.getheader.return_value = str(len(contents))
        response.read.return_value = contents
        with patch.object(alpine, "HTTPSConnection") as connection:
            connection.return_value.getresponse.return_value.__enter__.return_value = response

            self.assertEqual(contents, alpine.fetch_package(package))

        connection.assert_called_once_with("dl-cdn.alpinelinux.org", timeout=15)
        connection.return_value.request.assert_called_once_with(
            "GET", "/alpine/v3.23/main/x86_64/bubblewrap-1.2-r0.apk",
            headers={"Accept": "application/vnd.alpine.apk"},
        )
        response.read.assert_called_once_with(len(contents) + 1)

    def test_download_refuses_redirects_and_inconsistent_bytes(self):
        package, _ = alpine.read_packages(self.manifest)
        for status, size, body, reason in (
            (302, None, b"", "requires HTTP 200"),
            (200, "99999", b"", "Content-Length disagrees"),
            (200, None, b"", "downloaded size disagrees"),
            (200, None, b"x" * (package.size + 1), "downloaded size disagrees"),
            (200, None, b"x" * package.size, "SHA-256 disagrees"),
        ):
            with self.subTest(reason=reason), patch.object(alpine, "HTTPSConnection") as connection:
                response = connection.return_value.getresponse.return_value.__enter__.return_value
                response.status = status
                response.getheader.return_value = size
                response.read.return_value = body

                with self.assertRaisesRegex(alpine.InputError, reason):
                    alpine.fetch_package(package)

                connection.return_value.request.assert_called_once()

    def test_download_reports_transport_failure_as_input_rejection(self):
        package, _ = alpine.read_packages(self.manifest)
        with patch.object(alpine, "HTTPSConnection") as connection:
            connection.return_value.getresponse.side_effect = HTTPException("fixture failure")

            with self.assertRaisesRegex(alpine.InputError, "bubblewrap.apk download failed"):
                alpine.fetch_package(package)


if __name__ == "__main__":
    unittest.main()
