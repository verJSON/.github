"""Experimental connector identity evidence; this does not authorize a peer."""

import ctypes
import os
import select
import socket
import struct

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
