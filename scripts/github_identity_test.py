#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import github_identity as identity  # noqa: E402


# Each case-folds to an ASCII letter, so only the charset check refuses them.
LONG_S = "ſ"  # folds to `s`
KELVIN = "K"  # folds to `k`
PREFIX = "https://api.github.com/repos/Verjson/verjson-authn/actions/required_workflows/"


class GithubIdentityTest(unittest.TestCase):
    def test_organization_compares_case_insensitively_over_the_whole_login(self):
        self.assertTrue(identity.same_organization("verJSON", "Verjson"))
        self.assertTrue(identity.same_organization("VERJSON", "Verjson"))
        for observed in ("Elsewhere", "Verjson-x", "Verjso", "verjson ", "", f"Verj{LONG_S}on", None, 1):
            with self.subTest(observed=observed):
                self.assertFalse(identity.same_organization(observed, "Verjson"))

    def test_repository_compares_owner_and_name_together(self):
        self.assertTrue(identity.same_repository("verJSON/verjson-authn", "Verjson/verjson-authn"))
        self.assertTrue(identity.same_repository("Verjson/Verjson-Authn", "Verjson/verjson-authn"))
        for observed in (
            "Elsewhere/verjson-authn",
            "Verjson/verjson-authz",
            "verjson-authn",
            "Verjson",
            "Verjson/verjson-authn/extra",
            "Verjson/verjson-authn\n",
            f"Verj{LONG_S}on/verjson-authn",
            f"Verjson/{KELVIN}it",
            None,
        ):
            with self.subTest(observed=observed):
                self.assertFalse(identity.same_repository(observed, "Verjson/verjson-authn"))
        self.assertFalse(identity.same_repository("Verjson/kit", f"Verjson/{KELVIN}it"))

    def test_an_invalid_configured_identity_matches_nothing(self):
        for configured in ("", "Verjson/", "/verjson-authn", f"Verj{LONG_S}on/verjson-authn", None):
            with self.subTest(configured=configured):
                self.assertFalse(identity.same_repository("Verjson/verjson-authn", configured))
                self.assertFalse(identity.same_organization("Verjson", configured))

    def test_an_invalid_identity_does_not_match_itself(self):
        # Both sides equal and invalid: only validation, not comparison, refuses these.
        for value in (None, "", "Verjson\n", "a/b/c", f"Verj{LONG_S}on"):
            with self.subTest(value=value):
                self.assertFalse(identity.same_organization(value, value))
        for value in (None, "", "Verjson/verjson-authn\n", "a/b/c", f"Verjson/{KELVIN}it"):
            with self.subTest(value=value):
                self.assertFalse(identity.same_repository(value, value))
        for value in ([None], [""], ["a/b/c"]):
            with self.subTest(value=value):
                self.assertFalse(identity.same_repositories(value, value))

    def test_repository_lists_compare_identities_with_multiplicity(self):
        self.assertTrue(identity.same_repositories(["verJSON/b", "verJSON/a"], ["Verjson/a", "Verjson/b"]))
        self.assertTrue(identity.same_repositories([], []))
        for observed in (
            ["verJSON/a"],
            ["verJSON/a", "verJSON/a", "verJSON/b"],
            ["verJSON/a", "Elsewhere/b"],
            ["verJSON/a", f"Verj{LONG_S}on/b"],
        ):
            with self.subTest(observed=observed):
                self.assertFalse(identity.same_repositories(observed, ["Verjson/a", "Verjson/b"]))
        self.assertFalse(identity.same_repositories(["verJSON/a"], [f"Verj{LONG_S}on/a"]))

    def test_url_prefix_compares_only_the_embedded_repository_as_an_identity(self):
        renamed = "https://api.github.com/repos/verJSON/verjson-authn/actions/required_workflows/42"
        self.assertEqual(identity.strip_repository_url_prefix(renamed, PREFIX, "Verjson/verjson-authn"), "42")
        self.assertEqual(identity.strip_repository_url_prefix(PREFIX, PREFIX, "Verjson/verjson-authn"), "")
        for value in (
            renamed.replace("verJSON", "Elsewhere"),
            renamed.replace("verJSON", f"Verj{LONG_S}on"),
            renamed.replace("verjson-authn", "verjson-authz"),
            renamed.replace("/actions/", "/Actions/"),
            renamed.replace("required_workflows", "workflows"),
            renamed.replace("https://api.github.com/", "https://API.github.com/"),
            renamed.replace("https://", "http://"),
            "https://api.github.com/repos/verJSON",
            "https://api.github.com/repos/verJSON/verjson-authn",
            None,
        ):
            with self.subTest(value=value):
                self.assertIsNone(identity.strip_repository_url_prefix(value, PREFIX, "Verjson/verjson-authn"))

    def test_url_prefix_must_embed_the_configured_repository_exactly_once(self):
        value = "https://example.test/Verjson/a/Verjson/a/x"
        for prefix, repository in (
            (PREFIX, "Verjson/verjson-authz"),
            ("https://example.test/Verjson/a/Verjson/a/", "Verjson/a"),
            (PREFIX, f"Verj{LONG_S}on/verjson-authn"),
        ):
            with self.subTest(prefix=prefix, repository=repository):
                self.assertIsNone(identity.strip_repository_url_prefix(value, prefix, repository))
                self.assertIsNone(identity.strip_repository_url_prefix(PREFIX + "1", prefix, repository))


if __name__ == "__main__":
    unittest.main()
