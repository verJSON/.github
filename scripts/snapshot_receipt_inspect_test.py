#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch

from snapshot_receipt_inspect import ReceiptError, decode_receipt, inspect_snapshot_receipt


class SnapshotReceiptInspectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "source").write_text("source\n")
        self.git("add", "source")
        self.git("commit", "-qm", "source")
        source = self.git("rev-parse", "HEAD")
        (self.repo / "CHANGELOG").mkdir()
        (self.repo / "CHANGELOG/v1.2.3.md").write_bytes(b"release notes\n")
        self.git("add", "CHANGELOG")
        self.git("commit", "-qm", "release")
        commit = self.git("rev-parse", "HEAD")
        self.git("tag", "-a", "v1.2.3", "-m", "release", commit)
        self.receipt = {
            "schemaVersion": 1, "kind": "snapshot-receipt-prototype", "repositoryId": "123",
            "producerWorkflow": "provider/repository/workflow@immutable", "runId": "456", "runAttempt": "1",
            "sourceCommit": source, "version": "v1.2.3", "tagRef": "refs/tags/v1.2.3",
            "tagObject": self.git("rev-parse", "refs/tags/v1.2.3"), "snapshotCommit": commit,
            "snapshotTree": self.git("rev-parse", "HEAD^{tree}"),
            "changelogSha256": "sha256:" + hashlib.sha256(b"release notes\n").hexdigest(),
        }
        self.expected = {key: self.receipt[key] for key in ("repositoryId", "producerWorkflow", "runId", "runAttempt", "sourceCommit", "version")}

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, env=self.env,
                              capture_output=True, text=True).stdout.strip()

    def inspect(self, receipt=None, expected=None):
        value = self.receipt if receipt is None else receipt
        return inspect_snapshot_receipt(json.dumps(value).encode(), expected=self.expected if expected is None else expected, repository=self.repo)

    def test_matching_actual_tag_commit_tree_and_snapshot_are_consistent(self):
        self.assertIsNone(self.inspect())

    def test_independent_expected_context_cannot_be_substituted(self):
        for key in self.expected:
            with self.subTest(key=key):
                expected = dict(self.expected, **{key: "wrong"})
                with self.assertRaises(ReceiptError):
                    self.inspect(expected=expected)
        with self.assertRaises(ReceiptError):
            self.inspect(expected={})

    def test_rejects_substituted_object_and_content_bindings(self):
        for key, value in (("tagObject", self.receipt["snapshotCommit"]),
                           ("snapshotCommit", self.receipt["sourceCommit"]),
                           ("snapshotTree", "0" * 40),
                           ("changelogSha256", "sha256:" + "0" * 64),
                           ("tagRef", "refs/tags/other")):
            with self.subTest(key=key), self.assertRaises(ReceiptError):
                self.inspect(dict(self.receipt, **{key: value}))

    def test_moved_or_lightweight_tag_cannot_pass(self):
        self.git("tag", "-f", "v1.2.3", self.receipt["sourceCommit"])
        with self.assertRaises(ReceiptError):
            self.inspect()
        changed = dict(self.receipt, tagObject=self.git("rev-parse", "refs/tags/v1.2.3"))
        with self.assertRaises(ReceiptError):
            self.inspect(changed)

    def test_tag_object_must_point_directly_to_the_expected_snapshot(self):
        self.git("tag", "-fa", "v1.2.3", "-m", "different", self.receipt["sourceCommit"])
        receipt = dict(self.receipt, tagObject=self.git("rev-parse", "refs/tags/v1.2.3"))
        with self.assertRaises(ReceiptError):
            self.inspect(receipt)

    def test_snapshot_is_not_accepted_when_source_is_not_its_parent(self):
        alternate = self.git("rev-parse", "HEAD")
        receipt = dict(self.receipt, sourceCommit=alternate)
        with self.assertRaises(ReceiptError):
            self.inspect(receipt, dict(self.expected, sourceCommit=alternate))

    def test_grafted_parent_cannot_replace_the_commit_object_parent(self):
        alternate = self.git("commit-tree", self.receipt["snapshotTree"], "-m", "alternate")
        grafts = self.repo / ".git/info/grafts"
        grafts.write_text(f"{self.receipt['snapshotCommit']} {alternate}\n")
        self.assertIsNone(self.inspect())
        receipt = dict(self.receipt, sourceCommit=alternate)
        expected = dict(self.expected, sourceCommit=alternate)
        with self.assertRaises(ReceiptError):
            self.inspect(receipt, expected)

    def test_oversized_and_ambiguous_raw_commits_are_rejected(self):
        tree = self.receipt["snapshotTree"]
        parent = self.receipt["sourceCommit"]
        header = f"tree {tree}\nparent {parent}\nauthor Test <test@example.invalid> 0 +0000\ncommitter Test <test@example.invalid> 0 +0000\n"
        for payload in (
            (header + f"tree {tree}\n\nrelease\n").encode(),
            (header + f"parent {parent}\n\nrelease\n").encode(),
            (header + "\n").encode() + b"x" * (1024 * 1024),
        ):
            with self.subTest(size=len(payload)):
                result = subprocess.run(["git", "-C", str(self.repo), "hash-object", "--literally", "-t", "commit", "-w", "--stdin"],
                                        env=self.env, input=payload, check=True, capture_output=True)
                commit = result.stdout.decode().strip()
                self.git("tag", "-fa", "v1.2.3", "-m", "release", commit)
                receipt = dict(self.receipt, snapshotCommit=commit,
                               tagObject=self.git("rev-parse", "refs/tags/v1.2.3"))
                with self.assertRaises(ReceiptError):
                    self.inspect(receipt)

    def test_symlink_snapshot_is_rejected_even_with_matching_digest(self):
        (self.repo / "CHANGELOG/v1.2.3.md").unlink()
        (self.repo / "CHANGELOG/v1.2.3.md").symlink_to("../source")
        self.git("add", "CHANGELOG")
        self.git("commit", "-qm", "symlink")
        self.git("tag", "-fa", "v1.2.3", "-m", "release")
        receipt = dict(self.receipt, sourceCommit=self.receipt["snapshotCommit"],
                       snapshotCommit=self.git("rev-parse", "HEAD"), snapshotTree=self.git("rev-parse", "HEAD^{tree}"),
                       tagObject=self.git("rev-parse", "refs/tags/v1.2.3"),
                       changelogSha256="sha256:" + hashlib.sha256(b"../source").hexdigest())
        with self.assertRaises(ReceiptError):
            self.inspect(receipt, dict(self.expected, sourceCommit=receipt["sourceCommit"]))

    def test_hostile_global_git_config_is_not_read(self):
        hostile = self.repo / "hostile-gitconfig"
        hostile.write_text("[broken\n")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(hostile)}):
            control = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "--show-object-format"],
                                     capture_output=True)
            self.assertNotEqual(control.returncode, 0)
            self.assertIsNone(self.inspect())

    def assert_missing_promisor_blob_never_triggers_lazy_fetch(self, worktree_config):
        blob = self.git("rev-parse", "HEAD:CHANGELOG/v1.2.3.md")
        marker = self.repo / "fetch-called"
        upload = self.repo / "fake-upload-pack"
        upload.write_text(f"#!/bin/sh\nprintf called > '{marker}'\nexit 1\n")
        upload.chmod(0o755)
        remote = self.repo / "remote"
        remote.mkdir()
        if worktree_config:
            self.git("config", "extensions.worktreeConfig", "true")
        for key, value in (("url", remote.as_uri()), ("uploadpack", str(upload)),
                           ("promisor", "true"), ("partialclonefilter", "blob:none")):
            args = ["config"] + (["--worktree"] if worktree_config else [])
            self.git(*args, "remote.origin." + key, value)
        (self.repo / ".git/objects" / blob[:2] / blob[2:]).unlink()
        control = subprocess.run(["git", "-C", str(self.repo), "cat-file", "blob", blob],
                                 env=self.env, capture_output=True)
        self.assertNotEqual(control.returncode, 0)
        self.assertTrue(marker.exists(), "control did not attempt the promisor fetch")
        marker.unlink()
        wrapper_dir = self.repo / "wrapper"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        real_git = shutil.which("git")
        wrapper.write_text(f"#!/bin/sh\nunset GIT_NO_LAZY_FETCH\nexec '{real_git}' \"$@\"\n")
        wrapper.chmod(0o755)
        with patch.dict(os.environ, {"PATH": f"{wrapper_dir}{os.pathsep}{os.environ['PATH']}"}):
            with self.assertRaises(ReceiptError):
                self.inspect()
        self.assertFalse(marker.exists(), "offline inspection attempted a promisor fetch")

    def test_local_promisor_config_never_triggers_lazy_fetch(self):
        self.assert_missing_promisor_blob_never_triggers_lazy_fetch(False)

    def test_worktree_promisor_config_never_triggers_lazy_fetch(self):
        self.assert_missing_promisor_blob_never_triggers_lazy_fetch(True)

    def test_decoder_rejects_duplicate_unknown_and_malformed_fields(self):
        encoded = json.dumps(self.receipt).encode()
        with self.assertRaises(ReceiptError):
            decode_receipt(encoded[:-1] + b', "runId":"456"}')
        for update in ({"extra": 1}, {"runId": "01"}, {"schemaVersion": True},
                       {"version": "../escape"}, {"changelogSha256": "sha256:" + "A" * 64}):
            with self.subTest(update=update), self.assertRaises(ReceiptError):
                decode_receipt(json.dumps(dict(self.receipt, **update)).encode())
        with self.assertRaises(ReceiptError):
            decode_receipt(b"{" * (16 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
