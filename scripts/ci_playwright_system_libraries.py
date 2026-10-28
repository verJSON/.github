#!/usr/bin/env python3
"""Provision the fixed Playwright Chromium system-library profile."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import io
import json
import os
import posixpath
import pwd
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import tarfile
import time
from pathlib import Path
from typing import Any
from functools import wraps


PROFILE_ID = "playwright-chromium-1.63.0-ubuntu-24.04-amd64"
PLAYWRIGHT_VERSION = "1.63.0"
EXPECTED_CATALOG_SHA256 = "932dc0580f023cfbad0885e63bae40d2f33a580f3e2234bce15b4761b8f58134"
EXPECTED_MANIFEST_SHA256 = "e20854d12666e7db8cc947158056de7b49a272d45628c9faa9d8e491b5fb8d87"
STATE_SCHEMA_VERSION = 1
MAX_PACKAGE_COUNT = 256
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_AGGREGATE_BYTES = 256 * 1024 * 1024
MAX_INDEX_BYTES = 256 * 1024 * 1024
MAX_INDEX_COUNT = 32
MAX_DPKG_CONFIG_FILES = 128
MAX_DPKG_CONFIG_FILE_BYTES = 16 * 1024
MAX_DPKG_CONFIG_LINE_BYTES = 1023
MAX_DPKG_CONFIG_TOTAL_BYTES = 256 * 1024
MAX_NEEDRESTART_TAR_BYTES = 32 * 1024 * 1024
DPKG_HOOK_OPTIONS = frozenset({"pre-invoke", "post-invoke", "status-logger"})
DPKG_SAFE_CONFIG = {"no-debsig": "", "log": "/var/log/dpkg.log"}
NEEDRESTART_CONFIG = "/etc/dpkg/dpkg.cfg.d/needrestart"
SAFE_KEY = "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/nonexistent",
    "DEBIAN_FRONTEND": "noninteractive",
    "LC_ALL": "C",
    "APT_CONFIG": "/dev/null",
}


class PlaywrightProfileError(RuntimeError):
    """The fixed Playwright profile contract was violated."""


_CANCELLATION_SIGNALS = frozenset({signal.SIGINT, signal.SIGTERM})
_CURRENT_OPERATION: OperationLifecycle | None = None


class OperationLifecycle:
    def __init__(self) -> None:
        self.cancel_signal: int | None = None
        self.receipt_published = False
        self.processes: dict[int, subprocess.Popen[bytes]] = {}
        self.temp_roots: set[Path] = set()
        self.previous_handlers: dict[int, Any] = {}

    def __enter__(self) -> OperationLifecycle:
        global _CURRENT_OPERATION
        if _CURRENT_OPERATION is not None:
            raise PlaywrightProfileError("nested trusted operations are not supported")
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _CANCELLATION_SIGNALS)
        try:
            self.previous_handlers = {
                signum: signal.getsignal(signum) for signum in _CANCELLATION_SIGNALS
            }
            for signum in _CANCELLATION_SIGNALS:
                signal.signal(signum, self._record_cancellation)
            _CURRENT_OPERATION = self
        except BaseException:
            for signum, handler in self.previous_handlers.items():
                signal.signal(signum, handler)
            raise
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        global _CURRENT_OPERATION
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _CANCELLATION_SIGNALS)
        cleanup_error: BaseException | None = None
        try:
            for process in tuple(self.processes.values()):
                try:
                    if process.poll() is None or _process_group_has_live_members(process.pid):
                        _terminate_process_group(process)
                except BaseException as error:
                    cleanup_error = cleanup_error or error
            for root in tuple(self.temp_roots):
                try:
                    self.remove_temp_root(root)
                except BaseException as error:
                    cleanup_error = cleanup_error or error
            for signum, handler in self.previous_handlers.items():
                signal.signal(signum, signal.SIG_IGN if self.receipt_published else handler)
            _CURRENT_OPERATION = None
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            if self.receipt_published:
                for signum, handler in self.previous_handlers.items():
                    signal.signal(signum, handler)
        if cleanup_error is not None:
            raise cleanup_error
        if self.cancel_signal is not None and not self.receipt_published and exc_type is None:
            raise self.cancellation_error()
        return False

    def _record_cancellation(self, signum: int, _frame: Any) -> None:
        if not self.receipt_published and self.cancel_signal is None:
            self.cancel_signal = signum

    def check_cancelled(self) -> None:
        if self.cancel_signal is None or self.receipt_published:
            return
        for process in tuple(self.processes.values()):
            if process.poll() is None or _process_group_has_live_members(process.pid):
                _terminate_process_group(process)
        raise self.cancellation_error()

    def cancellation_error(self) -> PlaywrightProfileError:
        name = signal.Signals(self.cancel_signal).name if self.cancel_signal is not None else "signal"
        return PlaywrightProfileError(f"trusted package operation cancelled by {name}")

    def register_process(self, process: subprocess.Popen[bytes]) -> None:
        self.processes[process.pid] = process

    def unregister_process(self, process: subprocess.Popen[bytes]) -> None:
        self.processes.pop(process.pid, None)

    def register_temp_root(self, path: Path) -> None:
        self.temp_roots.add(path)

    def remove_temp_root(self, path: Path) -> None:
        if path.exists() or path.is_symlink():
            shutil.rmtree(path)
        self.temp_roots.discard(path)

    def mark_receipt_published(self) -> None:
        previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK, _CANCELLATION_SIGNALS
        )
        try:
            if self.cancel_signal is not None:
                raise self.cancellation_error()
            self.receipt_published = True
            pending = signal.sigpending() & _CANCELLATION_SIGNALS
            if pending:
                self.receipt_published = False
                self.cancel_signal = int(min(pending, key=int))
                raise self.cancellation_error()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def _operation_cancellable(function: Any) -> Any:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with OperationLifecycle():
            return function(*args, **kwargs)

    return wrapped


def _create_private_temp_root() -> Path:
    operation = _CURRENT_OPERATION
    if operation is None:
        raise PlaywrightProfileError("private APT state requires an active trusted operation")
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _CANCELLATION_SIGNALS)
    try:
        root = Path(tempfile.mkdtemp(prefix="verjson-playwright-apt-", dir="/var/tmp"))
        operation.register_temp_root(root)
        os.chmod(root, 0o711)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    operation.check_cancelled()
    return root

def _config_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
    )


def _open_trusted_directory(parent_fd: int, name: str, label: str, owner_uid: int) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as error:
        raise PlaywrightProfileError(f"dpkg configuration directory is unavailable: {label}") from error
    metadata = os.fstat(descriptor)
    if metadata.st_uid != owner_uid or metadata.st_mode & 0o022:
        os.close(descriptor)
        raise PlaywrightProfileError(f"dpkg configuration directory has unsafe ownership or mode: {label}")
    return descriptor


def _read_dpkg_config_at(
    directory_fd: int,
    name: str,
    label: str,
    owner_uid: int,
) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise PlaywrightProfileError(f"dpkg configuration file is unavailable: {label}") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or before.st_mode & 0o022
            or before.st_nlink != 1
            or before.st_size > MAX_DPKG_CONFIG_FILE_BYTES
        ):
            raise PlaywrightProfileError(f"dpkg configuration file has unsafe metadata: {label}")
        contents = bytearray()
        while len(contents) <= MAX_DPKG_CONFIG_FILE_BYTES:
            chunk = os.read(descriptor, min(4096, MAX_DPKG_CONFIG_FILE_BYTES + 1 - len(contents)))
            if not chunk:
                break
            contents.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(contents) > MAX_DPKG_CONFIG_FILE_BYTES
            or len(contents) != before.st_size
            or _config_metadata(before) != _config_metadata(after)
        ):
            raise PlaywrightProfileError(f"dpkg configuration file changed while being read: {label}")
        return {"metadata": _config_metadata(after), "contents": bytes(contents)}
    finally:
        os.close(descriptor)


def _matches_dpkg_config_filename(name: str) -> bool:
    return re.fullmatch(r"[0-9A-Za-z_-]+", name) is not None


def _scan_global_dpkg_config(root_path: Path = Path("/"), owner_uid: int = 0) -> dict[str, Any]:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        root_fd = os.open(root_path, directory_flags)
    except OSError as error:
        raise PlaywrightProfileError("cannot open filesystem root for dpkg config validation") from error
    etc_fd = -1
    dpkg_fd = -1
    fragments_fd = -1
    try:
        root_metadata = os.fstat(root_fd)
        if root_metadata.st_uid != owner_uid or root_metadata.st_mode & 0o022:
            raise PlaywrightProfileError("filesystem root has unsafe ownership or mode")
        etc_fd = _open_trusted_directory(root_fd, "etc", "/etc", owner_uid)
        dpkg_fd = _open_trusted_directory(etc_fd, "dpkg", "/etc/dpkg", owner_uid)
        snapshot: dict[str, Any] = {
            "root": _directory_identity(root_metadata),
            "etc": _directory_identity(os.fstat(etc_fd)),
            "dpkg": _directory_identity(os.fstat(dpkg_fd)),
            "main": None,
            "fragments_dir": None,
            "fragments": {},
        }
        total_bytes = 0
        try:
            os.stat("dpkg.cfg", dir_fd=dpkg_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise PlaywrightProfileError("cannot inspect global dpkg configuration") from error
        else:
            snapshot["main"] = _read_dpkg_config_at(
                dpkg_fd,
                "dpkg.cfg",
                "/etc/dpkg/dpkg.cfg",
                owner_uid,
            )
            total_bytes += len(snapshot["main"]["contents"])

        try:
            fragments_fd = _open_trusted_directory(
                dpkg_fd,
                "dpkg.cfg.d",
                "/etc/dpkg/dpkg.cfg.d",
                owner_uid,
            )
        except PlaywrightProfileError as error:
            if not isinstance(error.__cause__, FileNotFoundError):
                raise
        if fragments_fd >= 0:
            directory_before = os.fstat(fragments_fd)
            try:
                names = sorted(
                    name for name in os.listdir(fragments_fd) if _matches_dpkg_config_filename(name)
                )
            except OSError as error:
                raise PlaywrightProfileError("cannot enumerate global dpkg configuration fragments") from error
            if len(names) > MAX_DPKG_CONFIG_FILES:
                raise PlaywrightProfileError("too many global dpkg configuration fragments")
            for name in names:
                fragment = _read_dpkg_config_at(
                    fragments_fd,
                    name,
                    f"/etc/dpkg/dpkg.cfg.d/{name}",
                    owner_uid,
                )
                total_bytes += len(fragment["contents"])
                if total_bytes > MAX_DPKG_CONFIG_TOTAL_BYTES:
                    raise PlaywrightProfileError("global dpkg configuration exceeds size limit")
                snapshot["fragments"][name] = fragment
            directory_after = os.fstat(fragments_fd)
            if _directory_identity(directory_before) != _directory_identity(directory_after):
                raise PlaywrightProfileError("global dpkg configuration directory changed during inspection")
            snapshot["fragments_dir"] = _directory_identity(directory_after)

        snapshot["root"] = _directory_identity(os.fstat(root_fd))
        snapshot["etc"] = _directory_identity(os.fstat(etc_fd))
        snapshot["dpkg"] = _directory_identity(os.fstat(dpkg_fd))
        return snapshot
    finally:
        for descriptor in (fragments_fd, dpkg_fd, etc_fd, root_fd):
            if descriptor >= 0:
                os.close(descriptor)


def _parse_dpkg_config(contents: bytes, label: str) -> list[tuple[str, str]]:
    raw_lines = contents.split(b"\n")
    if any(len(line) >= MAX_DPKG_CONFIG_LINE_BYTES for line in raw_lines):
        raise PlaywrightProfileError(f"dpkg configuration line exceeds the physical-line limit: {label}")
    try:
        lines = [line.decode("utf-8", "strict") for line in raw_lines]
    except UnicodeDecodeError as error:
        raise PlaywrightProfileError(f"dpkg configuration is not valid UTF-8: {label}") from error
    if any("\x00" in line or "\r" in line for line in lines):
        raise PlaywrightProfileError(f"dpkg configuration contains unsupported control bytes: {label}")

    directives: list[tuple[str, str]] = []
    log_directive = re.compile(r"log(?:[ \t]+|[ \t]*=[ \t]*)/var/log/dpkg\.log")
    option_directive = re.compile(r"(?P<option>[a-z][a-z0-9-]*)(?P<rest>.*)")
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "no-debsig":
            directives.append(("no-debsig", ""))
            continue
        if log_directive.fullmatch(stripped):
            directives.append(("log", DPKG_SAFE_CONFIG["log"]))
            continue

        option_match = option_directive.fullmatch(stripped)
        if option_match is None:
            raise PlaywrightProfileError(
                f"dpkg configuration has an unsupported directive: {label}:{line_number}"
            )
        option = option_match.group("option")
        if option in DPKG_HOOK_OPTIONS:
            rest = option_match.group("rest")
            if rest.startswith("=") and not rest.startswith(("==", "+=", "-=")):
                value = rest[1:].strip(" \t")
            elif rest.startswith((" ", "\t")):
                value = rest.strip(" \t")
            else:
                value = ""
            if not value or value.startswith(("==", "+=", "-=")):
                raise PlaywrightProfileError(
                    f"dpkg configuration has unsupported hook syntax: {label}:{line_number}"
                )
            directives.append((option, value))
            continue
        raise PlaywrightProfileError(
            f"dpkg configuration has an unsupported directive {option}: {label}:{line_number}"
        )
    return directives


def _validate_global_dpkg_config(snapshot: dict[str, Any]) -> bool:
    main = snapshot["main"]
    if main is not None:
        if any(option in DPKG_HOOK_OPTIONS for option, _value in _parse_dpkg_config(main["contents"], "/etc/dpkg/dpkg.cfg")):
            raise PlaywrightProfileError("global dpkg main configuration contains an executable hook")
    suppress_needrestart = False
    for name, fragment in snapshot["fragments"].items():
        label = f"/etc/dpkg/dpkg.cfg.d/{name}"
        directives = _parse_dpkg_config(fragment["contents"], label)
        hooks = [option for option, _value in directives if option in DPKG_HOOK_OPTIONS]
        if not hooks:
            continue
        if name != "needrestart" or hooks != ["status-logger"]:
            raise PlaywrightProfileError(f"global dpkg fragment contains an unexpected executable hook: {label}")
        if len(directives) != 1:
            raise PlaywrightProfileError(f"needrestart fragment has unsupported directives: {label}")
        suppress_needrestart = True
    return suppress_needrestart


def _write_dpkg_view_file(path: Path, contents: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    try:
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o444)
        view = memoryview(contents)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PlaywrightProfileError("cannot write private dpkg configuration view")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _make_dpkg_config_view(root: Path, snapshot: dict[str, Any], suppress_needrestart: bool) -> Path:
    view = root / "dpkg-view"
    fragments_dir = view / "dpkg.cfg.d"
    view.mkdir(mode=0o700)
    fragments_dir.mkdir(mode=0o700)
    os.chown(view, 0, 0)
    os.chown(fragments_dir, 0, 0)
    main = snapshot["main"]
    if main is not None:
        _write_dpkg_view_file(view / "dpkg.cfg", main["contents"])
    for name, fragment in snapshot["fragments"].items():
        if name == "needrestart" and suppress_needrestart:
            continue
        _write_dpkg_view_file(fragments_dir / name, fragment["contents"])
    os.chmod(fragments_dir, 0o555)
    os.chmod(view, 0o555)
    return view


def _catalog_path() -> Path:
    script_dir = Path(__file__).resolve().parent
    repository_catalog = script_dir.parent / "config/ci-playwright-system-libraries.json"
    if repository_catalog.is_file():
        return repository_catalog
    return script_dir / "config/ci-playwright-system-libraries.json"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _validate_package_record(record: Any) -> dict[str, Any]:
    fields = {"package", "architecture", "version", "size", "sha256", "filename"}
    if not isinstance(record, dict) or set(record) != fields:
        raise PlaywrightProfileError("Playwright package catalog has an invalid record")
    package = record["package"]
    architecture = record["architecture"]
    version = record["version"]
    filename = record["filename"]
    if not isinstance(package, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,127}", package):
        raise PlaywrightProfileError("Playwright package catalog has an invalid package name")
    if architecture not in {"amd64", "all"}:
        raise PlaywrightProfileError("Playwright package catalog has an unsupported architecture")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9.+:~_-]{1,128}", version):
        raise PlaywrightProfileError("Playwright package catalog has an invalid version")
    if type(record["size"]) is not int or not 0 < record["size"] <= MAX_PACKAGE_BYTES:
        raise PlaywrightProfileError("Playwright package catalog has an invalid archive size")
    if not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
        raise PlaywrightProfileError("Playwright package catalog has an invalid archive digest")
    if (
        not isinstance(filename, str)
        or filename.startswith("/")
        or "\\" in filename
        or any(part in {"", ".", ".."} for part in filename.split("/"))
        or not filename.startswith(("pool/main/", "pool/universe/"))
        or not filename.endswith(".deb")
    ):
        raise PlaywrightProfileError("Playwright package catalog has an invalid archive path")
    return dict(record)


def resolve_profile(profile_id: str) -> dict[str, Any]:
    if profile_id != PROFILE_ID:
        raise PlaywrightProfileError("unreviewed Playwright system-library profile")
    catalog_path = _catalog_path()
    try:
        catalog_bytes = catalog_path.read_bytes()
        catalog = json.loads(catalog_bytes)
    except (OSError, json.JSONDecodeError) as error:
        raise PlaywrightProfileError("Playwright system-library catalog is unavailable") from error
    if hashlib.sha256(catalog_bytes).hexdigest() != EXPECTED_CATALOG_SHA256:
        raise PlaywrightProfileError("Playwright system-library catalog digest mismatch")
    if not isinstance(catalog, dict) or set(catalog) != {"schemaVersion", "profiles"}:
        raise PlaywrightProfileError("Playwright system-library catalog has an invalid shape")
    if type(catalog["schemaVersion"]) is not int or catalog["schemaVersion"] != 1:
        raise PlaywrightProfileError("unsupported Playwright system-library catalog version")
    profiles = catalog["profiles"]
    if not isinstance(profiles, dict) or set(profiles) != {PROFILE_ID}:
        raise PlaywrightProfileError("Playwright system-library catalog has unexpected profiles")
    profile = profiles[PROFILE_ID]
    fields = {
        "id",
        "playwrightVersion",
        "platform",
        "directPackages",
        "packages",
        "packageManifestSha256",
    }
    if not isinstance(profile, dict) or set(profile) != fields or profile["id"] != PROFILE_ID:
        raise PlaywrightProfileError("Playwright profile has an invalid shape")
    if profile["playwrightVersion"] != PLAYWRIGHT_VERSION:
        raise PlaywrightProfileError("Playwright profile version does not match the reviewed profile")
    if profile["platform"] != {
        "distribution": "ubuntu",
        "versionId": "24.04",
        "architecture": "amd64",
    }:
        raise PlaywrightProfileError("Playwright profile platform is invalid")
    direct_packages = profile["directPackages"]
    package_rows = profile["packages"]
    if (
        not isinstance(direct_packages, list)
        or not direct_packages
        or len(direct_packages) > MAX_PACKAGE_COUNT
        or len(set(direct_packages)) != len(direct_packages)
        or any(
            not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,127}", name)
            for name in direct_packages
        )
    ):
        raise PlaywrightProfileError("Playwright profile has an invalid direct package list")
    if not isinstance(package_rows, list) or not package_rows or len(package_rows) > MAX_PACKAGE_COUNT:
        raise PlaywrightProfileError("Playwright profile has an invalid package manifest")
    if hashlib.sha256(_canonical_json(package_rows)).hexdigest() != EXPECTED_MANIFEST_SHA256:
        raise PlaywrightProfileError("Playwright package manifest digest mismatch")
    packages: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for raw_record in package_rows:
        record = _validate_package_record(raw_record)
        if record["package"] in packages:
            raise PlaywrightProfileError("Playwright package manifest repeats a package")
        packages[record["package"]] = record
        total_bytes += record["size"]
    if total_bytes > MAX_AGGREGATE_BYTES:
        raise PlaywrightProfileError("Playwright package manifest exceeds its aggregate size limit")
    if any(name not in packages for name in direct_packages):
        raise PlaywrightProfileError("Playwright direct package is absent from the package manifest")
    if not isinstance(profile["packageManifestSha256"], str) or profile["packageManifestSha256"] != EXPECTED_MANIFEST_SHA256:
        raise PlaywrightProfileError("Playwright package manifest digest declaration is invalid")
    resolved = dict(profile)
    resolved["packages"] = packages
    resolved["catalogSha256"] = EXPECTED_CATALOG_SHA256
    return resolved


def validate_runner_platform(
    os_release: dict[str, str],
    *,
    architecture: str,
    runner_environment: str,
    runner_os: str = "Linux",
) -> None:
    if (
        runner_environment != "github-hosted"
        or runner_os != "Linux"
        or os_release.get("ID", "").lower() != "ubuntu"
        or os_release.get("VERSION_ID") != "24.04"
        or architecture != "amd64"
    ):
        raise PlaywrightProfileError("Playwright system-library profile requires GitHub-hosted Ubuntu 24.04 amd64")


def parse_install_plan(plan_output: str, profile: dict[str, Any]) -> dict[tuple[str, str], str]:
    planned: dict[tuple[str, str], str] = {}
    pattern = re.compile(
        r"^Inst (?P<package>[a-z0-9][a-z0-9+.-]{0,127})(?::(?P<architecture>[a-z0-9-]+))?"
        r"(?: \[[^]]*\])? \((?P<version>[^ )]+)(?P<details>[^)]*)\)(?: \[\])?$"
    )
    for line in plan_output.splitlines():
        if line.startswith("Remv ") or line.startswith("Purg "):
            raise PlaywrightProfileError("Playwright apt plan removes a package")
        if not line.startswith("Inst "):
            continue
        match = pattern.fullmatch(line)
        if match is None:
            raise PlaywrightProfileError("Playwright apt plan contains an unrecognized install row")
        package = match.group("package")
        version = match.group("version")
        record = profile["packages"].get(package)
        architecture = match.group("architecture") or (record["architecture"] if record else "")
        if record is None or architecture != record["architecture"] or version != record["version"]:
            raise PlaywrightProfileError("Playwright apt plan selected an unpinned package")
        _validate_plan_architecture_annotation(match.group("details"), architecture)
        key = (package, architecture)
        if key in planned:
            raise PlaywrightProfileError("Playwright apt plan repeats a package")
        planned[key] = version
    return planned


def _validate_plan_architecture_annotation(details: str, architecture: str) -> None:
    if "[" not in details and "]" not in details:
        return
    match = re.fullmatch(r" (?P<origin>[^][]*)\[(?P<architecture>[a-z0-9-]+)\]", details)
    if match is None or match.group("architecture") != architecture:
        raise PlaywrightProfileError("Playwright apt plan has a malformed or mismatched architecture annotation")


def _reject_dpkg_install_plan(planned: dict[tuple[str, str], str]) -> None:
    if any(package == "dpkg" for package, _architecture in planned):
        raise PlaywrightProfileError("Playwright profile install plan would mutate the dpkg package manager")


def _package_key_for_name(profile: dict[str, Any], package: str, architecture: str | None = None) -> tuple[str, str]:
    record = profile["packages"].get(package)
    if record is None or (architecture is not None and record["architecture"] != architecture):
        raise PlaywrightProfileError("Playwright profile references an unpinned package")
    return package, record["architecture"]


def create_receipt(profile_id: str, package_versions: dict[tuple[str, str], str]) -> dict[str, Any]:
    profile = resolve_profile(profile_id)
    expected = {
        (package, record["architecture"]): record["version"]
        for package, record in profile["packages"].items()
    }
    if package_versions != expected:
        raise PlaywrightProfileError("Playwright receipt differs from its complete pinned package closure")
    packages = [
        {"package": package, "architecture": architecture, "version": version}
        for (package, architecture), version in sorted(package_versions.items())
    ]
    return {
        "schemaVersion": STATE_SCHEMA_VERSION,
        "profileId": profile_id,
        "catalogSha256": profile["catalogSha256"],
        "packageManifestSha256": profile["packageManifestSha256"],
        "packages": packages,
        "packageStateSha256": hashlib.sha256(_canonical_json(packages)).hexdigest(),
    }


def verify_receipt(
    receipt: Any,
    profile_id: str,
    package_versions: dict[tuple[str, str], str],
) -> None:
    expected_fields = {
        "schemaVersion",
        "profileId",
        "catalogSha256",
        "packageManifestSha256",
        "packages",
        "packageStateSha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_fields:
        raise PlaywrightProfileError("Playwright provisioning receipt has an invalid shape")
    if type(receipt["schemaVersion"]) is not int or receipt["schemaVersion"] != STATE_SCHEMA_VERSION:
        raise PlaywrightProfileError("unsupported Playwright provisioning receipt version")
    if receipt["profileId"] != profile_id:
        raise PlaywrightProfileError("Playwright provisioning receipt names a different profile")
    expected = create_receipt(profile_id, package_versions)
    if receipt != expected:
        raise PlaywrightProfileError("Playwright package state differs from its provisioning receipt")


def _require_root() -> None:
    if os.geteuid() != 0 or sys.version_info < (3, 10):
        raise PlaywrightProfileError("Playwright package provisioning requires root and Python 3.10+")


def _read_os_release(path: str = "/etc/os-release") -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise PlaywrightProfileError("cannot read runner operating-system identity") from error
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        try:
            parsed = shlex.split(value, comments=False, posix=True)
        except ValueError as error:
            raise PlaywrightProfileError("runner operating-system identity is malformed") from error
        if key and len(parsed) == 1:
            values[key] = parsed[0]
    return values


def _child_setup(parent_pid: int, inherited_mask: set[signal.Signals]) -> None:
    _parent_death_signal(parent_pid)
    signal.pthread_sigmask(signal.SIG_SETMASK, inherited_mask)


def _run_supervised(
    argv: tuple[str, ...] | list[str],
    *,
    timeout: int,
    maximum: int,
    env: dict[str, str] | None,
    cwd: Path | None,
    merge_stderr: bool,
) -> tuple[bytes, bytes, int]:
    operation = _CURRENT_OPERATION
    if operation is None:
        with OperationLifecycle():
            return _run_supervised(
                argv,
                timeout=timeout,
                maximum=maximum,
                env=env,
                cwd=cwd,
                merge_stderr=merge_stderr,
            )

    operation.check_cancelled()
    parent_pid = os.getpid()
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _CANCELLATION_SIGNALS)
    process: subprocess.Popen[bytes] | None = None
    spawn_error: BaseException | None = None
    try:
        try:
            process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                env=SAFE_ENV if env is None else env,
                cwd=cwd,
                start_new_session=True,
                preexec_fn=lambda: _child_setup(parent_pid, previous_mask),
            )
            operation.register_process(process)
        except BaseException as error:
            spawn_error = error
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    if spawn_error is not None:
        operation.check_cancelled()
        raise spawn_error
    if process is None:
        raise PlaywrightProfileError("trusted package process was not created")

    try:
        operation.check_cancelled()
        deadline = time.monotonic() + timeout
        while True:
            operation.check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                output, error_output = process.communicate(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise
        if _process_group_has_live_members(process.pid):
            _terminate_process_group(process)
            raise PlaywrightProfileError("trusted package command left child processes")
        operation.check_cancelled()
        error_output = error_output or b""
        if len(output) > maximum or len(error_output) > maximum:
            raise PlaywrightProfileError("trusted package command exceeded its output limit")
        if process.returncode != 0:
            raise PlaywrightProfileError("trusted package command failed")
        return output, error_output, process.returncode
    except subprocess.TimeoutExpired as error:
        _terminate_process_group(process)
        raise PlaywrightProfileError("trusted package command timed out") from error
    finally:
        if process.poll() is None or _process_group_has_live_members(process.pid):
            _terminate_process_group(process)
        operation.unregister_process(process)

def _exact_output(
    argv: tuple[str, ...],
    *,
    maximum: int = 1024 * 1024,
    env: dict[str, str] | None = None,
) -> bytes:
    stdout, _stderr, _returncode = _run_supervised(
        argv,
        timeout=60,
        maximum=maximum,
        env=env,
        cwd=None,
        merge_stderr=False,
    )
    return stdout


def _installed_package_state(env: dict[str, str] | None = None) -> dict[tuple[str, str], tuple[str, str]]:
    output = _exact_output(
        (
            "/usr/bin/dpkg-query",
            "-W",
            r"-f=${db:Status-Abbrev}\t${binary:Package}\t${Version}\t${Architecture}\n",
        ),
        maximum=32 * 1024 * 1024,
        env=env,
    ).decode("utf-8", "strict")
    state: dict[tuple[str, str], tuple[str, str]] = {}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 4:
            raise PlaywrightProfileError("trusted package database returned an invalid row")
        status, binary_package, version, architecture = fields
        package = binary_package.split(":", 1)[0]
        if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,127}", package):
            raise PlaywrightProfileError("trusted package database returned an invalid package name")
        if not architecture or not version:
            raise PlaywrightProfileError("trusted package database returned an incomplete row")
        if status.startswith("ii "):
            key = (package, architecture)
            if key in state:
                raise PlaywrightProfileError("trusted package database repeated an installed package")
            state[key] = (status, version)
    return state


def _installed_profile_closure(
    profile: dict[str, Any],
    installed: dict[tuple[str, str], tuple[str, str]],
) -> dict[tuple[str, str], str]:
    package_versions: dict[tuple[str, str], str] = {}
    for package, record in profile["packages"].items():
        key = (package, record["architecture"])
        version = record["version"]
        state = installed.get(key)
        if state is None or not state[0].startswith("ii ") or state[1] != version:
            raise PlaywrightProfileError("installed Playwright package closure differs from its pinned manifest")
        package_versions[key] = version
    return package_versions


def _validate_safe_key() -> None:
    try:
        metadata = os.lstat(SAFE_KEY)
    except OSError as error:
        raise PlaywrightProfileError("Ubuntu archive signing key is unavailable") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
        or os.path.realpath(SAFE_KEY) != SAFE_KEY
    ):
        raise PlaywrightProfileError("Ubuntu archive signing key has unsafe ownership or permissions")
    owner = _exact_output(("/usr/bin/dpkg-query", "-S", SAFE_KEY), maximum=4096)
    if owner != f"ubuntu-keyring: {SAFE_KEY}\n".encode("ascii"):
        raise PlaywrightProfileError("Ubuntu archive signing key is not owned by ubuntu-keyring")


def _write_root_file(path: Path, data: bytes, mode: int = 0o400) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _apt_config_contents(configparts: Path) -> bytes:
    resolved = configparts.resolve(strict=True)
    path_text = str(resolved)
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", path_text):
        raise PlaywrightProfileError("private APT configuration path is unsafe")
    return (
        f'Dir::Etc::Parts "{path_text}";\n'
        'Dir::Etc::main "/dev/null";\n'
    ).encode("ascii")


def _validate_apt_config(paths: dict[str, Path]) -> Path:
    root = paths["root"]
    home = paths["home"]
    configparts = paths["configparts"]
    config_file = paths["apt_config"]
    try:
        root_metadata = os.lstat(root)
        configparts_metadata = os.lstat(configparts)
        config_metadata = os.lstat(config_file)
        root_resolved = root.resolve(strict=True)
        configparts_resolved = configparts.resolve(strict=True)
        config_resolved = config_file.resolve(strict=True)
        home_metadata = os.lstat(home)
        home_resolved = home.resolve(strict=True)
        contents = config_file.read_bytes()
    except OSError as error:
        raise PlaywrightProfileError("private APT configuration is unavailable") from error
    if (
        not root.is_absolute()
        or root_resolved != root
        or not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != 0
        or root_metadata.st_mode & 0o022
        or home != root / "home"
        or home_resolved != root_resolved / "home"
        or not stat.S_ISDIR(home_metadata.st_mode)
        or home_metadata.st_uid != 0
        or stat.S_IMODE(home_metadata.st_mode) != 0o555
        or any(os.scandir(home))
        or configparts != root / "configparts"
        or configparts_resolved != root_resolved / "configparts"
        or not stat.S_ISDIR(configparts_metadata.st_mode)
        or configparts_metadata.st_uid != 0
        or stat.S_IMODE(configparts_metadata.st_mode) != 0o500
        or any(os.scandir(configparts))
        or config_file != root / "apt.conf"
        or config_resolved != root_resolved / "apt.conf"
        or not stat.S_ISREG(config_metadata.st_mode)
        or config_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(config_metadata.st_mode) != 0o400
        or config_metadata.st_nlink != 1
        or config_metadata.st_size > 4096
        or contents != _apt_config_contents(configparts)
    ):
        raise PlaywrightProfileError("private APT configuration has unsafe ownership, mode, path, or contents")
    return config_file


def _apt_environment(paths: dict[str, Path]) -> dict[str, str]:
    apt_config = _validate_apt_config(paths)
    return {**SAFE_ENV, "HOME": str(paths["home"]), "APT_CONFIG": str(apt_config)}


def _make_apt_environment(root: Path) -> dict[str, Path]:
    apt_uid = pwd.getpwnam("_apt").pw_uid
    lists = root / "lists"
    cache = root / "cache"
    archives = cache / "archives"
    sourceparts = root / "sourceparts"
    configparts = root / "configparts"
    trustedparts = root / "trustedparts"
    home = root / "home"
    for directory in (lists, cache, archives):
        directory.mkdir(mode=0o755)
    (lists / "partial").mkdir(mode=0o700)
    (archives / "partial").mkdir(mode=0o700)
    home.mkdir(mode=0o755)
    os.chown(lists, apt_uid, 0)
    os.chown(lists / "partial", apt_uid, 0)
    os.chown(cache, apt_uid, 0)
    os.chown(archives, apt_uid, 0)
    os.chown(archives / "partial", apt_uid, 0)
    sourceparts.mkdir(mode=0o500)
    configparts.mkdir(mode=0o500)
    trustedparts.mkdir(mode=0o500)
    os.chown(home, 0, 0)
    os.chmod(home, 0o555)
    for directory in (sourceparts, configparts, trustedparts):
        os.chown(directory, 0, 0)
    source_list = root / "ubuntu.sources.list"
    source_data = (
        f"deb [arch=amd64 signed-by={SAFE_KEY}] https://archive.ubuntu.com/ubuntu noble main universe\n"
        f"deb [arch=amd64 signed-by={SAFE_KEY}] https://archive.ubuntu.com/ubuntu noble-updates main universe\n"
        f"deb [arch=amd64 signed-by={SAFE_KEY}] https://security.ubuntu.com/ubuntu noble-security main universe\n"
    ).encode("ascii")
    _write_root_file(source_list, source_data)
    apt_config = root / "apt.conf"
    _write_root_file(apt_config, _apt_config_contents(configparts))
    paths = {
        "root": root,
        "lists": lists,
        "cache": cache,
        "archives": archives,
        "source_list": source_list,
        "sourceparts": sourceparts,
        "configparts": configparts,
        "trustedparts": trustedparts,
        "apt_config": apt_config,
        "home": home,
    }
    return paths


def _apt_options(paths: dict[str, Path]) -> list[str]:
    pairs = (
        ("Dir::Etc::main", "/dev/null"),
        ("Dir::Etc::parts", str(paths["configparts"])),
        ("Dir::Etc::sourcelist", str(paths["source_list"])),
        ("Dir::Etc::sourceparts", str(paths["sourceparts"])),
        ("Dir::Etc::trusted", "/dev/null"),
        ("Dir::Etc::trustedparts", str(paths["trustedparts"])),
        ("Dir::Etc::preferences", "/dev/null"),
        ("Dir::Etc::preferencesparts", str(paths["configparts"])),
        ("Dir::State::lists", str(paths["lists"])),
        ("Dir::State::status", "/var/lib/dpkg/status"),
        ("Dir::Cache", str(paths["cache"])),
        ("APT::Architecture", "amd64"),
        ("APT::Get::AllowUnauthenticated", "false"),
        ("Acquire::AllowInsecureRepositories", "false"),
        ("Acquire::AllowDowngradeToInsecureRepositories", "false"),
    )
    return [part for key, value in pairs for part in ("-o", f"{key}={value}")]


def _parent_death_signal(parent_pid: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent_pid:
        os._exit(126)


def _process_group_has_live_members(process_group_id: int) -> bool:
    try:
        process_entries = os.scandir("/proc")
    except OSError as error:
        raise PlaywrightProfileError("cannot inspect trusted package process group") from error
    with process_entries:
        for entry in process_entries:
            if not entry.name.isdecimal():
                continue
            try:
                raw_stat = Path(entry.path, "stat").read_text(encoding="ascii")
            except FileNotFoundError:
                continue
            except OSError as error:
                raise PlaywrightProfileError("cannot inspect trusted package process identity") from error
            closing_paren = raw_stat.rfind(")")
            if closing_paren < 0:
                raise PlaywrightProfileError("trusted package process identity is malformed")
            fields = raw_stat[closing_paren + 2 :].split()
            if len(fields) < 3:
                raise PlaywrightProfileError("trusted package process identity is incomplete")
            try:
                process_group = int(fields[2])
            except ValueError as error:
                raise PlaywrightProfileError("trusted package process group is malformed") from error
            if process_group == process_group_id and fields[0] not in {"Z", "X"}:
                return True
    return False


def _ensure_process_group_stopped(process_group_id: int) -> None:
    if not _process_group_has_live_members(process_group_id):
        return
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while _process_group_has_live_members(process_group_id) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _process_group_has_live_members(process_group_id):
        raise PlaywrightProfileError("trusted package child process could not be terminated")


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired as error:
        raise PlaywrightProfileError("trusted package process tree could not be reaped") from error
    _ensure_process_group_stopped(process.pid)


def _run(
    argv: tuple[str, ...] | list[str],
    *,
    timeout: int,
    maximum: int = 2 * 1024 * 1024,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> bytes:
    output, _stderr, _returncode = _run_supervised(
        argv,
        timeout=timeout,
        maximum=maximum,
        env=env,
        cwd=cwd,
        merge_stderr=True,
    )
    return output


_NAMESPACE_MOUNT_SCRIPT = r'''import ctypes
import os
import stat
import sys

source = sys.argv[1]
apt_argv = sys.argv[2:]
home = os.environ.get("HOME", "")
if not apt_argv or apt_argv[0] != "/usr/bin/apt-get":
    raise SystemExit("invalid private dpkg namespace command")
source_info = os.lstat(source)
source_config = os.lstat(os.path.join(source, "dpkg.cfg.d"))
target_info = os.lstat("/etc/dpkg")
home_info = os.lstat(home)
if (
    not stat.S_ISDIR(source_info.st_mode)
    or source_info.st_uid != 0
    or source_info.st_mode & 0o022
    or not stat.S_ISDIR(source_config.st_mode)
    or source_config.st_uid != 0
    or source_config.st_mode & 0o022
    or not stat.S_ISDIR(target_info.st_mode)
    or target_info.st_uid != 0
    or target_info.st_mode & 0o022
    or not stat.S_ISDIR(home_info.st_mode)
    or home_info.st_uid != 0
    or home_info.st_mode & 0o222
    or os.listdir(home)
    or os.path.realpath(source) != source
    or os.path.realpath(home) != home
):
    raise SystemExit("unsafe private dpkg configuration mount path")
libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]
libc.mount.restype = ctypes.c_int
source_bytes = os.fsencode(source)
target_bytes = b"/etc/dpkg"
MS_BIND = 4096
MS_REC = 16384
MS_REMOUNT = 32
MS_RDONLY = 1
if libc.mount(source_bytes, target_bytes, None, MS_BIND | MS_REC, None) != 0:
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error), "/etc/dpkg")
if libc.mount(None, target_bytes, None, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_REC, None) != 0:
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error), "/etc/dpkg")
home_bytes = os.fsencode(home)
if libc.mount(home_bytes, home_bytes, None, MS_BIND, None) != 0:
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error), home)
if libc.mount(None, home_bytes, None, MS_BIND | MS_REMOUNT | MS_RDONLY, None) != 0:
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error), home)
mounted = set()
with open("/proc/self/mountinfo", "rt", encoding="ascii") as stream:
    for line in stream:
        fields = line.rstrip("\n").split()
        separator = fields.index("-")
        mountpoint = fields[4].replace("\\040", " ")
        if mountpoint in {"/etc/dpkg", home}:
            optional = fields[6:separator]
            if "ro" in fields[5].split(",") and not any(
                value.startswith(("shared:", "master:")) for value in optional
            ):
                mounted.add(mountpoint)
if mounted != {"/etc/dpkg", home}:
    raise SystemExit("private dpkg config or HOME mount is not read-only and private")
os.execve(apt_argv[0], apt_argv, os.environ.copy())
'''


def _run_dpkg_install_in_namespace(
    apt_argv: list[str] | tuple[str, ...],
    config_view: Path,
    env: dict[str, str],
    timeout: int,
) -> bytes:
    if (
        not apt_argv
        or apt_argv[0] != "/usr/bin/apt-get"
        or apt_argv.count("--no-download") != 1
        or apt_argv.count("--no-remove") != 1
        or apt_argv.count("--no-install-recommends") != 1
        or apt_argv.count("--yes") != 1
        or apt_argv.count("install") != 1
        or "--download-only" in apt_argv
        or config_view.name != "dpkg-view"
        or config_view.parent != Path(env["APT_CONFIG"]).parent
    ):
        raise PlaywrightProfileError("private dpkg namespace request is outside the fixed install plan")
    try:
        config_metadata = os.lstat(config_view)
        config_resolved = config_view.resolve(strict=True)
    except OSError as error:
        raise PlaywrightProfileError("private dpkg configuration view is unavailable") from error
    if (
        config_resolved != config_view
        or not stat.S_ISDIR(config_metadata.st_mode)
        or config_metadata.st_uid != 0
        or stat.S_IMODE(config_metadata.st_mode) != 0o555
    ):
        raise PlaywrightProfileError("private dpkg configuration view has unsafe metadata")
    return _run(
        (
            "/usr/bin/unshare",
            "--mount",
            "--pid",
            "--fork",
            "--kill-child",
            "--propagation",
            "private",
            "--mount-proc=/proc",
            "/usr/bin/python3",
            "-I",
            "-c",
            _NAMESPACE_MOUNT_SCRIPT,
            str(config_view.resolve(strict=True)),
            *apt_argv,
        ),
        timeout=timeout,
        env=env,
        maximum=4 * 1024 * 1024,
    )


def _parse_control_stanza(data: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in data.decode("utf-8", "strict").splitlines():
        if line.startswith((" ", "\t")):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key] = value.lstrip()
    return fields


def _read_package_indexes(paths: dict[str, Path]) -> dict[tuple[str, str, str], dict[str, Any]]:
    options = _apt_options(paths)
    apt_environment = _apt_environment(paths)
    raw_targets = _run(
        ["/usr/bin/apt-get", "indextargets", *options, "--format", "$(FILENAME)", "Created-By: Packages"],
        timeout=60,
        maximum=1024 * 1024,
        env=apt_environment,
    )
    target_paths = sorted(set(raw_targets.decode("utf-8", "strict").splitlines()))
    if not target_paths or len(target_paths) > MAX_INDEX_COUNT:
        raise PlaywrightProfileError("trusted apt returned an invalid package-index set")
    records: dict[tuple[str, str, str], dict[str, Any]] = {}
    lists_root = paths["lists"].resolve(strict=True)
    for target in target_paths:
        index_path = Path(target)
        try:
            resolved = index_path.resolve(strict=True)
            metadata = os.lstat(index_path)
        except OSError as error:
            raise PlaywrightProfileError("trusted apt package index is unavailable") from error
        if (
            not resolved.is_relative_to(lists_root)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > MAX_INDEX_BYTES
        ):
            raise PlaywrightProfileError("trusted apt package index has an unsafe path or size")
        raw = _run(
            ["/usr/lib/apt/apt-helper", "cat-file", str(resolved)],
            timeout=120,
            maximum=MAX_INDEX_BYTES,
            env=apt_environment,
        )
        for stanza in re.split(rb"\n\s*\n", raw):
            if not stanza.strip():
                continue
            fields = _parse_control_stanza(stanza)
            required = ("Package", "Version", "Architecture", "Size", "SHA256", "Filename")
            if any(field not in fields for field in required):
                continue
            try:
                size = int(fields["Size"])
            except ValueError as error:
                raise PlaywrightProfileError("trusted apt metadata has an invalid archive size") from error
            if not re.fullmatch(r"[0-9a-f]{64}", fields["SHA256"]):
                raise PlaywrightProfileError("trusted apt metadata has an invalid archive digest")
            record = {
                "package": fields["Package"],
                "architecture": fields["Architecture"],
                "version": fields["Version"],
                "size": size,
                "sha256": fields["SHA256"],
                "filename": fields["Filename"],
            }
            key = (record["package"], record["architecture"], record["version"])
            previous = records.get(key)
            if previous is not None and previous != record:
                raise PlaywrightProfileError("trusted apt indexes disagree on package metadata")
            records[key] = record
    return records


def _validate_signed_metadata(
    indexes: dict[tuple[str, str, str], dict[str, Any]],
    required: dict[tuple[str, str], str],
    profile: dict[str, Any],
) -> None:
    for key, version in required.items():
        package, architecture = key
        record = profile["packages"].get(package)
        if record is None or record["architecture"] != architecture or record["version"] != version:
            raise PlaywrightProfileError("Playwright package closure is outside its pinned manifest")
        signed = indexes.get((package, architecture, version))
        if signed != record:
            raise PlaywrightProfileError("signed Ubuntu package metadata differs from the pinned manifest")


def _assert_dpkg_healthy(env: dict[str, str]) -> None:
    output = _exact_output(("/usr/bin/dpkg", "--audit"), maximum=1024 * 1024, env=env)
    if output.strip():
        raise PlaywrightProfileError("dpkg package database is not healthy; no recovery was attempted")


def _runner_image_identity() -> dict[str, str]:
    image_os = os.environ.get("RUNNER_IMAGE_OS", "")
    image_version = os.environ.get("RUNNER_IMAGE_VERSION", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", image_os) or not re.fullmatch(
        r"[A-Za-z0-9._-]{1,64}", image_version
    ):
        raise PlaywrightProfileError("hosted acceptance lacks a valid runner image identity")
    return {"imageOS": image_os, "imageVersion": image_version}


def _needrestart_package_version(env: dict[str, str]) -> str:
    owner = _exact_output(
        ("/usr/bin/dpkg-query", "-S", NEEDRESTART_CONFIG),
        maximum=4096,
        env=env,
    )
    if owner != f"needrestart: {NEEDRESTART_CONFIG}\n".encode("ascii"):
        raise PlaywrightProfileError("dpkg configuration fragment is not owned by the exact needrestart package")
    output = _exact_output(
        (
            "/usr/bin/dpkg-query",
            "-W",
            r"-f=${db:Status-Abbrev}\t${binary:Package}\t${Version}\t${Architecture}\n",
            "needrestart",
        ),
        maximum=4096,
        env=env,
    ).decode("utf-8", "strict")
    rows = output.splitlines()
    if len(rows) != 1:
        raise PlaywrightProfileError("needrestart package identity is missing or ambiguous")
    fields = rows[0].split("\t")
    if len(fields) != 4:
        raise PlaywrightProfileError("needrestart package identity is malformed")
    status, package, version, architecture = fields
    if status != "ii " or package != "needrestart" or architecture != "all":
        raise PlaywrightProfileError("needrestart fragment package is not installed as the exact all-architecture package")
    return version


def _needrestart_payload_from_archive(path: Path) -> bytes:
    tar_data = _run(
        ("/usr/bin/dpkg-deb", "--fsys-tarfile", str(path)),
        timeout=120,
        maximum=MAX_NEEDRESTART_TAR_BYTES,
    )
    matches: list[bytes] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:") as package_tar:
            for member in package_tar:
                normalized_name = posixpath.normpath(member.name)
                if normalized_name == "etc/dpkg/dpkg.cfg.d/needrestart" and member.name not in {
                    "etc/dpkg/dpkg.cfg.d/needrestart",
                    "./etc/dpkg/dpkg.cfg.d/needrestart",
                }:
                    raise PlaywrightProfileError("signed needrestart archive has an aliased config-file path")
                if member.name not in {
                    "etc/dpkg/dpkg.cfg.d/needrestart",
                    "./etc/dpkg/dpkg.cfg.d/needrestart",
                }:
                    continue
                if (
                    not member.isfile()
                    or member.uid != 0
                    or member.gid != 0
                    or member.mode & 0o7777 != 0o644
                    or member.size > MAX_DPKG_CONFIG_FILE_BYTES
                ):
                    raise PlaywrightProfileError("signed needrestart archive has unsafe config-file metadata")
                stream = package_tar.extractfile(member)
                if stream is None:
                    raise PlaywrightProfileError("signed needrestart archive is missing config-file data")
                contents = stream.read(MAX_DPKG_CONFIG_FILE_BYTES + 1)
                if len(contents) != member.size or len(contents) > MAX_DPKG_CONFIG_FILE_BYTES:
                    raise PlaywrightProfileError("signed needrestart config file exceeds its size limit")
                matches.append(contents)
    except (OSError, tarfile.TarError) as error:
        raise PlaywrightProfileError("signed needrestart archive filesystem tar is invalid") from error
    if len(matches) != 1:
        raise PlaywrightProfileError("signed needrestart archive must contain exactly one dpkg config fragment")
    return matches[0]


def _authenticate_needrestart_fragment(
    root: Path,
    fragment: dict[str, Any],
    indexes: dict[tuple[str, str, str], dict[str, Any]],
    options: list[str],
    env: dict[str, str],
) -> None:
    metadata = fragment["metadata"]
    if (metadata[3], metadata[4], metadata[2] & 0o7777, metadata[5]) != (0, 0, 0o644, 1):
        raise PlaywrightProfileError("needrestart config fragment has unsafe owner, mode, or link count")
    version = _needrestart_package_version(env)
    record = indexes.get(("needrestart", "all", version))
    if record is None:
        raise PlaywrightProfileError("installed needrestart version is absent from authenticated Noble indexes")
    record = _validate_package_record(record)
    if record["package"] != "needrestart" or record["architecture"] != "all" or record["version"] != version:
        raise PlaywrightProfileError("authenticated needrestart package record has an unexpected identity")
    archive_directory = root / "needrestart-archive"
    archive_directory.mkdir(mode=0o700)
    os.chown(archive_directory, 0, 0)
    _run(
        (
            "/usr/bin/apt-get",
            *options,
            "download",
            f"needrestart={version}",
        ),
        timeout=300,
        env=env,
        cwd=archive_directory,
    )
    expected_name = _apt_cache_filename("needrestart", version, "all")
    entries = list(os.scandir(archive_directory))
    if len(entries) != 1 or entries[0].name != expected_name:
        raise PlaywrightProfileError("needrestart archive download did not produce the exact signed package")
    archive_path = archive_directory / expected_name
    archive_metadata = os.lstat(archive_path)
    if (
        archive_metadata.st_uid != 0
        or archive_metadata.st_mode & 0o022
        or archive_metadata.st_nlink != 1
        or not stat.S_ISREG(archive_metadata.st_mode)
    ):
        raise PlaywrightProfileError("needrestart archive file has unsafe ownership or permissions")
    _verify_archive(archive_path, "needrestart", record)
    package_fragment = _needrestart_payload_from_archive(archive_path)
    installed_digest = hashlib.sha256(fragment["contents"]).digest()
    signed_digest = hashlib.sha256(package_fragment).digest()
    if not hmac.compare_digest(installed_digest, signed_digest):
        raise PlaywrightProfileError("installed needrestart fragment differs from its signed package payload")


def _package_spec(package: str, record: dict[str, Any]) -> str:
    architecture = record["architecture"]
    suffix = f":{architecture}" if architecture != "all" else ""
    return f"{package}{suffix}={record['version']}"


def _apt_cache_filename(package: str, version: str, architecture: str) -> str:
    encoded_version = version.replace(":", "%3a")
    return f"{package}_{encoded_version}_{architecture}.deb"


def _parse_deb_control_fields(output: str) -> dict[str, str]:
    expected = {"Package", "Version", "Architecture"}
    fields: dict[str, str] = {}
    for line in output.splitlines():
        name, separator, value = line.partition(": ")
        if not separator or name not in expected or name in fields or not value:
            raise PlaywrightProfileError("pinned Playwright package archive has invalid control fields")
        fields[name] = value
    if set(fields) != expected:
        raise PlaywrightProfileError("pinned Playwright package archive has incomplete control fields")
    return fields


def _verify_archive(path: Path, package: str, record: dict[str, Any]) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise PlaywrightProfileError("pinned Playwright package archive is missing") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != record["size"] or metadata.st_nlink != 1:
        raise PlaywrightProfileError("pinned Playwright package archive has an invalid file identity")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != record["sha256"]:
        raise PlaywrightProfileError("pinned Playwright package archive digest mismatch")
    fields = _parse_deb_control_fields(_exact_output(
        (
            "/usr/bin/dpkg-deb",
            "--field",
            str(path),
            "Package",
            "Version",
            "Architecture",
        ),
        maximum=4096,
    ).decode("utf-8", "strict"))
    if fields != {
        "Package": package,
        "Version": record["version"],
        "Architecture": record["architecture"],
    }:
        raise PlaywrightProfileError("pinned Playwright package archive identity mismatch")


def _validate_archive_lock(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise PlaywrightProfileError("APT archive lock has an invalid file identity") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or metadata.st_size != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise PlaywrightProfileError("APT archive lock has an unsafe file identity")


def _validate_archive_cache(paths: dict[str, Path], planned: dict[tuple[str, str], str], profile: dict[str, Any]) -> None:
    archive_dir = paths["archives"]
    expected: dict[str, tuple[str, dict[str, Any]]] = {}
    total_bytes = 0
    for (package, architecture), version in planned.items():
        record = profile["packages"][package]
        if architecture != record["architecture"] or version != record["version"]:
            raise PlaywrightProfileError("Playwright download plan changed package identity")
        filename = _apt_cache_filename(package, version, architecture)
        expected[filename] = (package, record)
        total_bytes += record["size"]
    if total_bytes > MAX_AGGREGATE_BYTES:
        raise PlaywrightProfileError("Playwright package download exceeds its aggregate size limit")
    entries = list(os.scandir(archive_dir))
    names = {entry.name for entry in entries}
    unexpected = [entry.name for entry in entries if entry.name not in {"partial", "lock"} and entry.name not in expected]
    present = names - {"partial", "lock"}
    if unexpected or present != set(expected):
        raise PlaywrightProfileError("Playwright package download set differs from the exact apt plan")
    if "lock" in names:
        _validate_archive_lock(archive_dir / "lock")
    for filename, (package, record) in expected.items():
        entry_path = archive_dir / filename
        _verify_archive(entry_path, package, record)


def _validate_package_delta(
    before: dict[tuple[str, str], tuple[str, str]],
    after: dict[tuple[str, str], tuple[str, str]],
    planned: dict[tuple[str, str], str],
) -> dict[tuple[str, str], str]:
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    if not changed <= set(planned):
        raise PlaywrightProfileError("offline Playwright installation changed an unplanned package")
    installed: dict[tuple[str, str], str] = {}
    for key, version in planned.items():
        row = after.get(key)
        if row is None or not row[0].startswith("ii ") or row[1] != version:
            raise PlaywrightProfileError("offline Playwright installation did not produce its exact package state")
        installed[key] = version
    if any(key in before and key not in after for key in before):
        raise PlaywrightProfileError("offline Playwright installation removed a package")
    return installed


def _state_file_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.parent != Path("/run"):
        raise PlaywrightProfileError("Playwright receipt must be stored directly under /run")
    if not re.fullmatch(r"verjson-playwright-[0-9]{1,20}-[0-9]{1,6}-[a-z0-9-]{1,64}\.json", candidate.name):
        raise PlaywrightProfileError("Playwright receipt path is not a workflow-owned path")
    return candidate


class _ReceiptPublication:
    def __init__(self, directory_fd: int, final_name: str) -> None:
        self.directory_fd = directory_fd
        self.final_name = final_name
        self.staging_name = f".{final_name}.stage-{os.getpid()}-{secrets.token_hex(12)}"
        self.identity: tuple[int, int] | None = None
        self.staging_owned = False

    def remember_staging_file(self, descriptor: int) -> None:
        self.staging_owned = True
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PlaywrightProfileError("receipt staging path is not a regular file")
        self.identity = (metadata.st_dev, metadata.st_ino)

    def unlink_owned(self, name: str) -> None:
        if self.identity is None and (name != self.staging_name or not self.staging_owned):
            return
        try:
            metadata = os.stat(name, dir_fd=self.directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        same_inode = self.identity is not None and (metadata.st_dev, metadata.st_ino) == self.identity
        recover_open = (
            name == self.staging_name
            and self.staging_owned
            and self.identity is None
            and stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == os.geteuid()
            and stat.S_IMODE(metadata.st_mode) == 0o600
        )
        if same_inode or recover_open:
            os.unlink(name, dir_fd=self.directory_fd)

    def rollback(self) -> None:
        self.unlink_owned(self.final_name)
        self.unlink_owned(self.staging_name)
        os.fsync(self.directory_fd)


def _publish_receipt_in_directory(
    directory_fd: int,
    final_name: str,
    receipt: dict[str, Any],
    operation: OperationLifecycle,
) -> None:
    publication = _ReceiptPublication(directory_fd, final_name)
    payload = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor: int | None = None
    try:
        operation.check_cancelled()
        try:
            os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise PlaywrightProfileError("Playwright receipt already exists")

        descriptor = os.open(
            publication.staging_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        publication.remember_staging_file(descriptor)
        operation.check_cancelled()
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise PlaywrightProfileError("receipt staging write made no progress")
            remaining = remaining[written:]
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        operation.check_cancelled()
        closing_descriptor = descriptor
        descriptor = None
        os.close(closing_descriptor)
        operation.check_cancelled()

        os.link(
            publication.staging_name,
            final_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        operation.check_cancelled()
        os.unlink(publication.staging_name, dir_fd=directory_fd)
        operation.check_cancelled()
        os.fsync(directory_fd)
        operation.check_cancelled()
        operation.mark_receipt_published()
    except BaseException:
        if descriptor is not None:
            closing_descriptor = descriptor
            descriptor = None
            try:
                os.close(closing_descriptor)
            except OSError:
                pass
        publication.rollback()
        raise


def _write_receipt(state_file: Path, receipt: dict[str, Any]) -> None:
    operation = _CURRENT_OPERATION
    if operation is None:
        raise PlaywrightProfileError("receipt publication requires an active trusted operation")
    state_path = _state_file_path(state_file)
    try:
        directory_fd = os.open(
            state_path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        raise PlaywrightProfileError("Playwright receipt directory is unavailable") from error
    try:
        directory_metadata = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_metadata.st_mode) or directory_metadata.st_uid != 0:
            raise PlaywrightProfileError("Playwright receipt directory is not root-owned")
        _publish_receipt_in_directory(directory_fd, state_path.name, receipt, operation)
    finally:
        os.close(directory_fd)

def _read_receipt(state_file: Path) -> dict[str, Any]:
    try:
        metadata = os.lstat(state_file)
    except OSError as error:
        raise PlaywrightProfileError("Playwright provisioning receipt is unavailable") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o444
        or metadata.st_size > 128 * 1024
        or metadata.st_nlink != 1
    ):
        raise PlaywrightProfileError("Playwright provisioning receipt has unsafe file metadata")
    try:
        document = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlaywrightProfileError("Playwright provisioning receipt is malformed") from error
    if not isinstance(document, dict):
        raise PlaywrightProfileError("Playwright provisioning receipt has an invalid shape")
    return document


@_operation_cancellable
def provision(
    profile_id: str,
    state_file: str | Path,
    runner_environment: str,
    runner_os: str,
    *,
    require_nonempty_plan: bool = False,
) -> None:
    operation = _CURRENT_OPERATION
    if operation is None:
        raise PlaywrightProfileError("Playwright provisioning requires an active trusted operation")
    operation.check_cancelled()
    _require_root()
    profile = resolve_profile(profile_id)
    os_release = _read_os_release()
    architecture = _exact_output(('/usr/bin/dpkg', '--print-architecture'), maximum=64).decode('ascii').strip()
    validate_runner_platform(
        os_release,
        architecture=architecture,
        runner_environment=runner_environment,
        runner_os=runner_os,
    )
    image_identity = _runner_image_identity() if require_nonempty_plan else {}
    state_path = _state_file_path(state_file)
    _validate_safe_key()
    root = _create_private_temp_root()
    receipt: dict[str, Any] | None = None
    try:
        paths = _make_apt_environment(root)
        options = _apt_options(paths)
        apt_environment = _apt_environment(paths)
        snapshot = _scan_global_dpkg_config()
        suppress_needrestart = _validate_global_dpkg_config(snapshot)
        _assert_dpkg_healthy(apt_environment)
        before = _installed_package_state(apt_environment)
        _run(("/usr/bin/apt-get", *options, "update"), timeout=600, env=apt_environment)
        closure_specs = [
            _package_spec(name, record)
            for name, record in sorted(profile["packages"].items())
        ]
        plan_output = _run(
            (
                "/usr/bin/apt-get",
                *options,
                "--simulate",
                "--no-install-recommends",
                "--no-remove",
                "--yes",
                "install",
                *closure_specs,
            ),
            timeout=180,
            env=apt_environment,
        ).decode("utf-8", "strict")
        planned = parse_install_plan(plan_output, profile)
        _reject_dpkg_install_plan(planned)
        if require_nonempty_plan and not planned:
            raise PlaywrightProfileError("hosted acceptance requires a nonempty exact package-install plan")
        clean_plan_output = _run(
            (
                "/usr/bin/apt-get",
                *options,
                "-o",
                "Dir::State::status=/dev/null",
                "--simulate",
                "--no-install-recommends",
                "--no-remove",
                "--yes",
                "install",
                *closure_specs,
            ),
            timeout=180,
            env=apt_environment,
        ).decode("utf-8", "strict")
        complete_closure = parse_install_plan(clean_plan_output, profile)
        expected_closure = {
            (name, record["architecture"]): record["version"]
            for name, record in profile["packages"].items()
        }
        if complete_closure != expected_closure or not set(planned) <= set(complete_closure):
            raise PlaywrightProfileError("Playwright apt plan differs from its complete pinned dependency closure")
        indexes = _read_package_indexes(paths)
        _validate_signed_metadata(indexes, complete_closure, profile)
        needrestart = snapshot["fragments"].get("needrestart")
        if needrestart is not None:
            _authenticate_needrestart_fragment(root, needrestart, indexes, options, apt_environment)
        config_view = _make_dpkg_config_view(root, snapshot, suppress_needrestart)
        if _scan_global_dpkg_config() != snapshot:
            raise PlaywrightProfileError("host dpkg configuration changed during provisioning")
        if planned:
            _run(
                (
                    "/usr/bin/apt-get",
                    *options,
                    "--download-only",
                    "--no-install-recommends",
                    "--no-remove",
                    "--yes",
                    "install",
                    *closure_specs,
                ),
                timeout=600,
                env=apt_environment,
            )
            _validate_archive_cache(paths, planned, profile)
            exact_specs = [
                _package_spec(package, profile["packages"][package])
                for package, _architecture in sorted(planned)
            ]
            apt_environment = _apt_environment(paths)
            _run_dpkg_install_in_namespace(
                (
                    "/usr/bin/apt-get",
                    *options,
                    "--no-download",
                    "--no-install-recommends",
                    "--no-remove",
                    "--yes",
                    "install",
                    *exact_specs,
                ),
                config_view,
                apt_environment,
                timeout=600,
            )
            if _scan_global_dpkg_config() != snapshot:
                raise PlaywrightProfileError("host dpkg configuration bytes or metadata changed during install")
            _apt_environment(paths)
        after = _installed_package_state(apt_environment)
        _validate_package_delta(before, after, planned)
        _assert_dpkg_healthy(apt_environment)
        package_versions = _installed_profile_closure(profile, after)
        receipt = create_receipt(profile_id, package_versions)
    finally:
        operation.remove_temp_root(root)
    if receipt is None:
        raise PlaywrightProfileError("Playwright profile install ended without a complete receipt")
    operation.check_cancelled()
    if root.exists():
        raise PlaywrightProfileError("private APT state remains after provisioning cleanup")
    _write_receipt(state_path, receipt)
    if require_nonempty_plan:
        evidence = {
            "event": "verjson-playwright-profile-hosted-acceptance",
            "runner": {
                **image_identity,
                "runnerEnvironment": runner_environment,
                "runnerOS": runner_os,
                "distribution": os_release["ID"],
                "versionId": os_release["VERSION_ID"],
                "architecture": architecture,
            },
            "installPlan": [
                {"package": package, "architecture": architecture, "version": version}
                for (package, architecture), version in sorted(planned.items())
            ],
            "dpkgInInstallPlan": False,
            "dpkgAudit": "clean-before-and-after",
            "hostDpkgConfiguration": "unchanged",
            "aptPrivateStateRemoved": True,
            "receipt": {
                "profileId": receipt["profileId"],
                "packageCount": len(receipt["packages"]),
                "packageStateSha256": receipt["packageStateSha256"],
            },
        }
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")), flush=True)


def verify_runtime(profile_id: str, state_file: str | Path) -> None:
    profile = resolve_profile(profile_id)
    validate_runner_platform(
        _read_os_release(),
        architecture=_exact_output(("/usr/bin/dpkg", "--print-architecture"), maximum=64).decode("ascii").strip(),
        runner_environment=os.environ.get("RUNNER_ENVIRONMENT", ""),
        runner_os=os.environ.get("RUNNER_OS", ""),
    )
    state_path = _state_file_path(state_file)
    receipt = _read_receipt(state_path)
    package_versions = {
        (package, architecture): version
        for package, architecture, version in (
            (record["package"], record["architecture"], record["version"])
            for record in receipt.get("packages", [])
            if isinstance(record, dict)
        )
    }
    if not package_versions:
        raise PlaywrightProfileError("Playwright provisioning receipt has no installed packages")
    verify_receipt(receipt, profile_id, package_versions)
    current = _installed_package_state()
    if _installed_profile_closure(profile, current) != package_versions:
        raise PlaywrightProfileError("Playwright installed package state changed after provisioning")


def _parse_expected_receipt_identity(identity: str) -> tuple[int, int]:
    match = re.fullmatch(r"(0|[1-9][0-9]*):(0|[1-9][0-9]*)", identity)
    if match is None:
        raise PlaywrightProfileError("Playwright cleanup requires an invocation receipt identity")
    return int(match.group(1)), int(match.group(2))


def cleanup(state_file: str | Path, expected_identity: str) -> None:
    _require_root()
    state_path = _state_file_path(state_file)
    expected_device, expected_inode = _parse_expected_receipt_identity(expected_identity)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        directory_fd = os.open(state_path.parent, directory_flags)
    except FileNotFoundError:
        return
    try:
        directory_metadata = os.fstat(directory_fd)
        owner_uid = os.geteuid()
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != owner_uid
            or directory_metadata.st_mode & 0o022
        ):
            raise PlaywrightProfileError("Playwright cleanup refused unsafe receipt directory")
        try:
            receipt_fd = os.open(
                state_path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return
        try:
            metadata = os.fstat(receipt_fd)
            identity = (metadata.st_dev, metadata.st_ino)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o444
                or identity != (expected_device, expected_inode)
            ):
                raise PlaywrightProfileError(
                    "Playwright cleanup refused receipt not created by this invocation"
                )
            current = os.stat(state_path.name, dir_fd=directory_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity:
                raise PlaywrightProfileError("Playwright receipt changed during cleanup")
            os.unlink(state_path.name, dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            os.close(receipt_fd)
    finally:
        os.close(directory_fd)


def _cli_error_detail(error: BaseException) -> str:
    if not isinstance(error, PlaywrightProfileError):
        return ""
    escaped: list[str] = []
    size = 0
    for character in str(error):
        if character == "%":
            encoded = "%25"
        elif character == "\r":
            encoded = "%0D"
        elif character == "\n":
            encoded = "%0A"
        elif not character.isprintable():
            encoded = f"%{ord(character):02X}"
        else:
            encoded = character
        if size + len(encoded) > 240:
            break
        escaped.append(encoded)
        size += len(encoded)
    return "".join(escaped)


def _command_line(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--profile", required=True)
    validate_parser.add_argument("--runner-environment", required=True)
    validate_parser.add_argument("--runner-os", required=True)
    provision_parser = subparsers.add_parser("provision")
    provision_parser.add_argument("--profile", required=True)
    provision_parser.add_argument("--state-file", required=True)
    provision_parser.add_argument("--runner-environment", required=True)
    provision_parser.add_argument("--runner-os", required=True)
    provision_parser.add_argument("--require-nonempty-plan", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--profile", required=True)
    verify_parser.add_argument("--state-file", required=True)
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--state-file", required=True)
    cleanup_parser.add_argument("--expected-identity", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            profile = resolve_profile(args.profile)
            validate_runner_platform(
                _read_os_release(),
                architecture=_exact_output(("/usr/bin/dpkg", "--print-architecture"), maximum=64).decode("ascii").strip(),
                runner_environment=args.runner_environment,
                runner_os=args.runner_os,
            )
            if not (os.environ.get("VERJSON_CI_SECRETLESS_MODE") == "true"):
                raise PlaywrightProfileError("Playwright system-library profile requires a secretless script plan")
            del profile
        elif args.command == "provision":
            if os.environ.get("VERJSON_CI_SECRETLESS_MODE") != "true":
                raise PlaywrightProfileError("Playwright system-library profile requires a secretless script plan")
            provision(
                args.profile,
                args.state_file,
                args.runner_environment,
                args.runner_os,
                require_nonempty_plan=args.require_nonempty_plan,
            )
        elif args.command == "verify":
            if os.environ.get("VERJSON_CI_SECRETLESS_MODE") != "true":
                raise PlaywrightProfileError("Playwright system-library profile requires a secretless script plan")
            verify_runtime(args.profile, args.state_file)
        else:
            cleanup(args.state_file, args.expected_identity)
    except (OSError, subprocess.SubprocessError, UnicodeError, PlaywrightProfileError) as error:
        detail = _cli_error_detail(error)
        suffix = f": {detail}" if detail else ""
        print(
            f"::error::trusted Playwright system-library profile failed ({type(error).__name__}){suffix}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_command_line())
