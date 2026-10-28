#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import json
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).with_name("gate_coverage_audit.py")
SPEC = importlib.util.spec_from_file_location("gate_coverage_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


SHA = "0123456789abcdef0123456789abcdef01234567"


def pr_node(repo, number, *, draft=False, hold=False, fork=False, contexts=()):
    nodes = [
        {"__typename": "CheckRun", "name": context}
        for context in contexts
    ]
    return {
        "number": number,
        "isDraft": draft,
        "headRefOid": SHA,
        "baseRefName": "main",
        "isCrossRepository": fork,
        "repository": {"nameWithOwner": repo},
        "labels": {
            "totalCount": 1 if hold else 0,
            "nodes": [{"name": "hold"}] if hold else [],
        },
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "statusCheckRollup": {
                            "contexts": {"totalCount": len(nodes), "nodes": nodes}
                        }
                    }
                }
            ]
        },
    }


def page(nodes, *, next_page=False, cursor=None):
    return {
        "data": {
            "search": {
                "nodes": nodes,
                "pageInfo": {"hasNextPage": next_page, "endCursor": cursor},
            }
        }
    }


class FakeClient:
    def __init__(self, pages, workflows=None, rules=None, failure=None):
        self.pages = list(pages)
        self.workflows = workflows or {}
        self.rules = rules or {}
        self.failure = failure
        self.commands = []

    def json(self, args):
        self.commands.append(args)
        if self.failure:
            raise self.failure
        if args[:2] == ["api", "graphql"]:
            return self.pages.pop(0)
        repo = args[1].split("/actions/workflows/")[0].removeprefix("repos/")
        workflow_path = args[1].split("/actions/workflows/", 1)[1]
        state = self.workflows.get((repo, workflow_path), self.workflows.get(repo, "active"))
        if state == "missing":
            raise MODULE.AuditError("github_unavailable", "missing", 404)
        return {"state": state}

    def run(self, args):
        self.commands.append(args)
        if args[:2] == ["api", "--paginate"] and "/rules/branches/" in args[2]:
            repo = args[2].split("/rules/branches/")[0].removeprefix("repos/")
            rules = self.rules.get(repo, [])
            return "".join(json.dumps(rule) + "\n" for rule in rules)
        return ""


