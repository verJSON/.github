#!/usr/bin/env python3
"""Run runtime-resolved secretless compatibility lanes.

Node CI fetches this file from the pinned JOB_WORKFLOW_SHA, checks its
SHA-256, and executes ``python3 -I ci_secretless_compatibility_lanes.py run``.
The workflow no longer inlines this executor.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    """Admit only the explicit workflow command before the lane runs."""
    if argv is None:
        argv = sys.argv[1:]
    if argv != ["run"]:
        raise SystemExit("usage: ci_secretless_compatibility_lanes.py run")


if __name__ != "__main__":
    raise SystemExit(
        "ci_secretless_compatibility_lanes.py is an executable lane, not a library"
    )
main()

import ctypes
import errno
import fcntl
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

protected = {
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AZURE_CREDENTIALS", "GH_TOKEN", "GITHUB_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS", "NODE_AUTH_TOKEN", "NPM_TOKEN",
}
leaked = sorted(name for name in protected if os.environ.get(name))
if leaked:
    sys.exit("credential reached compatibility consumer execution: " + ", ".join(leaked))
request = json.loads(os.environ["COMPATIBILITY_RANGES"])
artifact_dir = Path(os.environ["COMPATIBILITY_ARTIFACT_DIR"])
provenance_path = artifact_dir / "provenance.json"
provenance_bytes = provenance_path.read_bytes()
if hashlib.sha256(provenance_bytes).hexdigest() \
        != os.environ["EXPECTED_COMPATIBILITY_PROVENANCE_SHA256"]:
    sys.exit("compatibility provenance changed before consumer execution")
provenance = json.loads(provenance_bytes)
if provenance.get("request") != request:
    sys.exit("compatibility consumer provenance does not match the declared request")
package_json = json.loads(Path("package.json").read_text(encoding="utf-8"))
scripts = package_json.get("scripts")
if not isinstance(scripts, dict) or request["script"] not in scripts:
    sys.exit("compatibility script is absent from package.json")

blocked_service_env = {
    "GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE",
    "GITHUB_STEP_SUMMARY", "VERJSON_CI_TRUSTED_DB_HOST",
    "VERJSON_CI_PLAYWRIGHT_PROFILE", "VERJSON_CI_PLAYWRIGHT_STATE",
    "VERJSON_CI_PLAYWRIGHT_SECRETLESS_MODE",
    "VERJSON_CI_COMMAND_FILES_DIR", "GH_TOKEN",
    "GITHUB_TOKEN", "NODE_AUTH_TOKEN",
    "NPM_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "FTP_PROXY",
    "AWS_SESSION_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_CREDENTIALS", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL", "BASH_ENV", "BASH_XTRACEFD", "ENV",
    "PS4", "SHELLOPTS", "BASHOPTS", "PATH", "NODE_OPTIONS", "NODE_PATH",
    "NODE_EXTRA_CA_CERTS", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
    "PYTHONUSERBASE", "PERL5OPT", "PERL5LIB", "RUBYOPT", "RUBYLIB",
    "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS",
    "GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSL_CAINFO", "GIT_SSL_CAPATH",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE",
    "GCONV_PATH", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
}
blocked_service_prefixes = (
    "GITHUB_", "RUNNER_", "ACTIONS_", "GH_", "DOCKER_", "AWS_", "GOOGLE_",
    "AZURE_", "GCP_", "COREPACK_", "NPM_CONFIG_", "GIT_CONFIG_",
    "LD_", "DYLD_", "POSTGRES_",
)
credential_environment_name_pattern = re.compile(
    r"(?:PASS(?:WORD|WD)?|PWD|SECRET|TOKEN|CREDENTIAL|PRIVATE[_-]?KEY|"
    r"API[_-]?KEY|ACCESS[_-]?KEY|SIGNATURE|BEARER|COOKIE|SESSION|JWT|AUTH)",
    re.IGNORECASE,
)
credential_environment_ambiguous_name_pattern = re.compile(
    r"(?<![A-Z0-9])(?:KEY|CODE|SIG)(?![A-Z0-9])",
    re.IGNORECASE,
)

def credential_environment_name_is_sensitive(name):
    normalized_name = name.upper()
    if normalized_name.startswith("NPM_CONFIG_") and normalized_name != "NPM_CONFIG_CACHE":
        return True
    if normalized_name == "PWD":
        return False
    if credential_environment_name_pattern.search(name) is not None:
        return True
    if normalized_name in {"EXIT_CODE", "CACHE_KEY", "PUBLIC_KEY"}:
        return False
    return credential_environment_ambiguous_name_pattern.search(name) is not None

credential_parameter_name_pattern = re.compile(
    r"(?:PASS(?:WORD|WD)?|PWD|SECRET|TOKEN|CREDENTIAL|PRIVATE[_-]?KEY|"
    r"API[_-]?KEY|ACCESS[_-]?KEY|SIGNATURE|"
    r"(?<![A-Z0-9])KEY(?![A-Z0-9])|(?<![A-Z0-9])CODE(?![A-Z0-9])|"
    r"(?<![A-Z0-9])SIG(?![A-Z0-9])|BEARER|COOKIE|SESSION|JWT|"
    r"(?<![A-Z0-9])AUTH(?:ORIZATION|ENTICATION)?(?![A-Z0-9]))",
    re.IGNORECASE,
)
credential_parameter_pattern = re.compile(
    r"(?:^|[;?&#=\s])(?:[A-Z0-9]+[_-])*(?:PASS(?:WORD|WD)?|PWD|SECRET|TOKEN|CREDENTIAL|"
    r"PRIVATE[_-]?KEY|API[_-]?KEY|ACCESS[_-]?KEY|SIGNATURE|"
    r"(?<![A-Z0-9])KEY(?![A-Z0-9])|(?<![A-Z0-9])CODE(?![A-Z0-9])|"
    r"(?<![A-Z0-9])SIG(?![A-Z0-9])|"
    r"BEARER|COOKIE|SESSION|JWT|"
    r"(?<![A-Z0-9])AUTH(?:ORIZATION|ENTICATION)?(?![A-Z0-9]))\s*=",
    re.IGNORECASE,
)

def credential_component_is_present(component):
    for _ in range(8):
        if credential_parameter_pattern.search(component):
            return True
        if "=" in component and any(
            credential_parameter_name_pattern.search(key)
            for key, _value in parse_qsl(
                component, keep_blank_values=True, max_num_fields=128
            )
        ):
            return True
        decoded = unquote(component)
        if decoded == component:
            return False
        component = decoded
    return True

def credential_environment_value_is_sensitive(name, value):
    if name.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "ALL_PROXY"}:
        return False
    try:
        parsed = urlsplit(value if "://" in value else f"//{value}")
    except ValueError:
        return True
    if parsed.username is not None or parsed.password is not None:
        return True
    return any(
        credential_component_is_present(component)
        for component in (parsed.query, parsed.fragment)
    )

def opaque_service_value_contains_userinfo(name, value):
    normalized_name = name.upper()
    connection_name_suffixes = (
  "_URL", "_URI", "_DSN", "_CONNECTION_STRING", "_CONN_STRING", "_CONNSTR", "_CONN_STR",
    )
    is_connection_value = normalized_name in {
  "URL", "URI", "DSN", "CONNECTION_STRING", "CONN_STRING", "CONNSTR", "CONN_STR",
    } or normalized_name.endswith(connection_name_suffixes)
    for _ in range(8):
        at_index = -1
        while True:
            at_index = value.find("@", at_index + 1)
            if at_index < 0:
                break
            userinfo = value[:at_index].rsplit("=", 1)[-1].rsplit(";", 1)[-1].rsplit("&", 1)[-1].rsplit(None, 1)[-1]
            endpoint = value[at_index + 1:]
            if userinfo.lower().startswith("mailto:"):
                mailto_local_part = userinfo[len("mailto:"):]
                if (
                    re.fullmatch(r"[A-Za-z0-9_+-]+(?:\.[A-Za-z0-9_+-]+)*", mailto_local_part)
                    and re.fullmatch(
                        r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
                        r"[A-Za-z]{2,63}",
                        endpoint,
                    )
                ):
                    continue
            if (
                is_connection_value
                or ":" in userinfo
                or
                re.match(r"(?:tcp|unix)\(", endpoint, re.IGNORECASE)
                or endpoint.startswith("/")
                or re.match(r"[A-Za-z0-9.-]+:\d+(?:/|$)", endpoint)
                or re.match(r"\[[^]]+\]:\d+(?:/|$)", endpoint)
                or re.match(r"[A-Za-z0-9.-]+/", endpoint)
                or re.fullmatch(r"(?:localhost|127(?:\.\d{1,3}){3}|\[::1\])", endpoint, re.IGNORECASE)
            ):
                return True
        decoded = unquote(value)
        if decoded == value:
            return False
        value = decoded
    return True

def validate_candidate_service_value(name, value):
    if credential_environment_name_is_sensitive(name) and not (
        name.upper() == "OPENAI_API_KEY" and value == "ci-dummy-key"
    ):
        sys.exit("candidate service environment includes a credential-bearing variable")
    if "://" not in value:
        if (
            credential_component_is_present(value)
            or opaque_service_value_contains_userinfo(name, value)
        ):
            sys.exit("candidate service value contains credential-bearing data")
        return
    if credential_parameter_pattern.search(value):
        sys.exit("candidate service value contains credential-bearing data")
    try:
        parsed = urlsplit(value)
        has_credential_fields = any(
            credential_component_is_present(component)
            for component in (parsed.query, parsed.fragment)
        )
    except ValueError:
        sys.exit("candidate service URL is malformed")
    if has_credential_fields:
        sys.exit("candidate service URL contains credential-bearing data")
    if parsed.username is not None or parsed.password is not None:
        local_service_hosts = {"localhost", "127.0.0.1", "::1"}
        trusted_db_host = os.environ.get(
            "VERJSON_CI_TRUSTED_DB_HOST", ""
        ).strip().strip("[]").lower()
        if trusted_db_host:
            local_service_hosts.add(trusted_db_host)
        if parsed.hostname is None or parsed.hostname.lower() not in local_service_hosts:
            sys.exit("credentialed candidate service URLs must target the local service")


def configured_compatibility_service_names(db_env, cache_env):
    names = {"DB_HOST", "DB_PORT", "CACHE_PORT"}
    for source in (db_env, cache_env):
        for line in source.splitlines():
            stripped_line = line.strip()
            if not stripped_line or stripped_line.startswith("#"):
                continue
            name, separator, _value = stripped_line.partition("=")
            if not separator or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
                sys.exit("compatibility service environment is malformed")
            if not name.upper().startswith("POSTGRES_"):
                names.add(name)
    return names

def compatibility_service_environment(
    requires_services, db_env, cache_env, environment,
):
    if not requires_services:
        return {}
    service_environment = {}
    for name in sorted(configured_compatibility_service_names(db_env, cache_env)):
        if name not in environment:
            continue
        normalized_name = name.upper()
        if normalized_name in blocked_service_env or any(
            normalized_name.startswith(prefix)
            for prefix in blocked_service_prefixes
        ):
            sys.exit("compatibility service environment includes a protected variable")
        try:
            validate_candidate_service_value(name, environment[name])
        except SystemExit as error:
            sys.exit(f"compatibility service environment rejected: {error}")
        service_environment[name] = environment[name]
    return service_environment

compatibility_service_names = configured_compatibility_service_names(
    os.environ.get("DB_ENV", ""), os.environ.get("CACHE_ENV", ""),
)
compatibility_service_env = compatibility_service_environment(
    os.environ.get("COMPATIBILITY_REQUIRES_SERVICES") == "true",
    os.environ.get("DB_ENV", ""),
    os.environ.get("CACHE_ENV", ""),
    os.environ,
)
package_parts = request["package"].split("/")
if (not package_parts
        or any(not part or part in (".", "..") or "/" in part or "\\" in part
               for part in package_parts)):
    sys.exit("compatibility package path is invalid")
installed_root = Path("node_modules").joinpath(*package_parts)
package_parent = installed_root.parent
installed_leaf = installed_root.name
max_archive_bytes = 32 * 1024 * 1024
max_expanded_bytes = 64 * 1024 * 1024
max_members = 4096
max_tar_bytes = max_expanded_bytes + max_members * 1024 + 1024

directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)

def open_installed_package_parent():
    descriptor = None
    try:
        for component in ("node_modules", *package_parts[:-1]):
            try:
                child = os.open(component, directory_flags, dir_fd=descriptor)
            except FileNotFoundError:
                raise SystemExit("installed compatibility package parent is absent")
            except OSError:
                raise SystemExit(
                    "installed compatibility package parent is not a real directory"
                )
            if descriptor is not None:
                os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise

package_parent_descriptor = open_installed_package_parent()
package_parent_metadata = os.fstat(package_parent_descriptor)
package_parent_handle = Path(f"/proc/self/fd/{package_parent_descriptor}")
libc = ctypes.CDLL(None, use_errno=True)
renameat2 = libc.renameat2
renameat2.argtypes = [
    ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
]
renameat2.restype = ctypes.c_int
rename_no_replace = 1

def place_without_replacement(source, destination):
    result = renameat2(
        package_parent_descriptor,
        os.fsencode(source),
        package_parent_descriptor,
        os.fsencode(destination),
        rename_no_replace,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(destination)
    raise OSError(error, os.strerror(error), destination)

def verify_installed_package_parent():
    try:
        metadata = package_parent.lstat()
    except FileNotFoundError:
        raise SystemExit("installed compatibility package parent is absent")
    if (stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino)
            != (package_parent_metadata.st_dev, package_parent_metadata.st_ino)):
        raise SystemExit(
            "installed compatibility package parent is not a real directory"
        )

verify_installed_package_parent()
try:
    initial_target_metadata = os.stat(
        installed_leaf,
        dir_fd=package_parent_descriptor,
        follow_symlinks=False,
    )
except FileNotFoundError:
    target_initially_absent = True
else:
    target_initially_absent = False
    if (stat.S_ISLNK(initial_target_metadata.st_mode)
            or not stat.S_ISDIR(initial_target_metadata.st_mode)):
        raise SystemExit(
            "installed compatibility package target is not a real directory"
        )

def read_verified_artifact(path, expected_sha512):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SystemExit("verified compatibility artifact is not a regular file")
        if metadata.st_size > max_archive_bytes:
            raise SystemExit("compatibility package archive exceeds compressed-size bound")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(max_archive_bytes + 1)
    finally:
        os.close(descriptor)
    if len(content) > max_archive_bytes:
        raise SystemExit("compatibility package archive exceeds compressed-size bound")
    if hashlib.sha512(content).hexdigest() != expected_sha512:
        raise SystemExit("compatibility artifact changed before consumer execution")
    return content

def write_all(descriptor, content):
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise SystemExit("compatibility package extraction made no progress")
        view = view[written:]

def open_directory_chain(root_descriptor, parts):
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise

def read_bounded_tar_stream(content):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content), mode="rb") as stream:
            tar_bytes = stream.read(max_tar_bytes + 1)
    except (OSError, EOFError) as error:
        raise SystemExit(f"compatibility package archive is invalid gzip: {error}")
    if len(tar_bytes) > max_tar_bytes:
        raise SystemExit("compatibility package archive exceeds tar-stream bound")

    offset = 0
    raw_members = 0
    zero_blocks = 0
    while offset + 512 <= len(tar_bytes):
        header = tar_bytes[offset:offset + 512]
        offset += 512
        if header == bytes(512):
            zero_blocks += 1
            if zero_blocks == 2:
                if any(tar_bytes[offset:]):
                    raise SystemExit("compatibility package archive has trailing content")
                return tar_bytes
            continue
        if zero_blocks:
            raise SystemExit("compatibility package archive has content after end marker")
        raw_members += 1
        if raw_members > max_members:
            raise SystemExit("compatibility package archive exceeds member-count bound")
        entry_type = header[156:157]
        if entry_type not in (b"\0", b"0", b"5"):
            raise SystemExit("compatibility package archive contains links or special entries")
        size_field = header[124:136]
        if size_field[:1] and size_field[0] & 0x80:
            raise SystemExit("compatibility package archive member size encoding is unsupported")
        stripped_size = size_field.rstrip(b"\0 ").lstrip(b" ")
        if any(byte not in b"01234567" for byte in stripped_size):
            raise SystemExit("compatibility package archive member size is invalid")
        declared_size = int(stripped_size or b"0", 8)
        if entry_type == b"5" and declared_size:
            raise SystemExit("compatibility package archive directory has content")
        padded_size = (declared_size + 511) // 512 * 512
        if offset + padded_size > len(tar_bytes):
            raise SystemExit("compatibility package archive member is truncated")
        offset += padded_size
    raise SystemExit("compatibility package archive has no complete end marker")

def stage_verified_package(content, lane, package_parent_handle):
    tar_bytes = read_bounded_tar_stream(content)
    verify_installed_package_parent()
    staging = Path(tempfile.mkdtemp(
        prefix=f".{installed_root.name}.compat-", dir=package_parent_handle,
    ))
    root_descriptor = os.open(
        staging,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    manifest_bytes = None
    member_count = 0
    expanded_bytes = 0
    seen = set()
    sandbox_directories = set()
    sandbox_files = []
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
            for member in archive:
                member_count += 1
                if member_count > max_members:
                    raise SystemExit("compatibility package archive exceeds member-count bound")
                raw_name = member.name
                if (not raw_name or "\\" in raw_name or "\x00" in raw_name
                        or raw_name.startswith("/")):
                    raise SystemExit("compatibility package archive member path is invalid")
                normalized = raw_name[:-1] if member.isdir() and raw_name.endswith("/") else raw_name
                parts = normalized.split("/")
                if (not parts or parts[0] != "package"
                        or any(part in ("", ".", "..") for part in parts)):
                    raise SystemExit("compatibility package archive must have one package root")
                relative_parts = tuple(parts[1:])
                if not relative_parts:
                    if not member.isdir():
                        raise SystemExit("compatibility package root must be a directory")
                    continue
                if relative_parts in seen:
                    raise SystemExit("compatibility package archive contains duplicate members")
                seen.add(relative_parts)
                if not (member.isdir() or member.isreg()):
                    raise SystemExit("compatibility package archive contains links or special entries")
                if member.size < 0:
                    raise SystemExit("compatibility package archive member size is invalid")
                expanded_bytes += member.size
                if expanded_bytes > max_expanded_bytes:
                    raise SystemExit("compatibility package archive exceeds expanded-size bound")

                parent_descriptor = open_directory_chain(root_descriptor, relative_parts[:-1])
                try:
                    leaf = relative_parts[-1]
                    if member.isdir():
                        sandbox_directories.add(relative_parts)
                        try:
                            os.mkdir(leaf, mode=0o700, dir_fd=parent_descriptor)
                        except FileExistsError:
                            child = os.open(
                                leaf,
                                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                                dir_fd=parent_descriptor,
                            )
                            os.close(child)
                        continue
                    source = archive.extractfile(member)
                    if source is None:
                        raise SystemExit("compatibility package archive file is unreadable")
                    file_descriptor = os.open(
                        leaf,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                        0o755 if member.mode & 0o111 else 0o644,
                        dir_fd=parent_descriptor,
                    )
                    captured = bytearray()
                    try:
                        remaining = member.size
                        while remaining:
                            chunk = source.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise SystemExit("compatibility package archive file is truncated")
                            write_all(file_descriptor, chunk)
                            captured.extend(chunk)
                            remaining -= len(chunk)
                        if source.read(1):
                            raise SystemExit("compatibility package archive file exceeds declared size")
                        os.fsync(file_descriptor)
                    finally:
                        os.close(file_descriptor)
                    file_content = bytes(captured)
                    sandbox_files.append((
                        relative_parts,
                        0o755 if member.mode & 0o111 else 0o644,
                        file_content,
                    ))
                    if relative_parts == ("package.json",):
                        manifest_bytes = file_content
                finally:
                    os.close(parent_descriptor)
    except BaseException:
        os.close(root_descriptor)
        shutil.rmtree(staging)
        raise
    if manifest_bytes is None:
        os.close(root_descriptor)
        shutil.rmtree(staging)
        raise SystemExit("compatibility package archive has no package manifest")
    try:
        staged_manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        os.close(root_descriptor)
        shutil.rmtree(staging)
        raise SystemExit(f"compatibility package manifest is invalid: {error}")
    if (staged_manifest.get("name") != lane["package"]
            or staged_manifest.get("version") != lane["version"]):
        os.close(root_descriptor)
        shutil.rmtree(staging)
        raise SystemExit("compatibility package manifest does not match resolved provenance")
    os.fsync(root_descriptor)
    staging_metadata = os.fstat(root_descriptor)
    return (
        staging,
        root_descriptor,
        (staging_metadata.st_dev, staging_metadata.st_ino),
        (tuple(sorted(sandbox_directories)), tuple(sandbox_files)),
    )

def discard_staging(staging):
    if staging.exists() and not staging.is_symlink():
        shutil.rmtree(staging)

def reject_verified_staging(staging, descriptor, identity, reason):
    try:
        try:
            named_metadata = os.stat(
                staging.name,
                dir_fd=package_parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            named_metadata = None
        if (named_metadata is not None
                and (named_metadata.st_dev, named_metadata.st_ino) == identity):
            discard_staging(staging)
    finally:
        os.close(descriptor)
    raise SystemExit(reason)

def installed_target_is_absent():
    try:
        os.stat(
            installed_leaf,
            dir_fd=package_parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return True
    return False

def swap_verified_package(
        staging, staging_descriptor, staging_identity,
        lane, index, expected_owned_identity):
    verify_installed_package_parent()
    held_staging_metadata = os.fstat(staging_descriptor)
    if ((held_staging_metadata.st_dev, held_staging_metadata.st_ino)
            != staging_identity):
        os.close(staging_descriptor)
        raise SystemExit("verified compatibility staging identity changed")
    try:
        named_staging_metadata = os.stat(
            staging.name,
            dir_fd=package_parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        reject_verified_staging(
            staging, staging_descriptor, staging_identity,
            "verified compatibility staging path is absent",
        )
    if ((named_staging_metadata.st_dev, named_staging_metadata.st_ino)
            != staging_identity):
        reject_verified_staging(
            staging, staging_descriptor, staging_identity,
            "verified compatibility staging path changed",
        )
    try:
        target_metadata = os.stat(
            installed_leaf,
            dir_fd=package_parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        if not target_initially_absent or index != 0:
            reject_verified_staging(
                staging, staging_descriptor, staging_identity,
                "installed compatibility package target disappeared unexpectedly",
            )
        target_exists = False
    else:
        target_exists = True
        if (stat.S_ISLNK(target_metadata.st_mode)
                or not stat.S_ISDIR(target_metadata.st_mode)):
            reject_verified_staging(
                staging, staging_descriptor, staging_identity,
                "installed compatibility package target is not a real directory",
            )
        target_identity = (target_metadata.st_dev, target_metadata.st_ino)
        if target_initially_absent and expected_owned_identity is None:
            reject_verified_staging(
                staging, staging_descriptor, staging_identity,
                "installed compatibility package target appeared unexpectedly",
            )
        if index > 0 and target_identity != expected_owned_identity:
            reject_verified_staging(
                staging, staging_descriptor, staging_identity,
                "installed compatibility package target changed unexpectedly",
            )
    backup_container = Path(tempfile.mkdtemp(
        prefix=f".{installed_root.name}.previous-{index}-", dir=package_parent_handle,
    ))
    backup = backup_container / "package"
    backup_relative = f"{backup_container.name}/package"
    swapped = False
    committed = False
    installed_identity = None
    try:
        if target_exists:
            os.rename(
                installed_leaf,
                backup_relative,
                src_dir_fd=package_parent_descriptor,
                dst_dir_fd=package_parent_descriptor,
            )
        try:
            try:
                place_without_replacement(staging.name, installed_leaf)
            except FileExistsError:
                raise SystemExit(
                    "installed compatibility package target appeared during placement"
                )
            swapped = True
        except BaseException:
            if target_exists and installed_target_is_absent():
                os.rename(
                    backup_relative,
                    installed_leaf,
                    src_dir_fd=package_parent_descriptor,
                    dst_dir_fd=package_parent_descriptor,
                )
            raise
        placed_metadata = os.stat(
            installed_leaf,
            dir_fd=package_parent_descriptor,
            follow_symlinks=False,
        )
        if ((placed_metadata.st_dev, placed_metadata.st_ino)
                != staging_identity):
            raise SystemExit(
                "placed compatibility package does not match verified staging identity"
            )
        target_descriptor = staging_descriptor
        try:
            descriptor = os.open(
                "package.json",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=target_descriptor,
            )
            try:
                with os.fdopen(
                    descriptor, "r", encoding="utf-8", closefd=False,
                ) as stream:
                    installed = json.load(stream)
            finally:
                os.close(descriptor)
            installed_metadata = os.fstat(target_descriptor)
        except BaseException:
            raise
        if (installed.get("name") != lane["package"]
                or installed.get("version") != lane["version"]):
            raise SystemExit("resolved compatibility artifact did not reach package swap")
        installed_identity = (installed_metadata.st_dev, installed_metadata.st_ino)
        committed = True
    finally:
        if committed:
            if target_exists:
                shutil.rmtree(backup_container)
            else:
                try:
                    backup_container.rmdir()
                except OSError as error:
                    if error.errno in (errno.EEXIST, errno.ENOTEMPTY):
                        restore_initial_absence(installed_identity)
                        raise SystemExit(
                            "empty compatibility backup acquired unexpected entries"
                        )
                    raise
        elif swapped:
            rejected_relative = f"{backup_container.name}/rejected"
            rejected_is_verified = False
            try:
                os.rename(
                    installed_leaf,
                    rejected_relative,
                    src_dir_fd=package_parent_descriptor,
                    dst_dir_fd=package_parent_descriptor,
                )
                rejected_metadata = (
                    backup_container / "rejected"
                ).lstat()
                rejected_is_verified = (
                    rejected_metadata.st_dev, rejected_metadata.st_ino,
                ) == staging_identity
            finally:
                if target_exists and backup.exists() and installed_target_is_absent():
                    os.rename(
                        backup_relative,
                        installed_leaf,
                        src_dir_fd=package_parent_descriptor,
                        dst_dir_fd=package_parent_descriptor,
                    )
            if rejected_is_verified:
                shutil.rmtree(backup_container)
        else:
            try:
                remaining_staging_metadata = os.stat(
                    staging.name,
                    dir_fd=package_parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                remaining_staging_metadata = None
            if (remaining_staging_metadata is not None
                    and (remaining_staging_metadata.st_dev,
                         remaining_staging_metadata.st_ino) == staging_identity):
                discard_staging(staging)
            if backup_container.exists() and not backup.exists():
                backup_container.rmdir()
        if not committed:
            os.close(staging_descriptor)
    return installed_identity, staging_descriptor

def verify_bound_installed_target(descriptor, identity, phase):
    verify_installed_package_parent()
    held_metadata = os.fstat(descriptor)
    try:
        named_metadata = os.stat(
            installed_leaf,
            dir_fd=package_parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        raise SystemExit(
            f"installed compatibility package target disappeared {phase}"
        )
    if ((held_metadata.st_dev, held_metadata.st_ino) != identity
            or (named_metadata.st_dev, named_metadata.st_ino) != identity
            or stat.S_ISLNK(named_metadata.st_mode)
            or not stat.S_ISDIR(named_metadata.st_mode)):
        raise SystemExit(
            f"installed compatibility package target changed {phase}"
        )

def add_sandbox_binding(arguments, source, destination, *, writable):
    metadata = source.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        arguments.extend(["--symlink", os.readlink(source), destination])
    elif stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode):
        arguments.extend([
            "--bind" if writable else "--ro-bind",
            str(source),
            destination,
        ])
    else:
        raise SystemExit(
            f"compatibility sandbox cannot mirror special entry {source}"
        )

def mirror_node_modules(arguments, source, destination, target_parts):
    for entry in sorted(source.iterdir(), key=lambda path: path.name):
        child_destination = f"{destination}/{entry.name}"
        if entry.name != target_parts[0]:
            add_sandbox_binding(
                arguments, entry, child_destination, writable=False,
            )
            continue
        if len(target_parts) == 1:
            continue
        metadata = entry.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SystemExit(
                "installed compatibility package parent is not a real directory"
            )
        arguments.extend(["--dir", child_destination])
        mirror_node_modules(
            arguments,
            entry,
            child_destination,
            target_parts[1:],
        )

def create_sealed_file(content):
    try:
        descriptor = os.memfd_create(
            "verjson-compatibility-artifact",
            os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
    except (AttributeError, OSError):
        raise SystemExit("sealed compatibility inputs are unavailable")
    try:
        write_all(descriptor, content)
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.fcntl(
            descriptor,
            fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE,
        )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise

def verified_public_cache_source(script_environment):
    # The sandbox masks every ambient npm cache path, so the verified
    # public blobs the install step wrote have to be handed in
    # deliberately. Resolve the source from the workflow-controlled
    # RUNTIME_CACHE_DIR rather than the ambient cache variables this
    # sandbox exists to mask, and only ever from content-v2: no
    # ambient npm config, index, or credential state is eligible.
    # The install step populates the runtime cache when
    # SECRETLESS_RUNTIME_PUBLIC_CACHE is true *or* when
    # RESTORE_PERSISTED_PUBLIC_CACHE is -- the latter being
    # `inputs.cache && inputs.package-manager == 'npm'`. Gating on the
    # first alone would leave an ordinary `cache: true` npm caller
    # resolving to a silent None with the cache populated and bound,
    # which is Verjson/.github#1372 unchanged for that caller.
    requested = any(
        script_environment.get(name, "") == "true"
        for name in (
            "RESTORE_PERSISTED_PUBLIC_CACHE",
            "SECRETLESS_RUNTIME_PUBLIC_CACHE",
        )
    )
    runtime_cache_value = script_environment.get("RUNTIME_CACHE_DIR", "")
    if not runtime_cache_value:
        # The step sets RUNTIME_CACHE_DIR from an unconditional
        # expression, so an empty value means the env key itself was
        # dropped. Starting the sandbox with no cache bind here would
        # silently reproduce Verjson/.github#1372.
        if requested:
            raise SystemExit(
                "compatibility public cache is requested without a runtime cache path")
        return None
    # Both sides of the comparison below are built from this step's
    # own env: block, so this is a drift check between two
    # workflow-controlled expressions -- it catches RUNTIME_CACHE_DIR
    # being re-pointed away from the run-scoped path the population
    # step writes. It is not validation of untrusted input and cannot
    # detect a wrong RUNNER_TEMP, which it reads as the truth.
    runner_temp = Path(script_environment.get("RUNNER_TEMP", ""))
    runtime_cache = Path(runtime_cache_value)
    expected_runtime = runner_temp / (
        "secretless-runtime-cache-"
        f"{script_environment.get('RUN_ID', '')}"
        f"-{script_environment.get('RUN_ATTEMPT', '')}")
    if (not runner_temp.is_absolute() or runner_temp.is_symlink()
            or not runner_temp.is_dir()
            or runtime_cache != expected_runtime):
        raise SystemExit(
            "compatibility public cache is not the workflow runtime cache")
    content = runtime_cache / "_cacache" / "content-v2"
    for candidate in (runtime_cache, content.parent, content):
        try:
            candidate_metadata = candidate.lstat()
        except (FileNotFoundError, NotADirectoryError):
            # A caller that asked for the cache must get it: a missing
            # component means the population step renamed or relocated
            # the runtime cache and is otherwise indistinguishable from
            # the feature being off.
            if requested:
                raise SystemExit(
                    "compatibility public cache is requested but was never populated")
            # secretless-runtime-public-cache is off, so the runtime
            # cache was never created. Start the sandbox without it.
            return None
        if not stat.S_ISDIR(candidate_metadata.st_mode):
            raise SystemExit(
                "compatibility public cache content is not a directory")
    return runner_temp, content


def stage_verified_public_cache(content, staging):
    # npm writes into its own cache while the consumer runs, so the
    # sandbox gets a disposable copy rather than the job's verified
    # cache: a read-only bind fails npm outright, and a writable bind
    # of the real cache would let consumer code rewrite content the
    # rest of the job trusts. Copy regular files only, so nothing but
    # integrity-verified blob content crosses the boundary.
    for directory, subdirectories, names in os.walk(content):
        relative = Path(directory).relative_to(content)
        (staging / relative).mkdir(parents=True, exist_ok=True)
        for name in subdirectories:
            if Path(directory, name).is_symlink():
                raise SystemExit(
                    "compatibility public cache content is not a directory")
        for name in names:
            blob = Path(directory, name)
            # lstat is intentional: a cache entry may be a symlink to
            # a regular host file, including directly under content-v2.
            blob_metadata = blob.lstat()
            if not stat.S_ISREG(blob_metadata.st_mode):
                raise SystemExit(
                    "compatibility public cache blob is not a regular file")
            shutil.copyfile(blob, staging / relative / name)


def run_bound_consumer(
        sandbox_entries, artifact_content, provenance_content,
        script_environment):
    public_cache = verified_public_cache_source(script_environment)
    if public_cache is None:
        run_sandboxed_consumer(
            sandbox_entries, artifact_content, provenance_content,
            script_environment, [],
        )
        return
    runner_temp, content = public_cache
    staging = Path(tempfile.mkdtemp(
        prefix="verjson-compatibility-public-cache-",
        dir=str(runner_temp),
    ))
    try:
        stage_verified_public_cache(content, staging / "content-v2")
        run_sandboxed_consumer(
            sandbox_entries, artifact_content, provenance_content,
            script_environment,
            [
                "--bind", str(staging / "content-v2"),
                "/dev/shm/npm-cache/_cacache/content-v2",
            ],
        )
    finally:
        try:
            shutil.rmtree(staging)
        except OSError:
            # Consumer code runs as the runner's uid inside the
            # sandbox and can seal a staged directory, so cleanup can
            # legitimately fail. The residue outlives this step --
            # the job-end cleanup does not cover the staging prefix --
            # so report the staging root rather than swallowing it.
            # Only this step's own mkdtemp path is named: no
            # consumer-chosen path reaches the log.
            print(
                "::warning::compatibility public cache staging was not "
                f"removed: {staging}",
                file=sys.stderr,
            )


# This is the fail-closed subset of bwrap mount-producing flags that
# public_cache_arguments may contain, not an inventory of every bwrap
# option. The compatibility contract parses the installed `bwrap
# --help` synopsis and verifies each accepted flag's operand count and
# DEST offset. Adding a flag requires updating that contract first.
# bwrap consumes each accepted flag with a fixed arity, and only the
# offset named here becomes a mountpoint inside the sandbox. Walking
# the list by that table is what makes the ambient-mask guard below
# actually derived from the bind arguments: a positional slice holds
# only while every element is a `--bind src dst` triple, and silently
# re-indexes onto a source path or a flag the moment an argument of
# different arity is added. An unrecognized or truncated argument
# fails closed rather than yielding a guard that covers nothing.
PUBLIC_CACHE_MOUNT_ARITY = {
    "--bind": (2, 2),
    "--dir": (1, 1),
    "--ro-bind": (2, 2),
    "--ro-bind-try": (2, 2),
    "--tmpfs": (1, 1),
}


def public_cache_bind_targets(public_cache_arguments):
    targets = []
    index = 0
    while index < len(public_cache_arguments):
        arity, destination = PUBLIC_CACHE_MOUNT_ARITY.get(
            public_cache_arguments[index], (None, None))
        if arity is None:
            raise SystemExit(
                "compatibility public cache bind argument is unrecognized")
        if index + arity >= len(public_cache_arguments):
            raise SystemExit(
                "compatibility public cache bind argument is truncated")
        targets.append(Path(public_cache_arguments[index + destination]))
        index += arity + 1
    return targets


def run_sandboxed_consumer(
        sandbox_entries, artifact_content, provenance_content,
        script_environment, public_cache_arguments):
    bubblewrap = Path("/usr/bin/bwrap")
    try:
        bubblewrap_metadata = bubblewrap.stat()
    except FileNotFoundError:
        raise SystemExit("trusted bubblewrap compatibility sandbox is unavailable")
    if (not stat.S_ISREG(bubblewrap_metadata.st_mode)
            or bubblewrap_metadata.st_uid != 0
            or bubblewrap_metadata.st_mode & 0o022):
        raise SystemExit("trusted bubblewrap compatibility sandbox is unsafe")

    workspace = Path.cwd().resolve(strict=True)
    sandbox_workspace = "/dev/shm/verjson-compatibility-workspace"
    sandbox_node_modules = f"{sandbox_workspace}/node_modules"
    sandbox_target = "/".join((sandbox_node_modules, *package_parts))
    node_path_value = shutil.which(
        "node", path=script_environment.get("PATH", "")
    )
    npm_path_value = shutil.which(
        "npm", path=script_environment.get("PATH", "")
    )
    if node_path_value is None or npm_path_value is None:
        raise SystemExit("trusted Node and npm executables are unavailable")
    node_path = Path(node_path_value).resolve(strict=True)
    npm_path = Path(npm_path_value).resolve(strict=True)
    node_toolchain_root = None
    node_toolchain_destination = None
    node_toolchain_bin = "/usr/local/bin"
    if node_path.is_relative_to(Path("/usr")) and npm_path.is_relative_to(Path("/usr")):
        pass
    else:
        runner_tool_cache = Path(
            script_environment.get("RUNNER_TOOL_CACHE", "")
        )
        if (
            not runner_tool_cache.is_absolute()
            or runner_tool_cache.is_symlink()
            or not runner_tool_cache.is_dir()
            or runner_tool_cache.resolve() != runner_tool_cache
            or node_path.parent.name != "bin"
        ):
            raise SystemExit(
                "trusted Node toolchain is outside the runner tool cache"
            )
        node_tool_cache = (runner_tool_cache / "node").resolve(strict=True)
        node_toolchain_root = node_path.parent.parent.resolve(strict=True)
        if (
            not node_toolchain_root.is_relative_to(node_tool_cache)
            or node_toolchain_root == node_tool_cache
            or not npm_path.is_relative_to(node_toolchain_root)
        ):
            raise SystemExit(
                "trusted npm and Node executables are outside one toolchain"
            )
        node_toolchain_destination = "/opt/verjson-node-toolchain"
        node_toolchain_bin = f"{node_toolchain_destination}/bin"
    arguments = [
        str(bubblewrap),
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup-try",
        "--disable-userns",
        "--die-with-parent",
        "--new-session",
        "--cap-drop", "ALL",
        "--tmpfs", "/",
        "--tmpfs", "/tmp",
        "--dir", "/usr",
        "--dir", "/bin",
        "--dir", "/lib",
        "--dir", "/lib64",
        "--dir", "/etc",
        "--dir", "/etc/ssl",
        "--dir", "/opt",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/dev/shm",
        *(
            ["--dir", node_toolchain_destination]
            if node_toolchain_destination is not None
            else []
        ),
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", Path("/bin").resolve(strict=True).as_posix(), "/bin",
        "--ro-bind", Path("/lib").resolve(strict=True).as_posix(), "/lib",
        "--ro-bind", Path("/lib64").resolve(strict=True).as_posix(), "/lib64",
        # Alternatives-managed /usr names (awk, cc, c++) resolve here (#1782).
        "--ro-bind", Path("/etc/alternatives").resolve(strict=True).as_posix(), "/etc/alternatives",
        "--ro-bind", Path("/etc/ssl").resolve(strict=True).as_posix(), "/etc/ssl",
        "--ro-bind", Path("/etc/hosts").resolve(strict=True).as_posix(), "/etc/hosts",
        "--ro-bind", Path("/etc/resolv.conf").resolve(strict=True).as_posix(), "/etc/resolv.conf",
        "--ro-bind", Path("/etc/nsswitch.conf").resolve(strict=True).as_posix(), "/etc/nsswitch.conf",
        "--ro-bind", Path("/etc/passwd").resolve(strict=True).as_posix(), "/etc/passwd",
        "--ro-bind", Path("/etc/group").resolve(strict=True).as_posix(), "/etc/group",
        *(
            ["--ro-bind", str(node_toolchain_root), node_toolchain_destination]
            if node_toolchain_root is not None
            else []
        ),
        "--dir", "/dev/shm/tmp",
        "--dir", "/dev/shm/npm-cache",
        "--dir", sandbox_workspace,
        "--dir", f"{sandbox_workspace}/node_modules",
        "--dir", "/dev/shm/home",
    ]
    arguments.extend(public_cache_arguments)
    ambient_masks = {
        script_environment.get("NPM_CONFIG_CACHE", ""),
        script_environment.get("NPM_CONFIG_GLOBALCONFIG", ""),
        script_environment.get("NPM_CONFIG_USERCONFIG", ""),
        script_environment.get("npm_config_cache", ""),
        script_environment.get("npm_config_globalconfig", ""),
        script_environment.get("npm_config_userconfig", ""),
    }
    ambient_home = Path(script_environment.get("HOME", ""))
    if ambient_home.is_absolute():
        ambient_masks.update((
            str(ambient_home / ".npm"),
            str(ambient_home / ".npmrc"),
        ))
    ambient_masks.update(("/etc/npmrc", "/usr/local/etc/npmrc"))
    # Masks are applied after the public cache bind, so a mask that
    # covers the bind target would shadow it and fail open to
    # Verjson/.github#1372. Derive the guarded paths from the bind
    # arguments themselves, by the arity of each flag rather than by
    # position, so the two cannot drift apart when the argument list
    # changes shape.
    public_cache_binds = public_cache_bind_targets(public_cache_arguments)
    masked_targets = set()
    masked_directories = []
    for candidate in sorted(
        ambient_masks,
        key=lambda value: (len(Path(value).parts), value),
    ):
        if not candidate:
            continue
        candidate_path = Path(candidate)
        if (
            not candidate_path.is_absolute()
            or Path(os.path.abspath(candidate_path)) != candidate_path
        ):
            raise SystemExit("ambient npm path is not canonical absolute")
        try:
            candidate_path.relative_to(workspace)
        except ValueError:
            pass
        else:
            raise SystemExit("ambient npm path overlaps compatibility workspace")
        for bind_target in public_cache_binds:
            if (bind_target.is_relative_to(candidate_path)
                    or candidate_path.is_relative_to(bind_target)):
                raise SystemExit(
                    "ambient npm path overlaps compatibility public cache bind")
        try:
            candidate_metadata = candidate_path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(candidate_metadata.st_mode):
            try:
                candidate_path = candidate_path.resolve(strict=True)
            except (OSError, RuntimeError):
                raise SystemExit("ambient npm path symlink is unresolved")
        try:
            candidate_path.relative_to(workspace)
        except ValueError:
            pass
        else:
            raise SystemExit("ambient npm path resolves into compatibility workspace")
        if not candidate_path.is_relative_to(Path("/usr")):
            continue
        if str(candidate_path) in masked_targets:
            continue
        if any(
            candidate_path == directory
            or candidate_path.is_relative_to(directory)
            for directory in masked_directories
        ):
            continue
        masked_targets.add(str(candidate_path))
        candidate_metadata = candidate_path.stat()
        if stat.S_ISDIR(candidate_metadata.st_mode):
            arguments.extend(("--tmpfs", str(candidate_path)))
            masked_directories.append(candidate_path)
        elif stat.S_ISREG(candidate_metadata.st_mode):
            arguments.extend(("--ro-bind", "/dev/null", str(candidate_path)))
        else:
            raise SystemExit("ambient npm path has unsupported file type")
    for entry in sorted(workspace.iterdir(), key=lambda path: path.name):
        if entry.name == "node_modules":
            continue
        if entry.is_symlink():
            link_target = Path(os.readlink(entry))
            if link_target.is_absolute():
                raise SystemExit(
                    "compatibility workspace top-level symlink is absolute"
                )
            try:
                resolved_entry = entry.resolve(strict=True)
                resolved_entry.relative_to(workspace)
            except (FileNotFoundError, OSError, RuntimeError, ValueError):
                raise SystemExit(
                    "compatibility workspace top-level symlink escapes workspace"
                )
        add_sandbox_binding(
            arguments,
            entry,
            f"{sandbox_workspace}/{entry.name}",
            writable=entry.name != ".git",
        )
    mirror_node_modules(
        arguments,
        workspace / "node_modules",
        sandbox_node_modules,
        tuple(package_parts),
    )

    arguments.extend(["--tmpfs", sandbox_target])
    sandbox_directories, sandbox_files = sandbox_entries
    all_directories = set(sandbox_directories)
    for parts, _mode, _content in sandbox_files:
        for length in range(1, len(parts)):
            all_directories.add(parts[:length])
    for parts in sorted(all_directories, key=lambda item: (len(item), item)):
        arguments.extend(["--dir", "/".join((sandbox_target, *parts))])

    sealed_descriptors = []
    try:
        artifact_descriptor = create_sealed_file(artifact_content)
        sealed_descriptors.append(artifact_descriptor)
        provenance_descriptor = create_sealed_file(provenance_content)
        sealed_descriptors.append(provenance_descriptor)
        arguments.extend([
            "--perms", "0444",
            "--ro-bind-data", str(artifact_descriptor),
            "/dev/shm/verjson-compatibility-artifact.tgz",
            "--perms", "0444",
            "--ro-bind-data", str(provenance_descriptor),
            "/dev/shm/verjson-compatibility-provenance.json",
        ])
        for parts, mode, content in sandbox_files:
            descriptor = create_sealed_file(content)
            sealed_descriptors.append(descriptor)
            arguments.extend([
                "--perms", f"{mode:04o}",
                "--ro-bind-data", str(descriptor),
                "/".join((sandbox_target, *parts)),
            ])
        arguments.extend([
            "--remount-ro", sandbox_target,
            "--chdir", sandbox_workspace,
            "--",
            "npm", "run", request["script"],
        ])
        sandbox_environment = script_environment.copy()
        sandbox_environment.update({
            "VERJSON_COMPATIBILITY_ARTIFACT":
                "/dev/shm/verjson-compatibility-artifact.tgz",
            "VERJSON_SECRETLESS_COMPATIBILITY_PROVENANCE":
                "/dev/shm/verjson-compatibility-provenance.json",
            "GITHUB_WORKSPACE": sandbox_workspace,
            "HOME": "/dev/shm/home",
            "NPM_CONFIG_CACHE": "/dev/shm/npm-cache",
            "NPM_CONFIG_GLOBALCONFIG": "/dev/shm/npm-globalconfig",
            "NPM_CONFIG_USERCONFIG": "/dev/shm/npm-userconfig",
            "RUNNER_TEMP": "/dev/shm/tmp",
            "PATH": f"{node_toolchain_bin}:/usr/bin:/bin",
            "TEMP": "/dev/shm/tmp",
            "TMP": "/dev/shm/tmp",
            "TMPDIR": "/dev/shm/tmp",
            "npm_config_cache": "/dev/shm/npm-cache",
            "npm_config_globalconfig": "/dev/shm/npm-globalconfig",
            "npm_config_userconfig": "/dev/shm/npm-userconfig",
        })
        output_read, output_write = os.pipe()
        try:
            process = subprocess.Popen(
                arguments,
                env=sandbox_environment,
                pass_fds=tuple(sealed_descriptors),
                stdout=output_write,
                stderr=subprocess.STDOUT,
            )
        finally:
            os.close(output_write)
        output_tail = bytearray()
        try:
            while chunk := os.read(output_read, 8192):
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
                output_tail.extend(chunk)
                del output_tail[:-16384]
        finally:
            os.close(output_read)
        status = process.wait()
        if status != 0:
            failure = subprocess.CalledProcessError(
                status,
                ["npm", "run", request["script"]],
                output=bytes(output_tail),
            )
            output = failure.output.decode("utf-8", errors="replace")
            raise SystemExit(
                "compatibility consumer command failed: "
                f"command={failure.cmd!r} exit={failure.returncode} "
                f"output-tail={output!r}"
            )
    finally:
        for descriptor in sealed_descriptors:
            os.close(descriptor)

def restore_initial_absence(expected_owned_identity):
    if not target_initially_absent or expected_owned_identity is None:
        return
    verify_installed_package_parent()
    try:
        metadata = os.stat(
            installed_leaf,
            dir_fd=package_parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    if (stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != expected_owned_identity):
        raise SystemExit(
            "installed compatibility package target changed before absence restoration"
        )
    cleanup_container = Path(tempfile.mkdtemp(
        prefix=f".{installed_root.name}.absent-restore-",
        dir=package_parent_handle,
    ))
    removed = cleanup_container / "package"
    os.rename(
        installed_leaf,
        f"{cleanup_container.name}/package",
        src_dir_fd=package_parent_descriptor,
        dst_dir_fd=package_parent_descriptor,
    )
    removed_metadata = removed.lstat()
    if ((removed_metadata.st_dev, removed_metadata.st_ino)
            != expected_owned_identity):
        raise SystemExit(
            "installed compatibility package target changed during absence restoration"
        )
    shutil.rmtree(cleanup_container)

def interrupt_compatibility_lane(signum, _frame):
    raise SystemExit(f"compatibility lane interrupted by signal {signum}")

previous_signal_handlers = {
    signum: signal.signal(signum, interrupt_compatibility_lane)
    for signum in (signal.SIGINT, signal.SIGTERM)
}
owned_target_identity = None
owned_target_descriptor = None
try:
    for index, lane in enumerate(provenance["lanes"]):
        artifact = artifact_dir / f"lane-{index}.tgz"
        if not artifact.is_file() or artifact.is_symlink():
            sys.exit("verified compatibility artifact is absent before consumer execution")
        artifact_bytes = read_verified_artifact(artifact, lane["sha512"])
        (
            staging,
            staging_descriptor,
            staging_identity,
            sandbox_entries,
        ) = stage_verified_package(artifact_bytes, lane, package_parent_handle)
        owned_target_identity, owned_target_descriptor = swap_verified_package(
            staging, staging_descriptor, staging_identity,
            lane, index, owned_target_identity,
        )
        script_env = os.environ.copy()
        for env_name in (
            "GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE",
            "GITHUB_STEP_SUMMARY", "CI_SCRIPT_PLAN", "DB_ENV", "CACHE_ENV",
            "VERJSON_CI_TRUSTED_DB_HOST",
        ):
            script_env.pop(env_name, None)
        for service_name in compatibility_service_names:
            script_env.pop(service_name, None)
        script_env.update(compatibility_service_env)
        script_env.update({
            "VERJSON_COMPATIBILITY_ARTIFACT": str(artifact),
            "VERJSON_COMPATIBILITY_PACKAGE": lane["package"],
            "VERJSON_COMPATIBILITY_RANGE": lane["range"],
            "VERJSON_COMPATIBILITY_VERSION": lane["version"],
            "VERJSON_SECRETLESS_COMPATIBILITY_PROVENANCE": str(provenance_path),
        })
        try:
            verify_bound_installed_target(
                owned_target_descriptor,
                owned_target_identity,
                "before consumer execution",
            )
            try:
                run_bound_consumer(
                    sandbox_entries,
                    artifact_bytes,
                    provenance_bytes,
                    script_env,
                )
            finally:
                verify_bound_installed_target(
                    owned_target_descriptor,
                    owned_target_identity,
                    "after consumer execution",
                )
        finally:
            os.close(owned_target_descriptor)
            owned_target_descriptor = None
finally:
    try:
        restore_initial_absence(owned_target_identity)
    finally:
        if owned_target_descriptor is not None:
            os.close(owned_target_descriptor)
        for signum, previous_handler in previous_signal_handlers.items():
            signal.signal(signum, previous_handler)
        os.close(package_parent_descriptor)
