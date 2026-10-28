import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GUARD = Path(__file__).with_name('release-push-guard.sh').resolve()


class ReleasePushGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='release-push-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        self.repo = self.root / 'repo'
        self.git(self.root, 'init', '--bare', '--quiet', str(self.remote))
        self.git(self.root, 'clone', '--quiet', str(self.remote), str(self.repo))
        self.git(self.repo, 'config', 'user.name', 'Release test')
        self.git(self.repo, 'config', 'user.email', 'release-test@example.invalid')
        self.git(self.repo, 'switch', '-qc', 'main')
        (self.repo / 'file').write_text('previous')
        self.git(self.repo, 'add', 'file')
        self.git(self.repo, 'commit', '-qm', 'previous')
        self.previous = self.git(self.repo, 'rev-parse', 'HEAD').stdout.strip()
        (self.repo / 'file').write_text('dispatch')
        self.git(self.repo, 'commit', '-qam', 'dispatch')
        self.dispatch = self.git(self.repo, 'rev-parse', 'HEAD').stdout.strip()
        self.git(self.repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
        (self.repo / 'file').write_text('release')
        self.git(self.repo, 'commit', '-qam', 'release')
        self.release = self.git(self.repo, 'rev-parse', 'HEAD').stdout.strip()
        self.git(self.repo, 'tag', '-a', 'v1.0.0', '-m', 'release')
        self.git(self.remote, 'config', 'receive.denyNonFastForwards', 'true')

    def git(self, cwd, *args, check=True):
        return subprocess.run(['git', *args], cwd=cwd, check=check, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=os.environ | {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'})

    def push(self, expected=None, release=None, tag='v1.0.0', dispatch_ref='refs/heads/main'):
        return subprocess.run(['bash', str(GUARD), 'main', dispatch_ref, expected or self.dispatch,
                               release or self.release, tag], cwd=self.repo, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=os.environ | {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'})

    def remote_ref(self, ref):
        r = self.git(self.remote, 'rev-parse', '--verify', ref, check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    def test_actual_changelog_release_is_published_with_its_dispatch_parent(self):
        remote = self.root / 'engine-remote.git'
        repo = self.root / 'engine-repo'
        self.git(self.root, 'init', '--bare', '--quiet', str(remote))
        self.git(self.root, 'clone', '--quiet', str(remote), str(repo))
        self.git(repo, 'config', 'user.name', 'Release test')
        self.git(repo, 'config', 'user.email', 'release-test@example.invalid')
        self.git(repo, 'switch', '-qc', 'main')
        (repo / 'NEXT').mkdir()
        (repo / 'CHANGELOG').mkdir()
        fragment = '2026-10-28-issue-1942-test.md'
        (repo / 'NEXT' / fragment).write_text(
            '---\ndate: 2026-10-28\nissue: 1942\nimpact: patch\ntitle: Test release\n---\n\nTest.\n')
        (repo / 'CHANGELOG' / 'v1.0.0.md').write_text('# v1.0.0\n\nInitial.\n')
        self.git(repo, 'add', 'NEXT', 'CHANGELOG')
        self.git(repo, 'commit', '-qm', 'dispatch')
        dispatch = self.git(repo, 'rev-parse', 'HEAD').stdout.strip()
        self.git(repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
        subprocess.run([sys.executable, '-I', str(GUARD.with_name('changelog.py')), 'release',
                        '--repo-root', str(repo), '--version', 'v1.0.1', '--fragment', fragment],
                       check=True, capture_output=True, text=True)
        release = self.git(repo, 'rev-parse', 'HEAD').stdout.strip()
        result = subprocess.run(['bash', str(GUARD), 'main', 'refs/heads/main', dispatch,
                                 release, 'v1.0.1'], cwd=repo, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(remote, 'rev-parse', 'refs/heads/main').stdout.strip(), release)
        self.assertEqual(self.git(remote, 'rev-parse', 'refs/tags/v1.0.1^{commit}').stdout.strip(), release)
        self.assertFalse((repo / 'NEXT' / fragment).exists())
        self.assertTrue((repo / 'CHANGELOG' / 'v1.0.1.md').is_file())

    def test_matching_head_publishes_direct_fast_forward_and_annotated_tag(self):
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.release)
        self.assertEqual(self.remote_ref('refs/tags/v1.0.0^{commit}'), self.release)

    def test_push_does_not_follow_unrequested_tags_from_git_config(self):
        self.git(self.repo, 'config', 'push.followTags', 'true')
        self.git(self.repo, 'tag', '-a', 'unrequested', self.release, '-m', 'other')
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.release)
        self.assertIsNone(self.remote_ref('refs/tags/unrequested'))

    def test_rewound_head_rejects_both_branch_and_tag(self):
        self.git(self.remote, 'update-ref', 'refs/heads/main', self.previous, self.dispatch)
        result = self.push()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.previous)
        self.assertIsNone(self.remote_ref('refs/tags/v1.0.0'))

    def test_advanced_head_rejects_both_branch_and_tag(self):
        tree = self.git(self.repo, 'rev-parse', f'{self.dispatch}^{{tree}}').stdout.strip()
        advanced = self.git(self.repo, 'commit-tree', tree, '-p', self.dispatch, '-m', 'other').stdout.strip()
        self.git(self.repo, 'push', '-q', 'origin', f'{advanced}:refs/heads/main')
        result = self.push()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_ref('refs/heads/main'), advanced)
        self.assertIsNone(self.remote_ref('refs/tags/v1.0.0'))

    def test_conflicting_existing_tag_rejects_atomic_branch_update(self):
        self.git(self.remote, '-c', 'user.name=Release test', '-c', 'user.email=release-test@example.invalid',
                 'tag', '-a', 'v1.0.0', self.dispatch, '-m', 'existing')
        original = self.remote_ref('refs/tags/v1.0.0')
        result = self.push()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.dispatch)
        self.assertEqual(self.remote_ref('refs/tags/v1.0.0'), original)

    def test_annotated_tag_object_must_name_the_requested_tag(self):
        self.git(self.repo, 'tag', '-a', 'other-name', self.release, '-m', 'release')
        other = self.git(self.repo, 'rev-parse', 'refs/tags/other-name').stdout.strip()
        self.git(self.repo, 'update-ref', 'refs/tags/v1.0.0', other)
        result = self.push()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.dispatch)
        self.assertIsNone(self.remote_ref('refs/tags/v1.0.0'))

    def test_identical_existing_tag_is_idempotently_accepted_without_overwrite(self):
        self.git(self.repo, 'push', '-q', 'origin', 'refs/tags/v1.0.0:refs/tags/v1.0.0')
        original = self.remote_ref('refs/tags/v1.0.0')
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.release)
        self.assertEqual(self.remote_ref('refs/tags/v1.0.0'), original)

    def test_local_tag_move_after_validation_cannot_change_published_tag(self):
        self.git(self.repo, 'tag', '-a', 'alternate', self.previous, '-m', 'alternate')
        moved_tag = self.git(self.repo, 'rev-parse', 'refs/tags/alternate').stdout.strip()
        wrapper_dir = self.root / 'wrapper'
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / 'git'
        wrapper.write_text("#!/bin/sh\n"
                           "for arg in \"$@\"; do\n"
                           "  if [ \"$arg\" = push ]; then\n"
                           "    \"$REAL_GIT\" update-ref refs/tags/v1.0.0 \"$MOVED_TAG\" || exit 1\n"
                           "    break\n"
                           "  fi\n"
                           "done\n"
                           "exec \"$REAL_GIT\" \"$@\"\n")
        wrapper.chmod(0o755)
        env = os.environ | {'PATH': f'{wrapper_dir}:{os.environ["PATH"]}',
                            'REAL_GIT': shutil.which('git'), 'MOVED_TAG': moved_tag,
                            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        result = subprocess.run(['bash', str(GUARD), 'main', 'refs/heads/main',
                                 self.dispatch, self.release, 'v1.0.0'], cwd=self.repo,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.remote_ref('refs/tags/v1.0.0^{commit}'), self.release)
        self.assertEqual(self.git(self.repo, 'rev-parse', 'refs/tags/v1.0.0').stdout.strip(), moved_tag)

    def test_unrelated_release_commit_cannot_use_lease_to_rewrite_history(self):
        tree = self.git(self.repo, 'rev-parse', f'{self.release}^{{tree}}').stdout.strip()
        bad = self.git(self.repo, 'commit-tree', tree, '-p', self.previous, '-m', 'wrong parent').stdout.strip()
        self.git(self.repo, 'tag', '-a', 'bad', bad, '-m', 'bad')
        result = self.push(release=bad, tag='bad')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.dispatch)
        self.assertIsNone(self.remote_ref('refs/tags/bad'))

    def test_replacement_objects_cannot_forge_release_parent(self):
        tree = self.git(self.repo, 'rev-parse', f'{self.release}^{{tree}}').stdout.strip()
        bad = self.git(self.repo, 'commit-tree', tree, '-p', self.previous, '-m', 'wrong parent').stdout.strip()
        self.git(self.repo, 'tag', '-a', 'bad', bad, '-m', 'bad')
        self.git(self.repo, 'replace', bad, self.release)
        result = self.push(release=bad, tag='bad')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.dispatch)
        self.assertIsNone(self.remote_ref('refs/tags/bad'))

    def test_merge_commit_cannot_satisfy_single_parent_guard(self):
        tree = self.git(self.repo, 'rev-parse', f'{self.release}^{{tree}}').stdout.strip()
        merge = self.git(self.repo, 'commit-tree', tree, '-p', self.dispatch,
                         '-p', self.previous, '-m', 'merge').stdout.strip()
        self.git(self.repo, 'tag', '-a', 'merge', merge, '-m', 'merge')
        result = self.push(release=merge, tag='merge')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.dispatch)
        self.assertIsNone(self.remote_ref('refs/tags/merge'))

    def test_lightweight_and_mismatched_tags_are_rejected(self):
        self.git(self.repo, 'tag', 'lightweight', self.release)
        self.git(self.repo, 'tag', '-a', 'wrong', self.dispatch, '-m', 'wrong')
        for tag in ('lightweight', 'wrong'):
            with self.subTest(tag=tag):
                result = self.push(tag=tag)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.remote_ref('refs/heads/main'), self.dispatch)
                self.assertIsNone(self.remote_ref(f'refs/tags/{tag}'))

    def test_dispatch_ref_must_be_the_target_default_branch(self):
        result = self.push(dispatch_ref='refs/heads/other')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.dispatch)
        self.assertIsNone(self.remote_ref('refs/tags/v1.0.0'))

    def test_malformed_expected_sha_fails_without_remote_write(self):
        result = self.push(expected='not-a-full-object-id')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.dispatch)
        self.assertIsNone(self.remote_ref('refs/tags/v1.0.0'))

    def test_aba_to_same_sha_is_not_observable_by_a_ref_lease(self):
        self.git(self.remote, 'update-ref', 'refs/heads/main', self.previous, self.dispatch)
        self.git(self.remote, 'update-ref', 'refs/heads/main', self.dispatch, self.previous)
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.remote_ref('refs/heads/main'), self.release)


if __name__ == '__main__':
    unittest.main()
