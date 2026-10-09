#!/usr/bin/env python3
"""Fail-closed pre-credential release-tree reconciliation."""

import argparse
import ctypes
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path


HOOK = "scripts/release-reconcile.sh"
BWRAP = "/usr/bin/bwrap"
MAX_ALLOWLIST = 32
BLOB_MODES = {"100644", "100755"}
PATH_PATTERN = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]*$")
# `.git/` files that decide what later git invocations execute. `git status` never
# reports them, and the steps after this one run `git commit` and the pinned
# changelog engine with the release App token, so they are compared byte for byte.
GIT_CONFIG_SURFACES = ("config", "config.worktree", "info/exclude")
# Surfaces the release engine itself owns, or that decide what code runs with the
# release App token. Reconciliation may never be pointed at any of them.
PROTECTED_ROOTS = frozenset({"RELEASES", "CHANGELOG", "NEXT"})
PROTECTED_FILES = frozenset({
    HOOK,
    "scripts/container_release_promotion.py",
    "scripts/container_release_manifest.py",
    "scripts/container_artifact_extract.py",
    "scripts/container_attestation_verify.py",
    "scripts/container_release_reconcile.py",
    "scripts/container-release-contract.test.sh",
})


class ReconcileError(Exception):
    pass


def git(root: Path, *args: str) -> str:
    environment = os.environ.copy()
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        env=environment,
    )
    if result.returncode != 0:
        raise ReconcileError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def tracked_mode(root: Path, path: str) -> str:
    """The index mode of `path` itself — empty unless `path` is exactly one tracked file.

    `ls-files -- deploy` lists the files *under* `deploy`, so comparing the listed
    name is what keeps a directory from passing as a reviewed regular file.
    """
    record = git(root, "ls-files", "--stage", "-z", "--", path).split("\0")[0]
    metadata, _, listed = record.partition("\t")
    return metadata.split()[0] if listed == path else ""


def validate_allowlist(root: Path, raw: str) -> list:
    try:
        allowlist = json.loads(raw)
    except ValueError as error:
        raise ReconcileError(f"reconciliation allowlist is not valid JSON: {error}") from None
    if not isinstance(allowlist, list) or not allowlist:
        raise ReconcileError("reconciliation allowlist must be a non-empty JSON array")
    if len(allowlist) > MAX_ALLOWLIST:
        raise ReconcileError(f"reconciliation allowlist exceeds {MAX_ALLOWLIST} reviewed paths")
    if len(set(allowlist)) != len(allowlist):
        raise ReconcileError("reconciliation allowlist contains duplicate paths")
    for path in allowlist:
        if not isinstance(path, str) or not PATH_PATTERN.fullmatch(path):
            raise ReconcileError(f"reconciliation allowlist entry is not a normalized repository-relative path: {path!r}")
        segments = path.split("/")
        if any(segment in ("", ".", "..") or segment.startswith(".git") for segment in segments):
            raise ReconcileError(f"reconciliation allowlist entry is not a normalized repository-relative path: {path!r}")
        if segments[0] in PROTECTED_ROOTS or path in PROTECTED_FILES:
            raise ReconcileError(f"reconciliation allowlist may not name a release-engine surface: {path}")
        mode = tracked_mode(root, path)
        if mode == "":
            raise ReconcileError(f"reconciliation allowlist entry is not a reviewed tracked file: {path}")
        if mode not in BLOB_MODES:
            raise ReconcileError(f"reconciliation allowlist entry {path} is a symlink or submodule, not a reviewed regular file")
    return allowlist


def require_bounded_manifest(root: Path, manifest: str) -> None:
    if not PATH_PATTERN.fullmatch(manifest) or any(
        segment in ("", ".", "..") for segment in manifest.split("/")
    ):
        raise ReconcileError(f"release manifest path is not repository-relative: {manifest!r}")
    path = root / manifest
    if path.is_symlink() or not path.is_file():
        raise ReconcileError(f"release manifest {manifest} is absent or not a regular file")


