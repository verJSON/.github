import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "scripts/org-secret-scope-audit.py"
POLICY = ROOT / "config/org-actions-secret-policy.json"


def audit_module():
    spec = importlib.util.spec_from_file_location("org_secret_scope_audit", AUDIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OrgSecretScopeAuditTest(unittest.TestCase):
    def run_audit(self, policy, listing, selected=None, api_failure=False, raw_policy=None):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            (temp / "policy.json").write_text(
                raw_policy if raw_policy is not None else json.dumps(policy), encoding="utf-8"
            )
            gh = temp / "gh"
            gh.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "if os.environ.get('API_FAILURE') == '1': sys.exit(1)\n"
                "path = sys.argv[-1]\n"
                "if path.endswith('/repositories'): print(os.environ['SELECTED'])\n"
                "else: print(os.environ['LISTING'])\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            env = os.environ | {
                "PATH": f"{temp}:{os.environ['PATH']}",
                "ORG_SECRET_POLICY": str(temp / "policy.json"),
                "LISTING": json.dumps(listing if isinstance(listing, list) else [listing]),
                "SELECTED": json.dumps(selected if isinstance(selected, list) else [selected or {"repositories": []}]),
                "API_FAILURE": "1" if api_failure else "0",
            }
            return subprocess.run(["python3", str(AUDIT)], env=env, capture_output=True, text=True)

    def test_exact_all_and_selected_policy_conforms(self):
        policy = {"organization": "Verjson", "secrets": {
            "FLEET": {"target_visibility": "all", "selected_repositories": [], "consumers": ["fleet"], "reason": "required"},
            "SCOPED": {"target_visibility": "selected", "selected_repositories": ["Verjson/.github"], "consumers": ["policy"], "reason": "only consumer"},
        }}
        listing = {"secrets": [{"name": "FLEET", "visibility": "all"}, {"name": "SCOPED", "visibility": "selected"}]}
        result = self.run_audit(policy, listing, {"repositories": [{"full_name": "Verjson/.github"}]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("secrets=2", result.stdout)

    def test_unmanifested_secret_fails_closed(self):
        policy = {"organization": "Verjson", "secrets": {}}
        result = self.run_audit(policy, {"secrets": [{"name": "NEW_SECRET", "visibility": "all"}]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("unmanifested=['NEW_SECRET']", result.stderr)

    def test_visibility_or_selected_grant_drift_fails(self):
        policy = {"organization": "Verjson", "secrets": {
            "SCOPED": {"target_visibility": "selected", "selected_repositories": ["Verjson/.github"], "consumers": ["policy"], "reason": "only consumer"},
        }}
        listing = {"secrets": [{"name": "SCOPED", "visibility": "selected"}]}
        result = self.run_audit(policy, listing, {"repositories": [{"full_name": "Verjson/other"}]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("selected grants differ", result.stderr)

    def test_api_failure_cannot_report_conformance(self):
        result = self.run_audit({"organization": "Verjson", "secrets": {}}, {}, api_failure=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot establish secret policy state", result.stderr)

    def test_paginated_selected_grants_are_combined(self):
        policy = {"organization": "Verjson", "secrets": {
            "SCOPED": {"target_visibility": "selected", "selected_repositories": ["Verjson/a", "Verjson/b"], "consumers": ["two repos"], "reason": "exact"},
        }}
        listing = [{"secrets": [{"name": "SCOPED", "visibility": "selected"}]}]
        selected = [{"repositories": [{"full_name": "Verjson/b"}]}, {"repositories": [{"full_name": "Verjson/a"}]}]
        result = self.run_audit(policy, listing, selected)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_malformed_policy_and_selected_response_fail_with_diagnostics(self):
        malformed_policy = {"organization": "Verjson", "secrets": {
            "BAD": {"target_visibility": "selected", "selected_repositories": 42, "consumers": "not-a-list", "reason": 7},
        }}
        listing = {"secrets": [{"name": "BAD", "visibility": "selected"}]}
        result = self.run_audit(malformed_policy, listing)
        self.assertEqual(result.returncode, 1)
        self.assertIn("incomplete policy justification", result.stderr)

        valid_policy = {"organization": "Verjson", "secrets": {
            "BAD": {"target_visibility": "selected", "selected_repositories": ["Verjson/.github"], "consumers": ["policy"], "reason": "exact"},
        }}
        result = self.run_audit(valid_policy, listing, [{"wrong": []}])
        self.assertEqual(result.returncode, 1)
        self.assertIn("selected grants page 0 for BAD.repositories must be an array", result.stderr)

    def test_invalid_boundary_shapes_fail_without_tracebacks(self):
        cases = [
            ([], {"secrets": []}, "secret policy must be an object"),
            ({"organization": "Verjson", "secrets": []}, {"secrets": []}, "secret policy.secrets must be an object"),
            ({"organization": "Verjson", "secrets": {}}, "BAD", "secret listing page 0 must be an object"),
            ({"organization": "Verjson", "secrets": {}}, {"secrets": {}}, "secret listing page 0.secrets must be an array"),
            ({"organization": "Verjson", "secrets": {}}, {"secrets": ["BAD"]}, "secret listing entry 0 on page 0 must be an object"),
        ]
        for policy, listing, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                result = self.run_audit(policy, listing)
                self.assertEqual(result.returncode, 2)
                self.assertIn(diagnostic, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_duplicate_live_secrets_and_selected_repositories_fail_closed(self):
        policy = {"organization": "Verjson", "secrets": {"SCOPED": {
            "target_visibility": "selected", "selected_repositories": ["Verjson/.github"],
            "consumers": ["policy"], "reason": "exact",
        }}}
        duplicate_secrets = {"secrets": [
            {"name": "SCOPED", "visibility": "selected"},
            {"name": "SCOPED", "visibility": "selected"},
        ]}
        result = self.run_audit(policy, duplicate_secrets)
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate secret 'SCOPED'", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        result = self.run_audit(
            {}, {}, raw_policy='{"organization":"Verjson","secrets":{"S":{},"S":{}}}'
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate object key 'S'", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        listing = {"secrets": [{"name": "SCOPED", "visibility": "selected"}]}
        selected = {"repositories": [
            {"full_name": "Verjson/.github"}, {"full_name": "Verjson/.github"},
        ]}
        duplicate_policy_repositories = {"organization": "Verjson", "secrets": {"SCOPED": {
            "target_visibility": "selected",
            "selected_repositories": ["Verjson/.github", "Verjson/.github"],
            "consumers": ["policy"], "reason": "exact",
        }}}
        result = self.run_audit(duplicate_policy_repositories, listing, selected)
        self.assertEqual(result.returncode, 1)
        self.assertIn("policy contains duplicate repositories", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        result = self.run_audit(policy, listing, selected)
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate repositories", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def withdrawn_policy(self):
        return {"organization": "Verjson", "secrets": {"RELEASE_APP_PRIVATE_KEY": {
            "target_visibility": "withdrawn", "selected_repositories": [],
            "consumers": ["environment secrets of the main-only role environment"],
            "reason": "Broad copy must be deleted once every consumer reads the environment secret.",
            "custody": "environment-only-migration-residue",
            "withdrawal": {
                "contract": "docs/app-key-environment-rollout.md",
                "tracking": "https://github.com/Verjson/.github/issues/1285",
                "recorded_on": "2026-09-14",
            },
        }}}

    def test_a_withdrawn_secret_conforms_only_while_the_broad_copy_is_absent(self):
        result = self.run_audit(self.withdrawn_policy(), {"secrets": []})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("secrets=1", result.stdout)

    def test_a_withdrawn_secret_that_still_exists_names_the_broad_copy(self):
        listing = {"secrets": [{"name": "RELEASE_APP_PRIVATE_KEY", "visibility": "all"}]}
        result = self.run_audit(self.withdrawn_policy(), listing)
        self.assertEqual(result.returncode, 1)
        self.assertIn("RELEASE_APP_PRIVATE_KEY: organization copy must be withdrawn", result.stderr)
        self.assertNotIn("absent_live", result.stderr)

    def test_a_withdrawn_secret_may_not_name_repositories(self):
        policy = self.withdrawn_policy()
        policy["secrets"]["RELEASE_APP_PRIVATE_KEY"]["selected_repositories"] = ["Verjson/.github"]
        result = self.run_audit(policy, {"secrets": []})
        self.assertEqual(result.returncode, 1)
        self.assertIn("non-selected policy must not name repositories", result.stderr)

    def test_a_missing_non_withdrawn_secret_is_still_reported_absent(self):
        policy = {"organization": "Verjson", "secrets": {"FLEET": {
            "target_visibility": "all", "selected_repositories": [],
            "consumers": ["fleet"], "reason": "required"}}}
        result = self.run_audit(policy, {"secrets": []})
        self.assertEqual(result.returncode, 1)
        self.assertIn("absent_live=['FLEET']", result.stderr)

    def test_environment_only_app_key_cannot_claim_organization_custody(self):
        policy = {"organization": "Verjson", "secrets": {"MERGE_APP_PRIVATE_KEY": {
            "target_visibility": "all", "selected_repositories": [],
            "consumers": ["generated privileged-merge callers"],
            "reason": "fleet callers resolve the key in each caller context",
            "custody": "organization",
        }}}
        listing = {"secrets": [{"name": "MERGE_APP_PRIVATE_KEY", "visibility": "all"}]}
        result = self.run_audit(policy, listing)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "MERGE_APP_PRIVATE_KEY: environment-only App private key cannot declare organization custody",
            result.stderr,
        )
        self.assertNotIn("conformant", result.stdout)


    def test_environment_only_residue_requires_a_withdrawal_record(self):
        policy = {"organization": "Verjson", "secrets": {"RELEASE_APP_PRIVATE_KEY": {
            "target_visibility": "all", "selected_repositories": [],
            "consumers": ["generated release callers"], "reason": "migration state",
            "custody": "environment-only-migration-residue",
        }}}
        listing = {"secrets": [{"name": "RELEASE_APP_PRIVATE_KEY", "visibility": "all"}]}
        result = self.run_audit(policy, listing)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("migration residue must record withdrawal contract, tracking, recorded_on", result.stderr)

    def test_unrecognized_app_private_key_name_fails_closed(self):
        policy = {"organization": "Verjson", "secrets": {"SHADOW_APP_PRIVATE_KEY": {
            "target_visibility": "all", "selected_repositories": [],
            "consumers": ["unknown"], "reason": "unknown",
            "custody": "organization",
        }}}
        listing = {"secrets": [{"name": "SHADOW_APP_PRIVATE_KEY", "visibility": "all"}]}
        result = self.run_audit(policy, listing)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("unrecognized App private-key name has no custody contract", result.stderr)

    def test_organization_custody_app_key_must_be_declared_explicitly(self):
        entry = {
            "target_visibility": "all", "selected_repositories": [],
            "consumers": ["renovate compatibility reconciliation"],
            "reason": "role has no environment custody decision yet",
        }
        listing = {"secrets": [{"name": "RENOVATE_COMPATIBILITY_APP_PRIVATE_KEY", "visibility": "all"}]}
        policy = {"organization": "Verjson", "secrets": {"RENOVATE_COMPATIBILITY_APP_PRIVATE_KEY": entry}}
        result = self.run_audit(policy, listing)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("App private key must declare explicit 'organization' custody", result.stderr)

        declared = {"organization": "Verjson", "secrets": {
            "RENOVATE_COMPATIBILITY_APP_PRIVATE_KEY": entry | {"custody": "organization"},
        }}
        result = self.run_audit(declared, listing)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("secret-scope-policy=conformant", result.stdout)

    def test_non_app_secret_cannot_claim_residue_custody_or_withdrawal(self):
        base = {
            "target_visibility": "all", "selected_repositories": [],
            "consumers": ["fleet"], "reason": "required",
        }
        listing = {"secrets": [{"name": "NODE_AUTH_TOKEN", "visibility": "all"}]}
        cases = [
            (base | {"custody": "environment-only-migration-residue"},
             "only an environment-only App private key may declare"),
            (base | {"withdrawal": {"contract": "x", "tracking": "y", "recorded_on": "z"}},
             "withdrawal is reserved for environment-only migration residue"),
        ]
        for entry, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                result = self.run_audit({"organization": "Verjson", "secrets": {"NODE_AUTH_TOKEN": entry}}, listing)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(diagnostic, result.stderr)

    def test_residue_reports_withdrawal_pending_rather_than_conformance(self):
        policy = {"organization": "Verjson", "secrets": {"MERGE_APP_PRIVATE_KEY": {
            "target_visibility": "all", "selected_repositories": [],
            "consumers": ["generated privileged-merge callers"], "reason": "migration state",
            "custody": "environment-only-migration-residue",
            "withdrawal": {
                "contract": "docs/app-key-environment-rollout.md",
                "tracking": "https://github.com/Verjson/.github/issues/1285",
                "recorded_on": "2026-09-14",
            },
        }}}
        listing = {"secrets": [{"name": "MERGE_APP_PRIVATE_KEY", "visibility": "all"}]}
        result = self.run_audit(policy, listing)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("secret-scope-policy=withdrawal-pending", result.stdout)
        self.assertIn("residue=MERGE_APP_PRIVATE_KEY", result.stdout)
        self.assertNotIn("conformant", result.stdout)

    def test_shipped_policy_conforms_once_environment_only_organization_copies_are_withdrawn(self):
        # #1776 / #1385: the review, merge and release organization copies were withdrawn
        # on 2026-09-27 and the owner confirmed they stay withdrawn. Model the live
        # organization as every other reviewed secret at its target visibility.
        module = audit_module()
        secrets = json.loads(POLICY.read_text(encoding="utf-8"))["secrets"]
        listing = {"secrets": [
            {"name": name, "visibility": rule["target_visibility"]}
            for name, rule in sorted(secrets.items())
            if rule["target_visibility"] != "withdrawn" and name not in module.ENVIRONMENT_ONLY_APP_KEYS
        ]}
        granted = {
            tuple(rule["selected_repositories"])
            for rule in secrets.values()
            if rule["target_visibility"] == "selected"
        }
        self.assertEqual(len(granted), 1, "one selected-grant stub cannot model differing grants")
        selected = {"repositories": [{"full_name": repository} for repository in granted.pop()]}
        result = self.run_audit(None, listing, selected, raw_policy=POLICY.read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("absent_live", result.stderr)
        self.assertIn("secret-scope-policy=withdrawal-pending", result.stdout)

    def test_shipped_policy_declares_custody_for_every_app_private_key(self):
        module = audit_module()
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        secrets = policy["secrets"]
        failures = [failure for name in sorted(secrets) for failure in module.custody_failures(name, secrets[name])]
        self.assertEqual(failures, [])
        residue = {name for name, rule in secrets.items() if rule.get("custody") == module.RESIDUE_CUSTODY}
        self.assertEqual(residue, module.ENVIRONMENT_ONLY_APP_KEYS & set(secrets))
        self.assertTrue(residue, "the shipped policy must still name its migration residue")


class OrgSecretPolicyManifestTest(unittest.TestCase):
    """The reviewed policy accounts for every App key tracked by #1285."""

    def setUp(self):
        self.policy = json.loads((ROOT / "config/org-actions-secret-policy.json").read_text(encoding="utf-8"))
        self.roles = json.loads((ROOT / "config/app-key-roles.json").read_text(encoding="utf-8"))["roles"]

    def test_every_app_key_marked_for_withdrawal_has_a_withdrawal_policy(self):
        for entry in self.roles:
            if entry["organization_copy"] != "withdraw":
                continue
            with self.subTest(secret=entry["secret"]):
                rule = self.policy["secrets"].get(entry["secret"])
                self.assertIsNotNone(rule, f"{entry['secret']} is not in the reviewed org secret policy")
                self.assertIn("withdrawal", rule)
                self.assertIn(rule["target_visibility"], {"all", "selected", "withdrawn"})
                if rule["target_visibility"] in {"all", "selected"}:
                    self.assertEqual(rule.get("custody"), "environment-only-migration-residue")

    def test_no_app_key_is_declared_a_permanent_broad_organization_secret(self):
        for entry in self.roles:
            rule = self.policy["secrets"].get(entry["secret"])
            if rule is not None:
                with self.subTest(secret=entry["secret"]):
                    if rule["target_visibility"] == "all":
                        self.assertEqual(rule.get("custody"), "environment-only-migration-residue")
                        self.assertIn("withdrawal", rule)


if __name__ == "__main__":
    unittest.main()
