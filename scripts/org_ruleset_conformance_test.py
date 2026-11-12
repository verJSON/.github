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


def merge_admission_ruleset(ruleset_id, *checks, enforcement="disabled"):
    """ADR 0260's item-2 ruleset as GitHub returns it once created, by default disabled."""
    detail = ruleset(
        ruleset_id,
        name=MERGE_ADMISSION_BINDING["ruleset_name"],
        bypass_actors=copy.deepcopy(MERGE_ADMISSION_BYPASS),
        rules=[required_checks_rule(*checks)],
    )
    detail["enforcement"] = enforcement
    return detail


def merge_admission_contract():
    return {
        "ruleset_id": None,
        "name": MERGE_ADMISSION_BINDING["ruleset_name"],
        "bypass_actors": copy.deepcopy(MERGE_ADMISSION_BYPASS),
    }


def required_checks_rule(*checks):
    return {
        "type": "required_status_checks",
        "parameters": {
            "strict_required_status_checks_policy": False,
            "required_status_checks": list(checks),
        },
    }


def actions_state_responses(repositories):
    """The ADR 0252 paused-state reads, for repositories mapped to (id, verjson-actions-state)."""
    return {
        "orgs/Verjson/repos?type=all&per_page=100": [[
            {"id": repository_id, "name": name, "default_branch": "main"}
            for name, (repository_id, _) in repositories.items()
        ]],
        "orgs/Verjson/properties/values?per_page=100": [[
            {
                "repository_id": repository_id,
                "repository_name": name,
                "properties": [{"property_name": "verjson-actions-state", "value": state}],
            }
            for name, (repository_id, state) in repositories.items()
        ]],
    }


