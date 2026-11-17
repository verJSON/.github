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

# #2156 / #2159: prove flag orderings and GraphQL admin merge match; non-merge
# --admin and non-admin GraphQL must not.
merge_probes_ok = (
    "gh pr merge --admin --auto",
    "gh pr merge --auto --admin",
    'gh pr merge "$n" --squash --admin',
)
merge_probes_bad = (
    "echo --admin only",
    "gh pr view --json number",
)
graphql_probes_ok = (
    "mergePullRequest(input: {pullRequestId: $id, admin: true})",
    "client.mergePullRequest({admin: True})",
)
graphql_probes_bad = (
    "mergePullRequest(input: {pullRequestId: $id})",
    "describeAdminRole()",
)
missing = [p for p in merge_probes_ok if not MERGE_ADMIN.search(p)]
missing += [p for p in graphql_probes_ok if not GRAPHQL_ADMIN.search(p)]
if missing:
    print(
        "FAIL - inventory pattern misses a merge … --admin / GraphQL admin probe:\n"
        + "\n".join(missing)
    )
    sys.exit(1)
if any(MERGE_ADMIN.search(p) for p in merge_probes_bad):
    print("FAIL - inventory pattern matched a non-merge --admin probe (#2156)")
    sys.exit(1)
if any(GRAPHQL_ADMIN.search(p) for p in graphql_probes_bad):
    print("FAIL - inventory pattern matched a non-admin GraphQL probe (#2159)")
    sys.exit(1)

# #2160: skip exact directory names and test-named path components without
# treating substrings like "contest" as tests.
_SKIP_DIRS = frozenset({"fixtures", "NEXT", "docs"})
_TEST_PART = re.compile(r"(?:^|[^A-Za-z0-9])tests?(?:[^A-Za-z0-9]|$)|\.test\.", re.I)


def _skip_path(path: pathlib.Path) -> bool:
    if path.name == "README.md":
        return True
    for part in path.parts:
        if part in _SKIP_DIRS:
            return True
        if _TEST_PART.search(part):
            return True
    return False


roots = [pathlib.Path("scripts"), pathlib.Path(".github/workflows")]
hits: list[str] = []
for root in roots:
    if not root.exists():
        continue
    for path in root.rglob("*"):
        if not path.is_file() or _skip_path(path):
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
print("ok - inventory pattern covers merge and GraphQL admin probes (#2156/#2159)")
PY
