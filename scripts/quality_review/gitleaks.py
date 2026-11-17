"""Fail-closed gitleaks scanning for quality-review chunks (#1820)."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True)
class ScanResult:
    sent: list[str]
    dropped: list[str]
    reason: str | None = None


Scanner = Callable[[str], list[str]]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_pinned_binary(path: Path, expected_sha256: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"gitleaks binary missing: {path}")
    actual = sha256_file(path)
    if actual.lower() != expected_sha256.lower():
        raise ValueError(f"gitleaks digest mismatch: expected {expected_sha256}, got {actual}")


def subprocess_scanner(binary: Path) -> Scanner:
    """Return a scanner that invokes a verified gitleaks binary on stdin.

    gitleaks exit 0 = clean, 1 = findings; any other status fails closed.
    """

    def scan(chunk: str) -> list[str]:
        completed = subprocess.run(
            [str(binary), "stdin", "--no-banner"],
            input=chunk,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode not in (0, 1):
            raise RuntimeError(
                f"gitleaks failed (exit {completed.returncode}): "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        if completed.returncode == 0:
            return []
        findings = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
        return findings or ["gitleaks reported findings"]

    return scan


def scan_chunks(chunks: Sequence[str], scanner: Scanner | None) -> ScanResult:
    """Scan chunks fail-closed.

    If ``scanner`` is None or raises, nothing is sent and ``reason`` explains why.
    Chunks with findings are dropped and listed; clean chunks are returned in order.
    """
    if scanner is None:
        return ScanResult(sent=[], dropped=[], reason="gitleaks scanner absent")
    sent: list[str] = []
    dropped: list[str] = []
    try:
        for index, chunk in enumerate(chunks):
            findings = scanner(chunk)
            if findings:
                dropped.append(f"chunk[{index}]:{len(findings)} finding(s)")
            else:
                sent.append(chunk)
    except Exception as exc:  # noqa: BLE001 — fail closed on any scanner error
        return ScanResult(sent=[], dropped=[], reason=f"gitleaks scanner failed: {exc}")
    return ScanResult(sent=sent, dropped=dropped, reason=None)
