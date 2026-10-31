#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import compatibility_lane_channel as channel


def read_to_eof(endpoint):
    endpoint.settimeout(2)
    data = bytearray()
    while chunk := endpoint.recv(4096):
        data.extend(chunk)
    return bytes(data)


class LaneChannelTests(unittest.TestCase):
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
