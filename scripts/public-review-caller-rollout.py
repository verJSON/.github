#!/usr/bin/env python3
"""Rollout gate: every public repository's AI review caller includes a fix.

ADR 0235 decision 7 moves a public target of the merge-gate relay to the
ephemeral lane, but an adopter's `ai-review-merge.yml` is a generated caller
pinned to a canonical commit, so an adopter keeps sending public pull-request
heads to the persistent pool until its caller is regenerated at or after the
fix. This read-only check is the blocking rollout step: it lists every
public, non-archived repository in the organization, reads its default-branch
caller, and requires the pinned canonical commit to contain `--minimum`.

Exit 0: every public caller contains the minimum (a repository without a
caller has nothing to dispatch and is reported, not failed). Exit 1: a caller
predates or diverges from the minimum. Exit 2: undetermined, including any
API failure other than an absent caller, a caller this check cannot parse, or
a caller not pinned to a full commit SHA. It never reads unknown as current.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import subprocess
import sys
from urllib.parse import quote

import yaml

# GitHub runs a workflow under either extension, so both spellings are callers.
CALLER_PATHS = (".github/workflows/ai-review-merge.yml", ".github/workflows/ai-review-merge.yaml")
CANONICAL_REPOSITORY = ".github"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
# Any reference to the canonical reusable workflow, pinned or not.
CANONICAL_CALL = re.compile(r"(?i)^verjson/\.github/\.github/workflows/ai-review-merge\.ya?ml@(.*)$")
CONTAINS = ("identical", "ahead")
EXCLUDES = ("behind", "diverged")


class Undetermined(Exception):
    """GitHub state could not be established; the check fails closed."""


class NotFound(Exception):
    """The API answered 404 for a path."""


def gh_json(path: str, *, paginate: bool = False):
    command = ["gh", "api", "--hostname", "github.com", "--method", "GET"]
    if paginate:
        command += ["--paginate", "--slurp"]
    command.append(path)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        if "(HTTP 404)" in result.stderr:
            raise NotFound(path)
        raise Undetermined(f"GitHub API read failed for {path}: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise Undetermined(f"GitHub API response for {path} is not valid JSON: {error}") from None
    if not paginate:
        return payload
    if not isinstance(payload, list) or not all(isinstance(page, list) for page in payload):
        raise Undetermined(f"GitHub API pages for {path} are not arrays")
    return [item for page in payload for item in page]


def public_repositories(org: str) -> list[tuple[str, str]]:
    """(name, default branch) of every public, non-archived repository."""
    found = []
    for record in gh_json(f"orgs/{org}/repos?type=public&per_page=100", paginate=True):
        if not isinstance(record, dict):
            raise Undetermined("a repository record is not an object")
        name, branch = record.get("name"), record.get("default_branch")
        visibility, archived = record.get("visibility"), record.get("archived")
        if not (isinstance(name, str) and isinstance(branch, str) and isinstance(archived, bool)):
            raise Undetermined(f"repository record {name!r} lacks name, default_branch, or archived")
        if visibility != "public":
            raise Undetermined(f"repository {name} listed as public reports visibility {visibility!r}")
        if not archived:
            found.append((name, branch))
    if not found:
        raise Undetermined(f"organization {org} lists no public repositories")
    return sorted(found)


def pinned_commit(text: str, where: str) -> str:
    """The single full commit SHA the caller pins the canonical workflow to."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise Undetermined(f"{where}: caller does not parse: {error}") from None
    jobs = document.get("jobs") if isinstance(document, dict) else None
    if not isinstance(jobs, dict) or not jobs:
        raise Undetermined(f"{where}: caller has no jobs")
    pins = set()
    for name, job in jobs.items():
        uses = job.get("uses") if isinstance(job, dict) else None
        match = CANONICAL_CALL.match(uses) if isinstance(uses, str) else None
        if match is None:
            raise Undetermined(f"{where}: job {name!r} does not call the canonical ai-review-merge.yml")
        if not SHA_PATTERN.fullmatch(match.group(1)):
            raise Undetermined(f"{where}: job {name!r} pins {match.group(1)!r}, not a full commit SHA")
        pins.add(match.group(1))
    if len(pins) != 1:
        raise Undetermined(f"{where}: caller pins more than one canonical commit: {sorted(pins)}")
    return pins.pop()


