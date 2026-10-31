#!/usr/bin/env python3
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compatibility_lane_channel as channel


def read_to_eof(endpoint):
    endpoint.settimeout(2)
    data = bytearray()
    while chunk := endpoint.recv(4096):
        data.extend(chunk)
    return bytes(data)


class LaneChannelTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('VERJSON_TEST_REAL_BWRAP') == '1', 'requires Bubblewrap')
    def test_chunk_sender_evidence_matches_a_live_isolated_process(self):
        import compatibility_lane_peer as peer
        import struct

        with tempfile.TemporaryDirectory() as directory:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                listener.bind(directory + '/listener')
                listener.listen(1)
                listener.settimeout(3)
                command = [
                    '/usr/bin/bwrap', '--unshare-user', '--unshare-pid', '--unshare-net',
                    '--cap-drop', 'ALL', '--die-with-parent', '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib',
                    '--ro-bind', '/lib64', '/lib64', '--ro-bind', directory, '/probe',
                    '--proc', '/proc', '--dev', '/dev', '--', '/usr/bin/python3', '-I', '-c',
                    "import socket,time; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); "
                    "s.connect('/probe/listener'); s.sendall(b'isolated'); time.sleep(10)",
                ]
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    accepted, _ = listener.accept()
                    with accepted:
                        pid, _, _ = struct.unpack('iII', accepted.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                        pidfd = os.pidfd_open(pid)
                        try:
                            proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                            try:
                                self.assertNotEqual(os.stat(f'/proc/{pid}/ns/pid').st_ino,
                                                    os.stat('/proc/self/ns/pid').st_ino)
                                self.assertEqual(peer.read_pinned_chunk(
                                    accepted, sender_pidfd=pidfd, expected_uid=os.getuid(), trusted_proc_fd=proc_fd,
                                    deadline=time.monotonic() + 2), b'isolated')
                            finally:
                                os.close(proc_fd)
                        finally:
                            os.close(pidfd)
                finally:
                    process.terminate()
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=5)

    def test_chunk_sender_evidence_rejects_an_inherited_writer(self):
        import compatibility_lane_peer as peer

        receiver, sender = socket.socketpair()
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        pidfd = os.pidfd_open(os.getpid())
        proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            sender.sendall(b'parent')
            self.assertEqual(peer.read_pinned_chunk(receiver, sender_pidfd=pidfd,
                                                    expected_uid=os.getuid(), trusted_proc_fd=proc_fd,
                                                    deadline=time.monotonic() + 2), b'parent')
            child = os.fork()
            if child == 0:
                receiver.close()
                sender.sendall(b'child')
                os._exit(0)
            os.waitpid(child, 0)
            with self.assertRaises(peer.PeerEvidenceError):
                peer.read_pinned_chunk(receiver, sender_pidfd=pidfd,
                                       expected_uid=os.getuid(), trusted_proc_fd=proc_fd, deadline=time.monotonic() + 2)
        finally:
            receiver.close()
            sender.close()
            os.close(pidfd)
            os.close(proc_fd)

    def test_chunk_sender_evidence_rejects_received_descriptors_without_leaks(self):
        import compatibility_lane_peer as peer
        import struct

        receiver, sender = socket.socketpair()
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        pidfd = os.pidfd_open(os.getpid())
        proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for count in (1, 100):
                with self.subTest(count=count):
                    sender.sendmsg([b'x'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                           struct.pack(f'{count}i', *([proc_fd] * count)))])
                    before = len(os.listdir('/proc/self/fd'))
                    with self.assertRaises(peer.PeerEvidenceError):
                        peer.read_pinned_chunk(receiver, sender_pidfd=pidfd,
                                               expected_uid=os.getuid(), trusted_proc_fd=proc_fd, deadline=time.monotonic() + 2)
                    self.assertEqual(len(os.listdir('/proc/self/fd')), before)
        finally:
            receiver.close()
            sender.close()
            os.close(pidfd)
            os.close(proc_fd)

    def test_chunk_sender_evidence_fails_closed_without_valid_credentials(self):
        import compatibility_lane_peer as peer

        pidfd = os.pidfd_open(os.getpid())
        proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for case in ('disabled', 'queued-before-enable', 'wrong-uid', 'eof'):
                with self.subTest(case=case):
                    receiver, sender = socket.socketpair()
                    with receiver, sender:
                        if case == 'queued-before-enable':
                            sender.sendall(b'x')
                        if case != 'disabled':
                            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                        if case in ('disabled', 'wrong-uid'):
                            sender.sendall(b'x')
                        if case == 'eof':
                            sender.shutdown(socket.SHUT_WR)
                        with self.assertRaises(peer.PeerEvidenceError):
                            peer.read_pinned_chunk(receiver, sender_pidfd=pidfd,
                                                   expected_uid=os.getuid() + (case == 'wrong-uid'),
                                                   trusted_proc_fd=proc_fd, deadline=time.monotonic() + 2)
        finally:
            os.close(pidfd)
            os.close(proc_fd)

    def test_chunk_sender_evidence_times_out_for_an_idle_sender(self):
        import compatibility_lane_peer as peer

        receiver, sender = socket.socketpair()
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        pidfd = os.pidfd_open(os.getpid())
        proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            with self.assertRaises(peer.PeerEvidenceError):
                peer.read_pinned_chunk(receiver, sender_pidfd=pidfd, expected_uid=os.getuid(),
                                       trusted_proc_fd=proc_fd, deadline=time.monotonic() + 0.02)
        finally:
            receiver.close()
            sender.close()
            os.close(pidfd)
            os.close(proc_fd)

    def test_chunk_sender_evidence_rejects_unbounded_deadlines(self):
        import compatibility_lane_peer as peer

        receiver, sender = socket.socketpair()
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        pidfd = os.pidfd_open(os.getpid())
        proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for deadline in (10 ** 1000, 1e308):
                with self.subTest(deadline=deadline):
                    with self.assertRaises(peer.PeerEvidenceError):
                        peer.read_pinned_chunk(receiver, sender_pidfd=pidfd, expected_uid=os.getuid(),
                                               trusted_proc_fd=proc_fd, deadline=deadline)
        finally:
            receiver.close()
            sender.close()
            os.close(pidfd)
            os.close(proc_fd)

    def test_chunk_sender_cleanup_attempts_every_descriptor_after_close_failure(self):
        import compatibility_lane_peer as peer
        import struct

        receiver, sender = socket.socketpair()
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        pidfd = os.pidfd_open(os.getpid())
        proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        original_close = os.close
        closed = []

        def close_and_report_error_once(fd):
            original_close(fd)
            closed.append(fd)
            if len(closed) == 1:
                raise OSError('injected cleanup failure')

        try:
            sender.sendmsg([b'x'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack('2i', proc_fd, proc_fd))])
            with patch.object(peer, '_pidfd_pid', return_value=os.getpid()), \
                    patch.object(peer.os, 'close', side_effect=close_and_report_error_once):
                with self.assertRaises(peer.PeerEvidenceError):
                    peer.read_pinned_chunk(receiver, sender_pidfd=pidfd, expected_uid=os.getuid(),
                                           trusted_proc_fd=proc_fd, deadline=time.monotonic() + 2)
            self.assertEqual(len(closed), 2)
        finally:
            receiver.close()
            sender.close()
            original_close(pidfd)
            original_close(proc_fd)

    def test_pins_live_recorded_connector_in_supervisor_namespace(self):
        import compatibility_lane_peer as peer

        with tempfile.TemporaryDirectory() as directory:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(directory + '/listener')
                listener.listen(1)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(directory + '/listener')
                    accepted, _ = listener.accept()
                    with accepted:
                        namespace_fd = os.open('/proc/self/ns/pid', os.O_RDONLY | os.O_CLOEXEC)
                        supervisor_pidfd = os.pidfd_open(os.getpid())
                        proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                        try:
                            peer_pidfd = peer.pin_recorded_connector(
                                accepted, expected_uid=os.getuid(), supervisor_pidfd=supervisor_pidfd,
                                supervisor_namespace_fd=namespace_fd, trusted_proc_fd=proc_fd,
                            )
                            try:
                                os.fstat(peer_pidfd)
                            finally:
                                os.close(peer_pidfd)
                        finally:
                            os.close(proc_fd)
                            os.close(supervisor_pidfd)
                            os.close(namespace_fd)

    def test_unavailable_socket_evidence_fails_closed_with_domain_error(self):
        import compatibility_lane_peer as peer

        endpoint, other = socket.socketpair()
        with endpoint, other:
            with patch.object(socket.socket, 'getsockopt', side_effect=OSError('unsupported')):
                with self.assertRaises(peer.PeerEvidenceError):
                    peer.pin_recorded_connector(
                        endpoint, expected_uid=os.getuid(), supervisor_pidfd=-1,
                        supervisor_namespace_fd=-1, trusted_proc_fd=-1,
                    )

    def test_invalid_descriptor_arguments_fail_with_domain_error(self):
        import compatibility_lane_peer as peer

        endpoint, other = socket.socketpair()
        with endpoint, other:
            for descriptor in (None, True, -1, 2 ** 64):
                with self.subTest(descriptor=descriptor):
                    with self.assertRaises(peer.PeerEvidenceError):
                        peer.pin_recorded_connector(
                            endpoint, expected_uid=os.getuid(), supervisor_pidfd=descriptor,
                            supervisor_namespace_fd=descriptor, trusted_proc_fd=descriptor,
                        )

    def test_rejects_a_fabricated_procfs_root_even_with_matching_namespace(self):
        import compatibility_lane_peer as peer

        endpoint, other = socket.socketpair()
        with tempfile.TemporaryDirectory() as directory, endpoint, other:
            os.makedirs(f'{directory}/{os.getpid()}/ns')
            os.symlink('/proc/self/ns/pid', f'{directory}/{os.getpid()}/ns/pid')
            namespace_fd = os.open('/proc/self/ns/pid', os.O_RDONLY | os.O_CLOEXEC)
            supervisor_pidfd = os.pidfd_open(os.getpid())
            fake_proc_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                with patch.object(peer, '_pidfd_pid', return_value=os.getpid()):
                    result = None
                    try:
                        with self.assertRaises(peer.PeerEvidenceError):
                            result = peer.pin_recorded_connector(
                                endpoint, expected_uid=os.getuid(), supervisor_pidfd=supervisor_pidfd,
                                supervisor_namespace_fd=namespace_fd, trusted_proc_fd=fake_proc_fd,
                            )
                    finally:
                        if result is not None:
                            os.close(result)
            finally:
                os.close(fake_proc_fd)
                os.close(supervisor_pidfd)
                os.close(namespace_fd)

    def test_rejects_a_live_non_pidfd_as_supervisor_identity(self):
        import compatibility_lane_peer as peer

        endpoint, other = socket.socketpair()
        pipe_read, pipe_write = os.pipe()
        namespace_fd = os.open('/proc/self/ns/pid', os.O_RDONLY | os.O_CLOEXEC)
        proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        with endpoint, other:
            try:
                result = None
                try:
                    with self.assertRaises(peer.PeerEvidenceError):
                        result = peer.pin_recorded_connector(
                            endpoint, expected_uid=os.getuid(), supervisor_pidfd=pipe_read,
                            supervisor_namespace_fd=namespace_fd, trusted_proc_fd=proc_fd,
                        )
                finally:
                    if result is not None:
                        os.close(result)
            finally:
                os.close(proc_fd)
                os.close(namespace_fd)
                os.close(pipe_write)
                os.close(pipe_read)

    def test_rejects_wrong_peer_evidence_without_leaking_descriptors(self):
        import compatibility_lane_peer as peer

        endpoint, other = socket.socketpair()
        namespace_fd = os.open('/proc/self/ns/pid', os.O_RDONLY | os.O_CLOEXEC)
        supervisor_pidfd = os.pidfd_open(os.getpid())
        proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        with endpoint, other, tempfile.TemporaryFile() as wrong_namespace:
            try:
                before = set(os.listdir('/proc/self/fd'))
                cases = ('uid', 'namespace', 'pid')
                for case in cases:
                    with self.subTest(case=case):
                        expected_uid = os.getuid() + (case == 'uid')
                        selected_namespace = wrong_namespace.fileno() if case == 'namespace' else namespace_fd
                        pid_values = [os.getpid(), os.getpid() + 1] if case == 'pid' else None
                        with patch.object(peer, '_pidfd_pid', side_effect=pid_values, wraps=peer._pidfd_pid):
                            with self.assertRaises(peer.PeerEvidenceError):
                                peer.pin_recorded_connector(
                                    endpoint, expected_uid=int(expected_uid), supervisor_pidfd=supervisor_pidfd,
                                    supervisor_namespace_fd=selected_namespace, trusted_proc_fd=proc_fd,
                                )
                        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                original_getsockopt = endpoint.getsockopt

                def without_peer_pidfd(level, option, *args):
                    if option == peer._SO_PEERPIDFD:
                        raise OSError('SO_PEERPIDFD unsupported')
                    return original_getsockopt(level, option, *args)

                with patch.object(socket.socket, 'getsockopt', side_effect=without_peer_pidfd):
                    with self.assertRaises(peer.PeerEvidenceError):
                        peer.pin_recorded_connector(
                            endpoint, expected_uid=os.getuid(), supervisor_pidfd=supervisor_pidfd,
                            supervisor_namespace_fd=namespace_fd, trusted_proc_fd=proc_fd,
                        )
                self.assertEqual(set(os.listdir('/proc/self/fd')), before)
            finally:
                os.close(proc_fd)
                os.close(supervisor_pidfd)
                os.close(namespace_fd)

    def test_rejects_an_accepted_connection_after_its_connector_exits(self):
        import compatibility_lane_peer as peer

        with tempfile.TemporaryDirectory() as directory:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                path = directory + '/listener'
                listener.bind(path)
                listener.listen(1)
                listener.settimeout(5)
                child = os.fork()
                if child == 0:
                    try:
                        listener.close()
                        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                            client.connect(path)
                    finally:
                        os._exit(0)
                accepted, _ = listener.accept()
                os.waitpid(child, 0)
                with accepted:
                    namespace_fd = os.open('/proc/self/ns/pid', os.O_RDONLY | os.O_CLOEXEC)
                    supervisor_pidfd = os.pidfd_open(os.getpid())
                    proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                    try:
                        before = set(os.listdir('/proc/self/fd'))
                        with self.assertRaises(peer.PeerEvidenceError):
                            peer.pin_recorded_connector(
                                accepted, expected_uid=os.getuid(), supervisor_pidfd=supervisor_pidfd,
                                supervisor_namespace_fd=namespace_fd, trusted_proc_fd=proc_fd,
                            )
                        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                    finally:
                        os.close(proc_fd)
                        os.close(supervisor_pidfd)
                        os.close(namespace_fd)

    def test_rejects_supervisor_exit_during_peer_validation(self):
        import compatibility_lane_peer as peer

        endpoint, other = socket.socketpair()
        hold_read, hold_write = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(hold_write)
            os.read(hold_read, 1)
            os._exit(0)
        os.close(hold_read)
        supervisor_pidfd = os.pidfd_open(child)
        namespace_fd = os.open('/proc/self/ns/pid', os.O_RDONLY | os.O_CLOEXEC)
        proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        original_pid = peer._pidfd_pid
        calls = 0

        def exit_supervisor_during_validation(proc, pidfd):
            nonlocal calls
            value = original_pid(proc, pidfd)
            calls += 1
            if calls == 2:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
            return value

        with endpoint, other:
            try:
                with patch.object(peer, '_pidfd_pid', side_effect=exit_supervisor_during_validation):
                    with self.assertRaises(peer.PeerEvidenceError):
                        peer.pin_recorded_connector(
                            endpoint, expected_uid=os.getuid(), supervisor_pidfd=supervisor_pidfd,
                            supervisor_namespace_fd=namespace_fd, trusted_proc_fd=proc_fd,
                        )
                self.assertEqual(calls, 2)
            finally:
                os.close(hold_write)
                try:
                    os.waitpid(child, 0)
                except ChildProcessError:
                    pass
                os.close(proc_fd)
                os.close(namespace_fd)
                os.close(supervisor_pidfd)

    def test_relays_one_bound_request_and_response_without_granting_readiness(self):
        harness_controller, harness = socket.socketpair()
        candidate_controller, candidate = socket.socketpair()
        for endpoint in (harness_controller, harness, candidate_controller, candidate):
            self.addCleanup(endpoint.close)
        request = {'version': 1, 'type': 'request', 'nonce': 'a' * 32, 'payload': 'probe'}
        response = {'version': 1, 'type': 'response', 'nonce': 'a' * 32, 'payload': 'answer'}
        harness.sendall(json.dumps(request).encode() + b'\n')
        harness.shutdown(socket.SHUT_WR)
        observed = []

        def candidate_exchange():
            observed.append(json.loads(read_to_eof(candidate)))
            candidate.sendall(json.dumps(response).encode() + b'\n')
            candidate.shutdown(socket.SHUT_WR)

        thread = threading.Thread(target=candidate_exchange)
        thread.start()
        result = channel.exchange(harness_controller, candidate_controller, expected_nonce='a' * 32, timeout_seconds=2)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(observed, [request])
        self.assertEqual(json.loads(read_to_eof(harness)), response)
        self.assertEqual(result, {'status': 'exchanged', 'readyForApply': False})

    def test_reassembles_fragmented_frames_before_relaying(self):
        harness_controller, harness = socket.socketpair()
        candidate_controller, candidate = socket.socketpair()
        for endpoint in (harness_controller, harness, candidate_controller, candidate):
            self.addCleanup(endpoint.close)
        request = {'version': 1, 'type': 'request', 'nonce': 'b' * 32, 'payload': 'probe'}
        response = {'version': 1, 'type': 'response', 'nonce': 'b' * 32, 'payload': 'answer'}
        encoded_request = json.dumps(request).encode() + b'\n'
        encoded_response = json.dumps(response).encode() + b'\n'
        harness.sendall(encoded_request[:12])

        def peers():
            time.sleep(0.03)
            harness.sendall(encoded_request[12:])
            harness.shutdown(socket.SHUT_WR)
            self.assertEqual(json.loads(read_to_eof(candidate)), request)
            candidate.sendall(encoded_response[:12])
            time.sleep(0.03)
            candidate.sendall(encoded_response[12:])
            candidate.shutdown(socket.SHUT_WR)

        thread = threading.Thread(target=peers)
        thread.start()
        result = channel.exchange(harness_controller, candidate_controller, expected_nonce='b' * 32, timeout_seconds=2)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(json.loads(read_to_eof(harness)), response)
        self.assertEqual(result, {'status': 'exchanged', 'readyForApply': False})

    def test_rejects_ambiguous_or_invalid_requests_before_candidate_receives_them(self):
        for frame in [
            b'{"version":true,"type":"request","nonce":"' + b'a' * 32 + b'","payload":"probe"}\n',
            b'{"version":1,"type":"request","type":"request","nonce":"' + b'a' * 32 + b'","payload":"probe"}\n',
            b'{"version":1,"type":"request","nonce":"bad","payload":"probe"}\n',
            b'{"version":1,"type":"request","nonce":"' + b'a' * 32 + b'","payload":"probe","extra":true}\n',
        ]:
            with self.subTest(frame=frame):
                harness_controller, harness = socket.socketpair()
                candidate_controller, candidate = socket.socketpair()
                try:
                    harness.sendall(frame)
                    harness.shutdown(socket.SHUT_WR)
                    result = channel.exchange(harness_controller, candidate_controller, expected_nonce='a' * 32, timeout_seconds=0.2)
                    candidate.settimeout(1)
                    self.assertEqual(candidate.recv(4096), b'')
                    self.assertEqual(result, {'status': 'unavailable', 'readyForApply': False})
                finally:
                    for endpoint in (harness_controller, harness, candidate_controller, candidate):
                        endpoint.close()

    def test_rejects_a_delayed_second_frame_before_forwarding(self):
        harness_controller, harness = socket.socketpair()
        candidate_controller, candidate = socket.socketpair()
        for endpoint in (harness_controller, harness, candidate_controller, candidate):
            self.addCleanup(endpoint.close)
        request = {'version': 1, 'type': 'request', 'nonce': 'a' * 32, 'payload': 'probe'}
        harness.sendall(json.dumps(request).encode() + b'\n')

        def append_frame():
            time.sleep(0.03)
            harness.sendall(b'{}\n')
            harness.shutdown(socket.SHUT_WR)

        thread = threading.Thread(target=append_frame)
        thread.start()
        result = channel.exchange(harness_controller, candidate_controller, expected_nonce='a' * 32, timeout_seconds=0.3)
        thread.join(timeout=1)
        candidate.settimeout(1)
        self.assertEqual(candidate.recv(4096), b'')
        self.assertEqual(result, {'status': 'unavailable', 'readyForApply': False})

    def test_invalid_deadlines_fail_before_forwarding(self):
        request = {'version': 1, 'type': 'request', 'nonce': 'a' * 32, 'payload': 'probe'}
        for deadline in (True, 0, -1, float('nan'), float('inf'), 10**1000):
            with self.subTest(deadline=deadline):
                harness_controller, harness = socket.socketpair()
                candidate_controller, candidate = socket.socketpair()
                try:
                    harness.sendall(json.dumps(request).encode() + b'\n')
                    harness.shutdown(socket.SHUT_WR)
                    result = channel.exchange(harness_controller, candidate_controller, expected_nonce='a' * 32, timeout_seconds=deadline)
                    candidate.settimeout(1)
                    self.assertEqual(candidate.recv(4096), b'')
                    self.assertEqual(result, {'status': 'unavailable', 'readyForApply': False})
                finally:
                    for endpoint in (harness_controller, harness, candidate_controller, candidate):
                        endpoint.close()

    def test_does_not_report_exchange_after_the_deadline(self):
        harness_controller, harness = socket.socketpair()
        candidate_controller, candidate = socket.socketpair()
        for endpoint in (harness_controller, harness, candidate_controller, candidate):
            self.addCleanup(endpoint.close)
        request = {'version': 1, 'type': 'request', 'nonce': 'a' * 32, 'payload': 'probe'}
        response = {'version': 1, 'type': 'response', 'nonce': 'a' * 32, 'payload': 'answer'}
        harness.sendall(json.dumps(request).encode() + b'\n')
        harness.shutdown(socket.SHUT_WR)
        candidate.sendall(json.dumps(response).encode() + b'\n')
        candidate.shutdown(socket.SHUT_WR)
        original_send = socket.socket.sendall

        def delayed_send(endpoint, data):
            original_send(endpoint, data)
            if endpoint is harness_controller:
                time.sleep(0.15)

        with patch.object(socket.socket, 'sendall', new=delayed_send):
            result = channel.exchange(harness_controller, candidate_controller, expected_nonce='a' * 32, timeout_seconds=0.1)
        self.assertEqual(result, {'status': 'unavailable', 'readyForApply': False})

    def test_candidate_cannot_forward_forged_or_unbound_completion(self):
        request = {'version': 1, 'type': 'request', 'nonce': 'a' * 32, 'payload': 'probe'}
        for frame in [
            b'{"version":1,"type":"completion","nonce":"' + b'a' * 32 + b'","payload":"passed"}\n',
            b'{"version":1,"type":"response","nonce":"' + b'b' * 32 + b'","payload":"answer"}\n',
            b'{"version":1,"type":"response","type":"response","nonce":"' + b'a' * 32 + b'","payload":"answer"}\n',
            b'{"version":1,"type":"response","nonce":"' + b'a' * 32 + b'","payload":"' + b'x' * 8192 + b'"}\n',
        ]:
            with self.subTest(frame=frame[:80]):
                harness_controller, harness = socket.socketpair()
                candidate_controller, candidate = socket.socketpair()
                try:
                    harness.sendall(json.dumps(request).encode() + b'\n')
                    harness.shutdown(socket.SHUT_WR)
                    candidate.sendall(frame)
                    candidate.shutdown(socket.SHUT_WR)
                    result = channel.exchange(harness_controller, candidate_controller, expected_nonce='a' * 32, timeout_seconds=1)
                    harness.settimeout(1)
                    self.assertEqual(harness.recv(4096), b'')
                    self.assertEqual(result, {'status': 'unavailable', 'readyForApply': False})
                finally:
                    for endpoint in (harness_controller, harness, candidate_controller, candidate):
                        endpoint.close()

    def test_stalled_sender_times_out_without_forwarding(self):
        harness_controller, harness = socket.socketpair()
        candidate_controller, candidate = socket.socketpair()
        for endpoint in (harness_controller, harness, candidate_controller, candidate):
            self.addCleanup(endpoint.close)
        request = {'version': 1, 'type': 'request', 'nonce': 'a' * 32, 'payload': 'probe'}
        harness.sendall(json.dumps(request).encode() + b'\n')
        started = time.monotonic()
        result = channel.exchange(harness_controller, candidate_controller, expected_nonce='a' * 32, timeout_seconds=0.1)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(candidate.recv(4096), b'')
        self.assertEqual(result, {'status': 'unavailable', 'readyForApply': False})

    def test_candidate_receives_eof_before_forming_its_response(self):
        harness_controller, harness = socket.socketpair()
        candidate_controller, candidate = socket.socketpair()
        for endpoint in (harness_controller, harness, candidate_controller, candidate):
            self.addCleanup(endpoint.close)
        request = {'version': 1, 'type': 'request', 'nonce': 'a' * 32, 'payload': 'probe'}
        response = {'version': 1, 'type': 'response', 'nonce': 'a' * 32, 'payload': 'answer'}
        harness.sendall(json.dumps(request).encode() + b'\n')
        harness.shutdown(socket.SHUT_WR)
        received = []

        def candidate_exchange():
            try:
                chunks = bytearray()
                while chunk := candidate.recv(4096):
                    chunks.extend(chunk)
                received.append(json.loads(chunks))
                candidate.sendall(json.dumps(response).encode() + b'\n')
                candidate.shutdown(socket.SHUT_WR)
            except OSError:
                pass

        thread = threading.Thread(target=candidate_exchange)
        thread.start()
        result = channel.exchange(harness_controller, candidate_controller, expected_nonce='a' * 32, timeout_seconds=0.5)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, {'status': 'exchanged', 'readyForApply': False})
        self.assertEqual(received, [request])
        self.assertEqual(json.loads(read_to_eof(harness)), response)

    def test_rejects_request_from_a_different_controller_attempt(self):
        harness_controller, harness = socket.socketpair()
        candidate_controller, candidate = socket.socketpair()
        for endpoint in (harness_controller, harness, candidate_controller, candidate):
            self.addCleanup(endpoint.close)
        request = {'version': 1, 'type': 'request', 'nonce': 'b' * 32, 'payload': 'probe'}
        harness.sendall(json.dumps(request).encode() + b'\n')
        harness.shutdown(socket.SHUT_WR)
        result = channel.exchange(harness_controller, candidate_controller, expected_nonce='a' * 32, timeout_seconds=1)
        self.assertEqual(candidate.recv(4096), b'')
        self.assertEqual(result, {'status': 'unavailable', 'readyForApply': False})

    def test_separate_processes_do_not_inherit_the_harness_result_descriptor(self):
        harness_controller, harness = socket.socketpair()
        candidate_controller, candidate = socket.socketpair()
        result_read, result_write = os.pipe()
        harness_code = '''
import json, os, socket, sys
s = socket.socket(fileno=int(sys.argv[1]))
s.sendall(json.dumps({'version': 1, 'type': 'request', 'nonce': 'a'*32, 'payload': 'probe'}).encode() + b'\\n')
s.shutdown(socket.SHUT_WR)
data = bytearray()
while chunk := s.recv(4096): data.extend(chunk)
if json.loads(data)['payload'] == 'answer': os.write(int(sys.argv[2]), b'HARNESS_OK')
'''
        candidate_code = '''
import json, os, socket, sys
s = socket.socket(fileno=int(sys.argv[1]))
data = bytearray()
while chunk := s.recv(4096): data.extend(chunk)
request = json.loads(data)
try:
 os.write(int(sys.argv[2]), b'FORGED')
 payload = 'unexpected-open-result-descriptor'
except OSError:
 payload = 'answer'
s.sendall(json.dumps({'version': 1, 'type': 'response', 'nonce': request['nonce'], 'payload': payload}).encode() + b'\\n')
s.shutdown(socket.SHUT_WR)
'''
        children = []
        try:
            children.append(subprocess.Popen(
                [sys.executable, '-I', '-c', harness_code, str(harness.fileno()), str(result_write)],
                pass_fds=(harness.fileno(), result_write), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
            children.append(subprocess.Popen(
                [sys.executable, '-I', '-c', candidate_code, str(candidate.fileno()), str(result_write)],
                pass_fds=(candidate.fileno(),), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
            os.close(result_write)
            result_write = -1
            harness.close()
            candidate.close()
            result = channel.exchange(harness_controller, candidate_controller, expected_nonce='a' * 32, timeout_seconds=3)
            self.assertEqual(result, {'status': 'exchanged', 'readyForApply': False})
            for process in children:
                self.assertEqual(process.wait(timeout=3), 0)
            self.assertEqual(os.read(result_read, 64), b'HARNESS_OK')
        finally:
            for process in children:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)
            for endpoint in (harness_controller, harness, candidate_controller, candidate):
                endpoint.close()
            os.close(result_read)
            if result_write >= 0:
                os.close(result_write)


if __name__ == '__main__':
    unittest.main()
