"""Experimental connector identity evidence; this does not authorize a peer."""

import ctypes
import math
import os
import re
import select
import socket
import struct
import time

_SO_PEERPIDFD = 77  # Linux asm-generic/socket.h; unavailable kernels fail closed.


class PeerEvidenceError(ValueError):
    """The recorded connector could not be pinned to the supervisor namespace."""


def _assert_procfs_root(proc_fd):
    # f_type is the first native long in Linux struct statfs; the buffer is oversized.
    information = ctypes.create_string_buffer(256)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.fstatfs(proc_fd, information) != 0:
        raise OSError(ctypes.get_errno(), 'cannot inspect procfs')
    if ctypes.c_long.from_buffer(information).value != 0x9fa0 or os.fstat(proc_fd).st_ino != 1:
        raise PeerEvidenceError('trusted procfs root is invalid')


def _assert_live(pidfd):
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLERR | select.POLLHUP)
    if poller.poll(0):
        raise PeerEvidenceError('recorded process is no longer live')


def _pidfd_pid(proc_fd, pidfd):
    fdinfo = os.open(f'self/fdinfo/{pidfd}', os.O_RDONLY | os.O_CLOEXEC, dir_fd=proc_fd)
    try:
        content = os.read(fdinfo, 8193)
    finally:
        os.close(fdinfo)
    if len(content) > 8192 or not content.endswith(b'\n'):
        raise PeerEvidenceError('pidfd information is invalid')
    lines = [line.split(b':', 1)[1].strip() for line in content.splitlines() if line.startswith(b'Pid:')]
    if len(lines) != 1 or not lines[0].isdigit():
        raise PeerEvidenceError('pidfd identity is unavailable')
    return int(lines[0])


