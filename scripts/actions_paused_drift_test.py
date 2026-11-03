import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "scripts/actions-paused-drift.py"
POLICY = ROOT / "config/actions-paused-drift-policy.json"
BASELINE = 26466984
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
            rules.append(
                {"type": "pull_request", "ruleset_id": BASELINE, "ruleset_source_type": "Organization"}
            )
        responses[f"repos/Verjson/{name}/rules/branches/{detail['branch']}?per_page=100"] = [rules]
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


if __name__ == "__main__":
    unittest.main()
