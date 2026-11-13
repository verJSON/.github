#!/usr/bin/env python3
import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import changelog
import selection_evidence as evidence


class SelectionEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.contract = self.root / 'contract'
        for repo in (self.source, self.contract):
            repo.mkdir()
            self.git(repo, 'init', '-q')
            self.git(repo, 'config', 'user.name', 'Fixture')
            self.git(repo, 'config', 'user.email', 'fixture@example.invalid')
        self.write(self.contract, 'scripts/changelog.py', Path(changelog.__file__).read_bytes())
        self.contract_sha = self.commit(self.contract)
        self.write(self.source, 'CHANGELOG/v1.0.0.md', b'Initial release\n')
        self.fragment('2026-10-29-issue-1-first.md', 1, 'patch', 'Original body')
        self.first = self.commit(self.source)

    def git(self, repo, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT='0')
        return subprocess.run(['git', *args], cwd=repo, env=env, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()

    def write(self, repo, path, data):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def fragment(self, name, issue, impact, body, component=None):
        component_line = f'component: {component}\n' if component else ''
        data = (f'---\ndate: 2026-10-29\nissue: {issue}\nimpact: {impact}\n'
                f'title: Fixture\n{component_line}---\n\n{body}\n').encode()
        self.write(self.source, f'NEXT/{name}', data)

    def commit(self, repo):
        self.git(repo, 'add', '.')
        self.git(repo, 'commit', '-qm', 'fixture')
        return self.git(repo, 'rev-parse', 'HEAD')

    def build(self, source=None, contract=None):
        return evidence.build_selection_evidence(self.source, source or self.first,
                                                 self.contract, contract or self.contract_sha)

    def test_builds_deterministic_evidence_for_complete_unscoped_selection(self):
        result = self.build()
        self.assertEqual(result, self.build())
        self.assertEqual('v1.0.1', result['version'])
        self.assertEqual(['issue:1'], [f['identity'] for f in result['fragments']])
        self.assertEqual(['CHANGELOG/v1.0.0.md'], [f['path'] for f in result['history']])
        self.assertEqual(64, len(evidence.verify_selection_evidence(
            result, self.source, self.first, self.contract, self.contract_sha)))

    def test_changed_fragment_bytes_with_same_identity_are_not_interchangeable(self):
        original = self.build()
        self.fragment('2026-10-29-issue-1-first.md', 1, 'patch', 'Changed body')
        new_sha = self.commit(self.source)
        changed = self.build(new_sha)
        self.assertEqual(original['selectionDigest'], changed['selectionDigest'])
        self.assertNotEqual(original['fragments'][0]['sha256'], changed['fragments'][0]['sha256'])
        with self.assertRaises(evidence.SelectionEvidenceError):
            evidence.verify_selection_evidence(original, self.source, new_sha,
                                               self.contract, self.contract_sha)

    def test_future_major_fragment_is_included_and_changes_derived_version(self):
        self.fragment('2026-10-29-issue-2-second.md', 2, 'major', 'New work')
        result = self.build(self.commit(self.source))
        self.assertEqual('v2.0.0', result['version'])
        self.assertEqual(['issue:1', 'issue:2'], [f['identity'] for f in result['fragments']])
        omitted = copy.deepcopy(result)
        omitted['fragments'].pop()
        with self.assertRaises(evidence.SelectionEvidenceError):
            evidence.verify_selection_evidence(omitted, self.source, result['sourceCommit'],
                                               self.contract, self.contract_sha)

    def test_history_change_and_stale_source_are_rejected(self):
        original = self.build()
        self.write(self.source, 'CHANGELOG/v1.0.0.md', b'Changed release history\n')
        new_sha = self.commit(self.source)
        changed = self.build(new_sha)
        self.assertNotEqual(original['history'], changed['history'])
        with self.assertRaises(evidence.SelectionEvidenceError):
            evidence.verify_selection_evidence(original, self.source, new_sha,
                                               self.contract, self.contract_sha)

    def test_rejects_scoped_fragments_and_missing_baseline(self):
        self.fragment('2026-10-29-issue-2-second.md', 2, 'patch', 'Scoped', 'node')
        with self.assertRaisesRegex(evidence.SelectionEvidenceError, 'scoped'):
            self.build(self.commit(self.source))
        (self.source / 'NEXT/2026-10-29-issue-2-second.md').unlink()
        (self.source / 'CHANGELOG/v1.0.0.md').unlink()
        self.git(self.source, 'add', '-u')
        with self.assertRaises(evidence.SelectionEvidenceError):
            self.build(self.commit(self.source))

    def test_rejects_json_type_substitution_even_when_python_values_compare_equal(self):
        for replacement in (True, 1.0):
            with self.subTest(replacement=replacement):
                original = self.build()
                original['schemaVersion'] = replacement
                with self.assertRaises(evidence.SelectionEvidenceError):
                    evidence.verify_selection_evidence(original, self.source, self.first,
                                                       self.contract, self.contract_sha)

        class ForgedDict(dict):
            def __eq__(self, other):
                return True

        with self.assertRaises(evidence.SelectionEvidenceError):
            evidence.verify_selection_evidence(ForgedDict(self.build()), self.source, self.first,
                                               self.contract, self.contract_sha)

    def test_rejects_oversized_observed_fields_without_serializing_them(self):
        for field, replacement in (('fragments', [None] * 100000),
                                   ('sourceCommit', 'x' * 1000000)):
            with self.subTest(field=field):
                original = self.build()
                original[field] = replacement
                with mock.patch.object(evidence.json, 'dumps', wraps=evidence.json.dumps) as dumps:
                    with self.assertRaises(evidence.SelectionEvidenceError):
                        evidence.verify_selection_evidence(original, self.source, self.first,
                                                           self.contract, self.contract_sha)
                    self.assertFalse(any(call.args and call.args[0] is original
                                         for call in dumps.call_args_list))

    def test_rejects_unknown_fields_and_contract_engine_substitution(self):
        original = self.build()
        original['unexpected'] = True
        with self.assertRaises(evidence.SelectionEvidenceError):
            evidence.verify_selection_evidence(original, self.source, self.first,
                                               self.contract, self.contract_sha)
        self.write(self.contract, 'scripts/changelog.py', b'not the selected engine\n')
        changed_contract = self.commit(self.contract)
        with self.assertRaisesRegex(evidence.SelectionEvidenceError, 'engine differs'):
            self.build(contract=changed_contract)

    def test_rejects_promisor_repositories_before_reading_objects(self):
        self.git(self.source, 'config', 'remote.origin.promisor', 'true')
        with self.assertRaisesRegex(evidence.SelectionEvidenceError, 'partial'):
            self.build()

    def test_rejects_worktree_promisor_configuration(self):
        self.git(self.source, 'config', 'extensions.worktreeConfig', 'true')
        self.git(self.source, 'config', '--worktree', 'remote.origin.promisor', 'true')
        with self.assertRaisesRegex(evidence.SelectionEvidenceError, 'partial'):
            self.build()

    def test_version_uses_recorded_history_and_rejects_substitution(self):
        self.write(self.source, 'CHANGELOG/v1.2.0.md', b'Newer release\n')
        later = self.commit(self.source)
        result = self.build(later)
        self.assertEqual('v1.2.1', result['version'])
        tampered = copy.deepcopy(result)
        tampered['history'].pop()
        with self.assertRaises(evidence.SelectionEvidenceError):
            evidence.verify_selection_evidence(tampered, self.source, later,
                                               self.contract, self.contract_sha)

    def test_uncommitted_changes_do_not_change_selected_git_objects(self):
        original = self.build()
        self.fragment('2026-10-29-issue-1-first.md', 1, 'major', 'Uncommitted')
        self.assertEqual(original, self.build())

    def test_rejects_nonportable_paths_before_materializing_them(self):
        for name in ('foo\\..\\escape.md', 'CON.md', 'bad:.md', 'trailing..'):
            with self.subTest(name=name):
                target = self.source / 'NEXT' / name
                target.write_text('not a fragment')
                sha = self.commit(self.source)
                with self.assertRaisesRegex(evidence.SelectionEvidenceError, 'portable'):
                    self.build(sha)
                target.unlink()
                self.git(self.source, 'add', '-u')
                self.commit(self.source)

    def test_bounded_tree_rejects_excess_output(self):
        fakebin = self.root / 'fakebin'
        fakebin.mkdir()
        git = fakebin / 'git'
        git.write_text('#!/bin/sh\nhead -c 4194305 /dev/zero\n')
        git.chmod(0o755)
        with mock.patch.dict(os.environ, {'PATH': f'{fakebin}:/usr/bin:/bin'}):
            with self.assertRaisesRegex(evidence.SelectionEvidenceError, 'exceeded limit'):
                evidence._bounded_tree(self.source, self.first)

    def test_rejects_symlinks_and_malformed_commit_ids(self):
        (self.source / 'NEXT/2026-10-29-issue-1-first.md').unlink()
        (self.source / 'NEXT/2026-10-29-issue-1-first.md').symlink_to('../CHANGELOG/v1.0.0.md')
        with self.assertRaisesRegex(evidence.SelectionEvidenceError, 'regular file'):
            self.build(self.commit(self.source))
        with self.assertRaisesRegex(evidence.SelectionEvidenceError, 'full lowercase'):
            self.build('HEAD')


if __name__ == '__main__':
    unittest.main()
