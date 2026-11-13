"""Experimental harness lifecycle observation; never authorizes admission."""

import hashlib
import hmac
import json
import math
import os
import re
import socket
import subprocess
import time

import compatibility_lane_peer as peer


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate result field')
        result[key] = value
    return result


def observe_harness(*, source, source_digest, lane_id, attempt, nonce, timeout_seconds):
    """Observe one result from a caller-selected harness; callers must establish its authority."""
    unavailable = {'status': 'unavailable', 'readyForApply': False}
    result = unavailable
    parent = child = process = None
    proc_fd = pidfd = -1
    try:
        if (type(source) is not bytes or not 1 <= len(source) <= 131072 or
                type(source_digest) is not str or re.fullmatch(r'[0-9a-f]{64}', source_digest) is None or
                not hmac.compare_digest(hashlib.sha256(source).hexdigest(), source_digest) or
                any(type(value) is not str or re.fullmatch(r'[A-Za-z0-9._-]{1,64}', value) is None
                    for value in (lane_id, attempt)) or
                type(nonce) is not str or re.fullmatch(r'[0-9a-f]{32}', nonce) is None or
                type(timeout_seconds) not in (int, float) or timeout_seconds > 60 or
                not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError('invalid harness observation')
        code = source.decode('utf-8', 'strict')
        if '\x00' in code:
            raise ValueError('invalid harness source')
        deadline = time.monotonic() + timeout_seconds
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        process = subprocess.Popen(
            ['/usr/bin/python3', '-I', '-c', code, str(child.fileno())],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={'PATH': '/usr/bin:/bin'}, close_fds=True, pass_fds=(child.fileno(),),
            start_new_session=True,
        )
        child.close()
        child = None
        pidfd = os.pidfd_open(process.pid)
        data = bytearray()
        while b'\n' not in data:
            data.extend(peer.read_pinned_chunk(parent, sender_pidfd=pidfd, expected_uid=os.getuid(),
                                               trusted_proc_fd=proc_fd, deadline=deadline))
            if len(data) > 8192:
                raise ValueError('result exceeds limit')
        if data.count(b'\n') != 1 or not data.endswith(b'\n'):
            raise ValueError('ambiguous result')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('result deadline expired')
        parent.settimeout(remaining)
        if parent.recv(1, socket.MSG_PEEK) != b'':
            raise ValueError('result has trailing data')
        statement = json.loads(data.decode('utf-8', 'strict'), object_pairs_hook=_unique_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid constant')))
        if (type(statement) is not dict or set(statement) != {'version', 'laneId', 'attempt', 'nonce', 'outcome'} or
                type(statement['version']) is not int or statement['version'] != 1 or
                statement['laneId'] != lane_id or statement['attempt'] != attempt or statement['nonce'] != nonce or
                type(statement['outcome']) is not str or statement['outcome'] not in ('passed', 'failed')):
            raise ValueError('invalid result')
        peer._assert_live(pidfd)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('result deadline expired')
        parent.settimeout(remaining)
        parent.sendall(b'A')
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0 or time.monotonic() >= deadline:
            raise ValueError('harness did not complete')
        result = {'status': 'observed', 'outcome': statement['outcome'], 'readyForApply': False}
    except (OSError, ValueError, TypeError, OverflowError, subprocess.SubprocessError, RecursionError):
        result = unavailable
    finally:
        for endpoint in (parent, child):
            if endpoint is not None:
                try:
                    endpoint.close()
                except OSError:
                    result = unavailable
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=1)
            except (OSError, subprocess.SubprocessError):
                result = unavailable
        for fd in (pidfd, proc_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    result = unavailable
    return result