CORE_CHECKS_NODE_BYPASS = [
    {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"},
    {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
    {"actor_type": "Integration", "actor_id": 4693283, "bypass_mode": "always"},
]

# ADR 0260 Decision 2: `merge-admission` is the one context bound to the AI review App,
# and the item-2 ruleset's bypass list mirrors 20722935 without that App
# (ARB-2031-2026-11-12 C6, C10).
MERGE_ADMISSION_BINDING = {
    "context": "merge-admission",
    "integration_id": 4528902,
    "app_id_variable": "AI_REVIEW_APP_ID",
    "ruleset_name": "merge-admission-required",
}
APP_ID_VARIABLE_PATH = "orgs/Verjson/actions/variables/AI_REVIEW_APP_ID"
PRE_CREATION_RECEIPT = (
    "merge-admission-receipt=pre-creation gates=creation context=merge-admission "
    "app_id=4528902 app_id_variable=AI_REVIEW_APP_ID ruleset=merge-admission-required "
    "ruleset_state=absent"
)
MERGE_ADMISSION_BYPASS = [
    {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"},
    {"actor_type": "Integration", "actor_id": 2740, "bypass_mode": "always"},
    {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
    {"actor_type": "Integration", "actor_id": 4693283, "bypass_mode": "always"},
]


def paused_baseline():
    """The live image of ADR 0252's stand-in baseline ruleset 26466984 (2026-11-03)."""
    return {
        "id": 26466984,
        "name": "actions-paused-baseline-protection",
        "source_type": "Organization",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": copy.deepcopy(CORE_CHECKS_NODE_BYPASS),
        "conditions": {
            "ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]},
            "repository_property": {
                "exclude": [],
                "include": [{
                    "name": "verjson-actions-state",
                    "property_values": ["paused"],
                    "source": "custom",
                }],
            },
        },
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "allowed_merge_methods": ["merge", "squash", "rebase"],
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_extra_approval_for_unattributed_changes": True,
                    "require_last_push_approval": False,
                    "required_approving_review_count": 0,
                    "required_review_thread_resolution": False,
                    "required_reviewers": [],
                },
            },
            {"type": "non_fast_forward"},
            {"type": "deletion"},
        ],
    }


CLI_PROJECTS = "verjson-cli-projects"
CLI_PROJECTS_ID = 1277452690


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
        extra_responses=None,
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
                        "merge_admission_binding": copy.deepcopy(MERGE_ADMISSION_BINDING),
                        "bypassless_required_workflows": [],
                        "bypass_actor_contracts": [merge_admission_contract()] + [
                            {
                                "ruleset_id": ruleset_id,
                                "name": detail["name"],
                                "bypass_actors": detail["bypass_actors"],
                            }
                            for ruleset_id, detail in details.items()
                            if detail.get("bypass_actors")
                            # The name-keyed contract above covers it (C6).
                            and detail["name"] != MERGE_ADMISSION_BINDING["ruleset_name"]
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
            responses[APP_ID_VARIABLE_PATH] = [
                {"name": "AI_REVIEW_APP_ID", "value": "4528902", "visibility": "all"}
            ]
            responses.update(extra_responses or {})
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
                ["api", "--hostname", "github.com", "--method", "GET", "--paginate", "--slurp", APP_ID_VARIABLE_PATH],
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
                "merge_admission_binding": {
                    "context": "merge-admission",
                    "integration_id": 4528902,
                    "app_id_variable": "AI_REVIEW_APP_ID",
                    "ruleset_name": "merge-admission-required",
                },
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
                    {
                        "ruleset_id": None,
                        "name": "merge-admission-required",
                        "bypass_actors": [
                            {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 2740, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                            {"actor_type": "Integration", "actor_id": 4693283, "bypass_mode": "always"},
                        ],
                    },
                    {
                        "ruleset_id": None,
                        "name": "verjson-cli-cloud-sensitive-path-admin-review",
                        "bypass_actors": [
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                        ],
                    },
                    {
                        "ruleset_id": None,
                        "name": "verjson-cli-projects-sensitive-path-admin-review",
                        "bypass_actors": [
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                        ],
                    },
                    {
                        "ruleset_id": None,
                        "name": "verjson-cli-sensitive-path-admin-review",
                        "bypass_actors": [
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                        ],
                    },
                    {
                        "ruleset_id": None,
                        "name": "verjson-object-storage-sensitive-path-admin-review",
                        "bypass_actors": [
                            {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"},
                        ],
                    },
                ],
            },
        )

    def test_inherited_policy_environment_cannot_redirect_production_policy(self):
        committed = json.loads(POLICY.read_text(encoding="utf-8"))
        # A name-keyed contract (null id) is matched by name, so give its live
        # ruleset any id that no id-keyed contract uses.
        details = {
            ruleset_id: ruleset(
                ruleset_id,
                name=contract["name"],
                include=["refs/heads/release/*"],
                bypass_actors=contract["bypass_actors"],
            )
            for index, contract in enumerate(committed["bypass_actor_contracts"])
            for ruleset_id in [contract["ruleset_id"] or 90_000_000 + index]
        }
        for detail in details.values():
            if detail["name"] == MERGE_ADMISSION_BINDING["ruleset_name"]:
                detail["rules"] = [required_checks_rule(
                    {"context": "merge-admission", "integration_id": 4528902}
                )]
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
            all(call[-1].startswith("orgs/Verjson/") for call in calls),
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
        policy["bypass_actor_contracts"] = [merge_admission_contract()]
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
        policy["bypass_actor_contracts"] = [merge_admission_contract()]
        return json.dumps(policy)

    def test_bypassless_cli_projects_workflow_with_its_strict_admission_check_passes(self):
        result, _ = self.run_audit(
            [[{"id": 21830367}]],
            {21830367: self.cli_projects_required_workflow()},
            raw_policy=self.committed_policy_without_bypass_contracts(),
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def run_cli_projects_at_evaluate(self, detail=None, *, state="paused", **options):
        detail = detail or self.cli_projects_required_workflow()
        detail["enforcement"] = "evaluate"
        options.setdefault("extra_responses", actions_state_responses({CLI_PROJECTS: (CLI_PROJECTS_ID, state)}))
        return self.run_audit(
            [[{"id": detail["id"]}]],
            {detail["id"]: detail},
            raw_policy=self.committed_policy_without_bypass_contracts(),
            **options,
        )

    def test_recorded_relaxation_at_evaluate_is_accepted_while_its_repository_is_paused(self):
        result, _ = self.run_cli_projects_at_evaluate()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            "paused-relaxation=accepted cli-projects-package-surface-required-workflow (21830367) "
            "enforcement=evaluate while verjson-cli-projects is verjson-actions-state=paused (ADR 0252)",
            result.stdout,
        )

    def test_recorded_relaxation_at_evaluate_is_refused_once_its_repository_is_enabled(self):
        result, _ = self.run_cli_projects_at_evaluate(state="enabled")

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(
            "ERROR: cli-projects-package-surface-required-workflow (21830367): enforcement is "
            "evaluate but verjson-cli-projects is verjson-actions-state=enabled; "
            "restore enforcement to active (ADR 0252)",
            result.stderr,
        )

    def test_recorded_relaxation_at_evaluate_is_unestablished_when_its_repository_state_is_unreadable(self):
        # An outage is not drift: advising a restore here would re-block paused repositories.
        result, _ = self.run_cli_projects_at_evaluate(
            failing_paths=["orgs/Verjson/properties/values?per_page=100"]
        )

        self.assertEqual(2, result.returncode, result.stderr)
        self.assertEqual(
            "ERROR: cannot establish organization ruleset state: "
            "cli-projects-package-surface-required-workflow (21830367): enforcement is "
            "evaluate but verjson-actions-state of verjson-cli-projects cannot be read "
            "(GitHub API read failed for orgs/Verjson/properties/values?per_page=100)\n",
            result.stderr,
        )

    def test_drift_beside_an_unestablished_paused_state_is_reported_and_the_verdict_is_unknown(self):
        # An unread paused state must neither hide a finding the audit did establish
        # nor let that finding's exit 1 claim the verdict is known.
        unreadable = self.cli_projects_required_workflow()
        unreadable["enforcement"] = "evaluate"
        drift = ruleset(
            20515822,
            name="core-checks-actions",
            bypass_actors=[
                {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"}
            ],
        )

        result, _ = self.run_audit(
            [[{"id": unreadable["id"]}, {"id": drift["id"]}]],
            {unreadable["id"]: unreadable, drift["id"]: drift},
            raw_policy=self.committed_policy_without_bypass_contracts(),
            failing_paths=["orgs/Verjson/properties/values?per_page=100"],
        )

        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn(
            "ERROR: core-checks-actions (20515822): required release authorization bypass is absent",
            result.stderr,
        )
        self.assertIn(
            "ERROR: cannot establish organization ruleset state: "
            "cli-projects-package-surface-required-workflow (21830367)",
            result.stderr,
        )
        self.assertNotIn("ruleset-policy=conformant", result.stdout)

    def test_recorded_relaxation_at_evaluate_is_refused_when_its_repository_has_no_actions_state(self):
        result, _ = self.run_cli_projects_at_evaluate(state=None)

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(
            "ERROR: cli-projects-package-surface-required-workflow (21830367): enforcement is "
            "evaluate but verjson-cli-projects has no verjson-actions-state value; "
            "restore enforcement to active (ADR 0252)",
            result.stderr,
        )

    def test_unrecorded_ruleset_at_evaluate_is_refused(self):
        detail = self.cli_projects_required_workflow()
        detail["id"] = 99

        result, _ = self.run_cli_projects_at_evaluate(detail)

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(
            "ERROR: cli-projects-package-surface-required-workflow (99): enforcement is "
            "evaluate but ADR 0252 records no paused-only relaxation for this ruleset; "
            "restore enforcement to active (ADR 0252)",
            result.stderr,
        )

    def test_relaxation_at_evaluate_is_refused_when_its_target_is_not_the_recorded_repository(self):
        # The paused property read must be the targeted repository's, not a namesake's.
        result, _ = self.run_cli_projects_at_evaluate(
            extra_responses=actions_state_responses({CLI_PROJECTS: (5, "paused")})
        )

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(
            "ERROR: cli-projects-package-surface-required-workflow (21830367): enforcement is "
            f"evaluate but it targets [{CLI_PROJECTS_ID}], not exactly verjson-cli-projects (5); "
            "restore enforcement to active (ADR 0252)",
            result.stderr,
        )

    def test_relaxation_at_evaluate_with_any_other_drift_is_still_refused(self):
        detail = self.cli_projects_required_workflow()
        detail["rules"][0]["parameters"]["workflows"][0]["ref"] = "refs/heads/unreviewed"

        result, _ = self.run_cli_projects_at_evaluate(detail)

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(
            "ERROR: cli-projects-package-surface-required-workflow (21830367): "
            "required release authorization bypass is absent",
            result.stderr,
        )
        self.assertNotIn("paused-relaxation=accepted", result.stdout)

    def run_with_paused_baseline(self, baseline):
        """Audit the stand-in beside core-checks-node, the only reviewed contract present."""
        core_checks_node = ruleset(
            20515817, name="core-checks-node", bypass_actors=copy.deepcopy(CORE_CHECKS_NODE_BYPASS)
        )
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        policy["bypass_actor_contracts"] = [
            contract
            for contract in policy["bypass_actor_contracts"]
            if contract["ruleset_id"] == 20515817
            or contract["name"] == MERGE_ADMISSION_BINDING["ruleset_name"]
        ]
        return self.run_audit(
            [[{"id": 20515817}, {"id": baseline["id"]}]],
            {20515817: core_checks_node, baseline["id"]: baseline},
            raw_policy=json.dumps(policy),
        )

    def test_paused_baseline_with_the_reviewed_core_checks_bypass_set_is_accepted(self):
        result, _ = self.run_with_paused_baseline(paused_baseline())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            "paused-baseline=accepted actions-paused-baseline-protection (26466984) matches "
            "ADR 0252 with the bypass actors of the reviewed core-checks-node (20515817) contract",
            result.stdout,
        )

    def test_paused_baseline_with_an_extra_or_altered_bypass_actor_is_refused(self):
        renovate = {"actor_type": "Integration", "actor_id": 2740, "bypass_mode": "always"}
        mutations = {
            "extra actor": lambda actors: actors.append(renovate),
            "altered mode": lambda actors: actors[1].update(bypass_mode="pull_request"),
            "missing actor": lambda actors: actors.pop(),
        }
        for name, mutate in mutations.items():
            baseline = paused_baseline()
            mutate(baseline["bypass_actors"])
            with self.subTest(name):
                result, _ = self.run_with_paused_baseline(baseline)

                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn(
                    "ERROR: actions-paused-baseline-protection (26466984): bypass actors differ "
                    "from the reviewed core-checks-node (20515817) contract (ADR 0252)",
                    result.stderr,
                )

    def test_paused_baseline_with_any_other_rule_is_refused(self):
        def pull_request(rules):
            return rules[0]["parameters"]

        mutations = {
            "extra rule": lambda rules: rules.append({"type": "required_linear_history"}),
            "missing rule": lambda rules: rules.pop(),
            "duplicated rule": lambda rules: rules.append(copy.deepcopy(rules[0])),
            "approvals required": lambda rules: pull_request(rules).update(
                required_approving_review_count=1
            ),
            "parameterized deletion": lambda rules: rules[2].update(parameters={}),
            "extra pull_request key": lambda rules: rules[0].update(conditions={}),
            "boolean approvals": lambda rules: pull_request(rules).update(
                required_approving_review_count=False
            ),
        }
        for name, mutate in mutations.items():
            baseline = paused_baseline()
            mutate(baseline["rules"])
            with self.subTest(name):
                result, _ = self.run_with_paused_baseline(baseline)

                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn(
                    "ERROR: actions-paused-baseline-protection (26466984): rules differ from "
                    "ADR 0252's pull_request with 0 approvals, non_fast_forward and deletion",
                    result.stderr,
                )

    def test_paused_baseline_with_any_other_target_is_refused(self):
        def property_condition(conditions):
            return conditions["repository_property"]["include"][0]

        mutations = {
            "another branch": lambda c: c["ref_name"]["include"].append("refs/heads/develop"),
            "branch exclusion": lambda c: c["ref_name"]["exclude"].append("~DEFAULT_BRANCH"),
            "enabled repositories": lambda c: property_condition(c).update(
                property_values=["paused", "enabled"]
            ),
            "another property": lambda c: property_condition(c).update(name="verjson-core-checks"),
            "repository exclusion": lambda c: c["repository_property"]["exclude"].append(
                {"name": "verjson-actions-state", "property_values": ["paused"], "source": "custom"}
            ),
            "all repositories": lambda c: c.update(
                repository_name={"include": ["~ALL"], "exclude": []}
            ),
            "tag target": lambda c: None,
        }
        for name, mutate in mutations.items():
            baseline = paused_baseline()
            mutate(baseline["conditions"])
            if name == "tag target":
                baseline["target"] = "tag"
            with self.subTest(name):
                result, _ = self.run_with_paused_baseline(baseline)

                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn(
                    "ERROR: actions-paused-baseline-protection (26466984): target differs from "
                    "ADR 0252's ~DEFAULT_BRANCH of verjson-actions-state=paused repositories",
                    result.stderr,
                )

    def test_paused_baseline_that_is_not_enforced_is_refused(self):
        for enforcement in ("evaluate", "disabled"):
            baseline = paused_baseline()
            baseline["enforcement"] = enforcement
            with self.subTest(enforcement):
                result, _ = self.run_with_paused_baseline(baseline)

                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn(
                    f"ERROR: actions-paused-baseline-protection (26466984): enforcement is "
                    f"{enforcement}, but ADR 0252's baseline protection must be active",
                    result.stderr,
                )

    def test_paused_baseline_without_its_reference_contract_is_refused(self):
        baseline = paused_baseline()

        result, _ = self.run_audit(
            [[{"id": baseline["id"]}]],
            {baseline["id"]: baseline},
            raw_policy=self.committed_policy_without_bypass_contracts(),
        )

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(
            "ERROR: actions-paused-baseline-protection (26466984): bypass actors are not "
            "covered by a reviewed contract; ADR 0252 derives them from contract 20515817, "
            "which is absent",
            result.stderr,
        )

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
            "merge_admission_binding": copy.deepcopy(MERGE_ADMISSION_BINDING),
            "bypassless_required_workflows": [],
            "bypass_actor_contracts": [merge_admission_contract()],
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
            "merge_admission_binding": copy.deepcopy(MERGE_ADMISSION_BINDING),
            "bypassless_required_workflows": [],
            "bypass_actor_contracts": [
                merge_admission_contract(),
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
            "merge_admission_binding": copy.deepcopy(MERGE_ADMISSION_BINDING),
            "bypassless_required_workflows": [],
            "bypass_actor_contracts": [
                merge_admission_contract(),
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
            "merge_admission_binding": copy.deepcopy(MERGE_ADMISSION_BINDING),
            "bypassless_required_workflows": [],
            "bypass_actor_contracts": [merge_admission_contract()],
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
            "merge_admission_binding": copy.deepcopy(MERGE_ADMISSION_BINDING),
            "bypassless_required_workflows": [],
            "bypass_actor_contracts": [merge_admission_contract()],
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
            "merge_admission_binding": copy.deepcopy(MERGE_ADMISSION_BINDING),
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
                policy = base_policy | {"bypass_actor_contracts": [merge_admission_contract(), contract]}
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
            "merge_admission_binding": copy.deepcopy(MERGE_ADMISSION_BINDING),
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
                policy = base_policy | {"bypass_actor_contracts": [merge_admission_contract(), *contracts]}
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
            "merge_admission_binding": copy.deepcopy(MERGE_ADMISSION_BINDING),
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
                        merge_admission_contract(),
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
        self.assertEqual(
            [call[-1] for call in calls if call[-1].startswith("orgs/Verjson/rulesets")],
            ["orgs/Verjson/rulesets?per_page=100", "orgs/Verjson/rulesets/5", "orgs/Verjson/rulesets/6"],
        )

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
                    "merge_admission_binding": copy.deepcopy(MERGE_ADMISSION_BINDING),
                    "bypassless_required_workflows": [],
                    "bypass_actor_contracts": [
                        merge_admission_contract(),
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
                    "merge_admission_binding": copy.deepcopy(MERGE_ADMISSION_BINDING),
                    "bypassless_required_workflows": [],
                }
                if producer is not None:
                    document["required_check_producer_app_id"] = producer

                result, _ = self.run_audit(
                    [[{"id": 1}]], {1: ruleset(1)}, raw_policy=json.dumps(document)
                )

                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_merge_admission_bound_to_the_ai_review_app_is_conformant(self):
        detail = merge_admission_ruleset(
            1,
            {"context": "shell-tests", "integration_id": 15368},
            {"context": "merge-admission", "integration_id": 4528902},
        )

        result, _ = self.run_audit([[{"id": 1}]], {1: detail})

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_policy_cannot_bind_merge_admission_to_the_declared_producer(self):
        # ADR 0260 Decision 2: merge-admission bound to 15368 is a finding, so a policy
        # that declares 15368 for it would turn that finding into a pass.
        policy = json.loads(self.committed_policy_without_bypass_contracts())
        policy["merge_admission_binding"]["integration_id"] = 15368

        result, _ = self.run_audit(
            [[{"id": 1}]],
            {1: ruleset(1, rules=[required_checks_rule(
                {"context": "merge-admission", "integration_id": 15368},
            )])},
            raw_policy=json.dumps(policy),
        )

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(
            "ruleset policy.merge_admission_binding.integration_id must not be the "
            "declared producer App 15368",
            result.stderr,
        )

    def test_merge_admission_ruleset_needs_a_reviewed_bypass_contract_before_it_exists(self):
        # ARB-2031-2026-11-12 C6: the name-keyed contract lands before the ruleset.
        policy = json.loads(self.committed_policy_without_bypass_contracts())
        policy["bypass_actor_contracts"] = []

        result, _ = self.run_audit(
            [[{"id": 1}]], {1: ruleset(1, bypass_actors=[])}, raw_policy=json.dumps(policy)
        )

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(
            "ruleset policy.merge_admission_binding.ruleset_name 'merge-admission-required' "
            "has no reviewed bypass contract",
            result.stderr,
        )

    def test_merge_admission_ruleset_contract_never_lets_its_producer_app_bypass(self):
        # ARB-2031-2026-11-12 C6: a producer that can bypass the ruleset requiring its
        # own check could merge past it.
        policy = json.loads(self.committed_policy_without_bypass_contracts())
        [contract] = policy["bypass_actor_contracts"]
        contract["bypass_actors"].append(
            {"actor_type": "Integration", "actor_id": 4528902, "bypass_mode": "always"}
        )

        result, _ = self.run_audit(
            [[{"id": 1}]], {1: ruleset(1, bypass_actors=[])}, raw_policy=json.dumps(policy)
        )

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(
            "reviewed bypass contract merge-admission-required must not list the "
            "merge-admission producer App 4528902",
            result.stderr,
        )

    def test_absent_merge_admission_ruleset_yields_the_pre_creation_receipt(self):
        # ARB-2031-2026-11-12 C10: before creation the receipt checks the org variable
        # and names the ruleset as absent, never passing silently.
        result, _ = self.run_audit([[{"id": 1}]], {1: ruleset(1)})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(PRE_CREATION_RECEIPT, result.stdout.splitlines())

    def test_org_variable_naming_another_app_fails_before_creation(self):
        result, _ = self.run_audit(
            [[{"id": 1}]],
            {1: ruleset(1)},
            extra_responses={APP_ID_VARIABLE_PATH: [{"name": "AI_REVIEW_APP_ID", "value": "1234567"}]},
        )

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "ERROR: merge-admission ruleset merge-admission-required is absent: organization "
            "variable AI_REVIEW_APP_ID does not name the policy's merge-admission producer "
            "App 4528902",
            result.stderr,
        )
        self.assertNotIn("1234567", result.stdout + result.stderr)
        self.assertNotIn("merge-admission-receipt=", result.stdout)

    def test_disabled_merge_admission_ruleset_bound_to_the_app_yields_the_full_receipt(self):
        # C10: once the ruleset exists, even disabled, the receipt also reads its binding.
        # It gates activation only: it checks no enrollment scope (C3 canary id, paused
        # exclusion, C8 caller presence), so it must not claim to gate an enrollment edit.
        detail = merge_admission_ruleset(
            41, {"context": "merge-admission", "integration_id": 4528902}
        )

        result, _ = self.run_audit([[{"id": 41}]], {41: detail})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "merge-admission-receipt=full gates=activation "
            "context=merge-admission app_id=4528902 app_id_variable=AI_REVIEW_APP_ID "
            "ruleset=merge-admission-required ruleset_id=41 enforcement=disabled",
            result.stdout.splitlines(),
        )

    def test_merge_admission_ruleset_that_does_not_require_the_context_fails(self):
        detail = merge_admission_ruleset(
            41, {"context": "shell-tests", "integration_id": 15368}
        )

        result, _ = self.run_audit([[{"id": 41}]], {41: detail})

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "ERROR: merge-admission ruleset merge-admission-required (41): does not require "
            "the merge-admission context",
            result.stderr,
        )
        self.assertNotIn("merge-admission-receipt=", result.stdout)

    def test_two_live_rulesets_with_the_merge_admission_name_yield_no_receipt(self):
        # After creation C6 pins the contract to the ruleset id, so a second bypass-less
        # ruleset with the same name is not a contract finding; the receipt must not
        # pick one of the two.
        bound = {"context": "merge-admission", "integration_id": 4528902}
        pinned = merge_admission_ruleset(41, bound)
        shadow = merge_admission_ruleset(42, bound) | {"bypass_actors": []}
        shadow["conditions"]["ref_name"]["include"] = ["refs/heads/release/*"]
        policy = json.loads(self.committed_policy_without_bypass_contracts())
        policy["bypass_actor_contracts"][0]["ruleset_id"] = 41

        result, _ = self.run_audit(
            [[{"id": 41}, {"id": 42}]], {41: pinned, 42: shadow}, raw_policy=json.dumps(policy)
        )

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "ERROR: merge-admission ruleset merge-admission-required: 2 live rulesets share "
            "the name",
            result.stderr,
        )
        self.assertNotIn("merge-admission-receipt=", result.stdout)

    def test_merge_admission_required_under_another_name_yields_no_receipt(self):
        # The binding check accepts merge-admission on 4528902 in any ruleset, so an
        # active ruleset under another name, or another case, with a reviewed contract
        # for its bypass actors, would otherwise enforce the context while the receipt
        # reports the ruleset absent (ARB-2031-2026-11-12 C5).
        bound = {"context": "merge-admission", "integration_id": 4528902}
        for name in ("merge-admission-gate", "Merge-Admission-Required"):
            with self.subTest(name=name):
                detail = merge_admission_ruleset(41, bound, enforcement="active")
                detail["name"] = name

                result, _ = self.run_audit([[{"id": 41}]], {41: detail})

                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(
                    f"ERROR: {name} (41): requires the merge-admission context but is not "
                    "the policy's merge-admission ruleset merge-admission-required",
                    result.stderr,
                )
                self.assertNotIn("merge-admission-receipt=", result.stdout)

    def test_app_id_variable_must_be_a_variable_name_before_it_reaches_an_api_path(self):
        for name in ("AI_REVIEW_APP_ID/../../rulesets", "ai_review_app_id", "AI REVIEW"):
            with self.subTest(name=name):
                policy = json.loads(self.committed_policy_without_bypass_contracts())
                policy["merge_admission_binding"]["app_id_variable"] = name

                result, calls = self.run_audit(
                    [[{"id": 1}]], {1: ruleset(1)}, raw_policy=json.dumps(policy)
                )

                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(
                    "ruleset policy.merge_admission_binding.app_id_variable must be an "
                    "organization variable name",
                    result.stderr,
                )
                self.assertEqual(calls, [])

    def test_merge_admission_ruleset_with_its_context_unbound_or_on_actions_yields_no_receipt(self):
        cases = [
            ({"context": "merge-admission", "integration_id": 15368},
             "is bound to App 15368, not the declared merge-admission producer App 4528902"),
            ({"context": "merge-admission", "integration_id": None},
             "is not bound to a producer App"),
            ({"context": "merge-admission"}, "is not bound to a producer App"),
        ]
        for check, diagnostic in cases:
            with self.subTest(check=check):
                result, _ = self.run_audit(
                    [[{"id": 41}]], {41: merge_admission_ruleset(41, check)}
                )

                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(
                    "merge-admission-required (41): required status check "
                    f"'merge-admission' {diagnostic}",
                    result.stderr,
                )
                self.assertNotIn("merge-admission-receipt=", result.stdout)

    def test_any_other_context_bound_to_the_ai_review_app_still_fails(self):
        detail = ruleset(1, rules=[required_checks_rule(
            {"context": "shell-tests", "integration_id": 4528902},
        )])

        result, _ = self.run_audit([[{"id": 1}]], {1: detail})

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "ruleset-1 (1): required status check 'shell-tests' is bound to App 4528902, "
            "not the declared producer App 15368",
            result.stderr,
        )

    def test_org_variable_naming_another_app_fails_once_the_ruleset_exists(self):
        detail = merge_admission_ruleset(
            41, {"context": "merge-admission", "integration_id": 4528902}
        )

        result, _ = self.run_audit(
            [[{"id": 41}]],
            {41: detail},
            extra_responses={APP_ID_VARIABLE_PATH: [{"name": "AI_REVIEW_APP_ID", "value": "15368"}]},
        )

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "ERROR: merge-admission ruleset merge-admission-required (41): organization "
            "variable AI_REVIEW_APP_ID does not name the policy's merge-admission producer "
            "App 4528902",
            result.stderr,
        )
        self.assertNotIn("merge-admission-receipt=", result.stdout)

    def test_org_variable_compares_as_the_exact_id_string(self):
        for value in ("04528902", " 4528902", "4528902\n", "4528902.0"):
            with self.subTest(value=value):
                result, _ = self.run_audit(
                    [[{"id": 1}]],
                    {1: ruleset(1)},
                    extra_responses={
                        APP_ID_VARIABLE_PATH: [{"name": "AI_REVIEW_APP_ID", "value": value}]
                    },
                )

                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("does not name the policy's merge-admission producer App", result.stderr)

    def test_unreadable_or_malformed_org_variable_fails_closed(self):
        cases = [
            ({"failing_paths": [APP_ID_VARIABLE_PATH]},
             "GitHub API read failed for orgs/Verjson/actions/variables/AI_REVIEW_APP_ID"),
            ({"extra_responses": {APP_ID_VARIABLE_PATH: [{"name": "OTHER", "value": "4528902"}]}},
             "organization variable AI_REVIEW_APP_ID response names another variable"),
            ({"extra_responses": {APP_ID_VARIABLE_PATH: [{"name": "AI_REVIEW_APP_ID", "value": 4528902}]}},
             "organization variable AI_REVIEW_APP_ID.value must be a non-empty string"),
            ({"extra_responses": {APP_ID_VARIABLE_PATH: [{"name": "AI_REVIEW_APP_ID"}]}},
             "organization variable AI_REVIEW_APP_ID.value must be a non-empty string"),
            ({"extra_responses": {APP_ID_VARIABLE_PATH: [[]]}},
             "organization variable AI_REVIEW_APP_ID must be an object"),
        ]
        for arguments, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                result, _ = self.run_audit([[{"id": 1}]], {1: ruleset(1)}, **arguments)

                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(diagnostic, result.stderr)
                self.assertNotIn("merge-admission-receipt=", result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_malformed_merge_admission_binding_fails_closed(self):
        cases = [
            (lambda binding: binding.pop("ruleset_name"), "merge_admission_binding keys are"),
            (lambda binding: binding.update(extra=True), "merge_admission_binding keys are"),
            (lambda binding: binding.update(integration_id="4528902"),
             "merge_admission_binding.integration_id must be a positive integer"),
            (lambda binding: binding.update(integration_id=True),
             "merge_admission_binding.integration_id must be a positive integer"),
            (lambda binding: binding.update(context=""),
             "merge_admission_binding.context must be a non-empty string"),
        ]
        for mutate, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                policy = json.loads(self.committed_policy_without_bypass_contracts())
                mutate(policy["merge_admission_binding"])

                result, calls = self.run_audit(
                    [[{"id": 1}]], {1: ruleset(1)}, raw_policy=json.dumps(policy)
                )

                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(diagnostic, result.stderr)
                self.assertEqual(calls, [])

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
