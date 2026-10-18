#!/usr/bin/env python3

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE = Path(__file__).with_name("container_private_dependencies.py")
SPEC = importlib.util.spec_from_file_location("container_private_dependencies", MODULE)
assert SPEC and SPEC.loader
contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = contract
SPEC.loader.exec_module(contract)


VALID_INTEGRITY = "sha512-" + "A" * 86 + "=="


def lock(private_integrity: str | None = None):
    private_integrity = private_integrity or VALID_INTEGRITY
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "consumer"},
            "node_modules/@verjson/pg": {
                "version": "1.2.3",
                "resolved": "https://npm.pkg.github.com/download/@verjson/pg/1.2.3/hash",
                "integrity": private_integrity,
            },
            "node_modules/zod": {
                "version": "1.0.0",
                "resolved": "https://registry.npmjs.org/zod/-/zod-1.0.0.tgz",
                "integrity": VALID_INTEGRITY,
            },
        },
    }


class PrivateDependencyTests(unittest.TestCase):
    def test_accepts_exact_approved_lock_entries(self):
        plan = contract.build_plan(lock(), ["@verjson/pg"])
        self.assertEqual(len(plan), 2)
        self.assertEqual([item["private"] for item in plan], [True, False])

    def test_rejects_unapproved_or_missing_private_packages(self):
        for approved in ([], ["@verjson/other"], ["@verjson/pg", "@verjson/pg"], ["lodash"]):
            with self.subTest(approved=approved), self.assertRaises(contract.DependencyError):
                contract.build_plan(lock(), approved)

    def test_rejects_stale_or_unsafe_lock_identity(self):
        mutations = []
        for field, value in (
            ("integrity", "sha512-not-base64!"),
            ("resolved", "https://attacker.invalid/pkg.tgz"),
            ("resolved", "https://npm.pkg.github.com/download/@verjson/other/1.2.3/hash"),
            ("resolved", "https://npm.pkg.github.com/download/@verjson/pg/1.2.3/hash?token=bad"),
            ("resolved", "https://npm.pkg.github.com/download/%40verjson/pg/1.2.3/hash"),
            ("resolved", "https://registry.npmjs.org/@verjson/pg/-/pg-1.2.3.tgz"),
            ("resolved", "https://npm.pkg.github.com:bad/download/@verjson/pg/1.2.3/hash"),
            ("resolved", "https://registry.npmjs.org/unrelated/-/unrelated-1.0.0.tgz"),
        ):
            candidate = lock()
            candidate["packages"]["node_modules/@verjson/pg"][field] = value
            mutations.append(candidate)
        for candidate in mutations:
            with self.subTest(candidate=candidate), self.assertRaises(contract.DependencyError):
                contract.build_plan(candidate, ["@verjson/pg"])

    def test_accepts_the_owner_spelling_of_the_scope_in_the_download_url(self):
        candidate = lock()
        candidate["packages"]["node_modules/@verjson/pg"]["resolved"] = (
            "https://npm.pkg.github.com/download/@verJSON/pg/1.2.3/hash"
        )
        plan = contract.build_plan(candidate, ["@verjson/pg"])
        self.assertEqual([item["private"] for item in plan], [True, False])

    def test_rejects_a_case_variant_package_name_under_the_owner_scope(self):
        for resolved in (
            "https://npm.pkg.github.com/download/@verJSON/PG/1.2.3/hash",
            "https://npm.pkg.github.com/download/@verJSON/other/1.2.3/hash",
        ):
            candidate = lock()
            candidate["packages"]["node_modules/@verjson/pg"]["resolved"] = resolved
            with self.subTest(resolved=resolved), self.assertRaises(contract.DependencyError):
                contract.build_plan(candidate, ["@verjson/pg"])

    def test_rejects_a_non_ascii_scope_that_lowercases_to_an_approved_scope(self):
        candidate = lock()
        entry = candidate["packages"].pop("node_modules/@verjson/pg")
        # The Kelvin sign lowercases to an ASCII "k"; only an ASCII scope may fold.
        entry["resolved"] = "https://npm.pkg.github.com/download/@\u212Aorg/pg/1.2.3/hash"
        candidate["packages"]["node_modules/@korg/pg"] = entry
        with self.assertRaises(contract.DependencyError):
            contract.build_plan(candidate, ["@korg/pg"])

    def test_rejects_transitive_internal_package_not_in_exact_allowlist(self):
        candidate = lock()
        candidate["packages"]["node_modules/zod/node_modules/@verjson/transitive"] = {
            "version": "1.0.0",
            "resolved": "https://npm.pkg.github.com/download/@verjson/transitive/1.0.0/hash",
            "integrity": VALID_INTEGRITY,
        }
        with self.assertRaises(contract.DependencyError):
            contract.build_plan(candidate, ["@verjson/pg"])

    def test_accepts_canonical_public_package_installed_under_an_alias(self):
        candidate = lock()
        candidate["packages"]["node_modules/zod-alias"] = candidate["packages"].pop("node_modules/zod")
        candidate["packages"]["node_modules/zod-alias"]["name"] = "zod"
        contract.build_plan(candidate, ["@verjson/pg"])

    def test_accepts_bundled_dependency_without_standalone_download_metadata(self):
        candidate = lock()
        candidate["packages"]["node_modules/@tailwindcss/oxide-wasm32-wasi"] = {
            "version": "1.0.0",
            "resolved": "https://registry.npmjs.org/@tailwindcss/oxide-wasm32-wasi/-/oxide-wasm32-wasi-1.0.0.tgz",
            "integrity": VALID_INTEGRITY,
        }
        bundled_path = (
            "node_modules/@tailwindcss/oxide-wasm32-wasi/"
            "node_modules/@emnapi/core"
        )
        candidate["packages"][bundled_path] = {
            "name": "@emnapi/core",
            "version": "1.0.0",
            "inBundle": True,
        }

        plan = contract.build_plan(candidate, ["@verjson/pg"])

        self.assertEqual(len(plan), 3)
        self.assertNotIn("@emnapi/core", [item["url"] for item in plan])

    def test_missing_download_metadata_is_still_rejected_for_nonbundled_entries(self):
        candidate = lock()
        candidate["packages"]["node_modules/zod"].pop("resolved")
        with self.assertRaisesRegex(contract.DependencyError, "exact resolved URL and integrity"):
            contract.build_plan(candidate, ["@verjson/pg"])

    def test_rejects_bundled_entry_without_an_integrity_pinned_parent(self):
        candidate = lock()
        candidate["packages"]["node_modules/@emnapi/core"] = {
            "version": "1.0.0",
            "inBundle": True,
        }
        with self.assertRaisesRegex(contract.DependencyError, "no integrity-pinned parent package"):
            contract.build_plan(candidate, ["@verjson/pg"])

    def test_rejects_lockfile_v1_and_malformed_packages(self):
        candidate = lock()
        candidate["lockfileVersion"] = 1
        with self.assertRaises(contract.DependencyError):
            contract.build_plan(candidate, ["@verjson/pg"])
        candidate = lock()
        candidate["packages"] = []
        with self.assertRaises(contract.DependencyError):
            contract.build_plan(candidate, ["@verjson/pg"])
        candidate = lock()
        candidate["packages"]["node_modules/zod"] = []
        with self.assertRaises(contract.DependencyError):
            contract.build_plan(candidate, ["@verjson/pg"])

if __name__ == "__main__":
    unittest.main()
