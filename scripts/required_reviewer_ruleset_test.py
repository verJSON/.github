#!/usr/bin/env python3
"""Behavior of scripts/required-reviewer-ruleset.py (#1816, ADR 0210 amendment).

Every GitHub call goes through a fake `gh` on PATH that serves a stateful
organization ruleset store, so `apply` can be exercised end to end, twice, with
no network.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/required-reviewer-ruleset.py"
CONFIG = ROOT / "config/required-reviewer-rulesets.json"
ACK = "APPLY-REQUIRED-REVIEWERS-1816"
RELEASE_BYPASS = {"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"}
ADMINS_TEAM_ID = 17813010

# The desired state proposed on #1816 and rehearsed as ruleset 25327030.
CLI_CLOUD_PROPOSAL = {
    "name": "verjson-cli-cloud-sensitive-path-admin-review",
    "target": "branch",
    "enforcement": "active",
    "conditions": {
        "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
        "repository_name": {"include": ["verjson-cli-cloud"], "exclude": [], "protected": True},
    },
    "bypass_actors": [RELEASE_BYPASS],
    "rules": [{
        "type": "pull_request",
        "parameters": {
            "allowed_merge_methods": ["squash"],
            "dismiss_stale_reviews_on_push": True,
            "require_code_owner_review": False,
            "require_last_push_approval": False,
            "require_extra_approval_for_unattributed_changes": False,
            "required_approving_review_count": 0,
            "required_review_thread_resolution": False,
            "required_reviewers": [{
                "reviewer": {"type": "Team", "id": ADMINS_TEAM_ID},
                "minimum_approvals": 1,
                "file_patterns": [".github/**", "src/pulumi/**", "src/steps/**", "src/dns/**"],
            }],
        },
    }],
}


def load_module():
    spec = importlib.util.spec_from_file_location("required_reviewer_ruleset", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
state_path = os.environ["FAKE_GH_STATE"]
with open(state_path, encoding="utf-8") as stream:
    state = json.load(stream)
if args[:3] != ["api", "--hostname", "github.com"]:
    print("unexpected gh arguments", file=sys.stderr); sys.exit(90)
rest = args[3:]
method, paginate, payload = "GET", False, None
while rest and rest[0].startswith("--"):
    flag = rest.pop(0)
    if flag == "--method":
        method = rest.pop(0)
    elif flag in ("--paginate", "--slurp"):
        paginate = True
    else:
        print(f"unexpected flag {flag}", file=sys.stderr); sys.exit(90)
path = rest.pop(0)
if rest:
    if rest[:1] != ["--input"] or len(rest) != 2:
        print("unexpected trailing arguments", file=sys.stderr); sys.exit(90)
    with open(rest[1], encoding="utf-8") as stream:
        payload = json.load(stream)
with open(os.environ["FAKE_GH_CALLS"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps([method, path, payload]) + "\n")
if f"{method} {path}" in state.get("fail", []):
    print("simulated HTTP failure", file=sys.stderr); sys.exit(1)

def detail(ruleset_id):
    stored = state["rulesets"][str(ruleset_id)]
    return dict(stored, id=ruleset_id, source_type="Organization", source="verJSON",
                created_at="2026-10-19T00:00:00Z", updated_at="2026-10-19T00:00:00Z")

def save():
    with open(state_path, "w", encoding="utf-8") as stream:
        json.dump(state, stream)

prefix = "orgs/Verjson/rulesets"
if method == "GET" and path.startswith("repos/Verjson/") and not paginate:
    name = path.removeprefix("repos/Verjson/")
    if name not in state["repositories"]:
        print("Not Found", file=sys.stderr); sys.exit(1)
    print(json.dumps(dict({"name": name, "full_name": f"verJSON/{name}",
                           "archived": False, "default_branch": "main"},
                          **state["repositories"][name])))
elif method == "GET" and path == f"{prefix}?per_page=100" and paginate:
    print(json.dumps([[{"id": int(key), "name": value["name"]}
                       for key, value in state["rulesets"].items()]]))
elif method == "GET" and path.startswith(prefix + "/") and not paginate:
    key = path.removeprefix(prefix + "/")
    if key not in state["rulesets"]:
        print("Not Found", file=sys.stderr); sys.exit(1)
    print(json.dumps(detail(int(key))))
elif method == "POST" and path == prefix:
    new_id = state.setdefault("next_id", 900)
    state["next_id"] = new_id + 1
    state["rulesets"][str(new_id)] = payload
    save()
    print(json.dumps(detail(new_id)))
elif method == "PUT" and path.startswith(prefix + "/"):
    key = path.removeprefix(prefix + "/")
    if key not in state["rulesets"]:
        print("Not Found", file=sys.stderr); sys.exit(1)
    stored = json.loads(json.dumps(payload))
    if state.get("normalize_active_away_patterns") and payload.get("enforcement") == "active":
        stored["rules"][0]["parameters"]["required_reviewers"][0]["file_patterns"] = []
    state["rulesets"][key] = stored
    save()
    print(json.dumps(detail(int(key))))
else:
    print(f"unexpected API request {method} {path}", file=sys.stderr); sys.exit(91)
'''


class FakeGitHub:
    def __init__(self, directory, rulesets=None, fail=None, **options):
        self.directory = Path(directory)
        self.state = self.directory / "state.json"
        self.calls_file = self.directory / "calls.jsonl"
        options.setdefault("repositories", {
            ruleset["conditions"]["repository_name"]["include"][0]: {}
            for ruleset in desired_rulesets()
        })
        self.state.write_text(json.dumps({
            "rulesets": {str(key): value for key, value in (rulesets or {}).items()},
            "fail": fail or [],
            **options,
        }), encoding="utf-8")
        gh = self.directory / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)

    def run(self, *arguments):
        env = os.environ | {
            "PATH": f"{self.directory}:{os.environ['PATH']}",
            "FAKE_GH_STATE": str(self.state),
            "FAKE_GH_CALLS": str(self.calls_file),
        }
        return subprocess.run(
            ["python3", "-I", str(SCRIPT), *arguments],
            env=env, capture_output=True, text=True, check=False,
        )

    def calls(self):
        if not self.calls_file.exists():
            return []
        return [json.loads(line) for line in self.calls_file.read_text(encoding="utf-8").splitlines()]

    def writes(self):
        return [call for call in self.calls() if call[0] != "GET"]

    def rulesets(self):
        return json.loads(self.state.read_text(encoding="utf-8"))["rulesets"]


def desired_rulesets():
    return json.loads(CONFIG.read_text(encoding="utf-8"))["rulesets"]


class RenderTest(unittest.TestCase):
    def test_render_prints_the_exact_reviewed_cli_cloud_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            result = FakeGitHub(directory).run("render")

        self.assertEqual(0, result.returncode, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(CLI_CLOUD_PROPOSAL, rendered[0])


def mutate_parameters(change):
    def apply(config):
        change(config["rulesets"][0]["rules"][0]["parameters"])
    return apply


def mutate_reviewer(change):
    return mutate_parameters(lambda parameters: change(parameters["required_reviewers"][0]))


CONFIG_MUTATIONS = {
    "organization admin bypass": (
        lambda config: config["rulesets"][0]["bypass_actors"].append(
            {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"}),
        "bypass actors must be exactly the release-authorization App",
    ),
    "merge App bypass replaces release": (
        lambda config: config["rulesets"][0].update(bypass_actors=[
            {"actor_type": "Integration", "actor_id": 4693283, "bypass_mode": "always"}]),
        "bypass actors must be exactly the release-authorization App",
    ),
    "no bypass": (
        lambda config: config["rulesets"][0].update(bypass_actors=[]),
        "bypass actors must be exactly the release-authorization App",
    ),
    "code-owner review": (
        mutate_parameters(lambda parameters: parameters.update(require_code_owner_review=True)),
        "pull_request parameters must stay neutral",
    ),
    "approving review count": (
        mutate_parameters(lambda parameters: parameters.update(required_approving_review_count=1)),
        "pull_request parameters must stay neutral",
    ),
    "approving review count typed as boolean": (
        mutate_parameters(lambda parameters: parameters.update(required_approving_review_count=False)),
        "pull_request parameters must stay neutral",
    ),
    "team id typed as float": (
        mutate_reviewer(lambda reviewer: reviewer["reviewer"].update(id=17813010.0)),
        "reviewer must be the admins team",
    ),
    "last push approval": (
        mutate_parameters(lambda parameters: parameters.update(require_last_push_approval=True)),
        "pull_request parameters must stay neutral",
    ),
    "stale approvals survive a push": (
        mutate_parameters(lambda parameters: parameters.update(dismiss_stale_reviews_on_push=False)),
        "pull_request parameters must stay neutral",
    ),
    "extra parameter": (
        mutate_parameters(lambda parameters: parameters.update(automatic_copilot_code_review_enabled=False)),
        "pull_request parameters must stay neutral",
    ),
    "another team": (
        mutate_reviewer(lambda reviewer: reviewer["reviewer"].update(id=1)),
        "reviewer must be the admins team",
    ),
    "a user reviewer": (
        mutate_reviewer(lambda reviewer: reviewer["reviewer"].update(type="User")),
        "reviewer must be the admins team",
    ),
    "zero approvals": (
        mutate_reviewer(lambda reviewer: reviewer.update(minimum_approvals=0)),
        "minimum_approvals must be 1",
    ),
    "boolean approvals": (
        mutate_reviewer(lambda reviewer: reviewer.update(minimum_approvals=True)),
        "minimum_approvals must be 1",
    ),
    "no file patterns": (
        mutate_reviewer(lambda reviewer: reviewer.update(file_patterns=[])),
        "file_patterns must be a non-empty list of distinct repository-relative patterns",
    ),
    "anchored CODEOWNERS pattern": (
        mutate_reviewer(lambda reviewer: reviewer.update(file_patterns=["/.github/"])),
        "file_patterns must be a non-empty list of distinct repository-relative patterns",
    ),
    "duplicate pattern": (
        mutate_reviewer(lambda reviewer: reviewer.update(file_patterns=[".github/**", ".github/**"])),
        "file_patterns must be a non-empty list of distinct repository-relative patterns",
    ),
    "second reviewer entry": (
        mutate_parameters(lambda parameters: parameters["required_reviewers"].append(
            copy.deepcopy(parameters["required_reviewers"][0]))),
        "exactly one required reviewer",
    ),
    "second rule": (
        lambda config: config["rulesets"][0]["rules"].append({"type": "deletion"}),
        "exactly one pull_request rule",
    ),
    "name does not name the repository": (
        lambda config: config["rulesets"][0].update(name="verjson-cli-sensitive-path-admin-review"),
        "name must be <repository>-sensitive-path-admin-review",
    ),
    "second repository": (
        lambda config: config["rulesets"][0]["conditions"]["repository_name"]["include"].append("verjson-cli"),
        "conditions must target exactly one protected repository's default branch",
    ),
    "renameable repository": (
        lambda config: config["rulesets"][0]["conditions"]["repository_name"].update(protected=False),
        "conditions must target exactly one protected repository's default branch",
    ),
    "every branch": (
        lambda config: config["rulesets"][0]["conditions"]["ref_name"].update(include=["~ALL"]),
        "conditions must target exactly one protected repository's default branch",
    ),
    "rename protection typed as integer": (
        lambda config: config["rulesets"][0]["conditions"]["repository_name"].update(protected=1),
        "conditions must target exactly one protected repository's default branch",
    ),
    "bypass actor id typed as float": (
        lambda config: config["rulesets"][0]["bypass_actors"][0].update(actor_id=4583107.0),
        "bypass actors must be exactly the release-authorization App",
    ),
    "repository glob": (
        lambda config: config["rulesets"][0].update(
            name="verjson-*-sensitive-path-admin-review",
            conditions={
                "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
                "repository_name": {"include": ["verjson-*"], "exclude": [], "protected": True},
            }),
        "repository must be one literal repository name",
    ),
    "every repository": (
        lambda config: config["rulesets"][0].update(
            name="~ALL-sensitive-path-admin-review",
            conditions={
                "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
                "repository_name": {"include": ["~ALL"], "exclude": [], "protected": True},
            }),
        "repository must be one literal repository name",
    ),
    "two approvals": (
        mutate_reviewer(lambda reviewer: reviewer.update(minimum_approvals=2)),
        "minimum_approvals must be 1",
    ),
    "evaluate only": (
        lambda config: config["rulesets"][0].update(enforcement="evaluate"),
        "must be an active branch ruleset",
    ),
    "unknown ruleset key": (
        lambda config: config["rulesets"][0].update(id=1),
        "ruleset keys drifted",
    ),
    "unknown top-level key": (
        lambda config: config.update(owner_bypass=True),
        "config top-level keys drifted",
    ),
    "acknowledgement for another issue": (
        lambda config: config.update(apply_acknowledgement="APPLY-REQUIRED-REVIEWERS-1"),
        "apply acknowledgement drifted",
    ),
    "no rulesets": (
        lambda config: config.update(rulesets=[]),
        "config must declare at least one ruleset",
    ),
    "duplicate repository": (
        lambda config: config["rulesets"].append(copy.deepcopy(config["rulesets"][0])),
        "duplicate ruleset",
    ),
}


# Every repository whose hand-written CODEOWNERS routed paths to @Verjson/admins and
# whose canonical regeneration drops them (#1816), with the routes it carried.
DROPPED_ADMIN_ROUTES = {
    # main, 2026-10-19: /.github/ /src/pulumi/ /src/steps/ /src/dns/
    "verjson-cli-cloud": [".github/**", "src/pulumi/**", "src/steps/**", "src/dns/**"],
    # main, 2026-10-19: /.github/ /templates/
    "verjson-cli-projects": [".github/**", "templates/**"],
    # 00800b0f (before the #302 regeneration, 3275e8fc): /.github/ /.gitmodules
    "verjson-cli": [".github/**", ".gitmodules"],
    # main, 2026-10-19: /.github/ /scripts/ /docs/decisions/
    "verjson-object-storage": [".github/**", "scripts/**", "docs/decisions/**"],
}


class ConfigTest(unittest.TestCase):
    def test_committed_config_is_admissible(self):
        load_module().read_config()

    def test_committed_config_restores_each_dropped_codeowners_admin_route(self):
        declared = {
            ruleset["conditions"]["repository_name"]["include"][0]:
                ruleset["rules"][0]["parameters"]["required_reviewers"][0]["file_patterns"]
            for ruleset in desired_rulesets()
        }
        self.assertEqual(DROPPED_ADMIN_ROUTES, declared)

    def test_config_admits_only_the_reviewed_ruleset_shape(self):
        module = load_module()
        baseline = json.loads(CONFIG.read_text(encoding="utf-8"))
        for label, (mutate, message) in CONFIG_MUTATIONS.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                config = copy.deepcopy(baseline)
                mutate(config)
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaisesRegex(module.ContractError, message):
                    module.read_config(path)

    def test_every_ruleset_has_the_matching_name_keyed_conformance_contract(self):
        policy = json.loads(
            (ROOT / "config/org-ruleset-conformance-policy.json").read_text(encoding="utf-8"))
        name_keyed = {
            contract["name"]: contract["bypass_actors"]
            for contract in policy["bypass_actor_contracts"]
            if contract["ruleset_id"] is None
        }
        self.assertEqual(
            {ruleset["name"]: ruleset["bypass_actors"] for ruleset in desired_rulesets()},
            name_keyed,
        )

    def test_config_rejects_a_duplicated_json_key(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
            with self.assertRaisesRegex(module.ContractError, "duplicate key schema_version"):
                module.read_config(path)


def live_store(rulesets=None):
    """A live organization holding main-protection plus the given managed rulesets."""
    store = {18098028: {
        "name": "main-protection", "target": "branch", "enforcement": "active",
        "conditions": {}, "bypass_actors": [], "rules": [],
    }}
    for index, ruleset in enumerate(desired_rulesets() if rulesets is None else rulesets):
        store[25000000 + index] = copy.deepcopy(ruleset)
    return store


class CheckTest(unittest.TestCase):
    def test_check_passes_when_every_live_ruleset_equals_the_reviewed_config(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeGitHub(directory, live_store())
            result = fake.run("check")

        self.assertEqual(0, result.returncode, result.stderr)
        for ruleset in desired_rulesets():
            self.assertIn(f"verified: {ruleset['name']}", result.stdout)
        self.assertEqual([], fake.writes())


    def test_check_fails_when_a_reviewed_ruleset_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            result = FakeGitHub(directory, live_store([])).run("check")

        self.assertEqual(1, result.returncode, result.stderr)
        for ruleset in desired_rulesets():
            self.assertIn(f"{ruleset['name']}: absent", result.stderr)


    def test_check_fails_on_drift_in_any_reviewed_field(self):
        def reviewer(ruleset):
            return ruleset["rules"][0]["parameters"]["required_reviewers"][0]

        drifts = {
            "file pattern removed": (
                lambda ruleset: reviewer(ruleset)["file_patterns"].pop(), "rules"),
            "file pattern added": (
                lambda ruleset: reviewer(ruleset)["file_patterns"].append("docs/**"), "rules"),
            "team id": (
                lambda ruleset: reviewer(ruleset)["reviewer"].update(id=1), "rules"),
            "minimum approvals": (
                lambda ruleset: reviewer(ruleset).update(minimum_approvals=2), "rules"),
            "organization admin bypass": (
                lambda ruleset: ruleset["bypass_actors"].append(
                    {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"}),
                "bypass_actors"),
            "merge App bypass": (
                lambda ruleset: ruleset["bypass_actors"].append(
                    {"actor_type": "Integration", "actor_id": 4693283, "bypass_mode": "always"}),
                "bypass_actors"),
            "release bypass removed": (
                lambda ruleset: ruleset.update(bypass_actors=[]), "bypass_actors"),
            "code-owner review": (
                lambda ruleset: ruleset["rules"][0]["parameters"].update(require_code_owner_review=True),
                "rules"),
            "approving review count": (
                lambda ruleset: ruleset["rules"][0]["parameters"].update(required_approving_review_count=1),
                "rules"),
            "stale approvals kept": (
                lambda ruleset: ruleset["rules"][0]["parameters"].update(dismiss_stale_reviews_on_push=False),
                "rules"),
            "boolean typed as integer": (
                lambda ruleset: ruleset["rules"][0]["parameters"].update(require_last_push_approval=0),
                "rules"),
            "evaluate only": (lambda ruleset: ruleset.update(enforcement="evaluate"), "enforcement"),
            "disabled": (lambda ruleset: ruleset.update(enforcement="disabled"), "enforcement"),
            "renameable repository": (
                lambda ruleset: ruleset["conditions"]["repository_name"].update(protected=False),
                "conditions"),
        }
        for label, (drift, field) in drifts.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                live = desired_rulesets()
                drift(live[0])
                result = FakeGitHub(directory, live_store(live)).run("check")

                self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                self.assertIn(
                    f"{live[0]['name']} (25000000): differs from the reviewed config in {field}",
                    result.stderr,
                )
                self.assertNotIn(f"verified: {live[0]['name']}", result.stdout)


    def test_check_fails_closed_when_live_state_cannot_be_read(self):
        failures = {
            "listing": ["GET orgs/Verjson/rulesets?per_page=100"],
            "detail": ["GET orgs/Verjson/rulesets/25000000"],
        }
        for label, fail in failures.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                result = FakeGitHub(directory, live_store(), fail=fail).run("check")

                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertIn("error: GitHub API request failed", result.stderr)
                self.assertNotIn("verified:", result.stdout)


    def test_check_fails_on_a_managed_ruleset_the_config_no_longer_declares(self):
        orphan = copy.deepcopy(desired_rulesets()[0])
        orphan["name"] = "verjson-retired-sensitive-path-admin-review"
        orphan["conditions"]["repository_name"]["include"] = ["verjson-retired"]
        store = live_store()
        store[26000000] = orphan
        with tempfile.TemporaryDirectory() as directory:
            result = FakeGitHub(directory, store).run("check")

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn(
            "verjson-retired-sensitive-path-admin-review (26000000): "
            "not declared in the reviewed config",
            result.stderr,
        )

    def test_check_fails_when_two_live_rulesets_share_a_reviewed_name(self):
        store = live_store()
        store[26000001] = copy.deepcopy(desired_rulesets()[0])
        with tempfile.TemporaryDirectory() as directory:
            result = FakeGitHub(directory, store).run("check")

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn(
            f"{desired_rulesets()[0]['name']}: 2 live rulesets share the name",
            result.stderr,
        )


    def test_check_and_apply_refuse_a_target_that_is_not_a_live_default_main_repository(self):
        # A typo or retired repository would let check stay green while the
        # ruleset enforces nothing (#1859 review).
        repository = desired_rulesets()[0]["conditions"]["repository_name"]["include"][0]
        targets = {
            "missing": None,
            "archived": {"archived": True},
            "default branch is not main": {"default_branch": "master"},
            "another repository answered": {"name": "verjson-other",
                                            "full_name": "verJSON/verjson-other"},
        }
        for label, metadata in targets.items():
            repositories = {
                ruleset["conditions"]["repository_name"]["include"][0]: {}
                for ruleset in desired_rulesets()
            }
            if metadata is None:
                del repositories[repository]
            else:
                repositories[repository] = metadata
            for mode, arguments in (("check", ()), ("apply", ("--ack", ACK))):
                with self.subTest(label=label, mode=mode), \
                        tempfile.TemporaryDirectory() as directory:
                    fake = FakeGitHub(directory, live_store(), repositories=repositories)
                    result = fake.run(mode, *arguments)

                    self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                    self.assertIn("error:", result.stderr)
                    self.assertNotIn(f"verified: {desired_rulesets()[0]['name']}", result.stdout)
                    self.assertEqual([], fake.writes())


class ApplyTest(unittest.TestCase):
    def test_apply_refuses_before_any_api_call_without_the_acknowledgement(self):
        for arguments in ((), ("--ack", ""), ("--ack", "APPLY-REQUIRED-REVIEWERS-1"),
                          ("--ack", "apply-required-reviewers-1816")):
            with self.subTest(arguments), tempfile.TemporaryDirectory() as directory:
                fake = FakeGitHub(directory, live_store([]))
                result = fake.run("apply", *arguments)

                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertIn(f"explicit acknowledgement {ACK} is required", result.stderr)
                self.assertEqual([], fake.calls())


    def test_apply_creates_an_absent_ruleset_disabled_then_activates_it(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeGitHub(directory, live_store([]))
            result = fake.run("apply", "--ack", ACK)
            writes = fake.writes()
            check = fake.run("check")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        expected_writes = []
        for index, ruleset in enumerate(desired_rulesets()):
            staged = dict(ruleset, enforcement="disabled")
            expected_writes += [
                ["POST", "orgs/Verjson/rulesets", staged],
                ["PUT", f"orgs/Verjson/rulesets/{900 + index}", ruleset],
            ]
            self.assertIn(f"created: {ruleset['name']} ({900 + index})", result.stdout)
        self.assertEqual(expected_writes, writes)
        self.assertEqual(0, check.returncode, check.stdout + check.stderr)


    def test_apply_updates_a_drifted_ruleset_in_place(self):
        live = desired_rulesets()
        live[0]["bypass_actors"].append(
            {"actor_type": "OrganizationAdmin", "actor_id": None, "bypass_mode": "always"})
        live[0]["rules"][0]["parameters"]["required_reviewers"][0]["file_patterns"] = [".github/**"]
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeGitHub(directory, live_store(live))
            result = fake.run("apply", "--ack", ACK)
            writes = fake.writes()
            stored = fake.rulesets()

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        expected = desired_rulesets()[0]
        self.assertEqual([["PUT", "orgs/Verjson/rulesets/25000000", expected]], writes)
        self.assertEqual(expected, stored["25000000"])
        self.assertIn(f"updated: {expected['name']} (25000000)", result.stdout)


    def test_apply_is_idempotent_and_writes_nothing_once_converged(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeGitHub(directory, live_store([]))
            first = fake.run("apply", "--ack", ACK)
            converged = len(fake.writes())
            second = fake.run("apply", "--ack", ACK)
            writes_after_second = fake.writes()[converged:]

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual([], writes_after_second)
        for index, ruleset in enumerate(desired_rulesets()):
            self.assertIn(f"unchanged: {ruleset['name']} ({900 + index})", second.stdout)


    def test_apply_restores_disabled_when_the_activated_image_reads_back_differently(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeGitHub(directory, live_store([]), normalize_active_away_patterns=True)
            result = fake.run("apply", "--ack", ACK)
            writes = fake.writes()
            stored = fake.rulesets()

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        expected = desired_rulesets()[0]
        staged = dict(expected, enforcement="disabled")
        self.assertEqual([
            ["POST", "orgs/Verjson/rulesets", staged],
            ["PUT", "orgs/Verjson/rulesets/900", expected],
            ["PUT", "orgs/Verjson/rulesets/900", staged],
        ], writes)
        self.assertEqual(staged, stored["900"])
        self.assertIn("restored and verified disabled", result.stderr)


    def test_apply_activates_a_disabled_leftover_through_the_verified_rollback_path(self):
        # A failed create leaves its ruleset disabled; the next apply must not
        # activate it unverified (#1859 review).
        expected = desired_rulesets()[0]
        staged = dict(copy.deepcopy(expected), enforcement="disabled")
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeGitHub(directory, live_store([staged]), normalize_active_away_patterns=True)
            result = fake.run("apply", "--ack", ACK)
            writes = fake.writes()
            stored = fake.rulesets()

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual([
            ["PUT", "orgs/Verjson/rulesets/25000000", staged],
            ["PUT", "orgs/Verjson/rulesets/25000000", expected],
            ["PUT", "orgs/Verjson/rulesets/25000000", staged],
        ], writes)
        self.assertEqual(staged, stored["25000000"])
        self.assertIn("restored and verified disabled", result.stderr)

    def test_apply_activates_a_disabled_leftover_once_it_reads_back_exactly(self):
        expected = desired_rulesets()[0]
        staged = dict(copy.deepcopy(expected), enforcement="disabled")
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeGitHub(directory, live_store([staged]))
            result = fake.run("apply", "--ack", ACK)
            writes = fake.writes()[:2]
            stored = fake.rulesets()

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([
            ["PUT", "orgs/Verjson/rulesets/25000000", staged],
            ["PUT", "orgs/Verjson/rulesets/25000000", expected],
        ], writes)
        self.assertEqual(expected, stored["25000000"])
        self.assertIn(f"activated: {expected['name']} (25000000)", result.stdout)


class FailClosedTest(unittest.TestCase):
    """Every unanswerable question about live state is exit 2, never a verdict."""

    def run_with_listing(self, mode, listing, *arguments):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeGitHub(directory, live_store())
            gh = Path(directory) / "gh"
            gh.write_text(gh.read_text(encoding="utf-8").replace(
                'print(json.dumps([[{"id": int(key), "name": value["name"]}\n'
                '                       for key, value in state["rulesets"].items()]]))',
                f"print({json.dumps(json.dumps(listing))})",
            ), encoding="utf-8")
            return fake.run(mode, *arguments), fake.writes()

    def test_malformed_listing_fails_closed(self):
        name = desired_rulesets()[0]["name"]
        listings = {
            "no pages": [],
            "page is not a list": [{"id": 1}],
            "empty page": [[]],
            "entry is not an object": [["main-protection"]],
            "named entry without id": [[{"name": name}]],
            "named entry with string id": [[{"id": "25000000", "name": name}]],
            "named entry with boolean id": [[{"id": True, "name": name}]],
            "entry without name": [[{"id": 25000000}]],
        }
        for label, listing in listings.items():
            for mode, arguments in (("check", ()), ("apply", ("--ack", ACK))):
                with self.subTest(label=label, mode=mode):
                    result, writes = self.run_with_listing(mode, listing, *arguments)

                    self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                    self.assertIn("error:", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual([], writes)

    def test_live_detail_that_is_not_the_requested_org_ruleset_fails_closed(self):
        overrides = {
            "repository ruleset": {"source_type": "Repository"},
            "another organization": {"source": "Attacker"},
            "different id": {"id": 1},
        }
        for label, override in overrides.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                fake = FakeGitHub(directory, live_store())
                gh = Path(directory) / "gh"
                gh.write_text(gh.read_text(encoding="utf-8").replace(
                    "    print(json.dumps(detail(int(key))))\nelif method == \"POST\"",
                    f"    print(json.dumps(dict(detail(int(key)), **{override!r})))"
                    "\nelif method == \"POST\"",
                ), encoding="utf-8")
                result = fake.run("check")

                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertNotIn("verified:", result.stdout)

    def test_live_detail_missing_a_mutable_field_fails_closed(self):
        live = desired_rulesets()
        del live[0]["rules"]
        with tempfile.TemporaryDirectory() as directory:
            result = FakeGitHub(directory, live_store(live)).run("check")

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("live ruleset is missing required fields", result.stderr)

    def test_apply_plans_every_ruleset_before_writing_any(self):
        live = desired_rulesets()[1:]
        store = live_store(live)
        store[26000002] = copy.deepcopy(live[0])
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeGitHub(directory, store)
            result = fake.run("apply", "--ack", ACK)
            writes = fake.writes()

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn(f"{live[0]['name']}: 2 live rulesets share the name", result.stderr)
        self.assertEqual([], writes)

    def test_apply_stops_when_the_create_request_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeGitHub(directory, live_store([]), fail=["POST orgs/Verjson/rulesets"])
            result = fake.run("apply", "--ack", ACK)
            writes = fake.writes()
            stored = fake.rulesets()

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["POST"], [write[0] for write in writes])
        self.assertEqual(["18098028"], list(stored))

    def test_apply_fails_when_an_update_reads_back_differently(self):
        # An already-active drifted ruleset is rewritten in place (no rollback);
        # a non-active one takes the verified activation path tested above.
        live = desired_rulesets()
        live[0]["rules"][0]["parameters"]["required_reviewers"][0]["file_patterns"].pop()
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeGitHub(directory, live_store(live), normalize_active_away_patterns=True)
            result = fake.run("apply", "--ack", ACK)

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("updated ruleset reads back differently (rules)", result.stderr)
        self.assertNotIn(f"updated: {live[0]['name']}", result.stdout)


if __name__ == "__main__":
    unittest.main()
