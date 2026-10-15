#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("package_type_surface_ruleset", ROOT / "scripts/package-type-surface-ruleset.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PackageTypeSurfaceRulesetTest(unittest.TestCase):
    def test_policy_requires_only_release_authorization(self):
        _, _, entries = MODULE.read_policy()
        expected = [{"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"}]
        self.assertTrue(entries)
        for entry in entries:
            self.assertEqual(entry["desired"]["bypass_actors"], expected)

    def test_policy_rejects_an_extra_desired_actor(self):
        document = json.loads((ROOT / "config/package-type-surface-rulesets.json").read_text())
        document["rulesets"][0]["desired"]["bypass_actors"].append({"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"})
        with tempfile.NamedTemporaryFile("w", suffix=".json") as stream:
            json.dump(document, stream)
            stream.flush()
            with self.assertRaises(MODULE.ContractError):
                MODULE.read_policy(Path(stream.name))

    def test_validate_live_rejects_a_missing_release_actor(self):
        _, _, entries = MODULE.read_policy()
        entry = entries[0]
        live = {"id": entry["ruleset_id"], "name": entry["name"], "source_type": "Repository", "source": entry["repository"], **entry["desired"]}
        live["bypass_actors"] = []
        with self.assertRaises(MODULE.ContractError):
            MODULE.validate_live("Verjson", entry, live, entry["desired"])

    def test_validate_live_rejects_an_unexpected_source(self):
        _, _, entries = MODULE.read_policy()
        entry = entries[0]
        live = {"id": entry["ruleset_id"], "name": entry["name"], "source_type": "Organization", "source": "Verjson", **entry["desired"]}
        with self.assertRaises(MODULE.ContractError):
            MODULE.validate_live("Verjson", entry, live, entry["desired"])

    def test_validate_live_compares_the_source_as_a_case_insensitive_github_identity(self):
        # The organization login became `verJSON` on 2026-10-02 and the API reports
        # that display case; the reviewed policy spells it `Verjson` (#1779).
        organization, _, entries = MODULE.read_policy()
        for scope, renamed, foreign in (
            ("repository", lambda entry: "verJSON/" + entry["repository"].split("/", 1)[1],
             lambda entry: "Elsewhere/" + entry["repository"].split("/", 1)[1]),
            ("organization", lambda entry: "verJSON", lambda entry: "Elsewhere"),
        ):
            entry = next(entry for entry in entries if entry["scope"] == scope)
            source_type = "Repository" if scope == "repository" else "Organization"
            live = {"id": entry["ruleset_id"], "name": entry["name"], "source_type": source_type, **entry["desired"]}
            with self.subTest(scope=scope, source="renamed display case"):
                MODULE.validate_live(organization, entry, {**live, "source": renamed(entry)}, entry["desired"])
            # U+017F LATIN SMALL LETTER LONG S case-folds to `s`: `Verjſon` folds to `verjson`.
            homoglyph = renamed(entry).replace("verJSON", "Verjſon")
            for refused in (foreign(entry), homoglyph):
                with self.subTest(scope=scope, source=refused), self.assertRaisesRegex(MODULE.ContractError, "source drifted"):
                    MODULE.validate_live(organization, entry, {**live, "source": refused}, entry["desired"])

    def test_apply_selects_a_managed_repository_by_case_insensitive_identity(self):
        _, _, entries = MODULE.read_policy()
        entry = entries[0]
        renamed = "verJSON/" + entry["repository"].split("/", 1)[1]
        with mock.patch.object(MODULE, "apply_one") as apply_one:
            self.assertEqual(MODULE.main(["apply", "--repository", renamed, "--ack", MODULE.EXPECTED_ACK]), 0)
        self.assertEqual(apply_one.call_args.args[2]["repository"], entry["repository"])
        for refused in ("Elsewhere/" + entry["repository"].split("/", 1)[1], renamed.replace("verJSON", "Verjſon")):
            with self.subTest(repository=refused), mock.patch.object(MODULE, "apply_one") as apply_one:
                self.assertEqual(MODULE.main(["apply", "--repository", refused, "--ack", MODULE.EXPECTED_ACK]), 2)
            apply_one.assert_not_called()

    def test_validate_live_rejects_a_ruleset_id_reused_by_another_name(self):
        _, _, entries = MODULE.read_policy()
        entry = entries[0]
        live = {"id": entry["ruleset_id"], "name": "different-ruleset", "source_type": "Repository", "source": entry["repository"], **entry["preimage"]}
        with self.assertRaises(MODULE.ContractError):
            MODULE.validate_live("Verjson", entry, live, entry["preimage"])


if __name__ == "__main__":
    unittest.main()
