#!/usr/bin/env python3
"""Stage the exact locked Prisma schema engine for the offline Node CI sandbox."""

import argparse
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path

PRISMA_HOST = "binaries.prisma.sh"
ENGINE_NAME = "schema-engine"
OWNER_NAME = ".owner"
MAX_LOCKFILE_BYTES = 32 * 1024 * 1024
MAX_CHECKSUM_BYTES = 256
MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_ENGINE_BYTES = 64 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024


class PrismaEngineError(Exception):
    pass


@dataclass(frozen=True)
class EngineSpec:
    package_version: str
    engine_version: str
    commit: str


def _duplicate_free_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PrismaEngineError("Prisma lockfile contains a duplicate JSON key")
        result[key] = value
    return result


def _package_records(packages, package_name):
    suffix = f"node_modules/{package_name}"
    return [
        value
        for key, value in packages.items()
        if isinstance(key, str) and (key == suffix or key.endswith(f"/{suffix}"))
    ]


def _legacy_lock_contains_prisma(dependencies):
    if not isinstance(dependencies, dict):
        return False
    for package_name, package in dependencies.items():
        if package_name in {"prisma", "@prisma/engines"}:
            return True
        if isinstance(package, dict) and _legacy_lock_contains_prisma(package.get("dependencies")):
            return True
    return False


def parse_engine_spec(lockfile_bytes):
    if not isinstance(lockfile_bytes, bytes) or len(lockfile_bytes) > MAX_LOCKFILE_BYTES:
        raise PrismaEngineError("Prisma lockfile exceeds the supported size bound")
    try:
        lockfile = json.loads(lockfile_bytes, object_pairs_hook=_duplicate_free_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PrismaEngineError("Prisma lockfile is not valid JSON") from error
    if not isinstance(lockfile, dict):
        raise PrismaEngineError("Prisma lockfile root must be an object")

    lockfile_version = lockfile.get("lockfileVersion")
    if type(lockfile_version) is not int or lockfile_version not in {2, 3}:
        packages = lockfile.get("packages")
        packages_contain_prisma = isinstance(packages, dict) and any(
            _package_records(packages, package_name)
            for package_name in ("prisma", "@prisma/engines")
        )
        if packages_contain_prisma or _legacy_lock_contains_prisma(lockfile.get("dependencies")):
            raise PrismaEngineError("Prisma engine staging requires npm lockfile version 2 or 3")
        return None

    packages = lockfile.get("packages")
    if not isinstance(packages, dict):
        raise PrismaEngineError("supported npm lockfile is missing its packages map")

    engines_records = _package_records(packages, "@prisma/engines")
    prisma_records = _package_records(packages, "prisma")
    if not engines_records and not prisma_records:
        return None
    if len(engines_records) != 1:
        raise PrismaEngineError("Prisma lockfile must contain exactly one @prisma/engines package")

    engines = engines_records[0]
    if not isinstance(engines, dict):
        raise PrismaEngineError("locked @prisma/engines metadata is malformed")
    package_version = engines.get("version")
    if not isinstance(package_version, str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", package_version
    ):
        raise PrismaEngineError("locked @prisma/engines version is malformed")

    if prisma_records:
        if len(prisma_records) != 1 or not isinstance(prisma_records[0], dict):
            raise PrismaEngineError("Prisma lockfile has ambiguous prisma package metadata")
        prisma = prisma_records[0]
        dependencies = prisma.get("dependencies")
        if (
            prisma.get("version") != package_version
            or not isinstance(dependencies, dict)
            or dependencies.get("@prisma/engines") != package_version
        ):
            raise PrismaEngineError("locked prisma and @prisma/engines versions do not agree")

    engine_dependencies = engines.get("dependencies")
    if not isinstance(engine_dependencies, dict):
        raise PrismaEngineError("locked @prisma/engines package is missing its dependencies")
    engine_version = engine_dependencies.get("@prisma/engines-version")
    if not isinstance(engine_version, str):
        raise PrismaEngineError("locked @prisma/engines-version is missing")

    matching_version_records = [
        record
        for record in _package_records(packages, "@prisma/engines-version")
        if isinstance(record, dict) and record.get("version") == engine_version
    ]
    if not matching_version_records:
        raise PrismaEngineError("locked @prisma/engines-version package does not match its dependency")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-[0-9]+\.([0-9a-f]{40})", engine_version):
        raise PrismaEngineError("locked Prisma engine commit is malformed")

    return EngineSpec(
        package_version=package_version,
        engine_version=engine_version,
        commit=engine_version.rsplit(".", 1)[1],
    )


def _os_release_values(os_release_text):
    values = {}
    for line in os_release_text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z0-9_]+", key):
            continue
        try:
            parsed = shlex.split(value, posix=True)
        except ValueError as error:
            raise PrismaEngineError("runner operating system metadata is malformed") from error
        if len(parsed) != 1:
            raise PrismaEngineError("runner operating system metadata is malformed")
        values[key] = parsed[0]
    return values


