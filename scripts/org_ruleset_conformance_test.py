import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "scripts/org-ruleset-conformance.py"
POLICY = ROOT / "config/org-ruleset-conformance-policy.json"


def ruleset(
    ruleset_id,
    *,
    name=None,
    target="branch",
    include=None,
    bypass_actors=None,
    rules=None,
):
    return {
        "id": ruleset_id,
        "name": name or f"ruleset-{ruleset_id}",
        "source_type": "Organization",
        "target": target,
        "enforcement": "active",
        "bypass_actors": bypass_actors
        if bypass_actors is not None
        else [
            {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"},
            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
        ],
        "conditions": {
            "ref_name": {"include": include or ["~DEFAULT_BRANCH"], "exclude": []},
            "repository_property": {
                "include": [{"name": "verjson-core-checks", "property_values": ["enforced"]}],
                "exclude": [],
            },
        },
        "rules": rules
        if rules is not None
        else [
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [],
                },
            }
        ],
    }


class OrgRulesetConformanceTest(unittest.TestCase):
    def run_audit(
        self,
        pages,
        details,
        *,
        failing_paths=None,
        raw_policy=None,
        use_test_policy=True,
        inherited_policy=None,
        extra_arguments=None,
    ):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            policy = temp / "policy.json"
            policy.write_text(
                raw_policy
                if raw_policy is not None
                else json.dumps(
                    {
                        "organization": "Verjson",
                        "release_authorization_bypass": {
                            "actor_type": "Integration",
                            "actor_id": 4583107,
                            "bypass_mode": "always",
                        },
                        "required_check_producer_app_id": 15368,
                        "bypassless_required_workflows": [],
                        "bypass_actor_contracts": [
                            {
                                "ruleset_id": ruleset_id,
                                "name": detail["name"],
                                "bypass_actors": detail["bypass_actors"],
                            }
                            for ruleset_id, detail in details.items()
                            if detail.get("bypass_actors")
                        ],
                    }
                ),
                encoding="utf-8",
            )
            calls = temp / "calls.jsonl"
            gh = temp / "gh"
            gh.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['CALLS'], 'a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps(args) + '\\n')\n"
                "expected = ['api', '--hostname', 'github.com', '--method', 'GET', '--paginate', '--slurp']\n"
                "if args[:-1] != expected:\n"
                "    print('unexpected gh arguments', file=sys.stderr); sys.exit(90)\n"
                "path = args[-1]\n"
                "if path in json.loads(os.environ['FAILING_PATHS']):\n"
                "    print('simulated HTTP failure', file=sys.stderr); sys.exit(1)\n"
                "responses = json.loads(os.environ['RESPONSES'])\n"
                "if path not in responses:\n"
                "    print('unexpected API path', file=sys.stderr); sys.exit(91)\n"
                "print(json.dumps(responses[path]))\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            responses = {"orgs/Verjson/rulesets?per_page=100": pages}
            responses.update(
                {f"orgs/Verjson/rulesets/{ruleset_id}": [detail] for ruleset_id, detail in details.items()}
            )
            env = os.environ | {
                "PATH": f"{temp}:{os.environ['PATH']}",
                "RESPONSES": json.dumps(responses),
                "FAILING_PATHS": json.dumps(failing_paths or []),
                "CALLS": str(calls),
            }
            env.pop("ORG_RULESET_POLICY", None)
            if inherited_policy is not None:
                inherited = temp / "inherited-policy.json"
                inherited.write_text(json.dumps(inherited_policy), encoding="utf-8")
                env["ORG_RULESET_POLICY"] = str(inherited)
            command = ["python3", str(AUDIT)]
            if use_test_policy:
                command.extend(["--test-policy", str(policy)])
            command.extend(extra_arguments or [])
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            recorded = []
            if calls.exists():
                recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
            return result, recorded

    def test_preserves_existing_actors_rules_and_conditions_while_accepting_required_actor(self):
        detail = ruleset(
            1,
            bypass_actors=[
                {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"},
                {"actor_type": "Integration", "actor_id": 2740, "bypass_mode": "always"},
                {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
            ],
            rules=[
                {"type": "deletion"},
                {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
                {"type": "workflows", "parameters": {"workflows": [{"path": ".github/workflows/gate-rearm.yml"}]}},
            ],
        )
        original = copy.deepcopy(detail)

        result, calls = self.run_audit([[{"id": 1}]], {1: detail})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rulesets=1 default_branch_token_rulesets=1", result.stdout)
        self.assertEqual(detail, original)
        self.assertEqual(
            calls,
            [
                ["api", "--hostname", "github.com", "--method", "GET", "--paginate", "--slurp", "orgs/Verjson/rulesets?per_page=100"],
                ["api", "--hostname", "github.com", "--method", "GET", "--paginate", "--slurp", "orgs/Verjson/rulesets/1"],
            ],
        )
        self.assertNotIn("required_approving_review_count", result.stdout + result.stderr)

    def test_committed_policy_pins_the_release_authorization_app(self):
        self.assertEqual(
            json.loads(POLICY.read_text(encoding="utf-8")),
            {
                "organization": "Verjson",
                "release_authorization_bypass": {
                    "actor_type": "Integration",
                    "actor_id": 4583107,
                    "bypass_mode": "always",
                },
                "required_check_producer_app_id": 15368,
                "bypassless_required_workflows": [
                {
                    "name": "authn-type-surface-required-workflow",
                    "repository_id": 1302124584,
                    "workflow_repository_id": 1269388380,
                    "workflow_path": ".github/workflows/authn-type-surface-required.yml",
                    "workflow_ref": "refs/heads/main",
                    "required_status_check": None,
                },
                {
                    "name": "cli-projects-package-surface-required-workflow",
                    "repository_id": 1277452690,
                    "workflow_repository_id": 1269388380,
                    "workflow_path": ".github/workflows/cli-projects-package-surface-required.yml",
                    "workflow_ref": "refs/heads/main",
                    "required_status_check": {
                        "context": "admission",
                        "integration_id": 15368,
                        "strict": True,
                    },
                },
                ],
                "bypass_actor_contracts": [
                    {
                        "ruleset_id": 20722935,
                        "name": "ai-authorization-arm-required",
                        "bypass_actors": [
                            {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 2740, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4693283, "bypass_mode": "always"},
                        ],
                    },
                    {
                        "ruleset_id": 21750617,
                        "name": "authn-type-surface-required-workflow",
                        "bypass_actors": [
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                        ],
                    },
                    {
                        "ruleset_id": 20513599,
                        "name": "changelog-contract-required",
                        "bypass_actors": [
                            {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4693283, "bypass_mode": "always"},
                        ],
                    },
                    {
                        "ruleset_id": 20515822,
                        "name": "core-checks-actions",
                        "bypass_actors": [
                            {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4693283, "bypass_mode": "always"},
                        ],
                    },
                    {
                        "ruleset_id": 20515817,
                        "name": "core-checks-node",
                        "bypass_actors": [
                            {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4693283, "bypass_mode": "always"},
                        ],
                    },
                    {
                        "ruleset_id": 24056850,
                        "name": "core-checks-node-floor",
                        "bypass_actors": [
                            {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4693283, "bypass_mode": "always"},
                        ],
                    },
                    {
                        "ruleset_id": 18098028,
                        "name": "main-protection",
                        "bypass_actors": [
                            {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 2740, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4693283, "bypass_mode": "always"},
                        ],
                    },
                ],
            },
        )

    def test_inherited_policy_environment_cannot_redirect_production_policy(self):
        committed = json.loads(POLICY.read_text(encoding="utf-8"))
        details = {
            contract["ruleset_id"]: ruleset(
                contract["ruleset_id"],
                name=contract["name"],
                include=["refs/heads/release/*"],
                bypass_actors=contract["bypass_actors"],
            )
            for contract in committed["bypass_actor_contracts"]
        }
        details[13] = ruleset(
            13,
            include=["refs/heads/release/*"],
            bypass_actors=[],
        )
        result, calls = self.run_audit(
            [[{"id": ruleset_id} for ruleset_id in details]],
            details,
            use_test_policy=False,
            inherited_policy={
                "organization": "Attacker",
                "release_authorization_bypass": {
                    "actor_type": "Integration",
                    "actor_id": 1,
                    "bypass_mode": "always",
                },
                "bypassless_required_workflows": [],
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("organization=Verjson", result.stdout)
        self.assertTrue(
            all(call[-1].startswith("orgs/Verjson/rulesets") for call in calls),
            calls,
        )

    def test_only_the_explicit_test_policy_argument_is_accepted(self):
        result, _ = self.run_audit(
            [[{"id": 14}]],
            {14: ruleset(14)},
            use_test_policy=False,
            extra_arguments=["--policy", "/tmp/untrusted.json"],
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: org-ruleset-conformance.py [--test-policy PATH]", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_current_core_checks_actions_shape_without_release_actor_fails(self):
        detail = ruleset(
            20515822,
            name="core-checks-actions",
            bypass_actors=[
                {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"}
            ],
        )

        result, _ = self.run_audit([[{"id": 20515822}]], {20515822: detail})

        self.assertEqual(result.returncode, 1)
        self.assertIn("core-checks-actions (20515822)", result.stderr)
        self.assertIn("required release authorization bypass is absent", result.stderr)

    def test_exact_authn_required_workflow_is_the_only_bypassless_exception(self):
        detail = ruleset(
            99,
            name="authn-type-surface-required-workflow",
            bypass_actors=[],
            rules=[{
                "type": "workflows",
                "parameters": {
                    "do_not_enforce_on_create": False,
                    "workflows": [{
                        "path": ".github/workflows/authn-type-surface-required.yml",
                        "repository_id": 1269388380,
                        "ref": "refs/heads/main",
                        "sha": "a" * 40,
                    }],
                },
            }],
        )
        detail["conditions"] = {
            "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
            "repository_id": {"repository_ids": [1302124584]},
        }
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        policy["bypass_actor_contracts"] = []
        result, _ = self.run_audit(
            [[{"id": 99}]], {99: detail}, raw_policy=json.dumps(policy)
        )
        self.assertEqual(0, result.returncode, result.stderr)

        for mutation in ("repository", "path", "sha", "bypass"):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(detail)
                if mutation == "repository":
                    changed["conditions"]["repository_id"]["repository_ids"] = [1]
                elif mutation == "path":
                    changed["rules"][0]["parameters"]["workflows"][0]["path"] = ".github/workflows/ci.yml"
                elif mutation == "sha":
                    changed["rules"][0]["parameters"]["workflows"][0]["sha"] = "main"
                else:
                    changed["bypass_actors"] = [{
                        "actor_type": "OrganizationAdmin",
                        "actor_id": None,
                        "bypass_mode": "always",
                    }]
                result, _ = self.run_audit(
                    [[{"id": 99}]], {99: changed}, raw_policy=json.dumps(policy)
                )
                self.assertEqual(1, result.returncode)
                self.assertIn("required release authorization bypass is absent", result.stderr)

    def cli_projects_required_workflow(self):
        """The live image of ruleset 21830367 since the 2026-10-15 rotation (ADR 0212)."""
        detail = ruleset(
            21830367,
            name="cli-projects-package-surface-required-workflow",
            bypass_actors=[],
            rules=[
                {
                    "type": "workflows",
                    "parameters": {
                        "do_not_enforce_on_create": False,
                        "workflows": [{
                            "repository_id": 1269388380,
                            "path": ".github/workflows/cli-projects-package-surface-required.yml",
                            "sha": "c78fcd4ef9ab83b6775ae2ede94ebce475b3943c",
                            "ref": "refs/heads/main",
                        }],
                    },
                },
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "do_not_enforce_on_create": False,
                        "required_status_checks": [
                            {"context": "admission", "integration_id": 15368},
                        ],
                    },
                },
            ],
        )
        detail["conditions"] = {
            "ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]},
            "repository_id": {"repository_ids": [1277452690]},
        }
        return detail

    def committed_policy_without_bypass_contracts(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        policy["bypass_actor_contracts"] = []
        return json.dumps(policy)

    def test_bypassless_cli_projects_workflow_with_its_strict_admission_check_passes(self):
        result, _ = self.run_audit(
            [[{"id": 21830367}]],
            {21830367: self.cli_projects_required_workflow()},
            raw_policy=self.committed_policy_without_bypass_contracts(),
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def cli_projects_exception(self, policy):
        [entry] = [
            item
            for item in policy["bypassless_required_workflows"]
            if item["name"] == "cli-projects-package-surface-required-workflow"
        ]
        return entry

    def declared_check_policy(self, **declared):
        policy = json.loads(self.committed_policy_without_bypass_contracts())
        self.cli_projects_exception(policy)["required_status_check"].update(declared)
        return json.dumps(policy)

    def test_any_departure_from_the_declared_admission_rule_still_fails(self):
        def status_parameters(detail):
            return detail["rules"][1]["parameters"]

        mutations = {
            "not strict": lambda d: status_parameters(d).update(
                strict_required_status_checks_policy=False
            ),
            "enforced on create": lambda d: status_parameters(d).update(
                do_not_enforce_on_create=True
            ),
            "falsy create flag": lambda d: status_parameters(d).update(
                do_not_enforce_on_create=0
            ),
            "other context": lambda d: status_parameters(d)["required_status_checks"][0].update(
                context="ci"
            ),
            "other App": lambda d: status_parameters(d)["required_status_checks"][0].update(
                integration_id=2740
            ),
            "unbound check": lambda d: status_parameters(d)["required_status_checks"][0].pop(
                "integration_id"
            ),
            "second check": lambda d: status_parameters(d)["required_status_checks"].append(
                {"context": "ci", "integration_id": 15368}
            ),
            "no checks": lambda d: status_parameters(d).update(required_status_checks=[]),
            "extra check key": lambda d: status_parameters(d)["required_status_checks"][0].update(
                extra=True
            ),
            "extra parameter": lambda d: status_parameters(d).update(extra=True),
            "extra rule key": lambda d: d["rules"][1].update(extra=True),
            "status rule removed": lambda d: d["rules"].pop(1),
            "status rule duplicated": lambda d: d["rules"].append(copy.deepcopy(d["rules"][1])),
            "workflows rule removed": lambda d: d["rules"].pop(0),
            "workflows rule duplicated": lambda d: d["rules"].append(copy.deepcopy(d["rules"][0])),
            "unrelated rule added": lambda d: d["rules"].append(
                {"type": "deletion"}
            ),
            "bypass actor added": lambda d: d.update(bypass_actors=[
                {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "pull_request"},
            ]),
        }
        policy = self.committed_policy_without_bypass_contracts()
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                detail = self.cli_projects_required_workflow()
                mutate(detail)

                result, _ = self.run_audit(
                    [[{"id": 21830367}]], {21830367: detail}, raw_policy=policy
                )

                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn("required release authorization bypass is absent", result.stderr)

        with self.subTest(mutation="rules reordered"):
            detail = self.cli_projects_required_workflow()
            detail["rules"].reverse()

            result, _ = self.run_audit(
                [[{"id": 21830367}]], {21830367: detail}, raw_policy=policy
            )

            self.assertEqual(0, result.returncode, result.stderr)

    def test_an_exception_declaring_no_status_check_rejects_one(self):
        detail = self.cli_projects_required_workflow()
        policy = json.loads(self.committed_policy_without_bypass_contracts())
        self.cli_projects_exception(policy)["required_status_check"] = None

        result, _ = self.run_audit(
            [[{"id": 21830367}]], {21830367: detail}, raw_policy=json.dumps(policy)
        )

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("required release authorization bypass is absent", result.stderr)

    def test_declared_admission_check_matches_the_cli_projects_rollout_contract(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        rollout = json.loads(
            (ROOT / "config/cli-projects-package-surface-ruleset.json").read_text(encoding="utf-8")
        )["rollout"]
        entry = self.cli_projects_exception(policy)

        self.assertEqual(rollout["freshness_status_check"], entry["required_status_check"])

    def test_live_strictness_that_only_compares_equal_to_true_is_rejected(self):
        detail = self.cli_projects_required_workflow()
        detail["rules"][1]["parameters"]["strict_required_status_checks_policy"] = 1

        result, _ = self.run_audit(
            [[{"id": 21830367}]],
            {21830367: detail},
            raw_policy=self.committed_policy_without_bypass_contracts(),
        )

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("required release authorization bypass is absent", result.stderr)

    def test_declared_status_check_strictness_must_be_a_boolean(self):
        result, _ = self.run_audit(
            [[{"id": 21830367}]],
            {21830367: self.cli_projects_required_workflow()},
            raw_policy=self.declared_check_policy(strict=1),
        )

        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("required_status_check.strict must be a boolean", result.stderr)

    def test_declared_status_check_must_bind_the_declared_producer_app(self):
        detail = self.cli_projects_required_workflow()
        detail["rules"][1]["parameters"]["required_status_checks"][0]["integration_id"] = 2740

        result, _ = self.run_audit(
            [[{"id": 21830367}]],
            {21830367: detail},
            raw_policy=self.declared_check_policy(integration_id=2740),
        )

        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn(
            "required_status_check.integration_id must be the declared producer App 15368",
            result.stderr,
        )

    def test_declared_status_check_without_a_binding_fails_closed(self):
        result, _ = self.run_audit(
            [[{"id": 21830367}]],
            {21830367: self.cli_projects_required_workflow()},
            raw_policy=self.declared_check_policy(integration_id=None),
        )

        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn(
            "required_status_check.integration_id must be a positive integer", result.stderr
        )

    def test_declared_status_check_keys_must_be_exact(self):
        cases = {
            "unknown key": lambda check: check.update(do_not_enforce_on_create=False),
            "missing key": lambda check: check.pop("strict"),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                policy = json.loads(self.committed_policy_without_bypass_contracts())
                mutate(self.cli_projects_exception(policy)["required_status_check"])

                result, _ = self.run_audit(
                    [[{"id": 21830367}]],
                    {21830367: self.cli_projects_required_workflow()},
                    raw_policy=json.dumps(policy),
                )

                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn("required_status_check keys are", result.stderr)

    def test_wrong_actor_type_id_or_mode_does_not_satisfy_policy(self):
        wrong_actors = [
            {"actor_type": "Team", "actor_id": 4583107, "bypass_mode": "always"},
            {"actor_type": "Integration", "actor_id": 4583108, "bypass_mode": "always"},
            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "pull_request"},
        ]
        for actor in wrong_actors:
            with self.subTest(actor=actor):
                detail = ruleset(2, bypass_actors=[actor])
                result, _ = self.run_audit([[{"id": 2}]], {2: detail})
                self.assertEqual(result.returncode, 1)
                self.assertIn("required release authorization bypass is absent", result.stderr)

    def test_only_literal_default_branch_token_on_branch_target_requires_actor(self):
        develop_only = ruleset(3, include=["refs/heads/develop"], bypass_actors=[])
        tag_target = ruleset(4, target="tag", bypass_actors=[])
        all_branches = ruleset(15, include=["~ALL"], bypass_actors=[])
        explicit_main = ruleset(16, include=["refs/heads/main"], bypass_actors=[])

        result, _ = self.run_audit(
            [[{"id": 3}, {"id": 4}, {"id": 15}, {"id": 16}]],
            {3: develop_only, 4: tag_target, 15: all_branches, 16: explicit_main},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rulesets=4 default_branch_token_rulesets=0", result.stdout)

    def test_uncontracted_bypass_is_rejected_outside_default_branch_scope(self):
        detail = ruleset(17, include=["refs/heads/release/*"])
        policy = {
            "organization": "Verjson",
            "release_authorization_bypass": {
                "actor_type": "Integration",
                "actor_id": 4583107,
                "bypass_mode": "always",
            },
            "required_check_producer_app_id": 15368,
            "bypassless_required_workflows": [],
            "bypass_actor_contracts": [],
        }

        result, _ = self.run_audit(
            [[{"id": 17}]],
            {17: detail},
            raw_policy=json.dumps(policy),
        )

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(
            "ruleset-17 (17): bypass actors are not covered by a reviewed contract",
            result.stderr,
        )

    def test_reviewed_contract_and_uncontracted_bypassless_ruleset_pass(self):
        contracted = ruleset(17, include=["refs/heads/release/*"])
        bypassless = ruleset(
            18,
            include=["refs/heads/release/*"],
            bypass_actors=[],
        )
        policy = {
            "organization": "Verjson",
            "release_authorization_bypass": {
                "actor_type": "Integration",
                "actor_id": 4583107,
                "bypass_mode": "always",
            },
            "required_check_producer_app_id": 15368,
            "bypassless_required_workflows": [],
            "bypass_actor_contracts": [
                {
                    "ruleset_id": 17,
                    "name": "ruleset-17",
                    "bypass_actors": list(reversed(contracted["bypass_actors"])),
                }
            ],
        }

        result, _ = self.run_audit(
            [[{"id": 17}, {"id": 18}]],
            {17: contracted, 18: bypassless},
            raw_policy=json.dumps(policy),
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def name_keyed_policy(self, name, bypass_actors):
        return {
            "organization": "Verjson",
            "release_authorization_bypass": {
                "actor_type": "Integration",
                "actor_id": 4583107,
                "bypass_mode": "always",
            },
            "required_check_producer_app_id": 15368,
            "bypassless_required_workflows": [],
            "bypass_actor_contracts": [
                {"ruleset_id": None, "name": name, "bypass_actors": bypass_actors}
            ],
        }

    def test_name_keyed_contract_covers_the_live_ruleset_with_that_name(self):
        # A ruleset applied from reviewed config (#1816) has no id until it is
        # created, so its contract names it instead of pinning an id.
        release_only = [
            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"}
        ]
        applied = ruleset(31, name="repo-sensitive-path-admin-review", bypass_actors=release_only)
        policy = self.name_keyed_policy("repo-sensitive-path-admin-review", release_only)

        result, _ = self.run_audit(
            [[{"id": 31}]], {31: applied}, raw_policy=json.dumps(policy)
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def test_name_keyed_contract_rejects_bypass_drift_on_the_named_ruleset(self):
        release_only = [
            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"}
        ]
        drifted = ruleset(31, name="repo-sensitive-path-admin-review")
        policy = self.name_keyed_policy("repo-sensitive-path-admin-review", release_only)

        result, _ = self.run_audit(
            [[{"id": 31}]], {31: drifted}, raw_policy=json.dumps(policy)
        )

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(
            "reviewed bypass contract repo-sensitive-path-admin-review (None): "
            "bypass actors differ from the reviewed contract",
            result.stderr,
        )

    def test_name_keyed_contract_for_a_not_yet_applied_ruleset_is_not_a_finding(self):
        release_only = [
            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"}
        ]
        policy = self.name_keyed_policy("repo-sensitive-path-admin-review", release_only)
        policy["bypass_actor_contracts"].append(
            {"ruleset_id": 17, "name": "ruleset-17", "bypass_actors": ruleset(17)["bypass_actors"]}
        )

        result, _ = self.run_audit(
            [[{"id": 17}]], {17: ruleset(17)}, raw_policy=json.dumps(policy)
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def test_name_keyed_contract_cannot_cover_two_live_rulesets_with_its_name(self):
        release_only = [
            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"}
        ]
        policy = self.name_keyed_policy("repo-sensitive-path-admin-review", release_only)
        details = {
            ruleset_id: ruleset(
                ruleset_id, name="repo-sensitive-path-admin-review", bypass_actors=release_only
            )
            for ruleset_id in (31, 32)
        }

        result, _ = self.run_audit(
            [[{"id": 31}, {"id": 32}]], details, raw_policy=json.dumps(policy)
        )

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(
            "reviewed bypass contract repo-sensitive-path-admin-review (None): "
            "2 live rulesets share the name",
            result.stderr,
        )

    def test_uncontracted_bypassless_push_ruleset_without_ref_conditions_passes(self):
        detail = ruleset(19, target="push", bypass_actors=[])
        detail["conditions"] = {
            "repository_name": {"include": ["~ALL"], "exclude": []}
        }
        policy = {
            "organization": "Verjson",
            "release_authorization_bypass": {
                "actor_type": "Integration",
                "actor_id": 4583107,
                "bypass_mode": "always",
            },
            "required_check_producer_app_id": 15368,
            "bypassless_required_workflows": [],
            "bypass_actor_contracts": [],
        }

        result, _ = self.run_audit(
            [[{"id": 19}]],
            {19: detail},
            raw_policy=json.dumps(policy),
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def test_push_ruleset_bypass_still_requires_a_reviewed_contract(self):
        detail = ruleset(20, target="push")
        detail["conditions"] = {
            "repository_name": {"include": ["~ALL"], "exclude": []}
        }
        policy = {
            "organization": "Verjson",
            "release_authorization_bypass": {
                "actor_type": "Integration",
                "actor_id": 4583107,
                "bypass_mode": "always",
            },
            "required_check_producer_app_id": 15368,
            "bypassless_required_workflows": [],
            "bypass_actor_contracts": [],
        }

        result, _ = self.run_audit(
            [[{"id": 20}]],
            {20: detail},
            raw_policy=json.dumps(policy),
        )

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(
            "ruleset-20 (20): bypass actors are not covered by a reviewed contract",
            result.stderr,
        )

    def test_stale_or_mismatched_bypass_contract_fails(self):
        reviewed_actors = ruleset(17)["bypass_actors"]
        base_policy = {
            "organization": "Verjson",
            "release_authorization_bypass": {
                "actor_type": "Integration",
                "actor_id": 4583107,
                "bypass_mode": "always",
            },
            "required_check_producer_app_id": 15368,
            "bypassless_required_workflows": [],
        }
        cases = [
            (
                {17: ruleset(17, include=["refs/heads/release/*"])},
                {
                    "ruleset_id": 17,
                    "name": "renamed-ruleset",
                    "bypass_actors": reviewed_actors,
                },
                "contract identity drifted",
            ),
            (
                {18: ruleset(18, include=["refs/heads/release/*"], bypass_actors=[])},
                {
                    "ruleset_id": 17,
                    "name": "ruleset-17",
                    "bypass_actors": reviewed_actors,
                },
                "ruleset is absent; remove the stale contract",
            ),
            (
                {
                    17: ruleset(17, include=["refs/heads/release/*"])
                    | {
                        "bypass_actors": reviewed_actors
                        + [
                            {
                                "actor_type": "Integration",
                                "actor_id": 999,
                                "bypass_mode": "always",
                            }
                        ]
                    }
                },
                {
                    "ruleset_id": 17,
                    "name": "ruleset-17",
                    "bypass_actors": reviewed_actors,
                },
                "bypass actors differ from the reviewed contract",
            ),
            (
                {17: ruleset(17, include=["refs/heads/release/*"], bypass_actors=[])},
                {
                    "ruleset_id": 17,
                    "name": "ruleset-17",
                    "bypass_actors": reviewed_actors,
                },
                "bypass actors differ from the reviewed contract",
            ),
        ]
        for details, contract, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                policy = base_policy | {"bypass_actor_contracts": [contract]}
                result, _ = self.run_audit(
                    [[{"id": ruleset_id} for ruleset_id in details]],
                    details,
                    raw_policy=json.dumps(policy),
                )
                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn(diagnostic, result.stderr)

    def test_duplicate_bypass_contract_identity_fails_closed(self):
        reviewed_actors = ruleset(17)["bypass_actors"]
        base_policy = {
            "organization": "Verjson",
            "release_authorization_bypass": {
                "actor_type": "Integration",
                "actor_id": 4583107,
                "bypass_mode": "always",
            },
            "required_check_producer_app_id": 15368,
            "bypassless_required_workflows": [],
        }
        cases = [
            (
                [
                    {
                        "ruleset_id": 17,
                        "name": "ruleset-17",
                        "bypass_actors": reviewed_actors,
                    },
                    {
                        "ruleset_id": 17,
                        "name": "renamed-ruleset",
                        "bypass_actors": reviewed_actors,
                    },
                ],
                "duplicate bypass contract id 17",
            ),
            (
                [
                    {
                        "ruleset_id": 17,
                        "name": "ruleset-17",
                        "bypass_actors": reviewed_actors,
                    },
                    {
                        "ruleset_id": 18,
                        "name": "ruleset-17",
                        "bypass_actors": reviewed_actors,
                    },
                ],
                "duplicate bypass contract name ruleset-17",
            ),
        ]
        for contracts, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                policy = base_policy | {"bypass_actor_contracts": contracts}
                result, _ = self.run_audit(
                    [[{"id": 17}]],
                    {17: ruleset(17)},
                    raw_policy=json.dumps(policy),
                )
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn(diagnostic, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_bypass_contract_actor_image_must_be_nonempty_unique_and_well_formed(self):
        actor = {
            "actor_type": "Integration",
            "actor_id": 4583107,
            "bypass_mode": "always",
        }
        base_policy = {
            "organization": "Verjson",
            "release_authorization_bypass": actor,
            "required_check_producer_app_id": 15368,
            "bypassless_required_workflows": [],
        }
        cases = [
            ([], "bypass_actors must not be empty"),
            ([actor, actor], "contains duplicate bypass actor"),
            ([actor | {"unexpected": True}], "keys are"),
            ([actor | {"actor_id": 0}], "actor_id must be a positive integer"),
        ]
        for actors, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                policy = base_policy | {
                    "bypass_actor_contracts": [
                        {
                            "ruleset_id": 17,
                            "name": "ruleset-17",
                            "bypass_actors": actors,
                        }
                    ]
                }
                result, _ = self.run_audit(
                    [[{"id": 17}]],
                    {17: ruleset(17)},
                    raw_policy=json.dumps(policy),
                )
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn(diagnostic, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_paginated_listing_is_fully_enumerated(self):
        first = ruleset(5)
        second = ruleset(6, name="second-page", bypass_actors=[])

        result, calls = self.run_audit([[{"id": 5}], [{"id": 6}]], {5: first, 6: second})

        self.assertEqual(result.returncode, 1)
        self.assertIn("second-page (6)", result.stderr)
        self.assertEqual(len(calls), 3)

    def test_listing_and_detail_http_failures_fail_closed(self):
        listing_path = "orgs/Verjson/rulesets?per_page=100"
        result, _ = self.run_audit([], {}, failing_paths=[listing_path])
        self.assertEqual(result.returncode, 2)
        self.assertIn("GitHub API read failed", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        detail_path = "orgs/Verjson/rulesets/7"
        result, _ = self.run_audit(
            [[{"id": 7}]], {7: ruleset(7)}, failing_paths=[detail_path]
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("GitHub API read failed", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_malformed_or_incomplete_api_shapes_fail_closed(self):
        cases = [
            ([], {}, "GitHub API returned no pages"),
            ([[]], {}, "ruleset listing contained no rulesets"),
            ([[{"name": "missing-id"}]], {}, "ruleset listing entry 0 on page 0.id"),
            ([[{"id": 8}]], {8: {"id": 8}}, "ruleset 8.name"),
            (
                [[{"id": 9}]],
                {9: ruleset(9) | {"rules": ["not-an-object"]}},
                "ruleset 9 rule 0 must be an object",
            ),
            (
                [[{"id": 10}]],
                {10: ruleset(10) | {"conditions": {"ref_name": {"include": "~DEFAULT_BRANCH", "exclude": []}}}},
                "ruleset 10.conditions.ref_name.include must be an array",
            ),
        ]
        for pages, details, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                result, _ = self.run_audit(pages, details)
                self.assertEqual(result.returncode, 2)
                self.assertIn(diagnostic, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_malformed_live_bypass_actor_shapes_fail_closed(self):
        cases = [
            (
                13,
                {
                    "actor_type": "Integration",
                    "actor_id": 4583107,
                    "bypass_mode": "always",
                    "unexpected": "field",
                },
                "ruleset 13 bypass actor 0 keys are",
            ),
            (
                14,
                {
                    "actor_type": "Integration",
                    "bypass_mode": "always",
                },
                "ruleset 14 bypass actor 0 keys are",
            ),
        ]
        for ruleset_id, actor, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                reviewed = ruleset(ruleset_id)["bypass_actors"]
                policy = {
                    "organization": "Verjson",
                    "release_authorization_bypass": {
                        "actor_type": "Integration",
                        "actor_id": 4583107,
                        "bypass_mode": "always",
                    },
                    "required_check_producer_app_id": 15368,
                    "bypassless_required_workflows": [],
                    "bypass_actor_contracts": [
                        {
                            "ruleset_id": ruleset_id,
                            "name": f"ruleset-{ruleset_id}",
                            "bypass_actors": reviewed,
                        }
                    ],
                }
                detail = ruleset(ruleset_id, bypass_actors=[actor])

                result, _ = self.run_audit(
                    [[{"id": ruleset_id}]],
                    {ruleset_id: detail},
                    raw_policy=json.dumps(policy),
                )

                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn(diagnostic, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_duplicate_policy_keys_and_duplicate_ruleset_ids_fail_closed(self):
        result, _ = self.run_audit(
            [[{"id": 11}]],
            {11: ruleset(11)},
            raw_policy=(
                '{"organization":"Verjson","release_authorization_bypass":'
                '{"actor_type":"Integration","actor_id":4583107,'
                '"actor_id":1,"bypass_mode":"always"}}'
            ),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate object key 'actor_id'", result.stderr)

        result, _ = self.run_audit([[{"id": 12}], [{"id": 12}]], {12: ruleset(12)})
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate ruleset id 12", result.stderr)


    def test_required_status_check_without_producer_app_binding_fails(self):
        detail = ruleset(
            1,
            name="core-checks-actions",
            rules=[
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": False,
                        "required_status_checks": [{"context": "shell-tests"}],
                    },
                }
            ],
        )

        result, _ = self.run_audit([[{"id": 1}]], {1: detail})

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "core-checks-actions (1): required status check 'shell-tests' "
            "is not bound to a producer App",
            result.stderr,
        )


    def test_required_status_check_bound_to_another_app_fails(self):
        detail = ruleset(
            1,
            name="core-checks-actions",
            rules=[
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": False,
                        "required_status_checks": [
                            {"context": "shell-tests", "integration_id": 999}
                        ],
                    },
                }
            ],
        )

        result, _ = self.run_audit([[{"id": 1}]], {1: detail})

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "core-checks-actions (1): required status check 'shell-tests' "
            "is bound to App 999, not the declared producer App 15368",
            result.stderr,
        )


    def test_a_malformed_required_status_checks_rule_fails_closed(self):
        # The claim this PR makes in its ADR and docs is that a malformed
        # `required_status_checks` block fails closed rather than raising. Each
        # of these shapes was accepted by a weakened guard while the whole
        # suite stayed green: a rule with no `parameters`, parameters with no
        # check array, and a check with no `context`. An unreadable rule that
        # yields no finding is indistinguishable from a conformant one.
        cases = [
            (
                {"type": "required_status_checks"},
                "rule 0.parameters must be an object",
            ),
            (
                {"type": "required_status_checks", "parameters": {}},
                "required_status_checks must be an array",
            ),
            (
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"integration_id": 15368}]},
                },
                "required_status_checks[0].context must be a non-empty string",
            ),
        ]
        for rule, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                result, _ = self.run_audit([[{"id": 1}]], {1: ruleset(1, rules=[rule])})

                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(diagnostic, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_non_integer_producer_binding_fails_closed(self):
        for binding in ("15368", 0, -15368, True, [15368]):
            with self.subTest(binding=binding):
                detail = ruleset(
                    1,
                    rules=[
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "strict_required_status_checks_policy": False,
                                "required_status_checks": [
                                    {"context": "shell-tests", "integration_id": binding}
                                ],
                            },
                        }
                    ],
                )

                result, _ = self.run_audit([[{"id": 1}]], {1: detail})

                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(
                    "ruleset 1 rule 0.parameters.required_status_checks[0]."
                    "integration_id must be a positive integer",
                    result.stderr,
                )
                self.assertNotIn("Traceback", result.stderr)


    def test_explicit_null_binding_reports_an_absent_producer(self):
        detail = ruleset(
            1,
            rules=[
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": False,
                        "required_status_checks": [
                            {"context": "shell-tests", "integration_id": None}
                        ],
                    },
                }
            ],
        )

        result, _ = self.run_audit([[{"id": 1}]], {1: detail})

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("is not bound to a producer App", result.stderr)

    def test_binding_finding_does_not_mask_a_missing_release_actor(self):
        detail = ruleset(
            1,
            name="core-checks-actions",
            bypass_actors=[],
            rules=[
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": False,
                        "required_status_checks": [{"context": "shell-tests"}],
                    },
                }
            ],
        )

        result, _ = self.run_audit([[{"id": 1}]], {1: detail})

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("required release authorization bypass is absent", result.stderr)
        self.assertIn("is not bound to a producer App", result.stderr)

    def test_policy_without_a_declared_producer_app_fails_closed(self):
        for producer in (None, 0, "15368"):
            with self.subTest(producer=producer):
                document = {
                    "organization": "Verjson",
                    "release_authorization_bypass": {
                        "actor_type": "Integration",
                        "actor_id": 4583107,
                        "bypass_mode": "always",
                    },
                    "bypassless_required_workflows": [],
                }
                if producer is not None:
                    document["required_check_producer_app_id"] = producer

                result, _ = self.run_audit(
                    [[{"id": 1}]], {1: ruleset(1)}, raw_policy=json.dumps(document)
                )

                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_bindings_outside_default_branch_rulesets_are_still_required(self):
        detail = ruleset(
            1,
            include=["refs/heads/release/*"],
            rules=[
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": False,
                        "required_status_checks": [{"context": "shell-tests"}],
                    },
                }
            ],
        )

        result, _ = self.run_audit([[{"id": 1}]], {1: detail})

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("is not bound to a producer App", result.stderr)


if __name__ == "__main__":
    unittest.main()
