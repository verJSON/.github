import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts/provisioning-delegation-validate.py"
CONTRACT = ROOT / "config/provisioning-delegation-contract.json"
INVENTORY = ROOT / "config/app-role-custody-inventory.json"
POLICY = ROOT / "config/org-actions-secret-policy.json"
EXAMPLE = ROOT / "docs/examples/provisioning-delegation-grant.json"
PIN = "0" * 39 + "a"
DIGEST = "sha256:" + "b" * 64


def grant(**overrides):
    document = {
        "schema": "verjson-provisioning-delegation-grant/v1",
        "grant_id": "verjson-2026-09-14-release-cohort",
        "issuer": {"kind": "organization-owner", "identity": "pyousefi", "organization": "Verjson"},
        "executor": {"kind": "agent", "identity": "verjson-cli/provisioning-executor"},
        "contract_pin": {"repository": "Verjson/verjson-ci", "sha": PIN, "contract_version": "1.0.0"},
        "cohort": {
            "completeness": "exact",
            "targets": [{
                "role_id": "release",
                "repository_selection": "all",
                "repositories": [],
                "permission_ceiling": {"contents": "write", "metadata": "read"},
                "effects": ["secret-write"],
            }],
        },
        "effects": ["secret-write"],
        "owner_consent": [{
            "effect": "secret-write",
            "reference": "https://github.com/Verjson/.github/issues/1325#issuecomment-1",
            "approved_by": ["pyousefi"],
        }],
        "evidence": {
            "plan_digest": DIGEST,
            "review": {
                "kind": "github-pull-request",
                "reference": "https://github.com/Verjson/.github/pull/1339",
                "approved_by": ["pyousefi"],
            },
        },
        "expiry": {"not_before": "2026-09-14T00:00:00Z", "not_after": "2026-09-21T00:00:00Z"},
        "revocation": {
            "surface": "github-issue",
            "reference": "https://github.com/Verjson/.github/issues/1325",
        },
    }
    document.update(overrides)
    return document


