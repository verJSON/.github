#!/usr/bin/env python3
"""Fail-closed allowlist validation for Pulumi's private npm packages."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit


MAX_LOCKFILE_BYTES = 32 * 1024 * 1024
MAX_APPROVED_PACKAGES = 128
PRIVATE_PACKAGE_RE = re.compile(r"@verjson/[a-z0-9][a-z0-9._-]{0,127}\Z")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("package-lock.json contains a duplicate JSON key")
        result[key] = value
    return result


def _approved_packages(value: str) -> frozenset[str]:
    names = [line.strip() for line in value.splitlines() if line.strip()]
    if not names or len(names) > MAX_APPROVED_PACKAGES:
        raise ValueError("private-node-packages must list between 1 and 128 packages")
    if len(set(names)) != len(names):
        raise ValueError("private-node-packages contains a duplicate package name")
    if any(not PRIVATE_PACKAGE_RE.fullmatch(name) for name in names):
        raise ValueError("private-node-packages contains an invalid package name")
    return frozenset(names)


def _package_name_from_path(package_path: str) -> str | None:
    if (
        "\\" in package_path
        or package_path.startswith("/")
        or any(part in {"", ".", ".."} for part in package_path.split("/"))
    ):
        raise ValueError("package-lock.json contains an invalid package path")
    parts = PurePosixPath(package_path).parts
    if any(part in {".", ".."} for part in parts):
        raise ValueError("package-lock.json contains an invalid package path")

    package_names: list[str] = []
    index = 0
    while index < len(parts):
        if parts[index] != "node_modules":
            index += 1
            continue
        if index + 1 >= len(parts):
            raise ValueError("package-lock.json contains an invalid package path")
        name = parts[index + 1]
        if name.startswith("@"):
            if index + 2 >= len(parts):
                raise ValueError("package-lock.json contains an invalid scoped package path")
            name = f"{name}/{parts[index + 2]}"
            index += 3
        else:
            index += 2
        package_names.append(name)
    return package_names[-1] if package_names else None


def _validate_private_archive(name: str, package: dict[str, Any]) -> tuple[str, str]:
    resolved = package.get("resolved")
    if not isinstance(resolved, str):
        raise ValueError("private npm package missing its resolved archive URL")
    if any(ord(character) <= 32 or ord(character) == 127 for character in resolved):
        raise ValueError("private npm package has an invalid archive URL")
    try:
        parsed = urlsplit(resolved)
        port = parsed.port
    except ValueError as error:
        raise ValueError("private npm package has an invalid archive URL") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname != "npm.pkg.github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("private npm package has an invalid archive URL")
    version = package.get("version")
    if (
        not isinstance(version, str)
        or not version
        or not version.isascii()
        or any(ord(character) <= 32 or ord(character) == 127 for character in version)
        or "/" in version
        or "\\" in version
    ):
        raise ValueError("private npm package has an invalid archive URL")
    decoded_path = unquote(parsed.path)
    parts = decoded_path.split("/")
    if (
        len(parts) != 6
        or parts[1] != "download"
        or not parts[2].startswith("@")
        or not parts[2].isascii()
        or any(part in ("", ".", "..") for part in parts[1:])
        or resolved != f"https://npm.pkg.github.com{decoded_path}"
        or parts[4] != version
    ):
        raise ValueError("private npm package has an invalid archive URL")
    registry_name = f"{parts[2].lower()}/{parts[3]}"
    if registry_name != name:
        raise ValueError("private npm package has an invalid archive URL")
    archive_name = parts[5]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", archive_name):
        raise ValueError("private npm package has an invalid archive URL")

    integrity = package.get("integrity")
    if not isinstance(integrity, str) or not re.fullmatch(
        r"sha512-[A-Za-z0-9+/]{86}==", integrity
    ):
        raise ValueError("private npm package is missing SHA-512 integrity")
    try:
        digest = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("private npm package is missing SHA-512 integrity") from error
    if len(digest) != 64:
        raise ValueError("private npm package is missing SHA-512 integrity")
    return resolved, digest.hex()

def _read_lockfile(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ValueError("package-lock.json is unavailable") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_LOCKFILE_BYTES:
        raise ValueError("package-lock.json has an invalid file type or exceeds its size limit")
    try:
        raw = path.read_bytes()
        document = json.loads(raw, object_pairs_hook=_object_without_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("package-lock.json is invalid JSON") from error
    if len(raw) > MAX_LOCKFILE_BYTES:
        raise ValueError("package-lock.json exceeds its size limit")
    if not isinstance(document, dict):
        raise ValueError("package-lock.json has an invalid shape")
    version = document.get("lockfileVersion")
    if type(version) is not int or version not in {2, 3}:
        raise ValueError("private npm acquisition requires lockfile version 2 or 3")
    packages = document.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("package-lock.json has an invalid packages map")
    return packages


def validate_private_node_packages(
    lockfile_path: Path, approved_value: str
) -> tuple[tuple[str, str], ...]:
    approved = _approved_packages(approved_value)
    packages = _read_lockfile(lockfile_path)
    private_names: set[str] = set()
    private_archives: dict[str, str] = {}
    for package_path, metadata in packages.items():
        if not isinstance(package_path, str) or not isinstance(metadata, dict):
            raise ValueError("package-lock.json has an invalid package entry")
        if package_path == "":
            continue
        path_name = _package_name_from_path(package_path)
        declared_name = metadata.get("name")
        if declared_name is not None and not isinstance(declared_name, str):
            raise ValueError("package-lock.json has an invalid package name")
        name = declared_name or path_name
        resolved = metadata.get("resolved")
        is_github_package = (
            isinstance(resolved, str) and urlsplit(resolved).hostname == "npm.pkg.github.com"
        )
        if isinstance(name, str) and name.startswith("@verjson/"):
            if (
                path_name != name
                or (declared_name is not None and path_name != declared_name)
                or not PRIVATE_PACKAGE_RE.fullmatch(name)
            ):
                raise ValueError("package-lock.json has an invalid private package path")
            resolved, digest_hex = _validate_private_archive(name, metadata)
            previous_digest = private_archives.setdefault(resolved, digest_hex)
            if previous_digest != digest_hex:
                raise ValueError("private npm package URL has conflicting lock integrity")
            private_names.add(name)
        elif is_github_package:
            raise ValueError("package-lock.json contains an unapproved registry package")
    if private_names != approved:
        raise ValueError("private-node-packages does not exactly match the private package lock entries")
    return tuple(sorted(private_archives.items()))

def main() -> int:
    approved = os.environ.get("APPROVED_PRIVATE_NODE_PACKAGES", "")
    try:
        entries = validate_private_node_packages(Path("package-lock.json"), approved)
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    for resolved, digest_hex in entries:
        print(f"{resolved}\t{digest_hex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
