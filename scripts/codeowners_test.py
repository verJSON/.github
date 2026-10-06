#!/usr/bin/env python3
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('owners', ROOT / 'scripts/codeowners.py')
owners = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(owners)


class CaseInsensitivePath:
    def __init__(self, files, path=''):
        self.files, self.path = files, path

    def __truediv__(self, name):
        return CaseInsensitivePath(self.files, '/'.join(filter(None, (self.path, name))))

    def is_symlink(self):
        return False

    def is_file(self):
        return self.path.casefold() in {name.casefold() for name in self.files}

    def exists(self):
        return self.is_file()

    def read_bytes(self):
        return next(value for name, value in self.files.items() if name.casefold() == self.path.casefold())

    def iterdir(self):
        prefix = self.path + '/' if self.path else ''
        return [SimpleNamespace(name=name[len(prefix):].split('/')[0])
                for name in self.files if name.casefold().startswith(prefix.casefold())]


class CodeownersTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / '.github').mkdir()
        self.target = self.root / '.github/CODEOWNERS'
        self.target.write_text(owners.CONTENT)

    def test_exact_generated_owner_covers_every_path_including_itself(self):
        owners.check(self.root)
        rules = [line for line in owners.CONTENT.splitlines() if line and not line.startswith('#')]
        self.assertEqual(rules, ['* @verJSON/devs'])

    def test_missing_wrong_owner_exceptions_and_generated_header_drift_fail(self):
        self.target.unlink()
        with self.assertRaises(owners.OwnershipError):
            owners.check(self.root)
        for content in (owners.CONTENT.replace('@verJSON/devs', '@Verjson/other'),
                        owners.CONTENT + '/scripts/ @Verjson/other\n',
                        owners.CONTENT + '/.github/CODEOWNERS\n',
                        '* @Verjson/devs\n', owners.CONTENT.replace('\n', '\r\n')):
            with self.subTest(content=content):
                self.target.write_bytes(content.encode())
                with self.assertRaises(owners.OwnershipError):
                    owners.check(self.root)

    def test_generator_embedded_copy_matches_the_reviewed_source(self):
        # gen-changelog-caller.sh embeds the bytes (the audit materializes only the
        # generator at a pin); config/codeowners/CODEOWNERS is the reviewed source.
        # The generator resolves the changelog engine digest at the given commit
        # before any mode runs, so the pin must be a real commit: HEAD of this
        # checkout, or the commit CI materialized when no repository is present.
        head = subprocess.run(
            ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True,
        )
        pin = head.stdout.strip() if head.returncode == 0 else os.environ.get('GITHUB_SHA', '')
        self.assertRegex(pin, r'^[0-9a-f]{40}$', 'no resolvable contract commit for the drift check')
        emitted = subprocess.run(
            ['bash', str(ROOT / 'scripts/gen-changelog-caller.sh'), 'codeowners', pin],
            check=True, capture_output=True, text=True,
        ).stdout
        self.assertEqual(emitted, owners.CONTENT)

    def test_competing_fallback_locations_fail(self):
        for path in ('CODEOWNERS', 'docs/CODEOWNERS'):
            candidate = self.root / path
            candidate.parent.mkdir(exist_ok=True)
            candidate.write_text('* @Verjson/other\n')
            with self.assertRaises(owners.OwnershipError):
                owners.check(self.root)
            candidate.unlink()

    def test_symlinked_owner_file_or_directory_fails(self):
        self.target.unlink()
        alternate = self.root / 'alternate'
        alternate.write_text(owners.CONTENT)
        self.target.symlink_to(alternate)
        with self.assertRaises(owners.OwnershipError):
            owners.check(self.root)
        self.target.unlink()
        (self.root / '.github').rmdir()
        (self.root / '.github').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(owners.OwnershipError):
            owners.check(self.root)

    def test_case_insensitive_lookup_requires_exact_directory_and_filename_spelling(self):
        owners.check(CaseInsensitivePath({'.github/CODEOWNERS': owners.CONTENT.encode()}))
        for path in ('.Github/CODEOWNERS', '.github/CodeOwners', '.Github/CodeOwners'):
            with self.subTest(path=path), self.assertRaises(owners.OwnershipError):
                owners.check(CaseInsensitivePath({path: owners.CONTENT.encode()}))

    def test_case_insensitive_fallbacks_remain_rejected_without_scanning_other_locations(self):
        for fallback in ('CodeOwners', 'Docs/codeowners'):
            files = {'.github/CODEOWNERS': owners.CONTENT.encode(), fallback: b'* @Verjson/other\n'}
            with self.subTest(fallback=fallback), self.assertRaises(owners.OwnershipError):
                owners.check(CaseInsensitivePath(files))
        owners.check(CaseInsensitivePath({'.github/CODEOWNERS': owners.CONTENT.encode(),
                                          'examples/CODEOWNERS': b'example only\n'}))

    def test_canonical_generator_and_self_adoption_match_exactly(self):
        ref = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        generated = subprocess.check_output(['bash', str(ROOT / 'scripts/gen-changelog-caller.sh'), 'codeowners', ref], cwd=ROOT)
        self.assertEqual(generated, owners.CONTENT.encode())
        self.assertEqual((ROOT / '.github/CODEOWNERS').read_bytes(), generated)
        owners.check(ROOT)


if __name__ == '__main__':
    unittest.main()