class GateCoverageAuditTest(unittest.TestCase):
    def capture_main(self, client, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = MODULE.main(list(args), client)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_dispatch_lookup_uses_target_repo_but_required_rule_keeps_legacy_identity(self):
        for repo, workflow_path in (
            ("Verjson/.github", "ai-review-merge-dispatch.yml"),
            ("Verjson/app", "ai-review-merge.yml"),
            ("Other/.github", "ai-review-merge.yml"),
        ):
            with self.subTest(repo=repo):
                client = FakeClient([], workflows={(repo, workflow_path): "active"})

                self.assertEqual(MODULE.workflow_state(client, repo, "main"), "active")
                self.assertEqual(
                    client.commands[0][1],
                    f"repos/{repo}/actions/workflows/{workflow_path}",
                )

        repo = "Verjson/.github"
        client = FakeClient(
            [],
            workflows={(repo, "ai-review-merge-dispatch.yml"): "missing"},
            rules={
                repo: [
                    {
                        "type": "workflows",
                        "parameters": {
                            "workflows": [
                                {"path": ".github/workflows/ai-review-merge.yml"}
                            ]
                        },
                    }
                ]
            },
        )

        self.assertEqual(MODULE.workflow_state(client, repo, "main"), "required")
        self.assertIn(
            "repos/Verjson/.github/actions/workflows/ai-review-merge-dispatch.yml",
            client.commands[0][1],
        )

    def test_classifies_exact_targets_and_supported_retriggers(self):
        nodes = [
            pr_node("Verjson/app", 1),
            pr_node("Verjson/app", 2, contexts=("classify", "ai-review")),
            pr_node("Verjson/app", 3, draft=True),
            pr_node("Verjson/app", 4, hold=True),
            pr_node("Verjson/app", 5, fork=True),
            pr_node("Verjson/app", 6, contexts=("gate",)),
            pr_node("Verjson/no-gate", 7),
            pr_node("Verjson/required", 8),
        ]
        client = FakeClient(
            [page(nodes)],
            {
                "Verjson/app": "active",
                "Verjson/no-gate": "missing",
                "Verjson/required": "missing",
            },
            {
                "Verjson/required": [
                    {
                        "type": "workflows",
                        "parameters": {
                            "workflows": [
                                {"path": ".github/workflows/ai-review-merge.yml"}
                            ]
                        },
                    }
                ]
            },
        )

        findings = MODULE.audit(client, "Verjson")

        self.assertEqual(
            [finding["target"] for finding in findings],
            [
                "Verjson/app#1",
                "Verjson/app#2",
                "Verjson/app#3",
                "Verjson/app#4",
                "Verjson/app#5",
                "Verjson/no-gate#7",
                "Verjson/required#8",
            ],
        )
        by_pr = {finding["pr"]: finding for finding in findings}
        self.assertEqual(by_pr[1]["proposed_retrigger"], "add_re-review_label")
        self.assertEqual(by_pr[2]["head_stale_reason"], "legacy_gate_context")
        self.assertFalse(by_pr[3]["eligible"])
        self.assertFalse(by_pr[4]["eligible"])
        self.assertFalse(by_pr[5]["eligible"])
        self.assertEqual(by_pr[7]["workflow"], "missing")
        self.assertEqual(by_pr[8]["workflow"], "required")
        self.assertTrue(by_pr[8]["eligible"])
        self.assertEqual(by_pr[8]["proposed_retrigger"], "close_reopen")

    def test_graphql_pagination_is_complete(self):
        client = FakeClient(
            [
                page([pr_node("Verjson/app", 1)], next_page=True, cursor="cursor-2"),
                page([pr_node("Verjson/app", 2)]),
            ],
            {"Verjson/app": "active"},
        )

        findings = MODULE.audit(client, "Verjson")

        graphql = [command for command in client.commands if command[:2] == ["api", "graphql"]]
        self.assertEqual(len(findings), 2)
        self.assertEqual(len(graphql), 2)
        self.assertIn("cursor=cursor-2", graphql[1])

    def test_truncated_check_contexts_fail_closed(self):
        node = pr_node("Verjson/app", 1)
        contexts = node["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]
        contexts["totalCount"] = 101
        client = FakeClient([page([node])])

        with self.assertRaisesRegex(MODULE.AuditError, "truncated"):
            MODULE.audit(client, "Verjson")

    def test_rate_limit_is_typed_and_never_mutates(self):
        client = FakeClient(
            [],
            failure=MODULE.AuditError("rate_limited", "GitHub rate limit prevented audit", 403),
        )

        result, stdout, stderr = self.capture_main(
            client, "--apply", "--authorize-repo", "Verjson/app"
        )

        self.assertEqual(result, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["kind"], "rate_limited")
        self.assertFalse(any(command[:2] == ["pr", "edit"] for command in client.commands))

    def test_dry_run_never_dispatches_pushes_or_edits(self):
        client = FakeClient([page([pr_node("Verjson/app", 1)])], {"Verjson/app": "active"})

        result, stdout, _ = self.capture_main(client)

        self.assertEqual(result, 0)
        self.assertIn('"action": "dry_run"', stdout)
        flattened = [" ".join(command) for command in client.commands]
        self.assertFalse(any("workflow run" in command for command in flattened))
        self.assertFalse(any(command.startswith("git push") for command in flattened))
        self.assertFalse(any(command.startswith("pr edit") for command in flattened))
        self.assertFalse(any(command.startswith("pr close") for command in flattened))
        self.assertFalse(any(command.startswith("pr reopen") for command in flattened))

    def test_apply_requires_exact_repo_authority(self):
        client = FakeClient(
            [page([pr_node("Verjson/app", 1), pr_node("Verjson/other", 2)])],
            {"Verjson/app": "active", "Verjson/other": "active"},
        )

        result, stdout, _ = self.capture_main(
            client, "--apply", "--authorize-repo", "Verjson/app"
        )

        self.assertEqual(result, 0)
        records = [json.loads(line) for line in stdout.splitlines()]
        findings = {record["target"]: record for record in records if "target" in record}
        self.assertEqual(findings["Verjson/app#1"]["action"], "re-review_label_added")
        self.assertEqual(findings["Verjson/other#2"]["action"], "refused_unmanaged")
        mutations = [command for command in client.commands if command[:2] == ["pr", "edit"]]
        self.assertEqual(
            mutations,
            [["pr", "edit", "1", "--repo", "Verjson/app", "--add-label", "re-review"]],
        )

    def test_authority_without_apply_is_rejected_before_github(self):
        client = FakeClient([])

        result, _, stderr = self.capture_main(client, "--authorize-repo", "Verjson/app")

        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr)["kind"], "apply_required")
        self.assertEqual(client.commands, [])

    def test_cross_org_authority_is_rejected_before_github(self):
        client = FakeClient([])

        result, _, stderr = self.capture_main(
            client, "--apply", "--authorize-repo", "Other/app"
        )

        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr)["kind"], "invalid_authority")
        self.assertEqual(client.commands, [])

    def test_required_workflow_uses_authorized_close_reopen_not_dispatch(self):
        client = FakeClient(
            [page([pr_node("Verjson/required", 8)])],
            {"Verjson/required": "missing"},
            {
                "Verjson/required": [
                    {
                        "type": "workflows",
                        "parameters": {
                            "workflows": [
                                {"path": ".github/workflows/ai-review-merge.yml"}
                            ]
                        },
                    }
                ]
            },
        )

        result, stdout, _ = self.capture_main(
            client, "--apply", "--authorize-repo", "Verjson/required"
        )

        self.assertEqual(result, 0)
        self.assertIn('"action": "close_reopen_completed"', stdout)
        mutations = [
            command for command in client.commands if command and command[0] == "pr"
        ]
        self.assertEqual(mutations[0][:5], ["pr", "close", "8", "--repo", "Verjson/required"])
        self.assertEqual(
            mutations[1],
            ["pr", "reopen", "8", "--repo", "Verjson/required"],
        )
        self.assertFalse(any(command[:2] == ["workflow", "run"] for command in mutations))


if __name__ == "__main__":
    unittest.main()
