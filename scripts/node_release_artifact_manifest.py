#!/usr/bin/env python3
"""Validate package archives transferred from the unprivileged release job."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import pathlib
import re
import tarfile
from typing import Any


PACKAGE_NAME = re.compile(r"^@[a-z0-9][a-z0-9._~-]*/[a-z0-9][a-z0-9._-]*$")
ARCHIVE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*\.tgz$")
MAX_ARCHIVES = 256
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_ARCHIVE_BYTES = MAX_ARTIFACT_BYTES - 16 * 1024 * 1024
MAX_TOTAL_EXPANDED_BYTES = 8 * 1024 * 1024 * 1024
MAX_MEMBERS = 100_000
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PACKAGE_JSON_BYTES = 1024 * 1024
MAX_TAR_METADATA_BYTES = 1024 * 1024


class _BoundedTarStream:
    def __init__(self, stream, archive_name: str) -> None:
        self.stream = stream
        self.archive_name = archive_name
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError(f"package archive requests an unbounded read: {self.archive_name}")
        remaining = MAX_EXPANDED_BYTES - self.bytes_read
        data = self.stream.read(min(size, remaining + 1))
        self.bytes_read += len(data)
        if self.bytes_read > MAX_EXPANDED_BYTES:
            raise ValueError(f"package archive expands beyond the supported size: {self.archive_name}")
        return data

    def close(self) -> None:
        self.stream.close()


class _BoundedTarInfo(tarfile.TarInfo):
    def _proc_pax(self, archive) -> tarfile.TarInfo:
        if self.size > MAX_TAR_METADATA_BYTES:
            raise ValueError("package archive metadata field exceeds the read limit")
        return super()._proc_pax(archive)

    def _proc_gnulong(self, archive) -> tarfile.TarInfo:
        if self.size > MAX_TAR_METADATA_BYTES:
            raise ValueError("package archive metadata field exceeds the read limit")
        return super()._proc_gnulong(archive)


def _read_json(path: pathlib.Path) -> Any:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError(f"{path.name} is not a regular JSON file within the size limit")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read valid JSON from {path.name}: {error}") from error


def _expected_packages(value: Any) -> dict[str, str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ARCHIVES:
        raise ValueError(f"expected manifest must contain 1 to {MAX_ARCHIVES} packages")

    expected: dict[str, str] = {}
    for package in value:
        if (
            not isinstance(package, dict)
            or set(package) != {"name", "version"}
            or not isinstance(package["name"], str)
            or not PACKAGE_NAME.fullmatch(package["name"])
            or not isinstance(package["version"], str)
            or not package["version"]
            or package["name"] in expected
        ):
            raise ValueError("expected manifest contains an invalid or duplicate package")
        expected[package["name"]] = package["version"]
    return expected


def _archive_package_metadata(archive_path: pathlib.Path) -> tuple[dict[str, Any], int]:
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError(f"package archive is not a regular file: {archive_path.name}")
    archive_size = archive_path.stat().st_size
    if archive_size <= 0 or archive_size > MAX_ARCHIVE_BYTES:
        raise ValueError(f"package archive has an unsupported size: {archive_path.name}")

    package_json: dict[str, Any] | None = None
    seen: set[str] = set()
    expanded_bytes = 0
    try:
        with gzip.open(archive_path, mode="rb") as compressed:
            stream = _BoundedTarStream(compressed, archive_path.name)
            with tarfile.open(fileobj=stream, mode="r|", tarinfo=_BoundedTarInfo) as archive:
                for index, member in enumerate(archive):
                    if index >= MAX_MEMBERS:
                        raise ValueError(f"package archive contains too many entries: {archive_path.name}")
                    name = member.name
                    if (
                        not name
                        or "\\" in name
                        or name.startswith("/")
                        or "\x00" in name
                        or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
                        or not name.startswith("package/")
                        or name in seen
                    ):
                        raise ValueError(f"package archive contains an unsafe or duplicate path: {archive_path.name}")
                    seen.add(name)
                    if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                        raise ValueError(f"package archive contains a link or special file: {archive_path.name}")
                    expanded_bytes += member.size
                    if expanded_bytes > MAX_EXPANDED_BYTES:
                        raise ValueError(f"package archive expands beyond the supported size: {archive_path.name}")
                    if name.rstrip("/") == "package/package.json":
                        if not member.isfile() or member.size > MAX_PACKAGE_JSON_BYTES:
                            raise ValueError(f"package archive has an invalid package.json: {archive_path.name}")
                        package_stream = archive.extractfile(member)
                        if package_stream is None:
                            raise ValueError(f"package archive omits readable package.json: {archive_path.name}")
                        raw = package_stream.read(MAX_PACKAGE_JSON_BYTES + 1)
                        if len(raw) > MAX_PACKAGE_JSON_BYTES:
                            raise ValueError(f"package archive package.json is too large: {archive_path.name}")
                        package_json = json.loads(raw.decode("utf-8"))
    except (OSError, tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not inspect package archive {archive_path.name}: {error}") from error

    if not isinstance(package_json, dict):
        raise ValueError(f"package archive has no package/package.json: {archive_path.name}")
    return package_json, stream.bytes_read

def validate_artifacts(
    artifact_dir: pathlib.Path,
    expected_value: Any,
) -> list[dict[str, str]]:
    if artifact_dir.is_symlink() or not artifact_dir.is_dir():
        raise ValueError("artifact directory must be a real directory")
    expected = _expected_packages(expected_value)
    manifest_path = artifact_dir / "package-artifacts.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("package artifact manifest is missing or is not a regular file")
    artifact_manifest = _read_json(manifest_path)
    if not isinstance(artifact_manifest, list) or len(artifact_manifest) != len(expected):
        raise ValueError("package artifact manifest does not match the expected package count")

    validated: list[dict[str, str]] = []
    seen_names: set[str] = set()
    seen_files: set[str] = {manifest_path.name}
    total_archive_bytes = 0
    total_expanded_bytes = 0
    for item in artifact_manifest:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "version", "integrity", "filename"}
            or not isinstance(item["name"], str)
            or not isinstance(item["version"], str)
            or not isinstance(item["integrity"], str)
            or not isinstance(item["filename"], str)
        ):
            raise ValueError("package artifact manifest contains an invalid entry")
        name = item["name"]
        version = item["version"]
        integrity = item["integrity"]
        filename = item["filename"]
        if (
            name not in expected
            or expected[name] != version
            or name in seen_names
            or not PACKAGE_NAME.fullmatch(name)
            or not ARCHIVE_NAME.fullmatch(filename)
            or pathlib.PurePosixPath(filename).name != filename
            or filename in seen_files
            or not re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", integrity)
        ):
            raise ValueError("package artifact entry does not match trusted package identity and version")

        archive_path = artifact_dir / filename
        if archive_path.is_symlink() or not archive_path.is_file():
            raise ValueError(f"package archive is missing or is not a regular file: {filename}")
        total_archive_bytes += archive_path.stat().st_size
        if total_archive_bytes > MAX_TOTAL_ARCHIVE_BYTES:
            raise ValueError("package archives exceed the aggregate compressed size limit")
        hasher = hashlib.sha512()
        with archive_path.open("rb") as archive_file:
            for chunk in iter(lambda: archive_file.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = base64.b64encode(hasher.digest()).decode("ascii")
        if f"sha512-{digest}" != integrity:
            raise ValueError(f"package archive integrity does not match its manifest: {filename}")
        package_json, archive_expanded_bytes = _archive_package_metadata(archive_path)
        total_expanded_bytes += archive_expanded_bytes
        if total_expanded_bytes > MAX_TOTAL_EXPANDED_BYTES:
            raise ValueError("package archives exceed the aggregate expanded size limit")
        if package_json.get("name") != name or package_json.get("version") != version:
            raise ValueError(f"package archive identity does not match its manifest: {filename}")

        seen_names.add(name)
        seen_files.add(filename)
        validated.append({
            "name": name,
            "version": version,
            "integrity": integrity,
            "filename": filename,
            "path": str(archive_path),
        })

    if seen_names != set(expected):
        raise ValueError("package artifact manifest omits an expected package")
    entries = list(artifact_dir.iterdir())
    if {path.name for path in entries} != seen_files or any(path.is_symlink() or not path.is_file() for path in entries):
        raise ValueError("artifact directory contains unexpected files or non-regular entries")
    return validated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", required=True, type=pathlib.Path)
    parser.add_argument("--expected-manifest", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    expected = _read_json(args.expected_manifest)
    validated = validate_artifacts(args.artifact_dir, expected)
    args.output.write_text(json.dumps(validated, separators=(",", ":")) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
