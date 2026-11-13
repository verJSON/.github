import importlib.util
import json
import re
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "scripts/actions-paused-drift.py"
POLICY = ROOT / "config/actions-paused-drift-policy.json"
BASELINE = 26466984
SPEC = importlib.util.spec_from_file_location("actions_paused_drift", AUDIT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
GH_PREFIX = ["api", "--hostname", "github.com", "--method", "GET", "--paginate", "--slurp"]


def repository(repository_id, state, *, actions_enabled=None, baseline=None, branch="main"):
    return {
        "id": repository_id,
        "state": state,
        "actions_enabled": state == "enabled" if actions_enabled is None else actions_enabled,
        "baseline": state == "paused" if baseline is None else baseline,
        "branch": branch,
    }


def default_world():
    return {
        "repositories": {
            "verjson-editor": repository(11, "paused"),
            "verjson-authn": repository(12, "paused"),
            "verjson-ai": repository(13, "enabled"),
            "verjson-agents": repository(14, "enabled"),
        },
        "relaxations": [
            {"ruleset_id": 21750617, "scope": "organization", "repository": "verjson-authn"},
            {"ruleset_id": 21551883, "scope": "repository", "repository": "verjson-ai"},
        ],
        "enforcement": {},
    }


def build_policy(world):
    return {
        "source": "docs/decisions/0252-exclude-actions-paused-repos-from-required-checks/README.md",
        "organization": "Verjson",
        "property": "verjson-actions-state",
        "baseline_ruleset_id": BASELINE,
        "baseline_rule_types": ["deletion", "non_fast_forward", "pull_request"],
        "baseline_bypass_contract_ruleset_id": 20515817,
        "paused_only_relaxations": world["relaxations"],
    }


def expected_enforcement(state):
    return "evaluate" if state == "paused" else "active"


def build_responses(world):
    repositories = world["repositories"]
    responses = {
        "orgs/Verjson/properties/values?per_page=100": [
            [
                {
                    "repository_id": detail["id"],
                    "repository_name": name,
                    "repository_full_name": f"verJSON/{name}",
                    "properties": [
                        {"property_name": "verjson-core-checks", "value": "enforced"},
                        {"property_name": "verjson-actions-state", "value": detail["state"]},
                    ],
                }
                for name, detail in repositories.items()
            ]
        ],
        "orgs/Verjson/repos?type=all&per_page=100": [
            [
                {"id": detail["id"], "name": name, "default_branch": detail["branch"]}
                for name, detail in repositories.items()
            ]
        ],
    }
    for name, detail in repositories.items():
        if detail["state"] == "paused":
            responses[f"repos/Verjson/{name}/actions/permissions"] = [
                {"enabled": detail["actions_enabled"], "sha_pinning_required": False}
            ]
        rules = [{"type": "deletion", "ruleset_id": 900, "ruleset_source_type": "Organization"}]
        if detail["baseline"]:
            rules.extend(
                {"type": rule_type, "ruleset_id": BASELINE, "ruleset_source_type": "Organization"}
                for rule_type in ("pull_request", "non_fast_forward", "deletion")
            )
        responses[f"repos/Verjson/{name}/rules/branches/{quote(detail['branch'], safe='')}?per_page=100"] = [rules]
    for relaxation in world["relaxations"]:
        name = relaxation["repository"]
        ruleset_id = relaxation["ruleset_id"]
        enforcement = world["enforcement"].get(
            ruleset_id, expected_enforcement(repositories[name]["state"])
        )
        if relaxation["scope"] == "organization":
            responses[f"orgs/Verjson/rulesets/{ruleset_id}"] = [
                {
                    "id": ruleset_id,
                    "name": f"ruleset-{ruleset_id}",
                    "source_type": "Organization",
                    "source": "verJSON",
                    "enforcement": enforcement,
                    "conditions": {
                        "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
                        "repository_id": {"repository_ids": [repositories[name]["id"]]},
                    },
                }
            ]
        else:
            responses[f"repos/Verjson/{name}/rulesets/{ruleset_id}"] = [
                {
                    "id": ruleset_id,
                    "name": f"ruleset-{ruleset_id}",
                    "source_type": "Repository",
                    "source": f"verJSON/{name}",
                    "enforcement": enforcement,
                    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
                }
            ]
    return responses


FAKE_GH = (
    "#!/usr/bin/env python3\n"
    "import json, os, sys\n"
    "args = sys.argv[1:]\n"
    "with open(os.environ['CALLS'], 'a', encoding='utf-8') as stream:\n"
    "    stream.write(json.dumps(args) + '\\n')\n"
    f"if args[:-1] != {GH_PREFIX!r}:\n"
    "    print('unexpected gh arguments', file=sys.stderr); sys.exit(90)\n"
    "path = args[-1]\n"
    "if path in json.loads(os.environ['FAILING_PATHS']):\n"
    "    print('simulated HTTP failure', file=sys.stderr); sys.exit(1)\n"
    "responses = json.loads(os.environ['RESPONSES'])\n"
    "if path not in responses:\n"
    "    print('unexpected API path ' + path, file=sys.stderr); sys.exit(91)\n"
    "print(json.dumps(responses[path]))\n"
)


class ActionsPausedDriftTest(unittest.TestCase):
    def run_audit(self, world=None, *, responses=None, failing_paths=None, policy=None):
        world = world or default_world()
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            policy_path = temp / "policy.json"
            policy_path.write_text(json.dumps(policy or build_policy(world)), encoding="utf-8")
            calls = temp / "calls.jsonl"
            gh = temp / "gh"
            gh.write_text(FAKE_GH, encoding="utf-8")
            gh.chmod(0o755)
            env = os.environ | {
                "PATH": f"{temp}:{os.environ['PATH']}",
                "RESPONSES": json.dumps(responses if responses is not None else build_responses(world)),
                "FAILING_PATHS": json.dumps(failing_paths or []),
                "CALLS": str(calls),
            }
            result = subprocess.run(
                ["python3", str(AUDIT), "--test-policy", str(policy_path)],
                env=env,
                capture_output=True,
                text=True,
            )
            recorded = []
            if calls.exists():
                recorded = [
                    json.loads(line)[-1] for line in calls.read_text(encoding="utf-8").splitlines()
                ]
            return result, recorded

    def test_consistent_state_passes_after_reading_every_declared_surface(self):
        world = default_world()

        result, calls = self.run_audit(world)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("actions-paused-drift=conformant paused=2 enabled=2 relaxations=2", result.stdout)
        self.assertEqual(sorted(calls), sorted(build_responses(world)))

    def test_paused_repository_with_actions_enabled_is_drift(self):
        world = default_world()
        world["repositories"]["verjson-editor"]["actions_enabled"] = True

        result, _ = self.run_audit(world)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "ERROR: verjson-editor is verjson-actions-state=paused but its Actions are enabled",
            result.stderr,
        )

    def test_enabled_repository_still_matched_by_the_baseline_ruleset_is_drift(self):
        world = default_world()
        world["repositories"]["verjson-agents"]["baseline"] = True

        result, _ = self.run_audit(world)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "ERROR: verjson-agents is verjson-actions-state=enabled but its default branch "
            "main is still governed by the paused baseline ruleset 26466984",
            result.stderr,
        )

    def test_paused_repository_outside_the_baseline_ruleset_is_drift(self):
        world = default_world()
        world["repositories"]["verjson-editor"]["baseline"] = False

        result, _ = self.run_audit(world)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "ERROR: verjson-editor is verjson-actions-state=paused but its default branch "
            "main is not governed by the paused baseline ruleset 26466984",
            result.stderr,
        )

    def test_enabled_repository_whose_relaxation_is_still_evaluate_is_drift(self):
        world = default_world()
        world["enforcement"][21551883] = "evaluate"

        result, _ = self.run_audit(world)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "ERROR: ruleset 21551883 for verjson-ai is evaluate, expected active "
            "while verjson-actions-state=enabled",
            result.stderr,
        )

    def test_paused_repository_whose_relaxation_is_active_is_drift(self):
        world = default_world()
        world["enforcement"][21750617] = "active"

        result, _ = self.run_audit(world)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "ERROR: ruleset 21750617 for verjson-authn is active, expected evaluate "
            "while verjson-actions-state=paused",
            result.stderr,
        )

    def test_committed_policy_declares_the_adr_0252_table(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))

        self.assertTrue((ROOT / policy["source"]).is_file())
        self.assertEqual(
            policy,
            {
                "source": "docs/decisions/0252-exclude-actions-paused-repos-from-required-checks/README.md",
                "organization": "Verjson",
                "property": "verjson-actions-state",
                "baseline_ruleset_id": 26466984,
                "baseline_rule_types": ["deletion", "non_fast_forward", "pull_request"],
                "baseline_bypass_contract_ruleset_id": 20515817,
                "paused_only_relaxations": [
                    {"ruleset_id": 21750617, "scope": "organization", "repository": "verjson-authn"},
                    {"ruleset_id": 21830367, "scope": "organization", "repository": "verjson-cli-projects"},
                    {"ruleset_id": 21551883, "scope": "repository", "repository": "verjson-ai"},
                    {"ruleset_id": 21590428, "scope": "repository", "repository": "verjson-ai-gguf"},
                    {"ruleset_id": 21558202, "scope": "repository", "repository": "verjson-customer-lifecycle"},
                    {"ruleset_id": 21539441, "scope": "repository", "repository": "verjson-eslint-config"},
                    {"ruleset_id": 21559535, "scope": "repository", "repository": "verjson-graphql-conventions"},
                    {"ruleset_id": 21569694, "scope": "repository", "repository": "verjson-identity-lifecycle"},
                    {"ruleset_id": 21555695, "scope": "repository", "repository": "verjson-infra"},
                    {"ruleset_id": 21584088, "scope": "repository", "repository": "verjson-leads"},
                    {"ruleset_id": 19750811, "scope": "repository", "repository": "verjson-observability"},
                    {"ruleset_id": 21969667, "scope": "repository", "repository": "verjson-temporal-kit"},
                ],
            },
        )

    def test_repository_without_an_actions_state_is_outside_the_audit(self):
        world = default_world()
        responses = build_responses(world)
        responses["orgs/Verjson/properties/values?per_page=100"][0].append(
            {
                "repository_id": 99,
                "repository_name": "ungoverned",
                "repository_full_name": "verJSON/ungoverned",
                "properties": [{"property_name": "verjson-actions-state", "value": None}],
            }
        )
        responses["orgs/Verjson/repos?type=all&per_page=100"][0].append(
            {"id": 99, "name": "ungoverned", "default_branch": "main"}
        )

        result, calls = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("paused=2 enabled=2 relaxations=2", result.stdout)
        self.assertFalse([path for path in calls if "/ungoverned/" in path])

    def test_actions_permissions_without_a_boolean_enabled_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["repos/Verjson/verjson-editor/actions/permissions"] = [{"sha_pinning_required": False}]

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("repos/Verjson/verjson-editor/actions/permissions.enabled must be a boolean", result.stderr)

    def test_unknown_actions_state_value_fails_closed(self):
        world = default_world()
        world["repositories"]["verjson-agents"]["state"] = "disabled"

        result, _ = self.run_audit(world)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "verjson-agents has verjson-actions-state='disabled', expected one of ['enabled', 'paused']",
            result.stderr,
        )

    def test_declared_relaxation_repository_without_an_actions_state_fails_closed(self):
        world = default_world()
        world["relaxations"].append(
            {"ruleset_id": 21830367, "scope": "organization", "repository": "verjson-cli-projects"}
        )

        result, _ = self.run_audit(world, responses=build_responses(default_world()))

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "declared relaxation 21830367 names verjson-cli-projects, which has no verjson-actions-state",
            result.stderr,
        )

    def test_organization_relaxation_no_longer_targeting_its_repository_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["orgs/Verjson/rulesets/21750617"][0]["conditions"]["repository_id"] = {
            "repository_ids": [999]
        }

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "organization ruleset 21750617 must target exactly verjson-authn (12)", result.stderr
        )

    def test_repository_relaxation_inherited_from_the_organization_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["repos/Verjson/verjson-ai/rulesets/21551883"][0]["source_type"] = "Organization"

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("ruleset 21551883 must be a Repository ruleset of verjson-ai", result.stderr)

    def test_relaxation_without_a_known_enforcement_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        del responses["repos/Verjson/verjson-ai/rulesets/21551883"][0]["enforcement"]

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "ruleset 21551883 enforcement None is not one of ['active', 'disabled', 'evaluate']",
            result.stderr,
        )

    def test_relaxation_response_for_another_ruleset_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["repos/Verjson/verjson-ai/rulesets/21551883"][0]["id"] = 21564092

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("ruleset 21551883 response carries id 21564092", result.stderr)

    def test_branch_rule_without_a_ruleset_id_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["repos/Verjson/verjson-editor/rules/branches/main?per_page=100"][0].append(
            {"type": "pull_request", "ruleset_id": "26466984"}
        )

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "repos/Verjson/verjson-editor/rules/branches/main?per_page=100 rule.ruleset_id "
            "must be a positive integer",
            result.stderr,
        )

    def test_governed_repository_missing_from_the_repository_listing_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["orgs/Verjson/repos?type=all&per_page=100"][0] = [
            entry
            for entry in responses["orgs/Verjson/repos?type=all&per_page=100"][0]
            if entry["name"] != "verjson-agents"
        ]

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "verjson-agents has verjson-actions-state but is not in the organization repository listing",
            result.stderr,
        )

    def test_policy_with_an_unknown_relaxation_scope_fails_closed(self):
        world = default_world()
        policy = build_policy(world)
        policy["paused_only_relaxations"][1] = dict(policy["paused_only_relaxations"][1], scope="enterprise")

        result, calls = self.run_audit(world, policy=policy)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "paused_only_relaxations[1].scope must be one of ['organization', 'repository']",
            result.stderr,
        )
        self.assertEqual(calls, [])

    def test_every_failed_api_read_fails_closed(self):
        world = default_world()
        for path in build_responses(world):
            with self.subTest(path=path):
                result, _ = self.run_audit(world, failing_paths=[path])

                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(f"GitHub API read failed for {path}", result.stderr)
                self.assertNotIn("conformant", result.stdout)

    def test_api_read_with_no_pages_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["repos/Verjson/verjson-authn/actions/permissions"] = []

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "GitHub API returned no pages for repos/Verjson/verjson-authn/actions/permissions",
            result.stderr,
        )

    def test_object_read_that_is_not_an_object_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["repos/Verjson/verjson-editor/actions/permissions"] = [["enabled"]]

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("repos/Verjson/verjson-editor/actions/permissions must be an object", result.stderr)

    def test_list_page_that_is_not_an_array_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["orgs/Verjson/repos?type=all&per_page=100"] = [{"id": 11, "name": "verjson-editor"}]

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("orgs/Verjson/repos?type=all&per_page=100 page 0 must be an array", result.stderr)

    def test_object_read_spanning_several_pages_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["repos/Verjson/verjson-editor/actions/permissions"] = [
            {"enabled": False, "sha_pinning_required": False},
            {"enabled": False, "sha_pinning_required": False},
        ]

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "repos/Verjson/verjson-editor/actions/permissions returned 2 pages, expected one object",
            result.stderr,
        )

    def test_every_finding_is_reported_without_masking_another(self):
        world = default_world()
        world["repositories"]["verjson-editor"]["actions_enabled"] = True
        world["repositories"]["verjson-agents"]["baseline"] = True
        world["enforcement"][21551883] = "evaluate"

        result, _ = self.run_audit(world)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stderr.count("ERROR: "), 3, result.stderr)

    def test_unexpected_argument_is_rejected_before_any_read(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "called"
            gh = Path(directory) / "gh"
            gh.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 97\n", encoding="utf-8")
            gh.chmod(0o755)
            env = os.environ | {"PATH": f"{directory}:{os.environ['PATH']}"}
            result = subprocess.run(
                ["python3", str(AUDIT), "--apply"], env=env, capture_output=True, text=True
            )
            called = marker.exists()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("usage: actions-paused-drift.py [--test-policy PATH]", result.stderr)
        self.assertFalse(called)

    def test_repository_listed_twice_in_property_values_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        values = responses["orgs/Verjson/properties/values?per_page=100"]
        duplicate = json.loads(json.dumps(values[0][0]))
        duplicate["properties"][1]["value"] = "enabled"
        values.append([duplicate])

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("verjson-editor is listed more than once", result.stderr)

    def test_list_valued_actions_state_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["orgs/Verjson/properties/values?per_page=100"][0][0]["properties"][1]["value"] = ["paused"]

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "verjson-editor has verjson-actions-state=['paused'], expected one of ['enabled', 'paused']",
            result.stderr,
        )

    def test_policy_with_a_list_valued_scope_fails_closed(self):
        world = default_world()
        policy = build_policy(world)
        policy["paused_only_relaxations"][0] = dict(policy["paused_only_relaxations"][0], scope=["organization"])

        result, calls = self.run_audit(world, policy=policy)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "paused_only_relaxations[0].scope must be one of ['organization', 'repository']",
            result.stderr,
        )
        self.assertEqual(calls, [])

    def test_relaxation_with_an_object_valued_enforcement_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["repos/Verjson/verjson-ai/rulesets/21551883"][0]["enforcement"] = {"mode": "active"}

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("ruleset 21551883 enforcement {'mode': 'active'} is not one of", result.stderr)

    def test_repository_with_two_actions_state_entries_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        responses["orgs/Verjson/properties/values?per_page=100"][0][0]["properties"].append(
            {"property_name": "verjson-actions-state", "value": "enabled"}
        )

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("verjson-editor carries verjson-actions-state more than once", result.stderr)

    def test_drift_found_before_a_failed_read_is_still_reported(self):
        world = default_world()
        world["repositories"]["verjson-editor"]["actions_enabled"] = True

        result, _ = self.run_audit(world, failing_paths=["repos/Verjson/verjson-ai/rulesets/21551883"])

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        finding = "ERROR: verjson-editor is verjson-actions-state=paused but its Actions are enabled"
        unestablished = "ERROR: cannot establish Actions-paused state"
        self.assertIn(finding, result.stderr)
        self.assertLess(result.stderr.index(finding), result.stderr.index(unestablished))

    def test_default_branch_with_a_slash_is_read_as_one_encoded_path_segment(self):
        world = default_world()
        world["repositories"]["verjson-editor"]["branch"] = "release/2026.09"

        result, calls = self.run_audit(world)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "repos/Verjson/verjson-editor/rules/branches/release%2F2026.09?per_page=100", calls
        )

    def test_paused_repository_missing_a_baseline_rule_type_is_drift(self):
        world = default_world()
        responses = build_responses(world)
        path = "repos/Verjson/verjson-editor/rules/branches/main?per_page=100"
        responses[path][0] = [
            rule
            for rule in responses[path][0]
            if not (rule["ruleset_id"] == BASELINE and rule["type"] == "deletion")
        ]

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "ERROR: verjson-editor is verjson-actions-state=paused but its default branch main "
            "lacks baseline ruleset 26466984 rule types ['deletion']",
            result.stderr,
        )

    def test_branch_rule_without_a_type_fails_closed(self):
        world = default_world()
        responses = build_responses(world)
        del responses["repos/Verjson/verjson-editor/rules/branches/main?per_page=100"][0][-1]["type"]

        result, _ = self.run_audit(world, responses=responses)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "repos/Verjson/verjson-editor/rules/branches/main?per_page=100 rule.type must be a non-empty string",
            result.stderr,
        )

    def test_policy_without_baseline_rule_types_fails_closed(self):
        world = default_world()
        policy = dict(build_policy(world), baseline_rule_types=[])

        result, calls = self.run_audit(world, policy=policy)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("baseline_rule_types must be a non-empty list of distinct rule types", result.stderr)
        self.assertEqual(calls, [])


