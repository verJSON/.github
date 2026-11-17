"""Fail-closed gitleaks scanning for quality-review chunks (#1820)."""

from __future__ import annotations

import hashlib
import json
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


def _parse_findings(stdout: str) -> list[str] | None:
    """Return finding labels, or None when stdout is not a findings report."""
    text = (stdout or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if parsed is None:
        return []
    if isinstance(parsed, list):
        labels: list[str] = []
        for item in parsed:
            if isinstance(item, dict):
                labels.append(str(item.get("RuleID") or item.get("Description") or item.get("Match") or "finding"))
            else:
                labels.append(str(item))
        return labels
    return None


def subprocess_scanner(binary: Path) -> Scanner:
    """Return a scanner that invokes a verified gitleaks binary on stdin.

    Fail-closed rules:
    - exit 0 → clean (no findings)
    - exit 1 with a JSON findings array → those findings
    - exit 1 without a parseable findings report → scanner failure
    - any other exit → scanner failure

    gitleaks also uses exit 1 for some scan errors; requiring a findings report
    prevents those errors from being treated as per-chunk leaks.
    """

    def scan(chunk: str) -> list[str]:
        completed = subprocess.run(
            [
                str(binary),
                "detect",
                "--no-git",
                "--pipe",
                "--no-banner",
                "--report-format",
                "json",
                "--report-path",
                "/dev/stdout",
            ],
            input=chunk,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0:
            return []
        if completed.returncode == 1:
            findings = _parse_findings(completed.stdout)
            if findings is None:
                raise RuntimeError(
                    "gitleaks exit 1 without a JSON findings report: "
                    f"{(completed.stderr or completed.stdout or '').strip()}"
                )
            return findings or ["gitleaks reported findings"]
        raise RuntimeError(
            f"gitleaks failed (exit {completed.returncode}): "
            f"{(completed.stderr or completed.stdout or '').strip()}"
        )

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
