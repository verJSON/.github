#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Workflows start this script with `python3 -I`, which leaves its own directory
# off the module path; the sibling it imports lives there.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from changelog import ChangelogError, load_canonical_text  # noqa: E402


API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
RENOVATE_AUTHORS = frozenset({"app/renovate", "renovate[bot]"})
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
RENOVATE_REF = re.compile(
    r"^renovate/[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*$"
)
AUXILIARY_LINK = r"\[[^\]\r\n]{1,64}\]\(https://[^()\s]+\)"
AUXILIARY_LINK_GROUP = rf"\({AUXILIARY_LINK}(?:,\s+{AUXILIARY_LINK})*\)"
PACKAGE = re.compile(
    r"^\[([@A-Za-z0-9][@A-Za-z0-9._/+~:-]{0,213})\]"
    r"\(https://[^()\s]+\)"
    rf"(?:\s+{AUXILIARY_LINK_GROUP})*$"
)
BARE_PACKAGE = re.compile(r"^([@A-Za-z0-9][@A-Za-z0-9._/+~:-]{0,213})$")
CHANGE = re.compile(
    r"^(?:\[)?`([^`\r\n]{1,256})` → `([^`\r\n]{1,256})`"
    r"(?:\]\(https://[^()\s]+\))?$"
)
VERSION_PART = r"[0-9A-Za-z~^<>=*][0-9A-Za-z._+:/@~^<>=* -]{0,127}"
VERSION = re.compile(rf"^(?=.{{1,128}}$){VERSION_PART}(?: \|\| {VERSION_PART})*$")
PNPM_INTEGRITY_PIN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?\+sha512\.[0-9a-f]{128}$"
)
TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")
LOCK_FILE_HEADERS = ["Update", "Change"]
LOCK_FILE_UPDATE_TYPE = "lockFileMaintenance"
LOCK_FILE_CHANGE = re.compile(r"^[A-Za-z][A-Za-z0-9 ._-]{0,63}$")
MAX_UPDATES = 50
MAX_FRAGMENT_BYTES = 256 * 1024


class AutomationError(Exception):
    pass


class GitHubApiError(AutomationError):
    def __init__(self, method: str, path: str, status: int):
        super().__init__(f"GitHub API {method} {path} failed with HTTP {status}")
        self.status = status


@dataclass(frozen=True)
class Update:
    package: str
    from_version: str
    to_version: str


# Renovate's lockFileMaintenance class refreshes every transitive lock entry
# rather than named dependencies, so its admitted table carries no package rows.
LOCK_FILE_MAINTENANCE: tuple[Update, ...] = ()


class GitHubClient:
    def __init__(self, token: str):
        if not token:
            raise AutomationError("GitHub token is required")
        self.token = token

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        data = None
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "verjson-renovate-changelog",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{API_ROOT}/{path.lstrip('/')}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise GitHubApiError(method, path, error.code) from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise AutomationError(f"GitHub API {method} {path} failed: {error}") from None

    def pages(self, path: str) -> list[Any]:
        separator = "&" if "?" in path else "?"
        values: list[Any] = []
        for page in range(1, 31):
            document = self.request("GET", f"{path}{separator}per_page=100&page={page}")
            if not isinstance(document, list):
                raise AutomationError(f"GitHub API pagination response for {path} is not a list")
            values.extend(document)
            if len(document) < 100:
                return values
        raise AutomationError(f"GitHub API pagination exceeded 30 pages for {path}")


def require_mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AutomationError(f"{location} must be an object")
    return value


def require_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise AutomationError(f"{location} must be a non-empty string")
    return value


def require_sha(value: Any, location: str) -> str:
    text = require_string(value, location)
    if SHA.fullmatch(text) is None:
        raise AutomationError(f"{location} must be a full lower-case commit SHA")
    return text


def table_cells(line: str) -> list[str]:
    if not line.startswith("|") or not line.endswith("|"):
        raise AutomationError("Renovate update table uses an unsupported row shape")
    cells: list[str] = []
    cell: list[str] = []
    content = line[1:-1]
    index = 0
    while index < len(content):
        character = content[index]
        if character == "\\":
            if index + 1 >= len(content) or content[index + 1] != "|":
                raise AutomationError("Renovate update table uses an unsupported row escape")
            cell.append("\\|")
            index += 2
            continue
        if character == "|":
            cells.append("".join(cell))
            cell = []
        else:
            cell.append(character)
        index += 1
    cells.append("".join(cell))
    return [cell.replace("\\|", "|").strip() for cell in cells]


