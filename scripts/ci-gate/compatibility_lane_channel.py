"""Experimental one-shot lane relay; it does not authenticate or isolate peers."""

import json
import re
import socket
import time

MAX_FRAME_BYTES = 8192


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('lane exchange deadline expired')
    return remaining


def _read_frame(endpoint, deadline):
    data = bytearray()
    while True:
        endpoint.settimeout(_remaining(deadline))
        chunk = endpoint.recv(min(4096, MAX_FRAME_BYTES + 1 - len(data)))
        if not chunk:
            if data.count(b'\n') != 1 or not data.endswith(b'\n') or b'\r' in data:
                raise ValueError('lane frame is incomplete or ambiguous')
            return bytes(data)
        data.extend(chunk)
        if len(data) > MAX_FRAME_BYTES:
            raise ValueError('lane frame exceeds limit')
        if b'\n' in data:
            if data.count(b'\n') != 1 or not data.endswith(b'\n'):
                raise ValueError('lane frame has trailing data')


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate frame key')
        value[key] = item
    return value


def _invalid_constant(_value):
    raise ValueError('invalid frame constant')


def _parse_frame(data, expected_type):
    frame = json.loads(data.decode('utf-8', 'strict'), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if (type(frame) is not dict or set(frame) != {'version', 'type', 'nonce', 'payload'} or
            type(frame['version']) is not int or frame['version'] != 1 or
            type(frame['type']) is not str or frame['type'] != expected_type or
            type(frame['nonce']) is not str or re.fullmatch(r'[0-9a-f]{32}', frame['nonce']) is None or
            type(frame['payload']) is not str or len(frame['payload'].encode('utf-8', 'strict')) > 4096):
        raise ValueError('invalid lane frame')
    return frame


def exchange(harness, candidate, *, expected_nonce, timeout_seconds):
    """Consume and close two controller-owned Unix stream sockets, once."""
    unavailable = {'status': 'unavailable', 'readyForApply': False}
    outcome = unavailable
    try:
        if (type(harness) is not socket.socket or type(candidate) is not socket.socket or
                harness is candidate or harness.fileno() == candidate.fileno() or
                harness.family != socket.AF_UNIX or candidate.family != socket.AF_UNIX or
                harness.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM or
                candidate.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM or
                type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 60 or
                type(expected_nonce) is not str or re.fullmatch(r'[0-9a-f]{32}', expected_nonce) is None):
            raise ValueError('invalid lane channel')
        deadline = time.monotonic() + timeout_seconds
        request_bytes = _read_frame(harness, deadline)
        request = _parse_frame(request_bytes, 'request')
        if request['nonce'] != expected_nonce:
            raise ValueError('lane request nonce changed')
        candidate.settimeout(_remaining(deadline))
        candidate.sendall(request_bytes)
        candidate.shutdown(socket.SHUT_WR)
        response_bytes = _read_frame(candidate, deadline)
        response = _parse_frame(response_bytes, 'response')
        if response['nonce'] != request['nonce']:
            raise ValueError('lane nonce changed')
        harness.settimeout(_remaining(deadline))
        harness.sendall(response_bytes)
        harness.shutdown(socket.SHUT_WR)
        outcome = {'status': 'exchanged', 'readyForApply': False}
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        outcome = unavailable
    finally:
        for endpoint in (harness, candidate):
            if type(endpoint) is socket.socket:
                try:
                    endpoint.close()
                except OSError:
                    outcome = unavailable
    if outcome['status'] == 'exchanged' and time.monotonic() >= deadline:
        return unavailable
    return outcome
