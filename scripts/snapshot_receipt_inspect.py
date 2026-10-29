#!/usr/bin/env python3
"""Offline consistency checks for an unauthenticated snapshot receipt prototype.

A successful inspection does not authenticate the producer or authorize any action.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


class ReceiptError(ValueError):
    """A receipt or its local Git evidence is inconsistent."""


_FIELDS = {
    "schemaVersion", "kind", "repositoryId", "producerWorkflow", "runId", "runAttempt",
    "sourceCommit", "version", "tagRef", "tagObject", "snapshotCommit", "snapshotTree",
    "changelogSha256",
}
_EXPECTED_FIELDS = {"repositoryId", "producerWorkflow", "runId", "runAttempt", "sourceCommit", "version"}
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_MAX_RECEIPT = 16 * 1024
_MAX_SNAPSHOT = 16 * 1024 * 1024
_MAX_COMMIT = 1024 * 1024


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError("duplicate receipt field")
        result[key] = value
    return result


def decode_receipt(data: bytes) -> dict:
    if not isinstance(data, bytes) or not 0 < len(data) <= _MAX_RECEIPT:
        raise ReceiptError("receipt size is invalid")
    try:
        receipt = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ReceiptError("invalid number")))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReceiptError("receipt is invalid JSON") from exc
    if not isinstance(receipt, dict) or set(receipt) != _FIELDS:
        raise ReceiptError("receipt fields are invalid")
    if type(receipt["schemaVersion"]) is not int or receipt["schemaVersion"] != 1 or receipt["kind"] != "snapshot-receipt-prototype":
        raise ReceiptError("receipt version or kind is invalid")
    for field in ("repositoryId", "runId", "runAttempt"):
        if not isinstance(receipt[field], str) or not _ID.fullmatch(receipt[field]):
            raise ReceiptError(f"{field} is invalid")
    workflow = receipt["producerWorkflow"]
    if not isinstance(workflow, str) or not 1 <= len(workflow) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in workflow):
        raise ReceiptError("producer workflow is invalid")
    version = receipt["version"]
    if not isinstance(version, str) or not _VERSION.fullmatch(version) or ".." in version or version.endswith("."):
        raise ReceiptError("version is invalid")
    if receipt["tagRef"] != f"refs/tags/{version}":
        raise ReceiptError("tag ref does not match version")
    if not isinstance(receipt["changelogSha256"], str) or not _DIGEST.fullmatch(receipt["changelogSha256"]):
        raise ReceiptError("changelog digest is invalid")
    for field in ("sourceCommit", "tagObject", "snapshotCommit", "snapshotTree"):
        if not isinstance(receipt[field], str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", receipt[field]):
            raise ReceiptError(f"{field} is invalid")
    return receipt


def inspect_snapshot_receipt(data: bytes, *, expected: dict, repository: Path) -> None:
    """Check consistency only. This never authenticates or authorizes a release."""
    receipt = decode_receipt(data)
    if not isinstance(expected, dict) or set(expected) != _EXPECTED_FIELDS:
        raise ReceiptError("expected context is incomplete")
    if any(expected[key] != receipt[key] for key in _EXPECTED_FIELDS):
        raise ReceiptError("receipt does not match independent expected context")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_NO_REPLACE_OBJECTS": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_LAZY_FETCH": "1",
                "GIT_TERMINAL_PROMPT": "0"})

    def git(*args: str) -> bytes:
        try:
            return subprocess.run(["git", "-C", str(repository), *args], check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, timeout=10).stdout
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ReceiptError("Git evidence is unavailable") from exc

    try:
        promisor = subprocess.run(
            ["git", "-C", str(repository), "config", "--includes", "--get-regexp",
             r"^(extensions\.partialclone|remote\..*\.promisor)$"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReceiptError("Git configuration is unavailable") from exc
    if promisor.returncode != 1 or promisor.stdout:
        raise ReceiptError("partial or unreadable Git configuration is not supported")

    object_format = git("rev-parse", "--show-object-format").strip()
    lengths = {b"sha1": 40, b"sha256": 64}
    if object_format not in lengths or any(len(receipt[k]) != lengths[object_format] for k in ("sourceCommit", "tagObject", "snapshotCommit", "snapshotTree")):
        raise ReceiptError("object format mismatch")
    if git("check-ref-format", receipt["tagRef"]) != b"":
        raise ReceiptError("invalid tag ref")
    if git("rev-parse", "--verify", receipt["tagRef"]).strip().decode("ascii") != receipt["tagObject"]:
        raise ReceiptError("observed tag ref differs")
    if git("cat-file", "-t", receipt["tagObject"]).strip() != b"tag":
        raise ReceiptError("tag must be annotated")
    if int(git("cat-file", "-s", receipt["tagObject"]).strip()) > 65536:
        raise ReceiptError("tag object is too large")
    tag = git("cat-file", "-p", receipt["tagObject"]).split(b"\n\n", 1)[0].splitlines()
    if tag[:3] != [f"object {receipt['snapshotCommit']}".encode(), b"type commit", f"tag {receipt['version']}".encode()]:
        raise ReceiptError("tag object binding differs")
    commit_id = receipt["snapshotCommit"]
    if git("cat-file", "-t", commit_id).strip() != b"commit":
        raise ReceiptError("snapshot object is not a commit")
    if int(git("cat-file", "-s", commit_id).strip()) > _MAX_COMMIT:
        raise ReceiptError("snapshot commit is too large")
    raw_commit = git("cat-file", "commit", commit_id)
    header, separator, _ = raw_commit.partition(b"\n\n")
    if not separator:
        raise ReceiptError("snapshot commit header is malformed")
    lines = header.split(b"\n")
    trees = [line for line in lines if line == b"tree" or line.startswith(b"tree ")]
    parents = [line for line in lines if line == b"parent" or line.startswith(b"parent ")]
    if trees != [f"tree {receipt['snapshotTree']}".encode()]:
        raise ReceiptError("snapshot tree differs or is ambiguous")
    if parents != [f"parent {receipt['sourceCommit']}".encode()]:
        raise ReceiptError("snapshot commit does not have the expected sole parent")
    path = f"CHANGELOG/{receipt['version']}.md"
    entry = git("ls-tree", "-z", receipt["snapshotCommit"], "--", path)
    match = re.fullmatch(rb"100644 blob ([0-9a-f]+)\t" + re.escape(path.encode()) + rb"\x00", entry)
    if not match:
        raise ReceiptError("snapshot path is not a regular blob")
    blob = match.group(1).decode("ascii")
    if int(git("cat-file", "-s", blob).strip()) > _MAX_SNAPSHOT:
        raise ReceiptError("snapshot blob is too large")
    content = git("cat-file", "blob", blob)
    if "sha256:" + hashlib.sha256(content).hexdigest() != receipt["changelogSha256"]:
        raise ReceiptError("snapshot content digest differs")