def parse_update_row(package_cell: str, change_cell: str) -> Update:
    package_match = PACKAGE.fullmatch(package_cell) or BARE_PACKAGE.fullmatch(
        package_cell
    )
    if package_match is None:
        raise AutomationError("Renovate update table contains an unsupported package cell")
    change_match = CHANGE.fullmatch(change_cell)
    if change_match is None:
        raise AutomationError("Renovate update table contains an unsupported change cell")
    from_version, to_version = change_match.groups()
    if from_version != from_version.strip() or to_version != to_version.strip():
        raise AutomationError("Renovate versions must not contain surrounding whitespace")
    if from_version == to_version:
        raise AutomationError("Renovate update table contains an unchanged version")
    versions = (from_version, to_version)
    package = package_match.group(1)
    if not all(
        PNPM_INTEGRITY_PIN.fullmatch(version) is not None
        if package == "pnpm" and "+sha512." in version
        else VERSION.fullmatch(version) is not None
        for version in versions
    ):
        raise AutomationError("Renovate update table contains an unsafe version value")
    return Update(package, from_version, to_version)


def table_rows(headers: list[str], lines: list[str], index: int) -> list[list[str]]:
    if len(headers) != len(set(headers)):
        raise AutomationError("Renovate update table contains duplicate column names")
    if index + 1 >= len(lines):
        raise AutomationError("Renovate update table has no separator row")
    separators = table_cells(lines[index + 1])
    if len(separators) != len(headers) or not all(
        TABLE_SEPARATOR.fullmatch(cell) for cell in separators
    ):
        raise AutomationError("Renovate update table separator is malformed")
    rows: list[list[str]] = []
    row_index = index + 2
    while row_index < len(lines) and lines[row_index].startswith("|"):
        cells = table_cells(lines[row_index])
        if len(cells) != len(headers):
            raise AutomationError("Renovate update table row has the wrong number of columns")
        rows.append(cells)
        row_index += 1
    return rows


def parse_package_table(headers: list[str], rows: list[list[str]]) -> tuple[Update, ...]:
    package_index = headers.index("Package")
    change_index = headers.index("Change")
    updates = tuple(
        parse_update_row(cells[package_index], cells[change_index]) for cells in rows
    )
    if not updates:
        raise AutomationError("Renovate update table contains no updates")
    return updates


def lock_file_rows(
    headers: list[str], lines: list[str], index: int
) -> list[list[str]] | None:
    """Rows of a lockFileMaintenance table, or None when this table is not one.

    Recognition is keyed on the `lockFileMaintenance` update type rather than on the
    headers alone, so an unrelated two-column table quoted in a package pull request's
    release notes stays as invisible to the parser as it was before this shape existed.
    """
    if headers != LOCK_FILE_HEADERS:
        return None
    try:
        rows = table_rows(headers, lines, index)
    except AutomationError:
        return None
    if not any(cells[0] == LOCK_FILE_UPDATE_TYPE for cells in rows):
        return None
    return rows


def parse_lock_file_table(rows: list[list[str]]) -> tuple[Update, ...]:
    if len(rows) != 1 or LOCK_FILE_CHANGE.fullmatch(rows[0][1]) is None:
        raise AutomationError("Renovate lock file maintenance table is not the documented shape")
    return LOCK_FILE_MAINTENANCE


def parse_updates(body: str) -> tuple[Update, ...]:
    if not isinstance(body, str) or len(body.encode("utf-8")) > 1024 * 1024:
        raise AutomationError("Renovate pull-request body is missing or too large")
    lines = body.splitlines()
    tables: list[tuple[Update, ...]] = []
    for index, line in enumerate(lines[:-2]):
        if not line.startswith("|"):
            continue
        try:
            headers = table_cells(line)
        except AutomationError:
            continue
        if "Package" in headers and "Change" in headers:
            tables.append(parse_package_table(headers, table_rows(headers, lines, index)))
            continue
        rows = lock_file_rows(headers, lines, index)
        if rows is not None:
            tables.append(parse_lock_file_table(rows))
    if len(tables) != 1:
        raise AutomationError("pull-request body must contain exactly one Renovate update table")
    updates = tables[0]
    if len(updates) > MAX_UPDATES:
        raise AutomationError(f"Renovate update table exceeds {MAX_UPDATES} rows")
    if len(updates) != len(set(updates)):
        raise AutomationError("Renovate update table repeats a package transition")
    return updates


