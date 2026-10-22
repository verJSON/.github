#!/usr/bin/env python3
"""Stamp every `# runner-lane: <shape>` marked `runs-on:` line from the catalog.

    gen-runner-lane.py [--check] [workflow-dir]

A marker comment sits on the line directly above `runs-on:`. Without --check the
line is rewritten in place; with --check a stale or hand-edited line fails with
exit 1 and nothing is written. An unknown shape or a marker not followed by
`runs-on:` also fails: a marker that silently stamps nothing is the drift this
generator exists to prevent (ADR 0240).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner_lane_catalog as catalog  # noqa: E402

MARKER_LINE = re.compile(rf"^(\s*)#\s*{re.escape(catalog.MARKER)}\s*(\S+)\s*$")
RUNS_ON_LINE = re.compile(r"^(\s*)runs-on:")


def stamp(text: str, name: str) -> tuple[str, list[str]]:
    lines = text.split("\n")
    problems: list[str] = []
    for index, line in enumerate(lines):
        match = MARKER_LINE.match(line)
        if not match:
            continue
        indent, shape = match.groups()
        target = lines[index + 1] if index + 1 < len(lines) else ""
        if shape not in catalog.SHAPES:
            problems.append(f"{name}:{index + 1}: unknown runner-lane shape {shape!r}")
        elif not RUNS_ON_LINE.match(target) or RUNS_ON_LINE.match(target).group(1) != indent:
            problems.append(f"{name}:{index + 2}: runner-lane marker must precede a runs-on: line")
        else:
            lines[index + 1] = catalog.runs_on_line(shape, indent)
    return "\n".join(lines), problems


def main(argv: list[str]) -> int:
    check = "--check" in argv
    args = [arg for arg in argv if arg != "--check"]
    root = Path(args[0]) if args else Path(__file__).resolve().parents[1] / ".github/workflows"
    failed = False
    for path in sorted(root.glob("*.yml")):
        original = path.read_text()
        if catalog.MARKER not in original:
            continue
        stamped, problems = stamp(original, path.name)
        for problem in problems:
            print(problem, file=sys.stderr)
        failed = failed or bool(problems)
        if stamped != original:
            if check:
                print(f"{path.name}: runs-on is stale; run scripts/gen-runner-lane.py", file=sys.stderr)
                failed = True
            elif not problems:
                path.write_text(stamped)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
