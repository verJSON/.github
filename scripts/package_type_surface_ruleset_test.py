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
            # A delegated (null) required-workflow revision is live as an immutable SHA.
            image = json.loads(json.dumps(entry["desired"]))
            for index, position in MODULE.delegated_revisions(image):
                image["rules"][index]["parameters"]["workflows"][position]["sha"] = "a" * 40
            live = {"id": entry["ruleset_id"], "name": entry["name"], "source_type": source_type, **image}
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

    def authn_entry(self):
        organization, _, entries = MODULE.read_policy()
        entry = next(item for item in entries if item["name"] == "authn-type-surface-required-workflow")
        return organization, entry

    def authn_live(self, entry, sha):
        live = json.loads(json.dumps(entry["desired"]))
        live["rules"][0]["parameters"]["workflows"][0]["sha"] = sha
        return {"id": entry["ruleset_id"], "name": entry["name"], "source_type": "Organization", "source": "Verjson", **live}

    def test_the_authn_required_workflow_revision_is_owned_by_its_rotation_tool(self):
        # A pinned copy here went stale at every authn rotation, as the CLI projects
        # record did (#1824); the rotation tool reads, admits, and audits the revision.
        organization, entry = self.authn_entry()
        self.assertIsNone(entry["desired"]["rules"][0]["parameters"]["workflows"][0]["sha"])
        for sha in ("a" * 40, "dc6b2942342ac1a01b4e01a17cbe7a303f31e495"):
            with self.subTest(sha=sha):
                MODULE.validate_live(organization, entry, self.authn_live(entry, sha), entry["desired"])
        for sha in (None, "main", "A" * 40, "a" * 39):
            with self.subTest(sha=sha), self.assertRaisesRegex(MODULE.ContractError, "live image drifted"):
                MODULE.validate_live(organization, entry, self.authn_live(entry, sha), entry["desired"])
        widened = self.authn_live(entry, "a" * 40)
        widened["bypass_actors"] = []
        with self.assertRaisesRegex(MODULE.ContractError, "live image drifted"):
            MODULE.validate_live(organization, entry, widened, entry["desired"])

    def test_apply_never_writes_a_delegated_required_workflow_revision(self):
        organization, entry = self.authn_entry()
        drifted = self.authn_live(entry, "a" * 40)
        drifted["bypass_actors"] = []
        with (
            mock.patch.object(MODULE, "read_live", return_value=drifted),
            mock.patch.object(MODULE, "gh_json") as api,
            self.assertRaisesRegex(MODULE.ContractError, "rotation tool"),
        ):
            MODULE.apply_one(organization, MODULE.EXPECTED_ACK, entry)
        api.assert_not_called()
        with (
            mock.patch.object(MODULE, "read_live", return_value=self.authn_live(entry, "a" * 40)),
            mock.patch.object(MODULE, "gh_json") as api,
            mock.patch("sys.stdout"),
        ):
            MODULE.apply_one(organization, MODULE.EXPECTED_ACK, entry)
        api.assert_not_called()

    def test_a_workflows_binding_must_pin_a_sha_or_delegate_it(self):
        document = json.loads((ROOT / "config/package-type-surface-rulesets.json").read_text())
        index = next(
            position for position, item in enumerate(document["rulesets"])
            if item["name"] == "authn-type-surface-required-workflow"
        )
        for image in ("desired", "preimage"):
            for sha in ("main", "A" * 40, "", 7, ["a" * 40]):
                candidate = json.loads(json.dumps(document))
                if candidate["rulesets"][index][image] is None:
                    continue
                candidate["rulesets"][index][image]["rules"][0]["parameters"]["workflows"][0]["sha"] = sha
                with self.subTest(image=image, sha=sha), tempfile.NamedTemporaryFile("w", suffix=".json") as stream:
                    json.dump(candidate, stream)
                    stream.flush()
                    with self.assertRaisesRegex(MODULE.ContractError, "sha"):
                        MODULE.read_policy(Path(stream.name))

    def test_validate_live_rejects_a_ruleset_id_reused_by_another_name(self):
        _, _, entries = MODULE.read_policy()
        entry = entries[0]
        live = {"id": entry["ruleset_id"], "name": "different-ruleset", "source_type": "Repository", "source": entry["repository"], **entry["preimage"]}
        with self.assertRaises(MODULE.ContractError):
            MODULE.validate_live("Verjson", entry, live, entry["preimage"])


if __name__ == "__main__":
    unittest.main()