def admit_pull_request(
    document: Any,
    repository: str,
    pr_number: int,
    expected_head: str,
    expected_base: str,
) -> dict[str, Any]:
    pull_request = require_mapping(document, "pull request")
    if pull_request.get("number") != pr_number:
        raise AutomationError("live pull-request number differs from the event")
    if pull_request.get("state") != "open" or pull_request.get("merged") is True:
        raise AutomationError("pull request is not open")
    author = require_string(
        require_mapping(pull_request.get("user"), "pull request user").get("login"),
        "pull request user.login",
    )
    if author not in RENOVATE_AUTHORS:
        raise AutomationError("pull request is not authored by the approved Renovate App")
    head = require_mapping(pull_request.get("head"), "pull request head")
    head_repository = require_mapping(head.get("repo"), "pull request head repository")
    if head_repository.get("full_name") != repository:
        raise AutomationError("cross-repository Renovate pull requests are not eligible")
    head_ref = require_string(head.get("ref"), "pull request head.ref")
    if RENOVATE_REF.fullmatch(head_ref) is None:
        raise AutomationError("pull-request branch is not a normalized renovate/* branch")
    head_sha = require_sha(head.get("sha"), "pull request head.sha")
    if head_sha != expected_head:
        raise AutomationError("live pull-request head differs from the triggering event")
    base = require_mapping(pull_request.get("base"), "pull request base")
    base_ref = require_string(base.get("ref"), "pull request base.ref")
    if base_ref != expected_base:
        raise AutomationError("pull request does not target the triggering base branch")
    return {
        "author": author,
        "base_ref": base_ref,
        "body": require_string(pull_request.get("body"), "pull request body"),
        "head_ref": head_ref,
        "head_sha": head_sha,
    }


def decode_content(document: Any, path: str) -> str:
    value = require_mapping(document, f"contents response for {path}")
    if value.get("type") != "file" or value.get("encoding") != "base64":
        raise AutomationError(f"{path} is not a base64-encoded file")
    encoded = require_string(value.get("content"), f"contents response for {path}.content")
    try:
        compact = "".join(encoded.split())
        raw = base64.b64decode(compact, validate=True)
        if len(raw) > MAX_FRAGMENT_BYTES:
            raise AutomationError(f"{path} exceeds the fragment size limit")
        return raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise AutomationError(f"{path} is not valid base64 UTF-8: {error}") from None


def valid_added_fragment(client: Any, repository: str, pr_number: int, head_sha: str) -> bool:
    changed = client.pages(f"repos/{repository}/pulls/{pr_number}/files")
    for index, raw_file in enumerate(changed):
        file = require_mapping(raw_file, f"pull-request file {index}")
        path = require_string(file.get("filename"), f"pull-request file {index}.filename")
        status = file.get("status")
        previous = file.get("previous_filename")
        added = status == "added" or (
            status == "renamed"
            and isinstance(previous, str)
            and not previous.startswith("NEXT/")
        )
        if not added or not path.startswith("NEXT/") or path.count("/") != 1:
            continue
        encoded_path = urllib.parse.quote(path, safe="/")
        # A query value, so "/" stays literal. `head_sha` is 40-hex by the `require_sha`
        # above, so both encodings are no-ops on today's input -- the position is the
        # reason, not a hypothetical ref that cannot reach here.
        encoded_head = urllib.parse.quote(head_sha, safe="/")
        text = decode_content(
            client.request(
                "GET", f"repos/{repository}/contents/{encoded_path}?ref={encoded_head}"
            ),
            path,
        )
        try:
            fragment = load_canonical_text(Path(path), text)
            if fragment.metadata.get("impact") in {"patch", "minor", "major"}:
                return True
        except ChangelogError:
            continue
    return False