def resolve_runner_target(
    runner_environment,
    runner_os,
    runner_arch,
    os_release_text,
    openssl_version_text,
):
    if (
        runner_environment != "github-hosted"
        or runner_os != "Linux"
        or runner_arch != "X64"
    ):
        raise PrismaEngineError("Prisma schema engine staging requires a GitHub-hosted Linux x64 runner")
    os_values = _os_release_values(os_release_text)
    if os_values.get("ID") != "ubuntu" or os_values.get("VERSION_ID") != "24.04":
        raise PrismaEngineError("Prisma schema engine staging supports only Ubuntu 24.04")
    if not re.search(r"\bOpenSSL 3\.0\.[0-9]+\b", openssl_version_text):
        raise PrismaEngineError("Ubuntu 24.04 runner does not expose the supported OpenSSL 3.0 ABI")
    return "debian-openssl-3.0.x"


def _run_id_parts(run_id, run_attempt):
    if not re.fullmatch(r"[1-9][0-9]{0,19}", str(run_id)):
        raise PrismaEngineError("GitHub run ID is malformed")
    if not re.fullmatch(r"[1-9][0-9]{0,5}", str(run_attempt)):
        raise PrismaEngineError("GitHub run attempt is malformed")
    return str(run_id), str(run_attempt)


def _stage_directory_name(run_id, run_attempt):
    return f"verjson-prisma-schema-engine-{run_id}-{run_attempt}"


