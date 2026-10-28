#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Any, Iterable


GATE_QUERY = """
query($searchQuery: String!, $cursor: String) {
  search(type: ISSUE, query: $searchQuery, first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        isDraft
        headRefOid
        baseRefName
        isCrossRepository
        repository { nameWithOwner }
        labels(first: 100) { totalCount nodes { name } }
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup {
                contexts(first: 100) {
                  totalCount
                  nodes {
                    __typename
                    ... on CheckRun { name }
                    ... on StatusContext { context }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

HTTP_STATUS = re.compile(r"HTTP ([0-9]{3})")
CURRENT_GATE = {"gate"}
LEGACY_GATE = {"classify", "ai-review", "ai-merge"}


class AuditError(RuntimeError):
    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


class GhClient:
    def run(self, args: list[str]) -> str:
        completed = subprocess.run(
            ["gh", *args],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode == 0:
            return completed.stdout
        match = HTTP_STATUS.search(completed.stderr)
        status = int(match.group(1)) if match else None
        if status in {403, 429} and "rate limit" in completed.stderr.lower():
            raise AuditError("rate_limited", "GitHub rate limit prevented a complete audit", status)
        raise AuditError("github_unavailable", "GitHub data was unavailable", status)

    def json(self, args: list[str]) -> dict[str, Any]:
        raw = self.run(args)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise AuditError("invalid_response", "GitHub returned invalid JSON") from error
        if not isinstance(value, dict):
            raise AuditError("invalid_response", "GitHub returned an unexpected JSON shape")
        return value


@dataclass(frozen=True)
class PullRequest:
    repo: str
    number: int
    draft: bool
    hold: bool
    fork: bool
    head: str
    base: str
    contexts: frozenset[str]

    @property
    def target(self) -> str:
        return f"{self.repo}#{self.number}"


def _context_names(node: dict[str, Any]) -> frozenset[str]:
    commits = node.get("commits", {}).get("nodes", [])
    if len(commits) != 1:
        raise AuditError("invalid_response", "PR head commit data was incomplete")
    rollup = commits[0].get("commit", {}).get("statusCheckRollup")
    if rollup is None:
        return frozenset()
    contexts = rollup.get("contexts", {})
    nodes = contexts.get("nodes", [])
    total = contexts.get("totalCount")
    if not isinstance(nodes, list) or not isinstance(total, int) or total != len(nodes):
        raise AuditError("truncated_checks", "A PR check rollup was truncated")
    names: set[str] = set()
    for context in nodes:
        if context.get("__typename") == "CheckRun":
            name = context.get("name")
        elif context.get("__typename") == "StatusContext":
            name = context.get("context")
        else:
            continue
        if isinstance(name, str):
            names.add(name)
    return frozenset(names)


def parse_pr(node: dict[str, Any]) -> PullRequest:
    repo = node.get("repository", {}).get("nameWithOwner")
    number = node.get("number")
    head = node.get("headRefOid")
    base = node.get("baseRefName")
    label_connection = node.get("labels", {})
    labels = label_connection.get("nodes", [])
    label_total = label_connection.get("totalCount")
    if (
        not isinstance(repo, str)
        or not isinstance(number, int)
        or not isinstance(head, str)
        or not isinstance(base, str)
        or not re.fullmatch(r"[A-Za-z0-9._/-]+", base)
        or ".." in base
        or not re.fullmatch(r"[0-9a-f]{40}", head)
        or not isinstance(labels, list)
        or not isinstance(label_total, int)
        or label_total != len(labels)
    ):
        raise AuditError("invalid_response", "PR identity data was incomplete")
    label_names = {
        label.get("name")
        for label in labels
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    }
    return PullRequest(
        repo=repo,
        number=number,
        draft=bool(node.get("isDraft")),
        hold=bool({"hold", "DO NOT MERGE"} & label_names),
        fork=bool(node.get("isCrossRepository")),
        head=head,
        base=base,
        contexts=_context_names(node),
    )


def enumerate_open_prs(client: GhClient, org: str) -> list[PullRequest]:
    cursor: str | None = None
    results: list[PullRequest] = []
    while True:
        args = [
            "api",
            "graphql",
            "-f",
            f"query={GATE_QUERY}",
            "-F",
            f"searchQuery=org:{org} is:pr is:open",
        ]
        if cursor is not None:
            args.extend(["-F", f"cursor={cursor}"])
        payload = client.json(args)
        try:
            search = payload["data"]["search"]
            nodes = search["nodes"]
            page = search["pageInfo"]
        except (KeyError, TypeError) as error:
            raise AuditError("invalid_response", "GraphQL search data was incomplete") from error
        if not isinstance(nodes, list):
            raise AuditError("invalid_response", "GraphQL PR page was invalid")
        results.extend(parse_pr(node) for node in nodes if isinstance(node, dict))
        if not page.get("hasNextPage"):
            return results
        cursor = page.get("endCursor")
        if not isinstance(cursor, str) or not cursor:
            raise AuditError("invalid_response", "GraphQL pagination cursor was missing")


def ai_review_dispatch_workflow(repo: str) -> str:
    if repo.casefold() == "verjson/.github":
        return "ai-review-merge-dispatch.yml"
    return "ai-review-merge.yml"


def workflow_state(client: GhClient, repo: str, base: str) -> str:
    local_state = "missing"
    dispatch_workflow = ai_review_dispatch_workflow(repo)
    try:
        workflow = client.json(["api", f"repos/{repo}/actions/workflows/{dispatch_workflow}"])
    except AuditError as error:
        if error.status != 404:
            raise
    else:
        local_state = "active" if workflow.get("state") == "active" else "disabled"
        if local_state == "active":
            return local_state

    encoded_base = urllib.parse.quote(base, safe="")
    try:
        raw_rules = client.run(
            [
                "api",
                "--paginate",
                f"repos/{repo}/rules/branches/{encoded_base}?per_page=100",
                "--jq",
                ".[]",
            ]
        )
    except AuditError as error:
        if error.status == 404:
            return local_state
        raise
    try:
        rules = [json.loads(line) for line in raw_rules.splitlines() if line.strip()]
    except json.JSONDecodeError as error:
        raise AuditError("invalid_response", "Branch rules returned invalid JSON") from error
    if not all(isinstance(rule, dict) for rule in rules):
        raise AuditError("invalid_response", "Branch rules returned an unexpected shape")
    required = any(
        rule.get("type") == "workflows"
        and any(
        workflow.get("path") == ".github/workflows/ai-review-merge.yml"
            for workflow in rule.get("parameters", {}).get("workflows", [])
            if isinstance(workflow, dict)
        )
        for rule in rules
    )
    return "required" if required else local_state


def classify(pr: PullRequest, workflow: str) -> dict[str, Any]:
    legacy = sorted(pr.contexts & LEGACY_GATE)
    exclusions = [
        name
        for name, active in (("draft", pr.draft), ("hold", pr.hold), ("fork", pr.fork))
        if active
    ]
    eligible = not exclusions and workflow in {"active", "required"}
    if legacy:
        stale_reason = "legacy_gate_context"
    elif eligible:
        stale_reason = "no_gate_context"
    else:
        stale_reason = "excluded_or_unavailable"
    if not eligible:
        retrigger = None
    elif workflow == "required":
        retrigger = "close_reopen"
    else:
        retrigger = "add_re-review_label"
    return {
        "target": pr.target,
        "repo": pr.repo,
        "pr": pr.number,
        "head": pr.head,
        "missing_gate": True,
        "draft": pr.draft,
        "hold": pr.hold,
        "fork": pr.fork,
        "workflow": workflow,
        "head_stale": bool(legacy) or eligible,
        "head_stale_reason": stale_reason,
        "legacy_gate_contexts": legacy,
        "proposed_retrigger": retrigger,
        "eligible": eligible,
    }


def audit(client: GhClient, org: str) -> list[dict[str, Any]]:
    workflows: dict[tuple[str, str], str] = {}
    findings: list[dict[str, Any]] = []
    for pr in enumerate_open_prs(client, org):
        if CURRENT_GATE & pr.contexts:
            continue
        workflow_key = (pr.repo, pr.base)
        if workflow_key not in workflows:
            workflows[workflow_key] = workflow_state(client, pr.repo, pr.base)
        findings.append(classify(pr, workflows[workflow_key]))
    return sorted(findings, key=lambda item: (item["repo"], item["pr"]))


def apply_authorized(
    client: GhClient,
    findings: Iterable[dict[str, Any]],
    authorized: set[str],
) -> None:
    for finding in findings:
        if not finding["eligible"]:
            finding["action"] = "not_supported"
        elif finding["repo"] not in authorized:
            finding["action"] = "refused_unmanaged"
        elif finding["proposed_retrigger"] == "close_reopen":
            try:
                client.run(
                    [
                        "pr",
                        "close",
                        str(finding["pr"]),
                        "--repo",
                        finding["repo"],
                        "--comment",
                        "Gate coverage audit #474: reversible close/reopen retrigger for the current head.",
                    ]
                )
                client.run(["pr", "reopen", str(finding["pr"]), "--repo", finding["repo"]])
            except AuditError as error:
                raise AuditError(
                    "retrigger_failed",
                    f"{finding['target']} close/reopen did not complete; owning PM must verify PR state",
                    error.status,
                ) from error
            finding["action"] = "close_reopen_completed"
        else:
            client.run(
                [
                    "pr",
                    "edit",
                    str(finding["pr"]),
                    "--repo",
                    finding["repo"],
                    "--add-label",
                    "re-review",
                ]
            )
            finding["action"] = "re-review_label_added"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit open PRs missing the current gate check")
    parser.add_argument("--org", default="Verjson")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--authorize-repo", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, client: GhClient | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    authorized = set(args.authorize_repo)
    invalid = sorted(
        repo
        for repo in authorized
        if not re.fullmatch(r"[^/]+/[^/]+", repo)
        or repo.split("/", 1)[0] != args.org
    )
    if invalid:
        print(json.dumps({"kind": "invalid_authority", "repos": invalid}), file=sys.stderr)
        return 2
    if authorized and not args.apply:
        print(json.dumps({"kind": "apply_required", "message": "--authorize-repo requires --apply"}), file=sys.stderr)
        return 2
    gh = client or GhClient()
    try:
        findings = audit(gh, args.org)
        if args.apply:
            apply_authorized(gh, findings, authorized)
        else:
            for finding in findings:
                finding["action"] = "dry_run"
        for finding in findings:
            print(json.dumps(finding, sort_keys=True))
        print(
            json.dumps(
                {
                    "summary": {
                        "missing_gate": len(findings),
                        "non_draft_missing_gate": sum(
                            1 for finding in findings if not finding["draft"]
                        ),
                        "draft": sum(1 for finding in findings if finding["draft"]),
                        "eligible": sum(1 for finding in findings if finding["eligible"]),
                        "mode": "apply" if args.apply else "dry_run",
                    }
                },
                sort_keys=True,
            )
        )
        return 0
    except AuditError as error:
        print(
            json.dumps(
                {"kind": error.kind, "message": str(error), "http_status": error.status},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
