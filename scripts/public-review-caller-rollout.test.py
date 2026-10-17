#!/usr/bin/env python3
"""Behavioral tests for scripts/public-review-caller-rollout.py.

The rollout gate for ADR 0235 decision 7: a public adopter keeps sending
public pull-request heads to the persistent pool until its generated
`ai-review-merge.yml` caller pins a canonical commit that contains the fix.
Each case runs the real script against a fake `gh` that serves canned API
responses, and asserts the exit status and the line that explains it. Every
unreadable or unexpected state must fail closed (exit 2), never pass.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

SCRIPT = Path(__file__).resolve().parent / "public-review-caller-rollout.py"
MINIMUM = "a" * 40
CURRENT = "b" * 40
OLD = "c" * 40
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
responses = json.loads(os.environ["FAKE_GH_RESPONSES"])
path = sys.argv[-1]
if path not in responses:
    sys.stderr.write(f"gh: unexpected path {path} (HTTP 500)\\n")
    sys.exit(1)
answer = responses[path]
if answer.get("status", 200) != 200:
    sys.stderr.write(f"gh: {answer.get('message', 'error')} (HTTP {answer['status']})\\n")
    sys.exit(1)
body = answer["body"]
sys.stdout.write(body if isinstance(body, str) else json.dumps(body))
"""


def repository(name: str, branch: str = "main", archived: bool = False, visibility: str = "public") -> dict:
    return {"name": name, "default_branch": branch, "archived": archived, "visibility": visibility}


def caller(pin: str, extra_job: str = "") -> str:
    return (
        "name: AI review\non: workflow_dispatch\njobs:\n"
        f"  review:\n    uses: Verjson/.github/.github/workflows/ai-review-merge.yml@{pin}\n    secrets: inherit\n"
        + extra_job
    )


def contents(text: str) -> dict:
    return {"body": {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}}


def compare(pin: str, status: str) -> tuple[str, dict]:
    return f"repos/Verjson/.github/compare/{MINIMUM}...{pin}", {"body": {"status": status}}


REPOS_PATH = "orgs/Verjson/repos?type=public&per_page=100"


def listing(*records: dict) -> dict[str, dict]:
    return {REPOS_PATH: {"body": [list(records)]}}


def caller_path(name: str, branch: str = "main", suffix: str = "yml") -> str:
    return f"repos/Verjson/{name}/contents/.github/workflows/ai-review-merge.{suffix}?ref={quote(branch)}"


def no_yaml(name: str, branch: str = "main") -> dict[str, dict]:
    return {caller_path(name, branch, "yaml"): {"status": 404, "message": "Not Found"}}


def no_caller(name: str, branch: str = "main") -> dict[str, dict]:
    """A repository that exists and has neither caller spelling."""
    return {
        caller_path(name, branch): {"status": 404, "message": "Not Found"},
        caller_path(name, branch, "yaml"): {"status": 404, "message": "Not Found"},
        f"repos/Verjson/{name}": {"body": {"name": name}},
    }


def run(responses: dict, *arguments: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as directory:
        gh = Path(directory) / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *(arguments or ("--minimum", MINIMUM))],
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{directory}:{os.environ['PATH']}", "FAKE_GH_RESPONSES": json.dumps(responses)},
        )
    return completed.returncode, completed.stdout + completed.stderr


