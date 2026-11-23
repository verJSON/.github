#!/usr/bin/env python3
"""Stage the two pinned Alpine packages for the root-context amd64 CLI image."""

import argparse
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
from http.client import HTTPException, HTTPSConnection
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys


ALPINE_HOST = "dl-cdn.alpinelinux.org"
ALPINE_ORIGIN = f"https://{ALPINE_HOST}"
ALPINE_PACKAGE_PATH = "/alpine/v3.23/main/x86_64/"
PACKAGE_FILES = {"bubblewrap.apk", "libcap2.apk"}
MANIFEST_LIMIT = 4096
PACKAGE_LIMIT = 1024 * 1024


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class Package:
    file: str
    request_path: str
    size: int
    digest: str


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(pairs) != len(result):
        raise InputError("package manifest contains duplicate keys")
    return result


def read_packages(manifest: Path) -> list[Package]:
    try:
        fd = os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise InputError("package manifest must be a regular file")
            contents = source.read(MANIFEST_LIMIT + 1)
    except OSError as error:
        raise InputError("package manifest must be a regular file") from error
    if len(contents) > MANIFEST_LIMIT:
        raise InputError("package manifest exceeds the size limit")
    try:
        record = json.loads(contents.decode("utf-8"), object_pairs_hook=unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise InputError("package manifest must be UTF-8 JSON") from error
    if (
        not isinstance(record, dict)
        or record.keys() != {"schemaVersion", "artifacts"}
        or type(record["schemaVersion"]) is not int
        or record["schemaVersion"] != 1
        or not isinstance(record["artifacts"], list)
        or len(record["artifacts"]) != len(PACKAGE_FILES)
    ):
        raise InputError("invalid package manifest shape")

    packages = []
    remaining = PACKAGE_FILES.copy()
    for entry in record["artifacts"]:
        if not isinstance(entry, dict) or entry.keys() != {"file", "url", "size", "sha256"}:
            raise InputError("invalid package entry shape")
        file = entry["file"]
        if not isinstance(file, str) or file not in remaining:
            raise InputError("package manifest must name bubblewrap.apk and libcap2.apk once each")
        remaining.remove(file)

        url = entry["url"]
        prefix = ALPINE_ORIGIN + ALPINE_PACKAGE_PATH + file.removesuffix(".apk")
        if not isinstance(url, str) or not re.fullmatch(
            rf"{re.escape(prefix)}-[0-9]+(?:\.[0-9]+)+-r[0-9]+\.apk", url
        ):
            raise InputError(f"{file} URL must name its official Alpine x86_64 package")
        size = entry["size"]
        if type(size) is not int or not 1 <= size <= PACKAGE_LIMIT:
            raise InputError(f"{file} size is outside the package byte limit")
        digest = entry["sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise InputError(f"{file} requires a lowercase SHA-256 digest")
        packages.append(Package(file, url.removeprefix(ALPINE_ORIGIN), size, digest))
    return packages


def fetch_package(package: Package) -> bytes:
    # A direct HTTPS connection never follows redirects, consults proxies, or loads credentials.
    try:
        with closing(HTTPSConnection(ALPINE_HOST, timeout=15)) as connection:
            connection.request("GET", package.request_path, headers={"Accept": "application/vnd.alpine.apk"})
            with connection.getresponse() as response:
                if response.status != 200:
                    raise InputError(f"{package.file} requires HTTP 200")
                size = response.getheader("Content-Length")
                if size is not None and size != str(package.size):
                    raise InputError(f"{package.file} Content-Length disagrees with the manifest")
                contents = response.read(package.size + 1)
    except (OSError, HTTPException) as error:
        raise InputError(f"{package.file} download failed") from error
    if len(contents) != package.size:
        raise InputError(f"{package.file} downloaded size disagrees with the manifest")
    if sha256(contents).hexdigest() != package.digest:
        raise InputError(f"{package.file} SHA-256 disagrees with the manifest")
    return contents


def stage(context: Path) -> None:
    container = context / "container"
    dockerfile = container / "Dockerfile"
    destination = container / ".bubblewrap-apks"
    if not context.is_absolute() or context.is_symlink() or container.is_symlink() or not container.is_dir():
        raise InputError("package stage requires a non-symlink container directory in the build context")
    if dockerfile.is_symlink() or not dockerfile.is_file():
        raise InputError("package stage requires a regular container/Dockerfile")
    if os.path.lexists(destination):
        raise InputError("reserved package stage already exists")

    # Verify both downloads before exposing a stage that the Docker build could consume.
    packages = read_packages(container / "bubblewrap-apks.json")
    verified = [(package, fetch_package(package)) for package in packages]
    try:
        destination.mkdir(mode=0o700)
    except FileExistsError as error:
        raise InputError("reserved package stage already exists") from error
    created = []
    try:
        for package, contents in verified:
            output = destination / package.file
            fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created.append(output)
            with os.fdopen(fd, "wb") as stream:
                stream.write(contents)
    except BaseException:
        for output in created:
            output.unlink()
        destination.rmdir()
        raise


def main() -> int:
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, type=Path)
    args = parser.parse_args()
    try:
        stage(args.context)
    except InputError as error:
        print(f"::error::Alpine build input: {error}", file=sys.stderr)
        return 1
    except OSError:
        print("::error::Alpine build input: could not write the reserved package stage", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("::error::Alpine build input: staging interrupted", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
