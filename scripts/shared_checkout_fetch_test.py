#!/usr/bin/env python3
"""Decisions `shared_checkout_fetch` re-makes after waiting for its lock (#2003).

Mutual exclusion itself is proven against real concurrent gates in
`scripts/ci-gate/hub-changelog-validate-concurrent-deepen.test.sh`. These tests
replace the lock with one that lets another writer finish first, and assert that
the waiting writer then does not undo that writer's work. A checkout the helper
cannot inspect or lock is a typed error, never a silent pass.
"""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "shared_checkout_fetch", ROOT / "scripts/shared_checkout_fetch.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def is_shallow(repo: Path) -> bool:
    return git("-C", str(repo), "rev-parse", "--is-shallow-repository") == "true"


class SharedCheckoutFetchTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmp = Path(scratch.name)
        work = self.tmp / "work"
        git("init", "-q", "-b", "main", str(work))
        for n in range(4):
            git("-C", str(work), "-c", "user.name=test", "-c", "user.email=test@example.com",
                "commit", "-q", "--allow-empty", "-m", f"commit {n}")
        self.old = git("-C", str(work), "rev-parse", "HEAD~2")
        origin = self.tmp / "origin.git"
        git("clone", "-q", "--bare", str(work), str(origin))
        # Fetching an unadvertised commit by SHA, as github.com allows.
        git("-C", str(origin), "config", "uploadpack.allowAnySHA1InWant", "true")
        self.checkout = self.tmp / "checkout"
        git("clone", "-q", "--depth", "1", f"file://{origin}", str(self.checkout))
        self.assertTrue(is_shallow(self.checkout), "the fixture must start shallow")

    def another_writer_completes_history_first(self):
        @contextmanager
        def lock_won_after_unshallow(repo):
            git("-C", str(repo), "fetch", "-q", "--no-tags", "--unshallow", "origin")
            yield

        return mock.patch.object(MODULE, "history_lock", lock_won_after_unshallow)

    def test_ensure_commit_does_not_refetch_a_commit_another_writer_fetched_meanwhile(self):
        with self.another_writer_completes_history_first():
            present = MODULE.ensure_commit(self.checkout, self.old)

        self.assertTrue(present)
        self.assertFalse(is_shallow(self.checkout),
                         "a depth-one refetch truncated history another writer completed")

    def test_deepen_leaves_alone_a_checkout_another_writer_completed_meanwhile(self):
        head = git("-C", str(self.checkout), "rev-parse", "HEAD")

        with self.another_writer_completes_history_first():
            deepened = MODULE.deepen(self.checkout, head, 1)

        self.assertTrue(deepened)
        self.assertFalse(is_shallow(self.checkout),
                         "a bounded deepen truncated history another writer completed")

    def test_an_unopenable_git_directory_is_a_typed_error(self):
        missing = str(self.tmp / "missing-git-dir")

        with mock.patch.object(MODULE, "_common_dir", return_value=missing):
            with self.assertRaisesRegex(MODULE.SharedCheckoutError, "cannot open .*missing-git-dir"):
                with MODULE.history_lock(self.checkout):
                    pass

    def test_fetch_branch_takes_the_shared_lock(self):
        missing = str(self.tmp / "missing-git-dir")

        with mock.patch.object(MODULE, "_common_dir", return_value=missing):
            with self.assertRaisesRegex(MODULE.SharedCheckoutError, "cannot open .*missing-git-dir"):
                MODULE.fetch_branch(self.checkout, "main")

    def test_an_uninspectable_repository_is_not_treated_as_complete(self):
        not_a_repository = self.tmp / "not-a-repository"
        not_a_repository.mkdir()

        with self.assertRaisesRegex(MODULE.SharedCheckoutError, "cannot tell whether .* is shallow"):
            MODULE.unshallow(not_a_repository)

    def test_ensure_commit_refuses_an_option_in_place_of_a_commit(self):
        marker = self.tmp / "upload-pack-ran"

        present = MODULE.ensure_commit(self.checkout, f"--upload-pack=touch {marker}")

        self.assertFalse(present)
        self.assertFalse(marker.exists(), "an option-shaped commit reached git fetch")

    def test_fetch_branch_restores_the_branch_remote_tracking_ref(self):
        git("-C", str(self.checkout), "update-ref", "-d", "refs/remotes/origin/main")

        fetched = MODULE.fetch_branch(self.checkout, "main")

        self.assertTrue(fetched)
        self.assertEqual(
            git("-C", str(self.tmp / "origin.git"), "rev-parse", "refs/heads/main"),
            git("-C", str(self.checkout), "rev-parse", "refs/remotes/origin/main"),
        )

    def test_fetch_branch_refuses_a_name_that_is_not_one_branch(self):
        git("-C", str(self.tmp / "origin.git"), "branch", "other", "main")

        fetched = MODULE.fetch_branch(self.checkout, "*")

        self.assertFalse(fetched)
        self.assertEqual(
            "", git("-C", str(self.checkout), "for-each-ref", "refs/remotes/origin/other"),
            "a pattern fetched branches nobody named",
        )

    def test_the_program_passes_no_caller_arguments_through_to_git_fetch(self):
        marker = self.tmp / "upload-pack-ran"

        with mock.patch("sys.stderr"), self.assertRaises(SystemExit) as rejected:
            MODULE.main(["fetch", str(self.checkout), f"--upload-pack=touch {marker}", "origin"])

        self.assertEqual(rejected.exception.code, 2, "the program accepted a raw fetch operation")
        self.assertFalse(marker.exists(), "a caller's option reached git fetch")


if __name__ == "__main__":
    unittest.main()
