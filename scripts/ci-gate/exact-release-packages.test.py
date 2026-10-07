#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
SHA = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
GENERATOR = ROOT / 'scripts/gen-changelog-caller.sh'


def generate(mode, *args):
    return subprocess.run(['bash', str(GENERATOR), mode, SHA, *args], cwd=ROOT,
                          capture_output=True, text=True)


class ExactReleasePackagesTests(unittest.TestCase):
    def test_default_additive_and_exact_selections_drive_publication_and_stamping(self):
        cases = [([], ['.']), (['--package-dir', 'compat'], ['.', 'compat']),
                 (['--only-package-dir', 'packages/cli-schema'], ['packages/cli-schema']),
                 (['--only-package-dir', '.', '--only-package-dir', 'compat'], ['.', 'compat']),
                 (['--only-package-dir', 'schema', '--only-package-dir', 'compat'], ['schema', 'compat'])]
        for args, expected in cases:
            with self.subTest(args=args):
                result = generate('release-node', *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                jobs = yaml.safe_load(result.stdout)['jobs']
                self.assertEqual(json.loads(jobs['publish']['with']['package-dirs']), expected)
                stamp = next(step['run'] for step in jobs['verify']['steps']
                             if step.get('name') == 'Stamp the dispatched package versions')
                with tempfile.TemporaryDirectory() as directory:
                    binary = Path(directory) / 'npm'
                    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$3" >> "$CALLS"\n')
                    binary.chmod(0o755)
                    calls = Path(directory) / 'calls'
                    env = dict(os.environ, PATH=directory + os.pathsep + os.defpath,
                               PACKAGE_VERSION='1.2.3', CALLS=str(calls))
                    subprocess.run(['bash', '-eu', '-c', stamp], env=env, check=True)
                    self.assertEqual(calls.read_text().splitlines(), expected)

    def test_exact_selection_reaches_contract_expectations_and_reproduction_command(self):
        for mode in ('release-node', 'release-snapshot', 'release-artifact', 'contract-test'):
            args = ['--only-package-dir', 'packages/cli-schema']
            if mode == 'release-artifact':
                args += ['--build-runner', 'ubuntu-24.04']
            with self.subTest(mode=mode):
                result = generate(mode, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                if mode == 'contract-test':
                    self.assertIn("GENERATOR_RELEASE_PACKAGE_DIRS_JSON='[\"packages/cli-schema\"]'", result.stdout)
                    self.assertIn("GENERATOR_RELEASE_PACKAGE_DIRS_SHELL='packages/cli-schema'", result.stdout)
                else:
                    self.assertIn('--only-package-dir packages/cli-schema', result.stdout)
                    self.assertIn('package_dirs=(packages/cli-schema)', result.stdout)
                    self.assertNotIn('package_dirs=(. ', result.stdout)

    def test_invalid_empty_duplicate_and_mixed_selections_produce_no_artifact(self):
        cases = [['--only-package-dir'], ['--only-package-dir', '',],
                 ['--only-package-dir', 'schema', '--only-package-dir', 'schema'],
                 ['--only-package-dir', 'schema', '--package-dir', 'compat'],
                 ['--package-dir', 'compat', '--only-package-dir', 'schema']]
        cases += [['--only-package-dir', path] for path in
                  ('../schema', '/schema', './schema', 'a/../b', 'a/./b', 'a//b',
                   'schema/', '-schema', 'a b', 'a\nb', 'a\\b', "a'", '$(pwd)')]
        for args in cases:
            with self.subTest(args=args):
                result = generate('release-node', *args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
        self.assertEqual(generate('renderer', '--only-package-dir', 'schema').returncode, 2)


if __name__ == '__main__':
    unittest.main()
