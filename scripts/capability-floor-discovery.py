#!/usr/bin/env python3
"""Discover immutable canonical caller pins from a reviewed repository allowlist."""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import yaml

# `python3 -I` does not put the script directory on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from github_identity import same_organization  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALLOWLIST = ROOT / "config/capability-floor-consumers.json"
REPOSITORY_RE = re.compile(r"^Verjson/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
CALLER_PATH_RE = re.compile(r"^\.github/workflows/[A-Za-z0-9._-]+\.ya?ml$")
WORKFLOW_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")
GENERATOR_RE = re.compile(r"^scripts/gen-[A-Za-z0-9._-]+-caller\.sh$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CANONICAL_OWNER = "Verjson"
CANONICAL_REFERENCE_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9._-]+)/\.github/\.github/workflows/"
    r"(?P<workflow>[A-Za-z0-9._-]+\.ya?ml)@(?P<sha>[0-9a-f]{40})$"
)
# The owner is a case-insensitive identity whose display case changed (#1779). Decoy
# detection deliberately uses Unicode case folding, which is wider than the ASCII-only
# owner identity: any spelling that folds to the canonical path is canonical-looking and
# must then pass the exact grammar and identity check, or discovery fails closed.
CANONICAL_MARKER_RE = re.compile(r"verjson/\.github/\.github/workflows/", re.IGNORECASE)
MAX_CALLER_BYTES = 512 * 1024
WORKFLOWS_DIRECTORY = ".github/workflows"
# The contents API returns at most this many directory entries; a listing that
# reaches it may omit the caller, so it cannot prove absence.
CONTENTS_DIRECTORY_LIMIT = 1000


class DiscoveryError(Exception):
    pass


class ExactWorkflowLoader(yaml.SafeLoader):
    pass


def construct_exact_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError:
            raise DiscoveryError("workflow mapping keys must be scalar") from None
        if duplicate:
            raise DiscoveryError(f"workflow contains duplicate YAML-equivalent key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


ExactWorkflowLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_exact_mapping)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DiscoveryError(message)