class ProvisioningDelegationValidateTest(unittest.TestCase):
    def run_validator(self, document, *, contract=None, inventory=None, now="2026-09-15T00:00:00Z", raw=None):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            grant_path = temp / "grant.json"
            grant_path.write_text(raw if raw is not None else json.dumps(document), encoding="utf-8")
            if inventory is None:
                inventory_path = INVENTORY
            else:
                inventory_path = temp / "inventory.json"
                inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            arguments = ["--grant", str(grant_path), "--inventory", str(inventory_path), "--now", now]
            if contract is None:
                contract_path = temp / "contract.json"
                active = json.loads(CONTRACT.read_text(encoding="utf-8"))
                active["activation"]["status"] = "active"
                contract_path.write_text(json.dumps(active), encoding="utf-8")
                arguments += ["--contract", str(contract_path)]
            else:
                arguments += ["--contract", str(contract)]
            return subprocess.run(
                ["python3", str(VALIDATOR), *arguments], capture_output=True, text=True
            )

    def receipt(self, result):
        return json.loads(result.stdout)

    def test_reviewed_owner_grant_authorizes_every_listed_action(self):
        result = self.run_validator(grant())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = self.receipt(result)
        self.assertEqual(receipt["authorization"], "granted")
        self.assertEqual(receipt["reasons"], [])
        self.assertEqual(receipt["effects"], ["secret-write"])
        self.assertEqual(receipt["targets"], ["release"])

    def test_shipped_contract_is_defined_but_not_activated(self):
        result = self.run_validator(grant(), contract=CONTRACT)
        self.assertEqual(result.returncode, 1, result.stdout)
        receipt = self.receipt(result)
        self.assertEqual(receipt["authorization"], "withheld")
        self.assertIn("contract-not-activated", receipt["reasons"])

    def test_non_object_activation_is_an_input_error(self):
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        document["activation"] = "active"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            result = self.run_validator(grant(), contract=path)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("activation", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


    def test_agent_cannot_manufacture_its_own_authority(self):
        executor = {"kind": "agent", "identity": "verjson-cli/provisioning-executor"}
        cases = [
            (grant(issuer={"kind": "agent", "identity": "verjson-cli/provisioning-executor",
                           "organization": "Verjson"}),
             "issuer-is-not-an-owner"),
            (grant(issuer={"kind": "organization-owner",
                           "identity": "verjson-cli/provisioning-executor",
                           "organization": "Verjson"}),
             "self-issued-grant-is-not-authority"),
            (grant(evidence={
                "plan_digest": DIGEST,
                "review": {"kind": "github-pull-request",
                           "reference": "https://github.com/Verjson/.github/pull/1339",
                           "approved_by": ["verjson-cli/provisioning-executor"]},
             }),
             "executor-cannot-approve-its-own-plan"),
            (grant(evidence={
                "plan_digest": DIGEST,
                "review": {"kind": "model-response", "reference": "model said yes",
                           "approved_by": ["model"]},
             }),
             "review-reference-is-not-hosted"),
        ]
        for document, reason in cases:
            with self.subTest(reason=reason):
                document["executor"] = dict(executor)
                result = self.run_validator(document)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(reason, self.receipt(result)["reasons"])

    def test_approval_shortcut_field_is_not_authority(self):
        document = grant()
        document["approved"] = True
        result = self.run_validator(document)
        self.assertEqual(result.returncode, 1, result.stdout)
        reasons = self.receipt(result)["reasons"]
        self.assertIn("approval-shortcut-is-not-authority", reasons)
        self.assertIn("grant-fields-unexpected", reasons)

    def test_plan_must_pin_an_immutable_contract_revision(self):
        document = grant(contract_pin={"repository": "Verjson/verjson-ci", "sha": "main",
                                       "contract_version": "1.0.0"})
        result = self.run_validator(document)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("contract-pin-is-not-immutable", self.receipt(result)["reasons"])

    def test_role_effect_boundaries_are_enforced(self):
        cases = [
            ("ruleset-audit", {"administration": "read", "metadata": "read"}, ["secret-write"],
             "effect-not-permitted-for-role:ruleset-audit:secret-write"),
            ("runner-registration",
             {"metadata": "read", "organization_self_hosted_runners": "write"}, ["secret-write"],
             "effect-not-permitted-for-role:runner-registration:secret-write"),
            ("ai-review", {"checks": "write", "contents": "write", "metadata": "read",
                           "pull_requests": "write"}, ["secret-write"],
             "permission-ceiling-exceeded:ai-review"),
            ("dependency-supersession", {"contents": "read", "metadata": "read",
                                         "pull_requests": "write"}, ["resource-destruction"],
             "effect-not-permitted-for-role:dependency-supersession:resource-destruction"),
        ]
        for role_id, ceiling, effects, reason in cases:
            with self.subTest(role_id=role_id, reason=reason):
                document = grant()
                document["cohort"]["targets"] = [{
                    "role_id": role_id, "repository_selection": "all", "repositories": [],
                    "permission_ceiling": ceiling, "effects": effects,
                }]
                document["effects"] = effects
                document["owner_consent"] = [{
                    "effect": effect,
                    "reference": "https://github.com/Verjson/.github/issues/1325#issuecomment-1",
                    "approved_by": ["pyousefi"],
                } for effect in effects]
                result = self.run_validator(document)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(reason, self.receipt(result)["reasons"])

    def test_incomplete_cohort_inventory_is_never_proof_of_scope(self):
        cases = [
            ({"completeness": "partial", "targets": grant()["cohort"]["targets"]},
             "cohort-inventory-incomplete"),
            ({"completeness": "exact", "targets": []}, "cohort-targets-missing"),
            ({"completeness": "exact", "targets": [{
                "role_id": "release", "repository_selection": "selected", "repositories": [],
                "permission_ceiling": {"contents": "write", "metadata": "read"},
                "effects": ["secret-write"],
             }]}, "selected-repository-inventory-incomplete:release"),
            ({"completeness": "exact", "targets": [{
                "role_id": "unknown-role", "repository_selection": "all", "repositories": [],
                "permission_ceiling": {}, "effects": [],
             }]}, "cohort-target-role-unknown"),
        ]
        for cohort, reason in cases:
            with self.subTest(reason=reason):
                result = self.run_validator(grant(cohort=cohort))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(reason, self.receipt(result)["reasons"])

    def test_grant_window_is_bounded_and_must_be_in_force(self):
        cases = [
            ("2026-09-30T00:00:00Z", grant(), "grant-not-in-force"),
            ("2026-09-13T00:00:00Z", grant(), "grant-not-in-force"),
            ("2026-09-15T00:00:00Z",
             grant(expiry={"not_before": "2026-09-14T00:00:00Z", "not_after": "2026-12-14T00:00:00Z"}),
             "expiry-window-too-long"),
            ("2026-09-15T00:00:00Z",
             grant(expiry={"not_before": "2026-09-14T00:00:00Z", "not_after": "2026-09-14T00:00:00Z"}),
             "expiry-window-invalid"),
        ]
        for now, document, reason in cases:
            with self.subTest(now=now, reason=reason):
                result = self.run_validator(document, now=now)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(reason, self.receipt(result)["reasons"])

    def test_every_gated_effect_needs_its_own_owner_consent(self):
        document = grant()
        document["cohort"]["targets"][0]["effects"] = ["secret-write", "secret-withdrawal"]
        document["effects"] = ["secret-withdrawal", "secret-write"]
        result = self.run_validator(document)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("owner-consent-required:secret-withdrawal", self.receipt(result)["reasons"])

        document["owner_consent"].append({
            "effect": "secret-withdrawal",
            "reference": "https://github.com/Verjson/.github/issues/1325#issuecomment-2",
            "approved_by": ["pyousefi"],
        })
        result = self.run_validator(document)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.receipt(result)["effects"], ["secret-withdrawal", "secret-write"])

    def test_declared_effects_must_match_the_reviewed_plan(self):
        document = grant()
        document["effects"] = ["secret-write", "governance-change"]
        result = self.run_validator(document)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("declared-effects-differ-from-plan", self.receipt(result)["reasons"])

    def test_revocation_surface_is_required(self):
        cases = [
            ({"surface": "chat-message", "reference": "https://github.com/Verjson/.github/issues/1325"},
             "revocation-surface-missing"),
            ({"surface": "github-issue", "reference": "ask the operator"},
             "revocation-reference-is-not-hosted"),
        ]
        for revocation, reason in cases:
            with self.subTest(reason=reason):
                result = self.run_validator(grant(revocation=revocation))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(reason, self.receipt(result)["reasons"])

    def test_unusable_input_fails_without_a_traceback(self):
        result = self.run_validator(None, raw="{not json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("grant is unreadable", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        result = self.run_validator(None, raw="[]")
        self.assertEqual(result.returncode, 2)
        self.assertIn("grant must be a JSON object", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_published_example_grant_matches_the_contract(self):
        document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        result = self.run_validator(document, now=document["expiry"]["not_before"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.receipt(result)["authorization"], "granted")

        result = self.run_validator(document, contract=CONTRACT, now=document["expiry"]["not_before"])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("contract-not-activated", self.receipt(result)["reasons"])

    def test_custody_inventory_covers_every_owned_role(self):
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        roles = inventory["roles"]
        self.assertEqual(set(roles), {
            "ai-review", "merge", "release", "renovate-observation",
            "dependency-supersession", "ruleset-audit", "runner-registration",
        })
        for role_id, role in roles.items():
            with self.subTest(role_id=role_id):
                for field in ("storage", "trust", "rotation", "proof", "permissionCeiling",
                              "permittedEffects", "custodyRecord"):
                    self.assertIn(field, role)
                self.assertFalse(role["rotation"]["automationAllowed"])
                self.assertTrue(role["proof"]["insufficient"])
        self.assertEqual(roles["ruleset-audit"]["permittedEffects"], [])
        for role_id in ("ai-review", "merge", "release"):
            self.assertEqual(roles[role_id]["storage"]["kind"], "repository-environment")
            self.assertIn("organization secret metadata presence",
                          roles[role_id]["proof"]["insufficient"])


    def test_owner_consent_is_derived_from_the_whole_effect_vocabulary(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(contract["effects"]["ownerConsentRequired"], "all")

        document = grant()
        document["owner_consent"] = []
        result = self.run_validator(document)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("owner-consent-required:secret-write", self.receipt(result)["reasons"])

    def test_a_non_list_effect_vocabulary_is_an_input_error(self):
        for vocabulary in ("secret-write", ["secret-write", 3], {"secret-write": True}):
            with self.subTest(vocabulary=vocabulary):
                contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
                contract["activation"]["status"] = "active"
                contract["effects"]["vocabulary"] = vocabulary
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "contract.json"
                    path.write_text(json.dumps(contract), encoding="utf-8")
                    result = self.run_validator(grant(), contract=path)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("vocabulary", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_a_string_vocabulary_never_admits_an_effect_by_substring(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        contract["activation"]["status"] = "active"
        contract["effects"]["vocabulary"] = "secret-write"
        document = grant()
        document["cohort"]["targets"][0]["effects"] = ["write"]
        document["effects"] = ["write"]
        document["owner_consent"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            result = self.run_validator(document, contract=path)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("granted", result.stdout)

    def test_unrecognized_owner_consent_scope_is_an_input_error(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        contract["activation"]["status"] = "active"
        contract["effects"]["ownerConsentRequired"] = "none"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            result = self.run_validator(grant(), contract=path)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("ownerConsentRequired", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_environment_role_keys_match_the_workflow_token_set(self):
        workflow = (ROOT / ".github/workflows/app-key-environment.yml").read_text(encoding="utf-8")
        accepted = re.search(r'case "\$ROLE" in ([a-z|-]+)\)', workflow)
        self.assertIsNotNone(accepted, "app-key-environment.yml no longer enforces a role token set")
        roles = json.loads(INVENTORY.read_text(encoding="utf-8"))["roles"]
        environment_bound = {
            role_id for role_id, role in roles.items()
            if role["storage"]["kind"] == "repository-environment"
        }
        self.assertEqual(environment_bound, set(accepted.group(1).split("|")))
        for role_id in sorted(environment_bound):
            with self.subTest(role_id=role_id):
                self.assertEqual(roles[role_id]["storage"]["environment"], f"{role_id}-app")

    @staticmethod
    def pending_merge_inventory():
        """The shipped inventory with the merge role's organization copy still surviving.

        No shipped role is pending since the 2026-09-27 withdrawal (#1776), so the
        pending-state checks build the shape the inventory held before it.
        """
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        inventory["roles"]["merge"]["migrationStatus"] = {
            "achievedCustody": "environment-only-pending-broad-copy-withdrawal",
            "broadCopiesPending": True,
            "survivingBroadCopies": [{
                "kind": "organization-secret",
                "secret": "MERGE_APP_PRIVATE_KEY",
                "visibility": "all",
                "selectedRepositories": [],
                "policy": "config/org-actions-secret-policy.json",
            }],
            "trackingIssue": "https://github.com/Verjson/.github/issues/1285",
        }
        return inventory

    def test_a_pending_role_that_names_its_copies_is_accepted(self):
        result = self.run_validator(grant(), inventory=self.pending_merge_inventory())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_role_cannot_claim_achieved_custody_while_broad_copies_are_pending(self):
        inventory = self.pending_merge_inventory()
        role = inventory["roles"]["merge"]
        self.assertTrue(role["migrationStatus"]["broadCopiesPending"])
        role["migrationStatus"]["achievedCustody"] = role["custodyDecision"]
        result = self.run_validator(grant(), inventory=inventory)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("merge", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_migration_status_is_required_of_every_role(self):
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        del inventory["roles"]["release"]["migrationStatus"]
        result = self.run_validator(grant(), inventory=inventory)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("release", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_role_without_a_stated_custody_cannot_satisfy_the_comparison(self):
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        role = inventory["roles"]["release"]
        del role["custodyDecision"]
        role["migrationStatus"]["achievedCustody"] = None
        result = self.run_validator(grant(), inventory=inventory)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("release", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_pending_role_must_name_its_copies_and_a_hosted_tracking_reference(self):
        cases = {
            "unnamed copies": ("survivingBroadCopies", [None]),
            "unhosted tracking reference": ("trackingIssue", "x"),
        }
        for label, (field, value) in cases.items():
            with self.subTest(case=label):
                inventory = self.pending_merge_inventory()
                inventory["roles"]["merge"]["migrationStatus"][field] = value
                result = self.run_validator(grant(), inventory=inventory)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("merge", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_the_inventory_must_cover_every_role_the_contract_requires(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(sorted(contract["cohort"]["requiredRoles"]), [
            "ai-review", "dependency-supersession", "merge", "release",
            "renovate-observation", "ruleset-audit", "runner-registration",
        ])

        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        del inventory["roles"]["ruleset-audit"]
        result = self.run_validator(grant(), inventory=inventory)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("ruleset-audit", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_surviving_broad_copies_match_the_declared_org_secret_policy(self):
        roles = json.loads(INVENTORY.read_text(encoding="utf-8"))["roles"]
        declared = json.loads(POLICY.read_text(encoding="utf-8"))["secrets"]

        pending = {
            role_id for role_id, role in roles.items()
            if role["migrationStatus"]["broadCopiesPending"]
        }
        # The review, merge and release organization copies were withdrawn on 2026-09-27.
        self.assertEqual(pending, set())
        for role_id in ("ai-review", "merge", "release"):
            self.assertEqual(roles[role_id]["migrationStatus"]["achievedCustody"], "environment-only")

        for role_id in sorted(pending):
            with self.subTest(role_id=role_id):
                status = roles[role_id]["migrationStatus"]
                self.assertIn("1285", status["trackingIssue"])
                copies = status["survivingBroadCopies"]
                self.assertTrue(copies)
                for copy in copies:
                    self.assertEqual(copy["kind"], "organization-secret")
                    secret = declared[copy["secret"]]
                    self.assertEqual(copy["visibility"], secret["target_visibility"])
                    self.assertEqual(
                        copy["selectedRepositories"], secret.get("selected_repositories", [])
                    )

        for role_id, role in roles.items():
            with self.subTest(role_id=role_id):
                if role_id in pending:
                    continue
                self.assertEqual(role["migrationStatus"]["achievedCustody"], role["custodyDecision"])
                self.assertEqual(role["migrationStatus"]["survivingBroadCopies"], [])
                # An inventory with no surviving copy and a policy approving one would
                # disagree about the same organization secret.
                secret = declared.get(role.get("storage", {}).get("secret"))
                if secret is not None and role["custodyDecision"] == "environment-only":
                    self.assertEqual(secret["target_visibility"], "withdrawn")

    def test_no_role_restates_the_contracts_gated_effect_set(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        gated = set(contract["effects"]["vocabulary"])
        self.assertEqual(contract["effects"]["ownerConsentRequired"], "all")
        for role_id, role in json.loads(INVENTORY.read_text(encoding="utf-8"))["roles"].items():
            with self.subTest(role_id=role_id):
                self.assertNotIn("ownerGatedEffects", role)
                self.assertLessEqual(set(role["permittedEffects"]), gated)

    def test_consent_may_not_exceed_the_reviewed_plan(self):
        document = grant()
        document["owner_consent"].append({
            "effect": "governance-change",
            "reference": "https://github.com/Verjson/.github/issues/1325#issuecomment-3",
            "approved_by": ["pyousefi"],
        })
        result = self.run_validator(document)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("owner-consent-exceeds-plan:governance-change", self.receipt(result)["reasons"])

    def test_environment_configuration_is_a_permitted_gated_effect(self):
        document = grant()
        document["cohort"]["targets"][0]["effects"] = ["environment-configuration", "secret-write"]
        document["effects"] = ["environment-configuration", "secret-write"]
        result = self.run_validator(document)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("owner-consent-required:environment-configuration", self.receipt(result)["reasons"])

        document["owner_consent"].append({
            "effect": "environment-configuration",
            "reference": "https://github.com/Verjson/.github/issues/1325#issuecomment-4",
            "approved_by": ["pyousefi"],
        })
        result = self.run_validator(document)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        document["cohort"]["targets"][0]["role_id"] = "ruleset-audit"
        document["cohort"]["targets"][0]["permission_ceiling"] = {"administration": "read", "metadata": "read"}
        result = self.run_validator(document)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "effect-not-permitted-for-role:ruleset-audit:environment-configuration",
            self.receipt(result)["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