def read_callers(org: str, name: str, branch: str) -> list[tuple[str, str]]:
    """(path, text) of each caller spelling the default branch holds. With
    neither, the repository must still exist: one deleted or renamed after
    the listing is undetermined, never "no caller"."""
    found = []
    for path in CALLER_PATHS:
        try:
            record = gh_json(f"repos/{org}/{name}/contents/{path}?ref={quote(branch)}")
        except NotFound:
            continue
        content = record.get("content") if isinstance(record, dict) else None
        if not isinstance(content, str) or record.get("encoding") != "base64":
            raise Undetermined(f"{org}/{name}: {path} is not a base64 file")
        try:
            found.append((path, base64.b64decode(content, validate=False).decode("utf-8")))
        except (binascii.Error, UnicodeDecodeError) as error:
            raise Undetermined(f"{org}/{name}: {path} does not decode: {error}") from None
    if not found:
        try:
            gh_json(f"repos/{org}/{name}")
        except NotFound:
            raise Undetermined(f"{org}/{name} was listed but no longer exists; rerun the check") from None
    return found


def contains(org: str, minimum: str, pinned: str) -> bool:
    # Both operands reach a URL path, so both must be object names, not text.
    if not (SHA_PATTERN.fullmatch(minimum) and SHA_PATTERN.fullmatch(pinned)):
        raise Undetermined(f"compare operands must be full commit SHAs: {minimum!r}...{pinned!r}")
    try:
        status = gh_json(f"repos/{org}/{CANONICAL_REPOSITORY}/compare/{minimum}...{pinned}").get("status")
    except NotFound:
        raise Undetermined(f"{org}/{CANONICAL_REPOSITORY} cannot compare {minimum}...{pinned}") from None
    if status in CONTAINS:
        return True
    if status in EXCLUDES:
        return False
    raise Undetermined(f"compare {minimum}...{pinned} reported status {status!r}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", default="Verjson")
    parser.add_argument("--minimum", required=True, help="the 40-hex canonical commit every caller must contain")
    arguments = parser.parse_args(argv)
    if not SHA_PATTERN.fullmatch(arguments.minimum):
        print("UNDETERMINED: --minimum must be a full 40-hex commit SHA", file=sys.stderr)
        return 2
    stale = []
    try:
        for name, branch in public_repositories(arguments.org):
            if name.casefold() == CANONICAL_REPOSITORY:
                print(f"CANONICAL: {arguments.org}/{name} dispatches its own default-branch workflow")
                continue
            callers = read_callers(arguments.org, name, branch)
            if not callers:
                print(f"NO CALLER: {arguments.org}/{name}@{branch} has no {' or '.join(CALLER_PATHS)}")
                continue
            for path, text in callers:
                pinned = pinned_commit(text, f"{arguments.org}/{name}@{branch}:{path}")
                if contains(arguments.org, arguments.minimum, pinned):
                    print(f"CURRENT: {arguments.org}/{name} pins {pinned} ({path})")
                else:
                    stale.append(
                        f"STALE: {arguments.org}/{name} pins {pinned}, "
                        f"which does not contain {arguments.minimum} ({path})"
                    )
    except Undetermined as error:
        print(f"UNDETERMINED: {error}", file=sys.stderr)
        return 2
    for line in stale:
        print(line)
    if stale:
        print("regenerate each with: scripts/gen-ai-review-caller.sh <sha> > .github/workflows/ai-review-merge.yml")
        return 1
    print(f"clean: every public caller in {arguments.org} contains {arguments.minimum}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
