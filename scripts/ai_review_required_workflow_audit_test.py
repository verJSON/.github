import argparse
import base64
import copy
import subprocess
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = ROOT / "scripts/ai-review-required-workflow-audit.py"
SPEC = importlib.util.spec_from_file_location("ai_review_required_workflow_audit", AUDIT_PATH)
AUDIT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(AUDIT)

# The generated caller family an armed adopter is expected to carry. Held here
# rather than derived from the contract so a contract that forgets the set fails
# these tests instead of silently agreeing with itself.
ADOPTER_CALLERS = (
    ".github/workflows/ai-review-merge.yml",
    ".github/workflows/ai-review-label-rearm.yml",
    ".github/workflows/ai-privileged-merge.yml",
    ".github/workflows/ai-promotion-retry.yml",
)


class AiReviewRequiredWorkflowAuditTest(unittest.TestCase):
    def setUp(self):
        self.contract = AUDIT.read_contract()
        self.contract["arm_property_migration"]["repositories"] = [
            "Verjson/alpha",
            "Verjson/beta",
        ]
        self.ruleset_path = f"orgs/Verjson/rulesets/{self.contract['ruleset_id']}"
        self.workflow = """\
name: AI review authorization arm
on:
  pull_request_target:
  workflow_call:
jobs:
  arm:
    continue-on-error: true
    runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
    steps: []
"""
        # The retired path stays selected until the retarget lands, so the audit
        # must read it too. `pull_request` is what made it schedulable; #642
        # removed it and nothing noticed for four days.
        self.retired_workflow = """\
name: AI review + auto-merge
on:
  pull_request:
  workflow_dispatch:
  workflow_call:
jobs:
  preflight:
    runs-on: ubuntu-24.04
    steps: []
"""
        self.fixture = self.make_fixture()

    def make_fixture(self, owner="Verjson"):
        repositories = [
            {"full_name": f"{owner}/alpha", "private": False, "default_branch": "main"},
            {"full_name": f"{owner}/beta", "private": True, "default_branch": "develop/next"},
        ]
        live_preimage = {"id": self.contract["ruleset_id"], **copy.deepcopy(self.contract["preimage"])}
        node_declaration = next(
            declaration for declaration in self.contract["deterministic_rulesets"]
            if declaration["stack"] == "node"
        )
        actions_declaration = next(
            declaration for declaration in self.contract["deterministic_rulesets"]
            if declaration["stack"] == "actions"
        )
        live_node_ruleset = {"id": node_declaration["id"], **copy.deepcopy(node_declaration["image"])}
        live_actions_ruleset = {
            "id": actions_declaration["id"], **copy.deepcopy(actions_declaration["image"]),
        }
        fixture = {
            self.ruleset_path: [live_preimage],
            "orgs/Verjson/rulesets": [[
                {"id": self.contract["ruleset_id"]},
                {"id": node_declaration["id"]},
                {"id": actions_declaration["id"]},
            ]],
            f"orgs/Verjson/rulesets/{node_declaration['id']}": [live_node_ruleset],
            f"orgs/Verjson/rulesets/{actions_declaration['id']}": [live_actions_ruleset],
            "orgs/Verjson/repos?per_page=100&type=all": [repositories],
            "orgs/Verjson/properties/values?per_page=100": [[
                {
                    "repository_full_name": repository["full_name"],
                    "properties": [
                        {"property_name": "verjson-stack", "value": "node"},
                        {"property_name": "verjson-core-checks", "value": "enforced"},
                        {"property_name": "verjson-ai-authorization", "value": "enforced"},
                    ],
                }
                for repository in repositories
            ]],
            "orgs/Verjson/properties/schema": [[{
                "property_name": self.contract["arm_property"]["name"],
                "source_type": "organization",
                **copy.deepcopy(self.contract["arm_property"]["definition"]),
            }]],
            "orgs/Verjson/actions/secrets/AI_REVIEW_APP_PRIVATE_KEY": [{"visibility": "all"}],
            "orgs/Verjson/actions/variables/AI_REVIEW_APP_ID": [{"visibility": "all", "value": "4528902"}],
            "orgs/Verjson/actions/variables/AI_REVIEW_APP_SLUG": [{"visibility": "all", "value": "ai-review-authorization"}],
            "orgs/Verjson/actions/variables/AI_REVIEW_CLIENT_ID": [{"visibility": "all", "value": "Iv23liObnM1yEH8j9pJu"}],
            "orgs/Verjson/installations": [{"installations": [{
                "app_id": 4528902,
                "app_slug": "ai-review-authorization",
                "repository_selection": "all",
                "suspended_at": None,
                "events": [],
                "permissions": copy.deepcopy(self.contract["authorization"]["app_permissions"]),
            }]}],
            "repositories/1269388380/contents/.github/workflows/gate-rearm.yml?ref=refs/heads/main": [{
                "content": base64.b64encode(self.workflow.encode()).decode(),
            }],
            "repositories/1269388380/contents/.github/workflows/ai-review-merge.yml?ref=refs/heads/main": [{
                "content": base64.b64encode(self.retired_workflow.encode()).decode(),
            }],
        }
        for repository in repositories:
            full_name = repository["full_name"]
            default_branch = repository["default_branch"]
            fixture[f"repos/{full_name}/actions/secrets?per_page=100"] = [{"secrets": []}]
            fixture[f"repos/{full_name}/actions/variables?per_page=100"] = [{"variables": []}]
            fixture[f"repos/{full_name}/contents/.github/workflows?ref={default_branch}"] = [
                [{"name": Path(caller).name, "path": caller, "type": "file"} for caller in ADOPTER_CALLERS]
            ]
            fixture[f"repos/{full_name}/actions/workflows/ai-review-merge.yml"] = [
                {"id": 1, "name": "AI review", "state": "active"}
            ]
            fixture[f"repos/{full_name}/environments/ai-review-app"] = [{
                "name": "ai-review-app",
                "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
                "protection_rules": [{"id": 1, "type": "branch_policy"}],
            }]
            fixture[f"repos/{full_name}/environments/ai-review-app/deployment-branch-policies"] = [
                {"branch_policies": [{"name": default_branch, "type": "branch"}]}
            ]
        return fixture

    def adopter_workflow_listing(self, full_name):
        repository = next(
            item for item in self.fixture["orgs/Verjson/repos?per_page=100&type=all"][0]
            if item["full_name"] == full_name
        )
        path = f"repos/{full_name}/contents/.github/workflows?ref={repository['default_branch']}"
        return self.fixture[path][0]

    def remove_adopter_caller(self, full_name, caller):
        listing = self.adopter_workflow_listing(full_name)
        listing[:] = [entry for entry in listing if entry["path"] != caller]

    def enter_split_state(self):
        """Stub the organization after the dedicated property migration."""
        self.enter_split_state_with("arm_ruleset")

    def enter_legacy_selector_state(self):
        """Stub the split before the dedicated property migration."""
        self.enter_split_state_with("legacy_arm_ruleset")

    def enter_split_state_with(self, arm_image):
        self.arm_id = 4242
        self.fixture[self.ruleset_path] = [{
            "id": self.contract["ruleset_id"], **copy.deepcopy(self.contract["postimage"]),
        }]
        self.fixture["orgs/Verjson/rulesets"][0].append({"id": self.arm_id})
        self.fixture[f"orgs/Verjson/rulesets/{self.arm_id}"] = [{
            "id": self.arm_id, **copy.deepcopy(self.contract[arm_image]),
        }]

    def read(self, path, allow_missing=False):
        if path not in self.fixture:
            if allow_missing:
                return []
            raise AssertionError(f"unexpected API read: {path}")
        return copy.deepcopy(self.fixture[path])

    def select_scope(self, kind, name, repositories):
        self.fixture[f"orgs/Verjson/actions/{kind}/{name}"][0]["visibility"] = "selected"
        self.fixture[f"orgs/Verjson/actions/{kind}/{name}/repositories"] = [{
            "repositories": [{"full_name": repository} for repository in repositories],
        }]

    def live_pull_request_parameters(self, path=None):
        rules = self.fixture[path or self.ruleset_path][0]["rules"]
        return next(rule for rule in rules if rule["type"] == "pull_request")["parameters"]

    def assert_audit_error(self, text):
        with self.assertRaisesRegex(AUDIT.AuditError, text):
            AUDIT.audit(self.contract, self.read)

    def audit_error_message(self):
        with self.assertRaises(AUDIT.AuditError) as caught:
            AUDIT.audit(self.contract, self.read)
        return str(caught.exception)

    def drift_main_protection(self):
        self.live_pull_request_parameters()["required_approving_review_count"] = 0

    def drift_arm_ruleset(self):
        self.enter_split_state()
        self.fixture[f"orgs/Verjson/rulesets/{self.arm_id}"][0]["enforcement"] = "evaluate"

    def drift_deterministic_ruleset(self):
        node = next(
            declaration for declaration in self.contract["deterministic_rulesets"]
            if declaration["stack"] == "node"
        )
        live_node = self.fixture[f"orgs/Verjson/rulesets/{node['id']}"][0]
        live_node["rules"][0]["parameters"]["required_status_checks"][0]["context"] = "ci / elsewhere"

    def workflow_source(self, text):
        path = "repositories/1269388380/contents/.github/workflows/gate-rearm.yml?ref=refs/heads/main"
        self.fixture[path][0]["content"] = base64.b64encode(text.encode()).decode()

    def retired_workflow_source(self, text):
        path = "repositories/1269388380/contents/.github/workflows/ai-review-merge.yml?ref=refs/heads/main"
        self.fixture[path][0]["content"] = base64.b64encode(text.encode()).decode()

    def test_selected_required_workflow_must_declare_a_ruleset_eligible_trigger(self):
        # The live outage: the selected workflow kept only triggers a ruleset
        # cannot fire, so every governed repository lost its required-workflow
        # run and displayed "Workflow configuration invalid" instead.
        self.retired_workflow_source("""\
name: AI review + auto-merge
on:
  workflow_dispatch:
  workflow_call:
jobs:
  preflight:
    runs-on: ubuntu-24.04
    steps: []
""")
        self.assert_audit_error(
            r"ai-review-merge\.yml.*declares only.*workflow_call.*workflow_dispatch",
        )

    def test_merge_group_alone_keeps_the_selected_workflow_schedulable(self):
        self.retired_workflow_source("""\
name: AI review + auto-merge
on:
  merge_group:
jobs:
  preflight:
    runs-on: ubuntu-24.04
    steps: []
""")
        self.assertEqual(AUDIT.audit(self.contract, self.read)["state"], "ready")

    def test_an_unschedulable_selection_outranks_the_fleet_readiness_gap(self):
        # A live outage must not be masked by a rollout precondition. The
        # deterministic-CI gap is why the retarget cannot land yet; the dead
        # selection is why the gate is down right now.
        rows = self.fixture["orgs/Verjson/properties/values?per_page=100"][0]
        for row in rows:
            row["properties"] = []
        self.retired_workflow_source("""\
name: AI review + auto-merge
on:
  workflow_call:
jobs:
  preflight:
    runs-on: ubuntu-24.04
    steps: []
""")
        self.assert_audit_error(r"declares only")

    def test_the_split_selection_is_checked_against_the_replacement(self):
        self.enter_split_state()
        self.workflow_source("""\
name: AI review authorization arm
on:
  workflow_call:
jobs:
  arm:
    continue-on-error: true
    runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
    steps: []
""")
        self.assert_audit_error(r"gate-rearm\.yml.*declares only.*workflow_call")

    def test_the_split_can_be_rendered_while_the_dead_selection_it_repairs_persists(self):
        # The selection being unschedulable is the outage; the split is its
        # remedy. An audit must still refuse, or the finding disappears.
        self.retired_workflow_source("name: x\non:\n  workflow_call:\njobs: {}\n")
        self.assert_audit_error(r"declares only")
        self.assertEqual(
            AUDIT.render_payload(self.contract, "split", self.read),
            self.contract["postimage"],
        )
        self.assertEqual(
            AUDIT.render_payload(self.contract, "arm-ruleset", self.read),
            self.contract["arm_ruleset"],
        )

    def test_the_escape_never_excuses_a_dead_selection_after_the_split(self):
        # Post-split, an unschedulable selection is a fresh regression rather
        # than a state being repaired, so nothing may wave it through.
        self.enter_split_state()
        self.workflow_source("name: x\non:\n  workflow_call:\njobs:\n  arm:\n    steps: []\n")
        with self.assertRaisesRegex(AUDIT.AuditError, r"declares only"):
            AUDIT.audit(self.contract, self.read, allow_unschedulable_selection=True)

    def test_the_escape_does_not_suppress_any_other_precondition(self):
        rows = self.fixture["orgs/Verjson/properties/values?per_page=100"][0]
        beta = next(row for row in rows if row["repository_full_name"] == "Verjson/beta")
        beta["properties"] = [
            {"property_name": "verjson-stack", "value": "helm"},
            {"property_name": "verjson-core-checks", "value": "enforced"},
            {"property_name": "verjson-ai-authorization", "value": "enforced"},
        ]
        self.retired_workflow_source("name: x\non:\n  workflow_call:\njobs: {}\n")
        with self.assertRaisesRegex(AUDIT.AuditError, "armed default branches without canonical"):
            AUDIT.render_payload(self.contract, "split", self.read)

    def test_contract_pins_complete_preimage_postimage_and_rollback(self):
        self.assertEqual(self.contract["rollback_payload"], self.contract["preimage"])
        self.assertEqual(
            [rule["type"] for rule in self.contract["preimage"]["rules"]],
            ["deletion", "non_fast_forward", "required_linear_history", "pull_request", "workflows"],
        )
        self.assertEqual(self.contract["preimage"]["bypass_actors"], [
            {"actor_id": None, "actor_type": "OrganizationAdmin", "bypass_mode": "always"},
            {"actor_id": 2740, "actor_type": "Integration", "bypass_mode": "always"},
            {"actor_id": 4583107, "actor_type": "Integration", "bypass_mode": "always"},
            {"actor_id": 4693283, "actor_type": "Integration", "bypass_mode": "always"},
        ])
        self.assertEqual(self.contract["preimage"]["conditions"], {
            "ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH", "refs/heads/develop"]},
            "repository_name": {"exclude": [], "include": ["~ALL"]},
        })
        pre = copy.deepcopy(self.contract["preimage"])
        post = copy.deepcopy(self.contract["postimage"])
        arm = copy.deepcopy(self.contract["arm_ruleset"])
        self.assertEqual(AUDIT.workflow_path(pre), ".github/workflows/ai-review-merge.yml")
        self.assertEqual(AUDIT.workflow_path(arm), ".github/workflows/gate-rearm.yml")
        # Branch protection is untouched: the postimage is the preimage minus one
        # rule, and every remaining rule still applies to `~ALL`.
        self.assertEqual(
            [rule["type"] for rule in post["rules"]],
            ["deletion", "non_fast_forward", "required_linear_history", "pull_request"],
        )
        self.assertEqual(post["conditions"]["repository_name"], {"exclude": [], "include": ["~ALL"]})
        pre["rules"] = [rule for rule in pre["rules"] if rule["type"] != "workflows"]
        self.assertEqual(pre, post)
        # The arm is scoped by its own property, never by name or by the
        # deterministic-CI property it must remain independent from.
        self.assertNotIn("repository_name", arm["conditions"])
        self.assertEqual(arm["conditions"]["repository_property"]["include"], [{
            "name": "verjson-ai-authorization", "property_values": ["enforced"], "source": "custom",
        }])

    def test_fleet_is_ready_before_the_split_and_recognised_after_it(self):
        report = AUDIT.audit(self.contract, self.read, allow_unschedulable_selection=True)
        self.assertEqual(report["state"], "ready")
        self.assertEqual(report["governed_repositories"], 2)
        self.assertEqual(report["armed_repositories"], 2)
        self.enter_split_state()
        report = AUDIT.audit(self.contract, self.read)
        self.assertEqual(report["state"], "split")
        self.assertEqual(report["current_path"], ".github/workflows/gate-rearm.yml")

    def test_split_state_requires_the_arm_ruleset_to_exist_and_match(self):
        self.fixture[self.ruleset_path] = [{
            "id": self.contract["ruleset_id"], **copy.deepcopy(self.contract["postimage"]),
        }]
        self.assert_audit_error("must have exactly one arm ruleset")
        self.fixture = self.make_fixture()
        self.enter_split_state()
        self.fixture[f"orgs/Verjson/rulesets/{self.arm_id}"][0]["enforcement"] = "evaluate"
        self.assert_audit_error("arm ruleset drifted from both reviewed selector images")

    def test_the_arm_ruleset_must_not_exist_before_the_split(self):
        self.fixture["orgs/Verjson/rulesets"][0].append({"id": 4242})
        self.fixture["orgs/Verjson/rulesets/4242"] = [{
            "id": 4242, **copy.deepcopy(self.contract["arm_ruleset"]),
        }]
        self.assert_audit_error("arm ruleset already exists before the split")

    def test_an_armed_repository_without_deterministic_ci_is_rejected(self):
        rows = self.fixture["orgs/Verjson/properties/values?per_page=100"][0]
        beta = next(row for row in rows if row["repository_full_name"] == "Verjson/beta")
        # Armed (property set) but on an undeclared stack: nothing deterministic
        # stands behind the non-vetoing arm. That is the hazard, and it is rejected.
        beta["properties"] = [
            {"property_name": "verjson-stack", "value": "helm"},
            {"property_name": "verjson-core-checks", "value": "enforced"},
            {"property_name": "verjson-ai-authorization", "value": "enforced"},
        ]
        self.assert_audit_error("armed default branches without canonical deterministic required CI.*Verjson/beta")

    def test_core_checks_do_not_implicitly_arm_a_repository(self):
        # Core-check enrollment and authorization-arm enrollment are independent.
        # A core-check adopter without the dedicated property keeps human review.
        rows = self.fixture["orgs/Verjson/properties/values?per_page=100"][0]
        beta = next(row for row in rows if row["repository_full_name"] == "Verjson/beta")
        beta["properties"] = [
            {"property_name": "verjson-stack", "value": "node"},
            {"property_name": "verjson-core-checks", "value": "enforced"},
        ]
        self.contract["arm_property_migration"]["repositories"] = ["Verjson/alpha"]
        self.enter_split_state()
        report = AUDIT.audit(self.contract, self.read)
        self.assertEqual(report["governed_repositories"], 2)
        self.assertEqual(report["armed_repositories"], 1)

    def test_arm_enrollment_without_core_checks_is_rejected(self):
        rows = self.fixture["orgs/Verjson/properties/values?per_page=100"][0]
        beta = next(row for row in rows if row["repository_full_name"] == "Verjson/beta")
        beta["properties"] = [
            {"property_name": "verjson-stack", "value": "node"},
            {"property_name": "verjson-ai-authorization", "value": "enforced"},
        ]
        self.enter_split_state()
        self.assert_audit_error(
            "armed default branches without canonical deterministic required CI.*Verjson/beta"
        )

    def test_authorization_property_schema_must_exist_and_match(self):
        self.fixture["orgs/Verjson/properties/schema"] = [[]]
        self.enter_split_state()
        self.assert_audit_error("dedicated authorization property schema is missing")

        self.fixture = self.make_fixture()
        self.enter_split_state()
        schema = self.fixture["orgs/Verjson/properties/schema"][0][0]
        schema["allowed_values"] = ["enforced"]
        self.assert_audit_error("dedicated authorization property schema drifted")

    def test_legacy_selector_state_allows_ordered_property_migration(self):
        self.fixture["orgs/Verjson/properties/schema"] = [[]]
        for row in self.fixture["orgs/Verjson/properties/values?per_page=100"][0]:
            row["properties"] = [
                item
                for item in row["properties"]
                if item["property_name"] != self.contract["arm_property"]["name"]
            ]
        self.enter_legacy_selector_state()

        report = AUDIT.audit(self.contract, self.read)
        self.assertEqual(report["state"], "split-legacy-selector")
        self.assertEqual(report["armed_repositories"], 2)
        self.assertEqual(
            AUDIT.render_property_migration_payload(self.contract, "schema", self.read),
            self.contract["arm_property"]["definition"],
        )
        with self.assertRaisesRegex(AUDIT.AuditError, "property schema is missing"):
            AUDIT.render_property_migration_payload(self.contract, "values", self.read)
        with self.assertRaisesRegex(AUDIT.AuditError, "property schema is missing"):
            AUDIT.render_property_migration_payload(self.contract, "ruleset", self.read)

        self.fixture["orgs/Verjson/properties/schema"] = [[{
            "property_name": self.contract["arm_property"]["name"],
            "source_type": "organization",
            **copy.deepcopy(self.contract["arm_property"]["definition"]),
        }]]
        self.assertEqual(
            AUDIT.render_property_migration_payload(self.contract, "values", self.read),
            {
                "repository_names": ["alpha", "beta"],
                "properties": [{
                    "property_name": "verjson-ai-authorization",
                    "value": "enforced",
                }],
            },
        )
        with self.assertRaisesRegex(AUDIT.AuditError, "property cohort differs"):
            AUDIT.render_property_migration_payload(self.contract, "ruleset", self.read)

        for row in self.fixture["orgs/Verjson/properties/values?per_page=100"][0]:
            row["properties"].append({
                "property_name": self.contract["arm_property"]["name"],
                "value": self.contract["arm_property"]["value"],
            })
        self.assertEqual(
            AUDIT.render_property_migration_payload(self.contract, "ruleset", self.read),
            self.contract["arm_ruleset"],
        )

    def test_property_migration_rollback_recovers_a_partial_target_state(self):
        self.enter_split_state()
        self.fixture["orgs/Verjson/properties/schema"] = [[]]
        for row in self.fixture["orgs/Verjson/properties/values?per_page=100"][0]:
            row["properties"] = [
                item
                for item in row["properties"]
                if item["property_name"] != self.contract["arm_property"]["name"]
            ]
        self.assertEqual(
            AUDIT.render_property_migration_payload(self.contract, "rollback", self.read),
            self.contract["legacy_arm_ruleset"],
        )

    def test_property_cleanup_unsets_values_only_after_selector_rollback(self):
        self.enter_legacy_selector_state()
        rows = self.fixture["orgs/Verjson/properties/values?per_page=100"][0]
        beta = next(row for row in rows if row["repository_full_name"] == "Verjson/beta")
        beta["properties"] = [
            item
            for item in beta["properties"]
            if item["property_name"] != self.contract["arm_property"]["name"]
        ]
        self.assertEqual(
            AUDIT.render_property_migration_payload(self.contract, "unset-values", self.read),
            {
                "repository_names": ["alpha", "beta"],
                "properties": [{
                    "property_name": "verjson-ai-authorization",
                    "value": None,
                }],
            },
        )

        self.fixture["orgs/Verjson/properties/values?per_page=100"][0].append({
            "repository_full_name": "Verjson/gamma",
            "properties": [{
                "property_name": self.contract["arm_property"]["name"],
                "value": self.contract["arm_property"]["value"],
            }],
        })
        with self.assertRaisesRegex(AUDIT.AuditError, "outside the reviewed migration set"):
            AUDIT.render_property_migration_payload(self.contract, "unset-values", self.read)

        self.fixture = self.make_fixture()
        self.enter_split_state()
        with self.assertRaisesRegex(AUDIT.AuditError, "restored legacy selector"):
            AUDIT.render_property_migration_payload(self.contract, "unset-values", self.read)

    def test_legacy_selector_names_a_core_check_adopter_outside_the_reviewed_cohort(self):
        # #1775: a repository created after the reviewed list was last updated
        # carried `verjson-core-checks=enforced`, so the legacy selector armed it.
        # The audit failed daily for 12 days without naming it.
        self.contract["arm_property_migration"]["repositories"] = ["Verjson/alpha"]
        self.enter_legacy_selector_state()
        self.assert_audit_error(
            r"legacy arm cohort differs from the reviewed migration set: "
            r"unreviewed=\['Verjson/beta'\] absent=\[\]"
        )

    def test_legacy_cohort_ignores_the_organization_display_case(self):
        # #1775: on 2026-10-02 the organization login became `verJSON`, so the API
        # returns `verJSON/<repo>` while the reviewed list records `Verjson/<repo>`.
        # GitHub names are case-insensitive identities; a display rename changes
        # no membership and must not red the audit.
        self.fixture = self.make_fixture(owner="verJSON")
        self.enter_legacy_selector_state()
        report = AUDIT.audit(self.contract, self.read)
        self.assertEqual(report["state"], "split-legacy-selector")
        self.assertEqual(report["armed_repositories"], 2)

    def test_target_cohort_ignores_the_organization_display_case(self):
        self.fixture = self.make_fixture(owner="verJSON")
        self.enter_split_state()
        report = AUDIT.audit(self.contract, self.read)
        self.assertEqual(report["armed_repositories"], 2)

    def test_property_cleanup_ignores_the_organization_display_case(self):
        self.fixture = self.make_fixture(owner="verJSON")
        self.enter_legacy_selector_state()
        self.assertEqual(
            AUDIT.render_property_migration_payload(self.contract, "unset-values", self.read),
            {
                "repository_names": ["alpha", "beta"],
                "properties": [{
                    "property_name": "verjson-ai-authorization",
                    "value": None,
                }],
            },
        )

    def test_identity_includes_the_owner_not_only_the_repository_name(self):
        # Case-folding the identity must not drop the owner: a same-named
        # repository under another owner is a different repository.
        self.contract["arm_property_migration"]["repositories"] = ["Verjson/alpha"]
        with self.assertRaisesRegex(
            AUDIT.AuditError,
            r"unreviewed=\['Elsewhere/alpha'\] absent=\['Verjson/alpha'\]",
        ):
            AUDIT.require_reviewed_cohort(self.contract, ["Elsewhere/alpha"], "legacy arm cohort")

    def test_a_cohort_listing_one_identity_twice_is_not_the_reviewed_set(self):
        # Case-insensitive membership must not collapse a duplicate that the
        # former list equality rejected.
        self.contract["arm_property_migration"]["repositories"] = ["Verjson/alpha"]
        with self.assertRaisesRegex(AUDIT.AuditError, "lists a repository identity twice"):
            AUDIT.require_reviewed_cohort(
                self.contract, ["Verjson/alpha", "verJSON/alpha"], "legacy arm cohort",
            )

    def test_property_cleanup_rejects_one_identity_reported_twice(self):
        self.enter_legacy_selector_state()
        rows = self.fixture["orgs/Verjson/properties/values?per_page=100"][0]
        alpha = next(row for row in rows if row["repository_full_name"] == "Verjson/alpha")
        rows.append({**copy.deepcopy(alpha), "repository_full_name": "verJSON/alpha"})
        with self.assertRaisesRegex(AUDIT.AuditError, "organization property inventory is invalid"):
            AUDIT.render_property_migration_payload(self.contract, "unset-values", self.read)

    def test_target_selector_requires_the_reviewed_property_cohort(self):
        rows = self.fixture["orgs/Verjson/properties/values?per_page=100"][0]
        beta = next(row for row in rows if row["repository_full_name"] == "Verjson/beta")
        beta["properties"] = [
            item
            for item in beta["properties"]
            if item["property_name"] != self.contract["arm_property"]["name"]
        ]
        self.enter_split_state()
        self.assert_audit_error(
            r"authorization property cohort differs from the reviewed migration set: "
            r"unreviewed=\[\] absent=\['Verjson/beta'\]"
        )

    def test_property_inventory_rejects_malformed_and_duplicate_entries(self):
        rows = self.fixture["orgs/Verjson/properties/values?per_page=100"][0]
        rows[0]["properties"].append({
            "property_name": self.contract["arm_property"]["name"],
            "value": "disabled",
        })
        self.assert_audit_error("organization property values are invalid")

        self.fixture = self.make_fixture()
        rows = self.fixture["orgs/Verjson/properties/values?per_page=100"][0]
        rows[0]["properties"].append("malformed")
        self.assert_audit_error("organization property values are invalid")

    def test_an_arm_governing_nothing_is_rejected(self):
        for row in self.fixture["orgs/Verjson/properties/values?per_page=100"][0]:
            row["properties"] = []
        self.assert_audit_error("the arm would govern nothing")

    def test_any_collateral_ruleset_drift_rejects_both_rollout_and_rollback(self):
        self.fixture[self.ruleset_path][0]["bypass_actors"][1]["bypass_mode"] = "pull_request"
        self.assert_audit_error("matches neither reviewed image on the fields the contract asserts")
        with self.assertRaisesRegex(AUDIT.AuditError, "matches neither reviewed image"):
            AUDIT.render_payload(self.contract, "retarget", self.read)

    def test_a_field_github_adds_to_its_own_schema_is_not_drift(self):
        # GitHub extended `pull_request` parameters with
        # `require_extra_approval_for_unattributed_changes` long after the images
        # were frozen (#1404). Whole-object equality read the vendor's schema
        # growth as collateral drift and killed the audit at its first
        # precondition, where it stayed invisible for as long as nothing ran it.
        self.live_pull_request_parameters()["require_extra_approval_for_unattributed_changes"] = True
        self.assertEqual(AUDIT.audit(self.contract, self.read)["state"], "ready")

    def test_the_report_names_the_live_protection_fields_no_reviewed_image_pins(self):
        # ADR 0188 accepts that a key GitHub adds is invisible to the comparison
        # by construction — including one that weakens protection — and mitigates
        # it with a periodic review that pins newly security-relevant fields. A
        # review that starts by re-reading a whole vendor schema by eye does not
        # happen, so the audit reports the exact candidate set instead (#1410).
        # It still never judges them: an unpinned key is reported, never drift.
        self.assertEqual(AUDIT.audit(self.contract, self.read)["unpinned_live_fields"], [])
        self.live_pull_request_parameters()["require_extra_approval_for_unattributed_changes"] = True
        report = AUDIT.audit(self.contract, self.read)
        self.assertEqual(report["state"], "ready")
        self.assertIn(
            "main-protection.rules[3].parameters.require_extra_approval_for_unattributed_changes",
            report["unpinned_live_fields"],
        )

    def test_the_arm_image_tolerates_a_vendor_key_but_not_a_changed_value(self):
        # The live arm ruleset carries a `sha` GitHub resolves for the selected
        # workflow, which no reviewed image can contain. The same comparison that
        # ignores it must still reject a changed enforcement.
        self.enter_split_state()
        live_arm = self.fixture[f"orgs/Verjson/rulesets/{self.arm_id}"][0]
        live_arm["rules"][0]["parameters"]["workflows"][0]["sha"] = "c597d69"
        self.assertEqual(AUDIT.audit(self.contract, self.read)["state"], "split")
        live_arm["enforcement"] = "evaluate"
        self.assert_audit_error("arm ruleset drifted from both reviewed selector images")

    def test_the_report_names_the_unpinned_fields_of_every_contracted_ruleset(self):
        # The review this feeds has to cover the whole contracted set, not just
        # main-protection: the arm and both core-checks rulesets carry the same
        # residual. The live arm's resolved `sha` and the live required checks'
        # `integration_id` are the unpinned fields that exist today (#1410).
        self.enter_split_state()
        # main-protection is the first surface generated but sorts after the
        # arm ruleset's label, so giving it a residual too makes generation
        # order and sorted order differ -- without that the ordering assertion
        # below holds for either implementation and proves nothing.
        self.fixture[self.ruleset_path][0]["allow_force_pushes_for_maintainers"] = False
        arm = self.fixture[f"orgs/Verjson/rulesets/{self.arm_id}"][0]
        arm["rules"][0]["parameters"]["workflows"][0]["sha"] = "c597d69"
        node = next(
            declaration for declaration in self.contract["deterministic_rulesets"]
            if declaration["stack"] == "node"
        )
        live_node = self.fixture[f"orgs/Verjson/rulesets/{node['id']}"][0]
        live_node["rules"][0]["parameters"]["required_status_checks"][0]["integration_id"] = 15368
        unpinned = AUDIT.audit(self.contract, self.read)["unpinned_live_fields"]
        self.assertIn("ai-authorization-arm-required.rules[0].parameters.workflows[0].sha", unpinned)
        self.assertIn(
            "core-checks-node.rules[0].parameters.required_status_checks[0].integration_id",
            unpinned,
        )
        # Ordered, so the review diff this feeds shows only what actually
        # changed rather than every field moving whenever one is added.
        self.assertEqual(unpinned, sorted(unpinned))

    def test_a_contracted_ruleset_missing_from_the_listing_fails_rather_than_under_reporting(self):
        """An absent candidate must be loud, because its residual reads as none.

        The candidate lookup fell back to the normalized live ruleset, which by
        construction carries no top-level key outside `RULESET_FIELDS` — so a
        contracted ruleset missing from the org listing produced an empty
        residual and the audit passed. That is the silence ADR 0188 rules out:
        the report would say a ruleset pins everything precisely when it could
        not be read at all.
        """
        self.enter_split_state()
        listing = self.fixture["orgs/Verjson/rulesets"][0]
        self.fixture["orgs/Verjson/rulesets"][0] = [
            entry for entry in listing if entry["id"] != self.contract["ruleset_id"]
        ]

        with self.assertRaises(AUDIT.AuditError) as caught:
            AUDIT.audit(self.contract, self.read)

        self.assertIn(str(self.contract["ruleset_id"]), str(caught.exception))

    def test_the_report_names_a_top_level_field_github_adds_but_not_its_metadata(self):
        # `normalize_ruleset` keeps only the mutation payload, so a policy field
        # GitHub adds at the top level is invisible to the comparison *and* would
        # be invisible to the review this report feeds — the exact class ADR 0188
        # warns about. Identity, provenance, and timestamps are not candidates.
        live = self.fixture[self.ruleset_path][0]
        live.update({
            "node_id": "RRS_lACkfake",
            "source": "Verjson",
            "source_type": "Organization",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-09-17T00:00:00Z",
            "_links": {"self": {"href": "https://api.github.com/orgs/Verjson/rulesets/18098028"}},
            "current_user_can_bypass": "always",
            "allow_force_pushes_for_maintainers": True,
        })
        unpinned = AUDIT.audit(self.contract, self.read)["unpinned_live_fields"]
        self.assertIn("main-protection.allow_force_pushes_for_maintainers", unpinned)
        self.assertEqual(
            [field for field in unpinned if "allow_force_pushes_for_maintainers" not in field],
            [],
        )

    def test_the_deterministic_images_tolerate_a_vendor_key_but_not_a_changed_check(self):
        # Live required status checks carry the resolving App's `integration_id`,
        # which the reviewed images do not pin. Ignoring it must not extend to
        # ignoring a context that is no longer required.
        node = next(
            declaration for declaration in self.contract["deterministic_rulesets"]
            if declaration["stack"] == "node"
        )
        live_node = self.fixture[f"orgs/Verjson/rulesets/{node['id']}"][0]
        checks = live_node["rules"][0]["parameters"]["required_status_checks"]
        for check in checks:
            check["integration_id"] = 15368
        self.assertEqual(AUDIT.audit(self.contract, self.read)["state"], "ready")
        checks[0]["context"] = "ci / something-else"
        self.assert_audit_error(f"deterministic ruleset {node['id']} drifted from the fields its reviewed image asserts")

    def test_the_contract_records_the_reviewed_automation_bypass_grants(self):
        # `release-authorization` (4583107) and `merge-authorization` (4693283)
        # hold bypass because they land work on protected default branches — the
        # merge App squash-merges on green CI and cannot do so without it. They
        # were granted and never written down. `ai-review-authorization`
        # (4528902) deliberately holds none: review stays non-privileged, and an
        # audit that re-froze live state would never be able to say so.
        for name in ("preimage", "postimage", "arm_ruleset"):
            self.assertEqual(
                [actor["actor_id"] for actor in self.contract[name]["bypass_actors"]],
                [None, 2740, 4583107, 4693283],
                name,
            )
        for declaration in self.contract["deterministic_rulesets"]:
            self.assertEqual(
                [actor["actor_id"] for actor in declaration["image"]["bypass_actors"]],
                [None, 4583107, 4693283],
                declaration["stack"],
            )
        self.assertNotIn("4528902", json.dumps(self.contract))

    def test_the_node_image_records_the_decided_changelog_contract_requirement(self):
        # ADR 0186 made `changelog-contract` a required context on the node
        # stack. The reviewed image predates that decision, so the audit reported
        # as drift a requirement the organization had already taken and written
        # down — the mirror of re-freezing live state, and just as blinding.
        node = next(
            declaration for declaration in self.contract["deterministic_rulesets"]
            if declaration["stack"] == "node"
        )
        checks = node["image"]["rules"][0]["parameters"]["required_status_checks"]
        self.assertEqual(
            [check["context"] for check in checks],
            ["ci / build-test", "ci / eligibility", "changelog-contract"],
        )
        self.assertIn("changelog-contract", self.contract["deterministic_required_status_contexts"])

    def test_tolerating_an_unknown_key_never_tolerates_an_unexpected_value(self):
        # The one property that distinguishes this comparison from "accept what
        # the API returns". Each case asserts the same field the contract pins,
        # alongside a vendor key that must stay ignored.
        parameters = self.live_pull_request_parameters()
        parameters["require_extra_approval_for_unattributed_changes"] = True
        for value in (0, 2, "1", None):
            with self.subTest(required_approving_review_count=value):
                parameters["required_approving_review_count"] = value
                self.assert_audit_error("matches neither reviewed image on the fields the contract asserts")
        parameters["required_approving_review_count"] = 1
        self.assertEqual(AUDIT.audit(self.contract, self.read)["state"], "ready")

    def test_a_pinned_true_is_not_satisfied_by_a_truthy_one(self):
        # JSON `true` and `1` are distinct policy answers, and Python's `1 == True`
        # would otherwise let a ruleset flip type without reporting drift.
        self.live_pull_request_parameters()["require_code_owner_review"] = 1
        self.assert_audit_error("matches neither reviewed image on the fields the contract asserts")

    def test_an_asserted_key_the_api_stops_returning_is_drift(self):
        # The failure mode a subset comparison invites: silence when the field
        # the contract depends on simply is not there any more.
        del self.live_pull_request_parameters()["require_last_push_approval"]
        self.assert_audit_error("matches neither reviewed image on the fields the contract asserts")

    def test_an_unrecorded_bypass_actor_is_still_drift(self):
        # Unknown-key tolerance must not leak into list membership: a bypass
        # grant nobody wrote down is exactly what this audit exists to surface.
        self.fixture[self.ruleset_path][0]["bypass_actors"].append(
            {"actor_id": 4528902, "actor_type": "Integration", "bypass_mode": "always"},
        )
        self.assert_audit_error("matches neither reviewed image on the fields the contract asserts")

    def test_a_drift_report_names_the_field_that_disagrees(self):
        # "differs from both images" sent the reader to diff two 40-line objects
        # by eye, which is how a vendor-added key looked the same as a real
        # regression for as long as it did.
        self.live_pull_request_parameters()["required_approving_review_count"] = 0
        self.assert_audit_error(r"required_approving_review_count: 0 is not 1")

    def test_every_drift_report_describes_the_field_scoped_comparison_it_runs(self):
        # The comparison stopped being whole-object in #1404, but every report
        # still said "full reviewed image". That tells a maintainer the audit
        # compared everything, and sends them hunting for drift in keys the
        # contract never pinned — the opposite of what actually happened (#1411).
        for label, drift, expected in (
            (
                "main-protection",
                self.drift_main_protection,
                "main-protection matches neither reviewed image on the fields the contract asserts",
            ),
            (
                "arm",
                self.drift_arm_ruleset,
                "arm ruleset drifted from both reviewed selector images",
            ),
            (
                "deterministic",
                self.drift_deterministic_ruleset,
                "drifted from the fields its reviewed image asserts",
            ),
        ):
            with self.subTest(surface=label):
                self.fixture = self.make_fixture()
                drift()
                message = self.audit_error_message()
                self.assertIn(expected, message)
                self.assertNotIn("full reviewed", message)

    def test_rendered_payloads_are_verified_and_the_tool_has_no_mutation_path(self):
        self.assertEqual(
            AUDIT.render_payload(self.contract, "split", self.read),
            self.contract["postimage"],
        )
        self.assertEqual(
            AUDIT.render_payload(self.contract, "arm-ruleset", self.read),
            self.contract["arm_ruleset"],
        )
        self.enter_split_state()
        self.assertEqual(
            AUDIT.render_payload(self.contract, "rollback", self.read),
            self.contract["rollback_payload"],
        )
        source = AUDIT_PATH.read_text(encoding="utf-8")
        self.assertNotIn('"--method"', source)
        self.assertNotIn("rulesets/PUT", source)
        self.assertNotIn("rulesets/PATCH", source)

    def test_property_migration_cli_flags_dispatch_to_the_expected_renderer_mode(self):
        flag_modes = {
            "render_arm_property_schema_payload": "schema",
            "render_arm_property_values_payload": "values",
            "render_arm_property_ruleset_payload": "ruleset",
            "render_arm_property_rollback_payload": "rollback",
            "render_arm_property_unset_values_payload": "unset-values",
        }
        all_flags = {
            "render_split_payload",
            "render_arm_ruleset_payload",
            "render_rollback_payload",
            *flag_modes,
        }
        for selected_flag, mode in flag_modes.items():
            with self.subTest(flag=selected_flag):
                args = argparse.Namespace(**{
                    flag: flag == selected_flag
                    for flag in all_flags
                })
                with (
                    mock.patch.object(AUDIT, "parse_args", return_value=args),
                    mock.patch.object(AUDIT, "read_contract", return_value=self.contract),
                    mock.patch.object(
                        AUDIT,
                        "render_property_migration_payload",
                        return_value={"mode": mode},
                    ) as render,
                    mock.patch("builtins.print"),
                ):
                    self.assertEqual(AUDIT.main(), 0)
                render.assert_called_once_with(self.contract, mode)

    def test_retired_replacement_and_app_status_cannot_coexist(self):
        second = {"id": 99, **copy.deepcopy(self.contract["arm_ruleset"])}
        self.fixture["orgs/Verjson/rulesets"][0].append({"id": 99})
        self.fixture["orgs/Verjson/rulesets/99"] = [second]
        self.assert_audit_error("arm ruleset already exists before the split")

        self.fixture = self.make_fixture()
        self.fixture["orgs/Verjson/rulesets"][0].append({"id": 99})
        self.fixture["orgs/Verjson/rulesets/99"] = [{"id": 99, "rules": [{
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "AI review authorization"}]},
        }]}]
        self.assert_audit_error("status is also required")

    def test_repository_level_secret_and_variable_shadowing_are_rejected(self):
        self.fixture["repos/Verjson/alpha/actions/secrets?per_page=100"][0]["secrets"] = [
            {"name": "AI_REVIEW_APP_PRIVATE_KEY"},
        ]
        self.assert_audit_error("credential shadowing.*alpha:secret")
        self.fixture = self.make_fixture()
        self.fixture["repos/Verjson/beta/actions/variables?per_page=100"][0]["variables"] = [
            {"name": "AI_REVIEW_CLIENT_ID"},
        ]
        self.assert_audit_error("credential shadowing.*beta:variable")

    def test_secret_and_variables_must_reach_every_governed_repository(self):
        self.select_scope("secrets", "AI_REVIEW_APP_PRIVATE_KEY", ["Verjson/alpha"])
        self.assert_audit_error("AI_REVIEW_APP_PRIVATE_KEY.*missing=1.*Verjson/beta")
        self.fixture = self.make_fixture()
        self.select_scope("variables", "AI_REVIEW_APP_ID", ["Verjson/beta"])
        self.assert_audit_error("AI_REVIEW_APP_ID.*missing=1.*Verjson/alpha")

    def test_app_permissions_and_events_are_exact(self):
        self.assertEqual(self.contract["authorization"], {
            "private_key_secret": "AI_REVIEW_APP_PRIVATE_KEY",
            "variables": ["AI_REVIEW_APP_ID", "AI_REVIEW_APP_SLUG", "AI_REVIEW_CLIENT_ID"],
            "app_permissions": {
                "checks": "write",
                "contents": "read",
                "metadata": "read",
                "pull_requests": "write",
            },
            "app_events": [],
        })
        installation = self.fixture["orgs/Verjson/installations"][0]["installations"][0]
        installation["permissions"]["issues"] = "read"
        self.assert_audit_error("permission map drifted")
        self.fixture = self.make_fixture()
        self.fixture["orgs/Verjson/installations"][0]["installations"][0]["events"] = ["pull_request"]
        self.assert_audit_error("event subscriptions drifted")

    def test_replacement_requires_supported_trigger_nonveto_and_trusted_lane(self):
        self.workflow_source(self.workflow.replace("pull_request_target", "workflow_dispatch"))
        self.assert_audit_error("lacks pull_request_target")
        self.fixture = self.make_fixture()
        self.workflow_source(self.workflow.replace("    continue-on-error: true\n", ""))
        self.assert_audit_error("can veto ADR 0090")
        self.fixture = self.make_fixture()
        self.workflow_source(self.workflow.replace("CI_LANE_TRUSTED", "CI_LANE_PRIVILEGED"))
        self.assert_audit_error("not routed through the trusted lane")

    def assert_contract_rejected(self, mutate, diagnostic):
        contract = copy.deepcopy(self.contract)
        mutate(contract)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(AUDIT.AuditError, diagnostic):
                AUDIT.read_contract(path)

    def test_arm_property_contract_rejects_unsafe_or_ambiguous_configuration(self):
        def reuse_core_property(contract):
            contract["arm_property"]["name"] = contract["core_checks_property"]

        self.assert_contract_rejected(reuse_core_property, "must be independent")

        def delegate_property_values(contract):
            contract["arm_property"]["definition"]["values_editable_by"] = "org_and_repo_actors"

        self.assert_contract_rejected(delegate_property_values, "definition is invalid")

        def duplicate_cohort(contract):
            contract["arm_property_migration"]["repositories"].append("Verjson/alpha")

        self.assert_contract_rejected(duplicate_cohort, "repositories are invalid")

        def case_duplicate_cohort(contract):
            # Identity is case-insensitive (#1775), so a spelling variant is the
            # same repository listed twice.
            contract["arm_property_migration"]["repositories"].append("Verjson/Alpha")
            contract["arm_property_migration"]["repositories"].sort()

        self.assert_contract_rejected(case_duplicate_cohort, "repositories are invalid")

        def homoglyph_cohort(contract):
            # U+212A KELVIN SIGN casefolds to ASCII `k`, so this entry would
            # silently review the live repository `Verjson/kappa`.
            contract["arm_property_migration"]["repositories"].append("Verjson/\u212aappa")
            contract["arm_property_migration"]["repositories"].sort()

        self.assert_contract_rejected(homoglyph_cohort, "repositories are invalid")

        def nested_path_cohort(contract):
            contract["arm_property_migration"]["repositories"].append("Verjson/gamma/delta")
            contract["arm_property_migration"]["repositories"].sort()

        self.assert_contract_rejected(nested_path_cohort, "repositories are invalid")

        def trailing_newline_cohort(contract):
            # `re.match(...$)` would accept this, because `$` matches before a
            # final newline; only a full match rejects it.
            contract["arm_property_migration"]["repositories"].append("Verjson/alpha\n")
            contract["arm_property_migration"]["repositories"].sort()

        self.assert_contract_rejected(trailing_newline_cohort, "repositories are invalid")

        def empty_name_cohort(contract):
            contract["arm_property_migration"]["repositories"].append("Verjson/")
            contract["arm_property_migration"]["repositories"].sort()

        self.assert_contract_rejected(empty_name_cohort, "repositories are invalid")

        def unordered_cohort(contract):
            contract["arm_property_migration"]["repositories"].reverse()

        self.assert_contract_rejected(unordered_cohort, "repositories are invalid")

    def test_the_split_may_not_smuggle_in_any_other_protection_change(self):
        # The postimage is derived, not trusted. A contract that also relaxes
        # enforcement, widens a bypass, or drops another rule while "just moving
        # the workflows rule" is exactly what a reviewer would miss in a 7 KB diff.
        def relax(contract):
            contract["postimage"]["enforcement"] = "evaluate"
        self.assert_contract_rejected(relax, "beyond removing the workflows rule")

        def widen_bypass(contract):
            contract["postimage"]["bypass_actors"].append(
                {"actor_id": 5, "actor_type": "Integration", "bypass_mode": "always"},
            )
        self.assert_contract_rejected(widen_bypass, "beyond removing the workflows rule")

        def drop_protection(contract):
            contract["postimage"]["rules"] = [
                rule for rule in contract["postimage"]["rules"] if rule["type"] != "non_fast_forward"
            ]
        self.assert_contract_rejected(drop_protection, "beyond removing the workflows rule")

        def narrow_scope(contract):
            contract["postimage"]["conditions"]["repository_name"]["include"] = ["verjson-leads"]
        self.assert_contract_rejected(narrow_scope, "beyond removing the workflows rule")

    def test_the_arm_may_not_be_scoped_by_name_or_to_the_wrong_property(self):
        # Scoping the arm by name would re-create the ~ALL hazard the split
        # exists to remove, and silently decouple it from deterministic CI.
        def by_name(contract):
            contract["arm_ruleset"]["conditions"]["repository_name"] = {"exclude": [], "include": ["~ALL"]}
        self.assert_contract_rejected(by_name, "scoped by repository property, not by name")

        def wrong_property(contract):
            contract["arm_ruleset"]["conditions"]["repository_property"]["include"][0]["name"] = "verjson-stack"
        self.assert_contract_rejected(wrong_property, "not scoped to the dedicated authorization property")

        def wrong_value(contract):
            contract["arm_ruleset"]["conditions"]["repository_property"]["include"][0]["property_values"] = ["all"]
        self.assert_contract_rejected(wrong_value, "not scoped to the dedicated authorization property")

        def other_refs(contract):
            contract["arm_ruleset"]["conditions"]["ref_name"]["include"] = ["~ALL"]
        self.assert_contract_rejected(other_refs, "targets different refs")

    def test_the_relocated_rule_must_be_the_same_rule(self):
        def creation_bypass(contract):
            contract["arm_ruleset"]["rules"][0]["parameters"]["do_not_enforce_on_create"] = False
        self.assert_contract_rejected(creation_bypass, "repository-creation bypass drifted")

        def mutable_ref(contract):
            contract["arm_ruleset"]["rules"][0]["parameters"]["workflows"][0]["ref"] = "refs/heads/dev"
        self.assert_contract_rejected(mutable_ref, "not the relocated workflows rule")

        def wrong_source(contract):
            contract["arm_ruleset"]["rules"][0]["parameters"]["workflows"][0]["repository_id"] = 1
        self.assert_contract_rejected(wrong_source, "not the relocated workflows rule")

        def inactive(contract):
            contract["arm_ruleset"]["enforcement"] = "evaluate"
        self.assert_contract_rejected(inactive, "arm ruleset must be active")

        def wider_bypass(contract):
            contract["arm_ruleset"]["bypass_actors"] = []
        self.assert_contract_rejected(wider_bypass, "bypass actors diverge")

    def test_a_half_installed_adopter_caller_set_is_reported(self):
        # `verjson-agents` under #1401: the merge lane is installed and the
        # review caller is not, so "does this adopter have any AI caller?"
        # answers yes while `gate-rearm.yml` still cannot read the caller it
        # hard-requires and every pull request in the repository fails the arm.
        self.remove_adopter_caller("Verjson/alpha", ".github/workflows/ai-review-merge.yml")
        self.assert_audit_error(
            r"cannot satisfy the arm.*Verjson/alpha:\.github/workflows/ai-review-merge\.yml",
        )

    def test_an_armed_adopter_without_the_review_environment_is_reported(self):
        # ADR 0187: GitHub creates a referenced environment on first use with no
        # protection rules, so an adopter that installs the caller without
        # creating `ai-review-app` gets a working review lane and an unprotected
        # environment. Every signal reports success; the absent control produces
        # no error at all. Only a read-back can see it.
        del self.fixture["repos/Verjson/alpha/environments/ai-review-app"]
        del self.fixture["repos/Verjson/alpha/environments/ai-review-app/deployment-branch-policies"]
        self.assert_audit_error(r"review environment.*Verjson/alpha:ai-review-app:absent")

    def test_an_environment_github_auto_created_is_not_a_provisioned_one(self):
        # The exact shape GitHub leaves behind when a caller names an
        # environment that does not exist: it exists afterwards, and it
        # restricts nothing. Presence is therefore not the assertion; the
        # branch policy is (ADR 0187).
        environment = self.fixture["repos/Verjson/alpha/environments/ai-review-app"][0]
        environment["deployment_branch_policy"] = None
        environment["protection_rules"] = []
        self.assert_audit_error(
            r"review environment.*Verjson/alpha:ai-review-app:deployment_branch_policy: expected an object",
        )

    def test_a_branch_policy_naming_someone_elses_default_branch_is_reported(self):
        # `custom_branch_policies: true` says only that the policy is custom, not
        # what it admits. An adopter whose default branch is `develop/next` and
        # whose policy names `main` confines the review App key to a branch the
        # review lane never runs on, and the mirrored-from-`.github` shape makes
        # that look conformant field by field.
        path = "repos/Verjson/beta/environments/ai-review-app/deployment-branch-policies"
        self.fixture[path][0]["branch_policies"] = [{"name": "main", "type": "branch"}]
        self.assert_audit_error(
            r"review environment.*Verjson/beta:ai-review-app:branch_policies.*'main'.*develop/next",
        )

    def test_the_admission_set_is_the_caller_the_rearm_gate_hard_requires(self):
        # `retired_path` describes the ruleset split, not the gate, and today it
        # only coincidentally equals the file `gate-rearm.yml` cannot proceed
        # without. When the split completes and `retired_path` is repointed,
        # admission would silently follow it and the severity split would invert
        # with no test failing. So this asserts against the gate itself.
        #
        # The gate reads more than one adopter caller, but only one read is
        # fatal on every arm; the others sit inside the label-receipt path. The
        # fatal one is identified by the diagnostic it emits, not by position,
        # so reordering the file does not quietly repoint this assertion.
        gate = (ROOT / ".github/workflows/gate-rearm.yml").read_text(encoding="utf-8")
        fatal = gate.index("could not read the protected default-branch review caller")
        reads = re.findall(
            r"repos/\$TARGET_REPO/contents/(\.github/workflows/[A-Za-z0-9._-]+\.ya?ml)\?",
            gate[:fatal],
        )
        self.assertTrue(reads, "the gate's fatal caller read was not found")
        contract = json.loads(
            (ROOT / "config/ai-review-required-workflow-rollout.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            contract["adopter_conformance"]["caller_workflows"]["admission"],
            [reads[-1]],
            "the admission set must be exactly the caller gate-rearm.yml cannot proceed without",
        )

    def test_a_branch_policy_admitting_more_than_the_default_branch_is_reported(self):
        # Naming the right branch is not enough: the point of the policy is to
        # confine the review App key to where the review lane runs, so a policy
        # that also admits a wildcard hands the key to every branch a
        # contributor can create. Exact equality is the assertion, not
        # membership.
        path = "repos/Verjson/alpha/environments/ai-review-app/deployment-branch-policies"
        self.fixture[path][0]["branch_policies"] = [
            {"name": "main", "type": "branch"},
            {"name": "*", "type": "branch"},
        ]
        self.assert_audit_error(
            r"review environment.*Verjson/alpha:ai-review-app:branch_policies",
        )

    def run_gh_api(self, returncode, stdout, stderr):
        completed = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)
        original = AUDIT.subprocess.run
        AUDIT.subprocess.run = lambda *args, **kwargs: completed
        self.addCleanup(setattr, AUDIT.subprocess, "run", original)

    def test_a_tolerated_absence_is_a_404_and_nothing_else(self):
        # Reporting "absent" for an expired credential would convert a token
        # problem into a fleet-wide non-adoption report, and reporting it for a
        # 500 would let a transient API failure read as remediated drift. Only
        # the status that actually means "this does not exist" is tolerated.
        for returncode, stderr in (
            (1, "gh: Bad credentials (HTTP 401)"),
            (1, "gh: Forbidden (HTTP 403)"),
            (1, "gh: Internal Server Error (HTTP 500)"),
            (1, ""),
        ):
            with self.subTest(stderr=stderr):
                self.run_gh_api(returncode, "", stderr)
                with self.assertRaisesRegex(AUDIT.AuditError, r"GitHub API read failed for repos/x"):
                    AUDIT.gh_pages("repos/x", allow_missing=True)

    def test_a_caller_finding_does_not_mask_an_environment_finding(self):
        # The audit runs daily. Reporting one class of adopter finding at a time
        # makes the fleet take as many scheduled days to become visible as there
        # are classes, which is the same "the control could not tell you" shape
        # #1404 set out to remove.
        self.remove_adopter_caller("Verjson/alpha", ".github/workflows/ai-review-merge.yml")
        del self.fixture["repos/Verjson/beta/environments/ai-review-app"]
        del self.fixture["repos/Verjson/beta/environments/ai-review-app/deployment-branch-policies"]
        message = self.audit_error_message()
        self.assertIn("Verjson/alpha:.github/workflows/ai-review-merge.yml", message)
        self.assertIn("Verjson/beta:ai-review-app:absent", message)

    def test_an_unarmed_repository_owes_no_caller_set_and_no_environment(self):
        # A repository the arm does not cover cannot be blocked by it, so
        # demanding the review lane there would report 70 of 99 organization
        # repositories as findings and bury the 4 that are actually broken.
        rows = self.fixture["orgs/Verjson/properties/values?per_page=100"][0]
        beta = next(row for row in rows if row["repository_full_name"] == "Verjson/beta")
        beta["properties"] = [{"property_name": "verjson-stack", "value": "node"}]
        self.contract["arm_property_migration"]["repositories"] = ["Verjson/alpha"]
        for path in list(self.fixture):
            if path.startswith("repos/Verjson/beta/contents") or "/beta/environments/" in path:
                del self.fixture[path]
        report = AUDIT.audit(self.contract, self.read)
        self.assertEqual(report["armed_repositories"], 1)

    def test_only_a_file_satisfies_a_declared_caller(self):
        # The listing is adopter-controlled. A directory at the caller's path is
        # listed under that exact path and is not a workflow: `gate-rearm.yml`
        # reading the caller still fails, so a name-only membership test would
        # certify an adopter the arm cannot admit.
        listing = self.adopter_workflow_listing("Verjson/alpha")
        for entry in listing:
            if entry["path"] == ".github/workflows/ai-review-merge.yml":
                entry["type"] = "dir"
        self.assert_audit_error(
            r"cannot satisfy the arm.*Verjson/alpha:\.github/workflows/ai-review-merge\.yml",
        )

    def test_an_adopter_the_arm_blocks_is_reported_apart_from_an_incomplete_one(self):
        # Measured against the live fleet on 2026-09-17: 4 of 29 armed adopters
        # lack the caller `gate-rearm.yml` hard-requires and every pull request
        # in them fails, while 19 merely lack the lifecycle re-arm caller and
        # merge normally. Reported in one bucket, the 4 arrive as 8 of 31 sorted
        # strings — the signal #1401 needs, buried under the backlog ADR 0187
        # predicted from the pin skew.
        self.remove_adopter_caller("Verjson/alpha", ".github/workflows/ai-review-merge.yml")
        self.remove_adopter_caller("Verjson/beta", ".github/workflows/ai-review-label-rearm.yml")
        message = self.audit_error_message()
        blocked, incomplete = (part.strip() for part in message.split(" | ")[:2])
        self.assertIn("cannot satisfy the arm", blocked)
        self.assertIn("Verjson/alpha:.github/workflows/ai-review-merge.yml", blocked)
        self.assertNotIn("Verjson/beta", blocked)
        self.assertIn("incomplete generated adopter caller set", incomplete)
        self.assertIn("Verjson/beta:.github/workflows/ai-review-label-rearm.yml", incomplete)

    def test_the_declared_sets_partition_the_generated_caller_family(self):
        # The two sets differ in what their absence costs, not in what is
        # expected: together they are still the whole generated family, so
        # splitting the report can never quietly drop a member from the audit.
        callers = self.contract["adopter_conformance"]["caller_workflows"]
        self.assertEqual(
            sorted(callers["admission"] + callers["completeness"]),
            sorted(ADOPTER_CALLERS),
        )

    def test_a_disabled_admission_caller_is_reported_as_undispatchable(self):
        # `gate-rearm.yml` dispatches the review lane into the adopter. A
        # workflow disabled manually or for inactivity is present on the default
        # branch, satisfies the file-presence assertion, and GitHub refuses to
        # start it — so the arm fails for the same reason as an absent caller
        # while the audit would call the repository conformant. Read from the
        # Actions API, never from the adopter's workflow text.
        for state in ("disabled_manually", "disabled_inactivity"):
            with self.subTest(state=state):
                self.fixture = self.make_fixture()
                path = "repos/Verjson/alpha/actions/workflows/ai-review-merge.yml"
                self.fixture[path][0]["state"] = state
                self.assert_audit_error(
                    rf"cannot satisfy the arm.*Verjson/alpha:\.github/workflows/"
                    rf"ai-review-merge\.yml:{state}",
                )

    def test_a_default_branch_needing_encoding_still_probes_that_branch(self):
        # Git permits `#` and `&` in a ref name. Interpolated raw into a query
        # string, `#` truncates it and `&` starts another parameter, so the
        # audit would silently probe a branch nobody asked about and report
        # whatever it found there. The probe must name the branch it means.
        repositories = self.fixture["orgs/Verjson/repos?per_page=100&type=all"][0]
        beta = next(item for item in repositories if item["full_name"] == "Verjson/beta")
        listing = self.fixture.pop("repos/Verjson/beta/contents/.github/workflows?ref=develop/next")
        beta["default_branch"] = "release#1&2"
        self.fixture["repos/Verjson/beta/contents/.github/workflows?ref=release%231%262"] = listing
        self.fixture["repos/Verjson/beta/environments/ai-review-app/deployment-branch-policies"] = [
            {"branch_policies": [{"name": "release#1&2", "type": "branch"}]}
        ]
        self.assertEqual(AUDIT.audit(self.contract, self.read)["armed_repositories"], 2)

    def test_malformed_contract_fails_with_controlled_diagnostics(self):
        for section, key, value, diagnostic in (
            (None, "ruleset_id", True, "ruleset ID"),
            ("authorization", "variables", [{}], "authorization variables"),
            ("authorization", "app_permissions", {"checks": "write"}, "permission contract"),
            ("authorization", "app_events", ["pull_request"], "event contract"),
            (
                "adopter_conformance",
                "caller_workflows",
                {"admission": [".github/workflows/ai-review-merge.yml"]},
                "must declare an admission and a completeness set",
            ),
            (
                "adopter_conformance",
                "caller_workflows",
                {"admission": [], "completeness": [".github/workflows/a.yml"]},
                "adopter admission caller workflows are invalid",
            ),
            (
                "adopter_conformance",
                "caller_workflows",
                {
                    "admission": [".github/workflows/ai-review-merge.yml"],
                    "completeness": ["ai-review-label-rearm.yml"],
                },
                "adopter completeness caller workflows are invalid",
            ),
            (
                "adopter_conformance",
                "caller_workflows",
                {
                    "admission": [".github/workflows/ai-review-merge.yml"],
                    "completeness": [".github/workflows/ai-review-merge.yml"],
                },
                "declared in both sets",
            ),
            # An admission set that is not the file `gate-rearm.yml` reads would
            # let the audit certify the exact state #1401 reported.
            (
                "adopter_conformance",
                "caller_workflows",
                {
                    "admission": [".github/workflows/ai-privileged-merge.yml"],
                    "completeness": [".github/workflows/ai-review-merge.yml"],
                },
                "admission set must be exactly the caller",
            ),
            # A set the audit does not know about is a set it does not check,
            # so the shape is compared exactly rather than as a superset.
            (
                "adopter_conformance",
                "caller_workflows",
                {
                    "admission": [".github/workflows/ai-review-merge.yml"],
                    "completeness": [".github/workflows/ai-review-label-rearm.yml"],
                    "advisory": [".github/workflows/ai-review-advisory.yml"],
                },
                "must declare an admission and a completeness set",
            ),
            ("adopter_conformance", "environment", "ai-review", "review environment contract"),
            (
                "adopter_conformance",
                "environment_deployment_branch_policy",
                {"protected_branches": True, "custom_branch_policies": True},
                "branch-policy contract",
            ),
        ):
            with self.subTest(diagnostic=diagnostic), tempfile.TemporaryDirectory() as directory:
                contract = copy.deepcopy(self.contract)
                target = contract if section is None else contract[section]
                target[key] = value
                path = Path(directory) / "contract.json"
                path.write_text(json.dumps(contract), encoding="utf-8")
                with self.assertRaisesRegex(AUDIT.AuditError, diagnostic):
                    AUDIT.read_contract(path)


if __name__ == "__main__":
    unittest.main()
