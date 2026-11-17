#!/usr/bin/env bash
# Part of #1945 / ADR 0269: autonomous workflows and scripts must not invoke
# owner `gh pr merge --admin`. Test and fixture negatives remain allowed.
#
# Uses python3 (available on merge-gate runners) rather than ripgrep: the
# inventory must fail closed when the searcher is missing (#2156 CI).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

python3 - <<'PY'
from __future__ import annotations

import pathlib
import re
import sys

MERGE_ADMIN = re.compile(r"gh pr merge.{0,240}--admin", re.DOTALL)
GRAPHQL_ADMIN = re.compile(r"mergePullRequest.{0,240}[Aa]dmin", re.DOTALL)

# #2156: prove both flag orderings match, and non-merge --admin does not.
probes_ok = (
    "gh pr merge --admin --auto",
    "gh pr merge --auto --admin",
    'gh pr merge "$n" --squash --admin',
)
probes_bad = (
    "echo --admin only",
    "gh pr view --json number",
)
missing = [p for p in probes_ok if not MERGE_ADMIN.search(p)]
if missing:
    print(
        "FAIL - inventory pattern misses a gh pr merge … --admin ordering (#2156):\n"
        + "\n".join(missing)
    )
    sys.exit(1)
if any(MERGE_ADMIN.search(p) for p in probes_bad):
    print("FAIL - inventory pattern matched a non-merge --admin probe (#2156)")
    sys.exit(1)

skip_parts = ("test", "fixtures", "NEXT", "docs")
skip_names = {"README.md"}
roots = [pathlib.Path("scripts"), pathlib.Path(".github/workflows")]
hits: list[str] = []
for root in roots:
    if not root.exists():
        continue
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.parts)
        if parts & set(skip_parts):
            continue
        if any("test" in part for part in path.parts):
            continue
        if path.name in skip_names:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in (MERGE_ADMIN, GRAPHQL_ADMIN):
            if pattern.search(text):
                hits.append(f"{path}: matches {pattern.pattern}")
                break

if hits:
    print("FAIL - live --admin call sites remain (ADR 0269):")
    print("\n".join(hits))
    sys.exit(1)

print("ok - no live owner --admin merge call sites in scripts/ or workflows")
print("ok - inventory pattern covers both --admin flag orderings (#2156)")
PY
