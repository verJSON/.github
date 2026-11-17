#!/usr/bin/env python3
"""Read-only fleet ledger of active agent-delivery claims (#2212).

Visibility only: aggregates trusted PR claim markers across repositories. Does
not create, mutate, or authorize claims — the per-(repo, pr, head_sha) comment
receipt remains the claim authority (ADR 0243 / #2194).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Collection, Iterable


_CLAIM_PATH = Path(__file__).with_name("agent_delivery_claim.py")
_SPEC = importlib.util.spec_from_file_location("agent_delivery_claim", _CLAIM_PATH)
_CLAIM = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = _CLAIM
_SPEC.loader.exec_module(_CLAIM)

Claim = _CLAIM.Claim
ClaimRefusal = _CLAIM.ClaimRefusal
DEFAULT_TRUSTED_CLAIM_AUTHORS = _CLAIM.DEFAULT_TRUSTED_CLAIM_AUTHORS
parse_claim_marker = _CLAIM.parse_claim_marker
REPO_RE = _CLAIM.REPO_RE
PR_RE = _CLAIM.PR_RE


def _comment_author(comment: object) -> str | None:
    if not isinstance(comment, dict):
        return None
    user = comment.get("user")
    if not isinstance(user, dict):
        return None
    login = user.get("login")
    return login if isinstance(login, str) else None


def claims_from_comments(
    comments: Iterable[object],
    *,
    trusted_authors: Collection[str] = DEFAULT_TRUSTED_CLAIM_AUTHORS,
) -> list[Claim]:
    authors = frozenset(trusted_authors)
    if not authors:
        raise ClaimRefusal("trusted claim author allowlist is empty")
    claims: list[Claim] = []
    for comment in comments:
        if _comment_author(comment) not in authors:
            continue
        body = comment.get("body") if isinstance(comment, dict) else None
        parsed = parse_claim_marker(body if isinstance(body, str) else "")
        if parsed is not None and parsed.status == "claimed":
            claims.append(parsed)
    return claims


def list_active_claims(
    sources: Iterable[tuple[str, int, Iterable[object]]],
    *,
    trusted_authors: Collection[str] = DEFAULT_TRUSTED_CLAIM_AUTHORS,
) -> list[Claim]:
    """Return active claims from (repository, pull_request, comments) sources.

    Deduplicates by the claim triple, keeping the first trusted marker observed.
    Never invents a claim that is not present in a trusted comment.
    """
    seen: set[tuple[str, int, str]] = set()
    active: list[Claim] = []
    for repository, pull_request, comments in sources:
        if not isinstance(repository, str) or not REPO_RE.fullmatch(repository):
            raise ClaimRefusal("repository identity is malformed")
        if not isinstance(pull_request, int) or isinstance(pull_request, bool):
            raise ClaimRefusal("pull request number is malformed")
        if not PR_RE.fullmatch(str(pull_request)):
            raise ClaimRefusal("pull request number is malformed")
        for claim in claims_from_comments(comments, trusted_authors=trusted_authors):
            if (
                claim.repository != repository
                or claim.pull_request != pull_request
            ):
                # Marker must match the source PR; forged cross-PR bodies ignored.
                continue
            key = (claim.repository, claim.pull_request, claim.head_sha)
            if key in seen:
                continue
            seen.add(key)
            active.append(claim)
    return active


def format_ledger(claims: list[Claim]) -> str:
    lines = [
        "# Agent delivery claim ledger",
        "",
        "Read-only visibility over trusted claim receipts. Not a claim authority.",
        "",
    ]
    if not claims:
        lines.append("(no active claims)")
        lines.append("")
        return "\n".join(lines)
    for claim in claims:
        lines.append(
            f"- `{claim.repository}`#{claim.pull_request} "
            f"head=`{claim.head_sha}` claim_id=`{claim.claim_id}` "
            f"run=`{claim.run_id or '-'}` workflow=`{claim.workflow_path or '-'}`"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os
    import subprocess

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        action="append",
        dest="repositories",
        required=True,
        help="Repository to scan (repeatable), e.g. verJSON/.github",
    )
    parser.add_argument(
        "--pull-request",
        action="append",
        dest="pull_requests",
        type=int,
        default=None,
        help="Optional PR number to scan (repeatable). Defaults to open Renovate PRs.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON objects instead of the markdown ledger.",
    )
    args = parser.parse_args(argv)

    token_present = bool(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    if not token_present:
        print("::error::GH_TOKEN or GITHUB_TOKEN is required to read claim comments", file=sys.stderr)
        return 2

    def gh_api(*api_args: str) -> str:
        completed = subprocess.run(
            ["gh", "api", *api_args],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise ClaimRefusal(
                f"github api failed: {completed.stderr.strip() or completed.stdout.strip()}"
            )
        return completed.stdout

    def list_open_renovate_prs(repository: str) -> list[int]:
        raw = gh_api(
            f"repos/{repository}/pulls?state=open&per_page=100",
        )
        items = json.loads(raw)
        if not isinstance(items, list):
            raise ClaimRefusal("pull request list is malformed")
        numbers: list[int] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            user = item.get("user")
            login = user.get("login") if isinstance(user, dict) else None
            if login not in {"renovate[bot]", "renovate"}:
                continue
            number = item.get("number")
            if isinstance(number, int) and not isinstance(number, bool):
                numbers.append(number)
        return numbers

    def list_comments(repository: str, pull_request: int) -> list[dict]:
        raw = gh_api(
            "--paginate",
            f"repos/{repository}/issues/{pull_request}/comments?per_page=100",
        )
        chunks: list[object] = []
        decoder = json.JSONDecoder()
        idx = 0
        text = raw.strip()
        while idx < len(text):
            while idx < len(text) and text[idx].isspace():
                idx += 1
            if idx >= len(text):
                break
            obj, end = decoder.raw_decode(text, idx)
            chunks.append(obj)
            idx = end
        comments: list[dict] = []
        for chunk in chunks:
            if isinstance(chunk, list):
                comments.extend(item for item in chunk if isinstance(item, dict))
        return comments

    sources: list[tuple[str, int, list[dict]]] = []
    try:
        for repository in args.repositories:
            pr_numbers = list(args.pull_requests or [])
            if not pr_numbers:
                pr_numbers = list_open_renovate_prs(repository)
            for number in pr_numbers:
                sources.append((repository, number, list_comments(repository, number)))
        claims = list_active_claims(sources)
    except ClaimRefusal as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([asdict(claim) for claim in claims], sort_keys=True, indent=2))
    else:
        print(format_ledger(claims), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
