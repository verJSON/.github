#!/usr/bin/env python3
import hashlib
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compatibility_lane_harness as harness


class HarnessLifecycleTests(unittest.TestCase):
    def run_source(self, source, **overrides):
        source = source.encode()
        arguments = {
            'source': source,
            'source_digest': hashlib.sha256(source).hexdigest(),
            'lane_id': 'lane-1',
            'attempt': 'attempt-1',
            'nonce': '1' * 32,
            'timeout_seconds': 2,
        }
        arguments.update(overrides)
        return harness.observe_harness(**arguments)

    def test_accepts_only_a_complete_context_bound_result_from_the_live_harness(self):
        source = '''import json, os, socket, sys
s = socket.socket(fileno=int(sys.argv[1]))
result = {'version': 1, 'laneId': 'lane-1', 'attempt': 'attempt-1', 'nonce': '1' * 32, 'outcome': 'passed'}
s.sendall(json.dumps(result).encode() + b'\\n')
s.shutdown(socket.SHUT_WR)
assert s.recv(1) == b'A'
'''
        self.assertEqual(self.run_source(source), {'status': 'observed', 'outcome': 'passed', 'readyForApply': False})
        self.assertEqual(self.run_source(source, nonce='2' * 32), {'status': 'unavailable', 'readyForApply': False})
        self.assertEqual(self.run_source(source, source_digest='0' * 64), {'status': 'unavailable', 'readyForApply': False})

    def test_rejects_an_inherited_writer_and_an_idle_harness(self):
        inherited = '''import os, socket, sys, time
s = socket.socket(fileno=int(sys.argv[1]))
pid = os.fork()
if pid == 0:
 s.sendall(b'{}\\n')
 os._exit(0)
os.waitpid(pid, 0)
time.sleep(1)
'''
        self.assertEqual(self.run_source(inherited), {'status': 'unavailable', 'readyForApply': False})
        self.assertEqual(self.run_source('import time; time.sleep(5)', timeout_seconds=0.1),
                         {'status': 'unavailable', 'readyForApply': False})

    def test_rejects_ambiguous_results_and_failure_after_acknowledgment(self):
        cases = (
            b'{"version":1,"version":1}\n',
            b'{"version":1}\nextra',
            b'x' * 8193 + b'\n',
        )
        for payload in cases:
            with self.subTest(payload_size=len(payload)):
                source = ("import socket,sys\ns=socket.socket(fileno=int(sys.argv[1]))\n"
                          f"s.sendall({payload!r})\ns.shutdown(socket.SHUT_WR)\ns.recv(1)\n")
                self.assertEqual(self.run_source(source), {'status': 'unavailable', 'readyForApply': False})
        source = '''import json, socket, sys
s = socket.socket(fileno=int(sys.argv[1]))
s.sendall(json.dumps({'version':1,'laneId':'lane-1','attempt':'attempt-1','nonce':'1'*32,'outcome':'passed'}).encode()+b'\\n')
s.shutdown(socket.SHUT_WR)
s.recv(1)
sys.exit(7)
'''
        self.assertEqual(self.run_source(source), {'status': 'unavailable', 'readyForApply': False})


if __name__ == '__main__':
    unittest.main()
