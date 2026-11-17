"""Filter and chunk PR diffs before quality-review evaluation (#1820)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Sequence

# Path basenames / suffixes dropped before any provider sees them.
_LOCKFILE_NAMES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "poetry.lock",
        "Pipfile.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
        "bun.lockb",
    }
)
_ENV_NAMES = frozenset({".env", ".env.local", ".env.production", ".env.development"})
_GENERATED_SUFFIXES = (".pb.go", ".pb.ts", "_pb2.py", ".generated.ts", ".generated.js")
_BINARY_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
    ".7z",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mp3",
    ".wasm",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".bin",
)
_VENDORED_PREFIXES = (
    "vendor/",
    "third_party/",
    "third-party/",
    "node_modules/",
    ".yarn/cache/",
)


@dataclass(frozen=True)
class DiffFile:
    path: str
    patch: str


@dataclass
class PayloadPlan:
    chunks: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    omitted_for_budget: list[str] = field(default_factory=list)


_DIFF_FILE_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)


def parse_unified_diff(diff_text: str) -> list[DiffFile]:
    """Split a unified diff into per-file patches."""
    if not diff_text.strip():
        return []
    matches = list(_DIFF_FILE_RE.finditer(diff_text))
    if not matches:
        return [DiffFile(path="unknown", patch=diff_text)]
    files: list[DiffFile] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(diff_text)
        path = match.group(2)
        files.append(DiffFile(path=path, patch=diff_text[start:end]))
    return files


def should_drop_path(path: str) -> str | None:
    """Return a drop reason, or None if the path may be sent."""
    posix = path.replace("\\", "/")
    name = PurePosixPath(posix).name
    lower = posix.lower()
    if name in _LOCKFILE_NAMES or name.endswith(".lock"):
        return "lockfile"
    if name in _ENV_NAMES or name.startswith(".env."):
        return "env"
    if any(lower.startswith(prefix) or f"/{prefix}" in f"/{lower}" for prefix in _VENDORED_PREFIXES):
        return "vendored"
    if any(lower.endswith(suffix) for suffix in _BINARY_SUFFIXES):
        return "binary"
    if any(lower.endswith(suffix) for suffix in _GENERATED_SUFFIXES) or "/generated/" in f"/{lower}/":
        return "generated"
    return None


def estimate_tokens(text: str) -> int:
    """Cheap token estimate: ~4 characters per token."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def build_payload(
    diff_text: str,
    *,
    token_cap: int,
    total_budget: int,
) -> PayloadPlan:
    """Filter, chunk, and budget a PR diff for provider submission.

    Per-file patches over ``token_cap`` are dropped (listed). Remaining files are
    packed into chunks that each stay under ``token_cap`` until ``total_budget``
    tokens would be exceeded; further files are listed as omitted_for_budget.
    """
    if token_cap <= 0 or total_budget <= 0:
        raise ValueError("token_cap and total_budget must be positive")
    plan = PayloadPlan()
    accepted: list[DiffFile] = []
    for diff_file in parse_unified_diff(diff_text):
        reason = should_drop_path(diff_file.path)
        if reason:
            plan.dropped.append(f"{diff_file.path}:{reason}")
            continue
        tokens = estimate_tokens(diff_file.patch)
        if tokens > token_cap:
            plan.dropped.append(f"{diff_file.path}:over_cap:{tokens}")
            continue
        accepted.append(diff_file)

    current: list[str] = []
    current_tokens = 0
    used = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            plan.chunks.append("".join(current))
            current = []
            current_tokens = 0

    for diff_file in accepted:
        tokens = estimate_tokens(diff_file.patch)
        if used + tokens > total_budget:
            plan.omitted_for_budget.append(diff_file.path)
            continue
        if current_tokens + tokens > token_cap:
            flush()
        current.append(diff_file.patch)
        current_tokens += tokens
        used += tokens
    flush()
    return plan
