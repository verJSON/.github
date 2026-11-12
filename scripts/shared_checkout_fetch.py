#!/usr/bin/env python3
"""Fetch into a checkout that other processes share, one writer at a time (#2003).

`actions-ci-group.sh` runs a group's rows concurrently in one checkout, and that
checkout is shallow (`fetch-depth: 1`, ADR 0045), so several rows deepen or fetch
into it. Git does not wait for a concurrent shallow update: a fetch reads
`.git/shallow` when it starts and aborts with `shallow file has changed since we
read it` if another process rewrote the file before it commits its own.

Every fetch into such a checkout goes through this module. It holds an exclusive
flock(2) on the repository's git common directory for the whole fetch, so linked
worktrees, which share one `shallow` file, share one lock too. The checks that
decide whether to fetch at all are made again under the lock, because another
writer may have done the work while this one waited:

- an unshallow another writer finished is not repeated, which git refuses on a
  complete repository;
- a commit another writer already fetched is not fetched again at depth one,
  and a checkout another writer completed is not deepened to a bounded depth:
  either fetch would mark a present commit shallow and truncate history that
  writer had completed.

Presence and completeness are each checked once without the lock first, so a
caller that needs no fetch does not queue behind another row's.

Run as a program for shell callers:

    shared_checkout_fetch.py unshallow <repo>
    shared_checkout_fetch.py ensure-commit <repo> <sha>
    shared_checkout_fetch.py deepen <repo> <sha> <depth>
    shared_checkout_fetch.py fetch-branch <repo> <branch>

There is deliberately no pass-through to `git fetch`: an argument such as
`--upload-pack=<command>` would run that command, so every operation builds its
own fetch arguments from a validated object or branch name (#2020).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterator


# A full object name, never an option or a refspec a caller did not mean: 40 hex
# digits for a SHA-1 repository or 64 for a SHA-256 one (`extensions.objectFormat`).
# Abbreviations are refused, because git would resolve them only locally (#2019).
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class SharedCheckoutError(RuntimeError):
    """The repository, its git directory, or its lock cannot be inspected or opened."""


def _git(repo: Path | str, *args: str, **run_options) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=False, **run_options)


def _common_dir(repo: Path | str) -> str:
    result = _git(
        repo, "rev-parse", "--path-format=absolute", "--git-common-dir",
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise SharedCheckoutError(
            f"cannot locate the git directory of {repo}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


@contextmanager
def history_lock(repo: Path | str) -> Iterator[None]:
    common_dir = _common_dir(repo)
    try:
        descriptor = os.open(common_dir, os.O_RDONLY)
    except OSError as error:
        raise SharedCheckoutError(
            f"cannot open the git directory {common_dir} to lock it: {error}"
        ) from None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        # Closing the only descriptor releases the lock.
        os.close(descriptor)


def _is_shallow(repo: Path | str) -> bool:
    result = _git(repo, "rev-parse", "--is-shallow-repository", capture_output=True, text=True)
    # Failing open here would report an uninspectable checkout as complete, and
    # every caller would then skip the fetch its check depends on.
    if result.returncode != 0:
        raise SharedCheckoutError(
            f"cannot tell whether {repo} is shallow: {result.stderr.strip()}"
        )
    return result.stdout.strip() == "true"


def _has_commit(repo: Path | str, sha: str) -> bool:
    result = _git(repo, "cat-file", "-e", f"{sha}^{{commit}}", capture_output=True)
    return result.returncode == 0


def unshallow(repo: Path | str, **run_options) -> bool:
    """Make `repo` complete from `origin`; True when it is complete afterwards."""
    if not _is_shallow(repo):
        return True
    with history_lock(repo):
        if not _is_shallow(repo):
            return True
        result = _git(repo, "fetch", "--quiet", "--no-tags", "--unshallow", "origin", **run_options)
        return result.returncode == 0


def ensure_commit(repo: Path | str, sha: str, **run_options) -> bool:
    """Fetch `sha` from `origin` at depth one unless present; True when present."""
    if _COMMIT.fullmatch(sha) is None:
        return False
    if _has_commit(repo, sha):
        return True
    with history_lock(repo):
        if _has_commit(repo, sha):
            return True
        _git(repo, "fetch", "--quiet", "--no-tags", "--depth", "1", "origin", sha, **run_options)
        return _has_commit(repo, sha)


def _is_branch_name(branch: str) -> bool:
    # The full refname is checked, not `--branch`, which would expand `@{-1}`;
    # a valid refname has no glob, so the refspec below names exactly one branch.
    # The name only ever appears inside that refspec, never as its own argument.
    if not branch:
        return False
    result = subprocess.run(
        ["git", "check-ref-format", f"refs/heads/{branch}"], check=False, capture_output=True,
    )
    return result.returncode == 0


def fetch_branch(repo: Path | str, branch: str, **run_options) -> bool:
    """Update `refs/remotes/origin/<branch>` from `origin`; True when the fetch succeeds."""
    if not _is_branch_name(branch):
        return False
    with history_lock(repo):
        result = _git(
            repo, "fetch", "--quiet", "--no-tags", "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}", **run_options,
        )
        return result.returncode == 0


def deepen(repo: Path | str, sha: str, depth: int, **run_options) -> bool:
    """Fetch `sha`'s history to `depth` while `repo` is shallow; True unless that fails.

    A complete checkout has nothing to deepen, so it is left alone.
    """
    if _COMMIT.fullmatch(sha) is None or depth < 1:
        return False
    if not _is_shallow(repo):
        return True
    with history_lock(repo):
        if not _is_shallow(repo):
            return True
        result = _git(
            repo, "fetch", "--quiet", "--no-tags", "--depth", str(depth), "origin", sha,
            **run_options,
        )
        return result.returncode == 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    operations = parser.add_subparsers(dest="operation", required=True)
    operations.add_parser("unshallow").add_argument("repo")
    ensure = operations.add_parser("ensure-commit")
    ensure.add_argument("repo")
    ensure.add_argument("sha")
    deepening = operations.add_parser("deepen")
    deepening.add_argument("repo")
    deepening.add_argument("sha")
    deepening.add_argument("depth", type=int)
    branch_fetch = operations.add_parser("fetch-branch")
    branch_fetch.add_argument("repo")
    branch_fetch.add_argument("branch")
    options = parser.parse_args(argv)
    try:
        if options.operation == "unshallow":
            return 0 if unshallow(options.repo) else 1
        if options.operation == "ensure-commit":
            return 0 if ensure_commit(options.repo, options.sha) else 1
        if options.operation == "deepen":
            return 0 if deepen(options.repo, options.sha, options.depth) else 1
        if not _is_branch_name(options.branch):
            print(f"shared_checkout_fetch: {options.branch!r} is not a branch name", file=sys.stderr)
            return 1
        return 0 if fetch_branch(options.repo, options.branch) else 1
    except SharedCheckoutError as error:
        print(f"shared_checkout_fetch: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