def require_pinned_contract(root: Path, contract_root: str, contract_ref: str, stage: str) -> None:
    """The pinned engine checkout decides what runs with the release App token."""
    contract = root / contract_root
    if contract.is_symlink() or not contract.is_dir():
        raise ReconcileError(f"pinned contract checkout {contract_root} is absent or not a directory")
    if git(contract, "rev-parse", "HEAD").strip() != contract_ref:
        raise ReconcileError(f"pinned contract checkout is not at the bound contract ref ({stage})")
    if git(contract, "status", "--porcelain", "--untracked-files=all").strip():
        raise ReconcileError(f"pinned contract checkout was modified ({stage})")


def require_reviewed_hook(root: Path) -> None:
    tracked = git(root, "ls-files", "--", HOOK).splitlines()
    if HOOK not in tracked:
        raise ReconcileError(f"{HOOK} is not tracked in the reviewed release tree")
    if git(root, "ls-files", "--stage", "--", HOOK).split()[0] == "120000":
        raise ReconcileError(f"{HOOK} is a symlink; the hook must be a reviewed regular file")
    path = root / HOOK
    if path.is_symlink() or not path.is_file():
        raise ReconcileError(f"{HOOK} is a symlink or not a regular file")
    if not os.access(path, os.X_OK):
        raise ReconcileError(f"{HOOK} is not executable")


def require_untracked_staged_list(root: Path, staged_list: str) -> None:
    if not PATH_PATTERN.fullmatch(staged_list) or any(
        segment in ("", ".", "..") for segment in staged_list.split("/")
    ):
        raise ReconcileError(f"staged-list path is not repository-relative: {staged_list!r}")
    if tracked_mode(root, staged_list):
        raise ReconcileError(
            f"staged-list path {staged_list} is a tracked file and would be rewritten outside review"
        )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def control_surface(root: Path, git_dir: Path) -> dict:
    """Fingerprint the `.git/` state that decides what later git commands run.

    Covers `core.hooksPath`, `core.fsmonitor`, `credential.helper`, content
    filters and aliases (all of which live in `config`), directly installed
    hooks, the exclude file that could hide the hook's own output, index entries
    and flags, and the commit the release will be built on. The index projection
    excludes refreshable stat-cache data while preserving flags such as
    `skip-worktree` that can hide a modified file from `git status`.
    """
    surface = {}
    for name in GIT_CONFIG_SURFACES:
        entry = git_dir / name
        surface[name] = digest(entry) if entry.is_file() and not entry.is_symlink() else None
    hooks = git_dir / "hooks"
    listing = sorted(hooks.iterdir()) if hooks.is_dir() and not hooks.is_symlink() else []
    for entry in listing:
        surface[f"hooks/{entry.name}"] = (
            f"{digest(entry)}:{int(os.access(entry, os.X_OK))}"
            if entry.is_file() and not entry.is_symlink()
            else "not-a-regular-file"
        )
    surface["index-entries"] = git(root, "ls-files", "--stage", "-v", "-z")
    surface["replace-refs"] = git(
        root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/replace",
    ).strip()
    if surface["replace-refs"]:
        raise ReconcileError(
            "Git replacement refs are not allowed during release reconciliation: "
            + surface["replace-refs"].replace("\n", ", ")
        )
    surface["HEAD"] = git(root, "rev-parse", "HEAD").strip()
    surface["HEAD-ref"] = git(root, "rev-parse", "--symbolic-full-name", "HEAD").strip()
    return surface


def control_surfaces(checkouts: dict) -> dict:
    return {label: control_surface(root, git_dir) for label, (root, git_dir) in checkouts.items()}


def require_intact_control_surfaces(checkouts: dict, baseline: dict) -> None:
    current = control_surfaces(checkouts)
    for label, surface in baseline.items():
        changed = sorted(
            name for name in set(surface) | set(current[label])
            if surface.get(name) != current[label].get(name)
        )
        if changed:
            raise ReconcileError(
                f"Git control surface of the {label} changed during reconciliation: "
                + ", ".join(changed)
            )


def require_clean_tracked_tree(root: Path) -> None:
    dirty = [
        path for staged, worktree, path in status_entries(root)
        if not (staged == "?" and worktree == "?")
    ]
    if dirty:
        raise ReconcileError(
            "the tracked release tree is not clean before reconciliation: " + ", ".join(sorted(dirty))
        )