class AcceptPausedEvaluateTest(unittest.TestCase):
    """The shared ADR 0252 verdict the four ruleset conformance audits rely on."""

    RULESET = 21551883
    RESTORE = "; restore enforcement to active (ADR 0252)"

    def accept(self, *, state="paused", repository_ids=(13,), unreadable=None, ruleset_id=RULESET):
        world = {
            "repositories": {"verjson-ai": repository(13, state)},
            "relaxations": [{"ruleset_id": self.RULESET, "scope": "repository", "repository": "verjson-ai"}],
            "enforcement": {},
        }
        responses = build_responses(world)

        def read_pages(path):
            if path == unreadable:
                raise RuntimeError(f"GitHub API read failed for {path}")
            return responses[path]

        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            policy.write_text(json.dumps(build_policy(world)), encoding="utf-8")
            with mock.patch.object(MODULE, "gh_json_pages", side_effect=read_pages):
                return MODULE.accept_paused_evaluate(ruleset_id, list(repository_ids), policy)

    def test_accepts_evaluate_while_the_targeted_repository_is_paused(self):
        self.assertEqual(
            "enforcement=evaluate while verjson-ai is verjson-actions-state=paused (ADR 0252)",
            self.accept(),
        )

    def test_refuses_a_ruleset_adr_0252_does_not_record(self):
        with self.assertRaisesRegex(
            MODULE.PausedRelaxationRefused,
            "^enforcement is evaluate but ADR 0252 records no paused-only relaxation for this ruleset"
            + re.escape(self.RESTORE) + "$",
        ):
            self.accept(ruleset_id=99)

    def test_refuses_a_target_that_is_not_exactly_the_recorded_repository(self):
        for repository_ids in ((14,), (13, 14), ()):
            with self.subTest(repository_ids), self.assertRaisesRegex(
                MODULE.PausedRelaxationRefused,
                "^" + re.escape(
                    f"enforcement is evaluate but it targets {list(repository_ids)}, "
                    "not exactly verjson-ai (13)" + self.RESTORE
                ) + "$",
            ):
                self.accept(repository_ids=repository_ids)

    def test_refuses_a_repository_without_an_actions_state(self):
        with self.assertRaisesRegex(
            MODULE.PausedRelaxationRefused,
            "^" + re.escape("enforcement is evaluate but verjson-ai has no verjson-actions-state value"
                            + self.RESTORE) + "$",
        ):
            self.accept(state=None)

    def test_refuses_an_enabled_repository(self):
        with self.assertRaisesRegex(
            MODULE.PausedRelaxationRefused,
            "^" + re.escape("enforcement is evaluate but verjson-ai is verjson-actions-state=enabled"
                            + self.RESTORE) + "$",
        ):
            self.accept(state="enabled")

    def test_an_unreadable_state_is_unestablished_rather_than_drift(self):
        for path in ("orgs/Verjson/repos?type=all&per_page=100", "orgs/Verjson/properties/values?per_page=100"):
            with self.subTest(path), self.assertRaisesRegex(
                MODULE.PausedStateUnreadable,
                "^" + re.escape(
                    "enforcement is evaluate but verjson-actions-state of verjson-ai cannot be read "
                    f"(GitHub API read failed for {path})"
                ) + "$",
            ):
                self.accept(unreadable=path)

    def test_an_unreadable_policy_is_unestablished_rather_than_drift(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            MODULE.PausedStateUnreadable,
            r"^enforcement is evaluate but the ADR 0252 policy cannot be read \(.+\)$",
        ):
            MODULE.accept_paused_evaluate(self.RULESET, [13], Path(directory) / "absent.json")


if __name__ == "__main__":
    unittest.main()
