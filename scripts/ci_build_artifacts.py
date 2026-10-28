#!/usr/bin/env python3
"""Trusted same-run build ArtifactSet validation and transfer helpers."""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import io
import json
import os
import queue
import re
import select
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_FILES = 512
MAX_PARTICIPANTS = 16
MAX_DEPTH = 32
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_TAR_EXTENSION_BYTES = 16 * 1024
MAX_TAR_EXTENSION_HEADERS = MAX_FILES + 1
MAX_TAR_EXTENSION_CHAIN_DEPTH = 16
MAX_TAR_EXTENSION_TOTAL_BYTES = MAX_TAR_EXTENSION_HEADERS * (
    tarfile.BLOCKSIZE + MAX_TAR_EXTENSION_BYTES
)
MAX_PLAN_BYTES = 48 * 1024
MAX_POLICY_BYTES = 64 * 1024
MAX_AGGREGATE_NEEDS_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 2 * 1024
MAX_BWRAP_STATUS_BYTES = 16 * 1024
MAX_BWRAP_STATUS_LINE_BYTES = 4096
MAX_BWRAP_STATUS_RECORDS = 64
MAX_TRUSTED_WRAPPER_STATUS_BYTES = 4096
RUNTIME_INPUT_DEFAULTS: dict[str, Any] = {
    "ci-level-id": "ci",
    "ci-level-fail-fast": True,
    "node-version": "24",
    "timeout-minutes": 30,
    "cache": False,
    "cache-max-mb": 1024,
    "cache-dependency-path": "package-lock.json",
    "browser-cache": False,
    "package-manager": "npm",
    "scope": "@verjson",
    "secretless-pr": False,
    "secretless-trusted-ref": False,
    "secretless-runtime-public-cache": False,
    "approved-internal-packages": "",
    "approved-internal-scopes": "@verjson",
    "secretless-compatibility-ranges": "",
    "secretless-auxiliary-source": "",
    "secretless-rebuild-packages": "",
    "secretless-skip-install-scripts": "",
    "secretless-rebuild-env": "{}",
    "secretless-nested-manifests": "",
    "secretless-ci-script-plan": "",
    "secretless-ci-non-app-script-plan": "",
    "secretless-ci-non-app-paths": "",
    "schema-dir": "",
    "postgres-sandbox-profile": "",
    "runner": "",
    "db-image": "",
    "db-port": "5432",
    "db-health-cmd": "pg_isready",
    "db-host": "auto",
    "db-env": "",
    "cache-image": "",
    "cache-health-cmd": "redis-cli ping",
    "cache-env": "",
}
RUNTIME_INPUT_ENV_PREFIX = "BUILD_ARTIFACT_RUNTIME_INPUT_"
_ID = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_EXACT_NODE_VERSION = re.compile(r"v?[0-9]+\.[0-9]+\.[0-9]+\Z")
_EXACT_PACKAGE_MANAGER_VERSION = re.compile(r"v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")
_POLICY_FIELDS = {
    "repository", "visibility", "producer", "consumers", "buildScript",
    "allowedPaths", "requiredPaths", "profileFiles", "audienceApproved",
    "callerWorkflowPath", "callerWorkflowDigest",
}
_MANIFEST_FIELDS = {
    "version", "repository", "ref", "commit", "callerWorkflowSha",
    "reusableWorkflowSha", "runId", "runAttempt", "lane", "profile",
    "commands", "inputs", "toolchain", "exportPolicy", "files",
}


class ArtifactError(ValueError):
    """Invalid artifact request or untrusted artifact data."""


class IntegrityError(ArtifactError):
    """Trusted input or authenticated workflow result failed integrity checks."""


_TAR_EXTENSION_TYPES = frozenset(
    {
        tarfile.XHDTYPE,
        tarfile.XGLTYPE,
        tarfile.SOLARIS_XHDTYPE,
        tarfile.GNUTYPE_LONGNAME,
        tarfile.GNUTYPE_LONGLINK,
    }
)


class _ReplayReader:
    def __init__(self, prefix: bytes, source: Any) -> None:
        self._prefix = prefix
        self._offset = 0
        self._source = source

    def read(self, size: int) -> bytes:
        remaining = len(self._prefix) - self._offset
        if size <= remaining:
            result = self._prefix[self._offset:self._offset + size]
            self._offset += len(result)
            return result
        result = self._prefix[self._offset:]
        self._offset = len(self._prefix)
        return result + self._source.read(size - len(result))

    def tell(self) -> int:
        return self._source.tell() - (len(self._prefix) - self._offset)


def _validate_pax_extension_block(block: bytes, payload_size: int) -> None:
    payload = block[:payload_size]
    padding = block[payload_size:]
    if any(padding):
        raise ArtifactError("ArtifactSet PAX extension has nonzero padding")
    if b"\x00" in payload:
        raise ArtifactError("ArtifactSet PAX extension contains a NUL byte")

    offset = 0
    while offset < len(payload):
        separator = payload.find(b" ", offset)
        if separator <= offset:
            raise ArtifactError("ArtifactSet PAX extension record framing is invalid")
        length_bytes = payload[offset:separator]
        if not length_bytes.isdigit() or len(length_bytes) > 10:
            raise ArtifactError("ArtifactSet PAX extension record length is invalid")
        try:
            record_length = int(length_bytes)
        except ValueError as exc:
            raise ArtifactError("ArtifactSet PAX extension record length is invalid") from exc
        if record_length < separator - offset + 4:
            raise ArtifactError("ArtifactSet PAX extension record is too short")
        end = offset + record_length
        if end > len(payload) or payload[end - 1] != 0x0A:
            raise ArtifactError("ArtifactSet PAX extension record is truncated")
        field = payload[separator + 1:end - 1]
        key, equals, _value = field.partition(b"=")
        if not equals or not key:
            raise ArtifactError("ArtifactSet PAX extension record is malformed")
        if key == b"size" or key.startswith(b"GNU.sparse"):
            raise ArtifactError("ArtifactSet PAX extension contains a forbidden key")
        offset = end


