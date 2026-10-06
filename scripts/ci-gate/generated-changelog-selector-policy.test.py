#!/usr/bin/env python3
"""Exercise the consumer policy against actual canonical generator output."""
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / 'scripts/gen-changelog-caller.sh'
POLICY = ROOT / 'scripts/ci-gate/hosted-selector-policy.py'


class GeneratedCallerPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        cls.gate = yaml.safe_load(cls.generate('pr-gate'))

    @classmethod
    def generate(cls, mode, *args):
        return subprocess.check_output([str(GENERATOR), mode, cls.sha, *args], text=True)

    def policy(self, text):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'caller.yml').write_text(text)
            return subprocess.run([sys.executable, str(POLICY), '--consumer-policy', directory], capture_output=True, text=True)

    def assert_allowed(self, text):
        result = self.policy(text)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_generated_default_gate_and_release_callers_pass_consumer_policy(self):
        for mode in ('pr-gate', 'release-node', 'release-snapshot', 'release-propose'):
            with self.subTest(mode=mode):
                args = ('--autonomy', 'propose') if mode == 'release-propose' else ()
                self.assert_allowed(self.generate(mode, *args))

    def test_unreviewed_organization_spelling_does_not_expand_runner_policy(self):
        workflow = self.generate('release-node')
        self.assertIn("'verJSON'", workflow)
        result = self.policy(workflow.replace("'verJSON'", "'verjson'"))
        self.assertEqual(result.returncode, 2)
        self.assertIn('UNDETERMINED', result.stdout + result.stderr)

    def test_gate_hosted_isolation_and_release_routing_remain_unchanged(self):
        job = self.gate['jobs']['changelog-contract']
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        self.assertFalse(job['steps'][0]['with']['persist-credentials'])
        self.assertIn("vars.CI_RUNNER_DEFAULT", self.generate('release-snapshot'))

    def test_extra_or_changed_execution_fields_do_not_inherit_hosted_exception(self):
        mutations = [
            ((), 'env', {'INJECT': 'true'}), ((), 'defaults', {'run': {'shell': 'bash'}}),
            ((), 'permissions', {'contents': 'write'}), ((), True, {'pull_request_target': None}),
            (('jobs',), 'extra', {'runs-on': 'ubuntu-24.04', 'steps': [{'run': 'echo extra'}]}),
            (('jobs', 'changelog-contract'), 'permissions', {'contents': 'write'}),
            (('jobs', 'changelog-contract'), 'env', {'BASH_ENV': '/tmp/injected'}),
            (('jobs', 'changelog-contract'), 'defaults', {'run': {'working-directory': 'other'}}),
            (('jobs', 'changelog-contract'), 'timeout-minutes', 11),
            (('jobs', 'changelog-contract'), 'timeout-minutes', 10.0),
            (('jobs', 'changelog-contract', 'steps', 0, 'with'), 'persist-credentials', True),
            (('jobs', 'changelog-contract', 'steps', 0, 'with'), 'persist-credentials', 0),
            (('jobs', 'changelog-contract', 'steps', 0, 'with'), 'ref', '${{ github.event.pull_request.head.sha }}'),
            (('jobs', 'changelog-contract', 'steps', 0), 'uses', 'actions/checkout@main'),
            (('jobs', 'changelog-contract', 'steps', 1), 'run', 'echo injected'),
            (('jobs', 'changelog-contract', 'steps', 2), 'run', 'bash different.sh'),
            (('jobs', 'changelog-contract', 'steps', 2), 'env', {'SECRET': '${{ secrets.KEY }}'}),
        ]
        for path, key, value in mutations:
            with self.subTest(path=path, key=key):
                document = copy.deepcopy(self.gate)
                target = document
                for part in path:
                    target = target[part]
                target[key] = value
                result = self.policy(yaml.safe_dump(document))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('TierB', result.stdout + result.stderr)
        document = copy.deepcopy(self.gate)
        document['jobs']['changelog-contract']['steps'].append({'run': 'echo extra'})
        self.assertEqual(self.policy(yaml.safe_dump(document)).returncode, 1)

    def test_unreviewed_release_expression_and_metered_selector_remain_rejected(self):
        release = self.generate('release-snapshot')
        self.assertNotEqual(self.policy(release.replace('vars.CI_RUNNER_DEFAULT', 'vars.UNKNOWN_RUNNER')).returncode, 0)
        self.assertNotEqual(self.policy(release.replace('ubuntu-24.04', 'macos-15')).returncode, 0)

    def test_explicit_untrusted_override_stays_on_normal_policy_path(self):
        self.assert_allowed(self.generate('pr-gate', '--untrusted-runner', 'self-hosted,isolated'))
        self.assertNotEqual(self.policy(self.generate('pr-gate', '--untrusted-runner', 'macos-15')).returncode, 0)
        result = subprocess.run([str(GENERATOR), 'pr-gate', self.sha, '--untrusted-runner', '${{ vars.CI_LANE_TRUSTED }}'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
