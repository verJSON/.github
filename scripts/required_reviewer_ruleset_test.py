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
if method == "GET" and path == f"{prefix}?per_page=100" and paginate:
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
        "minimum_approvals must be a positive integer",
    ),
    "boolean approvals": (
        mutate_reviewer(lambda reviewer: reviewer.update(minimum_approvals=True)),
        "minimum_approvals must be a positive integer",
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


class ConfigTest(unittest.TestCase):
    def test_committed_config_is_admissible(self):
        load_module().read_config()

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


if __name__ == "__main__":
    unittest.main()
