#!/usr/bin/env python3
"""Idempotent agent delivery claim for Renovate handoff (ADR 0243 / #2191).

Durable identity is the triple (repository, pull_request, head_sha). A second
claim for the same triple is a no-op. Active claims are concurrency-bounded.
Receipts are GitHub-native PR comment markers and never carry secrets.
"""
from __future__ import annotations

import re
import secrets
import threading
from dataclasses import dataclass
from typing import Callable, Protocol


REPO_RE = re.compile(
    r"^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+"
    r"/[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+$"
)
PR_RE = re.compile(r"^[123456789][0123456789]{0,14}$")
HEAD_RE = re.compile(r"^[0123456789abcdef]{40}$")
CLAIM_ID_RE = re.compile(r"^[0123456789abcdef]{32}$")
MARKER_RE = re.compile(
    r"<!-- agent-delivery-claim:v1 "
    r"repo:(?P<repository>"
    r"[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+/"
    r"[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+) "
    r"pr:(?P<pull_request>[123456789][0123456789]{0,14}) "
    r"head:(?P<head_sha>[0123456789abcdef]{40}) "
    r"claim_id:(?P<claim_id>[0123456789abcdef]{32}) -->"
)

DEFAULT_MAX_CONCURRENT = 8


class ClaimRefusal(ValueError):
    """Fail-closed refusal for malformed input or a concurrency bound."""


@dataclass(frozen=True)
class Claim:
    claim_id: str
    repository: str
    pull_request: int
    head_sha: str
    status: str = "claimed"


@dataclass(frozen=True)
class ClaimResult:
    claim: Claim
    created: bool


@dataclass(frozen=True)
class WorkerOutcome:
    status: str
    detail: str


class Worker(Protocol):
    def run(self, claim: Claim) -> WorkerOutcome | None:
        ...


class StubDependencyUpgradeWorker:
    """Placeholder until the dependency-upgrade skill worker lands (#2191)."""

    def run(self, claim: Claim) -> WorkerOutcome:
        return WorkerOutcome(
            status="deferred",
            detail=(
                f"worker deferred: dependency-upgrade skill end-to-end for "
                f"{claim.repository}#{claim.pull_request}@{claim.head_sha} "
                f"(Part of #2191)"
            ),
        )


def validate_triple(repository: object, pull_request: object, head_sha: object) -> Claim:
    if not isinstance(repository, str) or not REPO_RE.fullmatch(repository):
        raise ClaimRefusal("repository identity is malformed")
    if not isinstance(pull_request, int) or isinstance(pull_request, bool):
        raise ClaimRefusal("pull request number is malformed")
    if not PR_RE.fullmatch(str(pull_request)):
        raise ClaimRefusal("pull request number is malformed")
    if not isinstance(head_sha, str) or not HEAD_RE.fullmatch(head_sha):
        raise ClaimRefusal("head SHA is malformed")
    return Claim(
        claim_id="",
        repository=repository,
        pull_request=pull_request,
        head_sha=head_sha,
        status="claimed",
    )


def new_claim_id() -> str:
    return secrets.token_hex(16)


def claim_marker(claim: Claim) -> str:
    if not CLAIM_ID_RE.fullmatch(claim.claim_id):
        raise ClaimRefusal("claim id is malformed")
    return (
        f"<!-- agent-delivery-claim:v1 repo:{claim.repository} "
        f"pr:{claim.pull_request} head:{claim.head_sha} "
        f"claim_id:{claim.claim_id} -->"
    )


def format_claim_receipt(claim: Claim) -> str:
    marker = claim_marker(claim)
    return (
        f"{marker}\n"
        f"## Agent delivery claim\n\n"
        f"- claim_id: `{claim.claim_id}`\n"
        f"- repository: `{claim.repository}`\n"
        f"- pull_request: `{claim.pull_request}`\n"
        f"- head_sha: `{claim.head_sha}`\n"
        f"- status: `{claim.status}`\n\n"
        f"Idempotent claim per ADR 0243 amendment 2026-11-17 "
        f"(ARB `arb-2139-2026-11-17`). Worker follows the dependency-upgrade "
        f"skill; this receipt carries no credentials.\n"
    )


def parse_claim_marker(body: str) -> Claim | None:
    if not isinstance(body, str):
        return None
    match = MARKER_RE.search(body)
    if match is None:
        return None
    return Claim(
        claim_id=match.group("claim_id"),
        repository=match.group("repository"),
        pull_request=int(match.group("pull_request")),
        head_sha=match.group("head_sha"),
        status="claimed",
    )


class MemoryClaimStore:
    """In-process store used by unit tests and local dry-runs."""

    def __init__(self, *, race_on_create: bool = False) -> None:
        self._claims: dict[tuple[str, int, str], Claim] = {}
        self._lock = threading.Lock()
        self._race_on_create = race_on_create
        self._create_gate = threading.Event()
        self._entered = 0
        self._entered_lock = threading.Lock()

    def find(self, repository: str, pull_request: int, head_sha: str) -> Claim | None:
        with self._lock:
            return self._claims.get((repository, pull_request, head_sha))

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for claim in self._claims.values() if claim.status == "claimed")

    def create(self, claim: Claim, *, max_concurrent: int | None = None) -> Claim:
        key = (claim.repository, claim.pull_request, claim.head_sha)
        if self._race_on_create:
            with self._entered_lock:
                self._entered += 1
                entered = self._entered
            if entered == 1:
                # Wait until the second racer has also entered create().
                while True:
                    with self._entered_lock:
                        if self._entered >= 2:
                            break
                self._create_gate.set()
            else:
                self._create_gate.wait(timeout=5)
        with self._lock:
            existing = self._claims.get(key)
            if existing is not None:
                return existing
            active = sum(1 for item in self._claims.values() if item.status == "claimed")
            if max_concurrent is not None and active >= max_concurrent:
                raise ClaimRefusal("concurrency bound refuses a new agent delivery claim")
            self._claims[key] = claim
            return claim