def read_json(path: Path, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DiscoveryError(f"cannot read {label} ({path}): {error}") from None


def load_allowlist(path: Path) -> list[dict]:
    payload = read_json(path, "consumer allowlist")
    require(isinstance(payload, dict), "consumer allowlist must be a JSON object")
    require(payload.get("schema_version") == 1, "unsupported consumer allowlist schema_version")
    consumers = payload.get("consumers")
    require(isinstance(consumers, list) and consumers, "consumer allowlist is missing or empty")

    parsed: list[dict] = []
    seen_repositories: set[str] = set()
    seen_callers: set[tuple[str, str]] = set()
    for consumer_index, consumer in enumerate(consumers):
        require(isinstance(consumer, dict), f"consumer {consumer_index} must be an object")
        require(
            set(consumer) == {"repository", "callers"},
            f"consumer {consumer_index} must contain exactly repository and callers",
        )
        repository = consumer.get("repository")
        callers = consumer.get("callers")
        require(
            isinstance(repository, str) and REPOSITORY_RE.fullmatch(repository),
            f"consumer {consumer_index} repository is outside the Verjson allowlist boundary",
        )
        require(repository not in seen_repositories, f"duplicate consumer repository {repository}")
        seen_repositories.add(repository)
        require(isinstance(callers, list) and callers, f"consumer {repository} callers are missing or empty")

        parsed_callers = []
        for caller_index, caller in enumerate(callers):
            require(isinstance(caller, dict), f"consumer {repository} caller {caller_index} must be an object")
            require(
                set(caller) == {"path", "generator", "canonical_workflows"},
                f"consumer {repository} caller {caller_index} has unexpected fields",
            )
            path = caller.get("path")
            generator = caller.get("generator")
            workflows = caller.get("canonical_workflows")
            require(isinstance(path, str) and CALLER_PATH_RE.fullmatch(path), f"invalid caller path for {repository}")
            require(
                isinstance(generator, str)
                and GENERATOR_RE.fullmatch(generator)
                and (ROOT / generator).is_file(),
                f"invalid or absent generator for {repository}:{path}",
            )
            require(
                isinstance(workflows, list)
                and workflows
                and len(workflows) == len(set(workflows))
                and all(isinstance(workflow, str) and WORKFLOW_RE.fullmatch(workflow) for workflow in workflows),
                f"invalid canonical workflows for {repository}:{path}",
            )
            key = (repository, path)
            require(key not in seen_callers, f"duplicate caller {repository}:{path}")
            seen_callers.add(key)
            parsed_callers.append({"path": path, "generator": generator, "canonical_workflows": workflows})
        parsed.append({"repository": repository, "callers": parsed_callers})
    return parsed


def run_gh(endpoint: str, fields: dict[str, str] | None = None):
    command = ["gh", "api", "--method", "GET", endpoint]
    for name, value in (fields or {}).items():
        command.extend(["-f", f"{name}={value}"])
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        diagnostic = result.stderr.decode(errors="replace").strip()
        raise DiscoveryError(f"GitHub API read failed for {endpoint}: {diagnostic}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise DiscoveryError(f"GitHub API returned malformed JSON for {endpoint}: {error}") from None


def resolve_default_branch(repository: str) -> tuple[str, str]:
    metadata = run_gh(f"repos/{repository}")
    require(isinstance(metadata, dict), f"repository metadata is malformed for {repository}")
    default_branch = metadata.get("default_branch")
    require(
        isinstance(default_branch, str) and default_branch and len(default_branch) <= 255,
        f"repository default branch is malformed for {repository}",
    )
    commit = run_gh(f"repos/{repository}/commits/{quote(default_branch, safe='')}")
    require(isinstance(commit, dict), f"default-branch commit response is malformed for {repository}")
    source_sha = commit.get("sha")
    require(
        isinstance(source_sha, str) and SHA_RE.fullmatch(source_sha),
        f"default-branch commit sha is malformed for {repository}",
    )
    return default_branch, source_sha


def list_workflow_paths(repository: str, source_sha: str) -> set[str]:
    response = run_gh(f"repos/{repository}/contents/{WORKFLOWS_DIRECTORY}", {"ref": source_sha})
    require(isinstance(response, list), f"workflows directory listing is malformed for {repository}")
    require(
        len(response) < CONTENTS_DIRECTORY_LIMIT,
        f"workflows directory listing may be truncated for {repository}",
    )
    paths = set()
    for entry in response:
        require(
            isinstance(entry, dict) and isinstance(entry.get("path"), str),
            f"workflows directory listing entry is malformed for {repository}",
        )
        paths.add(entry["path"])
    return paths


def read_caller(repository: str, path: str, source_sha: str) -> tuple[str, str]:
    response = run_gh(f"repos/{repository}/contents/{path}", {"ref": source_sha})
    require(isinstance(response, dict), f"contents response is malformed for {repository}:{path}")
    require(response.get("type") == "file", f"allowlisted caller is not a file: {repository}:{path}")
    require(response.get("encoding") == "base64", f"caller encoding is not base64: {repository}:{path}")
    blob_sha = response.get("sha")
    require(
        isinstance(blob_sha, str) and SHA_RE.fullmatch(blob_sha),
        f"caller blob sha is malformed for {repository}:{path}",
    )
    content = response.get("content")
    require(isinstance(content, str), f"caller content is missing for {repository}:{path}")
    try:
        compact_content = "".join(content.split())
        decoded = base64.b64decode(compact_content, validate=True)
    except (ValueError, base64.binascii.Error):
        raise DiscoveryError(f"caller content is not valid base64: {repository}:{path}") from None
    require(len(decoded) <= MAX_CALLER_BYTES, f"caller exceeds {MAX_CALLER_BYTES} bytes: {repository}:{path}")
    try:
        return decoded.decode("utf-8"), blob_sha
    except UnicodeDecodeError:
        raise DiscoveryError(f"caller is not UTF-8: {repository}:{path}") from None


def load_workflow(repository: str, path: str, text: str):
    try:
        tokens = yaml.scan(text)
        for token in tokens:
            if isinstance(token, (yaml.AliasToken, yaml.AnchorToken, yaml.TagToken)):
                raise DiscoveryError(f"workflow aliases, anchors, and tags are unsupported: {repository}:{path}")
        documents = list(yaml.load_all(text, Loader=ExactWorkflowLoader))
    except DiscoveryError:
        raise
    except yaml.YAMLError as error:
        raise DiscoveryError(f"workflow YAML is malformed for {repository}:{path}: {error}") from None
    require(len(documents) == 1, f"workflow must contain exactly one YAML document: {repository}:{path}")
    workflow = documents[0]
    require(isinstance(workflow, dict), f"workflow root is not a mapping: {repository}:{path}")
    jobs = workflow.get("jobs")
    require(isinstance(jobs, dict) and jobs, f"workflow jobs mapping is missing or empty: {repository}:{path}")
    return workflow


def canonical_scalars(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and CANONICAL_MARKER_RE.search(key):
                yield path + ("<mapping-key>",), key
            yield from canonical_scalars(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from canonical_scalars(child, path + (index,))
    elif isinstance(value, str) and CANONICAL_MARKER_RE.search(value):
        yield path, value


def extract_pin(repository: str, path: str, text: str, expected_workflows: list[str]) -> str:
    workflow = load_workflow(repository, path, text)
    references = list(canonical_scalars(workflow))
    require(references, f"caller has no canonical workflow reference: {repository}:{path}")
    matches = []
    for value_path, value in references:
        require(
            len(value_path) == 3 and value_path[0] == "jobs" and value_path[2] == "uses",
            f"canonical reference is outside jobs.<job>.uses: {repository}:{path}",
        )
        require(
            isinstance(value_path[1], str) and value_path[1],
            f"canonical reusable job name is invalid: {repository}:{path}",
        )
        match = CANONICAL_REFERENCE_RE.fullmatch(value)
        require(
            match is not None and same_organization(match.group("owner"), CANONICAL_OWNER),
            f"caller has a malformed or mutable canonical reference: {repository}:{path}",
        )
        matches.append(match)
    workflows = {match.group("workflow") for match in matches}
    pins = {match.group("sha") for match in matches}
    require(
        workflows == set(expected_workflows),
        f"caller canonical target differs from allowlist for {repository}:{path}",
    )
    require(
        len(matches) == len(expected_workflows),
        f"caller canonical reference count differs from allowlist for {repository}:{path}",
    )
    require(len(pins) == 1, f"caller contains mixed canonical pins: {repository}:{path}")
    return next(iter(pins))


def report_absent_caller(absence: dict) -> None:
    print(
        f"::warning title=Generated caller absent::{absence['repository']} is non-conforming: "
        f"allowlisted generated caller {absence['path']} is absent at {absence['source_sha']}; "
        "regenerate it or correct the reviewed allowlist"
    )


def discover(consumers: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    pins = []
    receipts = []
    absent = []
    for consumer in consumers:
        repository = consumer["repository"]
        default_branch, source_sha = resolve_default_branch(repository)
        workflow_paths = list_workflow_paths(repository, source_sha)
        for caller in consumer["callers"]:
            path = caller["path"]
            if path not in workflow_paths:
                absence = {
                    "repository": repository,
                    "default_branch": default_branch,
                    "source_sha": source_sha,
                    "path": path,
                    "generator": caller["generator"],
                    "canonical_workflows": caller["canonical_workflows"],
                    "finding": "allowlisted-caller-absent",
                }
                report_absent_caller(absence)
                absent.append(absence)
                continue
            text, blob_sha = read_caller(repository, path, source_sha)
            pinned_sha = extract_pin(repository, path, text, caller["canonical_workflows"])
            pins.append({"repo": repository, "generator": caller["generator"], "pinned_sha": pinned_sha})
            receipts.append(
                {
                    "repository": repository,
                    "default_branch": default_branch,
                    "source_sha": source_sha,
                    "path": path,
                    "blob_sha": blob_sha,
                    "generator": caller["generator"],
                    "canonical_workflows": caller["canonical_workflows"],
                    "pinned_sha": pinned_sha,
                }
            )
    return pins, receipts, absent


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--print-repositories", action="store_true")
    parser.add_argument("--pins-output", type=Path)
    parser.add_argument("--receipts-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        consumers = load_allowlist(args.allowlist)
        if args.print_repositories:
            require(not args.pins_output and not args.receipts_output, "scope mode does not accept output paths")
            print(",".join(consumer["repository"].split("/", 1)[1] for consumer in consumers))
            return 0
        require(args.pins_output is not None, "--pins-output is required for discovery")
        require(args.receipts_output is not None, "--receipts-output is required for discovery")
        pins, receipts, absent = discover(consumers)
        write_json(args.pins_output, pins)
        write_json(
            args.receipts_output,
            {
                "schema_version": 1,
                "mode": "observe-only",
                "repositories_checked": len(consumers),
                "callers_checked": len(receipts),
                "callers_absent": len(absent),
                "receipts": receipts,
                "absent_callers": absent,
            },
        )
        return 0
    except DiscoveryError as error:
        print(f"ERROR: capability-floor-discovery-invalid: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