def fragment_for(
    pr_number: int, updates: tuple[Update, ...], today: dt.date
) -> tuple[str, str]:
    slug = "renovate-dependencies"
    if updates == LOCK_FILE_MAINTENANCE:
        slug = "renovate-lock-file-maintenance"
        title = "Refresh all lock file dependencies"
        body = "Refresh all lock file dependencies with Renovate lock file maintenance."
    elif len(updates) == 1:
        update = updates[0]
        title = (
            f"Update dependency {update.package} from {update.from_version} "
            f"to {update.to_version}"
        )
        body = (
            f"Update `{update.package}` from `{update.from_version}` "
            f"to `{update.to_version}`."
        )
    else:
        title = f"Update {len(updates)} dependencies with Renovate"
        changes = "; ".join(
            f"`{update.package}` from `{update.from_version}` to `{update.to_version}`"
            for update in updates
        )
        body = f"Update dependencies with Renovate: {changes}."
    path = f"NEXT/{today.isoformat()}-issue-{pr_number}-{slug}.md"
    content = (
        "---\n"
        f"date: {today.isoformat()}\n"
        f"issue: {pr_number}\n"
        "impact: patch\n"
        f"title: {title}\n"
        "---\n\n"
        f"{body}\n"
    )
    try:
        load_canonical_text(Path(path), content)
    except ChangelogError as error:
        raise AutomationError(f"generated fragment is invalid: {error}") from None
    return path, content


def plan(
    client: Any,
    repository: str,
    pr_number: int,
    expected_head: str,
    expected_base: str,
    today: dt.date,
) -> dict[str, Any]:
    if REPOSITORY.fullmatch(repository) is None:
        raise AutomationError("repository must be owner/name")
    if pr_number <= 0:
        raise AutomationError("pull-request number must be positive")
    require_sha(expected_head, "expected head")
    if not expected_base:
        raise AutomationError("expected base branch is invalid")
    live = admit_pull_request(
        client.request("GET", f"repos/{repository}/pulls/{pr_number}"),
        repository,
        pr_number,
        expected_head,
        expected_base,
    )
    updates = parse_updates(live["body"])
    if valid_added_fragment(client, repository, pr_number, expected_head):
        return {
            "schema": 1,
            "required": False,
            "repository": repository,
            "pr_number": pr_number,
            "head_sha": expected_head,
            "head_ref": live["head_ref"],
            "base_ref": live["base_ref"],
            "updates": [asdict(update) for update in updates],
        }
    path, content = fragment_for(pr_number, updates, today)
    return {
        "schema": 1,
        "required": True,
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": expected_head,
        "head_ref": live["head_ref"],
        "base_ref": live["base_ref"],
        "updates": [asdict(update) for update in updates],
        "fragment_path": path,
        "fragment_content": content,
    }


def require_plan(document: Any) -> dict[str, Any]:
    value = require_mapping(document, "plan")
    required = value.get("required")
    expected = {
        "schema",
        "required",
        "repository",
        "pr_number",
        "head_sha",
        "head_ref",
        "base_ref",
        "updates",
    }
    if required is True:
        expected |= {"fragment_path", "fragment_content"}
    if set(value) != expected or value.get("schema") != 1 or not isinstance(required, bool):
        raise AutomationError("plan schema is invalid")
    return value


