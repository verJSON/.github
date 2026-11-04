#!/usr/bin/env python3
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/ai-review-merge.yml').read_text())
STEPS = {step.get('id'): step for step in WORKFLOW['jobs']['gate']['steps']}
HEAD = 'a' * 40


def fallback_allowed(step, *, cancelled=False, usable='false', reserved='true'):
    values = {
        'inputs.explicit_rereview': False,
        'needs.preflight.outputs.lane': 'ai',
        'needs.preflight.outputs.provider': 'deepseek',
        'steps.rereview.outputs.skip_model': 'false',
        'steps.reserve_1.outputs.allowed': 'true',
        'steps.reserve_2.outputs.allowed': reserved,
        'steps.verdict_1.outputs.usable': usable,
    }
    expression = step['if'].removeprefix('${{').removesuffix('}}').strip()
    expression = expression.replace('always()', 'True').replace('cancelled()', str(cancelled))
    for name, value in values.items():
        expression = expression.replace(name, repr(value))
    expression = re.sub(r'(?<![\w\'"])true(?![\w\'"])', 'True', expression)
    expression = re.sub(r'(?<![\w\'"])false(?![\w\'"])', 'False', expression)
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    expression = re.sub(r'!(?!=)', 'not ', expression)
    return eval(expression, {'__builtins__': {}}, {})


class DeepSeekCancellationTest(unittest.TestCase):
    def test_cancelled_run_neither_reserves_nor_invokes_fallback(self):
        for name in ('reserve_2', 'deepseek_fallback'):
            with self.subTest(step=name):
                self.assertFalse(fallback_allowed(STEPS[name], cancelled=True))

    def test_active_run_can_fallback_after_failed_or_inconclusive_primary(self):
        for name in ('reserve_2', 'deepseek_fallback'):
            with self.subTest(step=name):
                self.assertTrue(fallback_allowed(STEPS[name]))

    def test_usable_primary_and_denied_reservation_prevent_another_call(self):
        for name in ('reserve_2', 'deepseek_fallback'):
            with self.subTest(step=name):
                self.assertFalse(fallback_allowed(STEPS[name], usable='true'))
        self.assertFalse(fallback_allowed(STEPS['deepseek_fallback'], reserved='false'))

    def test_each_provider_pass_has_a_five_minute_wall_clock_limit(self):
        for name in ('deepseek_primary', 'deepseek_fallback'):
            with self.subTest(step=name):
                self.assertEqual(STEPS[name].get('timeout-minutes'), 5)
                self.assertEqual(STEPS[name]['env'].get('GH_TOKEN'), '${{ github.token }}')

    def run_provider(self, step, current_head=HEAD, lookup_status=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_path = root / 'bin'
            bin_path.mkdir()
            (bin_path / 'gh').write_text(
                '#!/bin/sh\nprintf "lookup\\n" >> "$EVENTS"\n'
                'printf "%s\\n" "$CURRENT_HEAD"\nexit "$LOOKUP_STATUS"\n'
            )
            (bin_path / 'gh').chmod(0o755)
            (root / 'deepseek-review.py').write_text(
                'import os\nfrom pathlib import Path\n'
                'assert "GH_TOKEN" not in os.environ\n'
                'with Path(os.environ["EVENTS"]).open("a") as events:\n'
                '    events.write("provider\\n")\n'
            )
            events = root / 'events'
            env = {
                'PATH': str(bin_path) + os.pathsep + os.defpath,
                'RUNNER_TEMP': str(root), 'EVENTS': str(events),
                'PROMPT_FILE': str(root / 'prompt'), 'REVIEW_PROMPT': 'review',
                'CURRENT_HEAD': current_head, 'LOOKUP_STATUS': str(lookup_status),
                'EXPECTED_HEAD_SHA': HEAD, 'TARGET_REPO': 'example/repo', 'PR_NUMBER': '7',
                'GH_TOKEN': 'mock-read-token',
            }
            result = subprocess.run(['bash', '-c', step['run']], env=env, text=True, capture_output=True)
            return result, events.read_text().splitlines() if events.exists() else []

    def test_still_current_head_invokes_provider_after_github_lookup(self):
        for name in ('deepseek_primary', 'deepseek_fallback'):
            with self.subTest(step=name):
                result, events = self.run_provider(STEPS[name])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(events, ['lookup', 'provider'])

    def test_head_changed_after_reservation_never_invokes_provider(self):
        for name in ('deepseek_primary', 'deepseek_fallback'):
            with self.subTest(step=name):
                result, events = self.run_provider(STEPS[name], current_head='b' * 40)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(events, ['lookup'])
                self.assertIn('superseded', result.stdout)

    def test_failed_head_lookup_never_invokes_provider(self):
        for name in ('deepseek_primary', 'deepseek_fallback'):
            with self.subTest(step=name):
                result, events = self.run_provider(STEPS[name], lookup_status=1)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('::error::phase=model result=head_unverified', result.stdout)
                self.assertEqual(events, ['lookup'])


if __name__ == '__main__':
    unittest.main()
