#!/usr/bin/env python3

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("changelog.py")
SPEC = importlib.util.spec_from_file_location("changelog", MODULE_PATH)
assert SPEC and SPEC.loader
changelog = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = changelog
SPEC.loader.exec_module(changelog)


def run(root: Path, *args: str) -> str:
    return subprocess.run(
        args, cwd=root, check=True, text=True, stdout=subprocess.PIPE
    ).stdout.strip()


class PreContractMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        run(self.root, "git", "init", "-q")
        run(self.root, "git", "config", "user.name", "Test")
        run(self.root, "git", "config", "user.email", "test@example.com")
        self.source = self.root / "CHANGELOG" / "1.0.0.md"
        self.source.parent.mkdir()
        self.content = b"pre-contract history\n"
        self.source.write_bytes(self.content)
        self.commit("historical snapshot")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def commit(self, message: str) -> str:
        run(self.root, "git", "add", "-A")
        run(self.root, "git", "commit", "-qm", message)
        return run(self.root, "git", "rev-parse", "HEAD")

    def entry(self, **changes: str) -> dict[str, str]:
        entry = {
            "source": "CHANGELOG/1.0.0.md",
            "version": "1.0.0",
            "sha256": hashlib.sha256(self.content).hexdigest(),
            "destination": "docs/changelog/pre-contract/1.0.0.md",
        }
        entry.update(changes)
        return entry

    def write_permit(
        self, migrations: list[dict[str, str]] | None = None, **extra: object
    ) -> None:
        path = self.root / changelog.PRE_CONTRACT_MIGRATIONS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": 1,
            "migrations": [self.entry()] if migrations is None else migrations,
        }
        document.update(extra)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    def authorize(self) -> str:
        self.write_permit()
        return self.commit("authorize exact migration")

    def move(self, content: bytes | None = None) -> str:
        destination = self.root / "docs/changelog/pre-contract/1.0.0.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.content if content is None else content)
        self.source.unlink()
        return self.commit("move historical snapshot")

    def assert_rejected(self, base: str, head: str, message: str) -> None:
        with self.assertRaisesRegex(changelog.ChangelogError, message):
            changelog.check_pr(self.root, base, head)

    def test_base_authorized_exact_byte_move_is_accepted(self) -> None:
        base = self.authorize()
        head = self.move()
        changelog.check_pr(self.root, base, head)

    def test_pull_request_cannot_self_authorize(self) -> None:
        base = run(self.root, "git", "rev-parse", "HEAD")
        self.write_permit()
        head = self.move()
        self.assert_rejected(base, head, "ordinary pull requests cannot edit")

    def test_same_pr_cannot_widen_an_existing_permit(self) -> None:
        base = self.authorize()
        document = json.loads(
            (self.root / changelog.PRE_CONTRACT_MIGRATIONS_PATH).read_text()
        )
        document["migrations"].append(
            self.entry(
                source="CHANGELOG/2.0.0.md",
                version="2.0.0",
                destination="docs/changelog/pre-contract/2.0.0.md",
            )
        )
        (self.root / changelog.PRE_CONTRACT_MIGRATIONS_PATH).write_text(
            json.dumps(document) + "\n"
        )
        head = self.move()
        self.assert_rejected(base, head, "cannot widen its own")

    def test_edited_content_is_rejected(self) -> None:
        base = self.authorize()
        head = self.move(b"attacker replacement\n")
        self.assert_rejected(base, head, "not byte-identical")

    def test_file_mode_change_is_rejected(self) -> None:
        base = self.authorize()
        destination = self.root / "docs/changelog/pre-contract/1.0.0.md"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(self.content)
        destination.chmod(0o755)
        self.source.unlink()
        head = self.commit("change mode while moving")
        self.assert_rejected(base, head, "preserve a regular file mode")

    def test_symlink_destination_is_rejected(self) -> None:
        self.source.write_text("archive-target.md")
        self.content = b"archive-target.md"
        self.commit("make digest fixture")
        base = self.authorize()
        destination = self.root / "docs/changelog/pre-contract/1.0.0.md"
        destination.parent.mkdir(parents=True)
        destination.symlink_to("archive-target.md")
        self.source.unlink()
        head = self.commit("replace archive with symlink")
        self.assert_rejected(base, head, "preserve a regular file mode")

    def test_wrong_digest_is_rejected(self) -> None:
        self.write_permit([self.entry(sha256="0" * 64)])
        base = self.commit("authorize wrong digest")
        head = self.move()
        self.assert_rejected(base, head, "digest does not match")

    def test_deletion_without_destination_is_rejected(self) -> None:
        base = self.authorize()
        self.source.unlink()
        head = self.commit("delete only")
        self.assert_rejected(base, head, "must delete its source and add its destination")

    def test_copy_without_source_deletion_is_rejected(self) -> None:
        base = self.authorize()
        destination = self.root / "docs/changelog/pre-contract/1.0.0.md"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(self.content)
        head = self.commit("copy only")
        self.assert_rejected(base, head, "must delete its source and add its destination")

    def test_existing_destination_cannot_be_overwritten(self) -> None:
        destination = self.root / "docs/changelog/pre-contract/1.0.0.md"
        destination.parent.mkdir(parents=True)
        destination.write_text("existing\n")
        self.write_permit()
        base = self.commit("authorize with occupied destination")
        destination.write_bytes(self.content)
        self.source.unlink()
        head = self.commit("overwrite destination")
        self.assert_rejected(base, head, "must delete its source and add its destination")

    def test_tagged_snapshot_cannot_move(self) -> None:
        base = self.authorize()
        run(self.root, "git", "tag", "v1.0.0", base)
        head = self.move()
        self.assert_rejected(base, head, "snapshot present in a tag")

    def test_other_immutable_snapshot_cannot_change_with_migration(self) -> None:
        other = self.root / "CHANGELOG" / "2.0.0.md"
        other.write_text("other release\n")
        self.commit("other release")
        base = self.authorize()
        other.write_text("rewritten\n")
        head = self.move()
        self.assert_rejected(base, head, "cannot change other release snapshots")

    def test_malformed_or_attacker_selected_permits_fail_closed(self) -> None:
        unsafe = "pre-contract migration destination is not a safe path"
        attacks = (
            ({"schema_version": 1, "migrations": [self.entry(destination="../escape.md")]}, unsafe),
            ({"schema_version": 1, "migrations": [self.entry(destination=".git/config")]}, unsafe),
            ({"schema_version": 1, "migrations": [self.entry(destination="NEXT/archive.md")]},
             "pre-contract migration destination is active changelog state"),
            ({"schema_version": 1, "migrations": [self.entry(source="CHANGELOG/other.md")]},
             "pre-contract migration source does not match its version"),
            ({"schema_version": 1, "migrations": [dict(self.entry(), extra="write")]},
             "pre-contract migration entry has unsupported fields"),
            ({"schema_version": 1, "migrations": [self.entry(sha256="A" * 64)]},
             "pre-contract migration sha256 is invalid"),
        )
        for document, reason in attacks:
            with self.subTest(document=document):
                with self.assertRaisesRegex(changelog.ChangelogError, "^" + re.escape(reason) + "$"):
                    changelog.parse_pre_contract_migrations(json.dumps(document).encode())

    def test_schema_version_requires_an_exact_json_integer(self) -> None:
        for value in (True, False, 1.0, "1", None):
            with self.subTest(value=value):
                document = {"schema_version": value, "migrations": []}
                with self.assertRaisesRegex(changelog.ChangelogError, "unsupported schema"):
                    changelog.parse_pre_contract_migrations(json.dumps(document).encode())

    def test_shallow_repository_cannot_claim_complete_tag_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            clone = Path(tmpdir) / "shallow"
            subprocess.run(
                ["git", "clone", "-q", "--depth", "1", self.root.as_uri(), str(clone)],
                check=True,
            )
            with self.assertRaisesRegex(
                changelog.ChangelogError, "complete non-shallow tag checkout"
            ):
                changelog.require_complete_tag_visibility(clone)

    def test_malformed_base_permit_blocks_a_matching_move(self) -> None:
        path = self.root / changelog.PRE_CONTRACT_MIGRATIONS_PATH
        path.parent.mkdir(parents=True)
        path.write_text("not json\n")
        base = self.commit("malformed permit")
        head = self.move()
        self.assert_rejected(base, head, "not valid UTF-8 JSON")

    def test_reviewed_permit_can_only_grow_in_a_separate_pr(self) -> None:
        base = self.authorize()
        first = self.entry()
        second = self.entry(
            source="CHANGELOG/2.0.0.md",
            version="2.0.0",
            sha256="1" * 64,
            destination="docs/changelog/pre-contract/2.0.0.md",
        )
        self.write_permit([first, second])
        fragment = self.root / "NEXT" / "2026-09-23-issue-1455-permit.md"
        fragment.parent.mkdir(parents=True, exist_ok=True)
        fragment.write_text(
            "---\n"
            "date: 2026-09-23\n"
            "issue: 1455\n"
            "title: Authorize a future snapshot migration\n"
            "impact: patch\n"
            "---\n\n"
            "Record the reviewed migration permit.\n",
            encoding="utf-8",
        )
        head = self.commit("append future permit")
        changelog.check_pr(self.root, base, head)

    def test_permits_are_append_only_and_cannot_be_deleted(self) -> None:
        base = self.authorize()
        (self.root / changelog.PRE_CONTRACT_MIGRATIONS_PATH).unlink()
        head = self.commit("delete permit")
        self.assert_rejected(base, head, "permit is append-only")

    def test_multiple_permitted_destinations_cannot_hide_each_other(self) -> None:
        second_content = b"second archive\n"
        second_source = self.root / "CHANGELOG" / "2.0.0.md"
        second_source.write_bytes(second_content)
        second_destination = "docs/changelog/pre-contract/2.0.0.md"
        self.write_permit(
            [
                self.entry(),
                self.entry(
                    source="CHANGELOG/2.0.0.md",
                    version="2.0.0",
                    sha256=hashlib.sha256(second_content).hexdigest(),
                    destination=second_destination,
                ),
            ]
        )
        base = self.commit("authorize two future migrations")
        first_destination = self.root / self.entry()["destination"]
        second_destination_path = self.root / second_destination
        first_destination.parent.mkdir(parents=True)
        first_destination.write_bytes(self.content)
        second_destination_path.write_bytes(second_content)
        self.source.unlink()
        second_source.unlink()
        head = self.commit("attempt two moves")
        self.assert_rejected(base, head, "only one pre-contract migration permit")

    def test_ordinary_release_snapshot_edits_remain_rejected(self) -> None:
        base = run(self.root, "git", "rev-parse", "HEAD")
        self.source.write_text("rewritten\n")
        head = self.commit("rewrite release")
        self.assert_rejected(base, head, "ordinary pull requests cannot edit")


if __name__ == "__main__":
    unittest.main()
