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


if __name__ == "__main__":
    unittest.main()