def run_hook(root: Path, version: str, manifest: str, timeout: int) -> None:
    """Run the reviewed hook in a filesystem, process, and network namespace.

    The environment is built from scratch rather than filtered: a denylist cannot
    keep up with new credential-bearing variables, and this hook runs while the
    job is one step away from minting the release App token. Only system
    executables/configuration and the checkout are visible; private `/run` and
    `/tmp` mounts hide runner sockets, home directories, and temporary credentials.
    """
    if not os.path.isfile(BWRAP) or not os.access(BWRAP, os.X_OK):
        raise ReconcileError(f"required hook sandbox is unavailable: {BWRAP}")

    become_child_subreaper()
    workspace = str(root.resolve())
    sandbox_workspace = "/workspace"
    command = [
        BWRAP,
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--new-session",
        "--disable-userns",
        "--cap-drop", "ALL",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind-try", "/bin", "/bin",
        "--ro-bind-try", "/lib", "/lib",
        "--ro-bind-try", "/lib64", "/lib64",
        "--ro-bind", "/etc", "/etc",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/run",
        "--tmpfs", "/tmp",
        "--dir", sandbox_workspace,
        "--bind", workspace, sandbox_workspace,
        "--dir", "/tmp/release-reconcile-home",
        "--chdir", sandbox_workspace,
        "--clearenv",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/tmp/release-reconcile-home",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "GIT_CONFIG_NOSYSTEM", "1",
        "--setenv", "RELEASE_VERSION", version,
        "--setenv", "RELEASE_MANIFEST", manifest,
        "--",
        f"./{HOOK}", version, manifest,
    ]
    with open(os.devnull, "rb") as stdin:
        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                env={"PATH": "/usr/bin:/bin"},
                stdin=stdin,
                start_new_session=True,
                preexec_fn=set_no_new_privileges,
            )
        except OSError as error:
            raise ReconcileError(f"cannot start isolated reconciliation hook: {error}") from error
        # Resolve the group while the leader is alive: after `wait()` reaps it the
        # pid is gone, but the group can still hold processes the hook backgrounded.
        group = os.getpgid(process.pid)
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_group(process, group)
            raise ReconcileError(f"{HOOK} timed out after {timeout}s") from None
        finally:
            # Namespace isolation prevents host services from escaping the process
            # tree; these checks also reap descendants before the release token step.
            terminate_group(process, group)
            terminate_descendants()
    if returncode != 0:
        raise ReconcileError(f"{HOOK} exited {returncode}")