class CommentClaimStore:
    """GitHub-native durable store backed by PR issue comments."""

    def __init__(
        self,
        *,
        list_comments: Callable[[], list[dict]],
        create_comment: Callable[[str], dict],
    ) -> None:
        self._list_comments = list_comments
        self._create_comment = create_comment
        self._lock = threading.RLock()

    def _claims(self) -> list[Claim]:
        claims: list[Claim] = []
        for comment in self._list_comments():
            body = comment.get("body") if isinstance(comment, dict) else None
            parsed = parse_claim_marker(body if isinstance(body, str) else "")
            if parsed is not None:
                claims.append(parsed)
        return claims

    def find(self, repository: str, pull_request: int, head_sha: str) -> Claim | None:
        with self._lock:
            for claim in self._claims():
                if (
                    claim.repository == repository
                    and claim.pull_request == pull_request
                    and claim.head_sha == head_sha
                ):
                    return claim
            return None

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for claim in self._claims() if claim.status == "claimed")

    def create(self, claim: Claim, *, max_concurrent: int | None = None) -> Claim:
        with self._lock:
            existing = self.find(claim.repository, claim.pull_request, claim.head_sha)
            if existing is not None:
                return existing
            if max_concurrent is not None and self.active_count() >= max_concurrent:
                raise ClaimRefusal("concurrency bound refuses a new agent delivery claim")
            self._create_comment(format_claim_receipt(claim))
            created = self.find(claim.repository, claim.pull_request, claim.head_sha)
            if created is None:
                raise ClaimRefusal("claim receipt was not durable after create")
            return created


def claim_delivery(
    store,
    *,
    repository: object,
    pull_request: object,
    head_sha: object,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    new_claim_id: Callable[[], str] = new_claim_id,
    worker: Worker | None = None,
) -> ClaimResult:
    if not isinstance(max_concurrent, int) or isinstance(max_concurrent, bool) or max_concurrent < 1:
        raise ClaimRefusal("concurrency bound is malformed")
    triple = validate_triple(repository, pull_request, head_sha)
    existing = store.find(triple.repository, triple.pull_request, triple.head_sha)
    if existing is not None:
        return ClaimResult(claim=existing, created=False)

    claim_id = new_claim_id()
    if not isinstance(claim_id, str) or not CLAIM_ID_RE.fullmatch(claim_id):
        raise ClaimRefusal("claim id is malformed")
    candidate = Claim(
        claim_id=claim_id,
        repository=triple.repository,
        pull_request=triple.pull_request,
        head_sha=triple.head_sha,
        status="claimed",
    )
    # Bound + insert are atomic inside store.create when the store supports it.
    stored = store.create(candidate, max_concurrent=max_concurrent)
    created = stored.claim_id == candidate.claim_id
    if created and worker is not None:
        worker.run(stored)
    return ClaimResult(claim=stored, created=created)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import os
    import subprocess
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull-request", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--max-concurrent", type=int, default=DEFAULT_MAX_CONCURRENT)
    parser.add_argument(
        "--store",
        choices=("memory", "comment"),
        default="comment",
        help="Durable claim store. 'comment' posts a PR receipt via gh.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use the in-memory store and print the receipt without posting.",
    )
    args = parser.parse_args(argv)

    if args.dry_run or args.store == "memory":
        store = MemoryClaimStore()
    else:
        token_present = bool(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
        if not token_present:
            print("::error::GH_TOKEN or GITHUB_TOKEN is required for comment claims", file=sys.stderr)
            return 2

        def gh_api(*api_args: str) -> str:
            completed = subprocess.run(
                ["gh", "api", *api_args],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise ClaimRefusal(f"github api failed: {completed.stderr.strip() or completed.stdout.strip()}")
            return completed.stdout

        def list_comments() -> list[dict]:
            raw = gh_api(
                "--paginate",
                f"repos/{args.repository}/issues/{args.pull_request}/comments?per_page=100",
            )
            # --paginate may emit multiple JSON arrays; normalize.
            chunks = []
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

        def create_comment(body: str) -> dict:
            completed = subprocess.run(
                [
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    f"repos/{args.repository}/issues/{args.pull_request}/comments",
                    "--input",
                    "-",
                ],
                check=False,
                capture_output=True,
                text=True,
                input=json.dumps({"body": body}),
            )
            if completed.returncode != 0:
                raise ClaimRefusal(
                    f"github api failed: {completed.stderr.strip() or completed.stdout.strip()}"
                )
            return json.loads(completed.stdout)

        store = CommentClaimStore(list_comments=list_comments, create_comment=create_comment)

    try:
        result = claim_delivery(
            store,
            repository=args.repository,
            pull_request=args.pull_request,
            head_sha=args.head_sha,
            max_concurrent=args.max_concurrent,
            # CLI always attaches the stub so deferred worker outcome is logged.
            worker=StubDependencyUpgradeWorker(),
        )
    except ClaimRefusal as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    receipt = format_claim_receipt(result.claim)
    print(receipt)
    print(f"created={str(result.created).lower()}")
    print(f"claim_id={result.claim.claim_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