def _open_directory(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise PrismaEngineError("runner temporary directory is not a trusted directory")
    try:
        resolved = path.resolve(strict=True)
        if resolved != path or not resolved.is_dir():
            raise PrismaEngineError("runner temporary directory is not canonical")
        return os.open(
            resolved,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        raise PrismaEngineError("runner temporary directory is unavailable") from error


def _read_regular_file(path, maximum_bytes, *, missing_ok=False):
    path = Path(path)
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise PrismaEngineError("Prisma package-lock.json is missing")
    except OSError as error:
        raise PrismaEngineError("Prisma package-lock.json cannot be inspected") from error
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise PrismaEngineError("Prisma package-lock.json must be a regular file")
    if path_stat.st_size > maximum_bytes:
        raise PrismaEngineError("Prisma package-lock.json exceeds the supported size bound")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (path_stat.st_dev, path_stat.st_ino):
                raise PrismaEngineError("Prisma package-lock.json changed while opening")
            content = stream.read(maximum_bytes + 1)
    except OSError as error:
        raise PrismaEngineError("Prisma package-lock.json cannot be read safely") from error
    if len(content) > maximum_bytes:
        raise PrismaEngineError("Prisma package-lock.json exceeds the supported size bound")
    return content


def _owner_marker(run_id, run_attempt):
    return f"verjson-prisma-schema-engine-v1\nrun_id={run_id}\nrun_attempt={run_attempt}\n".encode()


def _write_owned_marker(stage_fd, run_id, run_attempt):
    descriptor = os.open(
        OWNER_NAME,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=stage_fd,
    )
    with os.fdopen(descriptor, "wb") as marker:
        marker.write(_owner_marker(run_id, run_attempt))
        marker.flush()
        os.fsync(marker.fileno())


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, _file, _code, _message, _headers, _new_url):
        raise PrismaEngineError("Prisma binary host redirects are not supported")


def _open_prisma_url(request, *, timeout):
    return urllib.request.build_opener(_RejectRedirect()).open(request, timeout=timeout)


def _download_bounded(url, maximum_bytes, opener):
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/octet-stream", "User-Agent": "VerJSON-Node-CI"},
    )
    try:
        with opener(request, timeout=20) as response:
            if response.geturl() != url or getattr(response, "status", 200) != 200:
                raise PrismaEngineError("Prisma binary endpoint returned an unexpected response")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                if not re.fullmatch(r"[0-9]+", content_length):
                    raise PrismaEngineError("Prisma binary endpoint returned an invalid content length")
                if int(content_length) > maximum_bytes:
                    raise PrismaEngineError("Prisma binary exceeds its download size bound")
            chunks = []
            total = 0
            while True:
                chunk = response.read(min(READ_CHUNK_BYTES, maximum_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum_bytes:
                    raise PrismaEngineError("Prisma binary exceeds its download size bound")
                chunks.append(chunk)
            if content_length is not None and total != int(content_length):
                raise PrismaEngineError("Prisma binary response ended before its declared size")
            if not total:
                raise PrismaEngineError("Prisma binary endpoint returned an empty response")
            return b"".join(chunks)
    except PrismaEngineError:
        raise
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        raise PrismaEngineError("Prisma binary download failed") from error


def _parse_published_checksum(content, target):
    try:
        lines = content.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise PrismaEngineError("Prisma checksum is not ASCII") from error
    if len(lines) != 1:
        raise PrismaEngineError("Prisma checksum must contain exactly one line")
    match = re.fullmatch(
        r"([0-9a-f]{64})  \./([a-z0-9.-]+)/schema-engine\.gz",
        lines[0],
    )
    if match is None or match.group(2) != target:
        raise PrismaEngineError("Prisma checksum does not name the expected engine artifact")
    return match.group(1)


def _write_engine(stage_fd, compressed, expected_digest):
    actual_digest = hashlib.sha256(compressed).hexdigest()
    if actual_digest != expected_digest:
        raise PrismaEngineError("Prisma schema engine SHA-256 does not match the published checksum")
    descriptor = os.open(
        ENGINE_NAME,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        0o700,
        dir_fd=stage_fd,
    )
    total = 0
    prefix = bytearray()
    try:
        with os.fdopen(descriptor, "wb") as output:
            try:
                import io

                with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as engine_stream:
                    while True:
                        chunk = engine_stream.read(READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_ENGINE_BYTES:
                            raise PrismaEngineError("decompressed Prisma schema engine exceeds its size bound")
                        if len(prefix) < 20:
                            prefix.extend(chunk[: 20 - len(prefix)])
                        output.write(chunk)
            except (OSError, EOFError, zlib.error) as error:
                raise PrismaEngineError("Prisma schema engine gzip payload is invalid") from error
            if (
                total < 20
                or bytes(prefix[:4]) != b"\x7fELF"
                or prefix[4] != 2
                or prefix[5] != 1
                or int.from_bytes(prefix[18:20], "little") != 62
            ):
                raise PrismaEngineError("Prisma schema engine is not an x86_64 Linux executable")
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o555)
    except BaseException:
        try:
            os.unlink(ENGINE_NAME, dir_fd=stage_fd)
        except OSError:
            pass
        raise


def _download_and_stage(spec, target, stage_fd, opener):
    if re.fullmatch(r"[0-9a-f]{40}", spec.commit) is None:
        raise PrismaEngineError("locked Prisma engine commit is malformed")
    base_url = f"https://{PRISMA_HOST}/all_commits/{spec.commit}/{target}/schema-engine.gz"
    checksum_url = f"{base_url}.sha256"
    checksum_bytes = _download_bounded(checksum_url, MAX_CHECKSUM_BYTES, opener)
    expected_digest = _parse_published_checksum(checksum_bytes, target)
    compressed = _download_bounded(base_url, MAX_COMPRESSED_BYTES, opener)
    _write_engine(stage_fd, compressed, expected_digest)


def stage_engine(
    lockfile,
    runner_temp,
    *,
    run_id,
    run_attempt,
    runner_environment,
    runner_os,
    runner_arch,
    os_release_text,
    openssl_version_text,
    opener=None,
):
    run_id, run_attempt = _run_id_parts(run_id, run_attempt)
    lockfile_content = _read_regular_file(
        lockfile,
        MAX_LOCKFILE_BYTES,
        missing_ok=True,
    )
    if lockfile_content is None:
        return False
    spec = parse_engine_spec(lockfile_content)
    if spec is None:
        return False
    target = resolve_runner_target(
        runner_environment,
        runner_os,
        runner_arch,
        os_release_text,
        openssl_version_text,
    )
    open_url = opener or _open_prisma_url
    directory_name = _stage_directory_name(run_id, run_attempt)
    runner_temp_fd = _open_directory(runner_temp)
    stage_fd = None
    marker_written = False
    try:
        try:
            os.mkdir(directory_name, mode=0o700, dir_fd=runner_temp_fd)
        except FileExistsError as error:
            raise PrismaEngineError("run-scoped Prisma engine staging path already exists") from error
        stage_fd = os.open(
            directory_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=runner_temp_fd,
        )
        _write_owned_marker(stage_fd, run_id, run_attempt)
        marker_written = True
        _download_and_stage(spec, target, stage_fd, open_url)
        return True
    except BaseException:
        if stage_fd is not None:
            os.close(stage_fd)
            stage_fd = None
        if marker_written:
            cleanup_engine(runner_temp, run_id=run_id, run_attempt=run_attempt)
        else:
            try:
                os.rmdir(directory_name, dir_fd=runner_temp_fd)
            except OSError:
                pass
        raise
    finally:
        if stage_fd is not None:
            os.close(stage_fd)
        os.close(runner_temp_fd)


def cleanup_engine(runner_temp, *, run_id, run_attempt):
    run_id, run_attempt = _run_id_parts(run_id, run_attempt)
    directory_name = _stage_directory_name(run_id, run_attempt)
    runner_temp_fd = _open_directory(runner_temp)
    try:
        try:
            stage_fd = os.open(
                directory_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=runner_temp_fd,
            )
        except FileNotFoundError:
            return False
        except OSError as error:
            raise PrismaEngineError("refusing cleanup of an unsafe Prisma engine staging path") from error
        try:
            names = set(os.listdir(stage_fd))
            if names - {OWNER_NAME, ENGINE_NAME}:
                raise PrismaEngineError("refusing cleanup of unexpected Prisma engine staging files")

            marker_content = None
            if OWNER_NAME in names:
                try:
                    marker_fd = os.open(
                        OWNER_NAME,
                        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=stage_fd,
                    )
                except OSError as error:
                    raise PrismaEngineError("refusing cleanup with an invalid Prisma engine owner marker") from error
                with os.fdopen(marker_fd, "rb") as marker:
                    marker_stat = os.fstat(marker.fileno())
                    if not stat.S_ISREG(marker_stat.st_mode):
                        raise PrismaEngineError("refusing cleanup with an invalid Prisma engine owner marker")
                    marker_content = marker.read(256)
            # The exact run-scoped directory can be empty if cancellation interrupts setup.
            elif names:
                raise PrismaEngineError("refusing cleanup without the Prisma engine owner marker")

            expected_marker = _owner_marker(run_id, run_attempt)
            if marker_content is not None and marker_content != expected_marker:
                # A prefix is safe only before the engine has been staged.
                if names != {OWNER_NAME} or not expected_marker.startswith(marker_content):
                    raise PrismaEngineError("refusing cleanup of a Prisma engine stage owned by another run")
        finally:
            os.close(stage_fd)
        if not shutil.rmtree.avoids_symlink_attacks:
            raise PrismaEngineError("safe Prisma engine cleanup is unavailable on this runner")
        shutil.rmtree(directory_name, dir_fd=runner_temp_fd)
        return True
    except PrismaEngineError:
        raise
    except OSError as error:
        raise PrismaEngineError("Prisma engine staging cleanup failed") from error
    finally:
        os.close(runner_temp_fd)


def _runner_facts():
    try:
        os_release_text = Path("/etc/os-release").read_text(encoding="utf-8")
        openssl = subprocess.run(
            ["/usr/bin/openssl", "version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PrismaEngineError("trusted runner platform metadata is unavailable") from error
    return os_release_text, openssl.stdout.strip()


def _write_output(available):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise PrismaEngineError("GitHub step output file is unavailable")
    try:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"available={'true' if available else 'false'}\n")
    except OSError as error:
        raise PrismaEngineError("GitHub step output file cannot be updated") from error


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--workspace", required=True)
    stage_parser.add_argument("--runner-temp", required=True)
    stage_parser.add_argument("--run-id", required=True)
    stage_parser.add_argument("--run-attempt", required=True)
    stage_parser.add_argument("--runner-environment", required=True)
    stage_parser.add_argument("--runner-os", required=True)
    stage_parser.add_argument("--runner-arch", required=True)
    cleanup_parser = commands.add_parser("cleanup")
    cleanup_parser.add_argument("--runner-temp", required=True)
    cleanup_parser.add_argument("--run-id", required=True)
    cleanup_parser.add_argument("--run-attempt", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "stage":
            lockfile = Path(arguments.workspace) / "package-lock.json"
            lock_bytes = _read_regular_file(lockfile, MAX_LOCKFILE_BYTES, missing_ok=True)
            if lock_bytes is None or parse_engine_spec(lock_bytes) is None:
                available = False
            else:
                os_release_text, openssl_version_text = _runner_facts()
                available = stage_engine(
                    lockfile,
                    arguments.runner_temp,
                    run_id=arguments.run_id,
                    run_attempt=arguments.run_attempt,
                    runner_environment=arguments.runner_environment,
                    runner_os=arguments.runner_os,
                    runner_arch=arguments.runner_arch,
                    os_release_text=os_release_text,
                    openssl_version_text=openssl_version_text,
                )
            _write_output(available)
            return 0
        cleanup_engine(
            arguments.runner_temp,
            run_id=arguments.run_id,
            run_attempt=arguments.run_attempt,
        )
        return 0
    except PrismaEngineError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