class PublicReviewCallerRolloutTests(unittest.TestCase):
    def assertOutcome(self, responses: dict, code: int, *fragments: str, arguments: tuple = ()) -> None:
        actual, output = run(responses, *arguments)
        self.assertEqual(actual, code, output)
        for fragment in fragments:
            self.assertIn(fragment, output)

    def test_current_caller_and_repositories_without_one_pass(self):
        self.assertOutcome(
            {
                **listing(repository(".github"), repository("verjson-git-runners"), repository("gguf-demo"),
                          repository("repo-dashboard", "master")),
                caller_path("verjson-git-runners"): contents(caller(CURRENT)),
                caller_path("verjson-git-runners", "main", "yaml"): {"status": 404},
                **no_caller("gguf-demo"),
                **no_caller("repo-dashboard", "master"),
                **dict([compare(CURRENT, "ahead")]),
            },
            0,
            f"CURRENT: Verjson/verjson-git-runners pins {CURRENT}",
            "NO CALLER: Verjson/gguf-demo@main",
            "NO CALLER: Verjson/repo-dashboard@master",
            "CANONICAL: Verjson/.github",
            "clean:",
        )

    def test_identical_pin_contains_the_minimum(self):
        self.assertOutcome(
            {**listing(repository("r")), caller_path("r"): contents(caller(MINIMUM)), **no_yaml("r"),
             **dict([compare(MINIMUM, "identical")])},
            0,
            f"CURRENT: Verjson/r pins {MINIMUM}",
        )

    def test_caller_predating_the_fix_blocks_rollout(self):
        for status in ("behind", "diverged"):
            with self.subTest(status=status):
                self.assertOutcome(
                    {**listing(repository("verjson-git-runners")),
                     caller_path("verjson-git-runners"): contents(caller(OLD)), **no_yaml("verjson-git-runners"),
                     **dict([compare(OLD, status)])},
                    1,
                    f"STALE: Verjson/verjson-git-runners pins {OLD}, which does not contain {MINIMUM}",
                    "scripts/gen-ai-review-caller.sh",
                )

    def test_default_branch_is_percent_encoded_in_the_query(self):
        # A default branch is repository-controlled text; `&` or `#` would
        # otherwise end the ref and re-address the request.
        branch = "release/a&ref=evil#x"
        self.assertNotIn("&", caller_path("r", branch).split("?ref=")[1])
        self.assertOutcome(
            {**listing(repository("r", branch)), caller_path("r", branch): contents(caller(CURRENT)),
             **no_yaml("r", branch),
             **dict([compare(CURRENT, "ahead")])},
            0,
            f"CURRENT: Verjson/r pins {CURRENT}",
        )

    def test_a_caller_spelled_yaml_is_checked(self):
        # GitHub runs `.yaml` workflows too; a stale one must not read as absent.
        self.assertOutcome(
            {**listing(repository("r")), caller_path("r"): {"status": 404},
             caller_path("r", suffix="yaml"): contents(caller(OLD)), **dict([compare(OLD, "behind")])},
            1,
            f"STALE: Verjson/r pins {OLD}",
        )

    def test_both_spellings_are_checked(self):
        self.assertOutcome(
            {**listing(repository("r")), caller_path("r"): contents(caller(CURRENT)),
             caller_path("r", suffix="yaml"): contents(caller(OLD)),
             **dict([compare(CURRENT, "ahead"), compare(OLD, "diverged")])},
            1,
            f"STALE: Verjson/r pins {OLD}",
        )

    def test_a_repository_that_vanishes_mid_run_is_undetermined(self):
        self.assertOutcome(
            {**listing(repository("r")), caller_path("r"): {"status": 404},
             caller_path("r", suffix="yaml"): {"status": 404}, "repos/Verjson/r": {"status": 404}},
            2,
            "UNDETERMINED",
        )

    def test_archived_public_repository_is_skipped(self):
        self.assertOutcome(
            {**listing(repository("old", archived=True), repository("r")), **no_caller("r")},
            0,
            "NO CALLER: Verjson/r@main",
        )

    def test_unreadable_or_unexpected_state_fails_closed(self):
        # Every request a healthy run makes is served (the .yaml spelling is
        # absent, the repository exists), so each case fails on the fault it
        # names rather than on an unserved request.
        good = {**listing(repository("r")), caller_path("r"): contents(caller(CURRENT)), **no_yaml("r"),
                "repos/Verjson/r": {"body": {"name": "r"}}, **dict([compare(CURRENT, "ahead")])}
        cases = {
            "listing API failure": {**good, REPOS_PATH: {"status": 502}},
            "listing not paged arrays": {**good, REPOS_PATH: {"body": {"message": "x"}}},
            "listing returns no public repository": {**good, REPOS_PATH: {"body": [[]]}},
            "record without default branch": {**good, REPOS_PATH: {"body": [[{"name": "r", "archived": False,
                                                                                "visibility": "public"}]]}},
            "record reports private": {**good, **listing(repository("r", visibility="private"))},
            "caller API failure other than 404": {**good, caller_path("r"): {"status": 403}},
            "caller not base64": {**good, caller_path("r"): {"body": {"encoding": "none", "content": ""}}},
            "caller does not parse": {**good, caller_path("r"): contents("jobs: [unclosed")},
            "caller has no jobs": {**good, caller_path("r"): contents("name: x\non: push\n")},
            # The compare would say "ahead", so only the pin rule can refuse these.
            "caller pins a branch": {**good, caller_path("r"): contents(caller("main")),
                                     **dict([compare("main", "ahead")])},
            "caller pins a short SHA": {**good, caller_path("r"): contents(caller(CURRENT[:12])),
                                        **dict([compare(CURRENT[:12], "ahead")])},
            "caller adds a job that is not the canonical call": {
                **good, caller_path("r"): contents(caller(CURRENT, "  other:\n    runs-on: [self-hosted]\n"))},
            "caller pins two commits": {
                **good, caller_path("r"): contents(caller(CURRENT, "  again:\n    uses: "
                                                          f"Verjson/.github/.github/workflows/ai-review-merge.yml@{OLD}\n")),
                **dict([compare(OLD, "ahead")])},
            "caller calls another repository": {
                **good, caller_path("r"): contents(caller(CURRENT).replace("Verjson/.github/", "Evil/.github/"))},
            "compare API failure": {**good, compare(CURRENT, "ahead")[0]: {"status": 500}},
            "compare cannot find the commit": {**good, compare(CURRENT, "ahead")[0]: {"status": 404}},
            "compare reports an unknown status": {**good, **dict([compare(CURRENT, "unknown")])},
            "compare response is not JSON": {**good, compare(CURRENT, "ahead")[0]: {"body": "not json"}},
        }
        for label, responses in cases.items():
            with self.subTest(label):
                self.assertOutcome(responses, 2, "UNDETERMINED")

    def test_each_fault_is_reported_by_the_guard_that_owns_it(self):
        # A later guard (the compare operands, an unserved request) also fails
        # closed, so these assert the message of the guard under test.
        base = {**listing(repository("r")), **no_yaml("r"), "repos/Verjson/r": {"body": {"name": "r"}}}
        for label, responses, message in (
            ("a 403 on the caller", {**base, caller_path("r"): {"status": 403}}, "GitHub API read failed"),
            ("a branch pin", {**base, caller_path("r"): contents(caller("main")), **dict([compare("main", "ahead")])},
             "not a full commit SHA"),
            ("a short pin", {**base, caller_path("r"): contents(caller(CURRENT[:12])),
                             **dict([compare(CURRENT[:12], "ahead")])}, "not a full commit SHA"),
        ):
            with self.subTest(label):
                self.assertOutcome(responses, 2, "UNDETERMINED", message)

    def test_minimum_must_be_a_full_commit_sha(self):
        for minimum in ("main", MINIMUM[:12], MINIMUM.upper()):
            with self.subTest(minimum=minimum):
                self.assertOutcome({}, 2, "UNDETERMINED", arguments=("--minimum", minimum))


if __name__ == "__main__":
    unittest.main()