def read_pinned_chunk(endpoint, *, sender_pidfd, expected_uid, trusted_proc_fd, deadline):
    """Observe one chunk from a pinned process; this does not authorize its contents."""
    received_fds = []
    try:
        if (type(endpoint) is not socket.socket or endpoint.family != socket.AF_UNIX
                or endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
                or endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) != 1
                or type(expected_uid) is not int or not 0 <= expected_uid <= 0xffffffff
                or type(deadline) not in (int, float) or deadline > time.monotonic() + 60
                or not math.isfinite(deadline)
                or any(type(fd) is not int or not 0 <= fd <= 0x7fffffff
                       for fd in (sender_pidfd, trusted_proc_fd))):
            raise PeerEvidenceError('sender evidence request is invalid')
        _assert_procfs_root(trusted_proc_fd)
        _assert_live(sender_pidfd)
        pid = _pidfd_pid(trusted_proc_fd, sender_pidfd)
        if pid <= 0:
            raise PeerEvidenceError('sender pidfd identity is unavailable')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PeerEvidenceError('sender evidence deadline expired')
        endpoint.settimeout(remaining)
        data, ancillary, flags, _ = endpoint.recvmsg(
            4096, socket.CMSG_SPACE(12) + socket.CMSG_SPACE(64), socket.MSG_CMSG_CLOEXEC,
        )
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                size = struct.calcsize('i')
                received_fds.extend(struct.unpack(f'{len(payload) // size}i', payload[:len(payload) // size * size]))
        if flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC) or not data or len(ancillary) != 1:
            raise PeerEvidenceError('sender evidence is incomplete or ambiguous')
        level, kind, payload = ancillary[0]
        if level != socket.SOL_SOCKET or kind != socket.SCM_CREDENTIALS or len(payload) != 12:
            raise PeerEvidenceError('sender credentials are unavailable')
        actual_pid, uid, _gid = struct.unpack('iII', payload)
        if actual_pid != pid or uid != expected_uid:
            raise PeerEvidenceError('sender credentials do not match')
        _assert_live(sender_pidfd)
        if time.monotonic() >= deadline:
            raise PeerEvidenceError('sender evidence deadline expired')
        return data
    except (OSError, ValueError, OverflowError, struct.error) as error:
        if isinstance(error, PeerEvidenceError):
            raise
        raise PeerEvidenceError('sender evidence is unavailable') from error
    finally:
        cleanup_failed = False
        for fd in received_fds:
            try:
                os.close(fd)
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise PeerEvidenceError('received descriptor cleanup failed')


def _parent_pid(proc_fd, pid):
    status_fd = os.open(f'{pid}/status', os.O_RDONLY | os.O_CLOEXEC, dir_fd=proc_fd)
    try:
        content = os.read(status_fd, 32769)
    finally:
        os.close(status_fd)
    lines = [line.split(b':', 1)[1].strip() for line in content.splitlines() if line.startswith(b'PPid:')]
    if len(content) > 32768 or len(lines) != 1 or not lines[0].isdigit():
        raise PeerEvidenceError('child parent identity is unavailable')
    return int(lines[0])


def receive_launch_pidfd(endpoint, *, supervisor_pidfd, expected_uid, supervisor_namespace_fd,
                         trusted_proc_fd, attempt, nonce, deadline):
    """Observe one supervisor handoff; the caller must enforce the launch gate and trust roots."""
    received = []
    connector_fd = -1
    result = -1
    try:
        if (type(endpoint) is not socket.socket or endpoint.family != socket.AF_UNIX
                or endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_SEQPACKET
                or endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) != 1
                or type(expected_uid) is not int or not 0 <= expected_uid <= 0xffffffff
                or type(attempt) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', attempt) is None
                or type(nonce) is not str or re.fullmatch(r'[0-9a-f]{32}', nonce) is None
                or type(deadline) not in (int, float) or deadline > time.monotonic() + 60
                or not math.isfinite(deadline)
                or any(type(fd) is not int or not 0 <= fd <= 0x7fffffff for fd in
                       (supervisor_pidfd, supervisor_namespace_fd, trusted_proc_fd))):
            raise PeerEvidenceError('launch handoff request is invalid')
        _assert_procfs_root(trusted_proc_fd)
        _assert_live(supervisor_pidfd)
        supervisor_pid = _pidfd_pid(trusted_proc_fd, supervisor_pidfd)
        supervisor_ns_fd = os.open(f'{supervisor_pid}/ns/pid', os.O_RDONLY | os.O_CLOEXEC, dir_fd=trusted_proc_fd)
        try:
            expected_ns = os.fstat(supervisor_namespace_fd)
            live_ns = os.fstat(supervisor_ns_fd)
        finally:
            os.close(supervisor_ns_fd)
        if (expected_ns.st_dev, expected_ns.st_ino) != (live_ns.st_dev, live_ns.st_ino):
            raise PeerEvidenceError('launch supervisor namespace does not match')
        pid, uid, _gid = struct.unpack('iII', endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        connector_fd = struct.unpack('i', endpoint.getsockopt(socket.SOL_SOCKET, _SO_PEERPIDFD, 4))[0]
        _assert_live(connector_fd)
        if pid <= 0 or pid != supervisor_pid or uid != expected_uid or _pidfd_pid(trusted_proc_fd, connector_fd) != pid:
            raise PeerEvidenceError('launch supervisor identity does not match')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PeerEvidenceError('launch handoff deadline expired')
        endpoint.settimeout(remaining)
        data, ancillary, flags, _ = endpoint.recvmsg(128, socket.CMSG_SPACE(12) + socket.CMSG_SPACE(64),
                                                    socket.MSG_CMSG_CLOEXEC)
        credentials = []
        unexpected = False
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                size = struct.calcsize('i')
                received.extend(struct.unpack(f'{len(payload) // size}i', payload[:len(payload) // size * size]))
            elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(payload) == 12:
                credentials.append(struct.unpack('iII', payload))
            else:
                unexpected = True
        if (unexpected or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or data != f'launch:{attempt}:{nonce}'.encode('ascii')
                or len(ancillary) != 2 or len(received) != 1 or len(credentials) != 1
                or credentials[0][0] != supervisor_pid or credentials[0][1] != expected_uid):
            raise PeerEvidenceError('launch handoff is incomplete or ambiguous')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PeerEvidenceError('launch handoff deadline expired')
        endpoint.settimeout(remaining)
        extra, extra_ancillary, extra_flags, _ = endpoint.recvmsg(
            128, socket.CMSG_SPACE(12) + socket.CMSG_SPACE(64), socket.MSG_CMSG_CLOEXEC,
        )
        for level, kind, payload in extra_ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                size = struct.calcsize('i')
                received.extend(struct.unpack(f'{len(payload) // size}i', payload[:len(payload) // size * size]))
        if extra or extra_ancillary or extra_flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            raise PeerEvidenceError('launch handoff contains a duplicate or trailing packet')
        child = received[0]
        _assert_live(child)
        child_pid = _pidfd_pid(trusted_proc_fd, child)
        if child_pid <= 0 or child_pid == supervisor_pid or _parent_pid(trusted_proc_fd, child_pid) != supervisor_pid:
            raise PeerEvidenceError('launch child is not the supervisors live child')
        namespace_fd = os.open(f'{child_pid}/ns/pid', os.O_RDONLY | os.O_CLOEXEC, dir_fd=trusted_proc_fd)
        try:
            expected = os.fstat(supervisor_namespace_fd)
            actual = os.fstat(namespace_fd)
        finally:
            os.close(namespace_fd)
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            raise PeerEvidenceError('launch child namespace does not match')
        if _parent_pid(trusted_proc_fd, child_pid) != supervisor_pid:
            raise PeerEvidenceError('launch child parent changed')
        _assert_live(child)
        _assert_live(supervisor_pidfd)
        _assert_live(connector_fd)
        if time.monotonic() >= deadline:
            raise PeerEvidenceError('launch handoff deadline expired')
        result = received.pop()
        return result
    except (OSError, ValueError, OverflowError, struct.error) as error:
        if isinstance(error, PeerEvidenceError):
            raise
        raise PeerEvidenceError('launch handoff is unavailable') from error
    finally:
        failed = False
        for fd in [*received, connector_fd]:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    failed = True
        if failed:
            if result >= 0:
                try:
                    os.close(result)
                except OSError:
                    pass
            raise PeerEvidenceError('launch handoff cleanup failed')


def pin_recorded_connector(endpoint, *, expected_uid, supervisor_pidfd, supervisor_namespace_fd, trusted_proc_fd):
    """Return a caller-owned peer pidfd using an independently trusted host procfs fd.

    This is a point-in-time connector observation, not launch or sender authority.
    The caller must keep both pidfds live and control socket descriptor transfer.
    Cancellation must be queued; asynchronous exceptions can interrupt fd handoff.
    """
    peer_pidfd = -1
    try:
        if (type(endpoint) is not socket.socket or endpoint.family != socket.AF_UNIX
                or endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
                or type(expected_uid) is not int or not 0 <= expected_uid <= 0xffffffff
                or any(type(fd) is not int or not 0 <= fd <= 0x7fffffff for fd in
                       (supervisor_pidfd, supervisor_namespace_fd, trusted_proc_fd))):
            raise PeerEvidenceError('connector request is invalid')
        _assert_procfs_root(trusted_proc_fd)
        _assert_live(supervisor_pidfd)
        supervisor_pid = _pidfd_pid(trusted_proc_fd, supervisor_pidfd)
        if supervisor_pid <= 0:
            raise PeerEvidenceError('supervisor pidfd identity is unavailable')
        supervisor_process_ns = os.open(
            f'{supervisor_pid}/ns/pid', os.O_RDONLY | os.O_CLOEXEC, dir_fd=trusted_proc_fd,
        )
        try:
            supervisor_namespace = os.fstat(supervisor_namespace_fd)
            supervisor_live_namespace = os.fstat(supervisor_process_ns)
        finally:
            os.close(supervisor_process_ns)
        if (supervisor_namespace.st_dev, supervisor_namespace.st_ino) != (
                supervisor_live_namespace.st_dev, supervisor_live_namespace.st_ino):
            raise PeerEvidenceError('supervisor namespace does not match its pidfd')
        peer_pid, uid, _gid = struct.unpack('iII', endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer_pid <= 0 or uid != expected_uid:
            raise PeerEvidenceError('recorded connector credentials do not match')
        peer_pidfd = struct.unpack('i', endpoint.getsockopt(socket.SOL_SOCKET, _SO_PEERPIDFD, 4))[0]
        _assert_live(peer_pidfd)
        if _pidfd_pid(trusted_proc_fd, peer_pidfd) != peer_pid:
            raise PeerEvidenceError('recorded connector pidfd does not match credentials')
        namespace_fd = os.open(f'{peer_pid}/ns/pid', os.O_RDONLY | os.O_CLOEXEC, dir_fd=trusted_proc_fd)
        try:
            peer_namespace = os.fstat(namespace_fd)
        finally:
            os.close(namespace_fd)
        if (supervisor_namespace.st_dev, supervisor_namespace.st_ino) != (peer_namespace.st_dev, peer_namespace.st_ino):
            raise PeerEvidenceError('recorded connector namespace does not match')
        _assert_live(supervisor_pidfd)
        _assert_live(peer_pidfd)
        result = peer_pidfd
        peer_pidfd = -1
        return result
    except (OSError, ValueError, struct.error) as error:
        if isinstance(error, PeerEvidenceError):
            raise
        raise PeerEvidenceError('recorded connector identity is unavailable') from error
    finally:
        if peer_pidfd >= 0:
            os.close(peer_pidfd)
