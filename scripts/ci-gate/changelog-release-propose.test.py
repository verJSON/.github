#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "release-propose.py"
SPEC = importlib.util.spec_from_file_location("release_propose", MODULE_PATH)
assert SPEC and SPEC.loader
release_propose = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_propose)


class FakeClient:
    def __init__(self, responses: dict[str, list[list[object]]]):
        self.responses = responses
        self.mutations: list[tuple[str, str, dict[str, object]]] = []

    def pages(self, path: str) -> list[object]:
        queued = self.responses.get(path)
        if not queued:
            raise AssertionError(f"unexpected or exhausted API read: {path}")
        return queued.pop(0)

    def mutate(self, method: str, path: str, payload: dict[str, object]) -> None:
        self.mutations.append((method, path, payload))


class ReleaseProposalEffectsTests(unittest.TestCase):
    repository = "Verjson/example"
    branch = "main"
    version = "v1.2.3"
    prefix = "v"
    selector = "b" * 64
    head = "a" * 40
    issue_path = "repos/Verjson/example/issues?state=open&per_page=100"
    runs_path = (
        "repos/Verjson/example/actions/workflows/release.yml/runs"
        "?event=workflow_dispatch&per_page=100"
    )

    def proposal(self, number: int, preview: str = "Preview.\n") -> dict[str, object]:
        return {
            "number": number,
            "title": f"Release proposal: {self.version}",
            "user": {"login": "github-actions[bot]"},
            "body": release_propose.proposal_body(
                self.version,
                self.prefix,
                self.selector,
                self.branch,
                self.head,
                preview,
            ),
        }

    def test_propose_scans_every_page_and_leaves_a_current_surface_unchanged(self) -> None:
        client = FakeClient(
            {
                self.issue_path: [
                    [
                        [{"number": 1, "title": "unrelated", "body": "ordinary"}],
                        [self.proposal(42)],
                    ]
                ]
            }
        )

        outcome = release_propose.ensure_proposal(
            client,
            self.repository,
            self.version,
            self.prefix,
            self.selector,
            self.branch,
            self.head,
            "Preview.\n",
        )

        self.assertEqual("release proposal #42 is already current", outcome)
        self.assertEqual([], client.mutations)

    def test_propose_updates_the_owned_surface_instead_of_duplicating_it(self) -> None:
        client = FakeClient({self.issue_path: [[[self.proposal(42, "Old.")]]]})

        outcome = release_propose.ensure_proposal(
            client,
            self.repository,
            self.version,
            self.prefix,
            self.selector,
            self.branch,
            self.head,
            "New.\n",
        )

        self.assertEqual("updated release proposal #42", outcome)
        self.assertEqual(1, len(client.mutations))
        method, path, payload = client.mutations[0]
        self.assertEqual(("PATCH", "repos/Verjson/example/issues/42"), (method, path))
        self.assertEqual("Release proposal: v1.2.3", payload["title"])
        self.assertIn("New.", payload["body"])

    def test_propose_creation_is_mocked_and_uses_one_durable_marker(self) -> None:
        client = FakeClient({self.issue_path: [[[]]]})

        outcome = release_propose.ensure_proposal(
            client,
            self.repository,
            self.version,
            self.prefix,
            self.selector,
            self.branch,
            self.head,
            "Preview.\n",
        )

        self.assertEqual("created release proposal", outcome)
        self.assertEqual(
            ("POST", "repos/Verjson/example/issues"),
            client.mutations[0][:2],
        )
        self.assertEqual(
            1,
            str(client.mutations[0][2]["body"]).count(
                release_propose.PROPOSAL_MARKER
            ),
        )

    def test_propose_fails_closed_when_two_surfaces_claim_ownership(self) -> None:
        client = FakeClient(
            {self.issue_path: [[[self.proposal(7)], [self.proposal(8)]]]}
        )

        with self.assertRaisesRegex(
            release_propose.ProposalError, "multiple open release proposal"
        ):
            release_propose.ensure_proposal(
                client,
                self.repository,
                self.version,
                self.prefix,
                self.selector,
                self.branch,
                self.head,
                "Preview.\n",
            )

        self.assertEqual([], client.mutations)

    def test_propose_does_not_adopt_a_foreign_issue_that_copied_the_marker(self) -> None:
        foreign = self.proposal(9)
        foreign["user"] = {"login": "maintainer"}
        client = FakeClient({self.issue_path: [[[foreign]]]})

        with self.assertRaisesRegex(
            release_propose.ProposalError, "not owned by github-actions"
        ):
            release_propose.ensure_proposal(
                client,
                self.repository,
                self.version,
                self.prefix,
                self.selector,
                self.branch,
                self.head,
                "Preview.\n",
            )

        self.assertEqual([], client.mutations)

    def run_record(
        self,
        run_id: int = 91,
        selector: str | None = None,
        head: str | None = None,
    ) -> dict[str, object]:
        return {
            "id": run_id,
            "event": "workflow_dispatch",
            "display_title": f"Release {self.version} {selector or self.selector}",
            "head_sha": head or self.head,
        }

    def test_dispatch_scans_every_page_and_does_not_duplicate_an_exact_run(self) -> None:
        client = FakeClient(
            {
                self.runs_path: [
                    [
                        {"workflow_runs": [{"id": 1, "event": "schedule"}]},
                        {"workflow_runs": [self.run_record()]},
                    ]
                ]
            }
        )

        outcome = release_propose.ensure_dispatch(
            client,
            self.repository,
            "release.yml",
            self.branch,
            self.head,
            self.version,
            self.prefix,
            self.selector,
            "",
            "",
            1,
            0,
        )

        self.assertEqual("release dispatch already exists as run 91", outcome)
        self.assertEqual([], client.mutations)

    def test_dispatch_does_not_let_subset_a_suppress_subset_b(self) -> None:
        selector_a = "c" * 64
        client = FakeClient(
            {
                self.runs_path: [
                    [{"workflow_runs": [self.run_record(91, selector_a)]}],
                    [{"workflow_runs": [self.run_record(99)]}],
                ]
            }
        )

        outcome = release_propose.ensure_dispatch(
            client,
            self.repository,
            "release.yml",
            self.branch,
            self.head,
            self.version,
            self.prefix,
            self.selector,
            "NEXT/subset-b.md",
            "",
            1,
            0,
        )

        self.assertEqual("dispatched release as run 99", outcome)
        self.assertEqual(1, len(client.mutations))

    def test_dispatch_race_cannot_acknowledge_a_newer_default_branch_head(self) -> None:
        client = FakeClient(
            {
                self.runs_path: [
                    [{"workflow_runs": []}],
                    [{"workflow_runs": [self.run_record(99, head="d" * 40)]}],
                ]
            }
        )

        with self.assertRaisesRegex(
            release_propose.ProposalError, "no exact-version, exact-head run"
        ):
            release_propose.ensure_dispatch(
                client,
                self.repository,
                "release.yml",
                self.branch,
                self.head,
                self.version,
                self.prefix,
                self.selector,
                "",
                "",
                1,
                0,
            )

        self.assertEqual(self.head, client.mutations[0][2]["inputs"]["expected_head"])

    def test_dispatch_posts_only_the_exact_derived_inputs_and_waits_for_receipt(self) -> None:
        client = FakeClient(
            {
                self.runs_path: [
                    [{"workflow_runs": []}],
                    [{"workflow_runs": [self.run_record(99)]}],
                ]
            }
        )

        outcome = release_propose.ensure_dispatch(
            client,
            self.repository,
            "release.yml",
            self.branch,
            self.head,
            self.version,
            self.prefix,
            self.selector,
            "NEXT/one.md\nNEXT/two.md",
            "python",
            1,
            0,
        )

        self.assertEqual("dispatched release as run 99", outcome)
        self.assertEqual(
            [
                (
                    "POST",
                    "repos/Verjson/example/actions/workflows/release.yml/dispatches",
                    {
                        "ref": "main",
                        "inputs": {
                            "version": "v1.2.3",
                            "prefix": "v",
                            "expected_head": "a" * 40,
                            "selector_digest": "b" * 64,
                            "fragments": "NEXT/one.md\nNEXT/two.md",
                            "component": "python",
                        },
                    },
                )
            ],
            client.mutations,
        )

    def test_dispatch_fails_when_the_api_never_acknowledges_the_exact_run(self) -> None:
        client = FakeClient(
            {
                self.runs_path: [
                    [{"workflow_runs": []}],
                    [{"workflow_runs": []}],
                ]
            }
        )

        with self.assertRaisesRegex(
            release_propose.ProposalError, "no exact-version, exact-head run"
        ):
            release_propose.ensure_dispatch(
                client,
                self.repository,
                "release.yml",
                self.branch,
                self.head,
                self.version,
                self.prefix,
                self.selector,
                "",
                "",
                1,
                0,
            )

    def test_github_failures_are_typed_without_echoing_response_content(self) -> None:
        completed = subprocess.CompletedProcess(
            ["gh"], returncode=1, stdout="sensitive stdout", stderr="sensitive stderr"
        )
        with mock.patch.object(subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                release_propose.ProposalError, "GitHub API request failed"
            ) as captured:
                release_propose.GitHubClient().pages(self.issue_path)

        self.assertNotIn("sensitive", str(captured.exception))

    def test_malformed_pagination_fails_closed(self) -> None:
        client = FakeClient({self.runs_path: [[[]]]})

        with self.assertRaisesRegex(
            release_propose.ProposalError, "has no workflow_runs array"
        ):
            release_propose.workflow_runs(client, self.repository, "release.yml")

    def test_default_branch_must_still_match_the_derived_head(self) -> None:
        ref_path = "repos/Verjson/example/git/ref/heads/release%2Fstable"
        client = FakeClient(
            {ref_path: [[{"object": {"sha": "b" * 40}}]]}
        )

        with self.assertRaisesRegex(
            release_propose.ProposalError, "default branch advanced"
        ):
            release_propose.require_current_head(
                client, self.repository, "release/stable", self.head
            )


class GeneratedCallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sha = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def generate(self, mode: str) -> tuple[str, dict[str, object]]:
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "gen-changelog-caller.sh"),
                "release-propose",
                self.sha,
                "--autonomy",
                mode,
            ],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        return result.stdout, yaml.safe_load(result.stdout)

    def test_autonomy_is_fixed_in_source_with_mode_specific_permissions(self) -> None:
        for mode, permission, workflow in (
            ("propose", "issues", "release-propose.yml"),
            ("dispatch", "actions", "release-dispatch.yml"),
        ):
            with self.subTest(mode=mode):
                raw, document = self.generate(mode)
                triggers = document.get("on", document.get(True))
                self.assertEqual({"schedule", "workflow_dispatch"}, set(triggers))
                self.assertNotIn("autonomy", triggers["workflow_dispatch"]["inputs"])
                job = document["jobs"]["release-propose"]
                self.assertEqual(
                    f"Verjson/.github/.github/workflows/{workflow}@{self.sha}",
                    job["uses"],
                )
                self.assertEqual(self.sha, job["with"]["contract_ref"])
                self.assertNotIn("autonomy", job["with"])
                self.assertEqual({"contents", permission}, set(job["permissions"]))
                reusable = yaml.safe_load(
                    (ROOT / ".github" / "workflows" / workflow).read_text(
                        encoding="utf-8"
                    )
                )
                called_job = next(iter(reusable["jobs"].values()))
                self.assertEqual(job["permissions"], called_job["permissions"])
                self.assertNotIn("issues: write", raw if mode == "dispatch" else "")
                self.assertNotIn("actions: write", raw if mode == "propose" else "")

    def test_generator_refuses_implicit_or_invalid_autonomy(self) -> None:
        generator = str(ROOT / "scripts" / "gen-changelog-caller.sh")
        for arguments in ([], ["--autonomy", "publish"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    ["bash", generator, "release-propose", self.sha, *arguments],
                    cwd=ROOT,
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(2, result.returncode)

    def test_reusable_workflows_have_exact_non_widenable_authority(self) -> None:
        workflows = {}
        for mode, permission, other_permission in (
            ("propose", "issues", "actions"),
            ("dispatch", "actions", "issues"),
        ):
            path = ROOT / ".github" / "workflows" / f"release-{mode}.yml"
            raw = path.read_text(encoding="utf-8")
            document = yaml.safe_load(raw)
            triggers = document.get("on", document.get(True))
            self.assertEqual({"workflow_call"}, set(triggers))
            self.assertNotIn("autonomy", triggers["workflow_call"]["inputs"])
            job = document["jobs"][f"release-{mode}"]
            self.assertEqual(
                {"contents": "read", permission: "write"}, job["permissions"]
            )
            self.assertNotIn(f"{other_permission}: write", raw)
            self.assertEqual(
                {
                    "group": "release-propose-${{ github.repository }}",
                    "cancel-in-progress": False,
                },
                document["concurrency"],
            )
            self.assertIn(f"--mode {mode}", raw)
            self.assertIn("next-version", raw)
            self.assertIn("selection-digest", raw)
            self.assertIn('echo "selected=false"', raw)
            self.assertIn("steps.release.outputs.selected == 'true'", raw)
            self.assertIn("render-next", raw)
            self.assertIn("--as-released", raw)
            self.assertIn("scripts/release-propose.py", raw)
            self.assertNotIn("changelog.py release", raw)
            self.assertNotIn("git push", raw)
            self.assertNotIn("git tag", raw)
            workflows[mode] = job

        proposal_steps = workflows["propose"]["steps"]
        dispatch_steps = workflows["dispatch"]["steps"]
        self.assertEqual(proposal_steps[1:4], dispatch_steps[1:4])
        self.assertEqual(
            set(proposal_steps[-1]["env"]), set(dispatch_steps[-1]["env"])
        )

    def test_generated_release_run_title_is_the_dispatch_idempotency_key(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "gen-changelog-caller.sh"),
                "release-node",
                self.sha,
            ],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        document = yaml.safe_load(result.stdout)

        self.assertEqual(
            "Release ${{ inputs.version }} ${{ inputs.selector_digest || 'manual' }}",
            document["run-name"],
        )
        inputs = document.get("on", document.get(True))["workflow_dispatch"]["inputs"]
        self.assertEqual("v", inputs["prefix"]["default"])
        self.assertEqual("", inputs["expected_head"]["default"])
        self.assertEqual("", inputs["selector_digest"]["default"])


if __name__ == "__main__":
    unittest.main()
