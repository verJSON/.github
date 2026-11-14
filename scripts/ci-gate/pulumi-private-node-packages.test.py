#!/usr/bin/env python3
from __future__ import annotations

import base64
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "pulumi_private_node_packages.py"
SPEC = importlib.util.spec_from_file_location("pulumi_private_node_packages", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
pulumi_packages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pulumi_packages)


def private_record(name: str, version: str = "0.20.1") -> dict[str, str]:
    digest = base64.b64encode(b"p" * 64).decode("ascii")
    return {
        "version": version,
        "resolved": f"https://npm.pkg.github.com/download/{name.replace('@verjson/', '@verJSON/')}/{version}/{'a' * 64}",
        "integrity": f"sha512-{digest}",
    }


def lockfile(packages: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": "xops-infra",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {"": {"name": "xops-infra", "version": "1.0.0"}, **packages},
    }


class PrivateNodePackageLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.lock_path = Path(self.temporary_directory.name) / "package-lock.json"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def validate(self, document: object, approved: str) -> tuple[tuple[str, str], ...]:
        self.lock_path.write_text(json.dumps(document), encoding="utf-8")
        return pulumi_packages.validate_private_node_packages(self.lock_path, approved)

    def test_cli_emits_validated_private_archive_rows_as_tsv(self) -> None:
        self.lock_path.write_text(
            json.dumps(
                lockfile(
                    {"node_modules/@verjson/infra": private_record("@verjson/infra")}
                )
            ),
            encoding="utf-8",
        )
        previous_directory = Path.cwd()
        output = io.StringIO()
        try:
            os.chdir(self.temporary_directory.name)
            with patch.dict(
                os.environ, {"APPROVED_PRIVATE_NODE_PACKAGES": "@verjson/infra"}
            ), redirect_stdout(output):
                status = pulumi_packages.main()
        finally:
            os.chdir(previous_directory)

        self.assertEqual(status, 0)
        self.assertEqual(
            output.getvalue(),
            "https://npm.pkg.github.com/download/@verJSON/infra/0.20.1/"
            + "a" * 64
            + "\t"
            + (b"p" * 64).hex()
            + "\n",
        )

    def test_exact_internal_package_allowlist_accepts_v3_lock_entries(self) -> None:
        document = lockfile(
            {
                "node_modules/@verjson/infra": private_record("@verjson/infra"),
                "node_modules/lodash": {
                    "version": "4.17.21",
                    "resolved": "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz",
                    "integrity": "sha512-" + base64.b64encode(b"l" * 64).decode("ascii"),
                },
                "node_modules/string-width-cjs": {
                    "name": "string-width",
                    "version": "4.2.3",
                    "resolved": "https://registry.npmjs.org/string-width/-/string-width-4.2.3.tgz",
                    "integrity": "sha512-" + base64.b64encode(b"s" * 64).decode("ascii"),
                },
            }
        )

        result = self.validate(document, "@verjson/infra\n")

        self.assertEqual(
            result,
            (
                (
                    "https://npm.pkg.github.com/download/@verJSON/infra/0.20.1/" + "a" * 64,
                    (b"p" * 64).hex(),
                ),
            ),
        )

    def test_every_private_lock_entry_must_be_explicitly_approved(self) -> None:
        document = lockfile(
            {
                "node_modules/@verjson/infra": private_record("@verjson/infra"),
                "node_modules/@verjson/authz": private_record("@verjson/authz"),
            }
        )

        with self.assertRaisesRegex(ValueError, "does not exactly match"):
            self.validate(document, "@verjson/infra")

    def test_approved_package_must_appear_in_the_lockfile(self) -> None:
        document = lockfile({"node_modules/@verjson/infra": private_record("@verjson/infra")})

        with self.assertRaisesRegex(ValueError, "does not exactly match"):
            self.validate(document, "@verjson/infra\n@verjson/authz")

    def test_allowlist_rejects_non_verjson_names_and_versions(self) -> None:
        document = lockfile({"node_modules/@verjson/infra": private_record("@verjson/infra")})

        for approved in ("@other/infra", "@verjson/infra@0.20.1", "@verjson/infra\n@other/authz"):
            with self.subTest(approved=approved), self.assertRaisesRegex(ValueError, "package name"):
                self.validate(document, approved)

    def test_private_package_requires_an_exact_https_github_packages_archive(self) -> None:
        document = lockfile(
            {
                "node_modules/@verjson/infra": {
                    **private_record("@verjson/infra"),
                    "resolved": "https://example.invalid/@verjson/infra.tgz",
                }
            }
        )

        with self.assertRaisesRegex(ValueError, "archive URL"):
            self.validate(document, "@verjson/infra")

    def test_private_package_archive_name_and_tsv_safe_url_are_required(self) -> None:
        for resolved in (
            "https://npm.pkg.github.com/download/@verJSON/infra/0.20.1/../other",
            "https://npm.pkg.github.com/download/@verJSON/infra/0.20.1/" + "a" * 64 + "\nextra",
        ):
            with self.subTest(resolved=resolved):
                document = lockfile(
                    {
                        "node_modules/@verjson/infra": {
                            **private_record("@verjson/infra"),
                            "resolved": resolved,
                        }
                    }
                )
                with self.assertRaisesRegex(ValueError, "archive URL"):
                    self.validate(document, "@verjson/infra")

    def test_private_archive_scope_is_ascii_case_insensitive_and_package_version_are_exact(self) -> None:
        records = (
            (
                private_record("@verjson/infra"),
                "https://npm.pkg.github.com/download/@verJSON/Infra/0.20.1/"
                + "a" * 64,
            ),
            (
                private_record("@verjson/infra", "1.0.0-alpha"),
                "https://npm.pkg.github.com/download/@verJSON/infra/1.0.0-Alpha/"
                + "a" * 64,
            ),
            (
                private_record("@verjson/infra"),
                "https://npm.pkg.github.com/download/@verjſon/infra/0.20.1/"
                + "a" * 64,
            ),
        )
        for record, resolved in records:
            with self.subTest(resolved=resolved):
                record["resolved"] = resolved
                document = lockfile({"node_modules/@verjson/infra": record})
                with self.assertRaisesRegex(ValueError, "archive URL"):
                    self.validate(document, "@verjson/infra")

    def test_private_package_requires_sha512_lock_integrity(self) -> None:
        record = private_record("@verjson/infra")
        record["integrity"] = "sha1-eHl6"
        document = lockfile({"node_modules/@verjson/infra": record})

        with self.assertRaisesRegex(ValueError, "SHA-512 integrity"):
            self.validate(document, "@verjson/infra")

    def test_private_archive_url_must_match_locked_package_and_version(self) -> None:
        record = {
            **private_record("@verjson/infra"),
            "resolved": "https://npm.pkg.github.com/download/@verJSON/authz/0.20.1/package.tgz",
        }
        document = lockfile({"node_modules/@verjson/infra": record})

        with self.assertRaisesRegex(ValueError, "archive URL"):
            self.validate(document, "@verjson/infra")

    def test_scoped_lock_path_and_declared_name_must_match(self) -> None:
        record = {**private_record("@verjson/infra"), "name": "@verjson/authz"}
        document = lockfile({"node_modules/@verjson/infra": record})

        with self.assertRaisesRegex(ValueError, "package path"):
            self.validate(document, "@verjson/infra")

    def test_private_registry_cannot_be_used_for_an_unscoped_package(self) -> None:
        document = lockfile(
            {
                "node_modules/infra": {
                    **private_record("@verjson/infra"),
                    "name": "infra",
                }
            }
        )

        with self.assertRaisesRegex(ValueError, "unapproved registry"):
            self.validate(document, "@verjson/infra")

    def test_lockfile_must_use_npm_lockfile_version_two_or_three(self) -> None:
        document = lockfile({"node_modules/@verjson/infra": private_record("@verjson/infra")})
        document["lockfileVersion"] = 1

        with self.assertRaisesRegex(ValueError, "lockfile version"):
            self.validate(document, "@verjson/infra")

    def test_duplicate_json_keys_are_rejected(self) -> None:
        self.lock_path.write_text(
            '{"lockfileVersion":3,"packages":{"":{},"":{}}}', encoding="utf-8"
        )

        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            pulumi_packages.validate_private_node_packages(self.lock_path, "@verjson/infra")


if __name__ == "__main__":
    unittest.main()