def terminate_group(process: subprocess.Popen, group: int) -> None:
    try:
        os.killpg(group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    process.wait()


def become_child_subreaper() -> None:
    """Adopt orphaned hook descendants so they can be killed before returning."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        error = ctypes.get_errno()
        raise ReconcileError(f"cannot contain reconciliation hook descendants: {os.strerror(error)}")


def set_no_new_privileges() -> None:
    """Prevent hook descendants from gaining runner privileges through sudo/setuid."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def direct_child_pids() -> list:
    children = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            record = (entry / "stat").read_text(encoding="ascii")
            fields = record[record.rfind(")") + 2 :].split()
            if int(fields[1]) == os.getpid():
                children.append(int(entry.name))
        except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
            continue
    return children


def terminate_descendants() -> None:
    deadline = time.monotonic() + 5
    while True:
        children = direct_child_pids()
        if not children:
            return
        for child in children:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for child in children:
            try:
                os.waitpid(child, os.WNOHANG)
            except ChildProcessError:
                pass
        if time.monotonic() >= deadline:
            raise ReconcileError("reconciliation hook descendants did not exit")
        time.sleep(0.01)


def status_entries(root: Path, *extra: str) -> list:
    """Parse `git status --porcelain=v1 -z -uall` into (index, worktree, path) triples."""
    raw = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", *extra)
    fields = raw.split("\0")
    entries = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        code, path = field[:2], field[3:]
        if code[0] in "RC":
            index += 1  # rename and copy records carry a second, original path
        entries.append((code[0], code[1], path))
    return entries


def untracked_paths(root: Path) -> set:
    return {path for staged, worktree, path in status_entries(root) if staged == "?" and worktree == "?"}


def ignored_paths(root: Path) -> set:
    """Ignored files are invisible to a plain `git status`, so they are tracked separately.

    A hook could otherwise stage output through a path the consumer's `.gitignore`
    already covers — including under `NEXT/`, which the pinned changelog engine
    consumes after the token is minted.
    """
    return {
        path for staged, worktree, path in status_entries(root, "--ignored=matching")
        if staged == "!" and worktree == "!"
    }


def path_fingerprint(path: Path, contract_git_dir: Path):
    """Fingerprint a pre-existing untracked path without following symlinks."""
    try:
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return ("file", mode, digest.hexdigest())
        if stat.S_ISLNK(info.st_mode):
            return ("symlink", os.readlink(path))
        if stat.S_ISDIR(info.st_mode):
            children = []
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                if child == contract_git_dir and child.is_dir() and not child.is_symlink():
                    # The pinned checkout's ref and worktree are independently checked.
                    continue
                children.append((child.name, path_fingerprint(child, contract_git_dir)))
            return ("directory", mode, tuple(children))
    except OSError as error:
        raise ReconcileError(f"cannot fingerprint pre-existing path {path}: {error}") from None
    raise ReconcileError(f"pre-existing path is not a regular file, symlink, or directory: {path}")


def path_fingerprints(root: Path, paths: set, contract_root: str) -> dict:
    contract_git_dir = root / contract_root / ".git"
    fingerprints = {}
    for path in paths:
        fingerprints[("path", path)] = path_fingerprint(root / path.rstrip("/"), contract_git_dir)
        parent = (root / path.rstrip("/")).parent
        while parent != root:
            try:
                info = parent.lstat()
            except FileNotFoundError:
                break
            if not stat.S_ISDIR(info.st_mode):
                break
            relative = parent.relative_to(root).as_posix() + "/"
            fingerprints.setdefault(
                ("directory", relative), ("directory", stat.S_IMODE(info.st_mode))
            )
            parent = parent.parent
    return fingerprints


def directory_mode_fingerprint(path: Path):
    try:
        info = path.lstat()
    except OSError as error:
        raise ReconcileError(f"cannot fingerprint pre-existing directory {path}: {error}") from None
    if not stat.S_ISDIR(info.st_mode):
        raise ReconcileError(f"pre-existing directory changed type: {path}")
    return ("directory", stat.S_IMODE(info.st_mode))


def validate_preserved_paths(
    root: Path,
    paths: set,
    fingerprints: dict,
    contract_root: str,
    label: str,
) -> None:
    missing = sorted(
        path for kind, path in fingerprints if kind == "path" and path not in paths
    )
    if missing:
        raise ReconcileError(f"hook removed pre-existing {label}: {missing[0]}")
    for (kind, path), expected in fingerprints.items():
        target = root / path.rstrip("/")
        current = (
            directory_mode_fingerprint(target)
            if kind == "directory"
            else path_fingerprint(target, root / contract_root / ".git")
        )
        if current != expected:
            raise ReconcileError(f"hook modified pre-existing {label}: {path}")


def validate_tree(
    root: Path,
    allowlist: list,
    pre_existing_untracked: set,
    pre_existing_ignored: set,
    untracked_before: dict,
    ignored_before: dict,
    contract_root: str,
) -> list:
    current_untracked = untracked_paths(root)
    current_ignored = ignored_paths(root)
    validate_preserved_paths(root, current_untracked, untracked_before, contract_root, "untracked output")
    validate_preserved_paths(root, current_ignored, ignored_before, contract_root, "ignored output")
    for path in sorted(current_ignored - pre_existing_ignored):
        raise ReconcileError(f"hook produced ignored output: {path}")
    changed = []
    for staged, worktree, path in status_entries(root):
        if staged == "?" and worktree == "?":
            if path not in pre_existing_untracked:
                raise ReconcileError(f"hook produced untracked output: {path}")
            continue
        if staged != " ":
            raise ReconcileError(f"hook staged its own change to the index: {path}")
        if worktree != "M":
            raise ReconcileError(
                f"hook made a non-content change ({worktree!r}) to {path}; "
                "deletion, rename and type changes are rejected"
            )
        if path not in allowlist:
            raise ReconcileError(f"hook changed a path outside the reviewed allowlist: {path}")
        changed.append(path)

    raw = git(root, "diff", "--raw", "-z").split("\0")
    index = 0
    while index < len(raw):
        record = raw[index]
        index += 1
        if not record.startswith(":"):
            continue
        source_mode, destination_mode = record[1:].split(" ")[:2]
        path = raw[index]
        index += 1
        if source_mode != destination_mode or destination_mode not in BLOB_MODES:
            raise ReconcileError(
                f"hook changed the file mode or type of {path} "
                f"({source_mode} -> {destination_mode})"
            )
        if (root / path).is_symlink() or not (root / path).is_file():
            raise ReconcileError(f"reconciled path {path} is not a regular file")
    return sorted(changed)


def content_fingerprint(root: Path, paths: list) -> str:
    """Blob hashes of the reconciled working-tree files.

    `git diff --raw` reports an all-zero destination hash for unstaged worktree
    changes, so it cannot distinguish two different reconciliations of the same
    path; hashing the files themselves can.
    """
    return "" if not paths else git(root, "hash-object", "--", *paths)


def rollback(root: Path, pre_existing_untracked: set) -> None:
    try:
        git(root, "reset", "-q")
        git(root, "checkout", "--", ".")
        for path in untracked_paths(root) - pre_existing_untracked:
            candidate = root / path
            if candidate.is_symlink() or candidate.is_file():
                candidate.unlink()
    except (OSError, ReconcileError) as error:
        print(f"release reconciliation rollback incomplete: {error}", file=sys.stderr)


def reconcile(root: Path, args) -> list:
    allowlist = validate_allowlist(root, args.allowlist)
    require_bounded_manifest(root, args.manifest)
    require_untracked_staged_list(root, args.staged_list)
    require_pinned_contract(root, args.contract_root, args.contract_ref, "before reconciliation")
    require_reviewed_hook(root)
    require_clean_tracked_tree(root)
    # Resolve the git directories from the trusted pre-hook state: once the hook has
    # run, the answer to "where is .git" is exactly what an attacker would redirect.
    contract = root / args.contract_root
    checkouts = {
        "release checkout": (root, Path(git(root, "rev-parse", "--absolute-git-dir").strip())),
        "pinned contract checkout": (
            contract, Path(git(contract, "rev-parse", "--absolute-git-dir").strip()),
        ),
    }
    baseline = control_surfaces(checkouts)
    pre_existing_untracked = untracked_paths(root)
    pre_existing_ignored = ignored_paths(root)
    untracked_before = path_fingerprints(root, pre_existing_untracked, args.contract_root)
    ignored_before = path_fingerprints(root, pre_existing_ignored, args.contract_root)

    def validate():
        # Control surfaces first: every later check reads `git` output, which
        # `.git/config` itself can be made to falsify.
        require_intact_control_surfaces(checkouts, baseline)
        return validate_tree(
            root, allowlist, pre_existing_untracked, pre_existing_ignored,
            untracked_before, ignored_before, args.contract_root,
        )

    try:
        run_hook(root, args.version, args.manifest, args.timeout)
        changed = validate()
        first = content_fingerprint(root, changed)
        # Releases are retried. Re-run the hook against its own output and require
        # a fixed point, so a retry can never produce a different release tree.
        run_hook(root, args.version, args.manifest, args.timeout)
        if validate() != changed or content_fingerprint(root, changed) != first:
            raise ReconcileError(f"{HOOK} is not idempotent: a second run changed the release tree")
        require_pinned_contract(root, args.contract_root, args.contract_ref, "after reconciliation")
    except BaseException:
        rollback(root, pre_existing_untracked)
        raise
    if changed:
        git(root, "add", "--", *changed)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--allowlist", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--contract-root", required=True)
    parser.add_argument("--contract-ref", required=True)
    parser.add_argument("--timeout", required=True, type=int)
    parser.add_argument("--staged-list", required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    try:
        staged = reconcile(root, args)
        (root / args.staged_list).write_text(
            "".join(f"{path}\n" for path in staged), encoding="utf-8",
        )
    except (OSError, ValueError, ReconcileError, subprocess.TimeoutExpired) as error:
        print(f"release reconciliation rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