def apply(read_client: Any, write_client: Any, raw_plan: Any) -> str:
    planned = require_plan(raw_plan)
    if planned["required"] is not True:
        return "no changelog fragment required"
    repository = require_string(planned["repository"], "plan.repository")
    pr_number = planned["pr_number"]
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0:
        raise AutomationError("plan.pr_number must be positive")
    head_sha = require_sha(planned["head_sha"], "plan.head_sha")
    live = admit_pull_request(
        read_client.request("GET", f"repos/{repository}/pulls/{pr_number}"),
        repository,
        pr_number,
        head_sha,
        require_string(planned["base_ref"], "plan.base_ref"),
    )
    if live["head_ref"] != planned["head_ref"]:
        raise AutomationError("live pull-request branch differs from the plan")
    updates = parse_updates(live["body"])
    if [asdict(update) for update in updates] != planned["updates"]:
        raise AutomationError("live Renovate update table differs from the plan")
    if valid_added_fragment(read_client, repository, pr_number, head_sha):
        return "a valid NEXT fragment was added before the write"

    fragment_path = require_string(planned["fragment_path"], "plan.fragment_path")
    fragment_content = require_string(planned["fragment_content"], "plan.fragment_content")
    try:
        fragment = load_canonical_text(Path(fragment_path), fragment_content)
        planned_date = dt.date.fromisoformat(fragment.metadata["date"])
        expected_path, expected_content = fragment_for(pr_number, updates, planned_date)
    except (ChangelogError, ValueError) as error:
        raise AutomationError(f"planned fragment is invalid: {error}") from None
    if fragment_path != expected_path or fragment_content != expected_content:
        raise AutomationError("planned fragment does not match the admitted Renovate updates")

    # `heads/<branch>` is a fixed prefix plus ONE branch path segment: encode the
    # branch with no safe separators so a nested `renovate/x` travels as
    # `renovate%2Fx`, matching release-propose.py's spelling of the same endpoint
    # (#1458, ADR 0192's path rule).
    encoded_ref = f"heads/{urllib.parse.quote(live['head_ref'], safe='')}"
    ref_path = f"repos/{repository}/git/ref/{encoded_ref}"
    current_ref = require_mapping(write_client.request("GET", ref_path), "head ref")
    ref_object = require_mapping(current_ref.get("object"), "head ref object")
    if ref_object.get("type") != "commit" or ref_object.get("sha") != head_sha:
        raise AutomationError("Renovate branch moved before the write")

    encoded_path = urllib.parse.quote(fragment_path, safe="/")
    try:
        read_client.request(
            "GET",
            f"repos/{repository}/contents/{encoded_path}?ref={urllib.parse.quote(head_sha, safe='/')}",
        )
    except GitHubApiError as error:
        if error.status != 404:
            raise
    else:
        raise AutomationError("planned fragment path already exists on the Renovate head")

    commit = require_mapping(
        write_client.request("GET", f"repos/{repository}/git/commits/{head_sha}"),
        "head commit",
    )
    tree_sha = require_sha(
        require_mapping(commit.get("tree"), "head commit tree").get("sha"),
        "head commit tree.sha",
    )
    blob = require_mapping(
        write_client.request(
            "POST",
            f"repos/{repository}/git/blobs",
            {"content": fragment_content, "encoding": "utf-8"},
        ),
        "created blob",
    )
    blob_sha = require_sha(blob.get("sha"), "created blob.sha")
    tree = require_mapping(
        write_client.request(
            "POST",
            f"repos/{repository}/git/trees",
            {
                "base_tree": tree_sha,
                "tree": [
                    {
                        "path": fragment_path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob_sha,
                    }
                ],
            },
        ),
        "created tree",
    )
    new_tree_sha = require_sha(tree.get("sha"), "created tree.sha")
    created_commit = require_mapping(
        write_client.request(
            "POST",
            f"repos/{repository}/git/commits",
            {
                "message": "chore(changelog): document Renovate updates",
                "tree": new_tree_sha,
                "parents": [head_sha],
            },
        ),
        "created commit",
    )
    new_commit_sha = require_sha(created_commit.get("sha"), "created commit.sha")
    updated_ref = require_mapping(
        write_client.request(
            "PATCH",
            f"repos/{repository}/git/refs/{encoded_ref}",
            {"sha": new_commit_sha, "force": False},
        ),
        "updated ref",
    )
    updated_object = require_mapping(updated_ref.get("object"), "updated ref object")
    if updated_object.get("sha") != new_commit_sha:
        raise AutomationError("updated Renovate ref does not name the created commit")
    return f"created {fragment_path} at {new_commit_sha}"


def write_github_output(path: str, required: bool) -> None:
    if not path:
        raise AutomationError("GITHUB_OUTPUT path is required")
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(f"required={'true' if required else 'false'}\n")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--repository", required=True)
    plan_parser.add_argument("--pr-number", required=True, type=int)
    plan_parser.add_argument("--expected-head", required=True)
    plan_parser.add_argument("--expected-base", required=True)
    plan_parser.add_argument("--output", required=True, type=Path)
    plan_parser.add_argument("--github-output", required=True)
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--plan", required=True, type=Path)
    return result


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    try:
        if args.command == "plan":
            client = GitHubClient(os.environ.get("GH_TOKEN", ""))
            document = plan(
                client,
                args.repository,
                args.pr_number,
                args.expected_head,
                args.expected_base,
                dt.datetime.now(dt.timezone.utc).date(),
            )
            args.output.write_text(
                json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            write_github_output(args.github_output, document["required"])
            print(
                "Renovate changelog fragment is required."
                if document["required"]
                else "A valid added NEXT fragment already satisfies attribution."
            )
        else:
            read_client = GitHubClient(os.environ.get("GH_READ_TOKEN", ""))
            write_client = GitHubClient(os.environ.get("GH_TOKEN", ""))
            document = json.loads(args.plan.read_text(encoding="utf-8"))
            print(apply(read_client, write_client, document))
        return 0
    except (AutomationError, OSError, json.JSONDecodeError) as error:
        print(f"renovate-changelog: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