class _BoundedTarInfo(tarfile.TarInfo):
    def _require_bounded_extension(self) -> int:
        if type(self.size) is not int or self.size < 0 or self.size > MAX_TAR_EXTENSION_BYTES:
            raise ArtifactError("ArtifactSet tar extension header exceeds its size limit")
        return self._block(self.size)

    def _guarded_process(
        self,
        archive: _BoundedTarFile,
        process: Callable[[_BoundedTarFile], tarfile.TarInfo],
    ) -> tarfile.TarInfo:
        if self.type == tarfile.GNUTYPE_SPARSE:
            raise ArtifactError("ArtifactSet archive contains a GNU sparse member")

        previous_depth = archive._artifact_extension_chain_depth
        if self.type in _TAR_EXTENSION_TYPES:
            padded_size = self._require_bounded_extension()
            extension_count = archive._artifact_extension_count + 1
            extension_bytes = archive._artifact_extension_bytes + tarfile.BLOCKSIZE + padded_size
            extension_depth = previous_depth + 1
            if extension_count > MAX_TAR_EXTENSION_HEADERS:
                raise ArtifactError("ArtifactSet archive exceeds extension header count limit")
            if extension_bytes > MAX_TAR_EXTENSION_TOTAL_BYTES:
                raise ArtifactError("ArtifactSet archive exceeds cumulative extension byte limit")
            if extension_depth > MAX_TAR_EXTENSION_CHAIN_DEPTH:
                raise ArtifactError("ArtifactSet archive exceeds consecutive extension depth limit")
            archive._artifact_extension_count = extension_count
            archive._artifact_extension_bytes = extension_bytes
            archive._artifact_extension_chain_depth = extension_depth
        else:
            archive._artifact_extension_chain_depth = 0

        try:
            return process(archive)
        finally:
            archive._artifact_extension_chain_depth = previous_depth

    if hasattr(tarfile.TarInfo, "_proc_member"):
        def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
            return self._guarded_process(archive, super()._proc_member)
    else:
        def _proc(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
            return self._guarded_process(archive, super()._proc)

    def _proc_pax(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        padded_size = self._require_bounded_extension()
        source = archive.fileobj
        block = source.read(padded_size)
        if len(block) != padded_size:
            raise ArtifactError("ArtifactSet PAX extension block is truncated")
        # tarfile applies PAX metadata while parsing, so reject forbidden size/sparse
        # overrides and malformed framing before the standard parser can use them.
        _validate_pax_extension_block(block, self.size)

        # The tar stream cannot seek backward, so replay the bounded block to the
        # standard parser after validating every byte it will inspect.
        archive.fileobj = _ReplayReader(block, source)
        try:
            return super()._proc_pax(archive)
        finally:
            archive.fileobj = source

    def _proc_gnulong(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        self._require_bounded_extension()
        return super()._proc_gnulong(archive)


class _BoundedTarFile(tarfile.TarFile):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._artifact_extension_count = 0
        self._artifact_extension_bytes = 0
        self._artifact_extension_chain_depth = 0
        super().__init__(*args, **kwargs)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


SCRIPT_ENTRY_RECEIPT_VERSION = 2
MAX_SCRIPT_PLAN_ENTRIES = 256
MAX_SCRIPT_ENTRY_RECEIPT_BYTES = 16 * 1024
_SCRIPT_ENTRY_IDENTITY_FIELDS = {
    "repository",
    "event",
    "ref",
    "commit",
    "workflowSha",
    "runId",
    "runAttempt",
    "jobId",
    "levelId",
}
_SCRIPT_ENTRY_FIELDS = {"directory", "script", "unsetEnv", "requiresServices"}
_SCRIPT_ENTRY_RECEIPT_FIELDS = {
    "version",
    "identity",
    "planDigest",
    "ordinal",
    "entry",
    "state",
    "exitCode",
    "signal",
    "candidateStarted",
    "hostCancellationSignal",
    "supervisorSignal",
    "cleanup",
}
_SCRIPT_PLAN_RECEIPT_FIELDS = {
    "version",
    "identity",
    "planDigest",
    "entryCount",
    "state",
    "hostCancellationSignal",
}


@dataclass(frozen=True)
class ScriptEntryExecutionResult:
    return_code: int
    cleanup_status: str = "verified"


@dataclass(frozen=True)
class ScriptPlanRunResult:
    exit_code: int
    receipt_directory: Path
    plan_digest: str


class ScriptPlanExecutionError(ArtifactError):
    def __init__(self, ordinal: int, receipt_directory: Path, failure: BaseException):
        self.ordinal = ordinal
        self.receipt_directory = receipt_directory
        super().__init__(
            f"trusted script supervisor failed at entry {ordinal}: "
            f"{type(failure).__name__}: {failure}"
        )


class ScriptPlanInterrupted(Exception):
    def __init__(
        self,
        signum: int,
        *,
        candidate_started: bool = False,
        candidate_exit_code: int | None = None,
        candidate_signal: int | None = None,
        supervisor_signal: int | None = None,
    ):
        if not isinstance(signum, int) or signum not in {signal.SIGINT, signal.SIGTERM}:
            raise ValueError("script-plan interruption requires a positive signal number")
        self.signum = signum
        self.candidate_started = candidate_started
        self.candidate_exit_code = candidate_exit_code
        self.candidate_signal = candidate_signal
        self.supervisor_signal = supervisor_signal
        self.cleanup_status = "unverified"
        super().__init__(f"script plan interrupted by signal {signum}")


class HostSignalQueue:
    """Capture host signals in a reentrant handler and latch them on the main loop."""

    def __init__(self) -> None:
        self._signals: queue.SimpleQueue[int] = queue.SimpleQueue()
        self.host_cancel_signal: int | None = None
        self._acceptance_closed = False

    def capture(self, signum: int, _frame: Any = None) -> None:
        if not self._acceptance_closed:
            self._signals.put(signum)

    @staticmethod
    def _validate(signum: Any) -> int:
        if not isinstance(signum, int) or signum not in {signal.SIGINT, signal.SIGTERM}:
            raise IntegrityError("host cancellation signal is invalid")
        return signum

    def _latch(self, signum: Any) -> int:
        validated = self._validate(signum)
        if self.host_cancel_signal is None:
            self.host_cancel_signal = validated
        return self.host_cancel_signal

    def _read_one(self) -> int | None:
        try:
            signum = self._signals.get_nowait()
        except queue.Empty:
            return None
        return self._latch(signum)

    def _drain_pending(self) -> None:
        while True:
            try:
                signum = self._signals.get_nowait()
            except queue.Empty:
                return
            self._latch(signum)

    def launch_decision(self) -> int | None:
        if self.host_cancel_signal is not None:
            return self.host_cancel_signal
        return self._read_one()

    def poll(self) -> int | None:
        if self._acceptance_closed:
            return self.host_cancel_signal
        if self.host_cancel_signal is None:
            self._read_one()
        self._drain_pending()
        return self.host_cancel_signal

    def acceptance_cutoff(self) -> int | None:
        if self._acceptance_closed:
            return self.host_cancel_signal
        self._acceptance_closed = True
        self._drain_pending()
        return self.host_cancel_signal


class _CallableHostSignalQueue:
    def __init__(self, read_signal: Callable[[], int | None]):
        self._read_signal = read_signal
        self.host_cancel_signal: int | None = None
        self._acceptance_closed = False

    def _poll(self) -> int | None:
        if self._acceptance_closed:
            return self.host_cancel_signal
        if self.host_cancel_signal is None:
            signum = self._read_signal()
            if signum is not None:
                self.host_cancel_signal = HostSignalQueue._validate(signum)
        return self.host_cancel_signal

    def poll(self) -> int | None:
        return self._poll()

    def launch_decision(self) -> int | None:
        return self._poll()

    def acceptance_cutoff(self) -> int | None:
        if self._acceptance_closed:
            return self.host_cancel_signal
        self._acceptance_closed = True
        if self.host_cancel_signal is None:
            signum = self._read_signal()
            if signum is not None:
                self.host_cancel_signal = HostSignalQueue._validate(signum)
        return self.host_cancel_signal


def _coerce_host_signal_queue(
    host_signals: HostSignalQueue | _CallableHostSignalQueue | None,
    cancellation_signal: Callable[[], int | None] | None,
) -> HostSignalQueue | _CallableHostSignalQueue:
    if host_signals is not None and cancellation_signal is not None:
        raise IntegrityError("provide one host cancellation source")
    if host_signals is not None:
        if not all(
            callable(getattr(host_signals, method, None))
            for method in ("poll", "launch_decision", "acceptance_cutoff")
        ):
            raise IntegrityError("host cancellation queue is invalid")
        return host_signals
    if cancellation_signal is not None:
        if not callable(cancellation_signal):
            raise IntegrityError("host cancellation callback is invalid")
        return _CallableHostSignalQueue(cancellation_signal)
    return HostSignalQueue()


class ScriptCandidateTerminal(ArtifactError):
    def __init__(
        self,
        message: str,
        *,
        return_code: int | None = None,
        signum: int | None = None,
        cleanup_status: str = "unverified",
        candidate_started: bool = False,
        supervisor_signal: int | None = None,
        host_cancellation_signal: int | None = None,
        infrastructure_failure: bool = False,
    ):
        if host_cancellation_signal is not None and host_cancellation_signal not in {
            signal.SIGINT,
            signal.SIGTERM,
        }:
            raise ValueError("script candidate terminal host signal is invalid")
        self.return_code = return_code
        self.signum = signum
        self.cleanup_status = cleanup_status
        self.candidate_started = candidate_started
        self.supervisor_signal = supervisor_signal
        self.host_cancellation_signal = host_cancellation_signal
        self.infrastructure_failure = infrastructure_failure
        super().__init__(message)


def _verify_candidate_runtime_boundary(
    candidate_result: Any,
    verify_boundary: Callable[[], None],
) -> None:
    try:
        verify_boundary()
    except Exception as failure:
        raise ScriptCandidateTerminal(
            "candidate runtime boundary verification failed",
            return_code=getattr(candidate_result, "return_code", None),
            signum=getattr(candidate_result, "signum", None),
            cleanup_status="unverified",
            candidate_started=bool(getattr(candidate_result, "candidate_started", False)),
            supervisor_signal=getattr(candidate_result, "supervisor_signal", None),
            host_cancellation_signal=getattr(
                candidate_result, "host_cancellation_signal", None
            ),
            infrastructure_failure=True,
        ) from failure


def _script_candidate_cleanup_failure(
    candidate_result: Any,
    candidate_failure: BaseException | None = None,
    *,
    cleanup_status: str = "failed",
) -> ScriptCandidateTerminal:
    if cleanup_status not in {"failed", "unverified"}:
        raise ValueError("candidate cleanup status must be failed or unverified")
    return_code = getattr(candidate_result, "return_code", None)
    if return_code is None:
        return_code = getattr(candidate_failure, "return_code", None)
    if return_code is None:
        return_code = getattr(candidate_failure, "candidate_exit_code", None)
    candidate_signal = getattr(candidate_result, "signum", None)
    if candidate_signal is None:
        candidate_signal = getattr(candidate_failure, "candidate_signal", None)
    if candidate_signal is None and isinstance(candidate_failure, ScriptCandidateTerminal):
        candidate_signal = candidate_failure.signum
    supervisor_signal = getattr(candidate_result, "supervisor_signal", None)
    if supervisor_signal is None:
        supervisor_signal = getattr(candidate_failure, "supervisor_signal", None)
    host_cancellation_signal = getattr(candidate_result, "host_cancellation_signal", None)
    if host_cancellation_signal is None:
        host_cancellation_signal = getattr(candidate_failure, "host_cancellation_signal", None)
    if host_cancellation_signal is None and isinstance(candidate_failure, ScriptPlanInterrupted):
        host_cancellation_signal = candidate_failure.signum
    candidate_started = bool(
        getattr(candidate_result, "candidate_started", False)
        or getattr(candidate_failure, "candidate_started", False)
    )
    return ScriptCandidateTerminal(
        "candidate cleanup failed after execution",
        return_code=return_code,
        signum=candidate_signal,
        cleanup_status=cleanup_status,
        candidate_started=candidate_started,
        supervisor_signal=supervisor_signal,
        host_cancellation_signal=host_cancellation_signal,
    )


def _verify_script_candidate_cleanup(
    candidate_result: Any,
    candidate_failure: BaseException | None,
    verify_cleanup: Callable[[], bool],
) -> None:
    try:
        cleanup_failed = verify_cleanup()
    except BaseException as error:
        raise _script_candidate_cleanup_failure(
            candidate_result,
            candidate_failure,
            cleanup_status="unverified",
        ) from error
    if cleanup_failed:
        raise _script_candidate_cleanup_failure(candidate_result, candidate_failure)


@dataclass(frozen=True)
class TrustedCandidateStatus:
    state: str
    return_code: int | None
    signum: int | None
    cleanup: str
    reaped_count: int
    candidate_started: bool
    host_cancellation_signal: int | None
    supervisor_signal: int | None


def _script_candidate_status_failure(status: TrustedCandidateStatus) -> ScriptCandidateTerminal:
    return ScriptCandidateTerminal(
        f"trusted sandbox ended in {status.state}",
        return_code=status.return_code,
        signum=status.signum,
        cleanup_status="verified" if status.cleanup == "complete" else status.cleanup,
        candidate_started=status.candidate_started,
        supervisor_signal=status.supervisor_signal,
        host_cancellation_signal=status.host_cancellation_signal,
        infrastructure_failure=True,
    )


SANDBOX_SUPERVISOR_ENTRYPOINT = r'''import ctypes, json, os, queue, re, select, signal, subprocess, sys, time
from pathlib import Path
watched_signals = {signal.SIGINT, signal.SIGTERM}
supervisor_signals = queue.SimpleQueue()
supervisor_signal = None
candidate = None
candidate_started = False
candidate_reaped = False
candidate_outcome_emitted = False
candidate_not_started_emitted = False
candidate_exit_code = None
candidate_signal = None
reaped_count = 0
cleanup_state = "not-started"
control_state = None
control_signal = None
ready_emitted = False
cleanup_started = False
result_state = "bootstrap-failure"
result_code = 125

def capture_signal(signum, _frame):
    supervisor_signals.put(signum)

def latch_supervisor_signal():
    global supervisor_signal
    while True:
        try:
            signum = supervisor_signals.get_nowait()
        except queue.Empty:
            break
        if signum not in watched_signals:
            raise RuntimeError("trusted supervisor captured an unsupported signal")
        if supervisor_signal is None:
            supervisor_signal = int(signum)
    return supervisor_signal

def emit(event, **fields):
    record = {"version": 2, "pid": os.getpid(), "event": event}
    record.update(fields)
    sys.stdout.buffer.write(json.dumps(record, separators=(",", ":")).encode("ascii") + b"\n")
    sys.stdout.buffer.flush()

def enforce_candidate_security_state():
    injected_signal = os.environ.pop("VERJSON_CI_TEST_SIGNAL_DURING_SECURITY", None)
    if injected_signal is not None:
        if injected_signal != "SIGTERM":
            raise RuntimeError("invalid candidate security test signal")
        os.kill(1, signal.SIGTERM)
    status_text = Path("/proc/self/status").read_text(encoding="utf-8")
    apparmor_profile = Path("/proc/self/attr/current").read_text(encoding="utf-8")
    if os.environ.pop("VERJSON_CI_TEST_FORCE_INVALID_APPARMOR", None) == "1":
        apparmor_profile = "unconfined"
    fields = {
        line.split(":", 1)[0]: line.split(":", 1)[1].strip()
        for line in status_text.splitlines()
        if ":" in line
    }
    try:
        capabilities_empty = all(
            int(fields[name], 16) == 0
            for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
        )
    except (KeyError, ValueError) as error:
        raise RuntimeError("candidate sandbox capability state is malformed") from error
    if not capabilities_empty:
        raise RuntimeError("candidate sandbox retains process capabilities")
    if not re.search(r"(?:^|\s)bwrap//&unpriv_bwrap \(enforce\)(?:\s|$)", apparmor_profile):
        raise RuntimeError("candidate sandbox enforcing AppArmor profile is missing")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise RuntimeError("cannot set candidate sandbox no_new_privs")
    status_text = Path("/proc/self/status").read_text(encoding="utf-8")
    fields = {
        line.split(":", 1)[0]: line.split(":", 1)[1].strip()
        for line in status_text.splitlines()
        if ":" in line
    }
    if fields.get("NoNewPrivs") != "1":
        raise RuntimeError("candidate sandbox no_new_privs state is missing")

def namespace_pids():
    return sorted(
        int(name)
        for name in os.listdir("/proc")
        if name.isdigit() and int(name) != 1
    )

def reap_adopted_children():
    global reaped_count
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return
        reaped_count += 1

def terminate_namespace_children():
    global cleanup_state
    cleanup_state = "terminating"
    term_deadline = time.monotonic() + 0.5
    kill_deadline = None
    while True:
        now = time.monotonic()
        pids = namespace_pids()
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM if kill_deadline is None else signal.SIGKILL)
            except ProcessLookupError:
                pass
        reap_adopted_children()
        if not namespace_pids():
            cleanup_state = "complete"
            return
        if kill_deadline is None and now >= term_deadline:
            kill_deadline = now + 3.0
        elif kill_deadline is not None and now >= kill_deadline:
            raise RuntimeError("namespace descendants survived bounded SIGKILL cleanup")
        time.sleep(0.02)

def read_control():
    deadline = time.monotonic() + 5.0
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("host control frame timed out")
        readable, _writable, _exceptional = select.select([0], [], [], remaining)
        if not readable:
            raise TimeoutError("host control frame timed out")
        chunk = os.read(0, 64 - len(data))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > 32:
            raise RuntimeError("host control frame exceeds its size limit")

def publish_terminal():
    global candidate_signal, cleanup_started, cleanup_state, reaped_count, result_code, result_state
    if not ready_emitted:
        emit(
            "terminal",
            state=result_state,
            candidateStarted=False,
            exitCode=None,
            candidateSignal=None,
            supervisorSignal=None,
            cleanup="not-started",
            reaped=reaped_count,
            control=None,
            controlSignal=None,
        )
        return
    if not cleanup_started:
        cleanup_started = True
        emit("cleanup-start")
        try:
            if candidate_started and candidate is not None and candidate.poll() is None:
                candidate.kill()
                candidate.wait()
                reaped_count += 1
                candidate_signal = signal.SIGKILL
            terminate_namespace_children()
            emit("cleanup-complete")
            cleanup_state = "complete"
        except BaseException:
            cleanup_state = "failed"
            result_state = "cleanup-failure"
            result_code = 125
    emit(
        "terminal",
        state=result_state,
        candidateStarted=candidate_started,
        exitCode=candidate_exit_code,
        candidateSignal=candidate_signal,
        supervisorSignal=supervisor_signal,
        cleanup=cleanup_state,
        reaped=reaped_count,
        control=control_state,
        controlSignal=control_signal,
    )

def main():
    global candidate, candidate_started, candidate_reaped, candidate_outcome_emitted
    global candidate_not_started_emitted, candidate_exit_code, candidate_signal
    global reaped_count
    global cleanup_state, control_state, control_signal, ready_emitted
    global cleanup_started, result_state, result_code, supervisor_signal
    try:
        if os.getpid() != 1:
            raise RuntimeError("trusted sandbox supervisor is not PID 1")
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(4, 0, 0, 0, 0) != 0 or libc.prctl(3, 0, 0, 0, 0) != 0:
            raise RuntimeError("cannot make trusted sandbox supervisor non-dumpable")
        if os.getpid() != 1:
            raise RuntimeError("trusted supervisor PID changed during bootstrap")
        maximum_fd = os.sysconf("SC_OPEN_MAX")
        if maximum_fd < 3:
            raise RuntimeError("invalid file descriptor limit")
        os.closerange(3, maximum_fd)
        signal.pthread_sigmask(signal.SIG_BLOCK, watched_signals)
        for caught_signal in watched_signals:
            signal.signal(caught_signal, capture_signal)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, watched_signals)
        enforce_candidate_security_state()
        candidate_environment = os.environ.copy()
        candidate_environment.pop("VERJSON_CI_TEST_AFTER_ACK_MARKER", None)
        candidate_environment.pop("VERJSON_CI_TEST_FORCE_INVALID_APPARMOR", None)
        candidate_environment.pop("VERJSON_CI_TEST_SIGNAL_DURING_SECURITY", None)
        pid_namespace = os.stat("/proc/self/ns/pid").st_ino
        ready_emitted = True
        emit("ready", pidNamespace=pid_namespace)
        control = read_control()
        if control in (b"abort:2\n", b"abort:15\n"):
            control_state = "abort"
            control_signal = int(control[6:-1])
            emit("control-aborted", signal=control_signal)
            result_state = "interrupted"
            result_code = 128 + control_signal
        elif control == b"launch\n":
            control_state = "launch"
            emit("control-accepted")
            test_marker = os.environ.pop("VERJSON_CI_TEST_AFTER_ACK_MARKER", None)
            if test_marker is not None:
                descriptor = os.open(test_marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
                test_deadline = time.monotonic() + 1.5
                while time.monotonic() < test_deadline and latch_supervisor_signal() is None:
                    time.sleep(0.01)
                try:
                    os.unlink(test_marker)
                except FileNotFoundError:
                    pass
            launch_signal = latch_supervisor_signal()
            if launch_signal is not None:
                supervisor_signal = launch_signal
                result_state = "supervisor-interrupted"
                result_code = 128 + launch_signal
                emit("candidate-not-started", reason="host-cancelled")
                candidate_not_started_emitted = True
            else:
                try:
                    candidate = subprocess.Popen(
                        sys.argv[1:],
                        env=candidate_environment,
                        stdin=subprocess.DEVNULL,
                        stdout=sys.stderr,
                        stderr=sys.stderr,
                        close_fds=True,
                        restore_signals=True,
                        start_new_session=True,
                    )
                    candidate_started = True
                    emit("candidate-started")
                except OSError:
                    launch_signal = latch_supervisor_signal()
                    if launch_signal is not None:
                        supervisor_signal = launch_signal
                    result_state = "exec-failure"
                    result_code = 125
                    emit("candidate-not-started", reason="exec-failure")
                    candidate_not_started_emitted = True
            if candidate_started:
                candidate_deadline = None
                candidate_killed = False
                signum = latch_supervisor_signal()
                if signum is not None:
                    try:
                        os.kill(candidate.pid, signum)
                    except ProcessLookupError:
                        pass
                    candidate_deadline = time.monotonic() + 0.5
                while candidate.poll() is None:
                    signum = latch_supervisor_signal()
                    if signum is not None and candidate_deadline is None:
                        try:
                            os.kill(candidate.pid, signum)
                        except ProcessLookupError:
                            pass
                        candidate_deadline = time.monotonic() + 0.5
                    if (
                        candidate_deadline is not None
                        and time.monotonic() >= candidate_deadline
                        and candidate.poll() is None
                        and not candidate_killed
                    ):
                        try:
                            os.kill(candidate.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        candidate_killed = True
                    time.sleep(0.01)
                candidate_return_code = candidate.wait()
                reaped_count += 1
                candidate_reaped = True
                if candidate_return_code < 0:
                    candidate_signal = -candidate_return_code
                else:
                    candidate_exit_code = candidate_return_code
                emit(
                    "candidate-outcome",
                    exitCode=candidate_exit_code,
                    candidateSignal=candidate_signal,
                )
                candidate_outcome_emitted = True
                # Signals latched while the candidate ran interrupt the plan; later ones only
                # add supervisor provenance and cannot replace the emitted candidate outcome.
                if supervisor_signal is not None:
                    result_state = "supervisor-interrupted"
                    result_code = 128 + supervisor_signal
                elif candidate_signal is not None:
                    result_state = "candidate-signal"
                    result_code = 128 + candidate_signal
                elif candidate_exit_code >= 128:
                    result_state = "candidate-ambiguous-exit"
                    result_code = candidate_exit_code
                else:
                    result_state = "candidate-exit"
                    result_code = candidate_exit_code
            elif not candidate_not_started_emitted:
                raise RuntimeError("host control frame is invalid")
        cleanup_started = True
        emit("cleanup-start")
        try:
            terminate_namespace_children()
            emit("cleanup-complete")
        except BaseException:
            cleanup_state = "failed"
            result_state = "cleanup-failure"
            result_code = 125
    except BaseException as error:
        sys.stderr.write(f"trusted supervisor error: {type(error).__name__}: {error}\\n")
        if not ready_emitted:
            result_state = "bootstrap-failure"
            result_code = 125
        elif control_state == "launch" and candidate_started:
            if candidate is not None and not candidate_reaped:
                if candidate.poll() is None:
                    candidate.kill()
                candidate_return_code = candidate.wait()
                reaped_count += 1
                candidate_reaped = True
                if candidate_return_code < 0:
                    candidate_signal = -candidate_return_code
                else:
                    candidate_exit_code = candidate_return_code
            if not candidate_outcome_emitted:
                emit(
                    "candidate-outcome",
                    exitCode=candidate_exit_code,
                    candidateSignal=candidate_signal,
                )
                candidate_outcome_emitted = True
            result_state = "supervisor-failure"
            result_code = 125
        elif control_state == "launch" and not candidate_started and result_state == "bootstrap-failure":
            sys.stderr.write(f"trusted supervisor could not start candidate: {type(error).__name__}: {error}\\n")
            if not candidate_not_started_emitted:
                emit("candidate-not-started", reason="exec-failure")
                candidate_not_started_emitted = True
            result_state = "exec-failure"
            result_code = 125
        elif result_state not in {"interrupted", "candidate-exit", "candidate-signal", "candidate-ambiguous-exit", "exec-failure"}:
            result_state = "cleanup-failure" if cleanup_started else "bootstrap-failure"
            result_code = 125
    late_signal = latch_supervisor_signal()
    if late_signal is not None:
        supervisor_signal = late_signal
    publish_terminal()
    return result_code

raise SystemExit(main())
'''

def parse_bubblewrap_json_status(
    data: bytes,
    process_returncode: int,
    *,
    allow_terminal_status: bool = False,
) -> int:
    if not isinstance(data, bytes) or len(data) > MAX_BWRAP_STATUS_BYTES:
        raise IntegrityError("Bubblewrap status receipt is missing or oversized")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise IntegrityError("Bubblewrap status receipt contains a duplicate JSON key")
            result[key] = value
        return result

    def reject_nonfinite_number(_value: str) -> None:
        raise IntegrityError("Bubblewrap status receipt contains a non-JSON number")

    try:
        lines = data.splitlines()
        if not lines or len(lines) > MAX_BWRAP_STATUS_RECORDS:
            raise IntegrityError("Bubblewrap status receipt has an invalid record count")
        records = []
        for line in lines:
            if not line or len(line) > MAX_BWRAP_STATUS_LINE_BYTES:
                raise IntegrityError("Bubblewrap status receipt contains an empty or oversized line")
            records.append(
                json.loads(
                    line.decode("utf-8", errors="strict"),
                    object_pairs_hook=reject_duplicate_keys,
                    parse_constant=reject_nonfinite_number,
                )
            )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IntegrityError("Bubblewrap status receipt is not valid JSON Lines") from error
    if type(process_returncode) is not int or not 0 <= process_returncode <= 255:
        raise IntegrityError("Bubblewrap process returned an invalid status")

    child_pid: int | None = None
    exit_code: int | None = None
    for record in records:
        if not isinstance(record, dict):
            raise IntegrityError("Bubblewrap status receipt contains a non-object event")
        known = [name for name in ("child-pid", "exit-code") if name in record]
        if len(known) > 1:
            raise IntegrityError("Bubblewrap status event combines ordered lifecycle events")
        if not known:
            continue
        if known[0] == "child-pid":
            value = record["child-pid"]
            if child_pid is not None or exit_code is not None or type(value) is not int or value <= 0:
                raise IntegrityError("Bubblewrap child-pid event is duplicate, reversed, or invalid")
            child_pid = value
            continue
        value = record["exit-code"]
        if child_pid is None or exit_code is not None or type(value) is not int or not 0 <= value <= 255:
            raise IntegrityError("Bubblewrap exit-code event is duplicate, reversed, or invalid")
        exit_code = value

    if child_pid is None or exit_code is None or exit_code != process_returncode:
        raise IntegrityError(
            "Bubblewrap status receipt is incomplete or contradicts its process status "
            f"(child-pid={child_pid!r}, exit-code={exit_code!r}, "
            f"process return code={process_returncode!r})"
        )
    if 128 <= exit_code <= 255 and not allow_terminal_status:
        raise IntegrityError("Bubblewrap status cannot distinguish a candidate signal from exit code")
    return exit_code


def read_bubblewrap_json_status(status_fd: int, process_returncode: int) -> int:
    try:
        data = bytearray()
        while True:
            chunk = os.read(status_fd, min(4096, MAX_BWRAP_STATUS_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_BWRAP_STATUS_BYTES:
                raise IntegrityError("Bubblewrap status receipt is oversized")
        return parse_bubblewrap_json_status(bytes(data), process_returncode)
    finally:
        os.close(status_fd)


def _strict_json_object(encoded: bytes, label: str) -> dict[str, Any]:
    def reject_nonfinite_number(_value: str) -> None:
        raise IntegrityError(f"{label} contains a non-JSON number")

    try:
        value = json.loads(
            encoded.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_receipt_keys,
            parse_constant=reject_nonfinite_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IntegrityError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise IntegrityError(f"{label} is not a JSON object")
    return value


def _bubblewrap_child_identity(data: bytes) -> tuple[int | None, int | None, bool]:
    child_pid: int | None = None
    pid_namespace: int | None = None
    exited = False
    complete_lines = data.splitlines(keepends=True)
    for raw_line in complete_lines:
        if not raw_line.endswith(b"\n"):
            break
        line = raw_line[:-1]
        if not line or len(line) > MAX_BWRAP_STATUS_LINE_BYTES:
            raise IntegrityError("Bubblewrap status stream has an invalid event line")
        if len(complete_lines) > MAX_BWRAP_STATUS_RECORDS:
            raise IntegrityError("Bubblewrap status stream has too many events")
        record = _strict_json_object(line, "Bubblewrap status event")
        known = [name for name in ("child-pid", "exit-code") if name in record]
        if len(known) > 1:
            raise IntegrityError("Bubblewrap status event combines lifecycle events")
        if not known:
            continue
        if known[0] == "child-pid":
            if child_pid is not None or exited:
                raise IntegrityError("Bubblewrap child-pid event is duplicate or reversed")
            value = record["child-pid"]
            namespace_value = record.get("pid-namespace")
            if type(value) is not int or value <= 0:
                raise IntegrityError("Bubblewrap child-pid event has an invalid PID")
            if namespace_value is not None and (type(namespace_value) is not int or namespace_value <= 0):
                raise IntegrityError("Bubblewrap child-pid event has an invalid PID namespace identity")
            child_pid = value
            pid_namespace = namespace_value
        else:
            if child_pid is None or exited:
                raise IntegrityError("Bubblewrap exit-code event is reversed or duplicate")
            code = record["exit-code"]
            if type(code) is not int or not 0 <= code <= 255:
                raise IntegrityError("Bubblewrap exit-code event is invalid")
            exited = True
    return child_pid if not exited else None, pid_namespace, exited


def _trusted_wrapper_event(line: bytes) -> dict[str, Any]:
    record = _strict_json_object(line, "trusted sandbox event")
    common = {"version", "pid", "event"}
    if (
        type(record.get("version")) is not int
        or record["version"] != 2
        or type(record.get("pid")) is not int
        or record["pid"] != 1
        or not isinstance(record.get("event"), str)
    ):
        raise IntegrityError("trusted sandbox event has an invalid envelope")
    event = record["event"]
    expected_fields = {
        "ready": common | {"pidNamespace"},
        "control-accepted": common,
        "control-aborted": common | {"signal"},
    "candidate-not-started": common | {"reason"},
    "candidate-started": common,
    "candidate-outcome": common | {"exitCode", "candidateSignal"},
        "cleanup-start": common,
        "cleanup-complete": common,
        "terminal": common
        | {
            "state",
            "candidateStarted",
            "exitCode",
            "candidateSignal",
            "supervisorSignal",
            "cleanup",
            "reaped",
            "control",
            "controlSignal",
        },
    }.get(event)
    if expected_fields is None or set(record) != expected_fields:
        raise IntegrityError("trusted sandbox event has an invalid schema")
    if event == "ready":
        if type(record["pidNamespace"]) is not int or record["pidNamespace"] <= 0:
            raise IntegrityError("trusted sandbox ready event has an invalid PID namespace")
    elif event == "control-aborted":
        if type(record["signal"]) is not int or record["signal"] not in {2, 15}:
            raise IntegrityError("trusted sandbox abort ACK has an invalid signal")
    elif event == "candidate-not-started":
        if record["reason"] not in {"host-cancelled", "exec-failure"}:
            raise IntegrityError("trusted sandbox candidate-not-started reason is invalid")
    elif event == "candidate-outcome":
        exit_code = record["exitCode"]
        signum = record["candidateSignal"]
        if (
            (exit_code is None) == (signum is None)
            or (exit_code is not None and (type(exit_code) is not int or not 0 <= exit_code <= 255))
            or (signum is not None and (type(signum) is not int or not 1 <= signum <= 127))
        ):
            raise IntegrityError("trusted sandbox candidate outcome is invalid")
    elif event == "terminal":
        if (
            not isinstance(record["state"], str)
            or type(record["candidateStarted"]) is not bool
            or type(record["reaped"]) is not int
            or record["reaped"] < 0
            or record["cleanup"] not in {"not-started", "complete", "failed"}
            or record["control"] not in {None, "launch", "abort"}
            or record["controlSignal"] not in {None, 2, 15}
            or record["supervisorSignal"] not in {None, 2, 15}
            or (
                record["exitCode"] is not None
                and (type(record["exitCode"]) is not int or not 0 <= record["exitCode"] <= 255)
            )
            or (
                record["candidateSignal"] is not None
                and (type(record["candidateSignal"]) is not int or not 1 <= record["candidateSignal"] <= 127)
            )
            or (
                record["exitCode"] is not None
                and record["candidateSignal"] is not None
            )
        ):
            raise IntegrityError("trusted sandbox terminal outcome is invalid")
    return record


def _parse_trusted_candidate_status(
    data: bytes,
    process_returncode: int,
    *,
    host_cancel_signal: int | None = None,
    forwarded_signal: int | None = None,
    expected_pid_namespace: int | None = None,
) -> TrustedCandidateStatus:
    if not isinstance(data, bytes) or len(data) > MAX_TRUSTED_WRAPPER_STATUS_BYTES:
        raise IntegrityError("trusted sandbox status is missing or oversized")
    if not data.endswith(b"\n"):
        raise IntegrityError("trusted sandbox status is missing its final newline")
    lines = data.splitlines()
    if not lines or len(lines) > 8 or any(not line for line in lines):
        raise IntegrityError("trusted sandbox status has an invalid record count")
    records = [_trusted_wrapper_event(line) for line in lines]
    final = records[-1]
    if final["event"] != "terminal" or process_returncode < 0 or process_returncode > 255:
        raise IntegrityError("trusted sandbox terminal status is missing or invalid")
    events = [record["event"] for record in records]
    state = final["state"]
    candidate_started = final["candidateStarted"]
    candidate_start_count = events.count("candidate-started")
    candidate_outcome_count = events.count("candidate-outcome")
    if (
        candidate_start_count > 1
        or candidate_outcome_count > 1
        or candidate_started != (candidate_start_count == 1)
        or (candidate_outcome_count == 1 and candidate_start_count != 1)
        or (
            candidate_outcome_count == 0
            and (final["exitCode"] is not None or final["candidateSignal"] is not None)
        )
    ):
        raise IntegrityError("trusted candidate start or outcome evidence is contradictory")
    if candidate_outcome_count == 1:
        outcome = records[events.index("candidate-outcome")]
        if (
            outcome["exitCode"] != final["exitCode"]
            or outcome["candidateSignal"] != final["candidateSignal"]
        ):
            raise IntegrityError("trusted candidate outcome differs from its terminal status")
    exit_code = final["exitCode"]
    candidate_signal = final["candidateSignal"]
    supervisor_signal = final["supervisorSignal"]
    cleanup = final["cleanup"]
    reaped = final["reaped"]
    control = final["control"]
    control_signal = final["controlSignal"]

    if state == "bootstrap-failure":
        if events not in (["terminal"], ["ready", "cleanup-start", "cleanup-complete", "terminal"]):
            raise IntegrityError("trusted bootstrap failure event order is invalid")
        if candidate_started or exit_code is not None or candidate_signal is not None or process_returncode != 125:
            raise IntegrityError("trusted bootstrap failure status is contradictory")
        if cleanup not in {"not-started", "complete"}:
            raise IntegrityError("trusted bootstrap failure cleanup is contradictory")
    elif state in {"cleanup-failure", "supervisor-failure"}:
        if events not in (
            ["ready", "control-accepted", "candidate-not-started", "cleanup-start", "terminal"],
            ["ready", "control-accepted", "candidate-not-started", "cleanup-start", "cleanup-complete", "terminal"],
            ["ready", "control-accepted", "candidate-started", "cleanup-start", "terminal"],
            ["ready", "control-accepted", "candidate-started", "cleanup-start", "cleanup-complete", "terminal"],
            ["ready", "control-accepted", "candidate-started", "candidate-outcome", "cleanup-start", "terminal"],
            ["ready", "control-accepted", "candidate-started", "candidate-outcome", "cleanup-start", "cleanup-complete", "terminal"],
            ["ready", "control-aborted", "cleanup-start", "terminal"],
        ):
            raise IntegrityError("trusted cleanup failure event order is invalid")
        if process_returncode != 125 or (
            state == "cleanup-failure" and cleanup != "failed"
        ) or (
            state == "supervisor-failure" and cleanup not in {"complete", "failed"}
        ):
            raise IntegrityError("trusted cleanup failure status is contradictory")
        if state == "supervisor-failure" and not candidate_started:
            raise IntegrityError("trusted supervisor failure lacks authenticated candidate start")
    elif state == "interrupted":
        if events != ["ready", "control-aborted", "cleanup-start", "cleanup-complete", "terminal"]:
            raise IntegrityError("trusted prelaunch interruption event order is invalid")
        if (
            candidate_started
            or exit_code is not None
            or candidate_signal is not None
            or control != "abort"
            or control_signal not in {2, 15}
            or cleanup != "complete"
            or process_returncode != 128 + control_signal
            or host_cancel_signal != control_signal
        ):
            raise IntegrityError("trusted abort status lacks matching host cancellation")
        return TrustedCandidateStatus(
            state, None, None, cleanup, reaped, False, control_signal, None
        )
    elif state == "supervisor-interrupted":
        if events not in (
            ["ready", "control-accepted", "candidate-not-started", "cleanup-start", "cleanup-complete", "terminal"],
            ["ready", "control-accepted", "candidate-started", "candidate-outcome", "cleanup-start", "cleanup-complete", "terminal"],
        ):
            raise IntegrityError("trusted supervisor interruption event order is invalid")
        if (
            supervisor_signal not in {2, 15}
            or host_cancel_signal != supervisor_signal
            or forwarded_signal != supervisor_signal
            or cleanup != "complete"
            or process_returncode != 128 + supervisor_signal
        ):
            raise IntegrityError("trusted supervisor interruption lacks host-latch provenance")
        if events[2] == "candidate-not-started" and candidate_started:
            raise IntegrityError("trusted interrupted candidate was incorrectly marked started")
        if events[2] == "candidate-started" and not candidate_started:
            raise IntegrityError("trusted interrupted candidate start lacks terminal start evidence")
        if events[2] == "candidate-not-started" and records[2]["reason"] != "host-cancelled":
            raise IntegrityError("trusted interrupted candidate was not host-cancelled")
        if "candidate-outcome" in events:
            candidate_event = records[3]
            if (
                candidate_event["exitCode"] != exit_code
                or candidate_event["candidateSignal"] != candidate_signal
            ):
                raise IntegrityError("trusted candidate outcome differs from its terminal status")
        return TrustedCandidateStatus(
            state,
            exit_code,
            candidate_signal,
            cleanup,
            reaped,
            candidate_started,
            host_cancel_signal,
            supervisor_signal,
        )
    elif state in {"candidate-exit", "candidate-signal", "candidate-ambiguous-exit"}:
        if events != ["ready", "control-accepted", "candidate-started", "candidate-outcome", "cleanup-start", "cleanup-complete", "terminal"]:
            raise IntegrityError("trusted candidate outcome event order is invalid")
        candidate_event = records[3]
        if (
            not candidate_started
            or candidate_event["exitCode"] != exit_code
            or candidate_event["candidateSignal"] != candidate_signal
            or cleanup != "complete"
            or (host_cancel_signal is None and forwarded_signal is not None)
            or (
                host_cancel_signal is not None
                and host_cancel_signal not in {signal.SIGINT, signal.SIGTERM}
            )
            or (
                forwarded_signal is not None
                and forwarded_signal != host_cancel_signal
            )
            or (
                supervisor_signal is not None
                and (
                    supervisor_signal != host_cancel_signal
                    or supervisor_signal != forwarded_signal
                )
            )
        ):
            raise IntegrityError("trusted candidate outcome status is contradictory")
        if state == "candidate-exit":
            if exit_code is None or exit_code >= 128 or candidate_signal is not None or process_returncode != exit_code:
                raise IntegrityError("trusted candidate exit status is contradictory")
        elif state == "candidate-signal":
            if candidate_signal is None or exit_code is not None or process_returncode != 128 + candidate_signal:
                raise IntegrityError("trusted candidate signal status is contradictory")
        elif exit_code is None or exit_code < 128 or candidate_signal is not None or process_returncode != exit_code:
            raise IntegrityError("trusted ambiguous candidate exit status is contradictory")
    elif state == "exec-failure":
        if events != [
            "ready",
            "control-accepted",
            "candidate-not-started",
            "cleanup-start",
            "cleanup-complete",
            "terminal",
        ]:
            raise IntegrityError("trusted exec failure event order is invalid")
        if (
            records[2]["reason"] != "exec-failure"
            or candidate_started
            or exit_code is not None
            or candidate_signal is not None
            or (
                supervisor_signal is not None
                and (
                    supervisor_signal not in {signal.SIGINT, signal.SIGTERM}
                    or host_cancel_signal != supervisor_signal
                )
            )
            or cleanup != "complete"
            or process_returncode != 125
        ):
            raise IntegrityError("trusted exec failure status is contradictory")
    else:
        raise IntegrityError("trusted sandbox status has an unknown terminal state")

    if expected_pid_namespace is not None and events[0] == "ready":
        if records[0]["pidNamespace"] != expected_pid_namespace:
            raise IntegrityError("trusted sandbox ready event PID namespace changed")
    final_state = (
        "interrupted"
        if host_cancel_signal is not None
        and state == "candidate-exit"
        else state
    )
    return TrustedCandidateStatus(
        final_state,
        exit_code,
        candidate_signal,
        cleanup,
        reaped,
        candidate_started,
        host_cancel_signal,
        supervisor_signal,
    )

def _trusted_event_prefix_is_valid(events: list[str]) -> bool:
    complete_paths = (
        ("terminal",),
        ("ready", "cleanup-start", "terminal"),
        ("ready", "cleanup-start", "cleanup-complete", "terminal"),
        ("ready", "control-aborted", "cleanup-start", "terminal"),
        ("ready", "control-aborted", "cleanup-start", "cleanup-complete", "terminal"),
        ("ready", "control-accepted", "candidate-not-started", "cleanup-start", "terminal"),
        ("ready", "control-accepted", "candidate-not-started", "cleanup-start", "cleanup-complete", "terminal"),
        ("ready", "control-accepted", "candidate-started", "cleanup-start", "terminal"),
        ("ready", "control-accepted", "candidate-started", "cleanup-start", "cleanup-complete", "terminal"),
        ("ready", "control-accepted", "candidate-started", "candidate-outcome", "cleanup-start", "terminal"),
        ("ready", "control-accepted", "candidate-started", "candidate-outcome", "cleanup-start", "cleanup-complete", "terminal"),
    )
    return any(tuple(events) == path[: len(events)] for path in complete_paths)


def run_supervised_bubblewrap(
    arguments: list[str],
    *,
    environment: dict[str, str],
    pass_fds: tuple[int, ...],
    host_signals: HostSignalQueue | _CallableHostSignalQueue | None = None,
    cancellation_signal: Callable[[], int | None] | None = None,
    cwd: str | None = None,
    test_hook: Callable[[str], None] | None = None,
    test_after_ack_marker: str | None = None,
) -> TrustedCandidateStatus:
    if not arguments:
        raise IntegrityError("trusted sandbox launcher arguments are invalid")
    signals = _coerce_host_signal_queue(host_signals, cancellation_signal)
    if test_after_ack_marker is not None and (
        not isinstance(test_after_ack_marker, str)
        or not os.path.isabs(test_after_ack_marker)
        or "\x00" in test_after_ack_marker
    ):
        raise IntegrityError("trusted sandbox test marker path is invalid")

    status_read_fd, status_write_fd = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    pidfd: int | None = None
    status_data = bytearray()
    wrapper_data = bytearray()
    wrapper_offset = 0
    wrapper_records: list[dict[str, Any]] = []
    wrapper_eof = False
    status_eof = False
    control_sent = False
    control_decision: str | None = None
    control_signal: int | None = None
    forwarded_signal: int | None = None
    supervisor_pid_exited = False
    supervisor_exit_deadline: float | None = None
    escalation_deadline: float | None = None
    final_kill_deadline: float | None = None
    child_pid: int | None = None
    pid_namespace: int | None = None
    ready_seen = False
    ack_seen = False
    start_time = time.monotonic()
    control_deadline: float | None = None

    def checkpoint(event: str) -> None:
        if test_hook is not None:
            test_hook(event)

    def drain_fd(descriptor: int, target: bytearray, limit: int) -> bool:
        eof = False
        while True:
            try:
                chunk = os.read(descriptor, 4096)
            except BlockingIOError:
                break
            if not chunk:
                eof = True
                break
            target.extend(chunk)
            if len(target) > limit:
                raise IntegrityError("trusted Bubblewrap result stream is oversized")
        return eof

    def send_control(decision_signal: int | None) -> None:
        nonlocal control_sent, control_decision, control_signal, control_deadline
        if process is None or process.stdin is None:
            raise IntegrityError("trusted supervisor control pipe is unavailable")
        control_signal = decision_signal
        if decision_signal is None:
            frame = b"launch\n"
            control_decision = "launch"
        else:
            if decision_signal not in {signal.SIGINT, signal.SIGTERM}:
                raise IntegrityError("trusted supervisor control signal is invalid")
            frame = f"abort:{int(decision_signal)}\n".encode("ascii")
            control_decision = "abort"
        try:
            process.stdin.write(frame)
            process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise IntegrityError("trusted supervisor control pipe closed before ACK") from error
        finally:
            process.stdin.close()
        control_sent = True
        control_deadline = time.monotonic() + 5.0

    def pin_supervisor() -> None:
        nonlocal pidfd
        if child_pid is None or pid_namespace is None:
            raise IntegrityError("Bubblewrap omitted a pinnable PID namespace identity")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise IntegrityError("host cannot safely pin and signal the trusted supervisor")
        try:
            pidfd = os.pidfd_open(child_pid)
            if os.stat(f"/proc/{child_pid}/ns/pid").st_ino != pid_namespace:
                raise IntegrityError("Bubblewrap supervisor PID namespace identity changed")
        except OSError as error:
            raise IntegrityError("cannot safely pin the Bubblewrap-reported supervisor PID") from error

    def send_pidfd_signal(signum: int) -> bool:
        if pidfd is None:
            raise IntegrityError("trusted supervisor PID was not pinned")
        try:
            signal.pidfd_send_signal(pidfd, signum)
            return True
        except ProcessLookupError:
            return False
        except OSError as error:
            if error.errno == errno.ESRCH:
                return False
            raise IntegrityError("cannot signal the pinned trusted supervisor") from error

    def signal_bubblewrap_group(signum: int) -> bool:
        if process is None:
            return False
        try:
            os.killpg(process.pid, signum)
            return True
        except ProcessLookupError:
            return False
        except OSError as error:
            if error.errno == errno.ESRCH:
                return False
            raise IntegrityError("cannot signal the isolated Bubblewrap process group") from error

    def bubblewrap_group_exists() -> bool:
        if process is None:
            return False
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except OSError as error:
            if error.errno == errno.ESRCH:
                return False
            raise IntegrityError("cannot verify the isolated Bubblewrap process group") from error

    def process_wrapper_lines() -> None:
        nonlocal wrapper_offset, ready_seen, ack_seen
        while b"\n" in wrapper_data[wrapper_offset:]:
            line_end = wrapper_data.index(b"\n", wrapper_offset)
            line = bytes(wrapper_data[wrapper_offset:line_end])
            wrapper_offset = line_end + 1
            if not line or len(line) > MAX_BWRAP_STATUS_LINE_BYTES:
                raise IntegrityError("trusted wrapper emitted an empty or oversized status line")
            record = _trusted_wrapper_event(line)
            events = [item["event"] for item in wrapper_records] + [record["event"]]
            if not _trusted_event_prefix_is_valid(events):
                raise IntegrityError(f"trusted wrapper event stream is out of order: {events!r}")
            mark_ready = False
            mark_ack = False
            if record["event"] == "ready":
                if ready_seen or wrapper_records:
                    raise IntegrityError("trusted wrapper ready event is duplicated or misplaced")
                mark_ready = True
            elif record["event"] in {"control-accepted", "control-aborted"}:
                if not ready_seen or not control_sent or ack_seen:
                    raise IntegrityError("trusted wrapper control ACK is duplicated or misplaced")
                expected = "control-aborted" if control_decision == "abort" else "control-accepted"
                if record["event"] != expected:
                    raise IntegrityError("trusted wrapper control ACK contradicts the host command")
                if record["event"] == "control-aborted" and record["signal"] != control_signal:
                    raise IntegrityError("trusted wrapper abort ACK changed the host signal")
                mark_ack = True
            elif record["event"] == "candidate-not-started":
                if not ack_seen or control_decision != "launch":
                    raise IntegrityError("trusted wrapper candidate result precedes launch ACK")
                if any(item["event"] == "candidate-started" for item in wrapper_records):
                    raise IntegrityError("trusted wrapper candidate was both started and not started")
            elif record["event"] == "candidate-started":
                if not ack_seen or control_decision != "launch":
                    raise IntegrityError("trusted wrapper candidate start precedes launch ACK")
                if any(
                    item["event"] in {"candidate-started", "candidate-outcome", "candidate-not-started"}
                    for item in wrapper_records
                ):
                    raise IntegrityError("trusted wrapper candidate start is duplicated or misplaced")
            elif record["event"] == "candidate-outcome":
                if not ack_seen or control_decision != "launch":
                    raise IntegrityError("trusted wrapper candidate outcome precedes launch ACK")
                if not any(item["event"] == "candidate-started" for item in wrapper_records):
                    raise IntegrityError("trusted wrapper candidate outcome lacks start evidence")
            wrapper_records.append(record)
            if mark_ready:
                ready_seen = True
            if mark_ack:
                ack_seen = True
            checkpoint(record["event"])

    def authenticated_candidate_started() -> bool:
        return (
            ready_seen
            and ack_seen
            and control_decision == "launch"
            and any(record["event"] == "candidate-started" for record in wrapper_records)
        )

    def authenticated_candidate_outcome() -> dict[str, Any] | None:
        if not ready_seen or not ack_seen or control_decision != "launch":
            return None
        return next(
            (record for record in wrapper_records if record["event"] == "candidate-outcome"),
            None,
        )

    def terminate_after_error() -> None:
        if process is None:
            return
        if process.poll() is None:
            try:
                signaled = pidfd is not None and send_pidfd_signal(signal.SIGTERM)
            except IntegrityError:
                signaled = False
            if not signaled:
                signal_bubblewrap_group(signal.SIGTERM)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            except ChildProcessError as error:
                if process.poll() is None:
                    raise IntegrityError("Bubblewrap process could not be reaped") from error

        if process.poll() is None:
            try:
                signaled = pidfd is not None and send_pidfd_signal(signal.SIGKILL)
            except IntegrityError:
                signaled = False
            if not signaled:
                signal_bubblewrap_group(signal.SIGKILL)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                signal_bubblewrap_group(signal.SIGKILL)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired as error:
                    raise IntegrityError("Bubblewrap process did not exit after SIGKILL") from error
            except ChildProcessError as error:
                if process.poll() is None:
                    raise IntegrityError("Bubblewrap process could not be reaped") from error

        if bubblewrap_group_exists():
            try:
                extinguish_supervised_process_group(process)
            except (IntegrityError, OSError) as error:
                raise IntegrityError("cannot verify isolated Bubblewrap process-group cleanup") from error
        if process.poll() is None:
            raise IntegrityError("Bubblewrap process remains alive after cleanup")


    try:
        os.set_blocking(status_read_fd, False)
        sandbox_environment = dict(environment)
        if test_after_ack_marker is not None:
            sandbox_environment["VERJSON_CI_TEST_AFTER_ACK_MARKER"] = test_after_ack_marker
        else:
            sandbox_environment.pop("VERJSON_CI_TEST_AFTER_ACK_MARKER", None)
        process = subprocess.Popen(
            [arguments[0], "--json-status-fd", str(status_write_fd), *arguments[1:]],
            env=sandbox_environment,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            start_new_session=True,
            pass_fds=tuple([*pass_fds, status_write_fd]),
        )
        os.close(status_write_fd)
        status_write_fd = -1
        if process.stdout is None or process.stdin is None:
            raise IntegrityError("trusted Bubblewrap pipes were not created")
        os.set_blocking(process.stdout.fileno(), False)

        while True:
            signals.poll()
            readable_fds = []
            if not wrapper_eof:
                readable_fds.append(process.stdout.fileno())
            if not status_eof:
                readable_fds.append(status_read_fd)
            if readable_fds:
                try:
                    readable, _writable, _exceptional = select.select(readable_fds, [], [], 0.05)
                except InterruptedError:
                    readable = []
            else:
                readable = []
            if process.stdout.fileno() in readable:
                wrapper_eof = drain_fd(
                    process.stdout.fileno(), wrapper_data, MAX_TRUSTED_WRAPPER_STATUS_BYTES
                )
                process_wrapper_lines()
            if status_read_fd in readable:
                status_eof = drain_fd(status_read_fd, status_data, MAX_BWRAP_STATUS_BYTES)

            observed_pid, observed_namespace, bwrap_exited = _bubblewrap_child_identity(bytes(status_data))
            if observed_pid is not None:
                child_pid = observed_pid
                pid_namespace = observed_namespace
            if ready_seen and not control_sent and child_pid is not None:
                ready = wrapper_records[0]
                if pid_namespace is None or ready["pidNamespace"] != pid_namespace:
                    raise IntegrityError("trusted wrapper ready identity mismatches Bubblewrap status")
                pin_supervisor()
                launch_signal = signals.launch_decision()
                send_control(launch_signal)
            if (
                control_sent
                and control_decision == "launch"
                and forwarded_signal is None
                and not supervisor_pid_exited
            ):
                signum = signals.poll()
                if signum is not None:
                    if process.poll() is None:
                        if send_pidfd_signal(signum):
                            forwarded_signal = signum
                            escalation_deadline = time.monotonic() + 8.0
                            final_kill_deadline = escalation_deadline + 4.0
                        else:
                            supervisor_pid_exited = True
                            supervisor_exit_deadline = time.monotonic() + 5.0
            if (
                forwarded_signal is not None
                and escalation_deadline is not None
                and time.monotonic() >= escalation_deadline
                and process.poll() is None
            ):
                if send_pidfd_signal(signal.SIGKILL):
                    escalation_deadline = None
                else:
                    supervisor_pid_exited = True
                    escalation_deadline = None
                    final_kill_deadline = None
                    supervisor_exit_deadline = time.monotonic() + 5.0
            if (
                final_kill_deadline is not None
                and time.monotonic() >= final_kill_deadline
                and process.poll() is None
            ):
                raise IntegrityError("trusted supervisor exceeded its independent exit deadline")
            if (
                supervisor_pid_exited
                and supervisor_exit_deadline is not None
                and process.poll() is None
                and time.monotonic() >= supervisor_exit_deadline
            ):
                raise IntegrityError("Bubblewrap did not drain after its PID 1 supervisor exited")
            if not ready_seen and time.monotonic() - start_time > 10.0 and process.poll() is None:
                raise IntegrityError("trusted sandbox supervisor did not become ready")
            if control_sent and not ack_seen and control_deadline is not None and time.monotonic() > control_deadline:
                raise IntegrityError("trusted sandbox supervisor did not ACK its control frame")
            if process.poll() is not None and wrapper_eof and status_eof:
                break
            if bwrap_exited and child_pid is None and process.poll() is not None and status_eof:
                break

        if process is None:
            raise IntegrityError("trusted Bubblewrap process was not created")
        process_returncode = process.wait()
        if wrapper_data[wrapper_offset:]:
            raise IntegrityError("trusted wrapper status ended with a truncated record")
        parse_bubblewrap_json_status(
            bytes(status_data), process_returncode, allow_terminal_status=True
        )
        if child_pid is None or pid_namespace is None:
            raise IntegrityError(
                f"Bubblewrap status lacks a child PID namespace identity: "
                f"{child_pid!r}, {pid_namespace!r}, {bytes(status_data)!r}"
            )
        if ready_seen and wrapper_records[0]["pidNamespace"] != pid_namespace:
            raise IntegrityError("trusted wrapper PID namespace identity changed")
        host_signal = signals.poll()
        status = _parse_trusted_candidate_status(
            bytes(wrapper_data),
            process_returncode,
            host_cancel_signal=host_signal,
            forwarded_signal=forwarded_signal,
            expected_pid_namespace=pid_namespace,
        )
        if status.state in {"bootstrap-failure", "exec-failure", "cleanup-failure", "supervisor-failure"}:
            raise _script_candidate_status_failure(status)
        if status.state == "interrupted":
            interruption = ScriptPlanInterrupted(
                status.host_cancellation_signal or signal.SIGTERM,
                candidate_started=status.candidate_started,
                candidate_exit_code=status.return_code,
                candidate_signal=status.signum,
                supervisor_signal=status.supervisor_signal,
            )
            interruption.cleanup_status = (
                "verified" if status.cleanup == "complete" else status.cleanup
            )
            raise interruption
        if status.state == "supervisor-interrupted":
            interruption = ScriptPlanInterrupted(
                status.host_cancellation_signal or signal.SIGTERM,
                candidate_started=status.candidate_started,
                candidate_exit_code=status.return_code,
                candidate_signal=status.signum,
                supervisor_signal=status.supervisor_signal,
            )
            interruption.cleanup_status = (
                "verified" if status.cleanup == "complete" else status.cleanup
            )
            raise interruption
        if status.state in {"candidate-signal", "candidate-ambiguous-exit"}:
            raise ScriptCandidateTerminal(
                f"candidate ended in terminal state {status.state}",
                return_code=status.return_code,
                signum=status.signum,
                cleanup_status="verified" if status.cleanup == "complete" else status.cleanup,
                candidate_started=True,
            )
        return status
    except ScriptPlanInterrupted:
        raise
    except BaseException as error:
        if isinstance(error, ScriptCandidateTerminal):
            raise
        candidate_outcome = authenticated_candidate_outcome()
        candidate_started = authenticated_candidate_started()
        raise ScriptCandidateTerminal(
            f"trusted Bubblewrap boundary failed: {type(error).__name__}: {error}",
            return_code=(
                candidate_outcome["exitCode"]
                if candidate_started and candidate_outcome is not None
                else None
            ),
            signum=(
                candidate_outcome["candidateSignal"]
                if candidate_started and candidate_outcome is not None
                else None
            ),
            cleanup_status="unverified",
            candidate_started=candidate_started,
            host_cancellation_signal=signals.host_cancel_signal,
        ) from error
    finally:
        if process is not None:
            try:
                terminate_after_error()
            except BaseException as error:
                candidate_outcome = authenticated_candidate_outcome()
                candidate_started = authenticated_candidate_started()
                if candidate_started:
                    raise ScriptCandidateTerminal(
                        f"trusted Bubblewrap cleanup could not be verified: {type(error).__name__}: {error}",
                        return_code=(
                            candidate_outcome["exitCode"]
                            if candidate_outcome is not None
                            else None
                        ),
                        signum=(
                            candidate_outcome["candidateSignal"]
                            if candidate_outcome is not None
                            else None
                        ),
                        cleanup_status="unverified",
                        candidate_started=candidate_started,
                        host_cancellation_signal=signals.host_cancel_signal,
                        infrastructure_failure=True,
                    ) from error
                raise
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
        for descriptor in (status_read_fd, status_write_fd, pidfd):
            if descriptor is not None and descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

def _validate_script_entry_identity(identity: Any) -> dict[str, str]:
    if not isinstance(identity, dict) or set(identity) != _SCRIPT_ENTRY_IDENTITY_FIELDS:
        raise IntegrityError("script-entry receipt identity is incomplete or has unknown fields")
    if any(
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        for value in identity.values()
    ):
        raise IntegrityError("script-entry receipt identity contains an invalid value")
    return dict(identity)


def _validate_script_entry_plan(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list) or not entries or len(entries) > MAX_SCRIPT_PLAN_ENTRIES:
        raise IntegrityError("script-entry receipt plan must contain 1-256 entries")
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _SCRIPT_ENTRY_FIELDS:
            raise IntegrityError("script-entry receipt plan contains an invalid entry shape")
        directory = entry["directory"]
        script = entry["script"]
        unset_env = entry["unsetEnv"]
        requires_services = entry["requiresServices"]
        if (
            not isinstance(directory, str)
            or not directory
            or (
                directory != "."
                and (
                    directory.startswith("/")
                    or "\\" in directory
                    or any(part in {"", ".", ".."} for part in directory.split("/"))
                )
            )
            or not isinstance(script, str)
            or not script
            or len(script) > 256
            or not isinstance(unset_env, list)
            or any(not isinstance(name, str) or not name for name in unset_env)
            or len(set(unset_env)) != len(unset_env)
            or type(requires_services) is not bool
        ):
            raise IntegrityError("script-entry receipt plan contains an invalid entry value")
        normalized.append(
            {
                "directory": directory,
                "script": script,
                "unsetEnv": list(unset_env),
                "requiresServices": requires_services,
            }
        )
    return normalized


def _script_entry_plan_digest(entries: list[dict[str, Any]], fail_fast: bool) -> str:
    return sha256_bytes(
        canonical_json(
            {"version": SCRIPT_ENTRY_RECEIPT_VERSION, "failFast": fail_fast, "entries": entries}
        )
    )


def _create_script_entry_receipt_store(runner_temp: str | Path) -> tuple[Path, int]:
    root = Path(runner_temp)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise IntegrityError("script-entry receipt store requires a real host RUNNER_TEMP directory")
    resolved_root = root.resolve(strict=True)
    if resolved_root != root:
        raise IntegrityError("script-entry receipt store RUNNER_TEMP path traverses a symlink")
    directory = Path(tempfile.mkdtemp(prefix=".verjson-node-ci-entry-receipts-", dir=str(root)))
    metadata = directory.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        shutil.rmtree(directory, ignore_errors=True)
        raise IntegrityError("script-entry receipt store is not a private directory")
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        shutil.rmtree(directory, ignore_errors=True)
        raise IntegrityError("script-entry receipt store cannot be opened safely") from error
    return directory, descriptor


def _write_script_receipt_atomic(
    directory_fd: int,
    final_name: str,
    receipt: dict[str, Any],
    *,
    checkpoint: Callable[[str], None] | None = None,
    checkpoint_prefix: str,
) -> None:
    temporary_name = f".{final_name}.{os.urandom(16).hex()}.tmp"
    encoded = canonical_json(receipt)
    if len(encoded) > MAX_SCRIPT_ENTRY_RECEIPT_BYTES:
        raise IntegrityError("script-entry receipt exceeds its size limit")
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        if checkpoint is not None:
            checkpoint(f"{checkpoint_prefix}-write")
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(descriptor)
    try:
        if checkpoint is not None:
            checkpoint(f"{checkpoint_prefix}-finalize")
        os.link(
            temporary_name,
            final_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
    finally:
        os.unlink(temporary_name, dir_fd=directory_fd)
    os.fsync(directory_fd)
    if checkpoint is not None:
        checkpoint(f"{checkpoint_prefix}-committed")


def _write_script_entry_receipt(
    directory_fd: int,
    receipt: dict[str, Any],
    *,
    checkpoint: Callable[[str], None] | None = None,
) -> None:
    ordinal = receipt["ordinal"]
    _write_script_receipt_atomic(
        directory_fd,
        f"entry-{ordinal:06d}.json",
        receipt,
        checkpoint=checkpoint,
        checkpoint_prefix="entry-receipt",
    )


def _replace_script_entry_receipt(
    directory_fd: int,
    receipt: dict[str, Any],
) -> None:
    ordinal = receipt["ordinal"]
    final_name = f"entry-{ordinal:06d}.json"
    existing = _read_json_receipt_at(directory_fd, final_name, "script-entry")
    if (
        existing.get("state") != "not-run-fail-fast"
        or existing.get("exitCode") is not None
        or existing.get("signal") is not None
        or existing.get("candidateStarted") is not False
        or existing.get("cleanup") != "not-started"
        or existing.get("hostCancellationSignal") is not None
        or existing.get("supervisorSignal") is not None
        or {key: value for key, value in existing.items() if key != "state"}
        != {key: value for key, value in receipt.items() if key != "state"}
    ):
        raise IntegrityError("fail-fast receipt changed before interruption cutoff")
    encoded = canonical_json(receipt)
    if len(encoded) > MAX_SCRIPT_ENTRY_RECEIPT_BYTES:
        raise IntegrityError("script-entry receipt exceeds its size limit")
    temporary_name = f".{final_name}.{os.urandom(16).hex()}.tmp"
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(descriptor)
    os.replace(
        temporary_name,
        final_name,
        src_dir_fd=directory_fd,
        dst_dir_fd=directory_fd,
    )
    os.fsync(directory_fd)


def _write_script_plan_receipt(
    directory_fd: int,
    receipt: dict[str, Any],
    *,
    checkpoint: Callable[[str], None] | None = None,
) -> str:
    encoded = canonical_json(receipt)
    if len(encoded) > MAX_SCRIPT_ENTRY_RECEIPT_BYTES:
        raise IntegrityError("script-plan receipt exceeds its size limit")
    temporary_name = f".plan.json.{os.urandom(16).hex()}.tmp"
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        if checkpoint is not None:
            checkpoint("plan-receipt-write")
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(descriptor)
    if checkpoint is not None:
        checkpoint("plan-receipt-finalize")
    os.fsync(directory_fd)
    return temporary_name


def _replace_staged_script_plan_receipt(
    directory_fd: int,
    temporary_name: str,
    receipt: dict[str, Any],
) -> None:
    encoded = canonical_json(receipt)
    if len(encoded) > MAX_SCRIPT_ENTRY_RECEIPT_BYTES:
        raise IntegrityError("script-plan receipt exceeds its size limit")
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        dir_fd=directory_fd,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
        ):
            raise IntegrityError("staged script-plan receipt metadata unsafe")
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_script_plan_receipt(directory_fd: int, temporary_name: str) -> None:
    os.link(
        temporary_name,
        "plan.json",
        src_dir_fd=directory_fd,
        dst_dir_fd=directory_fd,
    )
    os.fsync(directory_fd)
    os.unlink(temporary_name, dir_fd=directory_fd)
    os.fsync(directory_fd)

def _reject_duplicate_receipt_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntegrityError("script-entry receipt contains a duplicate JSON key")
        result[key] = value
    return result


def _read_json_receipt_at(
    directory_fd: int,
    name: str,
    label: str,
) -> dict[str, Any]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
    except OSError as error:
        raise IntegrityError(f"{label} cannot be opened safely") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_SCRIPT_ENTRY_RECEIPT_BYTES
        ):
            raise IntegrityError(f"{label} metadata is unsafe")
        chunks: list[bytes] = []
        remaining = MAX_SCRIPT_ENTRY_RECEIPT_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 4096))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if len(encoded) > MAX_SCRIPT_ENTRY_RECEIPT_BYTES:
            raise IntegrityError(f"{label} exceeds its size limit")
    finally:
        os.close(descriptor)
    return _strict_json_object(encoded, label)


def _read_script_plan_receipt(
    directory_fd: int,
    expected_entries: list[dict[str, Any]],
    expected_identity: dict[str, str],
    expected_digest: str,
    entry_receipts: list[Any],
) -> tuple[str, int | None]:
    record = _read_json_receipt_at(directory_fd, "plan.json", "script-plan receipt")
    if not isinstance(record, dict) or set(record) != _SCRIPT_PLAN_RECEIPT_FIELDS:
        raise IntegrityError("script-plan receipt has an invalid schema")
    state = record["state"]
    host_signal = record["hostCancellationSignal"]
    if (
        type(record["version"]) is not int
        or record["version"] != SCRIPT_ENTRY_RECEIPT_VERSION
        or record["identity"] != expected_identity
        or record["planDigest"] != expected_digest
        or type(record["entryCount"]) is not int
        or record["entryCount"] != len(expected_entries)
        or not isinstance(state, str)
        or state not in {"accepted", "candidate-failure", "infrastructure-failure", "interrupted"}
        or (
            host_signal is not None
            and (
                not isinstance(host_signal, int)
                or host_signal not in {signal.SIGINT, signal.SIGTERM}
            )
        )
        or (state == "interrupted" and host_signal is None)
        or (state in {"accepted", "candidate-failure"} and host_signal is not None)
    ):
        raise IntegrityError("script-plan receipt identity or outcome is contradictory")
    if state == "infrastructure-failure" and host_signal is not None:
        matching_cleanup_failure = any(
            isinstance(receipt, dict)
            and receipt.get("version") == SCRIPT_ENTRY_RECEIPT_VERSION
            and receipt.get("identity") == expected_identity
            and receipt.get("planDigest") == expected_digest
            and receipt.get("ordinal") == ordinal
            and receipt.get("entry") == expected_entry
            and receipt.get("state") == "infrastructure-failure"
            and receipt.get("cleanup") in {"failed", "unverified"}
            and receipt.get("hostCancellationSignal") == host_signal
            for ordinal, (expected_entry, receipt) in enumerate(
                zip(expected_entries, entry_receipts)
            )
        )
        if not matching_cleanup_failure:
            raise IntegrityError(
                "infrastructure plan signal lacks matching failed-cleanup entry evidence"
            )
    return state, host_signal


def validate_script_entry_receipts(
    receipt_directory: str | Path,
    entries: Any,
    identity: Any,
    fail_fast: bool,
    *,
    require_plan_receipt: bool = True,
    checkpoint: Callable[[str], None] | None = None,
    pending_plan_state: str | None = None,
    pending_host_signal: int | None = None,
) -> list[dict[str, Any]]:
    expected_entries = _validate_script_entry_plan(entries)
    expected_identity = _validate_script_entry_identity(identity)
    if type(fail_fast) is not bool:
        raise IntegrityError("script-entry receipt failFast value must be a boolean")
    if require_plan_receipt and (pending_plan_state is not None or pending_host_signal is not None):
        raise IntegrityError("pending script-plan state cannot override a published receipt")
    if pending_plan_state not in {None, "accepted", "candidate-failure", "infrastructure-failure", "interrupted"}:
        raise IntegrityError("pending script-plan state is invalid")
    if pending_plan_state == "interrupted":
        if pending_host_signal not in {signal.SIGINT, signal.SIGTERM}:
            raise IntegrityError("pending interruption lacks a host signal")
    elif pending_host_signal is not None:
        if (
            pending_plan_state != "infrastructure-failure"
            or pending_host_signal not in {signal.SIGINT, signal.SIGTERM}
        ):
            raise IntegrityError("pending host signal lacks matching infrastructure state")
    directory = Path(receipt_directory)
    if directory.is_symlink() or not directory.is_dir():
        raise IntegrityError("script-entry receipt store is missing or unsafe")
    directory_metadata = directory.lstat()
    if (
        not stat.S_ISDIR(directory_metadata.st_mode)
        or stat.S_IMODE(directory_metadata.st_mode) != 0o700
        or directory_metadata.st_uid != os.geteuid()
    ):
        raise IntegrityError("script-entry receipt store ownership or permissions changed")
    directory_fd = os.open(
        directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        expected_names = [f"entry-{index:06d}.json" for index in range(len(expected_entries))]
        if require_plan_receipt:
            expected_names.append("plan.json")
        if sorted(os.listdir(directory_fd)) != expected_names:
            raise IntegrityError("script-entry receipt set has missing or extra records")
        plan_digest = _script_entry_plan_digest(expected_entries, fail_fast)
        entry_receipts = [
            _read_json_receipt_at(
                directory_fd,
                f"entry-{ordinal:06d}.json",
                "script-entry receipt",
            )
            for ordinal in range(len(expected_entries))
        ]
        plan_state: str | None = None
        plan_signal: int | None = None
        if checkpoint is not None:
            checkpoint("validation")
        if require_plan_receipt:
            plan_state, plan_signal = _read_script_plan_receipt(
                directory_fd,
                expected_entries,
                expected_identity,
                plan_digest,
                entry_receipts,
            )
        else:
            plan_state, plan_signal = pending_plan_state, pending_host_signal

        receipts: list[dict[str, Any]] = []
        failure_latched = plan_state == "interrupted"
        terminal_state: str | None = None
        interrupted_entries: list[dict[str, Any]] = []
        aborted_suffix = False
        for ordinal, (expected_entry, receipt) in enumerate(
            zip(expected_entries, entry_receipts)
        ):
            if not isinstance(receipt, dict) or set(receipt) != _SCRIPT_ENTRY_RECEIPT_FIELDS:
                raise IntegrityError("script-entry receipt has an invalid schema")
            if (
                type(receipt["version"]) is not int
                or receipt["version"] != SCRIPT_ENTRY_RECEIPT_VERSION
                or type(receipt["ordinal"]) is not int
                or receipt["ordinal"] != ordinal
                or receipt["identity"] != expected_identity
                or receipt["planDigest"] != plan_digest
                or canonical_json(receipt["entry"]) != canonical_json(expected_entry)
            ):
                raise IntegrityError("script-entry receipt identity or plan binding mismatch")
            state = receipt["state"]
            if aborted_suffix and state != "not-run-aborted":
                raise IntegrityError("script-entry receipt follows the aborted suffix")
            exit_code = receipt["exitCode"]
            candidate_signal = receipt["signal"]
            candidate_started = receipt["candidateStarted"]
            host_signal = receipt["hostCancellationSignal"]
            supervisor_signal = receipt["supervisorSignal"]
            cleanup = receipt["cleanup"]
            if type(candidate_started) is not bool:
                raise IntegrityError("script-entry candidate-started field is invalid")
            if (
                exit_code is not None
                and (type(exit_code) is not int or not 0 <= exit_code <= 255)
            ):
                raise IntegrityError("script-entry candidate exit code is invalid")
            if (
                candidate_signal is not None
                and (
                type(candidate_signal) is not int
                    or not 1 <= candidate_signal <= 127
                )
            ):
                raise IntegrityError("script-entry candidate signal is invalid")
            if exit_code is not None and candidate_signal is not None:
                raise IntegrityError("script-entry candidate outcome is contradictory")
            if (
                host_signal is not None
                and (
                    not isinstance(host_signal, int)
                    or host_signal not in {signal.SIGINT, signal.SIGTERM}
                )
            ):
                raise IntegrityError("script-entry host cancellation signal is invalid")
            if (
                supervisor_signal is not None
                and (
                    not isinstance(supervisor_signal, int)
                    or supervisor_signal not in {signal.SIGINT, signal.SIGTERM}
                    or supervisor_signal != host_signal
                )
            ):
                raise IntegrityError("script-entry supervisor signal lacks host provenance")
            if cleanup not in {"verified", "not-started", "unverified", "failed"}:
                raise IntegrityError("script-entry cleanup state is invalid")

            if state == "completed":
                if (
                    terminal_state is not None
                    or not candidate_started
                    or type(exit_code) is not int
            or exit_code >= 128
                    or candidate_signal is not None
                    or host_signal is not None
                    or supervisor_signal is not None
                    or cleanup != "verified"
                ):
                    raise IntegrityError("completed script-entry receipt is contradictory")
                if exit_code != 0:
                    failure_latched = True
                    if fail_fast:
                        terminal_state = "fail-fast"
            elif state == "not-run-fail-fast":
                if (
                    not fail_fast
                    or terminal_state != "fail-fast"
                    or candidate_started
                    or exit_code is not None
                    or candidate_signal is not None
                    or host_signal is not None
                    or supervisor_signal is not None
                    or cleanup != "not-started"
                ):
                    raise IntegrityError("fail-fast receipt is unexpected or contradictory")
            elif state == "interrupted":
                if (
                    host_signal is None
                    or terminal_state is not None
                    or (
                        candidate_started
                        and cleanup != "verified"
                    )
                    or (
                        not candidate_started
                        and (
                            exit_code is not None
                            or candidate_signal is not None
                            or cleanup not in {"verified", "not-started"}
                        )
                    )
                ):
                    raise IntegrityError("interrupted script-entry receipt is contradictory")
                interrupted_entries.append(receipt)
                failure_latched = True
                terminal_state = "aborted"
                aborted_suffix = True
            elif state in {"candidate-terminal", "infrastructure-failure"}:
                if (
                    terminal_state is not None
                    or (
                        host_signal is not None
                        and (
                            state != "infrastructure-failure"
                            or cleanup not in {"failed", "unverified"}
                        )
                    )
                    or (
                        supervisor_signal is not None
                        and (
                            state != "infrastructure-failure"
                            or cleanup not in {"failed", "unverified"}
                        )
                    )
                    or (not candidate_started and (exit_code is not None or candidate_signal is not None))
                or (
                    state == "infrastructure-failure"
                    and candidate_started
                    and cleanup == "not-started"
                )
                    or cleanup not in {"verified", "not-started", "unverified", "failed"}
                    or (
                        state == "candidate-terminal"
                        and (
                            not candidate_started
                            or not (
                                candidate_signal is not None
                                or (exit_code is not None and exit_code >= 128)
                            )
                            or cleanup != "verified"
                        )
                    )
                ):
                    raise IntegrityError("terminal script-entry receipt is contradictory")
                failure_latched = True
                terminal_state = "aborted"
            elif state == "not-run-aborted":
                if (
                (terminal_state != "aborted" and plan_state != "interrupted")
                    or candidate_started
                    or exit_code is not None
                    or candidate_signal is not None
                    or host_signal is not None
                    or supervisor_signal is not None
                    or cleanup != "not-started"
                ):
                    raise IntegrityError("aborted receipt is unexpected or contradictory")
                aborted_suffix = True
            else:
                raise IntegrityError("script-entry receipt has an unknown state")
            receipts.append(receipt)
            if checkpoint is not None:
                checkpoint("validation-entry")

        if not failure_latched and any(receipt["state"] != "completed" for receipt in receipts):
            raise IntegrityError("successful script-entry receipt set contains an unexecuted entry")
        if require_plan_receipt:
            if plan_state == "accepted" and (
                failure_latched
                or any(
                    receipt["state"] != "completed" or receipt["exitCode"] != 0
                    for receipt in receipts
                )
            ):
                raise IntegrityError("accepted script-plan receipt contains an unsuccessful entry")
            if plan_state == "candidate-failure" and (
                not any(
                    receipt["state"] == "completed" and receipt["exitCode"] != 0
                    for receipt in receipts
                )
                or any(receipt["state"] in {"candidate-terminal", "infrastructure-failure", "interrupted"} for receipt in receipts)
            ):
                raise IntegrityError("candidate-failure plan receipt lacks an ordinary candidate failure")
            if plan_state == "infrastructure-failure":
                if not any(
                    receipt["state"] in {"candidate-terminal", "infrastructure-failure"}
                    for receipt in receipts
                ):
                    raise IntegrityError("infrastructure plan receipt lacks a terminal entry")
                host_signal_entries = [
                    receipt
                    for receipt in receipts
                    if receipt["hostCancellationSignal"] is not None
                ]
                if plan_signal is None:
                    if host_signal_entries:
                        raise IntegrityError(
                            "infrastructure entry host signal is missing from the plan receipt"
                        )
                elif not any(
                    receipt["state"] == "infrastructure-failure"
                    and receipt["cleanup"] in {"failed", "unverified"}
                    and receipt["hostCancellationSignal"] == plan_signal
                    for receipt in host_signal_entries
                ):
                    raise IntegrityError(
                        "infrastructure plan signal lacks matching failed-cleanup entry evidence"
                    )
            if plan_state == "interrupted":
                if plan_signal is None or any(
                    receipt["state"] in {"candidate-terminal", "infrastructure-failure"}
            or receipt["state"] == "not-run-fail-fast"
                    or (
                        receipt["state"] == "interrupted"
                        and receipt["hostCancellationSignal"] != plan_signal
                    )
                    for receipt in receipts
                ):
                    raise IntegrityError(
                        "interrupted plan receipt contradicts terminal outcome or host cancellation evidence"
                    )
            elif interrupted_entries:
                raise IntegrityError("interrupted entry appears in a non-interrupted plan")
        return receipts
    finally:
        os.close(directory_fd)

def run_script_plan_with_receipts(
    entries: Any,
    identity: Any,
    fail_fast: bool,
    runner_temp: str | Path,
    execute_entry: Callable[[int, dict[str, Any]], ScriptEntryExecutionResult],
    *,
    host_signals: HostSignalQueue | _CallableHostSignalQueue | None = None,
    cancellation_signal: Callable[[], int | None] | None = None,
    test_hook: Callable[[str], None] | None = None,
) -> ScriptPlanRunResult:
    normalized_entries = _validate_script_entry_plan(entries)
    normalized_identity = _validate_script_entry_identity(identity)
    if type(fail_fast) is not bool:
        raise IntegrityError("script-plan failFast value must be a boolean")
    signals = _coerce_host_signal_queue(host_signals, cancellation_signal)
    plan_digest = _script_entry_plan_digest(normalized_entries, fail_fast)
    receipt_directory, directory_fd = _create_script_entry_receipt_store(runner_temp)
    failure_latched = False
    plan_state: str | None = None
    interruption_signal: int | None = None
    infrastructure_host_signal: int | None = None
    terminal_error: BaseException | None = None
    terminal_ordinal: int | None = None
    first_failure_ordinal: int | None = None
    staged_plan: str | None = None

    def checkpoint(name: str) -> None:
        if test_hook is not None:
            test_hook(name)

    def current_signal() -> int | None:
        return signals.poll()

    def write_entry(
        ordinal: int,
        entry: dict[str, Any],
        state: str,
        exit_code: int | None,
        candidate_signal: int | None,
        cleanup: str,
        *,
        candidate_started: bool,
        host_signal: int | None = None,
        supervisor_signal: int | None = None,
    ) -> None:
        _write_script_entry_receipt(
            directory_fd,
            {
                "version": SCRIPT_ENTRY_RECEIPT_VERSION,
                "identity": normalized_identity,
                "planDigest": plan_digest,
                "ordinal": ordinal,
                "entry": entry,
                "state": state,
                "exitCode": exit_code,
                "signal": candidate_signal,
                "candidateStarted": candidate_started,
                "hostCancellationSignal": host_signal,
                "supervisorSignal": supervisor_signal,
                "cleanup": cleanup,
            },
            checkpoint=checkpoint,
        )

    def write_skipped(start: int, state: str) -> None:
        for skipped_ordinal, skipped_entry in enumerate(normalized_entries[start:], start):
            write_entry(
                skipped_ordinal,
                skipped_entry,
                state,
                None,
                None,
                "not-started",
                candidate_started=False,
            )

    def reclassify_fail_fast_suffix(start: int) -> None:
        for skipped_ordinal, skipped_entry in enumerate(normalized_entries[start:], start):
            receipt = _read_json_receipt_at(
                directory_fd,
                f"entry-{skipped_ordinal:06d}.json",
                "script-entry",
            )
            expected = {
                "version": SCRIPT_ENTRY_RECEIPT_VERSION,
                "identity": normalized_identity,
                "planDigest": plan_digest,
                "ordinal": skipped_ordinal,
                "entry": skipped_entry,
                "state": "not-run-fail-fast",
                "exitCode": None,
                "signal": None,
                "candidateStarted": False,
                "hostCancellationSignal": None,
                "supervisorSignal": None,
                "cleanup": "not-started",
            }
            if receipt != expected:
                raise IntegrityError("fail-fast suffix changed before interruption cutoff")
            receipt["state"] = "not-run-aborted"
            _replace_script_entry_receipt(directory_fd, receipt)

    def interrupted_error(
        signum: int,
        *,
        candidate_started: bool = False,
        candidate_exit_code: int | None = None,
        candidate_signal: int | None = None,
        supervisor_signal: int | None = None,
        cleanup: str = "unverified",
    ) -> ScriptPlanInterrupted:
        failure = ScriptPlanInterrupted(
            signum,
            candidate_started=candidate_started,
            candidate_exit_code=candidate_exit_code,
            candidate_signal=candidate_signal,
            supervisor_signal=supervisor_signal,
        )
        failure.cleanup_status = cleanup
        return failure

    def write_interrupted_entry(
        ordinal: int,
        entry: dict[str, Any],
        signum: int,
        *,
        candidate_started: bool,
        candidate_exit_code: int | None,
        candidate_signal: int | None,
        supervisor_signal: int | None,
        cleanup: str,
    ) -> None:
        write_entry(
            ordinal,
            entry,
            "interrupted",
            candidate_exit_code,
            candidate_signal,
            cleanup,
            candidate_started=candidate_started,
            host_signal=signum,
            supervisor_signal=supervisor_signal,
        )

    try:
        for ordinal, entry in enumerate(normalized_entries):
            signum = current_signal()
            if signum is not None:
                write_interrupted_entry(
                    ordinal,
                    entry,
                    signum,
                    candidate_started=False,
                    candidate_exit_code=None,
                    candidate_signal=None,
                    supervisor_signal=None,
                    cleanup="not-started",
                )
                write_skipped(ordinal + 1, "not-run-aborted")
                plan_state = "interrupted"
                interruption_signal = signum
                terminal_error = interrupted_error(signum, cleanup="not-started")
                terminal_ordinal = ordinal
                failure_latched = True
                break
            try:
                execution = execute_entry(ordinal, dict(entry))
                if (
                    not isinstance(execution, ScriptEntryExecutionResult)
                    or type(execution.return_code) is not int
                    or not 0 <= execution.return_code <= 255
                ):
                    raise IntegrityError("script executor returned an invalid status record")
                if execution.return_code >= 128:
                    raise ScriptCandidateTerminal(
                        "candidate exit status is ambiguous with signal termination",
                return_code=execution.return_code,
                        cleanup_status=execution.cleanup_status,
                        candidate_started=True,
                    )
                if execution.cleanup_status != "verified":
                    raise ScriptCandidateTerminal(
                        "candidate cleanup failed after execution",
                        return_code=execution.return_code,
                        cleanup_status=execution.cleanup_status,
                        candidate_started=True,
                    )
            except BaseException as error:
                error_host_signal = getattr(error, "host_cancellation_signal", None)
                if isinstance(error, ScriptPlanInterrupted):
                    error_host_signal = error.signum
                latched_signal = current_signal()
                if (
                    error_host_signal is None
                    and getattr(error, "cleanup_status", None) in {"failed", "unverified"}
                ):
                    error_host_signal = latched_signal
                if (
                    isinstance(error, ScriptPlanInterrupted)
                    and error.cleanup_status != "failed"
                ):
                    if (
                        latched_signal is None
                        or error_host_signal != latched_signal
                    ):
                        error = ScriptCandidateTerminal(
                            "supervisor interruption lacks a host signal latch",
                            return_code=error.candidate_exit_code,
                            signum=error.candidate_signal,
                            cleanup_status=error.cleanup_status,
                            candidate_started=error.candidate_started,
                        )
                    else:
                        write_interrupted_entry(
                            ordinal,
                            entry,
                            latched_signal,
                            candidate_started=error.candidate_started,
                            candidate_exit_code=error.candidate_exit_code,
                            candidate_signal=error.candidate_signal,
                            supervisor_signal=error.supervisor_signal,
                            cleanup=error.cleanup_status,
                        )
                        write_skipped(ordinal + 1, "not-run-aborted")
                        plan_state = "interrupted"
                        interruption_signal = latched_signal
                        terminal_error = error
                        terminal_ordinal = ordinal
                        failure_latched = True
                        break

                candidate_started = bool(getattr(error, "candidate_started", False))
                error_return_code = getattr(error, "return_code", None)
                if error_return_code is None:
                    error_return_code = getattr(error, "candidate_exit_code", None)
                error_candidate_signal = getattr(error, "candidate_signal", None)
                if (
                    error_candidate_signal is None
                    and isinstance(error, ScriptCandidateTerminal)
                ):
                    error_candidate_signal = error.signum
                error_supervisor_signal = getattr(error, "supervisor_signal", None)
                error_cleanup = getattr(error, "cleanup_status", "unverified")
                receipt_host_signal = (
                    error_host_signal
                    if error_cleanup in {"failed", "unverified"}
                    and isinstance(error_host_signal, int)
                    else None
                )
                if receipt_host_signal is not None:
                    infrastructure_host_signal = receipt_host_signal
                terminal_state = (
                    "candidate-terminal"
                    if error_cleanup == "verified"
                    and candidate_started
            and not bool(getattr(error, "infrastructure_failure", False))
                    and (
                        error_candidate_signal is not None
                        or (
                            isinstance(error_return_code, int)
                            and error_return_code >= 128
                        )
                    )
                    else "infrastructure-failure"
                )
                write_entry(
                    ordinal,
                    entry,
                    terminal_state,
                    error_return_code if isinstance(error_return_code, int) else None,
                    error_candidate_signal
                    if isinstance(error_candidate_signal, int)
                    else None,
                    error_cleanup,
                    candidate_started=candidate_started,
                    host_signal=receipt_host_signal,
                    supervisor_signal=(
                        error_supervisor_signal
                        if isinstance(error_supervisor_signal, int)
                        and error_cleanup in {"failed", "unverified"}
                        else None
                    ),
                )
                write_skipped(ordinal + 1, "not-run-aborted")
                failure_latched = True
                terminal_error = error
                terminal_ordinal = ordinal
                plan_state = "infrastructure-failure"
                break

            write_entry(
                ordinal,
                entry,
                "completed",
                execution.return_code,
                None,
                execution.cleanup_status,
                candidate_started=True,
            )
            if execution.return_code != 0:
                failure_latched = True
                if first_failure_ordinal is None:
                    first_failure_ordinal = ordinal
                if fail_fast:
                    write_skipped(ordinal + 1, "not-run-fail-fast")
                    plan_state = "candidate-failure"
                    terminal_ordinal = ordinal
                    break
            signum = current_signal()
            if signum is not None:
                write_skipped(ordinal + 1, "not-run-aborted")
                plan_state = "interrupted"
                interruption_signal = signum
                terminal_error = interrupted_error(signum, cleanup="verified")
                terminal_ordinal = ordinal
                failure_latched = True
                break

        if plan_state is None:
            plan_state = "candidate-failure" if failure_latched else "accepted"
            if plan_state == "candidate-failure":
                terminal_ordinal = first_failure_ordinal

        plan_host_signal = (
            interruption_signal
            if plan_state == "interrupted"
            else infrastructure_host_signal
            if plan_state == "infrastructure-failure"
            else None
        )
        validate_script_entry_receipts(
            receipt_directory,
            normalized_entries,
            normalized_identity,
            fail_fast,
            require_plan_receipt=False,
            checkpoint=checkpoint,
            pending_plan_state=plan_state,
            pending_host_signal=plan_host_signal,
        )
        plan_receipt = {
            "version": SCRIPT_ENTRY_RECEIPT_VERSION,
            "identity": normalized_identity,
            "planDigest": plan_digest,
            "entryCount": len(normalized_entries),
            "state": plan_state,
            "hostCancellationSignal": plan_host_signal,
        }
        staged_plan = _write_script_plan_receipt(
            directory_fd,
            plan_receipt,
            checkpoint=checkpoint,
        )
        checkpoint("before-acceptance")
        acceptance_signal = signals.acceptance_cutoff()
        if acceptance_signal is not None and plan_state != "infrastructure-failure":
            if (
                plan_state == "candidate-failure"
                and fail_fast
                and terminal_ordinal is not None
            ):
                reclassify_fail_fast_suffix(terminal_ordinal + 1)
            plan_receipt["state"] = "interrupted"
            plan_receipt["hostCancellationSignal"] = acceptance_signal
            plan_state = "interrupted"
            interruption_signal = acceptance_signal
            if terminal_error is None or not isinstance(terminal_error, ScriptPlanInterrupted):
                terminal_error = interrupted_error(
                    acceptance_signal,
                    cleanup="verified",
                )
                terminal_ordinal = (
                    terminal_ordinal
                    if terminal_ordinal is not None
                    else max(0, len(normalized_entries) - 1)
                )
        _replace_staged_script_plan_receipt(directory_fd, staged_plan, plan_receipt)
        _publish_script_plan_receipt(directory_fd, staged_plan)
        staged_plan = None
        validate_script_entry_receipts(
            receipt_directory,
            normalized_entries,
            normalized_identity,
            fail_fast,
            require_plan_receipt=True,
        )
        if plan_state == "accepted":
            return ScriptPlanRunResult(0, receipt_directory, plan_digest)
        if plan_state == "candidate-failure":
            return ScriptPlanRunResult(1, receipt_directory, plan_digest)
        if terminal_error is None:
            terminal_error = ArtifactError("script plan completed with candidate failures")
        if terminal_ordinal is None:
            terminal_ordinal = max(0, len(normalized_entries) - 1)
        raise ScriptPlanExecutionError(
            terminal_ordinal, receipt_directory, terminal_error
        ) from terminal_error
    finally:
        if staged_plan is not None:
            try:
                os.unlink(staged_plan, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)

def extinguish_supervised_process_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    graceful_deadline = time.monotonic() + 1
    while time.monotonic() < graceful_deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while True:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            raise IntegrityError("candidate process group survived bounded SIGKILL cleanup")
        time.sleep(0.05)


def bubblewrap_network_args(
    *,
    shared_artifact: bool,
    legacy_unshare: bool = False,
    postgres_profile: bool = False,
) -> list[str]:
    args = ["--cap-drop", "ALL"]
    if shared_artifact or legacy_unshare or postgres_profile:
        args.append("--unshare-net")
    return args


def _path_parts(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, str) or not value or len(value) > 1024 or value.startswith("/") or "\\" in value:
        return None
    if any(ord(char) < 32 or ord(char) == 127 or char in "*?[]{}" for char in value):
        return None
    parts = tuple(value.split("/"))
    if len(parts) > MAX_DEPTH or any(part in {"", ".", ".."} for part in parts):
        return None
    if any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in parts):
        return None
    return parts


def normalize_roots(value: Any, label: str, *, nonempty: bool = True) -> tuple[str, ...] | None:
    if not isinstance(value, list) or (nonempty and not value) or len(value) > 64:
        return None
    roots: list[str] = []
    for item in value:
        parts = _path_parts(item)
        if parts is None or item in roots:
            return None
        roots.append("/".join(parts))
    return tuple(roots)


def _is_within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _roots_overlap(roots: tuple[str, ...]) -> bool:
    return any(
        first != second and (_is_within(first, second) or _is_within(second, first))
        for index, first in enumerate(roots)
        for second in roots[index + 1:]
    )


def artifact_mount_roots(roots: Any) -> tuple[str, ...]:
    normalized = normalize_roots(roots, "ArtifactSet roots")
    if normalized is None or _roots_overlap(normalized):
        raise IntegrityError("ArtifactSet roots are invalid or overlap")
    if any("/" in root for root in normalized):
        raise IntegrityError("nested ArtifactSet roots cannot be mounted without hiding checkout siblings")
    return normalized


def requested_roots_allowed(
    requested: tuple[str, ...], allowed: tuple[str, ...], required: tuple[str, ...]
) -> bool:
    return all(any(_is_within(root, approved) for approved in allowed) for root in requested) and all(
        any(_is_within(required_root, requested_root) for requested_root in requested)
        for required_root in required
    )


def load_policy_table(source: str | bytes) -> dict[str, Any]:
    if len(source) > MAX_POLICY_BYTES:
        raise IntegrityError("trusted ArtifactSet policy exceeds size limit")
    try:
        raw = json.loads(source)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise IntegrityError("trusted ArtifactSet policy is not valid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {"version", "repositories"}:
        raise IntegrityError("trusted ArtifactSet policy schema is invalid")
    version = raw.get("version")
    if type(version) is not int or version not in {1, 2}:
        raise IntegrityError("trusted ArtifactSet policy schema is invalid")
    repositories = raw.get("repositories")
    if not isinstance(repositories, dict) or any(not isinstance(key, str) for key in repositories):
        raise IntegrityError("trusted ArtifactSet policy repository map is invalid")
    if version == 1 and repositories:
        raise IntegrityError("trusted ArtifactSet policy version 1 cannot enroll repositories")
    return raw


def _policy_row(table: dict[str, Any], repository: str) -> dict[str, Any] | None:
    if table.get("version") != 2:
        return None
    row = table["repositories"].get(repository)
    if not isinstance(row, dict) or set(row) != _POLICY_FIELDS:
        return None
    if row.get("repository") != repository or row.get("visibility") != "private":
        return None
    if not isinstance(row.get("audienceApproved"), bool):
        return None
    caller_workflow_path = row.get("callerWorkflowPath")
    caller_workflow_digest = row.get("callerWorkflowDigest")
    if (
        not _is_caller_workflow_path(caller_workflow_path)
        or not isinstance(caller_workflow_digest, str)
        or _SHA256.fullmatch(caller_workflow_digest) is None
    ):
        return None
    if not isinstance(row.get("producer"), str) or not _ID.fullmatch(row["producer"]):
        return None
    if not isinstance(row.get("buildScript"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}", row["buildScript"]):
        return None
    consumers = row.get("consumers")
    if not isinstance(consumers, list) or not consumers or len(consumers) > 8:
        return None
    if any(not isinstance(item, str) or not _ID.fullmatch(item) for item in consumers) or len(set(consumers)) != len(consumers):
        return None
    allowed = normalize_roots(row.get("allowedPaths"), "allowedPaths")
    required = normalize_roots(row.get("requiredPaths"), "requiredPaths", nonempty=False)
    profiles = normalize_roots(row.get("profileFiles"), "profileFiles")
    if allowed is None or required is None or profiles is None:
        return None
    if "package.json" not in profiles or not any(
        Path(item).name in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb"}
        for item in profiles
    ):
        return None
    if row["producer"] in consumers:
        return None
    return {
        **row,
        "allowedPaths": allowed,
        "requiredPaths": required,
        "profileFiles": profiles,
        "callerWorkflowPath": caller_workflow_path,
        "callerWorkflowDigest": caller_workflow_digest,
    }


def _is_caller_workflow_path(value: Any) -> bool:
    parts = _path_parts(value)
    return (
        parts is not None
        and len(parts) == 3
        and parts[:2] == (".github", "workflows")
        and Path(parts[2]).suffix.lower() in {".yml", ".yaml"}
    )


def _caller_workflow_path_from_ref(repository: str, workflow_ref: str) -> str | None:
    if not isinstance(workflow_ref, str):
        return None
    repository_prefix = f"{repository}/"
    if not workflow_ref.casefold().startswith(repository_prefix.casefold()):
        return None
    workflow_path, separator, ref = workflow_ref[len(repository_prefix):].partition("@refs/")
    if not separator or not ref or not _is_caller_workflow_path(workflow_path):
        return None
    return workflow_path


def _caller_workflow_matches_policy(
    *,
    repository: Path,
    workflow_commit: str,
    workflow_path: str,
    workflow_digest: str,
) -> bool:
    if not _REVISION.fullmatch(workflow_commit):
        return False
    try:
        entries = subprocess.run(
            ["git", "-C", str(repository), "ls-tree", "-z", "--full-tree", workflow_commit, "--", workflow_path],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.split(b"\0")
        matching: list[tuple[str, str, str]] = []
        for entry in entries:
            metadata, separator, raw_path = entry.partition(b"\t")
            if not separator or raw_path.decode("utf-8", errors="strict") != workflow_path:
                continue
            mode, kind, object_id = metadata.decode("ascii", errors="strict").split(" ")
            matching.append((mode, kind, object_id))
    except (subprocess.CalledProcessError, UnicodeDecodeError, ValueError):
        return False
    if len(matching) != 1:
        return False
    mode, kind, object_id = matching[0]
    if kind != "blob" or mode not in {"100644", "100755"}:
        return False
    try:
        workflow_bytes = subprocess.run(
            ["git", "-C", str(repository), "cat-file", "blob", object_id],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError:
        return False
    return hashlib.sha256(workflow_bytes).hexdigest() == workflow_digest


def _pull_request_workflow_graph_unchanged(checkout: Path, base_commit: str, commit: str) -> bool:
    if not _REVISION.fullmatch(base_commit):
        raise IntegrityError("pull request base commit is not an immutable full object ID")
    result = subprocess.run(
        ["git", "-C", str(checkout), "diff", "--quiet", base_commit, commit, "--", ".github"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise IntegrityError("cannot compare caller workflow graph with pull request base")


def _event_branch_matches(level: dict[str, Any], event_name: str, base_ref: str, ref_name: str) -> bool:
    events = level.get("events")
    branches = level.get("branches")
    if events is not None and event_name not in events:
        return False
    return branches is None or (base_ref or ref_name) in branches


def mode_gate(
    mode_plan_source: str,
    *,
    event_name: str,
    base_ref: str,
    ref_name: str,
    repository: str,
    head_repository: str,
    secretless_pr: bool,
    secretless_trusted_ref: bool,
) -> bool:
    try:
        levels = json.loads(mode_plan_source)
    except (json.JSONDecodeError, TypeError) as exc:
        raise IntegrityError("trusted mode-gate plan is invalid JSON") from exc
    if not isinstance(levels, list) or not levels or len(levels) > 16:
        raise IntegrityError("trusted mode-gate plan is not a bounded level list")
    if event_name not in {"pull_request", "push", "workflow_dispatch"}:
        raise ArtifactError("buildArtifact supports only pull_request, push, and workflow_dispatch")
    expected = (event_name == "pull_request", event_name in {"push", "workflow_dispatch"})
    if (secretless_pr, secretless_trusted_ref) != expected:
        raise ArtifactError("event-appropriate secretless mode was not generated")
    share_eligible = event_name != "pull_request" or repository.lower() == head_repository.lower()
    seen: set[str] = set()
    for level in levels:
        if not isinstance(level, dict) or set(level) != {"id", "events", "branches"}:
            raise IntegrityError("trusted mode-gate plan contains an invalid level")
        level_id = level["id"]
        if not isinstance(level_id, str) or not _ID.fullmatch(level_id) or level_id in seen:
            raise IntegrityError("trusted mode-gate plan contains duplicate or invalid level id")
        seen.add(level_id)
        events = level["events"]
        branches = level["branches"]
        if events is not None and (
            not isinstance(events, list)
            or any(event not in {"pull_request", "push", "workflow_dispatch"} for event in events)
        ):
            raise ArtifactError("unsupported event filter in buildArtifact caller")
        if branches is not None and (
            not isinstance(branches, list) or any(not isinstance(branch, str) for branch in branches)
        ):
            raise ArtifactError("unsupported branch filter in buildArtifact caller")
        if _event_branch_matches(level, event_name, base_ref, ref_name):
            # Generated caller calls carry these modes on every level; the gate
            # itself receives the same event-derived values and runs before code.
            if (secretless_pr, secretless_trusted_ref) != expected:
                raise ArtifactError(f"level {level_id} has no valid secretless execution mode")
    return share_eligible


def _needs_satisfied(level: dict[str, Any], needs_results: dict[str, Any]) -> bool:
    for need in level.get("needs", []):
        result = needs_results.get(need["level"], {}).get("result")
        if need["condition"] == "success" and result != "success":
            return False
        if need["condition"] == "completed" and result not in {"success", "failure"}:
            return False
    return True


def _participant_prerequisite_ids(plan: dict[str, Any]) -> tuple[str, ...]:
    artifact = plan.get("buildArtifact")
    levels = plan.get("levels")
    if not isinstance(artifact, dict) or not isinstance(levels, list):
        raise IntegrityError("typed buildArtifact prerequisite plan is malformed")
    participants = {artifact.get("producer"), *artifact.get("consumers", [])}
    level_map = {level.get("id"): level for level in levels if isinstance(level, dict)}
    if any(not isinstance(level_map.get(lane), dict) for lane in participants):
        raise IntegrityError("typed buildArtifact prerequisite participants are malformed")
    return tuple(
        sorted(
            {
                need["level"]
                for lane in participants
                for need in level_map[lane].get("needs", [])
            }
        )
    )


def _validate_prerequisite_results(plan: dict[str, Any], needs_results: Any) -> dict[str, dict[str, str]]:
    required = _participant_prerequisite_ids(plan)
    if not isinstance(needs_results, dict) or set(needs_results) != set(required):
        raise IntegrityError("trusted preflight prerequisite result projection is incomplete or unexpected")
    projection: dict[str, dict[str, str]] = {}
    for job_id, evidence in needs_results.items():
        if not isinstance(evidence, dict) or set(evidence) != {"result"}:
            raise IntegrityError("trusted preflight prerequisite result projection is not compact")
        result = evidence["result"]
        if result not in {"success", "failure", "cancelled", "skipped"}:
            raise IntegrityError("trusted preflight prerequisite result is invalid")
        projection[job_id] = {"result": result}
    return projection


def _role_matches_lane(role: str, lane: str, artifact: dict[str, Any]) -> bool:
    producer = artifact.get("producer")
    consumers = artifact.get("consumers")
    if role == "producer":
        return lane == producer
    if role == "consumer":
        return lane == producer or (isinstance(consumers, list) and lane in consumers)
    return False


def _script_plan_module():
    root = Path(__file__).resolve().parents[1]
    script_dir = os.environ.get("NODE_CI_SCRIPT_PLAN_DIR")
    sys.path.insert(0, script_dir or str(root / ".github/actions/node-ci-script-plan"))
    try:
        import script_plan
    except ImportError as exc:
        raise IntegrityError("pinned Node CI script-plan parser unavailable") from exc
    return script_plan


def _normalized_script_entry(entry: Any) -> dict[str, Any]:
    return {
        "script": entry.script,
        "unsetEnv": list(entry.unset_env),
        "requiresServices": entry.requires_services,
    }


def _participant_plans(
    level: dict[str, Any], build_script: str
) -> tuple[dict[str, str], list[str], dict[str, dict[str, Any]]] | None:
    inputs = level.get("inputs")
    if not isinstance(inputs, dict):
        return None
    if inputs.get("secretless-nested-manifests", "") not in {"", "[]"}:
        return None
    if inputs.get("secretless-compatibility-ranges", "") not in {"", "{}"}:
        return None
    if inputs.get("schema-dir", "") or inputs.get("db-image", "") or inputs.get("cache-image", ""):
        return None
    if any(key.endswith("protected-type-surface-declaration-path") and value for key, value in inputs.items()):
        return None
    parser = _script_plan_module()
    full_source = inputs.get("secretless-ci-script-plan", "")
    non_app_source = inputs.get("secretless-ci-non-app-script-plan", "") or full_source
    try:
        full = parser.parse_script_plan(full_source)
        non_app = parser.parse_script_plan(non_app_source)
    except (parser.ScriptPlanError, TypeError):
        return None
    if not full or not non_app:
        return None
    serialized: dict[str, str] = {}
    build_entries: dict[str, dict[str, Any]] = {}
    scripts: list[str] = []
    for variant, entries in (("full", full), ("nonapp", non_app)):
        effective_scripts = [entry.script for entry in entries]
        if any(entry.requires_services for entry in entries):
            return None
        if not effective_scripts or effective_scripts[0] != build_script or effective_scripts.count(build_script) != 1:
            return None
        build_entries[variant] = _normalized_script_entry(entries[0])
        for script in effective_scripts:
            if script not in scripts:
                scripts.append(script)
        serialized[variant] = json.dumps(
            [_normalized_script_entry(entry) for entry in entries[1:]],
            separators=(",", ":"),
        )
    if build_entries["full"] != build_entries["nonapp"]:
        return None
    return serialized, scripts, build_entries


def git_profile_blobs(repository: Path, commit: str, paths: tuple[str, ...]) -> list[dict[str, Any]]:
    if not _REVISION.fullmatch(commit):
        raise IntegrityError("caller commit is not a full immutable object id")
    records: dict[str, tuple[str, str]] = {}
    for path in paths:
        if _path_parts(path) is None:
            raise ArtifactError("profile file path is not normalized")
        result = subprocess.run(
            ["git", "-C", str(repository), "ls-tree", "-z", commit, "--", path],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        matching: list[tuple[str, str, str]] = []
        for raw in result.stdout.split(b"\0"):
            if not raw:
                continue
            metadata, separator, raw_path = raw.partition(b"\t")
            if not separator:
                raise IntegrityError("Git tree returned a malformed profile entry")
            mode, kind, oid = metadata.decode("ascii").split(" ")
            listed_path = raw_path.decode("utf-8", errors="strict")
            if listed_path == path:
                matching.append((mode, kind, oid))
        if len(matching) != 1:
            raise ArtifactError("policy profile path is missing or not a tracked regular file")
        mode, kind, oid = matching[0]
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ArtifactError("policy profile path is not a tracked regular file")
        records[path] = (mode, oid)
    files: list[dict[str, Any]] = []
    for path, (mode, oid) in records.items():
        blob = subprocess.run(
            ["git", "-C", str(repository), "cat-file", "blob", oid],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        files.append({"path": path, "mode": mode, "size": len(blob), "sha256": sha256_bytes(blob)})
    return sorted(files, key=lambda item: item["path"])


def _read_git_file(repository: Path, commit: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repository), "show", f"{commit}:{path}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def _tracked_npmrc_selects_workspaces(repository: Path, commit: str) -> bool:
    entries = subprocess.run(
        ["git", "-C", str(repository), "ls-tree", "-r", "-z", "--full-tree", commit],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    for entry in entries.split(b"\0"):
        metadata, separator, path = entry.partition(b"\t")
        if not separator or path.rsplit(b"/", 1)[-1] != b".npmrc":
            continue
        mode, object_type, object_id = metadata.split()
        if object_type != b"blob" or mode == b"120000":
            return True
        content = subprocess.run(
            ["git", "-C", str(repository), "cat-file", "blob", object_id.decode("ascii")],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.decode("utf-8", errors="replace")
        for line in content.splitlines():
            candidate = line.lstrip("\ufeff \t")
            if not candidate or candidate.startswith(("#", ";")):
                continue
            key = candidate.partition("=")[0].strip().lower()
            if len(key) > 1 and key[0] == key[-1] and key[0] in {"\"", "'"}:
                key = key[1:-1].strip()
            if key.endswith("[]"):
                key = key[:-2].strip()
            if key in {"workspace", "workspaces"}:
                return True
    return False


_PACKAGE_MANAGER_INVOKE = re.compile(r"(?:^|[;&|(){}\s/])(?:npm|pnpm|yarn|npx)\b")
_PACKAGE_MANAGER_NAMES = frozenset({"npm", "pnpm", "yarn", "npx"})
_SAFE_DIRECT_CHECK_COMMANDS = frozenset(
    {
        "true",
        "false",
        "echo",
        "printf",
        "test",
        "[",
        "vitest",
        "jest",
        "ava",
        "uvu",
        "playwright",
        "cypress",
        "eslint",
        "prettier",
        "biome",
        "stylelint",
        "markdownlint",
    }
)
_READ_ONLY_CHECK_SUBCOMMANDS = {
    "playwright": frozenset({"test"}),
    "cypress": frozenset({"run"}),
    "biome": frozenset({"check", "lint", "format"}),
}
_READ_ONLY_CHECK_OPTIONS = {
    "prettier": frozenset({"--check", "--list-different"}),
    "biome": frozenset({"--check"}),
}
_DYNAMIC_SHELL_EXPANSION = re.compile(r"\$(?:\([^)]*\)|\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*)|`")
_UNSUPPORTED_SHELL_SYNTAX = re.compile(r"['\\\"]")
_SHELL_ASSIGNMENT = re.compile(r"(?:^|[;&|(){}\s])[A-Za-z_][A-Za-z0-9_]*\s*=")
_WORKING_DIRECTORY_CHANGE = re.compile(r"(?:^|[;&|(){}\s])(?:cd|pushd|popd)\b")
_PACKAGE_SCOPE_REDIRECTION = re.compile(
    r"(?:^|[;&|(){}\s])(?:--(?:prefix|workspace|workspaces|ws|filter|dir|cwd|userconfig|globalconfig|location)|-[wC])(?:=|\s|$)",
    re.IGNORECASE,
)
_PACKAGE_SCRIPT_SHELL_OVERRIDE = re.compile(
    r"(?:^|[;&|(){}\s])--(?:script-shell|config\.script-shell|shell-path|shell-emulator)(?:=|\s|$)"
)
_OPAQUE_COMMAND_WRAPPER = re.compile(
    r"(?:^|[;&|(){}\s])(?:[^\s;&|(){}]*/)?"
    r"(?:env|command|exec|time|timeout|nice|sudo|nohup|xargs|busybox|setsid|stdbuf|chpst|runuser|su|doas|flock|daemonize|ionice|parallel)\b"
)
_OPAQUE_INTERPRETER_COMMAND = re.compile(
    r"(?:^|[;&|(){}\s]|\b(?:env|command|exec|time|timeout|nice|sudo)\s+)"
    r"(?:[^\s;&|(){}]*/)?(?:node|bun|deno|python|python3|ruby|perl)\b"
)
_OPAQUE_INLINE_EXECUTION = re.compile(
    r"(?:^|[;&|(){}\s])(?:[^\s;&|(){}]*/)?(?:bash|sh|dash|zsh)(?:\s|$)"
    r"|(?:^|[;&|(){}\s])(?:node|bun)\s+(?:-e|--eval|-p|--print)\b"
    r"|(?:^|[;&|(){}\s])(?:node|bun|python|python3|ruby|perl)\s+-\s*(?:\s|$)"
    r"|(?:^|[;&|(){}\s])(?:python|python3|ruby|perl)\s+(?:-e|-c|-m)\b"
    r"|(?:^|[;&|(){}\s])deno\s+eval\b"
    r"|(?:^|[;&|(){}\s])deno\s+run\s+-\s*(?:\s|$)"
    r"|\beval\b"
)
_NPM_SCRIPT_CALL = re.compile(
    r"(?:^|[;&|(){}\s/])npm\s+"
    r"(?:(?:--(?:silent|quiet|no-progress|ignore-scripts|if-present|no-audit|no-fund|offline|prefer-offline|foreground-scripts)"
    r")\s+)*(?:run|run-script)\s+([A-Za-z0-9][A-Za-z0-9._:-]*)(?=$|[\s;&|(){}])"
)


def _npm_script_forwards_options(command: str) -> bool:
    for match in _NPM_SCRIPT_CALL.finditer(command):
        trailing = command[match.end():]
        segment = re.split(r"[;&|(){}\n\r]", trailing, maxsplit=1)[0]
        if segment.strip():
            return True
    return False


def _direct_check_arguments_are_supported(name: str, tokens: list[str]) -> bool:
    if name in {"true", "false", "echo", "printf", "test", "["}:
        return True

    arguments = tokens[1:]
    if name == "vitest":
        if arguments and arguments[0] != "run":
            return False
        if arguments:
            arguments = arguments[1:]
    subcommands = _READ_ONLY_CHECK_SUBCOMMANDS.get(name)
    if subcommands is not None:
        if not arguments or arguments[0] not in subcommands:
            return False
        arguments = arguments[1:]

    allowed_options = _READ_ONLY_CHECK_OPTIONS.get(name, frozenset())
    return all(not argument.startswith("-") or argument in allowed_options for argument in arguments)


def _direct_commands_are_supported_checks(command: str) -> bool:
    if any(marker in command for marker in "(){}\r<>"):
        return False
    if any((ord(char) < 32 and char not in "\t\n") or ord(char) > 126 for char in command):
        return False
    for segment in re.split(r"[;&|(){}\n\r]", command):
        if any(marker in segment for marker in "*?[]"):
            return False
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return False
        if not tokens:
            continue
        executable = tokens[0]
        name = Path(executable).name
        if "/" in executable:
            return False
        if name in _PACKAGE_MANAGER_NAMES:
            continue
        if name not in _SAFE_DIRECT_CHECK_COMMANDS:
            return False
        if not _direct_check_arguments_are_supported(name, tokens):
            return False
    return True

def _script_reaches_build(
    package_scripts: dict[str, Any],
    script: str,
    build_script: str,
    visiting: frozenset[str] = frozenset(),
    *,
    include_lifecycle_hooks: bool = True,
) -> bool:
    if script == build_script or script in visiting:
        return True
    next_visiting = visiting | {script}
    command = package_scripts.get(script)
    if not isinstance(command, str):
        return True
    if (
        _UNSUPPORTED_SHELL_SYNTAX.search(command)
        or _DYNAMIC_SHELL_EXPANSION.search(command)
        or _SHELL_ASSIGNMENT.search(command)
        or _WORKING_DIRECTORY_CHANGE.search(command)
        or _PACKAGE_SCOPE_REDIRECTION.search(command)
        or _PACKAGE_SCRIPT_SHELL_OVERRIDE.search(command)
        or _npm_script_forwards_options(command)
        or _OPAQUE_COMMAND_WRAPPER.search(command)
        or _OPAQUE_INLINE_EXECUTION.search(command)
        or _OPAQUE_INTERPRETER_COMMAND.search(command)
    ):
        return True
    if include_lifecycle_hooks and any(
        hook in package_scripts
        and _script_reaches_build(
            package_scripts,
            hook,
            build_script,
            next_visiting,
            include_lifecycle_hooks=False,
        )
        for hook in (f"pre{script}", f"post{script}")
    ):
        return True
    calls = [match.group(1) for match in _NPM_SCRIPT_CALL.finditer(command)]
    if len(_PACKAGE_MANAGER_INVOKE.findall(command)) != len(calls):
        return True
    if not _direct_commands_are_supported_checks(command):
        return True
    if any(called not in package_scripts for called in calls):
        return True
    return any(
        called == build_script
        or (called in package_scripts and _script_reaches_build(package_scripts, called, build_script, next_visiting))
        for called in calls
    )


def _participant_has_lifecycle_build_hook(
    repository: Path, commit: str, scripts: list[str], build_script: str
) -> bool:
    try:
        package = json.loads(_read_git_file(repository, commit, "package.json"))
        package_scripts = package.get("scripts", {})
    except (subprocess.CalledProcessError, json.JSONDecodeError, AttributeError) as exc:
        raise IntegrityError("tracked package.json unavailable during lifecycle admission") from exc
    if not isinstance(package_scripts, dict):
        raise IntegrityError("tracked package.json scripts are invalid")
    lifecycle_hook = any(
        f"pre{script}" in package_scripts or f"post{script}" in package_scripts
        for script in scripts[1:]
    )
    return lifecycle_hook or any(
        _script_reaches_build(package_scripts, script, build_script) for script in scripts[1:]
    )


def _build_profile(
    repository: Path,
    commit: str,
    inputs: dict[str, Any],
    profile_files: tuple[str, ...],
    *,
    repository_name: str,
    workflow_sha: str,
    caller_workflow_sha: str,
    caller_workflow_path: str,
    caller_workflow_digest: str,
    runner_policy: str,
    runner_os: str,
    runner_arch: str,
    runner_image_os: str,
    runner_image_version: str,
    package_manager_version: str,
    build_script: str,
    build_entries: dict[str, dict[str, Any]],
) -> str:
    if not _REVISION.fullmatch(workflow_sha) or not _REVISION.fullmatch(caller_workflow_sha):
        raise IntegrityError("caller or reusable workflow revision is not immutable")
    blobs = git_profile_blobs(repository, commit, profile_files)
    try:
        package = json.loads(_read_git_file(repository, commit, "package.json"))
        package_scripts = package.get("scripts", {})
    except (json.JSONDecodeError, AttributeError) as exc:
        raise ArtifactError("tracked package.json is not a valid build-profile input") from exc
    if not isinstance(package_scripts, dict) or build_script not in package_scripts:
        raise ArtifactError("policy buildScript is not declared in tracked package.json")
    build_hooks = {
        name: package_scripts[name]
        for name in (f"pre{build_script}", build_script, f"post{build_script}")
        if name in package_scripts
    }
    build_inputs = {
        key: value
        for key, value in inputs.items()
        if key not in {"secretless-ci-script-plan", "secretless-ci-non-app-script-plan", "secretless-ci-non-app-paths"}
    }
    profile = {
        "repository": repository_name,
        "commit": commit,
        "callerWorkflowSha": caller_workflow_sha,
        "callerWorkflowPath": caller_workflow_path,
        "callerWorkflowDigest": caller_workflow_digest,
        "reusableWorkflowSha": workflow_sha,
        "runnerPolicy": runner_policy,
        "runnerOS": runner_os,
        "runnerArch": runner_arch,
        "runnerImage": {"os": runner_image_os, "version": runner_image_version},
        "buildScript": build_script,
        "buildEntries": build_entries,
        "buildHooks": build_hooks,
        "nodeVersion": inputs.get("node-version"),
        "packageManager": inputs.get("package-manager", "npm"),
        "packageManagerVersion": package_manager_version,
        "inputs": build_inputs,
        "profileFiles": blobs,
    }
    return sha256_bytes(canonical_json(profile))


def _preflight_fallback(
    reason: str,
    participants: list[str],
    aggregate_lanes: list[str],
) -> dict[str, Any]:
    return {
        "share-selected": False,
        "reason": reason,
        "participants": participants,
        "aggregate-lanes": aggregate_lanes,
    }


def _scheduled_participants(
    plan: dict[str, Any],
    participants: list[str],
    *,
    event_name: str,
    base_ref: str,
    ref_name: str,
    needs_results: dict[str, dict[str, str]],
) -> list[str]:
    levels = {level["id"]: level for level in plan["levels"]}
    return [
        lane
        for lane in participants
        if _event_branch_matches(levels[lane], event_name, base_ref, ref_name)
        and _needs_satisfied(levels[lane], needs_results)
    ]


def select_preflight(
    plan: dict[str, Any],
    policy_table: dict[str, Any],
    *,
    repository: str,
    private: bool,
    event_name: str,
    base_ref: str,
    ref_name: str,
    commit: str,
    caller_workflow_sha: str,
    caller_workflow_ref: str,
    caller_base_sha: str,
    workflow_sha: str,
    runner_policy: str,
    runner_os: str,
    runner_arch: str,
    runner_image_os: str,
    runner_image_version: str,
    package_manager_version: str,
    needs_results: dict[str, Any],
    checkout: Path,
    mode_share_eligible: bool = True,
    run_attempt: str = "1",
) -> dict[str, Any]:
    if not isinstance(plan, dict) or set(plan) != {"version", "pushBranches", "secrets", "levels", "buildArtifact"}:
        raise IntegrityError("typed buildArtifact plan is malformed")
    if len(canonical_json(plan)) > MAX_PLAN_BYTES:
        raise IntegrityError("typed buildArtifact plan exceeds size limit")
    if plan.get("version") != 1 or plan.get("secrets") != ["NODE_AUTH_TOKEN"]:
        raise IntegrityError("buildArtifact requires the exact acquisition-only NODE_AUTH_TOKEN mapping")
    if not run_attempt.isdigit() or int(run_attempt) < 1:
        raise IntegrityError("GitHub run attempt is invalid")
    levels = plan.get("levels")
    if not isinstance(levels, list) or not levels or len(levels) > 16:
        raise IntegrityError("typed buildArtifact level list is invalid")
    level_map = {item.get("id"): item for item in levels if isinstance(item, dict)}
    if len(level_map) != len(levels) or None in level_map:
        raise IntegrityError("typed buildArtifact levels contain duplicate or invalid IDs")
    artifact = plan.get("buildArtifact")
    if not isinstance(artifact, dict) or set(artifact) != {"producer", "consumers", "paths"}:
        raise IntegrityError("typed buildArtifact request is malformed")
    producer_id = artifact.get("producer")
    consumers = artifact.get("consumers")
    if not isinstance(producer_id, str) or not isinstance(consumers, list) or not consumers:
        raise IntegrityError("typed buildArtifact participants are invalid")
    participants = [producer_id, *consumers]
    if len(set(participants)) != len(participants) or any(item not in level_map for item in participants):
        raise IntegrityError("typed buildArtifact participants do not match declared levels")
    needs_results = _validate_prerequisite_results(plan, needs_results)
    aggregate_lanes = _scheduled_participants(
        plan,
        participants,
        event_name=event_name,
        base_ref=base_ref,
        ref_name=ref_name,
        needs_results=needs_results,
    )
    if run_attempt != "1":
        return {
            "share-selected": False,
            "reason": "retry-per-lane",
            "participants": participants,
            "aggregate-lanes": aggregate_lanes,
            "needs-results": json.dumps(needs_results, separators=(",", ":")),
        }

    def fallback(reason: str) -> dict[str, Any]:
        return _preflight_fallback(reason, participants, aggregate_lanes)

    if not mode_share_eligible:
        return fallback("mode-gate-share-ineligible")
    row = _policy_row(policy_table, repository)
    if row is None:
        return fallback("policy-row-missing-or-invalid")
    if not private or not row["audienceApproved"]:
        return fallback("audience-not-approved")
    caller_workflow_path = _caller_workflow_path_from_ref(repository, caller_workflow_ref)
    if caller_workflow_path is None or caller_workflow_path != row["callerWorkflowPath"]:
        return fallback("caller-workflow-path-mismatch")
    if not _caller_workflow_matches_policy(
        repository=checkout,
        workflow_commit=caller_workflow_sha,
        workflow_path=caller_workflow_path,
        workflow_digest=row["callerWorkflowDigest"],
    ):
        return fallback("caller-workflow-digest-mismatch")
    if not _REVISION.fullmatch(commit):
        raise IntegrityError("caller commit is not an immutable full object ID")
    if event_name == "pull_request" and not _pull_request_workflow_graph_unchanged(
        checkout, caller_base_sha, commit
    ):
        return fallback("caller-workflow-changed-in-pull-request")
    if row["producer"] != producer_id or row["consumers"] != consumers:
        return fallback("participant-set-mismatch")
    try:
        runner_lanes = json.loads(runner_policy)
    except json.JSONDecodeError:
        runner_lanes = [runner_policy]
    if runner_lanes != ["ubuntu-24.04"]:
        return fallback("runner-policy-not-admitted")
    if not runner_image_os or not runner_image_version:
        return fallback("runner-image-identity-unavailable")
    requested = normalize_roots(artifact.get("paths"), "buildArtifact.paths")
    if requested is None or _roots_overlap(requested) or not requested_roots_allowed(requested, row["allowedPaths"], row["requiredPaths"]):
        return fallback("export-roots-not-authorized")
    if any("/" in root for root in requested):
        return fallback("nested-export-roots-not-admitted")
    if not _EXACT_PACKAGE_MANAGER_VERSION.fullmatch(package_manager_version):
        raise IntegrityError("trusted package-manager version is not exact")
    try:
        if _tracked_npmrc_selects_workspaces(checkout, commit):
            return fallback("npm-workspace-config-not-admitted")
    except subprocess.CalledProcessError as exc:
        raise IntegrityError("tracked npm configuration inventory is unavailable") from exc
    matching_levels = [level for level in levels if _event_branch_matches(level, event_name, base_ref, ref_name)]
    if not matching_levels:
        return fallback("no-level-matches-event-branch")
    if aggregate_lanes != participants:
        return fallback("participant-event-branch-or-prerequisite-mismatch")
    build_script = row["buildScript"]
    prepared: dict[str, dict[str, str]] = {}
    prepared_build_entries: dict[str, dict[str, dict[str, Any]]] = {}
    profiles: dict[str, str] = {}
    for lane in participants:
        level = level_map[lane]
        level_inputs = level.get("inputs", {})
        if (
            not isinstance(level_inputs, dict)
            or not isinstance(level_inputs.get("node-version"), str)
            or not _EXACT_NODE_VERSION.fullmatch(level_inputs["node-version"])
        ):
            return fallback("node-version-not-exact")
        if level_inputs.get("package-manager", "npm") != "npm":
            return fallback("package-manager-not-admitted")
        if not _event_branch_matches(level, event_name, base_ref, ref_name):
            return fallback("participant-event-branch-mismatch")
        parsed = _participant_plans(level, build_script)
        if parsed is None:
            return fallback("participant-plan-ineligible")
        consumer_plans, scripts, build_entries = parsed
        if any(not json.loads(consumer_plans[variant]) for variant in ("full", "nonapp")):
            return fallback("participant-plan-empty-after-build")
        prepared_build_entries[lane] = build_entries
        if _participant_has_lifecycle_build_hook(checkout, commit, scripts, build_script):
            return fallback("participant-lifecycle-build-hook")
        prepared[lane] = consumer_plans
        try:
            profiles[lane] = _build_profile(
                checkout,
                commit,
                level.get("inputs", {}),
                row["profileFiles"],
                repository_name=repository,
                workflow_sha=workflow_sha,
                caller_workflow_sha=caller_workflow_sha,
                caller_workflow_path=row["callerWorkflowPath"],
                caller_workflow_digest=row["callerWorkflowDigest"],
                runner_policy=runner_policy,
                runner_os=runner_os,
                runner_arch=runner_arch,
                runner_image_os=runner_image_os,
                runner_image_version=runner_image_version,
                package_manager_version=package_manager_version,
                build_script=build_script,
                build_entries=build_entries,
            )
        except IntegrityError:
            raise
        except ArtifactError as exc:
            return fallback(f"profile-ineligible:{exc}")
    build_semantics = {canonical_json(entries["full"]) for entries in prepared_build_entries.values()}
    if len(build_semantics) != 1:
        return fallback("build-entry-mismatch")
    if len(set(profiles.values())) != 1:
        return fallback("profile-mismatch")
    producer_entries = prepared_build_entries[producer_id]
    producer_plan: dict[str, str] = {}
    for variant, entry in producer_entries.items():
        producer_plan[variant] = json.dumps([entry], separators=(",", ":"))
    return {
        "share-selected": True,
        "reason": "selected",
        "participants": participants,
        "aggregate-lanes": aggregate_lanes,
        "profile": next(iter(profiles.values())),
        "runner-image": json.dumps({"os": runner_image_os, "version": runner_image_version}, separators=(",", ":")),
        "package-manager-version": package_manager_version,
        "producer-plan": json.dumps(producer_plan, separators=(",", ":")),
        "consumer-plans": json.dumps(prepared, separators=(",", ":")),
        "policy-revision": workflow_sha,
        "export-paths": list(requested),
    }


def _open_workspace(path: Path) -> int:
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))


def _open_path(root_fd: int, path: str, *, final_directory: bool = False) -> int:
    parts = _path_parts(path)
    if parts is None:
        raise ArtifactError("artifact path is not normalized")
    current = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
            if not final or final_directory:
                flags |= os.O_DIRECTORY
            next_fd = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = next_fd
        result = current
        current = -1
        return result
    finally:
        if current >= 0:
            os.close(current)


def _list_files(root_fd: int, root: str) -> list[str]:
    descriptor = _open_path(root_fd, root)
    metadata = os.fstat(descriptor)
    if stat.S_ISREG(metadata.st_mode):
        if metadata.st_nlink != 1:
            os.close(descriptor)
            raise ArtifactError("ArtifactSet rejects hard-linked files")
        os.close(descriptor)
        return [root]
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise ArtifactError("ArtifactSet rejects special files")
    found: list[str] = []
    stack = [(descriptor, root, 0)]
    entries_seen = 0
    while stack:
        directory_fd, prefix, depth = stack.pop()
        if depth > MAX_DEPTH:
            os.close(directory_fd)
            raise ArtifactError("ArtifactSet exceeds directory depth limit")
        try:
            names = []
            with os.scandir(directory_fd) as directory_entries:
                for entry in directory_entries:
                    entries_seen += 1
                    if entries_seen > MAX_FILES:
                        raise ArtifactError("ArtifactSet exceeds directory entry count limit")
                    names.append(entry.name)
            names.sort()
            for name in names:
                path = f"{prefix}/{name}"
                if _path_parts(path) is None:
                    raise ArtifactError("ArtifactSet contains an invalid path")
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=directory_fd,
                )
                child_stat = os.fstat(child)
                if stat.S_ISDIR(child_stat.st_mode):
                    stack.append((child, path, depth + 1))
                elif stat.S_ISREG(child_stat.st_mode) and child_stat.st_nlink == 1:
                    found.append(path)
                    os.close(child)
                else:
                    os.close(child)
                    raise ArtifactError("ArtifactSet rejects links and special files")
                if len(found) + len(stack) > MAX_FILES:
                    raise ArtifactError("ArtifactSet exceeds file count limit")
        finally:
            os.close(directory_fd)
    return found


def _copy_trusted_file(root_fd: int, relative_path: str, destination: Path) -> dict[str, Any]:
    descriptor = _open_path(root_fd, relative_path)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE_BYTES:
            raise ArtifactError("ArtifactSet contains a link, special file, or oversized file")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, stat.S_IMODE(before.st_mode) & 0o777)
        digest = hashlib.sha256()
        total = 0
        try:
            while True:
                data = os.read(descriptor, 1024 * 1024)
                if not data:
                    break
                total += len(data)
                if total > MAX_FILE_BYTES:
                    raise ArtifactError("ArtifactSet file exceeds per-file size limit")
                digest.update(data)
                view = memoryview(data)
                while view:
                    written = os.write(output_fd, view)
                    view = view[written:]
            os.fsync(output_fd)
        finally:
            os.close(output_fd)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ) or total != before.st_size:
            raise IntegrityError("ArtifactSet source changed during snapshot")
        if before.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise ArtifactError("ArtifactSet rejects setuid and setgid output files")
        return {"path": relative_path, "mode": stat.S_IMODE(before.st_mode) & 0o777, "size": total, "sha256": digest.hexdigest()}
    finally:
        os.close(descriptor)


def export_artifact(
    workspace: Path,
    output_file: Path,
    *,
    roots: tuple[str, ...],
    identity: dict[str, Any],
    consumers: int,
) -> dict[str, Any]:
    normalized = normalize_roots(list(roots), "export paths")
    if normalized is None or not 1 <= consumers <= 9:
        raise ArtifactError("export requires normalized roots and bounded participant count")
    root_fd = _open_workspace(workspace)
    seen: set[str] = set()
    total = 0
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists() or output_file.is_symlink():
        raise ArtifactError("reserved ArtifactSet output path already exists")
    with tempfile.TemporaryDirectory(prefix="ci-artifact-export-") as temporary:
        stage = Path(temporary)
        payload = stage / "payload"
        payload.mkdir(mode=0o700)
        manifest_files: list[dict[str, Any]] = []
        try:
            for root in normalized:
                root_files = _list_files(root_fd, root)
                if not root_files:
                    raise ArtifactError(f"ArtifactSet export root is empty: {root}")
                for path in root_files:
                    if path in seen:
                        raise ArtifactError("ArtifactSet roots contain duplicate files")
                    seen.add(path)
                    entry = _copy_trusted_file(root_fd, path, payload.joinpath(*path.split("/")))
                    total += entry["size"]
                    if total > MAX_EXPANDED_BYTES or total * consumers > MAX_EXPANDED_BYTES:
                        raise ArtifactError("aggregate expanded ArtifactSet output exceeds 1 GiB")
                    if len(seen) > MAX_FILES:
                        raise ArtifactError("ArtifactSet exceeds file count limit")
                    manifest_files.append(entry)
        finally:
            os.close(root_fd)
        if not manifest_files:
            raise ArtifactError("ArtifactSet contains no regular files")
        manifest = dict(identity)
        manifest.update({"version": 1, "exportPolicy": {"paths": list(normalized)}, "files": sorted(manifest_files, key=lambda item: item["path"])})
        if set(manifest) != _MANIFEST_FIELDS:
            raise IntegrityError("ArtifactSet manifest identity is incomplete or contains extra fields")
        manifest_bytes = canonical_json(manifest)
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ArtifactError("ArtifactSet manifest exceeds size limit")
        try:
            with tarfile.open(output_file, mode="w:gz", compresslevel=6) as archive:
                info = tarfile.TarInfo("manifest.json")
                info.size = len(manifest_bytes)
                info.mode = 0o400
                info.mtime = 0
                archive.addfile(info, io.BytesIO(manifest_bytes))
                for entry in manifest["files"]:
                    source = payload.joinpath(*entry["path"].split("/"))
                    info = tarfile.TarInfo(f"payload/{entry['path']}")
                    info.size = entry["size"]
                    info.mode = entry["mode"] & 0o777
                    info.mtime = 0
                    with source.open("rb") as stream:
                        archive.addfile(info, stream)
        except (tarfile.TarError, ValueError) as exc:
            output_file.unlink(missing_ok=True)
            raise ArtifactError("ArtifactSet path or tar header exceeds supported capacity") from exc
    compressed_size = output_file.stat().st_size
    if compressed_size > MAX_ARTIFACT_BYTES:
        output_file.unlink(missing_ok=True)
        raise ArtifactError("compressed ArtifactSet exceeds 512 MiB")
    return {"bytes": compressed_size, "expanded": total, "manifest": manifest}


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_and_extract(
    artifact_file: Path,
    destination: Path,
    *,
    expected_digest: str,
    expected_identity: dict[str, Any],
    artifact_id: str,
    consumers: int,
) -> dict[str, Any]:
    if not _SHA256.fullmatch(expected_digest) or not artifact_id.isdigit():
        raise IntegrityError("same-run ArtifactSet identity is invalid")
    if artifact_file.is_symlink() or not artifact_file.is_file() or artifact_file.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ArtifactError("downloaded ArtifactSet is missing or oversized")
    digest = _file_digest(artifact_file)
    if digest != expected_digest:
        raise IntegrityError("downloaded ArtifactSet digest does not match upload output")
    if type(consumers) is not int or not 1 <= consumers <= MAX_PARTICIPANTS:
        raise IntegrityError("ArtifactSet consumer count is invalid")
    if destination.exists() or destination.is_symlink():
        raise ArtifactError("ArtifactSet extraction destination already exists")
    destination.mkdir(parents=True, mode=0o700)
    seen: set[str] = set()
    actual_files: list[dict[str, Any]] = []
    expanded = 0
    manifest = None
    try:
        with _BoundedTarFile.open(artifact_file, mode="r|gz", tarinfo=_BoundedTarInfo) as archive:
            member_count = 0
            for member in archive:
                member_count += 1
                if member_count > MAX_FILES + 1:
                    raise ArtifactError("ArtifactSet archive exceeds member count limit")
                if member.name in seen:
                    raise ArtifactError("ArtifactSet archive has duplicate members")
                seen.add(member.name)
                if member.sparse is not None:
                    raise ArtifactError("ArtifactSet archive contains a sparse member")
                if member.name == "manifest.json":
                    if member_count != 1 or not member.isfile() or member.size > MAX_MANIFEST_BYTES:
                        raise ArtifactError("ArtifactSet manifest member is invalid")
                    source = archive.extractfile(member)
                    if source is None:
                        raise ArtifactError("ArtifactSet manifest cannot be read")
                    raw_manifest = source.read(MAX_MANIFEST_BYTES + 1)
                    if len(raw_manifest) > MAX_MANIFEST_BYTES:
                        raise ArtifactError("ArtifactSet manifest exceeds size limit")
                    try:
                        manifest = json.loads(raw_manifest)
                    except (json.JSONDecodeError, RecursionError) as exc:
                        raise IntegrityError("ArtifactSet manifest is invalid JSON") from exc
                    continue
                if manifest is None:
                    raise ArtifactError("ArtifactSet manifest must precede payload members")
                if not member.isfile() or member.issym() or member.islnk() or member.isdev() or member.isdir():
                    raise ArtifactError("ArtifactSet archive contains a link or special member")
                if not member.name.startswith("payload/"):
                    raise ArtifactError("ArtifactSet archive contains an unexpected member")
                relative = member.name[len("payload/"):]
                parts = _path_parts(relative)
                if parts is None:
                    raise ArtifactError("ArtifactSet archive path is invalid or too deep")
                if type(member.size) is not int or member.size < 0 or member.size > MAX_FILE_BYTES:
                    raise ArtifactError("ArtifactSet archive exceeds per-file size limit")
                if member.mode & ~0o777:
                    raise ArtifactError("ArtifactSet archive has special file mode bits")
                expanded += member.size
                if expanded > MAX_EXPANDED_BYTES or expanded * consumers > MAX_EXPANDED_BYTES:
                    raise ArtifactError("aggregate expanded ArtifactSet output exceeds 1 GiB")
                source = archive.extractfile(member)
                if source is None:
                    raise ArtifactError("ArtifactSet payload cannot be read")
                path = destination.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                mode = member.mode & 0o777
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    mode & 0o555,
                )
                file_digest = hashlib.sha256()
                remaining = member.size
                try:
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise IntegrityError("ArtifactSet member size changed during extraction")
                        file_digest.update(chunk)
                        view = memoryview(chunk)
                        while view:
                            written = os.write(descriptor, view)
                            if written <= 0:
                                raise OSError("short write while extracting ArtifactSet")
                            view = view[written:]
                        remaining -= len(chunk)
                    if source.read(1):
                        raise IntegrityError("ArtifactSet member exceeds declared size")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                actual_files.append({
                    "path": relative,
                    "mode": mode,
                    "size": member.size,
                    "sha256": file_digest.hexdigest(),
                })
        if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS:
            raise IntegrityError("ArtifactSet manifest is missing or malformed")
        if any(manifest.get(key) != value for key, value in expected_identity.items()):
            raise IntegrityError("ArtifactSet manifest identity does not match this run")
        expected_files = manifest.get("files")
        if (
            not isinstance(expected_files, list)
            or any(not isinstance(entry, dict) for entry in expected_files)
            or sorted(expected_files, key=lambda item: item.get("path", ""))
            != sorted(actual_files, key=lambda item: item["path"])
        ):
            raise IntegrityError("ArtifactSet file manifest does not match archive bytes")
        for current, directories, _files in os.walk(destination, topdown=False):
            for directory in directories:
                os.chmod(Path(current) / directory, 0o500)
        os.chmod(destination, 0o500)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {"manifest": manifest, "digest": digest, "artifactId": artifact_id}


def create_receipt(
    *,
    identity: dict[str, Any],
    lane: str,
    profile: str,
    artifact_id: str | None,
    artifact_digest: str | None,
    verified_manifest: dict[str, Any] | None,
    command_digest: str,
) -> tuple[str, str]:
    if not _ID.fullmatch(lane) or not isinstance(profile, str) or len(profile) > 64:
        raise IntegrityError("receipt participant identity is invalid")
    if not _SHA256.fullmatch(command_digest):
        raise IntegrityError("receipt command digest is invalid")
    if verified_manifest is None:
        if artifact_id is not None or artifact_digest is not None:
            raise IntegrityError("fallback receipt cannot claim ArtifactSet identity")
        manifest_digest = None
        file_count = 0
        total_bytes = 0
    else:
        if (
            artifact_id is None
            or not artifact_id.isdigit()
            or artifact_digest is None
            or not _SHA256.fullmatch(artifact_digest)
        ):
            raise IntegrityError("receipt ArtifactSet identity is invalid")
        files = verified_manifest.get("files")
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise IntegrityError("verified ArtifactSet manifest has invalid bounded file list")
        total_bytes = 0
        file_paths: set[str] = set()
        for entry in files:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"path", "mode", "size", "sha256"}
                or _path_parts(entry.get("path")) is None
                or not isinstance(entry.get("mode"), int)
                or entry["mode"] & ~0o777
                or not isinstance(entry.get("size"), int)
                or entry["size"] < 0
                or not isinstance(entry.get("sha256"), str)
                or not _SHA256.fullmatch(entry["sha256"])
            ):
                raise IntegrityError("verified ArtifactSet file evidence is malformed")
            if entry["path"] in file_paths:
                raise IntegrityError("verified ArtifactSet contains duplicate file evidence")
            file_paths.add(entry["path"])
            total_bytes += entry["size"]
        if total_bytes > MAX_EXPANDED_BYTES:
            raise IntegrityError("verified ArtifactSet exceeds the expanded-byte limit")
        manifest_digest = sha256_bytes(canonical_json(verified_manifest))
        file_count = len(files)
    encoded = canonical_json({
        "version": 1,
        "identity": identity,
        "lane": lane,
        "profile": profile,
        "artifactSetId": artifact_id,
        "artifactSetDigest": artifact_digest,
        "commandsDigest": command_digest,
        "manifestDigest": manifest_digest,
        "fileCount": file_count,
        "totalBytes": total_bytes,
    }).decode("utf-8")
    if len(encoded.encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise ArtifactError("TestReceipt exceeds its bounded output size")
    return encoded, sha256_bytes(encoded.encode("utf-8"))


def receipt_identity_from_environment() -> dict[str, str]:
    try:
        values = {
            "repository": os.environ["GITHUB_REPOSITORY"],
            "ref": os.environ["GITHUB_REF"],
            "commit": os.environ["CALLER_COMMIT"],
            "callerWorkflowSha": os.environ["CALLER_WORKFLOW_SHA"],
            "reusableWorkflowSha": os.environ["REUSABLE_WORKFLOW_SHA"],
            "runId": os.environ["GITHUB_RUN_ID"],
            "runAttempt": os.environ["GITHUB_RUN_ATTEMPT"],
        }
    except KeyError as exc:
        raise IntegrityError("TestReceipt run identity is incomplete") from exc
    if (
        not _REVISION.fullmatch(values["commit"])
        or not _REVISION.fullmatch(values["callerWorkflowSha"])
        or not _REVISION.fullmatch(values["reusableWorkflowSha"])
        or not values["runId"].isdigit()
        or not values["runAttempt"].isdigit()
    ):
        raise IntegrityError("TestReceipt run identity is malformed")
    return values


def identity_from_environment(*, include_lane: bool = False) -> dict[str, Any]:
    try:
        commands = json.loads(os.environ["BUILD_ARTIFACT_COMMANDS_JSON"])
        export_paths = json.loads(os.environ["BUILD_ARTIFACT_PATHS_JSON"])
        runner_image = json.loads(os.environ["BUILD_ARTIFACT_RUNNER_IMAGE_JSON"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise IntegrityError("ArtifactSet command, export, or runner identity is invalid JSON") from exc
    identity = {
        "repository": os.environ["GITHUB_REPOSITORY"],
        "ref": os.environ["GITHUB_REF"],
        "commit": os.environ["CALLER_COMMIT"],
        "callerWorkflowSha": os.environ["CALLER_WORKFLOW_SHA"],
        "reusableWorkflowSha": os.environ["REUSABLE_WORKFLOW_SHA"],
        "runId": os.environ["GITHUB_RUN_ID"],
        "runAttempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "profile": os.environ["BUILD_ARTIFACT_PROFILE"],
        "commands": commands,
        "inputs": {"profileDigest": os.environ["BUILD_ARTIFACT_PROFILE"]},
        "toolchain": {
            "nodeVersion": os.environ["BUILD_ARTIFACT_NODE_VERSION"],
            "packageManager": os.environ.get("BUILD_ARTIFACT_PACKAGE_MANAGER", "npm"),
        "packageManagerVersion": os.environ["BUILD_ARTIFACT_PACKAGE_MANAGER_VERSION"],
            "runnerOS": os.environ["RUNNER_OS"],
            "runnerArch": os.environ["RUNNER_ARCH"],
            "runnerImage": runner_image,
        },
        "exportPolicy": {"paths": export_paths},
    }
    if include_lane:
        identity["lane"] = os.environ["BUILD_ARTIFACT_LEVEL_ID"]
    return identity


def _parse_exact_json_boolean(value: str | None, field: str) -> bool:
    if value not in {"true", "false"}:
        raise IntegrityError(f"aggregate {field} is missing or not an exact JSON boolean")
    return value == "true"


def verify_aggregate(
    *,
    share_selected: bool,
    policy_selected: bool,
    mode_gate_result: str,
    preflight_result: str,
    producer_result: str,
    upload_result: str,
    artifact_id: str,
    artifact_digest: str,
    expected_lanes: list[str],
    receipts: dict[str, Any],
    expected_profile: str,
    identity: dict[str, Any],
    attempt: str,
) -> None:
    if mode_gate_result != "success" or preflight_result != "success":
        raise IntegrityError("artifact mode gate or preflight did not succeed")
    if not attempt.isdigit() or int(attempt) < 1:
        raise IntegrityError("aggregate run attempt is malformed")
    if policy_selected and attempt != "1":
        raise IntegrityError("ArtifactSet policy selection is disabled on retries")
    if policy_selected and producer_result != "success":
        raise IntegrityError("policy-selected ArtifactSet producer did not succeed")
    if policy_selected and upload_result != "success":
        raise IntegrityError("policy-selected ArtifactSet upload did not succeed")
    if policy_selected and not share_selected:
        raise IntegrityError("policy-selected ArtifactSet was not made available to consumers")
    if share_selected and not policy_selected:
        raise IntegrityError("ArtifactSet was shared without a trusted policy selection")
    if share_selected and (not artifact_id.isdigit() or not _SHA256.fullmatch(artifact_digest)):
        raise IntegrityError("selected ArtifactSet producer evidence is missing")
    if len(set(expected_lanes)) != len(expected_lanes) or set(receipts) != set(expected_lanes):
        raise IntegrityError("aggregate receipt lanes differ from trusted participants")
    if not expected_lanes:
        if policy_selected or share_selected:
            raise IntegrityError("selected ArtifactSet has no scheduled participant lanes")
        return
    verified_manifest_digest: str | None = None
    verified_file_count: int | None = None
    verified_total_bytes: int | None = None
    for lane in expected_lanes:
        result = receipts[lane]
        if not isinstance(result, dict) or result.get("result") != "success":
            raise IntegrityError(f"participant lane {lane} did not succeed")
        if not share_selected:
            continue
        encoded = result.get("receipt")
        receipt_id = result.get("receipt-id")
        receipt_digest = result.get("receipt-digest")
        if not isinstance(encoded, str) or not isinstance(receipt_id, str) or not isinstance(receipt_digest, str):
            raise IntegrityError(f"participant lane {lane} receipt evidence is incomplete")
        actual = sha256_bytes(encoded.encode("utf-8"))
        if actual != receipt_id or actual != receipt_digest:
            raise IntegrityError(f"participant lane {lane} receipt digest mismatch")
        try:
            receipt = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise IntegrityError(f"participant lane {lane} receipt is invalid JSON") from exc
        receipt_identity = receipt.get("identity") if isinstance(receipt, dict) else None
        receipt_attempt = receipt_identity.get("runAttempt") if isinstance(receipt_identity, dict) else None
        receipt_attempt_is_valid = (
            isinstance(receipt_attempt, str)
            and receipt_attempt.isdigit()
            and 1 <= int(receipt_attempt) <= int(attempt)
        )
        if (
            not isinstance(receipt, dict)
            or receipt.get("lane") != lane
            or receipt.get("profile") != expected_profile
            or not isinstance(receipt_identity, dict)
            or {key: value for key, value in receipt_identity.items() if key != "runAttempt"} != identity
            or not receipt_attempt_is_valid
            or receipt.get("commandsDigest") != result.get("command-digest")
        ):
            raise IntegrityError(f"participant lane {lane} receipt identity mismatch")
        receipt_artifact_id = receipt.get("artifactSetId")
        receipt_artifact_digest = receipt.get("artifactSetDigest")
        if share_selected:
            if (
                receipt_attempt != attempt
                or receipt_artifact_id != artifact_id
                or receipt_artifact_digest != artifact_digest
            ):
                raise IntegrityError(f"participant lane {lane} receipt ArtifactSet identity mismatch")
        elif receipt_attempt != "1" and (receipt_artifact_id is not None or receipt_artifact_digest is not None):
            raise IntegrityError(f"fallback lane {lane} cannot promote an ArtifactSet from this attempt")
        elif (receipt_artifact_id is None) != (receipt_artifact_digest is None):
            raise IntegrityError(f"participant lane {lane} has partial ArtifactSet identity")
        elif receipt_artifact_id is not None and (
            receipt_artifact_id != artifact_id or receipt_artifact_digest != artifact_digest
        ):
            raise IntegrityError(f"prior-attempt lane {lane} names a different ArtifactSet")
        manifest_digest = receipt.get("manifestDigest")
        file_count = receipt.get("fileCount")
        total_bytes = receipt.get("totalBytes")
        has_artifact = receipt_artifact_id is not None
        if has_artifact and (
            not isinstance(manifest_digest, str)
            or not _SHA256.fullmatch(manifest_digest)
            or not isinstance(file_count, int)
            or file_count < 0
            or file_count > MAX_FILES
            or not isinstance(total_bytes, int)
            or total_bytes < 0
            or total_bytes > MAX_EXPANDED_BYTES
        ):
            raise IntegrityError(f"participant lane {lane} receipt has invalid bounded manifest evidence")
        if not has_artifact and (manifest_digest is not None or file_count != 0 or total_bytes != 0):
            raise IntegrityError(f"fallback lane {lane} receipt contains ArtifactSet manifest evidence")
        if not has_artifact:
            continue
        if verified_manifest_digest is None:
            verified_manifest_digest = manifest_digest
            verified_file_count = file_count
            verified_total_bytes = total_bytes
        elif (manifest_digest, file_count, total_bytes) != (
            verified_manifest_digest,
            verified_file_count,
            verified_total_bytes,
        ):
            raise IntegrityError("participant receipts disagree about verified ArtifactSet bytes")


def receipts_from_needs(needs: Any, expected_lanes: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(needs, dict) or not isinstance(expected_lanes, list):
        raise IntegrityError("caller-owned needs receipt context is malformed")
    if len(canonical_json(needs)) > MAX_AGGREGATE_NEEDS_BYTES:
        raise IntegrityError("compact caller-owned needs projection exceeds its byte limit")
    receipts: dict[str, dict[str, Any]] = {}
    for lane in expected_lanes:
        if not isinstance(lane, str) or not _ID.fullmatch(lane):
            raise IntegrityError("trusted expected participant ID is malformed")
        job = needs.get(lane)
        if not isinstance(job, dict):
            raise IntegrityError(f"caller-owned needs output is missing lane {lane}")
        outputs = job.get("outputs", {})
        if not isinstance(outputs, dict):
            raise IntegrityError(f"caller-owned needs outputs are malformed for lane {lane}")
        receipts[lane] = {
            "result": job.get("result"),
            "receipt": outputs.get("build-artifact-receipt"),
            "receipt-id": outputs.get("build-artifact-receipt-id"),
            "receipt-digest": outputs.get("build-artifact-receipt-digest"),
            "command-digest": outputs.get("build-artifact-command-digest"),
        }
    return receipts


_SANDBOX_CAPABILITY_FIELDS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
_SANDBOX_APPARMOR_PROFILE = re.compile(r"(?:^|\s)bwrap//&unpriv_bwrap \(enforce\)(?:\s|$)")


def validate_shared_sandbox_process_state(
    status_text: str,
    apparmor_profile: str,
    *,
    require_no_new_privs: bool,
) -> None:
    fields = {
        name: value.strip()
        for line in status_text.splitlines()
        if ":" in line
        for name, value in (line.split(":", 1),)
    }
    capability_values = [fields.get(name) for name in _SANDBOX_CAPABILITY_FIELDS]
    if any(value is None for value in capability_values):
        raise IntegrityError("shared sandbox capability status is incomplete")
    try:
        if any(int(value, 16) != 0 for value in capability_values if value is not None):
            raise IntegrityError("shared sandbox payload retains capabilities")
    except ValueError as exc:
        raise IntegrityError("shared sandbox capability status is malformed") from exc
    if require_no_new_privs and fields.get("NoNewPrivs") != "1":
        raise IntegrityError("shared sandbox payload lacks no_new_privs")
    if not _SANDBOX_APPARMOR_PROFILE.search(apparmor_profile):
        raise IntegrityError("shared sandbox enforcing AppArmor child profile is missing")


def enforce_shared_sandbox_process_state() -> None:
    try:
        status_text = Path("/proc/self/status").read_text(encoding="utf-8")
        apparmor_profile = Path("/proc/self/attr/current").read_text(encoding="utf-8")
        validate_shared_sandbox_process_state(
            status_text,
            apparmor_profile,
            require_no_new_privs=False,
        )
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        prctl.restype = ctypes.c_int
        if prctl(38, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS failed")
        status_text = Path("/proc/self/status").read_text(encoding="utf-8")
        validate_shared_sandbox_process_state(
            status_text,
            apparmor_profile,
            require_no_new_privs=True,
        )
    except OSError as exc:
        raise IntegrityError("shared sandbox security state could not be verified") from exc


def network_probe() -> None:
    """Prove the shared sandbox has confined loopback and no external route."""
    enforce_shared_sandbox_process_state()
    if socket.if_nameindex() != [(1, "lo")]:
        raise IntegrityError("unexpected network interface in shared sandbox")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.settimeout(2)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        client = socket.create_connection(("127.0.0.1", port), timeout=2)
        accepted, _peer = listener.accept()
        client.close()
        accepted.close()
    finally:
        listener.close()
    external = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    external.settimeout(1)
    try:
        try:
            external.connect(("1.1.1.1", 443))
        except OSError as exc:
            if exc.errno != errno.ENETUNREACH:
                raise IntegrityError("external network probe did not prove a missing route") from exc
            return
        raise IntegrityError("external network egress succeeded in shared mode")
    finally:
        external.close()


def _write_output(name: str, value: str, output_path: str | None = None) -> None:
    target = Path(output_path or os.environ["GITHUB_OUTPUT"])
    delimiter = "ci_artifact_" + os.urandom(16).hex()
    with target.open("a", encoding="utf-8") as output:
        output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def _env_json(name: str) -> Any:
    try:
        return json.loads(os.environ[name])
    except (KeyError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"required workflow input {name} is invalid JSON") from exc


def _runtime_input_environment_name(name: str) -> str:
    return RUNTIME_INPUT_ENV_PREFIX + name.upper().replace("-", "_")


def effective_runtime_inputs(level: dict[str, Any], event_name: str) -> dict[str, Any]:
    inputs = level.get("inputs")
    if (
        not isinstance(inputs, dict)
        or any(name not in RUNTIME_INPUT_DEFAULTS for name in inputs)
        or not isinstance(level.get("id"), str)
        or type(level.get("failFast", True)) is not bool
    ):
        raise IntegrityError("authorization plan contains an unsupported runtime input")
    expected = dict(RUNTIME_INPUT_DEFAULTS)
    expected.update(inputs)
    expected["ci-level-id"] = level["id"]
    expected["ci-level-fail-fast"] = level.get("failFast", True)
    expected["secretless-pr"] = event_name == "pull_request"
    expected["secretless-trusted-ref"] = event_name in {"push", "workflow_dispatch"}
    return expected


def runtime_inputs_from_environment() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in RUNTIME_INPUT_DEFAULTS:
        env_name = _runtime_input_environment_name(name)
        try:
            result[name] = json.loads(os.environ[env_name])
        except (KeyError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"effective runtime input {name} is missing or malformed") from exc
    return result


def _validate_participant_count(participants: Any, requested_count: str) -> int:
    if (
        not isinstance(participants, list)
        or not participants
        or any(not isinstance(participant, str) or not _ID.fullmatch(participant) for participant in participants)
        or len(set(participants)) != len(participants)
        or not isinstance(requested_count, str)
        or re.fullmatch(r"[0-9]+", requested_count) is None
    ):
        raise IntegrityError("trusted ArtifactSet participant count input is malformed")
    count = int(requested_count)
    if count != len(participants):
        raise IntegrityError("ArtifactSet participant count differs from trusted policy selection")
    return count


def _authorize_role_from_environment() -> None:
    role = os.environ.get("BUILD_ARTIFACT_ROLE", "")
    if role not in {"producer", "consumer"}:
        raise IntegrityError("only explicit producer and consumer roles may use an ArtifactSet")
    lane = os.environ.get("BUILD_ARTIFACT_LEVEL_ID", "")
    plan = _env_json("BUILD_ARTIFACT_AUTHORIZATION_PLAN_JSON")
    if not isinstance(plan, dict) or not isinstance(plan.get("levels"), list):
        raise IntegrityError("producer/consumer authorization plan is malformed")
    if plan.get("secrets") != ["NODE_AUTH_TOKEN"]:
        raise IntegrityError("producer/consumer authorization plan has an unsupported secret mapping")
    for level in plan["levels"]:
        if not isinstance(level, dict) or not isinstance(level.get("inputs"), dict):
            raise IntegrityError("producer/consumer authorization level inputs are malformed")
        if any(name.startswith("build-artifact-") for name in level["inputs"] if isinstance(name, str)):
            raise IntegrityError("producer/consumer authorization plan contains reserved internal inputs")
    mode_plan = [
        {"id": level.get("id"), "events": level.get("events"), "branches": level.get("branches")}
        for level in plan["levels"]
        if isinstance(level, dict)
    ]
    share_eligible = mode_gate(
        json.dumps(mode_plan, separators=(",", ":")),
        event_name=os.environ["GITHUB_EVENT_NAME"],
        base_ref=os.environ.get("GITHUB_BASE_REF", ""),
        ref_name=os.environ.get("GITHUB_REF_NAME", ""),
        repository=os.environ["GITHUB_REPOSITORY"],
        head_repository=os.environ.get("BUILD_ARTIFACT_HEAD_REPOSITORY", os.environ["GITHUB_REPOSITORY"]),
        secretless_pr=os.environ.get("SECRETLESS_PR", "").lower() == "true",
        secretless_trusted_ref=os.environ.get("SECRETLESS_TRUSTED_REF", "").lower() == "true",
    )
    if not share_eligible:
        raise IntegrityError("producer/consumer role is not share-eligible for this event")
    participant_level = next(
        (level for level in plan["levels"] if level.get("id") == lane),
        None,
    )
    if participant_level is None:
        raise IntegrityError("producer/consumer lane is absent from the authorization plan")
    if runtime_inputs_from_environment() != effective_runtime_inputs(
        participant_level, os.environ["GITHUB_EVENT_NAME"]
    ):
        raise IntegrityError("effective workflow inputs differ from the trusted participant plan")
    checkout = Path(os.environ["GITHUB_WORKSPACE"])
    expected_commit = os.environ["CALLER_COMMIT"]
    actual_commit = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    if actual_commit != expected_commit:
        raise IntegrityError("role checkout does not match the bound caller commit")
    actual_package_manager_version = subprocess.run(
        ["npm", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ).stdout.strip()
    if actual_package_manager_version != os.environ.get("BUILD_ARTIFACT_PACKAGE_MANAGER_VERSION", ""):
        raise IntegrityError("role package-manager version differs from trusted preflight")
    node_version = subprocess.run(
        ["node", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ).stdout.strip().removeprefix("v")
    policy = load_policy_table(Path(os.environ["BUILD_ARTIFACT_POLICY_FILE"]).read_bytes())
    decision = select_preflight(
        plan,
        policy,
        repository=os.environ["GITHUB_REPOSITORY"],
        private=os.environ.get("REPOSITORY_PRIVATE", "").lower() == "true",
        event_name=os.environ["GITHUB_EVENT_NAME"],
        base_ref=os.environ.get("GITHUB_BASE_REF", ""),
        ref_name=os.environ.get("GITHUB_REF_NAME", ""),
        commit=expected_commit,
        caller_workflow_sha=os.environ["CALLER_WORKFLOW_SHA"],
        caller_workflow_ref=os.environ.get("GITHUB_WORKFLOW_REF", ""),
        caller_base_sha=os.environ.get("CALLER_BASE_SHA", ""),
        workflow_sha=os.environ["REUSABLE_WORKFLOW_SHA"],
        runner_policy=os.environ["RUNNER_POLICY"],
        runner_os=os.environ["RUNNER_OS"],
        runner_arch=os.environ["RUNNER_ARCH"],
        runner_image_os=os.environ.get("ImageOS", ""),
        runner_image_version=os.environ.get("ImageVersion", ""),
        package_manager_version=actual_package_manager_version,
        needs_results=_env_json("BUILD_ARTIFACT_NEEDS_RESULTS"),
        checkout=checkout,
        run_attempt=os.environ["GITHUB_RUN_ATTEMPT"],
    )
    if decision.get("share-selected") is not True:
        raise IntegrityError(f"producer/consumer role lacks trusted policy selection: {decision.get('reason', 'unknown')}")
    _validate_participant_count(
        decision.get("participants"), os.environ.get("BUILD_ARTIFACT_PARTICIPANT_COUNT", "")
    )
    artifact = plan.get("buildArtifact")
    if not isinstance(artifact, dict):
        raise IntegrityError("producer/consumer role has no typed buildArtifact request")
    if not _role_matches_lane(role, lane, artifact):
        raise IntegrityError("producer/consumer role does not match the trusted participant lane")
    if os.environ.get("BUILD_ARTIFACT_PROFILE") != decision["profile"]:
        raise IntegrityError("role profile differs from trusted preflight")
    if _env_json("BUILD_ARTIFACT_PATHS_JSON") != decision["export-paths"]:
        raise IntegrityError("role export roots differ from trusted policy selection")
    if _env_json("BUILD_ARTIFACT_RUNNER_IMAGE_JSON") != json.loads(decision["runner-image"]):
        raise IntegrityError("role runner image differs from trusted preflight")
    selected_node_versions = {
        version.removeprefix("v")
        for level in plan["levels"]
        if isinstance(level, dict)
        and level.get("id") in {artifact.get("producer"), *artifact.get("consumers", [])}
        and isinstance(level.get("inputs"), dict)
        and isinstance((version := level["inputs"].get("node-version")), str)
    }
    if selected_node_versions != {node_version}:
        raise IntegrityError("role Node version differs from trusted participant profile")
    if role == "producer":
        if json.loads(os.environ["BUILD_ARTIFACT_PLAN_JSON"]) != json.loads(decision["producer-plan"]):
            raise IntegrityError("producer plan differs from trusted selection")
        if os.environ.get("BUILD_ARTIFACT_ID") or os.environ.get("BUILD_ARTIFACT_DIGEST"):
            raise IntegrityError("producer role cannot consume a preexisting ArtifactSet")
    else:
        consumer_plans = _env_json("BUILD_ARTIFACT_CONSUMER_PLANS")
        if consumer_plans != json.loads(decision["consumer-plans"]):
            raise IntegrityError("consumer plans differ from trusted selection")
        if not os.environ.get("BUILD_ARTIFACT_ID", "").isdigit() or not _SHA256.fullmatch(
            os.environ.get("BUILD_ARTIFACT_DIGEST", "")
        ):
            raise IntegrityError("consumer role lacks the exact same-run ArtifactSet identity")



def run_protected_candidate_plan() -> None:
    import hashlib
    import hashlib
    import json
    import os
    import re
    import shutil
    import signal
    import stat
    import subprocess
    import sys
    import time
    from pathlib import Path
    from urllib.parse import parse_qsl, unquote, urlsplit

    checkout_root = Path.cwd().resolve()
    normalized = []
    run_defaults = os.environ.get("SCRIPT_PLAN_DEFAULT_PLAN") == "true"
    env_pattern = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    entries_path = Path(os.environ["SCRIPT_PLAN_ENTRIES_FILE"])
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    if str(runner_temp) not in sys.path:
        sys.path.insert(0, str(runner_temp))
    import normalized_entries as normalized_script_plan

    if "run_script_plan_with_receipts" in globals():
        ci_build_artifacts = sys.modules[__name__]
    else:
        import ci_build_artifacts

    postgres_profile_name = os.environ.get("VERJSON_CI_POSTGRES_PROFILE", "")
    postgres_profile_module = None
    postgres_profile = None
    postgres_state_file = None
    postgres_profile_state = None
    if postgres_profile_name:
        import ci_postgres_sandbox as postgres_profile_module

        postgres_profile = postgres_profile_module.validate_profile_request(
            postgres_profile_name,
            db_image=os.environ.get("VERJSON_CI_LEGACY_DB_IMAGE", ""),
            cache_image=os.environ.get("VERJSON_CI_LEGACY_CACHE_IMAGE", ""),
            artifact_role=os.environ.get("BUILD_ARTIFACT_ROLE", ""),
            secretless_mode=os.environ.get("VERJSON_CI_POSTGRES_SECRETLESS_MODE") == "true",
        )
        postgres_state_file = Path(os.environ["VERJSON_CI_POSTGRES_SANDBOX_STATE"])

    host_signals = ci_build_artifacts.HostSignalQueue()
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    def stop_on_termination(signum, _frame):
        host_signals.capture(signum)
    for caught_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(caught_signal, stop_on_termination)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})
    if (
        entries_path.is_symlink()
        or entries_path.parent.resolve() != runner_temp
        or not entries_path.is_file()
    ):
        sys.exit("normalized script-plan entries file is unavailable")
    try:
        normalized_entries = json.loads(entries_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        sys.exit(f"normalized script-plan entries file is invalid: {error}")
    nested_source = os.environ.get("NESTED_MANIFESTS", "")
    try:
        validated_plan = normalized_script_plan.validate_normalized_entries(
            normalized_entries,
            nested_source,
        )
    except normalized_script_plan.ScriptPlanError as error:
        sys.exit(str(error))
    nested_manifests = validated_plan.nested_manifests
    nested_directories = []
    for nested in nested_manifests:
        nested_path = nested.path
        nested_directory = (checkout_root / nested_path).resolve()
        if not nested_directory.is_dir() or checkout_root not in nested_directory.parents:
            sys.exit(f"{nested_path} must resolve to a directory inside the checkout")
        nested_directories.append(nested_directory)

    for entry in validated_plan.entries:
        if entry.manifest_index is None:
            directory = checkout_root
            manifest_label = "package.json"
        else:
            directory = nested_directories[entry.manifest_index]
            manifest_label = f"{entry.manifest_path}/package.json"
        script = entry.script
        unset_env = list(entry.unset_env)
        requires_services = entry.requires_services
        package = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        scripts = package.get("scripts")
        if not isinstance(scripts, dict):
            sys.exit(f"{manifest_label} has no scripts object")
        if script not in scripts:
            sys.exit(f"planned npm script absent from {manifest_label}: {script}")
        normalized.append((directory, script, unset_env, requires_services))
    if run_defaults:
        root_scripts = json.loads(
            (checkout_root / "package.json").read_text(encoding="utf-8")
        ).get("scripts", {})
        if not isinstance(root_scripts, dict):
            sys.exit("package.json scripts must be an object")
        for name in ("build", "typecheck", "test", "lint"):
            if name in ("build", "test") or name in root_scripts:
                normalized.append((checkout_root, name, [], name == "test"))
    if any(requires_services for _directory, _name, _unset_env, requires_services in normalized):
        if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
            sys.exit(
                "candidate scripts requiring services need an isolated GitHub-hosted runner"
            )
    candidate_service_env = {}
    blocked_service_env = {
        "GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STATE",
        "GITHUB_STEP_SUMMARY", "VERJSON_CI_TRUSTED_DB_HOST",
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

    if any(requires_services for _directory, _name, _unset_env, requires_services in normalized):
        for service_name in ("DB_HOST", "DB_PORT", "CACHE_PORT"):
            if service_name in os.environ:
                service_value = os.environ[service_name]
                validate_candidate_service_value(service_name, service_value)
                candidate_service_env[service_name] = service_value
        for source_name in ("DB_ENV", "CACHE_ENV"):
            for service_line in os.environ.get(source_name, "").splitlines():
                stripped_line = service_line.strip()
                if not stripped_line or stripped_line.startswith("#"):
                    continue
                service_name, separator, _service_value = stripped_line.partition("=")
                if not separator or env_pattern.fullmatch(service_name) is None:
                    sys.exit("candidate service environment is malformed")
                normalized_service_name = service_name.upper()
                if normalized_service_name.startswith("POSTGRES_"):
                    continue
                if normalized_service_name in blocked_service_env or any(
                    normalized_service_name.startswith(prefix)
                    for prefix in blocked_service_prefixes
                ):
                    sys.exit("candidate service environment includes a protected variable")
                if service_name in os.environ:
                    service_value = os.environ[service_name]
                    validate_candidate_service_value(service_name, service_value)
                    candidate_service_env[service_name] = service_value
    # BEGIN source candidate script execution
    max_cache_files = 4096
    max_cache_bytes = 268435456
    sandbox_entrypoint = ci_build_artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT
    runner_temp_input = Path(os.environ["RUNNER_TEMP"])
    if (
        not runner_temp_input.is_absolute()
        or runner_temp_input.is_symlink()
        or not runner_temp_input.is_dir()
        or runner_temp_input.resolve() != runner_temp_input
    ):
        sys.exit("RUNNER_TEMP is not a canonical directory")
    runner_temp = runner_temp_input
    shared_artifact = os.environ.get("BUILD_ARTIFACT_ROLE") in ('producer', 'consumer')
    consume_artifact = os.environ.get("BUILD_ARTIFACT_ROLE") == "consumer"
    if shared_artifact:
        sys.path.insert(0, str(runner_temp))
        from ci_build_artifacts import artifact_mount_roots

    def verified_artifact_mounts():
        if not consume_artifact:
            return [], []
        artifact_root = runner_temp / "ci-build-artifact-verified"
        manifest_path = runner_temp / "ci-build-artifact-manifest.json"
        if artifact_root.is_symlink() or artifact_root.resolve() != artifact_root:
            sys.exit("verified ArtifactSet root is not canonical")
        root_metadata = artifact_root.stat(follow_symlinks=False)
        if not stat.S_ISDIR(root_metadata.st_mode) or root_metadata.st_mode & 0o222:
            sys.exit("verified ArtifactSet root is not a sealed directory")
        manifest_metadata = manifest_path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(manifest_metadata.st_mode)
            or manifest_path.is_symlink()
            or manifest_metadata.st_nlink != 1
            or manifest_metadata.st_size > 4 * 1024 * 1024
            or manifest_metadata.st_mode & 0o222
        ):
            sys.exit("verified ArtifactSet manifest is not a sealed regular file")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            roots = artifact_mount_roots(manifest["exportPolicy"]["paths"])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            sys.exit("verified ArtifactSet manifest is malformed")
        if not isinstance(roots, tuple) or not roots or len(roots) > 64:
            sys.exit("verified ArtifactSet export roots are malformed")
        root_fd = os.open(artifact_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        workspace_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        mount_args = []
        mount_fds = []
        try:
            for relative in roots:
                if (
                    not isinstance(relative, str)
                    or relative.startswith("/")
                    or "\\" in relative
                    or any(ord(char) < 32 or char in "*?[]{}" for char in relative)
                ):
                    sys.exit("verified ArtifactSet contains an unsafe mount path")
                parts = relative.split("/")
                if len(parts) > 32 or any(
                    part in {"", ".", ".."} or re.fullmatch(r"[A-Za-z0-9._-]+", part) is None
                    for part in parts
                ):
                    sys.exit("verified ArtifactSet contains an unsafe mount path")
                source_fd = os.dup(root_fd)
                for part in parts[:-1]:
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_fd)
                    os.close(source_fd)
                    source_fd = next_fd
                source_lstat = os.stat(parts[-1], dir_fd=source_fd, follow_symlinks=False)
                source_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if stat.S_ISDIR(source_lstat.st_mode):
                    source_flags |= os.O_DIRECTORY
                source_final_fd = os.open(parts[-1], source_flags, dir_fd=source_fd)
                os.close(source_fd)
                source_fd = source_final_fd
                source_metadata = os.fstat(source_fd)
                is_directory = stat.S_ISDIR(source_metadata.st_mode)
                if not is_directory and (
                    not stat.S_ISREG(source_metadata.st_mode)
                    or source_metadata.st_nlink != 1
                    or source_metadata.st_mode & 0o222
                ):
                    os.close(source_fd)
                    sys.exit("verified ArtifactSet mount source is unsafe")
                if is_directory and source_metadata.st_mode & 0o222:
                    os.close(source_fd)
                    sys.exit("verified ArtifactSet mount source is writable")
                parent_fd = os.dup(workspace_fd)
                try:
                    for part in parts[:-1]:
                        try:
                            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                        except FileNotFoundError:
                            os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                        os.close(parent_fd)
                        parent_fd = next_fd
                    try:
                        target_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
                    except FileNotFoundError:
                        if is_directory:
                            os.mkdir(parts[-1], mode=0o700, dir_fd=parent_fd)
                            target_fd = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                        else:
                            target_fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
                    target_metadata = os.fstat(target_fd)
                    compatible = stat.S_ISDIR(target_metadata.st_mode) if is_directory else stat.S_ISREG(target_metadata.st_mode)
                    os.close(target_fd)
                    if not compatible:
                        os.close(source_fd)
                        sys.exit("ArtifactSet mount target has an incompatible type")
                finally:
                    os.close(parent_fd)
                mount_args.extend(("--ro-bind", f"/proc/self/fd/{source_fd}", str(workspace.joinpath(*parts))))
                mount_fds.append(source_fd)
        finally:
            os.close(root_fd)
            os.close(workspace_fd)
        return mount_args, mount_fds

    corepack_default = Path.home() / ".cache/node/corepack"
    configured_corepack_home = os.environ.get("COREPACK_HOME")
    corepack_home_input = (
        Path(configured_corepack_home) if configured_corepack_home else corepack_default
    )
    corepack_home = None
    if corepack_home_input.exists() or corepack_home_input.is_symlink():
        if (
            not corepack_home_input.is_absolute()
            or corepack_home_input.is_symlink()
            or not corepack_home_input.is_dir()
            or corepack_home_input.resolve() != corepack_home_input
        ):
            sys.exit("Corepack cache is not a canonical directory")
        expected_corepack_home = corepack_default.resolve()
        if corepack_home_input != expected_corepack_home:
            sys.exit("Corepack cache is outside the runner's canonical cache path")
        corepack_home = corepack_home_input
    browser_cache = None
    browser_cache_value = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if browser_cache_value:
        expected_browser_cache = runner_temp / (
            "verjson-playwright-browsers-"
            + os.environ.get("GITHUB_RUN_ID", "")
            + "-"
            + os.environ.get("GITHUB_RUN_ATTEMPT", "")
        )
        browser_cache = Path(browser_cache_value)
        if (
            browser_cache != expected_browser_cache
            or browser_cache.is_symlink()
            or not browser_cache.is_dir()
            or browser_cache.resolve() != browser_cache
            or browser_cache.parent != runner_temp
        ):
            sys.exit("Playwright browser cache is not the validated job-scoped RUNNER_TEMP directory")
    changelog_ref = os.environ.get("VERJSON_CHANGELOG_CONTRACT_REF", "")
    changelog_sha256 = os.environ.get("VERJSON_CHANGELOG_CONTRACT_SHA256", "")
    if (
        re.fullmatch(r"[0-9a-f]{40}", changelog_ref) is None
        or re.fullmatch(r"[0-9a-f]{64}", changelog_sha256) is None
    ):
        sys.exit("pinned changelog contract identity is malformed")
    changelog_cache_root = Path(os.environ.get("VERJSON_CHANGELOG_TOOL_CACHE", ""))
    if (
        not changelog_cache_root.is_absolute()
        or changelog_cache_root.is_symlink()
        or not changelog_cache_root.is_dir()
        or changelog_cache_root.resolve() != changelog_cache_root
        or changelog_cache_root.parent != runner_temp
    ):
        sys.exit("verified changelog cache is not a canonical RUNNER_TEMP child")
    changelog_cache_root_metadata = changelog_cache_root.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(changelog_cache_root_metadata.st_mode)
        or changelog_cache_root_metadata.st_uid != os.getuid()
        or changelog_cache_root_metadata.st_mode & 0o022
    ):
        sys.exit("verified changelog cache root has unsafe ownership or mode")
    changelog_cache_entries = list(os.scandir(changelog_cache_root))
    if len(changelog_cache_entries) != 1 or changelog_cache_entries[0].name != changelog_ref:
        sys.exit("verified changelog cache contains unexpected entries")
    changelog_cache_dir = changelog_cache_root / changelog_ref
    changelog_cache_dir_metadata = changelog_cache_dir.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(changelog_cache_dir_metadata.st_mode)
        or changelog_cache_dir_metadata.st_uid != os.getuid()
        or changelog_cache_dir_metadata.st_mode & 0o022
    ):
        sys.exit("verified changelog cache entry has unsafe ownership or mode")
    changelog_cache_files = list(os.scandir(changelog_cache_dir))
    if len(changelog_cache_files) != 1 or changelog_cache_files[0].name != "changelog.py":
        sys.exit("verified changelog cache entry is incomplete")
    changelog_contract_path = changelog_cache_dir / "changelog.py"
    changelog_contract_metadata = changelog_contract_path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(changelog_contract_metadata.st_mode)
        or changelog_contract_metadata.st_uid != os.getuid()
        or changelog_contract_metadata.st_nlink != 1
        or changelog_contract_metadata.st_mode & 0o222
        or changelog_contract_metadata.st_size > 16777216
    ):
        sys.exit("verified changelog contract file has unsafe ownership or mode")
    try:
        changelog_contract_fd = os.open(
            changelog_contract_path,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        sys.exit("verified changelog contract changed during validation")
    opened_contract_metadata = os.fstat(changelog_contract_fd)
    if (
        opened_contract_metadata.st_dev != changelog_contract_metadata.st_dev
        or opened_contract_metadata.st_ino != changelog_contract_metadata.st_ino
        or opened_contract_metadata.st_uid != changelog_contract_metadata.st_uid
        or opened_contract_metadata.st_size != changelog_contract_metadata.st_size
    ):
        os.close(changelog_contract_fd)
        sys.exit("verified changelog contract changed during validation")
    changelog_digest = hashlib.sha256()
    with os.fdopen(changelog_contract_fd, "rb") as changelog_stream:
        for chunk in iter(lambda: changelog_stream.read(1024 * 1024), b""):
            changelog_digest.update(chunk)
    if changelog_digest.hexdigest() != changelog_sha256:
        sys.exit("verified changelog contract does not match its pinned SHA-256")
    changelog_cache_root_identity = (
        changelog_cache_root_metadata.st_dev,
        changelog_cache_root_metadata.st_ino,
        changelog_cache_root_metadata.st_uid,
        stat.S_IMODE(changelog_cache_root_metadata.st_mode),
    )
    baseline_value = os.environ.get("npm_config_cache", "").strip()
    baseline = None
    if baseline_value:
        candidate_baseline = Path(os.path.abspath(baseline_value))
        if candidate_baseline.exists() or candidate_baseline.is_symlink():
          baseline = candidate_baseline
          if not baseline.is_absolute() or baseline.is_symlink() or not baseline.is_dir():
            sys.exit("verified runtime cache is not an absolute regular directory")
          if baseline.resolve() != baseline:
            sys.exit("verified runtime cache path contains a symlink")
          try:
            baseline.resolve().relative_to(runner_temp)
          except ValueError:
            sys.exit("verified runtime cache escapes RUNNER_TEMP")

    bubblewrap = Path("/usr/bin/bwrap")
    try:
        bubblewrap_metadata = bubblewrap.stat(follow_symlinks=False)
    except OSError:
        sys.exit("verified bubblewrap namespace boundary is unavailable")
    if (
        not stat.S_ISREG(bubblewrap_metadata.st_mode)
        or bubblewrap_metadata.st_uid != 0
        or bubblewrap_metadata.st_mode & 0o022
    ):
        sys.exit("bubblewrap namespace boundary has unsafe ownership or mode")
    bubblewrap_version = subprocess.run(
        [str(bubblewrap), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    version_match = re.fullmatch(r"bubblewrap (\d+)\.(\d+)\.(\d+)\n?", bubblewrap_version)
    if version_match is None or tuple(map(int, version_match.groups())) < (0, 9, 0):
        sys.exit("bubblewrap namespace boundary is below version 0.9.0")

    def inventory(root):
        files = []
        total_bytes = 0
        pending = [root]
        while pending:
            directory = pending.pop()
            for entry in os.scandir(directory):
                entry_path = Path(entry.path)
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    sys.exit("verified runtime cache contains a symlink")
                if stat.S_ISDIR(mode):
                    pending.append(entry_path)
                    continue
                if not stat.S_ISREG(mode):
                    sys.exit("verified runtime cache contains a special file")
                try:
                    relative = entry_path.relative_to(root).as_posix()
                except ValueError:
                    sys.exit("verified runtime cache entry escapes its root")
                try:
                    descriptor = os.open(entry_path, os.O_RDONLY | os.O_NOFOLLOW)
                except OSError:
                    sys.exit("verified runtime cache file changed during validation")
                descriptor_stat = os.fstat(descriptor)
                if not stat.S_ISREG(descriptor_stat.st_mode):
                    os.close(descriptor)
                    sys.exit("verified runtime cache file changed type during validation")
                size = descriptor_stat.st_size
                total_bytes += size
                if len(files) + 1 > max_cache_files or total_bytes > max_cache_bytes:
                    sys.exit("verified runtime cache exceeds its file or byte bound")
                digest = hashlib.sha256()
                with os.fdopen(descriptor, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                files.append((relative, size, digest.hexdigest()))
        return tuple(sorted(files))

    baseline_inventory = inventory(baseline) if baseline is not None else ()
    cache_root = Path(os.environ["CANDIDATE_CACHE_ROOT"])
    if (
        not cache_root.is_absolute()
        or Path(os.path.abspath(cache_root)) != cache_root
        or cache_root.parent.resolve() != runner_temp
    ):
        sys.exit("candidate cache root escapes RUNNER_TEMP")
    if cache_root.exists() or cache_root.is_symlink():
        sys.exit("candidate cache root exists before script execution")
    workspace_lexical = Path(os.environ.get("PWD", ""))
    workspace = Path.cwd().resolve()
    if (
      not workspace_lexical.is_absolute()
      or workspace_lexical != workspace
      or workspace_lexical.resolve() != workspace_lexical
      or workspace_lexical.is_symlink()
      or not workspace.is_dir()
    ):
      sys.exit("candidate workspace is not a canonical directory")
    git_metadata = workspace / ".git"
    if (
        git_metadata.is_symlink()
        or not git_metadata.is_dir()
        or git_metadata.resolve() != git_metadata
    ):
        sys.exit("candidate workspace Git metadata is not a canonical directory")
    git_metadata_stat = git_metadata.stat(follow_symlinks=False)
    if not stat.S_ISDIR(git_metadata_stat.st_mode) or git_metadata_stat.st_uid != os.getuid():
        sys.exit("candidate workspace Git metadata has unexpected type or ownership")
    git_metadata_identity = (
        git_metadata_stat.st_dev,
        git_metadata_stat.st_ino,
        git_metadata_stat.st_uid,
        stat.S_IMODE(git_metadata_stat.st_mode),
    )
    if workspace == runner_temp or workspace in runner_temp.parents or runner_temp in workspace.parents:
        sys.exit("candidate workspace and RUNNER_TEMP overlap")
    if (baseline is not None and baseline.parent != runner_temp) or cache_root.parent != runner_temp:
        sys.exit("candidate cache roots are not exact RUNNER_TEMP children")
    if baseline is not None and (baseline == cache_root or baseline in cache_root.parents or cache_root in baseline.parents):
        sys.exit("candidate cache baseline and isolation root overlap")
    trusted_tool_root_input = Path(os.environ.get("RUNNER_TOOL_CACHE", ""))
    if (
        not trusted_tool_root_input.is_absolute()
        or trusted_tool_root_input.is_symlink()
        or not trusted_tool_root_input.is_dir()
        or trusted_tool_root_input.resolve() != trusted_tool_root_input
    ):
        sys.exit("trusted setup-node tool root unavailable or noncanonical")
    trusted_tool_root = trusted_tool_root_input
    hosted_tool_cache_root = Path("/opt/hostedtoolcache")
    # uid 0 is root; uid 1001 is the "runner" account GitHub-hosted
    # images provision and run Actions steps as.
    hosted_tool_cache_uids = (0, 1001)

    def paths_overlap(left, right):
      return left == right or left in right.parents or right in left.parents

    if paths_overlap(changelog_cache_root, cache_root) or (
        baseline is not None and paths_overlap(changelog_cache_root, baseline)
    ):
        sys.exit("verified changelog cache overlaps a candidate cache")

    def validate_trusted_ancestry(
      root,
      target,
      allowed_uids,
      label,
      include_target=True,
      require_unwritable=True,
    ):
      try:
        relative = target.relative_to(root)
      except ValueError:
        sys.exit(f"{label} escapes its trusted root")
      components = (root,)
      current = root
      for part in relative.parts:
        current = current / part
        components += (current,)
      if not include_target:
        components = components[:-1]
      for component in components:
        try:
          metadata = component.stat(follow_symlinks=False)
        except OSError:
          sys.exit(f"{label} ancestry is unreadable")
        if component != target or not include_target:
          if not stat.S_ISDIR(metadata.st_mode):
            sys.exit(f"{label} ancestry is not canonical directories")
        if metadata.st_uid not in allowed_uids or (
          require_unwritable and metadata.st_mode & 0o022
        ):
          sys.exit(f"{label} ancestry has unsafe ownership mode")

    candidate_controlled_roots = tuple(
        root for root in (workspace, runner_temp, baseline, cache_root, changelog_cache_root)
        if root is not None
    )
    if any(paths_overlap(trusted_tool_root, path) for path in candidate_controlled_roots):
        sys.exit("trusted setup-node tool root overlaps candidate-controlled paths")
    trusted_root_metadata = trusted_tool_root.stat(follow_symlinks=False)
    allow_hosted_tool_cache_root = (
      trusted_tool_root == hosted_tool_cache_root
      and stat.S_IMODE(trusted_root_metadata.st_mode) == 0o777
      and trusted_root_metadata.st_uid in hosted_tool_cache_uids
      and trusted_root_metadata.st_gid == 0
    )
    if (
      not stat.S_ISDIR(trusted_root_metadata.st_mode)
      or trusted_root_metadata.st_uid not in (
        hosted_tool_cache_uids if allow_hosted_tool_cache_root else (0,)
      )
      or (
        trusted_root_metadata.st_mode & 0o022
        and not allow_hosted_tool_cache_root
      )
    ):
      sys.exit("trusted setup-node tool root has unsafe ownership mode")
    trusted_tool_uids = hosted_tool_cache_uids if allow_hosted_tool_cache_root else (0,)
    # GitHub-hosted images provision every entry under /opt/hostedtoolcache
    # (directories, files, and symlinks alike) as uid 1001 mode 0777 -- the
    # same convention as the root, measured on ubuntu-latest for #1599 -- so
    # an unwritable requirement on any descendant can never pass there. The
    # trust anchor for that tree is the owner allowlist plus the fact that
    # nothing PR-controlled runs before this check (npm ci uses
    # --ignore-scripts): the write bit is unexploited capability, not
    # evidence of tampering. Every other tool root stays strict.
    trusted_tool_tree_requires_unwritable = not allow_hosted_tool_cache_root

    trusted_search_directories = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
      if not entry:
        continue
      lexical_directory = Path(entry)
      if not lexical_directory.is_absolute():
        continue
      try:
        lexical_directory.relative_to(trusted_tool_root)
      except ValueError:
        continue
      resolved_directory = lexical_directory.resolve()
      try:
        resolved_directory.relative_to(trusted_tool_root)
      except ValueError:
        sys.exit("setup-node PATH entry escapes trusted tool root")
      validate_trusted_ancestry(
        trusted_tool_root,
        lexical_directory,
        trusted_tool_uids,
        "setup-node lexical PATH",
        require_unwritable=trusted_tool_tree_requires_unwritable,
      )
      validate_trusted_ancestry(
        trusted_tool_root,
        resolved_directory,
        trusted_tool_uids,
        "setup-node resolved PATH",
        require_unwritable=trusted_tool_tree_requires_unwritable,
      )
      if resolved_directory not in trusted_search_directories:
        trusted_search_directories.append(resolved_directory)

    tool_executables = {}
    for tool_name in ("npm", "node", "pwsh"):
      candidates = []
      if tool_name == "pwsh":
        system_candidate = Path("/usr/bin/pwsh")
        if system_candidate.exists():
          resolved_system_candidate = system_candidate.resolve()
          microsoft_root = Path("/opt/microsoft/powershell")
          if (
            not resolved_system_candidate.is_relative_to(microsoft_root)
            or resolved_system_candidate.name != "pwsh"
            or not re.fullmatch(r"[0-9]+(?:[.][0-9]+)*", resolved_system_candidate.parent.name)
          ):
            sys.exit("trusted pwsh executable escapes Microsoft runtime contract")
          validate_trusted_ancestry(
            Path("/usr"), system_candidate, (0,), "pwsh lexical path", include_target=False
          )
          validate_trusted_ancestry(
            # GitHub-hosted runner images ship /opt root-owned but world-writable
            # (mode 777, matching /opt/hostedtoolcache's convention), so an
            # unwritable-ancestry requirement can never pass there. Root ownership
            # alone still holds: nothing PR-controlled runs outside this sandbox
            # before this check (npm ci uses --ignore-scripts), so the write bit is
            # unexploited capability, not evidence of tampering.
            Path("/opt"),
            resolved_system_candidate.parent,
            (0,),
            "pwsh resolved runtime",
            require_unwritable=False,
          )
          system_metadata = resolved_system_candidate.stat(follow_symlinks=False)
          if not stat.S_ISREG(system_metadata.st_mode) or system_metadata.st_uid != 0:
            sys.exit("trusted pwsh executable has unsafe ownership mode")
          candidates.append(resolved_system_candidate)
      for directory in trusted_search_directories:
        lexical_candidate = directory / tool_name
        if not lexical_candidate.exists():
          continue
        resolved_candidate = lexical_candidate.resolve()
        validate_trusted_ancestry(
          trusted_tool_root,
          lexical_candidate,
          trusted_tool_uids,
          f"trusted {tool_name} lexical path",
          include_target=False,
          require_unwritable=trusted_tool_tree_requires_unwritable,
        )
        try:
          resolved_candidate.relative_to(trusted_tool_root)
        except ValueError:
          sys.exit(f"trusted {tool_name} executable escapes setup-node tool root")
        validate_trusted_ancestry(
          trusted_tool_root,
          resolved_candidate,
          trusted_tool_uids,
          f"trusted {tool_name} resolved path",
          require_unwritable=trusted_tool_tree_requires_unwritable,
        )
        if resolved_candidate.is_file() and os.access(resolved_candidate, os.X_OK):
          candidates.append(resolved_candidate)
      candidates = list(dict.fromkeys(candidates))
      if not candidates:
        if tool_name in ("npm", "node"):
          sys.exit(f"trusted {tool_name} executable is unavailable")
        continue
      if len(candidates) != 1:
        sys.exit(f"trusted {tool_name} executable is ambiguous")
      tool_executable = candidates[0]
      executable_metadata = tool_executable.stat(follow_symlinks=False)
      # The Microsoft-shipped pwsh binary under GitHub-hosted /opt is root-owned
      # but world-writable (mode 777, matching /opt/hostedtoolcache's convention),
      # so the write-bit requirement below can never pass against a real,
      # untampered system pwsh there. Root ownership alone still holds for that
      # one location: nothing PR-controlled runs outside this sandbox before this
      # check (npm ci uses --ignore-scripts), so the write bit is unexploited
      # capability, not evidence of tampering. npm/node under an admitted hosted
      # tool cache carry the same image convention (see
      # trusted_tool_tree_requires_unwritable); every other root stays strict.
      executable_in_hosted_tool_cache = tool_executable.is_relative_to(hosted_tool_cache_root)
      executable_requires_unwritable = not (
        (tool_name == "pwsh" and tool_executable.is_relative_to("/opt/microsoft/powershell"))
        or (executable_in_hosted_tool_cache and not trusted_tool_tree_requires_unwritable)
      )
      executable_uids = trusted_tool_uids if executable_in_hosted_tool_cache else (0,)
      if executable_metadata.st_uid not in executable_uids or (
        executable_requires_unwritable and executable_metadata.st_mode & 0o022
      ):
        sys.exit(f"trusted {tool_name} executable has unsafe ownership mode")
      tool_executables[tool_name] = tool_executable
    npm_executable = tool_executables["npm"]
    tool_prefix_candidates = {
      (executable.parent if executable.is_relative_to("/opt/microsoft/powershell") else executable.parent.parent)
      for executable in tool_executables.values()
      if not executable.is_relative_to("/usr")
    }
    if corepack_home is not None:
      tool_prefix_candidates.add(corepack_home)
    tool_prefixes = []
    tool_prefix_identities = {}

    def validate_trusted_tool_tree(root, allowed_uids=(0,), require_unwritable=True):
      for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        directory_metadata = directory_path.stat(follow_symlinks=False)
        if (
          not stat.S_ISDIR(directory_metadata.st_mode)
          or directory_metadata.st_uid not in allowed_uids
          or (require_unwritable and directory_metadata.st_mode & 0o022)
        ):
          sys.exit("trusted tool tree directory has unsafe ownership mode")
        for name in (*directory_names, *file_names):
          entry = directory_path / name
          entry_metadata = entry.stat(follow_symlinks=False)
          if entry_metadata.st_uid not in allowed_uids:
            sys.exit("trusted tool tree entry has unapproved ownership")
          if stat.S_ISLNK(entry_metadata.st_mode):
            resolved_entry = entry.resolve()
            if resolved_entry.is_relative_to(root):
              continue
            if not resolved_entry.exists():
              # The Microsoft-shipped pwsh tree ships legacy OpenSSL 1.0
              # compatibility shims that name host library paths distros
              # dropped long ago (e.g. Ubuntu 24.04 carries no OpenSSL 1.0);
              # a dangling symlink cannot be read or executed by anything
              # that mounts this tree, so it carries no trust to verify.
              continue
            # The Microsoft-shipped pwsh tree symlinks its bundled OpenSSL 1.0
            # compatibility shims to the real system libraries under /usr on
            # distros that still carry them; re-validate the escape target
            # against /usr's own strict, unconditional trust instead of
            # blanket-rejecting every symlink that leaves the tool prefix.
            if resolved_entry.is_relative_to("/usr"):
              validate_trusted_ancestry(
                Path("/usr"),
                resolved_entry,
                (0,),
                "trusted tool tree external symlink target",
              )
              continue
            sys.exit("trusted tool tree symlink escapes mounted prefix")
          if require_unwritable and entry_metadata.st_mode & 0o022:
            sys.exit("trusted tool tree entry has unsafe writable mode")
          if not (stat.S_ISDIR(entry_metadata.st_mode) or stat.S_ISREG(entry_metadata.st_mode)):
            sys.exit("trusted tool tree contains special file")

    for tool_prefix in sorted(tool_prefix_candidates, key=lambda path: (len(path.parts), str(path))):
      if (
          tool_prefix == Path("/")
          or any(paths_overlap(tool_prefix, path) for path in candidate_controlled_roots)
      ):
        sys.exit("trusted tool prefix overlaps candidate-controlled paths")
      if tool_prefix.is_relative_to(trusted_tool_root):
        validate_trusted_ancestry(
          trusted_tool_root,
          tool_prefix,
          trusted_tool_uids,
          "trusted tool prefix",
          require_unwritable=trusted_tool_tree_requires_unwritable,
        )
      elif tool_prefix == corepack_home:
        validate_trusted_ancestry(
          Path.home(),
          tool_prefix,
          (os.getuid(),),
          "trusted Corepack cache",
          require_unwritable=True,
        )
      prefix_metadata = tool_prefix.stat(follow_symlinks=False)
      if not stat.S_ISDIR(prefix_metadata.st_mode):
        sys.exit("trusted tool prefix is not a directory")
      # See the pwsh discovery comment above: the Microsoft-shipped runtime tree
      # under GitHub-hosted /opt is root-owned but world-writable throughout, and
      # the admitted hosted tool cache is world-writable throughout by the same
      # image convention; only those prefixes relax the write-bit requirement.
      prefix_in_hosted_tool_cache = tool_prefix.is_relative_to(hosted_tool_cache_root)
      prefix_in_corepack_cache = tool_prefix == corepack_home
      validate_trusted_tool_tree(
        tool_prefix,
        allowed_uids=(
          (os.getuid(),) if prefix_in_corepack_cache
          else trusted_tool_uids if prefix_in_hosted_tool_cache
          else (0,)
        ),
        require_unwritable=not (
          tool_prefix.is_relative_to("/opt/microsoft/powershell")
          or (prefix_in_hosted_tool_cache and not trusted_tool_tree_requires_unwritable)
        ),
      )
      tool_prefix_identities[tool_prefix] = (
        prefix_metadata.st_dev,
        prefix_metadata.st_ino,
        prefix_metadata.st_uid,
        stat.S_IMODE(prefix_metadata.st_mode),
      )
      if not any(existing == tool_prefix or existing in tool_prefix.parents for existing in tool_prefixes):
        tool_prefixes.append(tool_prefix)
    npm_cli_candidates = set()
    for npm_cli_candidate in (
      npm_executable.parent.parent / "lib/node_modules/npm/bin/npm-cli.js",
      npm_executable.parent / "node_modules/npm/bin/npm-cli.js",
      npm_executable.resolve().parent.parent / "bin/npm-cli.js",
    ):
      if not npm_cli_candidate.is_file():
        continue
      if not any(npm_cli_candidate.is_relative_to(prefix) for prefix in tool_prefixes):
        sys.exit("trusted npm CLI is outside validated tool prefixes")
      resolved_npm_cli_candidate = npm_cli_candidate.resolve(strict=True)
      if not any(resolved_npm_cli_candidate.is_relative_to(prefix) for prefix in tool_prefixes):
        sys.exit("trusted npm CLI escapes validated tool prefixes")
      npm_cli_candidates.add(resolved_npm_cli_candidate)
    if len(npm_cli_candidates) > 1:
        sys.exit("trusted npm CLI is ambiguous")
    npm_cli_executable = next(iter(npm_cli_candidates), None)
    npm_command = (
      [str(tool_executables["node"]), str(npm_cli_executable)]
      if npm_cli_executable is not None
      else [str(npm_executable)]
    )
    cache_root.mkdir(mode=0o700)
    if os.environ.pop("VERJSON_CI_TEST_PENDING_HOST_SIGNAL", None) == "1":
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        os.kill(os.getpid(), signal.SIGTERM)

    def cleanup_cache_root(*_args):
        shutil.rmtree(cache_root, ignore_errors=True)

    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        def execute_candidate_entry(index, _entry):
            candidate_result = None
            candidate_failure = None
            script_directory, name, unset_env, requires_services = normalized[index]
            received_signal = host_signals.poll()
            if received_signal is not None:
                interruption = ScriptPlanInterrupted(received_signal)
                interruption.cleanup_status = "not-started"
                raise interruption
            if baseline is not None and inventory(baseline) != baseline_inventory:
                sys.exit("verified runtime cache changed before candidate script")
            script_cache = cache_root / str(index)
            script_cache.mkdir(mode=0o700)
            script_tmp = cache_root / f"tmp-{index}"
            script_home = cache_root / f"home-{index}"
            script_tmp.mkdir(mode=0o700)
            script_home.mkdir(mode=0o700)
            npm_user_config = script_tmp / "npm-userconfig"
            npm_global_config = script_tmp / "npm-globalconfig"
            for config_path in (npm_user_config, npm_global_config):
                config_descriptor = os.open(
                    config_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o400,
                )
                os.close(config_descriptor)
            if baseline is not None:
                for relative, _size, _digest in baseline_inventory:
                    source = baseline / relative
                    target = script_cache / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        source_descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
                    except OSError:
                        sys.exit("verified runtime cache changed during isolated copy")
                    if not stat.S_ISREG(os.fstat(source_descriptor).st_mode):
                        os.close(source_descriptor)
                        sys.exit("verified runtime cache copy source is not a regular file")
                    with (
                        os.fdopen(source_descriptor, "rb") as source_stream,
                        target.open("xb") as target_stream,
                    ):
                        shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
            if (baseline is not None and inventory(baseline) != baseline_inventory) or (
                inventory(script_cache) != baseline_inventory
            ):
                sys.exit("isolated candidate cache copy failed integrity verification")
            script_env = {
                "CI": "true",
                "GITHUB_ACTIONS": "true",
                "GITHUB_WORKSPACE": str(workspace),
                "RUNNER_TEMP": str(script_tmp),
                "HOME": str(script_home),
                "TMPDIR": str(script_tmp),
                "COREPACK_HOME": str(corepack_home) if corepack_home is not None else str(
                    script_home / ".cache/node/corepack"
                ),
                "COREPACK_ENABLE_NETWORK": "0",
                "NPM_CONFIG_CACHE": str(script_cache),
            "NPM_CONFIG_USERCONFIG": str(npm_user_config),
            "NPM_CONFIG_GLOBALCONFIG": str(npm_global_config),
                "npm_config_cache": str(script_cache),
            "npm_config_userconfig": str(npm_user_config),
            "npm_config_globalconfig": str(npm_global_config),
                "VERJSON_CHANGELOG_TOOL_CACHE": str(changelog_cache_root),
                "VERJSON_CHANGELOG_CONTRACT_REF": changelog_ref,
                "VERJSON_CHANGELOG_CONTRACT_SHA256": changelog_sha256,
            }
            for env_name in (
                "GITHUB_REF", "GITHUB_REF_NAME", "GITHUB_SHA", "GITHUB_REPOSITORY",
                "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
                "GITHUB_WORKFLOW", "GITHUB_EVENT_NAME", "RUNNER_OS", "RUNNER_ARCH",
            ):
                if env_name in os.environ:
                    script_env[env_name] = os.environ[env_name]
            if browser_cache is not None:
                script_env["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
            if requires_services:
                script_env.update(candidate_service_env)
            tool_path_entries = [
                *(str(executable.parent) for executable in tool_executables.values()),
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            ]
            script_env["PATH"] = ":".join(dict.fromkeys(tool_path_entries))
            if postgres_profile is not None and requires_services:
                script_env = postgres_profile_module.candidate_environment(
                    script_env,
                    script_env["PATH"],
                    profile_name=postgres_profile["name"],
                )
            for env_name in unset_env:
                script_env.pop(env_name, None)
            script_env["BUILD_ARTIFACT_ROLE"] = os.environ.get("BUILD_ARTIFACT_ROLE", "")
            script_env["NPM_CONFIG_SCRIPT_SHELL"] = "/bin/sh"
            script_env["npm_config_script_shell"] = "/bin/sh"
            isolated_paths = (script_cache, script_tmp, script_home)
            if len(set(isolated_paths)) != len(isolated_paths):
                sys.exit("candidate writable mount paths are not distinct")
            if any(
                left == right or left in right.parents or right in left.parents
                for position, left in enumerate(isolated_paths)
                for right in isolated_paths[position + 1:]
            ):
                sys.exit("candidate writable mount paths overlap")
            mount_targets = (
                workspace,
                git_metadata,
                cache_root,
                changelog_cache_root,
                *isolated_paths,
                *tool_prefixes,
                *((browser_cache,) if browser_cache is not None else ()),
            )
            namespace_directories = set()
            for target in mount_targets:
                namespace_directories.add(target)
                namespace_directories.update(target.parents)
            namespace_directories.discard(Path("/"))
            directory_args = []
            for directory in sorted(namespace_directories, key=lambda path: (len(path.parts), str(path))):
                if directory == Path("/usr") or directory.is_relative_to("/usr"):
                    continue
                directory_args.extend(("--dir", str(directory)))
            protected_parents = {workspace.parent, runner_temp, cache_root}
            chmod_args = []
            for directory in sorted(protected_parents, key=str):
                chmod_args.extend(("--chmod", "0555", str(directory)))
            tool_mount_args = []
            git_mount_args = []
            tool_prefix_fds = []
            readonly_mount_identities = dict(tool_prefix_identities)
            readonly_mount_identities[changelog_cache_root] = changelog_cache_root_identity
            readonly_mount_identities[git_metadata] = git_metadata_identity
            if browser_cache is not None:
                browser_cache_metadata = browser_cache.stat(follow_symlinks=False)
                if not stat.S_ISDIR(browser_cache_metadata.st_mode):
                    sys.exit("validated Playwright browser cache changed type before namespace bind")
                readonly_mount_identities[browser_cache] = (
                    browser_cache_metadata.st_dev,
                    browser_cache_metadata.st_ino,
                    browser_cache_metadata.st_uid,
                    stat.S_IMODE(browser_cache_metadata.st_mode),
                )
            try:
              for tool_prefix in (
                  *tool_prefixes,
                  changelog_cache_root,
                  git_metadata,
                  *((browser_cache,) if browser_cache is not None else ()),
              ):
                descriptor = os.open(
                  tool_prefix,
                  os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                )
                descriptor_metadata = os.fstat(descriptor)
                descriptor_identity = (
                  descriptor_metadata.st_dev,
                  descriptor_metadata.st_ino,
                  descriptor_metadata.st_uid,
                  stat.S_IMODE(descriptor_metadata.st_mode),
                )
                if descriptor_identity != readonly_mount_identities[tool_prefix]:
                  os.close(descriptor)
                  sys.exit("verified read-only mount changed before namespace bind")
                tool_prefix_fds.append(descriptor)
                mount_flag = "--bind" if tool_prefix == browser_cache else "--ro-bind"
                mount_args = (mount_flag, f"/proc/self/fd/{descriptor}", str(tool_prefix))
                if tool_prefix == git_metadata:
                  git_mount_args.extend(mount_args)
                else:
                  tool_mount_args.extend(mount_args)
            except BaseException:
              for descriptor in tool_prefix_fds:
                os.close(descriptor)
              raise
            artifact_mount_args, artifact_mount_fds = verified_artifact_mounts()
            tool_prefix_fds.extend(artifact_mount_fds)
            network_args = ci_build_artifacts.bubblewrap_network_args(
                shared_artifact=shared_artifact,
                legacy_unshare=not requires_services,
                postgres_profile=postgres_profile is not None,
            )
            artifact_helper_args = (
                ["--ro-bind", str(runner_temp / "ci_build_artifacts.py"), "/tmp/ci_build_artifacts.py"]
                if shared_artifact
                else []
            )
            legacy_network_args = []
            postgres_mount_args = (
                postgres_profile_module.profile_mount_args(postgres_profile_state)
                if postgres_profile is not None and requires_services
                else []
            )
            if postgres_profile is not None:
                postgres_profile_module.verify_profile_runtime(
                    postgres_state_file,
                    runner_temp=runner_temp,
                )
            try:
                received_signal = host_signals.poll()
                if received_signal is not None:
                    interruption = ScriptPlanInterrupted(received_signal)
                    interruption.cleanup_status = "not-started"
                    raise interruption
                arguments = [
                              str(bubblewrap),
                              "--unshare-user",
                              "--unshare-pid",
                              "--as-pid-1",
                              "--unshare-ipc",
                              "--unshare-uts",
                              "--unshare-cgroup-try",
                              "--disable-userns",
                              "--die-with-parent",
                              "--new-session",
                              *network_args,
                              "--tmpfs", "/",
                              "--tmpfs", "/tmp",
                              *artifact_helper_args,
                              *legacy_network_args,
                              *postgres_mount_args,
                              *directory_args,
                              *chmod_args,
                              "--ro-bind", "/usr", "/usr",
                              "--ro-bind", "/bin", "/bin",
                              "--ro-bind", "/lib", "/lib",
                              "--ro-bind", "/lib64", "/lib64",
                              # Present on every dpkg host; optional because this runner
                              # may be self-hosted (#1782).
                              "--ro-bind-try", "/etc/alternatives", "/etc/alternatives",
                              "--ro-bind", "/etc/ssl", "/etc/ssl",
                              "--ro-bind", "/etc/hosts", "/etc/hosts",
                              "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
                              "--ro-bind", "/etc/nsswitch.conf", "/etc/nsswitch.conf",
                              "--ro-bind", "/etc/passwd", "/etc/passwd",
                              "--ro-bind", "/etc/group", "/etc/group",
                              *tool_mount_args,
                              "--bind", str(workspace), str(workspace),
                              *artifact_mount_args,
                              *git_mount_args,
                              "--bind", str(script_cache), str(script_cache),
                              "--bind", str(script_home), str(script_home),
                              "--bind", str(script_tmp), str(script_tmp),
            "--ro-bind", str(npm_user_config), str(npm_user_config),
            "--ro-bind", str(npm_global_config), str(npm_global_config),
                              "--proc", "/proc",
                              "--dev", "/dev",
                              "--chdir", str(script_directory),
                              "--",
                              "/usr/bin/python3", "-I", "-c", sandbox_entrypoint,
                              *npm_command, "run", name,
                          ]
                candidate_result = ci_build_artifacts.run_supervised_bubblewrap(
                    arguments,
                    environment=script_env,
                    pass_fds=tuple(tool_prefix_fds),
                    host_signals=host_signals,
                )
                if postgres_profile is not None:
                    def verify_postgres_runtime():
                        postgres_profile_module.verify_profile_runtime(
                            postgres_state_file,
                            runner_temp=runner_temp,
                        )
                        if candidate_result.return_code is None:
                            raise IntegrityError(
                                "trusted Bubblewrap supervisor omitted the candidate exit code"
                            )

                    ci_build_artifacts._verify_candidate_runtime_boundary(
                        candidate_result, verify_postgres_runtime
                    )
                candidate_status = candidate_result.return_code
                if candidate_status is None:
                    raise IntegrityError("trusted Bubblewrap supervisor omitted the candidate exit code")
                for descriptor in tool_prefix_fds:
                  os.close(descriptor)
                tool_prefix_fds = []
            except (ci_build_artifacts.ScriptCandidateTerminal, ci_build_artifacts.ScriptPlanInterrupted) as failure:
                candidate_failure = failure
                raise
            finally:
              for descriptor in tool_prefix_fds:
                os.close(descriptor)
              shutil.rmtree(script_cache, ignore_errors=True)
              shutil.rmtree(script_tmp, ignore_errors=True)
              shutil.rmtree(script_home, ignore_errors=True)
              ci_build_artifacts._verify_script_candidate_cleanup(
                  candidate_result,
                  candidate_failure,
                  lambda: (
                      script_cache.exists()
                      or script_tmp.exists()
                      or script_home.exists()
                      or (baseline is not None and inventory(baseline) != baseline_inventory)
                  ),
              )
            return ScriptEntryExecutionResult(candidate_status)

        command_records = [
            {
                "directory": script_directory.relative_to(workspace).as_posix(),
                "script": name,
                "unsetEnv": sorted(unset_env),
                "requiresServices": requires_services,
            }
            for script_directory, name, unset_env, requires_services in normalized
        ]
        command_digest = hashlib.sha256(
            json.dumps(command_records, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with open(os.environ["VERJSON_CI_OUTPUT_FILE"], "a", encoding="utf-8") as output:
            output.write(f"command-digest={command_digest}\n")
        fail_fast_value = os.environ.get("CI_LEVEL_FAIL_FAST")
        if fail_fast_value not in {"true", "false"}:
            raise IntegrityError("typed CI level failFast value must be true or false")
        receipt_identity = {
            "repository": os.environ["CI_ENTRY_REPOSITORY"],
            "event": os.environ["CI_ENTRY_EVENT"],
            "ref": os.environ["CI_ENTRY_REF"],
            "commit": os.environ["CI_ENTRY_COMMIT"],
            "workflowSha": os.environ["CI_ENTRY_WORKFLOW_SHA"],
            "runId": os.environ["CI_ENTRY_RUN_ID"],
            "runAttempt": os.environ["CI_ENTRY_RUN_ATTEMPT"],
            "jobId": os.environ["CI_ENTRY_JOB_ID"],
            "levelId": os.environ["CI_LEVEL_ID"],
        }
        if postgres_profile is not None:
            postgres_profile_state = postgres_profile_module.prepare_profile_for_candidate(
                postgres_state_file,
                runner_temp=runner_temp,
            )
        receipt_result = run_script_plan_with_receipts(
            command_records,
            receipt_identity,
            fail_fast_value == "true",
            runner_temp,
            execute_candidate_entry,
            host_signals=host_signals,
        )
        if receipt_result.exit_code != 0:
            sys.exit("candidate script plan failed; see trusted per-entry receipts in RUNNER_TEMP")
    finally:
        cleanup_cache_root()
    if cache_root.exists() or (baseline is not None and inventory(baseline) != baseline_inventory):
        sys.exit("candidate cache root cleanup or final baseline integrity check failed")
    # END source candidate script execution

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("mode-gate")
    commands.add_parser("authorize-role")
    commands.add_parser("run-protected-candidate-plan")
    commands.add_parser("preflight")
    commands.add_parser("network-probe")
    export = commands.add_parser("export")
    export.add_argument("--workspace", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--roots-json", required=True)
    export.add_argument("--identity-json")
    export.add_argument("--consumers", type=int, required=True)
    verify = commands.add_parser("verify-extract")
    verify.add_argument("--artifact", required=True)
    verify.add_argument("--destination", required=True)
    verify.add_argument("--artifact-id", required=True)
    verify.add_argument("--digest", required=True)
    verify.add_argument("--identity-json")
    verify.add_argument("--consumers", type=int, required=True)
    verify.add_argument("--manifest-file")
    receipt = commands.add_parser("receipt")
    receipt.add_argument("--manifest-file")
    receipt.add_argument("--identity-json")
    receipt.add_argument("--lane", required=True)
    receipt.add_argument("--profile", required=True)
    receipt.add_argument("--artifact-id", default="")
    receipt.add_argument("--artifact-digest", default="")
    receipt.add_argument("--command-digest", required=True)
    receipt.add_argument("--output", default=None)
    aggregate = commands.add_parser("aggregate")
    args = parser.parse_args(argv)
    try:
        if args.command == "mode-gate":
            share_eligible = mode_gate(
                os.environ["BUILD_ARTIFACT_MODE_PLAN"],
                event_name=os.environ["GITHUB_EVENT_NAME"],
                base_ref=os.environ.get("GITHUB_BASE_REF", ""),
                ref_name=os.environ.get("GITHUB_REF_NAME", ""),
                repository=os.environ["GITHUB_REPOSITORY"],
                head_repository=os.environ.get("BUILD_ARTIFACT_HEAD_REPOSITORY", os.environ["GITHUB_REPOSITORY"]),
                secretless_pr=os.environ.get("SECRETLESS_PR") == "true",
                secretless_trusted_ref=os.environ.get("SECRETLESS_TRUSTED_REF") == "true",
            )
            _write_output("passed", "true")
            _write_output("share-eligible", str(share_eligible).lower())
            return 0
        if args.command == "network-probe":
            network_probe()
            return 0
        if args.command == "authorize-role":
            _authorize_role_from_environment()
            return 0
        if args.command == "run-protected-candidate-plan":
            run_protected_candidate_plan()
            return 0
        if args.command == "preflight":
            policy = load_policy_table(Path(os.environ["BUILD_ARTIFACT_POLICY_FILE"]).read_bytes())
            plan = _env_json("BUILD_ARTIFACT_PLAN_JSON")
            prerequisite_results = _validate_prerequisite_results(
                plan, _env_json("BUILD_ARTIFACT_NEEDS_RESULTS")
            )
            decision = select_preflight(
                plan, policy,
                repository=os.environ["GITHUB_REPOSITORY"],
                private=os.environ.get("REPOSITORY_PRIVATE") == "true",
                event_name=os.environ["GITHUB_EVENT_NAME"],
                base_ref=os.environ.get("GITHUB_BASE_REF", ""),
                ref_name=os.environ.get("GITHUB_REF_NAME", ""),
                commit=os.environ["CALLER_COMMIT"],
                caller_workflow_sha=os.environ["CALLER_WORKFLOW_SHA"],
                caller_workflow_ref=os.environ.get("GITHUB_WORKFLOW_REF", ""),
                caller_base_sha=os.environ.get("CALLER_BASE_SHA", ""),
                workflow_sha=os.environ["REUSABLE_WORKFLOW_SHA"],
                runner_policy=os.environ["RUNNER_POLICY"],
                runner_os=os.environ["RUNNER_OS"],
                runner_arch=os.environ["RUNNER_ARCH"],
                runner_image_os=os.environ.get("ImageOS", ""),
                runner_image_version=os.environ.get("ImageVersion", ""),
            package_manager_version=os.environ["BUILD_ARTIFACT_PACKAGE_MANAGER_VERSION"],
                needs_results=prerequisite_results,
                checkout=Path(os.environ["GITHUB_WORKSPACE"]),
                mode_share_eligible=os.environ.get("BUILD_ARTIFACT_MODE_SHARE_ELIGIBLE") == "true",
                run_attempt=os.environ["GITHUB_RUN_ATTEMPT"],
            )
            share_selected = decision.get("share-selected")
            if type(share_selected) is not bool:
                raise IntegrityError("preflight share selection is not a JSON boolean")
            _write_output("share-selected", "true" if share_selected else "false")
            _write_output("reason", decision["reason"])
            _write_output("needs-results", json.dumps(prerequisite_results, separators=(",", ":")))
            for key in (
                "producer-plan", "consumer-plans", "profile", "export-paths", "runner-image", "participants",
                "aggregate-lanes",
                "package-manager-version",
            ):
                value = decision.get(key, []) if key in {"export-paths", "participants", "aggregate-lanes"} else decision.get(key, "")
                _write_output(
                    key,
                    json.dumps(value, separators=(",", ":"))
                    if key in {"export-paths", "participants", "aggregate-lanes"}
                    else value,
                )
            return 0
        if args.command == "export":
            result = export_artifact(
                Path(args.workspace), Path(args.output), roots=tuple(_env_json_from_arg(args.roots_json)),
                identity=_env_json_from_arg(args.identity_json) if args.identity_json else identity_from_environment(include_lane=True),
                consumers=args.consumers,
            )
            _write_output("share-available", "true")
            _write_output("compressed-bytes", str(result["bytes"]))
            _write_output("expanded-bytes", str(result["expanded"]))
            return 0
        if args.command == "verify-extract":
            result = verify_and_extract(
                Path(args.artifact), Path(args.destination), expected_digest=args.digest,
                artifact_id=args.artifact_id,
                expected_identity=_env_json_from_arg(args.identity_json) if args.identity_json else identity_from_environment(),
                consumers=args.consumers,
            )
            manifest_file = Path(args.manifest_file or os.path.join(os.environ["RUNNER_TEMP"], "ci-build-artifact-manifest.json"))
            manifest_file.write_bytes(canonical_json(result["manifest"]))
            os.chmod(manifest_file, 0o400)
            _write_output("manifest-path", str(manifest_file))
            _write_output("artifact-digest", result["digest"])
            return 0
        if args.command == "receipt":
            manifest = (
                json.loads(Path(args.manifest_file).read_bytes())
                if args.manifest_file
                else None
            )
            encoded, digest = create_receipt(
                identity=receipt_identity_from_environment(),
                lane=args.lane, profile=args.profile,
                artifact_id=args.artifact_id or None,
                artifact_digest=args.artifact_digest or None,
                verified_manifest=manifest,
                command_digest=args.command_digest,
            )
            _write_output("receipt", encoded, args.output)
            _write_output("receipt-id", digest, args.output)
            _write_output("receipt-digest", digest, args.output)
            return 0
        if args.command == "aggregate":
            share_selected = _parse_exact_json_boolean(
                os.environ.get("SHARE_SELECTED"), "SHARE_SELECTED"
            )
            needs_json = os.environ.get("NEEDS_JSON", "")
            if len(needs_json.encode("utf-8")) > MAX_AGGREGATE_NEEDS_BYTES:
                raise IntegrityError("compact caller-owned needs projection exceeds size limit")
            needs = json.loads(needs_json)
            expected_lanes = _env_json("EXPECTED_LANES_JSON")
            verify_aggregate(
                share_selected=share_selected,
                policy_selected=_parse_exact_json_boolean(
                    os.environ.get("POLICY_SELECTED"), "POLICY_SELECTED"
                ),
                mode_gate_result=os.environ["MODE_GATE_RESULT"],
                preflight_result=os.environ["PREFLIGHT_RESULT"],
                producer_result=os.environ["PRODUCER_RESULT"],
                upload_result=os.environ["UPLOAD_RESULT"],
                artifact_id=os.environ.get("ARTIFACT_ID", ""),
                artifact_digest=os.environ.get("ARTIFACT_DIGEST", ""),
                expected_lanes=expected_lanes,
                receipts=receipts_from_needs(needs, expected_lanes),
                expected_profile=os.environ["EXPECTED_PROFILE"],
                identity={key: value for key, value in receipt_identity_from_environment().items() if key != "runAttempt"},
                attempt=os.environ["EXPECTED_ATTEMPT"],
            )
            return 0
    except (ArtifactError, OSError, subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 2


def _env_json_from_arg(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise IntegrityError("command JSON argument is invalid") from exc


if __name__ == "__main__":
    raise SystemExit(main())
