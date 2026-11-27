#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / 'scripts/gen-changelog-caller.sh'
PIN = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
FLAG = '--require-downstream-completion'


def generate(mode, *options):
    return subprocess.check_output(['bash', str(GENERATOR), mode, PIN, *options], text=True)


class SnapshotCompletionHold(unittest.TestCase):
    def test_selected_policy_fails_before_any_checkout_and_publish(self):
        workflow = generate('release-snapshot', FLAG)
        verify = workflow.split('\n  verify:\n', 1)[1].split('\n  snapshot:\n', 1)[0]
        publish = workflow.split('\n  publish:\n', 1)[1]
        marker = '      - name: Hold until downstream completion is authenticated\n'
        expected = marker + '        run: |\n          echo "::error::Downstream completion evidence is unavailable. Release remains on hold."\n          exit 1\n'
        for job in (verify, publish):
            self.assertEqual(job.count(marker), 1)
            self.assertIn(expected, job)
            self.assertLess(job.index(marker), job.index('uses: actions/checkout@'))
        self.assertLess(verify.index(marker), verify.index('      - name: Check out the existing snapshot for resumed verification'))
        self.assertLess(publish.index(marker), publish.index('      - name: Publish the snapshot release notes'))
        command = "\n".join(line.removeprefix("          ") for line in expected.splitlines()[2:])
        for history in ('fresh', 'missing-hook', 'hostile-hook'):
            with self.subTest(history=history), tempfile.TemporaryDirectory() as temp:
                marker_path = Path(temp) / 'hook-ran'
                hook = Path(temp) / 'release-verify.sh'
                if history == 'hostile-hook':
                    hook.write_text(f'#!/bin/sh\ntouch {marker_path}\n')
                    hook.chmod(0o755)
                result = subprocess.run(['bash', '-e', '-c', command], cwd=temp, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker_path.exists())

    def test_ordinary_snapshot_has_no_completion_hold(self):
        self.assertNotIn('Hold until downstream completion is authenticated', generate('release-snapshot'))

    def test_policy_flag_rejected_for_unrelated_modes_and_duplicates(self):
        for args in [('release-node', FLAG), ('release-snapshot', FLAG, FLAG)]:
            result = subprocess.run(['bash', str(GENERATOR), args[0], PIN, *args[1:]], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b'')


if __name__ == '__main__':
    unittest.main()
